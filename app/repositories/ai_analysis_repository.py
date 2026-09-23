from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ai_analysis import AIAnalysis


def create_ai_analysis(
    db: Session,
    change_id: int,
    model_name: str,
    keyword_relevant: bool,
    keyword_score: float,
    relevant: bool,
    importance: str,
    category: str,
    summary: str,
    notification_recommended: bool,
    evidence_text: str | None = None,
    evidence_type: str | None = None,
) -> AIAnalysis:

    analysis = AIAnalysis(
        change_id=change_id,
        model_name=model_name,
        keyword_relevant=keyword_relevant,
        keyword_score=keyword_score,
        relevant=relevant,
        importance=importance,
        category=category,
        summary=summary,
        notification_recommended=notification_recommended,
        evidence_text=evidence_text,
        evidence_type=evidence_type,
    )

    db.add(analysis)
    db.commit()
    db.refresh(analysis)

    return analysis


def get_ai_analysis_by_change(
    db: Session,
    change_id: int,
) -> AIAnalysis | None:

    statement = (
        select(AIAnalysis)
        .where(
            AIAnalysis.change_id == change_id
        )
        .limit(1)
    )

    return db.scalar(statement)