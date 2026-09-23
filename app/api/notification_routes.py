from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
)
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.repositories.ai_analysis_repository import (
    get_ai_analysis_by_change,
)
from app.repositories.change_repository import (
    get_change,
)
from app.repositories.monitor_repository import (
    get_monitor,
)
from app.repositories.regulation_comparison_repository import (
    get_comparison_by_change,
)
from app.repositories.notification_repository import (
    count_unread_notifications,
    get_notification,
    get_notifications,
    mark_notification_as_read,
)
from app.repositories.snapshot_repository import (
    get_snapshot,
)
from app.schemas.notification import (
    ContextBlockResponse,
    ContextLineResponse,
    NotificationDetailResponse,
    NotificationReadResponse,
    NotificationResponse,
    UnreadCountResponse,
)
from app.services.change_context_service import (
    ContextBlock,
    build_context_blocks,
)


router = APIRouter(
    prefix="/api/notifications",
    tags=["Notifications"],
)


@router.get(
    "",
    response_model=list[NotificationResponse],
)
def list_notifications(
    notification_type: str | None = Query(
        default=None,
        description=(
            "Bildirim turune gore filtre. Ornek: in_app"
        ),
    ),
    limit: int = Query(
        default=100,
        ge=1,
        le=500,
        description="Dondurulecek en fazla kayit sayisi.",
    ),
    db: Session = Depends(get_db),
):
    """
    Uygulama ici bildirimleri en yeniden en eskiye dogru dondurur.

    Bildirimler mevcut create_in_app_notification() akisi
    tarafindan uretilir. Bu endpoint yalnizca okuma yapar.
    """

    rows = get_notifications(
        db=db,
        notification_type=notification_type,
        limit=limit,
    )

    return [
        NotificationResponse(
            id=notification.id,
            monitor_id=notification.monitor_id,
            monitor_name=monitor_name,
            change_id=notification.change_id,
            ai_analysis_id=notification.ai_analysis_id,
            notification_type=notification.notification_type,
            title=notification.title,
            message=notification.message,
            importance=notification.importance,
            category=notification.category,
            status=notification.status,
            is_read=notification.is_read,
            created_at=notification.created_at,
        )
        for notification, monitor_name in rows
    ]


@router.get(
    "/unread-count",
    response_model=UnreadCountResponse,
)
def unread_notification_count(
    notification_type: str | None = Query(
        default=None,
        description=(
            "Bildirim turune gore filtre. Ornek: in_app"
        ),
    ),
    db: Session = Depends(get_db),
):
    """
    Okunmamis bildirim sayisini dondurur.

    Sayim veritabaninda COUNT ile yapilir.
    """

    return UnreadCountResponse(
        count=count_unread_notifications(
            db=db,
            notification_type=notification_type,
        )
    )


@router.patch(
    "/{notification_id}/read",
    response_model=NotificationReadResponse,
)
def mark_read(
    notification_id: int,
    db: Session = Depends(get_db),
):
    """
    Bildirimi okundu olarak isaretler.

    Idempotenttir: zaten okunmus bir bildirim icin de 200 doner
    ve kayitta hicbir sey degismez. is_read disinda hicbir alan
    guncellenmez.
    """

    notification = get_notification(
        db=db,
        notification_id=notification_id,
    )

    if notification is None:
        raise HTTPException(
            status_code=404,
            detail="Bildirim bulunamadı.",
        )

    if not notification.is_read:
        mark_notification_as_read(
            db=db,
            notification=notification,
        )

    return NotificationReadResponse(
        id=notification.id,
        is_read=notification.is_read,
    )


def _serialize_blocks(
    blocks: list[ContextBlock],
) -> list[ContextBlockResponse]:

    return [
        ContextBlockResponse(
            start_line=block.start_line,
            end_line=block.end_line,
            lines=[
                ContextLineResponse(
                    line_number=line.line_number,
                    text=line.text,
                    changed=line.changed,
                    evidence=line.evidence,
                )
                for line in block.lines
            ],
        )
        for block in blocks
    ]


