import logging

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models.ai_analysis import AIAnalysis
from app.models.change import Change
from app.models.monitor import Monitor
from app.models.snapshot import Snapshot

from app.repositories.ai_analysis_repository import (
    create_ai_analysis,
)
from app.repositories.change_repository import (
    create_change,
)
from app.repositories.monitor_repository import (
    set_auto_section_selection,
    update_last_checked,
)
from app.repositories.snapshot_repository import (
    calculate_content_hash,
    create_snapshot,
    get_latest_snapshot,
)

from app.services.ai_analyzer import (
    AIAnalyzerError,
    AIAnalysisResult,
    analyze_change_with_ai,
)
from app.services.change_detector import (
    ChangeResult,
    detect_changes,
)
from app.services.diff_noise_profiles import (
    collect_volatile_lines,
)
from app.services.diff_noise_reducer import (
    reduce_diff_noise,
)
from app.services.content_extractor import (
    ExtractedContent,
    extract_content,
)
from app.services.section_extractor import (
    ManualSectionResolutionError,
    extract_section_candidates,
    resolve_section,
)
from app.services.section_selector import (
    AutoSectionSelectionError,
    select_section_with_ai,
)
from app.services.topic_analyzer import (
    TopicAnalysisResult,
    analyze_topic_relevance,
)
from app.services.web_fetcher import (
    FetchResult,
    fetch_website,
)


# ---------------------------------------------------------
# TAKIP KAPSAMI
#
# full_page      : sayfanin tamami
# auto_section   : Gemini'nin sectigi bolum
# manual_section : KULLANICININ sectigi bolum
# ---------------------------------------------------------

SCOPE_FULL_PAGE = "full_page"
SCOPE_AUTO_SECTION = "auto_section"
SCOPE_MANUAL_SECTION = "manual_section"


logger = logging.getLogger(__name__)


@dataclass
class AutoSectionOutcome:
    """
    auto_section icerik cikariminin sonucu.

    ai_selection_used, kota davranisinin dogrulanabilmesi
    icin bu calistirmada bolum secimi AI cagrisi yapilip
    yapilmadigini soyler.
    """

    content: ExtractedContent

    reference: str
    label: str | None

    ai_selection_used: bool
    rediscovered: bool


@dataclass
class ManualSectionOutcome:
    """
    manual_section icerik cikariminin sonucu.

    Bu akista HICBIR AI cagrisi yoktur; bu yuzden
    auto_section'daki ai_selection_used / rediscovered
    bayraklarinin karsiligi da yoktur.
    """

    content: ExtractedContent

    reference: str
    label: str | None


@dataclass
class DatabaseMonitorResult:
    monitor: Monitor
    fetch_result: FetchResult
    content: ExtractedContent

    first_run: bool
    changed: bool

    previous_snapshot: Snapshot | None
    new_snapshot: Snapshot | None

    change_result: ChangeResult | None
    topic_analysis: TopicAnalysisResult | None
    change_record: Change | None

    ai_analysis_result: AIAnalysisResult | None
    ai_analysis_record: AIAnalysis | None
    ai_error: str | None

    # auto_section disinda None kalir.
    auto_section: AutoSectionOutcome | None = None

    # manual_section disinda None kalir.
    manual_section: ManualSectionOutcome | None = None


# ---------------------------------------------------------
# AUTO SECTION ICERIK CIKARIMI
#
# Gemini'ye bolum secimi YALNIZCA su iki durumda sorulur:
#
#   1. ilk auto_section calistirmasi (kayitli referans yok)
#   2. kayitli referans HTML'de artik bulunamiyor
#
# Normal calistirmalarda hic AI cagrisi yapilmaz.
# ---------------------------------------------------------

async def extract_auto_section_content(
    db: Session,
    monitor: Monitor,
    html: str,
) -> AutoSectionOutcome:
    """
    auto_section monitoru icin YALNIZCA secilmis bolumun
    temiz metnini uretir.

    Snapshot basligi tum sayfa davranisiyla ayni kalir
    (sayfa <title> etiketi).

    Uygun bolum bulunamazsa AutoSectionSelectionError
    firlatilir. Sessizce tum sayfaya DONULMEZ; kullanici
    auto_section sectigi icin davranis degistirilmez.
    """

    saved_reference = (
        monitor.auto_section_reference or ""
    ).strip()

    # -----------------------------------------------------
    # 1. KAYITLI BOLUMU AI'SIZ BULMAYI DENE
    # -----------------------------------------------------

    if saved_reference:

        resolution = resolve_section(
            html=html,
            reference=saved_reference,
        )

        if resolution.section is not None:

            section = resolution.section

            return AutoSectionOutcome(
                content=ExtractedContent(
                    title=resolution.page_title,
                    text=section.text,
                    text_length=section.text_length,
                ),
                reference=section.reference,
                label=(
                    monitor.auto_section_label
                    or section.label
                ),
                ai_selection_used=False,
                rediscovered=False,
            )

    # -----------------------------------------------------
    # 2. ADAY BOLUMLERI CIKAR
    # -----------------------------------------------------

    discovery = extract_section_candidates(html)

    if not discovery.candidates:
        raise AutoSectionSelectionError(
            "Sayfada izlenebilecek uygun bir bölüm bulunamadı. "
            f"monitor_id={monitor.id}"
        )

    # -----------------------------------------------------
    # 3. GEMINI BOLUM SECIMI
    # -----------------------------------------------------

    selection = await select_section_with_ai(
        topic=monitor.topic,
        candidates=discovery.candidates,
        page_title=discovery.page_title,
    )

    candidate = selection.candidate

    # -----------------------------------------------------
    # 4. SECIMI KALICI HALE GETIR
    # -----------------------------------------------------

    set_auto_section_selection(
        db=db,
        monitor=monitor,
        reference=candidate.reference,
        label=candidate.label,
    )

    return AutoSectionOutcome(
        content=ExtractedContent(
            title=discovery.page_title,
            text=candidate.text,
            text_length=candidate.text_length,
        ),
        reference=candidate.reference,
        label=candidate.label,
        ai_selection_used=True,
        rediscovered=bool(saved_reference),
    )


# ---------------------------------------------------------
# MANUAL SECTION ICERIK CIKARIMI
#
# Bolumu KULLANICI secmistir.
#
# Bu akista:
#   - HICBIR Gemini cagrisi yoktur
#   - otomatik bolum secimi yoktur
#   - bolum bulunamazsa baska bolum SECILMEZ
#   - sessizce tum sayfaya DONULMEZ
# ---------------------------------------------------------

SECTION_LOST_MESSAGE = (
    "Seçilen bölüm artık sayfada bulunamıyor. "
    "İzleme ayarlarından bölümü yeniden seçin."
)


def extract_manual_section_content(
    monitor: Monitor,
    html: str,
) -> ManualSectionOutcome:
    """
    manual_section monitoru icin YALNIZCA kullanicinin
    sectigi bolumun temiz metnini uretir.

    Snapshot basligi tum sayfa davranisiyla ayni kalir
    (sayfa <title> etiketi).

    Bolum bulunamazsa ManualSectionResolutionError firlatilir.
    """

    saved_reference = (
        monitor.manual_section_reference or ""
    ).strip()

    if not saved_reference:
        # Sema ve API dogrulamasi bunu engelliyor;
        # burasi eski/bozuk kayitlar icin son savunma.
        raise ManualSectionResolutionError(
            "Bu izleme için bir bölüm seçilmemiş. "
            "İzleme ayarlarından bölümü seçin. "
            f"monitor_id={monitor.id}"
        )

    resolution = resolve_section(
        html=html,
        reference=saved_reference,
    )

    if resolution.section is None:
        raise ManualSectionResolutionError(
            f"{SECTION_LOST_MESSAGE} monitor_id={monitor.id}"
        )

    section = resolution.section

    return ManualSectionOutcome(
        content=ExtractedContent(
            title=resolution.page_title,
            text=section.text,
            text_length=section.text_length,
        ),
        reference=section.reference,
        label=(
            monitor.manual_section_label
            or section.label
        ),
    )


