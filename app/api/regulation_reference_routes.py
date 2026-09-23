"""
REFERANS YONETMELIK UC NOKTALARI

Bu uc noktalar Gemini CAGIRMAZ. Referans olusturmak,
listelemek, detay gormek ve yenilemek hicbir AI istegi
uretmez.

Icerik alma yolu website monitor ile AYNIDIR: web_fetcher +
content_extractor. Yeni bir indirme yolu veya yeni bir
guvenlik davranisi tanimlanmaz.
"""

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.repositories.regulation_reference_repository import (
    create_regulation_reference,
    get_regulation_reference,
    get_regulation_reference_by_url,
    get_regulation_references,
    update_regulation_reference_content,
)
from app.schemas.regulation_reference import (
    MAX_PREVIEW_CHARS,
    RegulationReferenceCreate,
    RegulationReferenceDetail,
    RegulationReferenceMatchCandidate,
    RegulationReferenceMatchRequest,
    RegulationReferenceMatchResponse,
    RegulationReferenceRefreshResponse,
    RegulationReferenceSummary,
    build_preview,
)
from app.services.regulation_reference_matcher import (
    MATCH_STATUS_MATCHED,
    match_reference,
)
from app.services.regulation_reference_service import (
    RegulationReferenceFetchError,
    fetch_regulation_content,
)


router = APIRouter(
    prefix="/api/regulation-references",
    tags=["Regulation References"],
)


DUPLICATE_URL_MESSAGE = (
    "Bu kaynak adresi için zaten bir referans yönetmelik "
    "kayıtlı. Listeden mevcut kaydı seçebilir veya "
    "içeriğini yenileyebilirsiniz."
)


def build_detail(reference) -> RegulationReferenceDetail:
    """
    Detay yaniti uretir.

    Tam metin yerine kisaltilmis DUZ METIN onizleme
    gonderilir.
    """

    content = reference.content or ""

    return RegulationReferenceDetail(
        id=reference.id,
        title=reference.title,
        source_url=reference.source_url,
        content_length=reference.content_length,
        content_hash=reference.content_hash,
        source_type=reference.source_type,
        is_consolidated=reference.is_consolidated,
        version_date=reference.version_date,
        last_verified_at=reference.last_verified_at,
        created_at=reference.created_at,
        updated_at=reference.updated_at,
        preview=build_preview(content),
        preview_truncated=len(content) > MAX_PREVIEW_CHARS,
    )


@router.get(
    "",
    response_model=list[RegulationReferenceSummary],
)
def list_regulation_references(
    db: Session = Depends(get_db),
):
    """
    Monitor formundaki secim listesi icin referanslari
    dondurur.

    Tam metin BU YANITTA YOKTUR.
    """

    return get_regulation_references(db)


@router.get(
    "/{reference_id}",
    response_model=RegulationReferenceDetail,
)
def regulation_reference_detail(
    reference_id: int,
    db: Session = Depends(get_db),
):
    reference = get_regulation_reference(
        db=db,
        reference_id=reference_id,
    )

    if reference is None:
        raise HTTPException(
            status_code=404,
            detail="Referans yönetmelik bulunamadı.",
        )

    return build_detail(reference)


@router.post(
    "",
    response_model=RegulationReferenceSummary,
    status_code=status.HTTP_201_CREATED,
)
async def create_new_regulation_reference(
    payload: RegulationReferenceCreate,
    db: Session = Depends(get_db),
):
    """
    Kaynak adresten metni cekip referansi kaydeder.

    Akis:
        1. URL bicim dogrulamasi (sema katmani)
        2. website fetch  (web_fetcher)
        3. content extraction (content_extractor)
        4. temiz metin
        5. content hash
        6. veritabanina yazma

    Ayni kaynak adresi ikinci kez gonderilirse yeni kayit
    olusturulmaz; 409 ile mevcut kayda yonlendirilir.
    """

    # Kopya kontrolu fetch'ten ONCE yapilir; gereksiz bir
    # ag istegi ve bosuna bekleme olusmasin.
    existing = get_regulation_reference_by_url(
        db=db,
        source_url=payload.source_url,
    )

    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail=DUPLICATE_URL_MESSAGE,
        )

    try:
        content = await fetch_regulation_content(
            payload.source_url
        )

    except RegulationReferenceFetchError as exc:
        raise HTTPException(
            status_code=422,
            detail=str(exc),
        ) from exc

    try:
        reference = create_regulation_reference(
            db=db,
            title=payload.title,
            source_url=payload.source_url,
            content=content.text,

            # Surum bilgisi fetch sirasinda belirlendi.
            # Konsolide oldugu KANITLANAMAYAN metin
            # konsolide olarak isaretlenmez.
            source_type=content.source_type,
            is_consolidated=content.is_consolidated,
            version_date=content.version_date,
        )

    except IntegrityError as exc:
        # Iki es zamanli istek ayni adresi kaydetmeye
        # calistiysa veritabani kisiti son sozu soyler.
        db.rollback()

        raise HTTPException(
            status_code=409,
            detail=DUPLICATE_URL_MESSAGE,
        ) from exc

    return reference


@router.post(
    "/{reference_id}/refresh",
    response_model=RegulationReferenceRefreshResponse,
)
async def refresh_regulation_reference(
    reference_id: int,
    db: Session = Depends(get_db),
):
    """
    Referansi kaynagindan yeniden ceker.

    Icerik gercekten degistiyse kayit guncellenir
    (changed=True). Hash ayniysa HICBIR alan degistirilmez;
    updated_at bosuna oynatilmaz.

    Bu uc nokta scheduler'a BAGLI DEGILDIR; yalnizca
    kullanici istegiyle calisir.
    """

    reference = get_regulation_reference(
        db=db,
        reference_id=reference_id,
    )

    if reference is None:
        raise HTTPException(
            status_code=404,
            detail="Referans yönetmelik bulunamadı.",
        )

    previous_content_hash = reference.content_hash

    try:
        content = await fetch_regulation_content(
            reference.source_url
        )

    except RegulationReferenceFetchError as exc:
        raise HTTPException(
            status_code=422,
            detail=str(exc),
        ) from exc

    changed = update_regulation_reference_content(
        db=db,
        reference=reference,
        content=content.text,

        # Kaynak turu ve konsolidelik her yenilemede
        # yeniden belirlenir; kullanici adresi
        # duzeltirse kayit da duzelir.
        source_type=content.source_type,
        is_consolidated=content.is_consolidated,
        version_date=content.version_date,
    )

    return RegulationReferenceRefreshResponse(
        id=reference.id,
        title=reference.title,
        source_url=reference.source_url,
        content_length=reference.content_length,
        content_hash=reference.content_hash,
        source_type=reference.source_type,
        is_consolidated=reference.is_consolidated,
        version_date=reference.version_date,
        last_verified_at=reference.last_verified_at,
        created_at=reference.created_at,
        updated_at=reference.updated_at,
        changed=changed,
        previous_content_hash=previous_content_hash,
    )


# Oneride gosterilecek en fazla aday.
MAX_MATCH_CANDIDATES = 5


@router.post(
    "/match",
    response_model=RegulationReferenceMatchResponse,
)
async def match_regulation_reference(
    payload: RegulationReferenceMatchRequest,
    db: Session = Depends(get_db),
):
    """
    Bir degisiklik metni icin uygun referansi ONERIR.

    Bu uc nokta:
      - veritabanina HICBIR SEY YAZMAZ
      - kullanicinin secimini DEGISTIRMEZ
      - yalnizca oneri uretir

    GEMINI KULLANIMI

    Once tamamen yerel (deterministik) eslestirme yapilir.
    Yapay zeka YALNIZCA birden fazla aday ayirt
    edilemediginde ve allow_ai=true iken devreye girer;
    o durumda da en fazla bir mantiksal cagri yapilir ve
    modelin dondurdugu id sunucu tarafinda dogrulanir.
    """

    references = get_regulation_references(db)

    result = await match_reference(
        source_text=payload.source_text,
        references=references,
        title=payload.title,
        allow_ai=payload.allow_ai,
    )

    base_name = result.base_name

    return RegulationReferenceMatchResponse(
        status=result.status,

        reference_id=(
            result.reference.id
            if result.status == MATCH_STATUS_MATCHED
            and result.reference is not None
            else None
        ),
        reference_title=(
            result.reference.title
            if result.status == MATCH_STATUS_MATCHED
            and result.reference is not None
            else None
        ),

        base_regulation_name=(
            base_name.name if base_name is not None else None
        ),
        base_published_on=(
            base_name.published_on
            if base_name is not None
            else None
        ),
        base_gazette_issue=(
            base_name.official_gazette_issue
            if base_name is not None
            else None
        ),

        score=result.score,
        ai_used=result.ai_used,
        detail=result.detail,

        candidates=[
            RegulationReferenceMatchCandidate(
                reference_id=candidate.reference_id,
                title=candidate.reference.title,
                score=round(candidate.score, 4),
                is_consolidated=candidate.is_consolidated,
                source_type=candidate.reference.source_type,
            )
            for candidate in result.candidates[
                :MAX_MATCH_CANDIDATES
            ]
            if candidate.score > 0.0
        ],
    )
