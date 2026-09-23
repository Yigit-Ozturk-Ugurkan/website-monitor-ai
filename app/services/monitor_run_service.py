import logging

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models.monitor import Monitor
from app.models.notification import Notification

from app.repositories.monitor_repository import (
    update_last_run_status,
)

from app.services.db_monitor_service import (
    DatabaseMonitorResult,
    run_database_monitor,
)
from app.services.notification_service import (
    create_in_app_notification,
    send_email_notification,
)
from app.services.monitor_run_status import (
    describe,
    resolve_failure_status,
    resolve_run_status,
)
from app.services.regulation_monitor_service import (
    COMPARISON_COMPLETED,
    is_regulation_monitor,
    RegulationComparisonRunResult,
    run_regulation_comparison,
)


logger = logging.getLogger(__name__)


# ---------------------------------------------------------
# AYNI MONITOR İÇİN EŞ ZAMANLI ÇALIŞMA KORUMASI
#
# FastAPI ve scheduler aynı process ve aynı event loop
# üzerinde çalıştığı için basit bir in-process set yeterli.
# Kontrol ile ekleme arasında await olmadığından işlem
# atomiktir.
# ---------------------------------------------------------

_running_monitor_ids: set[int] = set()


def is_monitor_running(monitor_id: int) -> bool:
    return monitor_id in _running_monitor_ids


def acquire_monitor_run(monitor_id: int) -> bool:
    """
    Monitor için çalışma hakkı alır.

    Monitor zaten çalışıyorsa False döner.
    """

    if monitor_id in _running_monitor_ids:
        return False

    _running_monitor_ids.add(monitor_id)

    return True


def release_monitor_run(monitor_id: int) -> None:
    _running_monitor_ids.discard(monitor_id)


@dataclass
class MonitorRunOutcome:
    result: DatabaseMonitorResult

    # Uygulama ici bildirim.
    notification: Notification | None

    # E-mail bildirimi. Gonderilmediyse veya gerekmiyorsa None.
    # Gonderim basarisiz olduysa status="failed" ile doner.
    email_notification: Notification | None = None

    # Mevzuat karsilastirmasi.
    #
    # website_monitor icin status None'dir; regulation
    # monitor icin ya gercek bir sonuc ya da karsilastirmanin
    # NEDEN calismadigini soyleyen bir "skipped_*" degeri
    # tasir. Placeholder deger URETILMEZ.
    regulation_comparison: (
        RegulationComparisonRunResult | None
    ) = None


def _record_status(
    db: Session,
    monitor: Monitor,
    status: str,
) -> None:
    """
    Durumu ve kullanici mesajini monitore yazar.

    Durum yazimi calistirmayi BOZMAMALIDIR: burada olusan
    bir hata snapshot, change, AIAnalysis, karsilastirma
    ve bildirim kayitlarini gecersiz kilmaz.
    """

    info = describe(status)

    if info is None:
        # Taninmayan durum icin uydurma mesaj yazilmaz.
        return

    try:
        update_last_run_status(
            db=db,
            monitor=monitor,
            status=info.status,
            message=info.message,
        )

    except Exception:
        logger.exception(
            "Son calistirma durumu yazilamadi. id=%s",
            monitor.id,
        )