async def run_database_monitor(
    db: Session,
    monitor: Monitor,
    reduce_noise: bool = True,
) -> DatabaseMonitorResult:
    """
    PostgreSQL tabanlı website monitoring akışı.

    Akış:

    1. Website'i indir.
    2. Kapsama göre içeriği çıkar.
       full_page     : tüm sayfa
       auto_section  : Gemini'nin seçtiği bölüm
       manual_section: kullanıcının seçtiği bölüm
    3. Son snapshot'ı getir.
    4. İlk çalışmaysa snapshot oluştur.
    5. Hash aynıysa değişiklik yok.
    6. Değişiklikleri çıkar.
    7. Keyword topic analizi yap.
    8. Yeni snapshot oluştur.
    9. Change kaydı oluştur.
    10. Gemini ile anlamsal analiz yap.
    11. AI analizini PostgreSQL'e kaydet.
    12. last_checked_at değerini güncelle.
    """

    if not monitor.is_active:
        raise ValueError(
            f"Monitor aktif değil. monitor_id={monitor.id}"
        )

    # -----------------------------------------------------
    # 1. WEBSITE
    # -----------------------------------------------------

    fetch_result = await fetch_website(
        monitor.url
    )

    # -----------------------------------------------------
    # 2. KAPSAMA GORE CONTENT EXTRACTION
    #
    # auto_section   -> Gemini'nin sectigi bolumun metni
    # manual_section -> KULLANICININ sectigi bolumun metni
    # full_page      -> tum sayfa (mevcut davranis AYNEN)
    #
    # Bu adimdan sonrasi (snapshot, change detection, topic
    # analizi, Gemini degisiklik analizi, notification)
    # kapsamdan BAGIMSIZ olarak aynidir.
    # -----------------------------------------------------

    auto_section = None
    manual_section = None

    if monitor.scope_type == SCOPE_AUTO_SECTION:

        auto_section = await extract_auto_section_content(
            db=db,
            monitor=monitor,
            html=fetch_result.html,
        )

        content = auto_section.content

    elif monitor.scope_type == SCOPE_MANUAL_SECTION:

        manual_section = extract_manual_section_content(
            monitor=monitor,
            html=fetch_result.html,
        )

        content = manual_section.content

    else:

        content = extract_content(
            fetch_result.html
        )

    # -----------------------------------------------------
    # 3. SON SNAPSHOT
    # -----------------------------------------------------

    previous_snapshot = get_latest_snapshot(
        db=db,
        monitor_id=monitor.id,
    )

    current_hash = calculate_content_hash(
        content.text
    )

    # -----------------------------------------------------
    # 4. İLK ÇALIŞMA
    # -----------------------------------------------------

    if previous_snapshot is None:

        new_snapshot = create_snapshot(
            db=db,
            monitor_id=monitor.id,
            title=content.title,
            content=content.text,
        )

        update_last_checked(
            db=db,
            monitor=monitor,
        )

        return DatabaseMonitorResult(
            monitor=monitor,
            fetch_result=fetch_result,
            content=content,
            first_run=True,
            changed=False,
            previous_snapshot=None,
            new_snapshot=new_snapshot,
            change_result=None,
            topic_analysis=None,
            change_record=None,
            ai_analysis_result=None,
            ai_analysis_record=None,
            ai_error=None,
            auto_section=auto_section,
            manual_section=manual_section,
        )

    # -----------------------------------------------------
    # 5. HASH AYNI -> DEĞİŞİKLİK YOK
    # -----------------------------------------------------

    if previous_snapshot.content_hash == current_hash:

        update_last_checked(
            db=db,
            monitor=monitor,
        )

        return DatabaseMonitorResult(
            monitor=monitor,
            fetch_result=fetch_result,
            content=content,
            first_run=False,
            changed=False,
            previous_snapshot=previous_snapshot,
            new_snapshot=None,
            change_result=None,
            topic_analysis=None,
            change_record=None,
            ai_analysis_result=None,
            ai_analysis_record=None,
            ai_error=None,
            auto_section=auto_section,
            manual_section=manual_section,
        )

    # -----------------------------------------------------
    # 6. CHANGE DETECTOR
    # -----------------------------------------------------

    change_result = detect_changes(
        old_text=previous_snapshot.content,
        new_text=content.text,
    )

    # -----------------------------------------------------
    # 6b. DIFF GURULTU AZALTMA
    #
    # Ham diff DEGISTIRILMEZ; ayri ve deterministik bir
    # asama olarak anlamli diff uretilir.
    #
    # Bu pipeline takip planini BILMEZ ve ona gore
    # dallanmaz. Azaltmanin uygulanip uygulanmayacagina
    # CAGIRAN karar verir ve reduce_noise ile bildirir.
    #
    # Bazi kaynaklarda (ornegin hukuki metinler) bir
    # bolumun baska yere TASINMASI anlamli olabilir; orada
    # cagiran bu bayragi kapatir ve ham diff korunur.
    # -----------------------------------------------------

    reduction = None

    meaningful_added = change_result.added_lines
    meaningful_removed = change_result.removed_lines

    if reduce_noise:

        reduction = reduce_diff_noise(
            added_lines=change_result.added_lines,
            removed_lines=change_result.removed_lines,
            volatile_lines=collect_volatile_lines(
                old_content=previous_snapshot.content,
                new_content=content.text,
            ),
        )

        meaningful_added = reduction.added_lines
        meaningful_removed = reduction.removed_lines

        logger.debug(
            "Diff azaltma. monitor=%s raw_added=%s "
            "raw_removed=%s moved_cancelled=%s "
            "trivial=%s volatile=%s final_added=%s "
            "final_removed=%s",
            monitor.id,
            len(change_result.added_lines),
            len(change_result.removed_lines),
            reduction.moved_lines_cancelled,
            reduction.trivial_lines_removed,
            reduction.volatile_lines_removed,
            len(meaningful_added),
            len(meaningful_removed),
        )

        # -------------------------------------------------
        # ANLAMLI DEGISIKLIK KALMADI
        #
        # Ham snapshot'lar HALA FARKLI (yeniden siralama
        # gercek bir metin farkidir), bu yuzden yeni
        # snapshot KAYDEDILIR ve karsilastirma tabani
        # ilerler. Ama kullanici acisindan degisiklik
        # YOKTUR:
        #
        #   Change kaydi YOK
        #   Gemini cagrisi YOK
        #   bildirim YOK
        #   e-posta YOK
        # -------------------------------------------------

        if reduction.is_empty:

            logger.info(
                "Degisiklik yalnizca siralama/gurultu "
                "kaynakli. monitor=%s iptal_edilen=%s",
                monitor.id,
                reduction.total_removed_noise,
            )

            new_snapshot = create_snapshot(
                db=db,
                monitor_id=monitor.id,
                title=content.title,
                content=content.text,
            )

            update_last_checked(
                db=db,
                monitor=monitor,
            )

            return DatabaseMonitorResult(
                monitor=monitor,
                fetch_result=fetch_result,
                content=content,
                first_run=False,
                changed=False,
                previous_snapshot=previous_snapshot,
                new_snapshot=new_snapshot,
                change_result=change_result,
                topic_analysis=None,
                change_record=None,
                ai_analysis_result=None,
                ai_analysis_record=None,
                ai_error=None,
                auto_section=auto_section,
                manual_section=manual_section,
            )

    # -----------------------------------------------------
    # 7. KEYWORD TOPIC ANALYSIS
    # -----------------------------------------------------

    topic_analysis = analyze_topic_relevance(
        topic=monitor.topic,
        added_lines=meaningful_added,
        removed_lines=meaningful_removed,
    )

    # -----------------------------------------------------
    # 8. YENİ SNAPSHOT
    # -----------------------------------------------------

    new_snapshot = create_snapshot(
        db=db,
        monitor_id=monitor.id,
        title=content.title,
        content=content.text,
    )

    # -----------------------------------------------------
    # 9. CHANGE DATABASE KAYDI
    # -----------------------------------------------------

    change_record = create_change(
        db=db,
        monitor_id=monitor.id,
        old_snapshot_id=previous_snapshot.id,
        new_snapshot_id=new_snapshot.id,
        relevant=topic_analysis.relevant,
        relevance_score=topic_analysis.relevance_score,
        added_lines=meaningful_added,
        removed_lines=meaningful_removed,
    )

    # -----------------------------------------------------
    # 10. GEMINI AI ANALYSIS
    # -----------------------------------------------------

    ai_analysis_result = None
    ai_analysis_record = None
    ai_error = None

    try:

        ai_outcome = await analyze_change_with_ai(
            topic=monitor.topic,
            added_lines=meaningful_added,
            removed_lines=meaningful_removed,
            keyword_relevant=topic_analysis.relevant,
            keyword_score=topic_analysis.relevance_score,
        )

        ai_analysis_result = ai_outcome.result

        # -------------------------------------------------
        # 11. AI ANALYSIS DATABASE KAYDI
        # -------------------------------------------------

        ai_analysis_record = create_ai_analysis(
            db=db,
            change_id=change_record.id,
            # Cevabi gercekten ureten model kaydedilir.
            # Fallback devreye girdiyse yedek modelin adi yazilir.
            model_name=ai_outcome.model_name,
            keyword_relevant=topic_analysis.relevant,
            keyword_score=topic_analysis.relevance_score,
            relevant=ai_analysis_result.relevant,
            importance=ai_analysis_result.importance,
            category=ai_analysis_result.category,
            summary=ai_analysis_result.summary,
            notification_recommended=(
                ai_analysis_result.notification_recommended
            ),
            evidence_text=ai_analysis_result.evidence_text,
            evidence_type=ai_analysis_result.evidence_type,
        )

    except AIAnalyzerError as exc:

        # Gemini geçici olarak hata verse bile
        # snapshot ve change kayıtlarımız kaybolmaz.
        ai_error = str(exc)

    # -----------------------------------------------------
    # 12. SON KONTROL ZAMANI
    # -----------------------------------------------------

    update_last_checked(
        db=db,
        monitor=monitor,
    )

    return DatabaseMonitorResult(
        monitor=monitor,
        fetch_result=fetch_result,
        content=content,
        first_run=False,
        changed=change_result.changed,
        previous_snapshot=previous_snapshot,
        new_snapshot=new_snapshot,
        change_result=change_result,
        topic_analysis=topic_analysis,
        change_record=change_record,
        ai_analysis_result=ai_analysis_result,
        ai_analysis_record=ai_analysis_record,
        ai_error=ai_error,
        auto_section=auto_section,
        manual_section=manual_section,
    )