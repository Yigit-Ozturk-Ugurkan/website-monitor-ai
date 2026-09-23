from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.repositories.monitor_repository import (
    create_monitor,
    get_monitor,
    get_monitors,
    should_reset_regulation_reference,
    update_monitor,
)
from app.repositories.regulation_reference_repository import (
    get_regulation_reference,
)
from app.schemas.monitor import (
    MonitorCreate,
    MonitorResponse,
    MonitorRunResponse,
    MonitorUpdate,
    SectionCandidatePreview,
    SectionCandidateRequest,
    SectionCandidatesResponse,
    validate_monitor_settings,
)
from app.services.monitor_run_service import (
    acquire_monitor_run,
    execute_monitor_run,
    release_monitor_run,
)
from app.services.regulation_monitor_service import (
    is_regulation_monitor,
    resolve_reference_selection_mode,
)
from app.services.section_extractor import (
    ManualSectionResolutionError,
    extract_section_candidates,
)
from app.services.section_selector import (
    AutoSectionSelectionError,
)
from app.services.web_fetcher import (
    WebsiteFetchError,
    fetch_website,
)


router = APIRouter(
    prefix="/api/monitors",
    tags=["Monitors"],
)


# Frontend'e gonderilen onizleme metni siniri.
# Tum bolum metnini gondermek gereksiz buyuk payload uretir.
MAX_CANDIDATE_PREVIEW_CHARS = 500


def build_candidate_preview(text: str) -> str:
    """
    Duz metin onizleme uretir.

    Donen deger HTML DEGILDIR; frontend bunu metin olarak
    render eder (dangerouslySetInnerHTML kullanilmaz).
    """

    if len(text) <= MAX_CANDIDATE_PREVIEW_CHARS:
        return text

    return text[:MAX_CANDIDATE_PREVIEW_CHARS].rstrip() + "..."


@router.post(
    "/section-candidates",
    response_model=SectionCandidatesResponse,
)
async def list_section_candidates(
    payload: SectionCandidateRequest,
):
    """
    Manuel alan secimi icin sayfadaki aday bolumleri dondurur.

    Bu endpoint:
      - Gemini CAGIRMAZ
      - veritabanina HICBIR SEY YAZMAZ
      - yalnizca sayfayi indirip bolumleri cikarir

    Sayfada uygun bolum yoksa 200 ve bos liste doner;
    kullaniciya sessizce full_page secilmez.
    """

    try:
        fetch_result = await fetch_website(
            payload.url
        )

    except WebsiteFetchError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Website getirilemedi: {exc}",
        ) from exc

    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=str(exc),
        ) from exc

    discovery = extract_section_candidates(
        fetch_result.html
    )

    return SectionCandidatesResponse(
        page_title=discovery.page_title,
        candidates=[
            SectionCandidatePreview(
                candidate_id=candidate.candidate_id,
                label=candidate.label,
                reference=candidate.reference,
                tag=candidate.tag,
                text_length=candidate.text_length,
                preview=build_candidate_preview(
                    candidate.text
                ),
            )
            for candidate in discovery.candidates
        ],
    )


INVALID_REFERENCE_MESSAGE = (
    "Seçilen referans yönetmelik bulunamadı. Listeyi "
    "yenileyip tekrar seçin."
)


def ensure_regulation_reference_exists(
    db: Session,
    reference_regulation_id: int | None,
) -> None:
    """
    Secilen referansin GERCEKTEN var oldugunu dogrular.

    Sema katmani yalnizca "regulation_monitor ise bir id
    gonderilmis mi" sorusunu cevaplayabiliyor; var olup
    olmadigi veritabani gerektirdigi icin burada
    kontrol edilir.

    Backend frontend'e guvenmez: uydurma bir id ile
    regulation monitor olusturulamaz.
    """

    if reference_regulation_id is None:
        return

    reference = get_regulation_reference(
        db=db,
        reference_id=reference_regulation_id,
    )

    if reference is None:
        raise HTTPException(
            status_code=422,
            detail=INVALID_REFERENCE_MESSAGE,
        )


@router.get(
    "",
    response_model=list[MonitorResponse],
)
def list_monitors(
    db: Session = Depends(get_db),
):
    return get_monitors(db)


@router.get(
    "/{monitor_id}",
    response_model=MonitorResponse,
)
def monitor_detail(
    monitor_id: int,
    db: Session = Depends(get_db),
):
    monitor = get_monitor(
        db=db,
        monitor_id=monitor_id,
    )

    if monitor is None:
        raise HTTPException(
            status_code=404,
            detail="Monitor bulunamadı.",
        )

    return monitor


@router.post(
    "",
    response_model=MonitorResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_new_monitor(
    payload: MonitorCreate,
    db: Session = Depends(get_db),
):
    # Sema tracking_plan / referans tutarliligini zaten
    # dogruladi. Burada referansin veritabaninda gercekten
    # bulundugu dogrulanir.
    ensure_regulation_reference_exists(
        db=db,
        reference_regulation_id=(
            payload.reference_regulation_id
        ),
    )

    monitor = create_monitor(
        db=db,
        name=payload.name,
        url=payload.url,
        topic=payload.topic,
        tracking_plan=payload.tracking_plan,
        reference_selection_mode=(
            payload.reference_selection_mode
        ),
        reference_regulation_id=(
            payload.reference_regulation_id
        ),
        scope_type=payload.scope_type,
        schedule_type=payload.schedule_type,
        schedule_time=payload.schedule_time,
        start_date=payload.start_date,
        end_date=payload.end_date,
        is_unlimited=payload.is_unlimited,
        notify_in_app=payload.notify_in_app,
        notify_email=payload.notify_email,
        notification_email=payload.notification_email,
        manual_section_reference=(
            payload.manual_section_reference
        ),
        manual_section_label=payload.manual_section_label,
    )

    return monitor


@router.patch(
    "/{monitor_id}",
    response_model=MonitorResponse,
)
def update_existing_monitor(
    monitor_id: int,
    payload: MonitorUpdate,
    db: Session = Depends(get_db),
):
    """
    Mevcut monitoru kismi olarak gunceller.

    Gonderilmeyen alanlar degismez.
    created_at ve last_checked_at guncellenemez.
    """

    monitor = get_monitor(
        db=db,
        monitor_id=monitor_id,
    )

    if monitor is None:
        raise HTTPException(
            status_code=404,
            detail="Monitor bulunamadı.",
        )

    updates = payload.model_dump(
        exclude_unset=True,
    )

    if not updates:
        return monitor

    # Alanlar arasi kurallar, guncelleme sonrasi olusacak
    # BIRLESIK deger kumesi uzerinden dogrulanir.
    # Boylece partial PATCH'te gonderilmeyen alanlar
    # yuzunden validation bozulmaz.
    def merged(field: str):
        if field in updates:
            return updates[field]

        return getattr(monitor, field)

    # manual_section secimi de BIRLESIK deger kumesi
    # uzerinden dogrulanir. Boylece:
    #   - kapsam manual_section'a cevrilirken secim zorunlu
    #   - mevcut manual monitorde secim bosaltilamaz
    #   - URL degistiginde eski secim sessizce kullanilamaz
    manual_reference = merged("manual_section_reference")
    manual_label = merged("manual_section_label")

    url_changed = (
        "url" in updates
        and updates["url"] != monitor.url
    )

    if url_changed and "manual_section_reference" not in updates:
        # Referans eski sayfaya aitti; yeni URL icin
        # yeniden secim gerekir.
        manual_reference = None
        manual_label = None

    # Referans yonetmelik secimi de BIRLESIK deger kumesi
    # uzerinden dogrulanir. Takip plani degistiginde
    # update_monitor() secimi temizleyecegi icin dogrulama
    # da ayni temizlenmis deger uzerinden yapilmalidir:
    #
    #   regulation -> website : referans dusuyor, gecerli
    #   website -> regulation : referans bos kaliyor,
    #                           "referans secilmelidir"
    reference_regulation_id = merged(
        "reference_regulation_id"
    )

    if (
        should_reset_regulation_reference(
            monitor=monitor,
            updates=updates,
        )
        and "reference_regulation_id" not in updates
    ):
        # Takip plani veya secim modu degisti; eski referans
        # secimi bu guncellemede gecerli sayilmaz.
        reference_regulation_id = None

    try:
        validate_monitor_settings(
            notify_email=merged("notify_email"),
            notification_email=merged("notification_email"),
            is_unlimited=merged("is_unlimited"),
            start_date=merged("start_date"),
            end_date=merged("end_date"),
            scope_type=merged("scope_type"),
            manual_section_reference=manual_reference,
            manual_section_label=manual_label,
            tracking_plan=merged("tracking_plan"),
            reference_regulation_id=(
                reference_regulation_id
            ),
            reference_selection_mode=merged(
                "reference_selection_mode"
            ),
        )

    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=str(exc),
        ) from exc

    # Yalnizca bu istekte GONDERILEN referans dogrulanir.
    # Kayitli bir referansin arada silinmis olmasi ilgisiz
    # bir alani guncelleyen PATCH'i bloklamamali.
    if "reference_regulation_id" in updates:
        ensure_regulation_reference_exists(
            db=db,
            reference_regulation_id=(
                updates["reference_regulation_id"]
            ),
        )

    return update_monitor(
        db=db,
        monitor=monitor,
        updates=updates,
    )