async def execute_monitor_run(
    db: Session,
    monitor: Monitor,
) -> MonitorRunOutcome:
    """
    Monitor pipeline'ını çalıştırır ve gerekiyorsa
    uygulama içi bildirim oluşturur, e-mail gönderir.

    Manuel /run endpoint'i ile scheduler bu fonksiyonu
    ortak kullanır. Pipeline burada yeniden yazılmaz.
    """

    # -----------------------------------------------------
    # PIPELINE
    #
    # Hata durumunda da kartta EN SON denemenin sonucu
    # gorunmelidir. Bu yuzden istisna burada YAKALANIR,
    # durum yazilir ve AYNEN yeniden firlatilir.
    #
    # Cagiranlarin davranisi DEGISMEZ:
    #   - /run endpoint'i hala 502 / 422 doner
    #   - scheduler hala retry eder (last_checked_at'e
    #     dokunulmaz)
    # -----------------------------------------------------

    try:
        result = await run_database_monitor(
            db=db,
            monitor=monitor,
            # DIFF GURULTU AZALTMA KARARI
            #
            # Pipeline takip planini bilmez; karar burada
            # verilir.
            #
            # Mevzuat izlemesinde bir maddenin baska yere
            # TASINMASI hukuki olarak anlamli olabilir ve
            # karsilastirmanin kaynak metni change kaydindan
            # okunur. Bu yuzden orada HAM diff korunur.
            reduce_noise=not is_regulation_monitor(monitor),
        )

    except Exception as exc:
        _record_status(
            db=db,
            monitor=monitor,
            status=resolve_failure_status(exc),
        )

        raise

    # -----------------------------------------------------
    # MEVZUAT KARSILASTIRMASI
    #
    # Bildirimden ONCE calisir; boylece bildirim govdesi
    # karsilastirma ozetiyle zenginlestirilebilir.
    #
    # Yalnizca regulation_monitor + gercek degisiklik +
    # relevant analiz durumunda gercek bir karsilastirma
    # yapilir. Diger durumlarda hicbir Gemini cagrisi
    # olusmaz ve sonuc nedeni bildirir.
    #
    # Bu cagri HATA FIRLATMAZ; basarisizlik status="failed"
    # olarak doner ve snapshot/change/AIAnalysis korunur.
    # -----------------------------------------------------

    regulation_comparison = await run_regulation_comparison(
        db=db,
        monitor=monitor,
        result=result,
    )

    # Bildirim servisleri relevant / notification_recommended
    # kontrolunu ve duplicate korumasini kendileri yapiyor.

    notification = None
    email_notification = None

    has_analysis = (
        result.change_record is not None
        and result.ai_analysis_record is not None
    )

    # Bildirim govdesine YALNIZCA tamamlanmis ve dogrulanmis
    # item tasiyan karsilastirma eklenir.
    comparison_for_notification = (
        regulation_comparison.comparison
        if (
            regulation_comparison is not None
            and regulation_comparison.status
            == COMPARISON_COMPLETED
            and regulation_comparison.item_count > 0
        )
        else None
    )

    if monitor.notify_in_app and has_analysis:
        notification = create_in_app_notification(
            db=db,
            monitor=monitor,
            change=result.change_record,
            ai_analysis=result.ai_analysis_record,
            regulation_comparison=comparison_for_notification,
        )

    # -----------------------------------------------------
    # E-MAIL
    #
    # In-app'ten BAGIMSIZDIR. send_email_notification hata
    # firlatmaz; gonderim basarisiz olsa bile snapshot,
    # change, AIAnalysis ve in-app bildirim korunur ve
    # monitor run tamamlanmis sayilir.
    # -----------------------------------------------------

    if monitor.notify_email and has_analysis:
        email_notification = await send_email_notification(
            db=db,
            monitor=monitor,
            change=result.change_record,
            ai_analysis=result.ai_analysis_record,
            regulation_comparison=comparison_for_notification,
        )

    # -----------------------------------------------------
    # SON CALISTIRMA DURUMU
    #
    # Bildirim ve e-postadan SONRA yazilir: o ana kadar
    # calistirmanin butun sonuclari bellidir.
    #
    # Bu satir monitor kartindaki "Sonuç" alanini besler.
    # Yeni bir Notification veya e-posta URETMEZ.
    # -----------------------------------------------------

    _record_status(
        db=db,
        monitor=monitor,
        status=resolve_run_status(
            result=result,
            regulation_comparison=regulation_comparison,
        ),
    )

    return MonitorRunOutcome(
        result=result,
        notification=notification,
        email_notification=email_notification,
        regulation_comparison=regulation_comparison,
    )
