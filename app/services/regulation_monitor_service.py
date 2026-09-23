"""
TAKIP PLANI ve MEVZUAT KARSILASTIRMA ORKESTRASYONU

Bu modul regulation_monitor pipeline'inin ust katmanidir.

AKIS

    change
      -> normal Gemini konu/degisiklik analizi   (mevcut)
      -> relevant?
           evet -> mevzuat karsilastirmasi        (bu modul)
      -> persist
      -> notification

Mevcut AI analizi KALDIRILMAZ. Karsilastirma onun uzerine
eklenen ek bir zeka katmanidir.

KARSILASTIRMA NE ZAMAN CALISIR

Yalnizca su dort kosul birlikte saglandiginda:

    1. monitor.tracking_plan == regulation_monitor
    2. gercek bir Change olustu (ilk snapshot degil)
    3. AI analizi relevant == True
    4. monitorde bir referans yonetmelik bagli

Aksi durumlarda calistirma yaniti NEDENINI acikca soyler;
"calisti" izlenimi verilmez.

HATA IZOLASYONU

Karsilastirma basarisiz olursa snapshot, change ve
AIAnalysis KORUNUR, scheduler cokmez. Sonuc status="failed"
satiri olarak yazilir ve hata mesaji temizlenir.
"""

import logging

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models.monitor import Monitor
from app.models.regulation_comparison import (
    RegulationComparison,
)
from app.repositories.regulation_reference_repository import (
    get_regulation_references,
)
from app.repositories.regulation_comparison_repository import (
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_NO_MATCH,
    create_comparison,
    get_comparison_by_change,
)
from app.services.regulation_candidate_matcher import (
    MAX_CANDIDATES,
    select_candidates,
)
from app.services.regulation_comparator import (
    RegulationComparisonError,
    compare_regulation_change,
)
from app.services.regulation_discovery_service import (
    DISCOVERY_FAILED,
    DISCOVERY_NOT_APPLICABLE,
    DISCOVERY_NOT_NEEDED,
    discover_reference,
)
from app.services.regulation_freshness_service import (
    FRESHNESS_NOT_APPLICABLE,
    FRESHNESS_STALE_UNVERIFIED,
    ensure_reference_fresh,
)
from app.services.regulation_reference_matcher import (
    MATCH_STATUS_MATCHED,
    MATCH_STATUS_NEEDS_REVIEW,
    MATCH_STATUS_NO_MATCH,
    MATCH_STATUS_NO_REFERENCES,
    match_reference,
)
from app.services.regulation_source_builder import (
    MAX_SOURCE_TEXT_CHARS,
    build_comparison_source,
)
from app.services.regulation_text_parser import (
    parse_regulation_segments,
)
from app.services.regulation_title_parser import (
    extract_base_regulation_names,
)


logger = logging.getLogger(__name__)


# ---------------------------------------------------------
# TAKIP PLANI
# ---------------------------------------------------------

TRACKING_PLAN_WEBSITE = "website_monitor"
TRACKING_PLAN_REGULATION = "regulation_monitor"

TRACKING_PLANS = (
    TRACKING_PLAN_WEBSITE,
    TRACKING_PLAN_REGULATION,
)


# Takip plani belirtilmemis her kayit (eski kayitlar dahil)
# standart web sitesi takibi kabul edilir.
DEFAULT_TRACKING_PLAN = TRACKING_PLAN_WEBSITE


# ---------------------------------------------------------
# KARSILASTIRMA DURUMLARI
#
# Kalici (veritabaninda saklanan) durumlar:
#   completed | no_match | failed
#
# Yalnizca CALISTIRMA YANITINDA gorunen, karsilastirmanin
# neden hic calismadigini soyleyen durumlar:
#   skipped_first_run | skipped_no_change |
#   skipped_irrelevant | skipped_no_reference
#
# Bu "skipped" degerleri veritabanina YAZILMAZ; bir
# karsilastirma yapilmadigi icin saklanacak bir sonuc da
# yoktur.
# ---------------------------------------------------------

COMPARISON_COMPLETED = STATUS_COMPLETED
COMPARISON_NO_MATCH = STATUS_NO_MATCH
COMPARISON_FAILED = STATUS_FAILED

COMPARISON_SKIPPED_FIRST_RUN = "skipped_first_run"
COMPARISON_SKIPPED_NO_CHANGE = "skipped_no_change"
COMPARISON_SKIPPED_IRRELEVANT = "skipped_irrelevant"
COMPARISON_SKIPPED_NO_REFERENCE = "skipped_no_reference"

# OTOMATIK MODDA REFERANS BELIRLENEMEDI
#
# Eslestirme guvenilir tek bir referans veremedi.
# Bu durumda karsilastirma CALISTIRILMAZ; uydurma bir
# referansla sahte sonuc uretmektense hicbir sey
# uretmemek dogrudur.
COMPARISON_SKIPPED_NO_AUTO_MATCH = "skipped_no_auto_match"

# Referans metni dogrulanamadi ve onbellekte de
# kullanilabilir metin yok.
COMPARISON_SKIPPED_STALE_REFERENCE = "skipped_stale_reference"


# ---------------------------------------------------------
# REFERANS SECIM MODU
# ---------------------------------------------------------

REFERENCE_MODE_AUTOMATIC = "automatic"
REFERENCE_MODE_MANUAL = "manual"

REFERENCE_SELECTION_MODES = (
    REFERENCE_MODE_AUTOMATIC,
    REFERENCE_MODE_MANUAL,
)


# Mod tasimayan eski kayitlar: referansini kullanici
# secmisti, o yuzden manual kabul edilir.
DEFAULT_REFERENCE_SELECTION_MODE = REFERENCE_MODE_MANUAL


# ---------------------------------------------------------
# ESLESTIRME YONTEMI
#
# Referansin comparison satirinda NASIL belirlendigini
# saklar.
# ---------------------------------------------------------

MATCH_METHOD_MANUAL = "manual"
MATCH_METHOD_AUTOMATIC_EXACT = "automatic_exact"
MATCH_METHOD_AUTOMATIC_BASE = "automatic_base"
MATCH_METHOD_AUTOMATIC_AI = "automatic_ai"


# Otomatik secimin kabul edilmesi icin gereken en dusuk
# guven. Matcher zaten kendi esigini uyguluyor; bu ikinci
# bir emniyet katmanidir.
MIN_AUTOMATIC_CONFIDENCE = 0.60


def resolve_reference_selection_mode(
    value: str | None,
) -> str:
    """
    Kayittaki secim modunu normalize eder.

    Bos/None deger eski kayit demektir ve MANUAL kabul
    edilir; kullanicinin sectigi referans sessizce
    devre disi birakilmaz.
    """

    cleaned = (value or "").strip()

    if cleaned not in REFERENCE_SELECTION_MODES:
        return DEFAULT_REFERENCE_SELECTION_MODE

    return cleaned


def is_automatic_reference_mode(monitor: Monitor) -> bool:

    return (
        resolve_reference_selection_mode(
            getattr(monitor, "reference_selection_mode", None)
        )
        == REFERENCE_MODE_AUTOMATIC
    )


def resolve_match_method(match_result) -> str:
    """
    Otomatik eslesmenin hangi yolla bulundugunu belirler.
    """

    if match_result.ai_used:
        return MATCH_METHOD_AUTOMATIC_AI

    if match_result.score >= 0.999:
        return MATCH_METHOD_AUTOMATIC_EXACT

    return MATCH_METHOD_AUTOMATIC_BASE


def resolve_tracking_plan(value: str | None) -> str:
    """
    Kayittaki takip planini normalize eder.

    Bos/None deger eski kayit demektir ve website_monitor
    olarak yorumlanir.
    """

    cleaned = (value or "").strip()

    if not cleaned:
        return DEFAULT_TRACKING_PLAN

    return cleaned


def is_regulation_monitor(monitor: Monitor) -> bool:

    return (
        resolve_tracking_plan(monitor.tracking_plan)
        == TRACKING_PLAN_REGULATION
    )