@router.post(
    "/{monitor_id}/run",
    response_model=MonitorRunResponse,
)
async def run_monitor_now(
    monitor_id: int,
    db: Session = Depends(get_db),
):
    """
    Bir monitörü manuel olarak çalıştırır.

    Mevcut run_database_monitor() pipeline'ı yeniden kullanılır.
    Gemini sonucu bildirime değer ise uygulama içi notification
    oluşturulur.

    TAKİP PLANI DAVRANIŞI

    regulation_monitor monitörleri aynı pipeline'ı
    çalıştırır (fetch, snapshot, değişiklik tespiti, konu
    analizi, bildirim) ve AI analizi değişikliği konuyla
    ilgili bulduğunda ek olarak MEVZUAT KARŞILAŞTIRMASI
    yapılır.

    Yanıttaki regulation_comparison_status gerçek durumu
    taşır: completed / no_match / failed, ya da
    karşılaştırmanın neden hiç çalışmadığını söyleyen bir
    skipped_* değeri. Var olmayan bir karşılaştırma sonucu
    asla üretilmez.
    """

    monitor = get_monitor(
        db=db,
        monitor_id=monitor_id,
    )

    if monitor is None:
        raise HTTPException(
            status_code=404,
            detail="Monitor bulunamadı.",
        )

    if not monitor.is_active:
        raise HTTPException(
            status_code=400,
            detail="Monitor pasif durumda. Çalıştırılamaz.",
        )

    # Scheduler ile aynı anda çalışmayı engelle.
    if not acquire_monitor_run(monitor.id):
        raise HTTPException(
            status_code=409,
            detail="Bu monitör şu anda çalışıyor.",
        )

    try:
        outcome = await execute_monitor_run(
            db=db,
            monitor=monitor,
        )

    except WebsiteFetchError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Website getirilemedi: {exc}",
        ) from exc

    except ManualSectionResolutionError as exc:
        # Bolumu kullanici secmisti. Sistem kendi basina
        # baska bir bolum SECMEZ ve tum sayfaya donmez.
        raise HTTPException(
            status_code=422,
            detail=str(exc),
        ) from exc

    except AutoSectionSelectionError as exc:
        # Kullanici auto_section sectigi icin sessizce tum
        # sayfaya donulmez. Snapshot yazilmaz, durum
        # kullaniciya acikca bildirilir.
        raise HTTPException(
            status_code=422,
            detail=(
                "İzlenecek bölüm otomatik belirlenemedi: "
                f"{exc}"
            ),
        ) from exc

    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc

    finally:
        release_monitor_run(monitor.id)

    result = outcome.result
    notification = outcome.notification
    email_notification = outcome.email_notification

    ai_analysis = result.ai_analysis_record

    # -----------------------------------------------------
    # KARSILASTIRMA SONUCU
    #
    # website_monitor icin outcome.regulation_comparison
    # status=None tasir; yanitta da None gorunur.
    # -----------------------------------------------------

    comparison_run = outcome.regulation_comparison

    comparison_status = (
        comparison_run.status
        if comparison_run is not None
        else None
    )

    comparison_record = (
        comparison_run.comparison
        if comparison_run is not None
        else None
    )

    comparison_id = (
        comparison_record.id
        if comparison_record is not None
        else None
    )

    comparison_item_count = (
        len(comparison_record.items)
        if comparison_record is not None
        else None
    )

    # -----------------------------------------------------
    # REFERANS SECIMI
    #
    # Karsilastirma satiri varsa TARIHSEL GERCEK oradadir:
    # o calistirmada gercekten hangi referans kullanildi.
    #
    # Karsilastirma olusmadiysa (otomatik eslestirme
    # basarisiz) bilgi calistirma sonucundan okunur;
    # boylece kullanici NEDEN calismadigini gorur.
    # -----------------------------------------------------

    is_regulation = is_regulation_monitor(monitor)

    matched_reference = (
        comparison_record.reference_regulation
        if comparison_record is not None
        else None
    )

    reference_match_status = (
        comparison_record.reference_match_status
        if comparison_record is not None
        else (
            comparison_run.match_status
            if comparison_run is not None
            else None
        )
    )

    reference_freshness_status = (
        comparison_record.reference_freshness_status
        if comparison_record is not None
        else (
            comparison_run.freshness_status
            if comparison_run is not None
            else None
        )
    )

    # -----------------------------------------------------
    # OTOMATIK REFERANS KESFI
    #
    # Kalici gercek karsilastirma satirindadir; satir
    # olusmadiysa calistirma sonucundan okunur.
    # -----------------------------------------------------

    reference_discovery_status = (
        comparison_record.reference_discovery_status
        if comparison_record is not None
        else (
            comparison_run.discovery_status
            if comparison_run is not None
            else None
        )
    )

    return MonitorRunResponse(
        monitor_id=monitor.id,
        monitor_name=monitor.name,

        tracking_plan=monitor.tracking_plan,

        # MEVZUAT KARSILASTIRMASI
        #
        # website_monitor  -> None
        # regulation_monitor -> gercek durum:
        #   completed | no_match | failed
        #   veya karsilastirmanin NEDEN calismadigini
        #   soyleyen skipped_* degeri.
        #
        # Placeholder deger uretilmez.
        regulation_comparison_status=comparison_status,

        regulation_comparison_id=comparison_id,

        regulation_changes_count=comparison_item_count,

        reference_selection_mode=(
            resolve_reference_selection_mode(
                monitor.reference_selection_mode
            )
            if is_regulation
            else None
        ),

        reference_match_status=reference_match_status,

        matched_reference_id=(
            comparison_record.reference_regulation_id
            if comparison_record is not None
            else None
        ),
        matched_reference_title=(
            matched_reference.title
            if matched_reference is not None
            else None
        ),

        reference_match_method=(
            comparison_record.reference_match_method
            if comparison_record is not None
            else None
        ),
        reference_match_confidence=(
            comparison_record.reference_match_confidence
            if comparison_record is not None
            else None
        ),

        reference_freshness_status=reference_freshness_status,

        # KESIF
        #
        # Kesfin GERCEKTEN ne yaptigi. Uydurma bir "hazir"
        # degeri uretilmez; kesif calismadiysa None kalir.
        reference_discovery_status=reference_discovery_status,

        discovered_reference_id=(
            comparison_run.discovered_reference_id
            if comparison_run is not None
            else None
        ),
        discovered_reference_title=(
            comparison_run.discovered_reference_title
            if comparison_run is not None
            else None
        ),

        scope_type=monitor.scope_type,

        auto_section_label=monitor.auto_section_label,

        manual_section_label=monitor.manual_section_label,

        first_run=result.first_run,
        changed=result.changed,

        similarity_ratio=(
            result.change_result.similarity_ratio
            if result.change_result is not None
            else None
        ),

        topic_relevant=(
            result.topic_analysis.relevant
            if result.topic_analysis is not None
            else None
        ),

        ai_relevant=(
            ai_analysis.relevant
            if ai_analysis is not None
            else None
        ),

        ai_importance=(
            ai_analysis.importance
            if ai_analysis is not None
            else None
        ),

        ai_category=(
            ai_analysis.category
            if ai_analysis is not None
            else None
        ),

        ai_summary=(
            ai_analysis.summary
            if ai_analysis is not None
            else None
        ),

        notification_created=(
            notification is not None
        ),

        email_status=(
            email_notification.status
            if email_notification is not None
            else None
        ),

        # error_message notification_service tarafinda
        # zaten temizlenmis olarak yazilir.
        email_error=(
            email_notification.error_message
            if email_notification is not None
            else None
        ),

        ai_error=result.ai_error,

        last_checked_at=monitor.last_checked_at,
    )
