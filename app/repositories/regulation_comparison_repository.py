from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.regulation_comparison import (
    RegulationComparison,
    RegulationComparisonItem,
)


# ---------------------------------------------------------
# KARSILASTIRMA DURUMLARI
#
# completed : karsilastirma tamamlandi, dogrulanmis item var
# no_match  : ilgili degisiklik var ama referansta guvenilir
#             eslesme bulunamadi
# failed    : teknik / AI hatasi
# ---------------------------------------------------------

STATUS_COMPLETED = "completed"
STATUS_NO_MATCH = "no_match"
STATUS_FAILED = "failed"

COMPARISON_STATUSES = (
    STATUS_COMPLETED,
    STATUS_NO_MATCH,
    STATUS_FAILED,
)


def get_comparison(
    db: Session,
    comparison_id: int,
) -> RegulationComparison | None:

    return db.get(
        RegulationComparison,
        comparison_id,
    )


def get_comparison_by_change(
    db: Session,
    change_id: int,
) -> RegulationComparison | None:
    """
    Bu degisiklik icin daha once karsilastirma yapildi mi?

    DUPLICATE KORUMASININ ILK KATMANI. Gemini cagrisindan
    ONCE kontrol edilir; boylece manuel run ile scheduler
    ayni degisikligi islerse ikinci bir model cagrisi
    CIKMAZ.
    """

    statement = (
        select(RegulationComparison)
        .where(
            RegulationComparison.change_id == change_id
        )
        .limit(1)
    )

    return db.scalar(statement)


def create_comparison(
    db: Session,
    monitor_id: int,
    change_id: int,
    status: str,
    ai_analysis_id: int | None = None,
    reference_regulation_id: int | None = None,
    reference_content_hash: str | None = None,
    reference_is_consolidated: bool | None = None,
    reference_match_status: str | None = None,
    reference_match_method: str | None = None,
    reference_match_confidence: float | None = None,
    reference_freshness_status: str | None = None,
    reference_discovery_status: str | None = None,
    suggested_reference_id: int | None = None,
    overall_summary: str | None = None,
    importance: str | None = None,
    model_name: str | None = None,
    error_message: str | None = None,
    items: list[dict] | None = None,
) -> RegulationComparison:
    """
    Karsilastirma basligini ve varsa madde satirlarini
    TEK islemde yazar.

    items icindeki her sozluk RegulationComparisonItem
    alanlarini tasir. Buraya YALNIZCA sunucu tarafinda
    dogrulanmis metin gelmelidir; dogrulama comparator
    katmaninda yapilir.
    """

    comparison = RegulationComparison(
        monitor_id=monitor_id,
        change_id=change_id,
        ai_analysis_id=ai_analysis_id,
        reference_regulation_id=reference_regulation_id,
        reference_content_hash=reference_content_hash,
        reference_is_consolidated=reference_is_consolidated,
        reference_match_status=reference_match_status,
        reference_match_method=reference_match_method,
        reference_match_confidence=reference_match_confidence,
        reference_freshness_status=reference_freshness_status,
        reference_discovery_status=reference_discovery_status,
        suggested_reference_id=suggested_reference_id,
        status=status,
        overall_summary=overall_summary,
        importance=importance,
        model_name=model_name,
        error_message=error_message,
    )

    for row in items or []:
        comparison.items.append(
            RegulationComparisonItem(**row)
        )

    db.add(comparison)
    db.commit()
    db.refresh(comparison)

    return comparison


def get_comparisons_for_monitor(
    db: Session,
    monitor_id: int,
    limit: int = 50,
) -> list[RegulationComparison]:

    statement = (
        select(RegulationComparison)
        .where(
            RegulationComparison.monitor_id == monitor_id
        )
        .order_by(
            RegulationComparison.created_at.desc(),
            RegulationComparison.id.desc(),
        )
        .limit(limit)
    )

    return list(
        db.scalars(statement).all()
    )
