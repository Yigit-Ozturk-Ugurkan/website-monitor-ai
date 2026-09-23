import asyncio
import logging

from sqlalchemy.orm import Session

from app.models.ai_analysis import AIAnalysis
from app.models.change import Change
from app.models.monitor import Monitor
from app.models.notification import Notification
from app.models.regulation_comparison import (
    RegulationComparison,
)

from app.repositories.notification_repository import (
    STATUS_PENDING,
    STATUS_SENT,
    create_notification,
    get_notification_by_analysis_and_type,
    mark_notification_failed,
    mark_notification_sent,
)
from app.services.email_content import build_change_email
from app.services.email_service import (
    EmailServiceError,
    is_email_configured,
    sanitize_error_message,
    send_email,
)


logger = logging.getLogger(__name__)


NOTIFICATION_TYPE_IN_APP = "in_app"
NOTIFICATION_TYPE_EMAIL = "email"


def build_notification_message(
    ai_analysis: AIAnalysis,
    regulation_comparison: RegulationComparison | None = None,
) -> str:
    """
    Bildirim govdesini uretir.

    Varsayilan davranis DEGISMEDI: govde AIAnalysis ozetidir.

    Yalnizca DOGRULANMIS madde tasiyan bir mevzuat
    karsilastirmasi varsa govdenin sonuna kisa bir satir
    eklenir. Karsilastirma yoksa cikti birebir eskisiyle
    aynidir.
    """

    message = ai_analysis.summary

    if regulation_comparison is None:
        return message

    item_count = len(regulation_comparison.items)

    if item_count <= 0:
        return message

    parts = [message, ""]

    summary = (
        regulation_comparison.overall_summary or ""
    ).strip()

    if summary:
        parts.append(f"Mevzuat karşılaştırması: {summary}")
    else:
        parts.append("Mevzuat karşılaştırması yapıldı.")

    parts.append(
        f"Etkilenen madde sayısı: {item_count}"
    )

    return "\n".join(parts)


def should_notify(ai_analysis: AIAnalysis) -> bool:
    """
    Bu analiz kullaniciya bildirilmeye deger mi?

    Tek kural: Gemini hem KONUYLA ILGILI dedi hem de
    bildirim onerdi. In-app ve e-mail ayni kurali kullanir.
    """

    return bool(
        ai_analysis.relevant
        and ai_analysis.notification_recommended
    )


def create_in_app_notification(
    db: Session,
    monitor: Monitor,
    change: Change,
    ai_analysis: AIAnalysis,
    regulation_comparison: RegulationComparison | None = None,
) -> Notification | None:
    """
    Gemini sonucu kullanıcıya bildirmeye değer ise
    uygulama içi notification oluşturur.

    E-mail göndermez.
    """

    if not should_notify(ai_analysis):
        return None

    # Aynı analiz için daha önce notification
    # oluşturulduysa tekrar oluşturma.
    existing_notification = (
        get_notification_by_analysis_and_type(
            db=db,
            ai_analysis_id=ai_analysis.id,
            notification_type=NOTIFICATION_TYPE_IN_APP,
        )
    )

    if existing_notification is not None:
        return existing_notification

    title = (
        f"{monitor.name} - Yeni Değişiklik"
    )

    notification = create_notification(
        db=db,
        monitor_id=monitor.id,
        change_id=change.id,
        ai_analysis_id=ai_analysis.id,
        notification_type=NOTIFICATION_TYPE_IN_APP,
        title=title,
        message=build_notification_message(
            ai_analysis=ai_analysis,
            regulation_comparison=regulation_comparison,
        ),
        importance=ai_analysis.importance,
        category=ai_analysis.category,
        recipient=None,
    )

    return notification

# ---------------------------------------------------------
# E-MAIL BILDIRIMI
#
# Kurallar:
#
#   - yalnizca relevant + notification_recommended
#   - yalnizca monitor.notify_email acikken
#   - ayni analiz icin YALNIZ BIR e-mail
#   - gonderim hatasi pipeline'i BOZMAZ
#
# Bu fonksiyon HICBIR ZAMAN hata firlatmaz. Basarisizlik
# notification kaydinda status="failed" olarak gorunur.
# ---------------------------------------------------------

