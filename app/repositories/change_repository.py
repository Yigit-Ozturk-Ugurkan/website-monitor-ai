from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.change import Change


def create_change(
    db: Session,
    monitor_id: int,
    old_snapshot_id: int,
    new_snapshot_id: int,
    relevant: bool,
    relevance_score: float,
    added_lines: list[str],
    removed_lines: list[str],
) -> Change:

    added_content = (
        "\n".join(added_lines)
        if added_lines
        else None
    )

    removed_content = (
        "\n".join(removed_lines)
        if removed_lines
        else None
    )

    change = Change(
        monitor_id=monitor_id,
        old_snapshot_id=old_snapshot_id,
        new_snapshot_id=new_snapshot_id,
        relevant=relevant,
        relevance_score=relevance_score,
        added_content=added_content,
        removed_content=removed_content,
    )

    db.add(change)
    db.commit()
    db.refresh(change)

    return change


def get_change(
    db: Session,
    change_id: int,
) -> Change | None:

    return db.get(
        Change,
        change_id,
    )


def get_changes_for_monitor(
    db: Session,
    monitor_id: int,
) -> list[Change]:

    statement = (
        select(Change)
        .where(
            Change.monitor_id == monitor_id
        )
        .order_by(
            Change.detected_at.desc(),
            Change.id.desc(),
        )
    )

    return list(
        db.scalars(statement).all()
    )