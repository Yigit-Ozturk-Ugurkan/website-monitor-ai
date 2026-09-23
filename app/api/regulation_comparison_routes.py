"""
MEVZUAT KARSILASTIRMASI UC NOKTASI

Bu uc nokta Gemini CAGIRMAZ. Karsilastirma monitor
calistirmasi sirasinda bir kez yapilir ve veritabaninda
saklanir; buradaki istek yalnizca kayitli sonucu okur.

Bildirim detay ekrani karsilastirmayi ZATEN nested olarak
alir; bu uc nokta tek bir karsilastirmayi dogrudan gormek
isteyen istemciler icindir.
"""

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
)
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.repositories.regulation_comparison_repository import (
    get_comparison,
)
from app.schemas.regulation_comparison import (
    RegulationComparisonResponse,
)


router = APIRouter(
    prefix="/api/regulation-comparisons",
    tags=["Regulation Comparisons"],
)


@router.get(
    "/{comparison_id}",
    response_model=RegulationComparisonResponse,
)
def regulation_comparison_detail(
    comparison_id: int,
    db: Session = Depends(get_db),
):
    comparison = get_comparison(
        db=db,
        comparison_id=comparison_id,
    )

    if comparison is None:
        raise HTTPException(
            status_code=404,
            detail="Mevzuat karşılaştırması bulunamadı.",
        )

    return comparison