async def send_email_notification(
    db: Session,
    monitor: Monitor,
    change: Change,
    ai_analysis: AIAnalysis,
    regulation_comparison: RegulationComparison | None = None,
) -> Notification | None:
    """
    Gemini sonucu bildirmeye değer ise e-mail gönderir ve
    gönderim durumunu veritabanına yazar.

    Gönderilmeyecek durumlarda None döner:

      - analiz ilgisiz veya bildirim önerilmiyor
      - monitörde e-mail bildirimi kapalı
      - alıcı adresi yok
      - SMTP yapılandırılmamış
      - bu analiz için e-mail zaten oluşturulmuş

    Yeni bir Gemini çağrısı YAPILMAZ; içerik mevcut
    AIAnalysis kaydından üretilir.
    """

    if not should_notify(ai_analysis):
        return None

    if not monitor.notify_email:
        return None

    recipient = (monitor.notification_email or "").strip()

    if not recipient:
        # Sema dogrulamasi bunu engelliyor; burasi eski
        # kayitlar icin son savunma.
        logger.warning(
            "E-mail bildirimi acik ama alici adresi yok. "
            "monitor_id=%s",
            monitor.id,
        )
        return None

    # -----------------------------------------------------
    # DUPLICATE KORUMASI
    #
    # (ai_analysis_id, notification_type) benzersizdir.
    # Manuel run ile scheduler ayni analizi islerse
    # ikinci mail cikmaz.
    # -----------------------------------------------------

    existing_notification = (
        get_notification_by_analysis_and_type(
            db=db,
            ai_analysis_id=ai_analysis.id,
            notification_type=NOTIFICATION_TYPE_EMAIL,
        )
    )

    if existing_notification is not None:
        return existing_notification

    if not is_email_configured():
        # Deployment'ta SMTP yok. Sessizce basarili sayma;
        # ayrica sahte bir "gonderildi" kaydi da olusturma.
        logger.warning(
            "SMTP yapilandirilmamis, e-mail gonderilmedi. "
            "monitor_id=%s",
            monitor.id,
        )
        return None

    title = (
        f"{monitor.name} - Yeni Değişiklik"
    )

    # Once kayit olusturulur; boylece gonderim sirasinda
    # process cokse bile denemenin izi kalir.
    notification = create_notification(
        db=db,
        monitor_id=monitor.id,
        change_id=change.id,
        ai_analysis_id=ai_analysis.id,
        notification_type=NOTIFICATION_TYPE_EMAIL,
        title=title,
        message=build_notification_message(
            ai_analysis=ai_analysis,
            regulation_comparison=regulation_comparison,
        ),
        importance=ai_analysis.importance,
        category=ai_analysis.category,
        recipient=recipient,
        status=STATUS_PENDING,
    )

    content = build_change_email(
        monitor=monitor,
        ai_analysis=ai_analysis,
        changed_at=change.detected_at,
        notification_id=notification.id,
        regulation_comparison=regulation_comparison,
    )

    try:
        # smtplib bloklar. Event loop'u ve scheduler'i
        # bekletmemek icin thread'e devredilir.
        await asyncio.to_thread(
            send_email,
            recipient,
            content.subject,
            content.text_body,
            content.html_body,
        )

    except EmailServiceError as exc:
        # Beklenen tasima hatalari: DNS, timeout, auth,
        # TLS, alici reddi, SMTP sunucu hatasi.
        return mark_notification_failed(
            db=db,
            notification=notification,
            error_message=sanitize_error_message(str(exc)),
        )

    except Exception as exc:
        # Beklenmeyen bir hata da monitor run'ini
        # dusurmemeli.
        logger.exception(
            "E-mail gonderiminde beklenmeyen hata. "
            "monitor_id=%s",
            monitor.id,
        )

        return mark_notification_failed(
            db=db,
            notification=notification,
            error_message=sanitize_error_message(
                f"{type(exc).__name__}: {exc}"
            ),
        )

    return mark_notification_sent(
        db=db,
        notification=notification,
    )