@dataclass
class RegulationComparisonRunResult:
    """
    Karsilastirma denemesinin sonucu.

    comparison None ise hicbir kayit olusturulmamistir;
    status neden calismadigini soyler.
    """

    status: str
    comparison: RegulationComparison | None = None

    # Yeni bir Gemini karsilastirma cagrisi yapildi mi?
    # Duplicate korumasi bu bayrakla test edilebilir.
    ai_call_made: bool = False

    # Ayni change icin daha once yapilmis karsilastirma
    # yeniden kullanildi mi?
    reused_existing: bool = False

    # ---------------------------------------------------
    # REFERANS SECIMI
    #
    # Karsilastirma OLUSMASA bile doldurulur; boylece
    # kullanici "neden calismadi" sorusunun cevabini
    # gorur.
    # ---------------------------------------------------

    # matched | ambiguous | no_match | needs_review |
    # no_base_name | no_references
    match_status: str | None = None

    # Eslestirmenin insan tarafindan okunabilir aciklamasi.
    match_detail: str = ""

    # Eslestirme icin Gemini cagrildi mi?
    matcher_ai_used: bool = False

    # fresh | refreshed | refreshed_changed |
    # stale_unverified | not_applicable
    freshness_status: str | None = None

    # ---------------------------------------------------
    # OTOMATIK REFERANS KESFI
    #
    # Kayitli kutuphanede referans BULUNAMADIGINDA resmi
    # kaynakta (mevzuat.gov.tr) yapilan aramanin sonucu.
    #
    # website_monitor            -> None
    # manuel mod                 -> not_applicable
    # yerel eslestirme tuttu     -> not_needed
    #
    # not_needed | not_applicable | found_existing |
    # discovered | no_match | ambiguous | discovery_failed
    # ---------------------------------------------------

    discovery_status: str | None = None

    # Kesifle bulunan/olusturulan referans.
    discovered_reference_id: int | None = None
    discovered_reference_title: str | None = None

    # Kesif icin Gemini cagrildi mi?
    discovery_ai_used: bool = False

    # Kesfin insan tarafindan okunabilir aciklamasi.
    discovery_detail: str = ""

    @property
    def item_count(self) -> int:
        if self.comparison is None:
            return 0

        return len(self.comparison.items)


def _sanitize_error(message: str) -> str:
    """
    Kullaniciya gosterilecek hata mesajini temizler.

    Stack trace, dosya yolu ve API anahtari gibi degerler
    disari sizmaz. Mesaj kisaltilir.
    """

    cleaned = " ".join((message or "").split())

    if not cleaned:
        return "Bilinmeyen hata."

    # Olasi anahtar/token benzeri uzun kelimeleri at.
    parts = [
        "***" if len(word) > 40 else word
        for word in cleaned.split(" ")
    ]

    cleaned = " ".join(parts)

    return cleaned[:300]


async def run_regulation_comparison(
    db: Session,
    monitor: Monitor,
    result,
) -> RegulationComparisonRunResult:
    """
    Bir monitor calistirmasinin ardindan mevzuat
    karsilastirmasini yapar.

    result, db_monitor_service.run_database_monitor()
    ciktisidir.

    Bu fonksiyon HICBIR ZAMAN hata firlatmaz. Basarisizlik
    status="failed" satiri olarak doner; snapshot, change ve
    AIAnalysis kayitlari korunur.
    """

    # -----------------------------------------------------
    # 1. TETIKLEME KOSULLARI
    # -----------------------------------------------------

    if not is_regulation_monitor(monitor):
        # website_monitor: karsilastirma soz konusu degil.
        return RegulationComparisonRunResult(
            status=None,
        )

    if result.first_run:
        return RegulationComparisonRunResult(
            status=COMPARISON_SKIPPED_FIRST_RUN,
        )

    change = result.change_record

    if change is None or not result.changed:
        return RegulationComparisonRunResult(
            status=COMPARISON_SKIPPED_NO_CHANGE,
        )

    ai_analysis = result.ai_analysis_record

    if ai_analysis is None or not ai_analysis.relevant:
        # Konuyla ilgisiz degisiklik icin mevzuat
        # karsilastirmasi yapilmaz; bosuna kota harcanmaz.
        return RegulationComparisonRunResult(
            status=COMPARISON_SKIPPED_IRRELEVANT,
        )

    # -----------------------------------------------------
    # 2. DUPLICATE KORUMASI
    #
    # Gemini cagrisindan ONCE. Ayni change ikinci kez
    # islenirse yeni model cagrisi CIKMAZ.
    # -----------------------------------------------------

    existing = get_comparison_by_change(
        db=db,
        change_id=change.id,
    )

    if existing is not None:
        return RegulationComparisonRunResult(
            status=existing.status,
            comparison=existing,
            ai_call_made=False,
            reused_existing=True,
        )

    automatic_mode = is_automatic_reference_mode(monitor)

    reference = monitor.reference_regulation

    if reference is None and not automatic_mode:
        # MANUAL modda referans zorunludur.
        # Sema ve API dogrulamasi bunu engelliyor;
        # burasi eski/bozuk kayitlar icin son savunma.
        return RegulationComparisonRunResult(
            status=COMPARISON_SKIPPED_NO_REFERENCE,
        )

    # -----------------------------------------------------
    # 3. KAYNAK METIN
    #
    # TEK KAYNAK KURALI:
    #
    # Asagidaki metin hem Gemini prompt'una girer hem de
    # source_evidence / new_text exact dogrulamasinda
    # kullanilir. Modelin gordugu her cumle sunucunun da
    # dogrulayabildigi bir cumledir.
    #
    # Tum snapshot GONDERILMEZ; kaynak kurucu oncelik
    # sirasina gore sinirli bir metin uretir.
    # -----------------------------------------------------

    comparison_source = build_comparison_source(
        change=change,
        new_snapshot_content=(
            result.new_snapshot.content
            if getattr(result, "new_snapshot", None)
            is not None
            else None
        ),
        max_chars=MAX_SOURCE_TEXT_CHARS,
    )

    source_text = comparison_source.text

    if not source_text:
        return RegulationComparisonRunResult(
            status=COMPARISON_SKIPPED_NO_CHANGE,
        )

    # -----------------------------------------------------
    # -----------------------------------------------------
    # REFERANS SECIMI
    #
    # AUTOMATIC MOD
    #   Referans, degisiklik metninden OTOMATIK belirlenir.
    #   Kullanicidan onay BEKLENMEZ; matcher guvenilir tek
    #   bir sonuc verirse karsilastirma dogrudan calisir.
    #
    #   Guvenilir sonuc YOKSA (ambiguous / no_match /
    #   no_base_name / no_references) karsilastirma
    #   CALISTIRILMAZ. Uydurma referansla sahte sonuc
    #   uretmektense hicbir sey uretmemek dogrudur.
    #
    # MANUAL MOD
    #   Kullanicinin secimi KESIN. Matcher yalnizca
    #   dogrulama amaciyla ve allow_ai=False ile calisir;
    #   ek Gemini maliyeti uretmez, secimi degistirmez ve
    #   karsilastirmayi bloklamaz.
    # -----------------------------------------------------

    match_status = None
    match_method = None
    match_confidence = None
    suggested_reference_id = None
    matcher_ai_used = False

    # KESIF
    #
    # Manuel modda resmi kesif ASLA calismaz; kullanicinin
    # secimi kesindir ve arkasindan yeni referans
    # uretilmez.
    discovery_status = (
        None
        if automatic_mode
        else DISCOVERY_NOT_APPLICABLE
    )

    # Kesifle bulunan referans. None ise kesif bir referans
    # URETMEDI ve mevcut secim mantigi degismeden isler.
    discovered_reference = None

    discovery_reference_id = None
    discovery_reference_title = None
    discovery_ai_used = False
    discovery_detail = ""

    if automatic_mode:

        try:
            match_result = await match_reference(
                source_text=source_text,
                references=get_regulation_references(db),

                # Otomatik modda manuel secim YOKTUR;
                # matcher serbestce karar verir.
                manual_reference=None,

                # Belirsizlik AI ile cozulebilir.
                # En fazla BIR mantiksal cagri yapilir ve
                # dusuk guvenli sonuc kabul edilmez.
                allow_ai=True,
            )

        except Exception as exc:  # noqa: BLE001

            logger.exception(
                "Otomatik referans eslestirme basarisiz. "
                "monitor_id=%s",
                monitor.id,
            )

            return RegulationComparisonRunResult(
                status=COMPARISON_SKIPPED_NO_AUTO_MATCH,
                match_status=MATCH_STATUS_NO_MATCH,
                match_detail=_sanitize_error(
                    f"{type(exc).__name__}: {exc}"
                ),
            )

        match_status = match_result.status
        matcher_ai_used = match_result.ai_used

        local_matched = (
            match_result.status == MATCH_STATUS_MATCHED
            and match_result.reference is not None
            and match_result.score
            >= MIN_AUTOMATIC_CONFIDENCE
        )

        if local_matched:
            discovery_status = DISCOVERY_NOT_NEEDED

        # -------------------------------------------------
        # RESMI KESIF
        #
        # Kayitli kutuphanede guvenilir referans yoksa
        # mevzuat resmi kaynakta (mevzuat.gov.tr) aranir.
        #
        # YALNIZCA su iki yerel sonucta denenir:
        #
        #   no_match       -> kayitli referanslar uymadi
        #   no_references  -> hic kayitli referans yok
        #
        # ambiguous / needs_review durumunda DENENMEZ:
        # yerel belirsizligi resmi kaynaktan yeni bir satir
        # acarak "cozmek" duplicate referans uretirdi.
        # Mevcut belirsizlik davranisi KORUNUR.
        #
        # no_base_name durumunda arama yapilamaz.
        # -------------------------------------------------

        discovery_result = None

        if not local_matched:

            discovery_status = DISCOVERY_NOT_NEEDED

            if match_result.status in (
                MATCH_STATUS_NO_MATCH,
                MATCH_STATUS_NO_REFERENCES,
            ):
                # no_references yolunda matcher base adi
                # cikarmadan doner; ad burada cikarilir.
                base_name = match_result.base_name

                if base_name is None:
                    base_name = (
                        extract_base_regulation_names(
                            source_text=source_text,
                        ).primary
                    )

                if base_name is not None:

                    try:
                        discovery_result = (
                            await discover_reference(
                                db=db,
                                base_name=base_name,
                            )
                        )

                    except Exception as exc:  # noqa: BLE001
                        # Kesif HICBIR kosulda pipeline'i
                        # dusurmez.
                        logger.exception(
                            "Resmi referans kesfi "
                            "beklenmeyen sekilde basarisiz. "
                            "monitor_id=%s",
                            monitor.id,
                        )

                        discovery_status = DISCOVERY_FAILED
                        discovery_detail = _sanitize_error(
                            f"{type(exc).__name__}: {exc}"
                        )

        if discovery_result is not None:

            discovery_status = discovery_result.status
            discovery_ai_used = discovery_result.ai_used
            discovery_detail = discovery_result.detail

            if discovery_result.usable:

                # KESIF BASARILI
                #
                # Referans resmi kaynaktan DOGRULANARAK
                # olusturuldu (veya zaten vardi). Mevcut
                # tazelik/karsilastirma akisi buradan
                # DEGISMEDEN devam eder.
                discovered_reference = (
                    discovery_result.reference
                )

                reference = discovered_reference

                discovery_reference_id = reference.id
                discovery_reference_title = reference.title

                match_status = MATCH_STATUS_MATCHED
                match_confidence = discovery_result.score
                match_method = discovery_result.match_method

                logger.info(
                    "Referans resmi kaynaktan belirlendi. "
                    "monitor_id=%s reference_id=%s "
                    "durum=%s yontem=%s",
                    monitor.id,
                    reference.id,
                    discovery_status,
                    match_method,
                )

                local_matched = True

        # -------------------------------------------------
        # GUVENILIR SONUC YOKSA CALISTIRMA
        # -------------------------------------------------

        if not local_matched:
            logger.info(
                "Otomatik referans belirlenemedi. "
                "monitor_id=%s durum=%s skor=%.3f kesif=%s",
                monitor.id,
                match_result.status,
                match_result.score,
                discovery_status,
            )

            # KAYIT BIRAKILIR
            #
            # Karsilastirma yapilmadi ama DENENDI. Kayit
            # birakmanin iki faydasi var:
            #
            #   1. bildirim detayi kullaniciya NEDEN
            #      calismadigini soyleyebilir
            #   2. ayni change yeniden islenirse matcher
            #      (ve olasi AI cagrisi) TEKRARLANMAZ
            #
            # reference_regulation_id BILEREK null: hicbir
            # referans kullanilmadi ve uydurulmaz.
            return RegulationComparisonRunResult(
                status=COMPARISON_SKIPPED_NO_AUTO_MATCH,
                match_status=match_result.status,
                match_detail=match_result.detail,
                matcher_ai_used=matcher_ai_used,
                discovery_status=discovery_status,
                discovery_ai_used=discovery_ai_used,
                discovery_detail=discovery_detail,
                comparison=create_comparison(
                    db=db,
                    monitor_id=monitor.id,
                    change_id=change.id,
                    ai_analysis_id=ai_analysis.id,
                    reference_regulation_id=None,
                    status=COMPARISON_NO_MATCH,
                    reference_match_status=match_result.status,
                    reference_match_method=None,
                    reference_match_confidence=(
                        match_result.score or None
                    ),
                    reference_discovery_status=(
                        discovery_status
                    ),
                    suggested_reference_id=(
                        match_result.reference.id
                        if match_result.reference is not None
                        else None
                    ),
                ),
            )

        # -------------------------------------------------
        # KESIFLE BULUNAN REFERANS KORUNUR
        #
        # reference kesif basariliysa zaten dolduruldu ve
        # match_method / match_confidence oradan geldi.
        # Yerel eslestirmenin (bos) sonucu ONUN UZERINE
        # YAZILMAZ.
        # -------------------------------------------------

        if discovered_reference is None:
            reference = match_result.reference
            match_confidence = match_result.score
            match_method = resolve_match_method(match_result)

        logger.info(
            "Referans otomatik secildi. monitor_id=%s "
            "reference_id=%s yontem=%s guven=%.3f ai=%s "
            "kesif=%s",
            monitor.id,
            reference.id,
            match_method,
            match_confidence or 0.0,
            matcher_ai_used,
            discovery_status,
        )

    else:

        # -------------------------------------------------
        # MANUEL MOD: yalnizca dogrulama
        # -------------------------------------------------

        match_method = MATCH_METHOD_MANUAL

        try:
            match_result = await match_reference(
                source_text=source_text,
                references=get_regulation_references(db),
                manual_reference=reference,

                # MANUEL modda EK GEMINI MALIYETI YOK.
                allow_ai=False,
            )

            match_status = match_result.status

            if (
                match_result.status
                == MATCH_STATUS_NEEDS_REVIEW
                and match_result.reference is not None
            ):
                suggested_reference_id = (
                    match_result.reference.id
                )

                logger.info(
                    "Referans gozden gecirilmeli. "
                    "monitor_id=%s manuel=%s onerilen=%s",
                    monitor.id,
                    reference.id,
                    suggested_reference_id,
                )

        except Exception:  # noqa: BLE001
            # Dogrulama bir YARDIMCIDIR; basarisiz olmasi
            # karsilastirmayi engellemez.
            logger.exception(
                "Referans eslestirme dogrulamasi basarisiz. "
                "monitor_id=%s",
                monitor.id,
            )

    # -----------------------------------------------------
    # REFERANS TAZELIGI
    #
    # Konsolide metin degisir; onbellekteki eski bir metinle
    # karsilastirma yapmak aradaki degisiklikleri gormemek
    # demektir.
    #
    # 24 saatten yeni dogrulanmis referans icin AG ISTEGI
    # YAPILMAZ.
    # -----------------------------------------------------

    freshness = await ensure_reference_fresh(
        db=db,
        reference=reference,
    )

    freshness_status = freshness.status

    if not freshness.usable:
        # Elde guvenilir metin yok; sahte karsilastirma
        # uretilmez.
        logger.warning(
            "Referans dogrulanamadi ve onbellekte "
            "kullanilabilir metin yok. monitor_id=%s",
            monitor.id,
        )

        return RegulationComparisonRunResult(
            status=COMPARISON_SKIPPED_STALE_REFERENCE,
            match_status=match_status,
            freshness_status=freshness_status,
            matcher_ai_used=matcher_ai_used,
            discovery_status=discovery_status,
            discovered_reference_id=discovery_reference_id,
            discovered_reference_title=discovery_reference_title,
            discovery_ai_used=discovery_ai_used,
            discovery_detail=discovery_detail,
        )

    # -----------------------------------------------------
    # 4. YEREL SEGMENTASYON VE ADAY SECIMI
    #
    # Referans yonetmeligin TAMAMI modele GONDERILMEZ.
    # -----------------------------------------------------

    try:
        document = parse_regulation_segments(
            reference.content or ""
        )

        candidates = select_candidates(
            source_text=source_text,
            segments=document.segments,
            max_candidates=MAX_CANDIDATES,
        )

    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "Referans yonetmelik ayristirilamadi. "
            "monitor_id=%s",
            monitor.id,
        )

        return RegulationComparisonRunResult(
            status=COMPARISON_FAILED,
            match_status=match_status,
            freshness_status=freshness_status,
            matcher_ai_used=matcher_ai_used,
            discovery_status=discovery_status,
            discovered_reference_id=discovery_reference_id,
            discovered_reference_title=discovery_reference_title,
            discovery_ai_used=discovery_ai_used,
            discovery_detail=discovery_detail,
            comparison=create_comparison(
                db=db,
                monitor_id=monitor.id,
                change_id=change.id,
                ai_analysis_id=ai_analysis.id,
                reference_regulation_id=reference.id,
                reference_content_hash=reference.content_hash,
                reference_is_consolidated=(
                    reference.is_consolidated
                ),
                reference_match_status=match_status,
                reference_match_method=match_method,
                reference_match_confidence=match_confidence,
                reference_freshness_status=freshness_status,
                reference_discovery_status=discovery_status,
                suggested_reference_id=suggested_reference_id,
                status=COMPARISON_FAILED,
                error_message=_sanitize_error(
                    f"{type(exc).__name__}: {exc}"
                ),
            ),
        )

    if not candidates:
        # Referans metinde guvenilir bir aday bulunamadi.
        # Uydurma eslesme URETILMEZ.
        return RegulationComparisonRunResult(
            status=COMPARISON_NO_MATCH,
            match_status=match_status,
            freshness_status=freshness_status,
            matcher_ai_used=matcher_ai_used,
            discovery_status=discovery_status,
            discovered_reference_id=discovery_reference_id,
            discovered_reference_title=discovery_reference_title,
            discovery_ai_used=discovery_ai_used,
            discovery_detail=discovery_detail,
            comparison=create_comparison(
                db=db,
                monitor_id=monitor.id,
                change_id=change.id,
                ai_analysis_id=ai_analysis.id,
                reference_regulation_id=reference.id,
                reference_content_hash=reference.content_hash,
                reference_is_consolidated=(
                    reference.is_consolidated
                ),
                reference_match_status=match_status,
                reference_match_method=match_method,
                reference_match_confidence=match_confidence,
                reference_freshness_status=freshness_status,
                reference_discovery_status=discovery_status,
                suggested_reference_id=suggested_reference_id,
                status=COMPARISON_NO_MATCH,
                overall_summary=None,
            ),
        )

    # -----------------------------------------------------
    # 5. GEMINI KARSILASTIRMASI
    #
    # Bir mantiksal cagri (primary + yalnizca kota
    # durumunda tek fallback).
    # -----------------------------------------------------

    try:
        outcome = await compare_regulation_change(
            topic=monitor.topic,
            regulation_title=reference.title,
            source_text=source_text,
            candidates=candidates,
        )

    except (
        RegulationComparisonError,
        ValueError,
    ) as exc:

        logger.warning(
            "Mevzuat karsilastirmasi basarisiz. "
            "monitor_id=%s hata=%s",
            monitor.id,
            exc,
        )

        return RegulationComparisonRunResult(
            status=COMPARISON_FAILED,
            ai_call_made=True,
            match_status=match_status,
            matcher_ai_used=matcher_ai_used,
            freshness_status=freshness_status,
            discovery_status=discovery_status,
            discovered_reference_id=discovery_reference_id,
            discovered_reference_title=discovery_reference_title,
            discovery_ai_used=discovery_ai_used,
            discovery_detail=discovery_detail,
            comparison=create_comparison(
                db=db,
                monitor_id=monitor.id,
                change_id=change.id,
                ai_analysis_id=ai_analysis.id,
                reference_regulation_id=reference.id,
                reference_content_hash=reference.content_hash,
                reference_is_consolidated=(
                    reference.is_consolidated
                ),
                reference_match_status=match_status,
                reference_match_method=match_method,
                reference_match_confidence=match_confidence,
                reference_freshness_status=freshness_status,
                reference_discovery_status=discovery_status,
                suggested_reference_id=suggested_reference_id,
                status=COMPARISON_FAILED,
                error_message=_sanitize_error(str(exc)),
            ),
        )

    except Exception as exc:  # noqa: BLE001

        # Beklenmeyen bir hata da monitor run'ini dusurmez.
        logger.exception(
            "Mevzuat karsilastirmasinda beklenmeyen hata. "
            "monitor_id=%s",
            monitor.id,
        )

        return RegulationComparisonRunResult(
            status=COMPARISON_FAILED,
            ai_call_made=True,
            match_status=match_status,
            matcher_ai_used=matcher_ai_used,
            freshness_status=freshness_status,
            discovery_status=discovery_status,
            discovered_reference_id=discovery_reference_id,
            discovered_reference_title=discovery_reference_title,
            discovery_ai_used=discovery_ai_used,
            discovery_detail=discovery_detail,
            comparison=create_comparison(
                db=db,
                monitor_id=monitor.id,
                change_id=change.id,
                ai_analysis_id=ai_analysis.id,
                reference_regulation_id=reference.id,
                reference_content_hash=reference.content_hash,
                reference_is_consolidated=(
                    reference.is_consolidated
                ),
                reference_match_status=match_status,
                reference_match_method=match_method,
                reference_match_confidence=match_confidence,
                reference_freshness_status=freshness_status,
                reference_discovery_status=discovery_status,
                suggested_reference_id=suggested_reference_id,
                status=COMPARISON_FAILED,
                error_message=_sanitize_error(
                    f"{type(exc).__name__}: {exc}"
                ),
            ),
        )

    # -----------------------------------------------------
    # 6. SONUCU YAZ
    #
    # Dogrulanmis item yoksa sonuc no_match'tir. Modelin
    # "degisiklik var" demesi tek basina yeterli DEGILDIR.
    # -----------------------------------------------------

    if not outcome.items:

        return RegulationComparisonRunResult(
            status=COMPARISON_NO_MATCH,
            ai_call_made=True,
            match_status=match_status,
            matcher_ai_used=matcher_ai_used,
            freshness_status=freshness_status,
            discovery_status=discovery_status,
            discovered_reference_id=discovery_reference_id,
            discovered_reference_title=discovery_reference_title,
            discovery_ai_used=discovery_ai_used,
            discovery_detail=discovery_detail,
            comparison=create_comparison(
                db=db,
                monitor_id=monitor.id,
                change_id=change.id,
                ai_analysis_id=ai_analysis.id,
                reference_regulation_id=reference.id,
                reference_content_hash=reference.content_hash,
                reference_is_consolidated=(
                    reference.is_consolidated
                ),
                reference_match_status=match_status,
                reference_match_method=match_method,
                reference_match_confidence=match_confidence,
                reference_freshness_status=freshness_status,
                reference_discovery_status=discovery_status,
                suggested_reference_id=suggested_reference_id,
                status=COMPARISON_NO_MATCH,
                overall_summary=(
                    outcome.overall_summary or None
                ),
                importance=outcome.importance,
                model_name=outcome.model_name,
            ),
        )

    item_rows = [
        {
            "affected_article": item.affected_article,
            "affected_paragraph": item.affected_paragraph,
            "change_type": item.change_type,
            "old_text": item.old_text,
            "new_text": item.new_text,
            "source_evidence": item.source_evidence,
            "reference_evidence": item.reference_evidence,
            "change_summary": item.change_summary,
            "confidence": item.confidence,
        }
        for item in outcome.items
    ]

    comparison = create_comparison(
        db=db,
        monitor_id=monitor.id,
        change_id=change.id,
        ai_analysis_id=ai_analysis.id,
        reference_regulation_id=reference.id,
        reference_content_hash=reference.content_hash,
        reference_is_consolidated=reference.is_consolidated,
        reference_match_status=match_status,
        reference_match_method=match_method,
        reference_match_confidence=match_confidence,
        reference_freshness_status=freshness_status,
        reference_discovery_status=discovery_status,
        suggested_reference_id=suggested_reference_id,
        status=COMPARISON_COMPLETED,
        overall_summary=outcome.overall_summary or None,
        importance=outcome.importance,
        model_name=outcome.model_name,
        items=item_rows,
    )

    logger.info(
        "Mevzuat karsilastirmasi tamamlandi. monitor_id=%s "
        "change_id=%s item=%s model=%s",
        monitor.id,
        change.id,
        len(item_rows),
        outcome.model_name,
    )

    return RegulationComparisonRunResult(
        status=COMPARISON_COMPLETED,
        comparison=comparison,
        ai_call_made=True,
        match_status=match_status,
        matcher_ai_used=matcher_ai_used,
        freshness_status=freshness_status,
        discovery_status=discovery_status,
        discovered_reference_id=discovery_reference_id,
        discovered_reference_title=discovery_reference_title,
        discovery_ai_used=discovery_ai_used,
        discovery_detail=discovery_detail,
    )
