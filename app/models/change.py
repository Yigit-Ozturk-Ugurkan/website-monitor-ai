from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Change(Base):
    __tablename__ = "changes"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True,
    )

    monitor_id: Mapped[int] = mapped_column(
        ForeignKey(
            "monitors.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    old_snapshot_id: Mapped[int] = mapped_column(
        ForeignKey(
            "snapshots.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    new_snapshot_id: Mapped[int] = mapped_column(
        ForeignKey(
            "snapshots.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    relevant: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
    )

    relevance_score: Mapped[float] = mapped_column(
        Float,
        nullable=False,
    )

    added_content: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    removed_content: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    detected_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )