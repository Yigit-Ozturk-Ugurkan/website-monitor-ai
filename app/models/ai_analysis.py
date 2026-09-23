from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AIAnalysis(Base):
    __tablename__ = "ai_analyses"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True,
    )

    change_id: Mapped[int] = mapped_column(
        ForeignKey(
            "changes.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        unique=True,
        index=True,
    )

    model_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    keyword_relevant: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
    )

    keyword_score: Mapped[float] = mapped_column(
        Float,
        nullable=False,
    )

    relevant: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
    )

    importance: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    category: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    summary: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    notification_recommended: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
    )

    # Gemini kararinin dayandigi kaynak metin.
    # Eski kayitlarda null kalabilir.
    evidence_text: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # "added" | "removed" | None
    evidence_type: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
    )

    analyzed_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )