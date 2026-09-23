from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.monitor import Monitor
from app.models.notification import Notification


# ---------------------------------------------------------
# BILDIRIM DURUMLARI
#
# created : uygulama ici bildirim (gonderim gerektirmez)
# pending : e-mail olusturuldu, henuz gonderilmedi
# sent    : e-mail basariyla gonderildi
# failed  : e-mail gonderilemedi
# ---------------------------------------------------------

STATUS_CREATED = "created"
STATUS_PENDING = "pending"
STATUS_SENT = "sent"
STATUS_FAILED = "failed"


def create_notification(
    db: Session,
    monitor_id: int,
    change_id: int,
    ai_analysis_id: int,
    notification_type: str,
    title: str,
    message: str,
    importance: str,
    category: str,
    recipient: str | None = None,
    status: str = STATUS_CREATED,
) -> Notification:

    notification = Notification(
        monitor_id=monitor_id,
        change_id=change_id,
        ai_analysis_id=ai_analysis_id,
        notification_type=notification_type,
        recipient=recipient,
        title=title,
        message=message,
        importance=importance,
        category=category,
        status=status,
        is_read=False,
    )

    db.add(notification)
    db.commit()
    db.refresh(notification)

    return notification


def mark_notification_sent(
    db: Session,
    notification: Notification,
) -> Notification:
    """
    Gonderim basarili: durum guncellenir, onceki hata temizlenir.
    """

    notification.status = STATUS_SENT
    notification.sent_at = datetime.utcnow()
    notification.error_message = None

    db.commit()
    db.refresh(notification)

    return notification


def mark_notification_failed(
    db: Session,
    notification: Notification,
    error_message: str,
) -> Notification:
    """
    Gonderim basarisiz.

    error_message cagiran taraftan TEMIZLENMIS olarak gelir;
    SMTP kullanici adi/parolasi buraya ulasmaz.
    """

    notification.status = STATUS_FAILED
    notification.sent_at = None
    notification.error_message = error_message

    db.commit()
    db.refresh(notification)

    return notification


def get_notification(
    db: Session,
    notification_id: int,
) -> Notification | None:

    return db.get(
        Notification,
        notification_id,
    )


def get_notification_by_analysis_and_type(
    db: Session,
    ai_analysis_id: int,
    notification_type: str,
) -> Notification | None:

    statement = (
        select(Notification)
        .where(
            Notification.ai_analysis_id == ai_analysis_id,
            Notification.notification_type == notification_type,
        )
        .limit(1)
    )

    return db.scalar(statement)


def get_notifications(
    db: Session,
    notification_type: str | None = None,
    limit: int | None = None,
) -> list[tuple[Notification, str]]:
    """
    Tum bildirimleri en yeniden en eskiye dogru dondurur.

    Monitor adi tek sorguda join ile alinir, N+1 olusmaz.
    """

    statement = (
        select(Notification, Monitor.name)
        .join(
            Monitor,
            Monitor.id == Notification.monitor_id,
        )
        .order_by(
            Notification.created_at.desc(),
            Notification.id.desc(),
        )
    )

    if notification_type is not None:
        statement = statement.where(
            Notification.notification_type == notification_type
        )

    if limit is not None:
        statement = statement.limit(limit)

    return [
        (row[0], row[1])
        for row in db.execute(statement).all()
    ]


def get_notifications_for_monitor(
    db: Session,
    monitor_id: int,
) -> list[Notification]:

    statement = (
        select(Notification)
        .where(
            Notification.monitor_id == monitor_id
        )
        .order_by(
            Notification.created_at.desc(),
            Notification.id.desc(),
        )
    )

    return list(
        db.scalars(statement).all()
    )


def count_unread_notifications(
    db: Session,
    notification_type: str | None = None,
) -> int:
    """
    Okunmamis bildirim sayisini VERITABANINDA hesaplar.

    Kayitlar Python tarafina cekilmez; COUNT sorgusu kullanilir.
    """

    statement = (
        select(func.count())
        .select_from(Notification)
        .where(
            Notification.is_read.is_(False)
        )
    )

    if notification_type is not None:
        statement = statement.where(
            Notification.notification_type == notification_type
        )

    return db.scalar(statement) or 0


def mark_notification_as_read(
    db: Session,
    notification: Notification,
) -> Notification:

    notification.is_read = True

    db.commit()
    db.refresh(notification)

    return notification