@router.get(
    "/{notification_id}/detail",
    response_model=NotificationDetailResponse,
)
def notification_detail(
    notification_id: int,
    db: Session = Depends(get_db),
):
    """
    Degisiklik detayi ekrani icin tek istekte gereken veriyi dondurur.

    Bildirim, monitor, change, AI analizi ve iki snapshot mevcut
    repository fonksiyonlariyla okunur. Iliskili kayitlardan biri
    eksikse istek hata vermez; ilgili alanlar null doner.
    """

    notification = get_notification(
        db=db,
        notification_id=notification_id,
    )

    if notification is None:
        raise HTTPException(
            status_code=404,
            detail="Bildirim bulunamadı.",
        )

    monitor = get_monitor(
        db=db,
        monitor_id=notification.monitor_id,
    )

    if monitor is None:
        raise HTTPException(
            status_code=404,
            detail="Bildirime ait monitör bulunamadı.",
        )

    change = get_change(
        db=db,
        change_id=notification.change_id,
    )

    ai_analysis = None

    if change is not None:
        ai_analysis = get_ai_analysis_by_change(
            db=db,
            change_id=change.id,
        )

    old_snapshot = None
    new_snapshot = None

    if change is not None:
        old_snapshot = get_snapshot(
            db=db,
            snapshot_id=change.old_snapshot_id,
        )

        new_snapshot = get_snapshot(
            db=db,
            snapshot_id=change.new_snapshot_id,
        )

    # Gemini kararinin kaynak kaniti.
    # Eski AIAnalysis kayitlarinda null olabilir.

    evidence_text = (
        ai_analysis.evidence_text
        if ai_analysis is not None
        else None
    )

    evidence_type = (
        ai_analysis.evidence_type
        if ai_analysis is not None
        else None
    )

    # Eklenenler yeni snapshot uzerinden,
    # silinenler eski snapshot uzerinden gosterilir.
    # Evidence yalnizca ait oldugu tarafta isaretlenir.

    added_blocks = []
    removed_blocks = []

    if change is not None and new_snapshot is not None:
        added_blocks = build_context_blocks(
            snapshot_content=new_snapshot.content,
            changed_content=change.added_content,
            evidence_text=(
                evidence_text
                if evidence_type == "added"
                else None
            ),
        )

    if change is not None and old_snapshot is not None:
        removed_blocks = build_context_blocks(
            snapshot_content=old_snapshot.content,
            changed_content=change.removed_content,
            evidence_text=(
                evidence_text
                if evidence_type == "removed"
                else None
            ),
        )

    # -----------------------------------------------------
    # MEVZUAT KARSILASTIRMASI
    #
    # Bu degisiklik icin bir karsilastirma yapildiysa
    # detay yanitina NESTED olarak eklenir; istemcinin
    # ikinci bir istek yapmasi gerekmez.
    #
    # website_monitor bildirimlerinde daima None kalir.
    # -----------------------------------------------------

    regulation_comparison = None

    if change is not None:
        regulation_comparison = get_comparison_by_change(
            db=db,
            change_id=change.id,
        )

    return NotificationDetailResponse(
        notification_id=notification.id,

        regulation_comparison=regulation_comparison,

        monitor_id=monitor.id,
        monitor_name=monitor.name,
        url=monitor.url,

        change_id=notification.change_id,
        ai_analysis_id=notification.ai_analysis_id,

        title=notification.title,
        message=notification.message,
        importance=notification.importance,
        category=notification.category,
        created_at=notification.created_at,
        is_read=notification.is_read,

        ai_summary=(
            ai_analysis.summary
            if ai_analysis is not None
            else None
        ),

        evidence_text=evidence_text,
        evidence_type=evidence_type,

        added_content=(
            change.added_content
            if change is not None
            else None
        ),
        removed_content=(
            change.removed_content
            if change is not None
            else None
        ),
        relevance_score=(
            change.relevance_score
            if change is not None
            else None
        ),
        detected_at=(
            change.detected_at
            if change is not None
            else None
        ),

        old_snapshot_id=(
            old_snapshot.id
            if old_snapshot is not None
            else None
        ),
        new_snapshot_id=(
            new_snapshot.id
            if new_snapshot is not None
            else None
        ),
        old_snapshot_content=(
            old_snapshot.content
            if old_snapshot is not None
            else None
        ),
        new_snapshot_content=(
            new_snapshot.content
            if new_snapshot is not None
            else None
        ),
        snapshot_title=(
            new_snapshot.title
            if new_snapshot is not None
            else None
        ),

        added_blocks=_serialize_blocks(added_blocks),
        removed_blocks=_serialize_blocks(removed_blocks),
    )
