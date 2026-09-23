import hashlib

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.snapshot import Snapshot


def calculate_content_hash(content: str) -> str:
    return hashlib.sha256(
        content.encode("utf-8")
    ).hexdigest()


def create_snapshot(
    db: Session,
    monitor_id: int,
    title: str,
    content: str,
) -> Snapshot:

    snapshot = Snapshot(
        monitor_id=monitor_id,
        title=title,
        content=content,
        content_hash=calculate_content_hash(content),
    )

    db.add(snapshot)
    db.commit()
    db.refresh(snapshot)

    return snapshot


def get_snapshot(
    db: Session,
    snapshot_id: int,
) -> Snapshot | None:

    return db.get(
        Snapshot,
        snapshot_id,
    )


def get_latest_snapshot(
    db: Session,
    monitor_id: int,
) -> Snapshot | None:

    statement = (
        select(Snapshot)
        .where(
            Snapshot.monitor_id == monitor_id
        )
        .order_by(
            Snapshot.created_at.desc(),
            Snapshot.id.desc(),
        )
        .limit(1)
    )

    return db.scalar(statement)