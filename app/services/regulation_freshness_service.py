"""
REFERANS TAZELIGI

Otomatik secilen bir referansla karsilastirma yapmadan
once, referans metninin GUNCEL olup olmadigini kontrol
eder.

NEDEN GEREKLI

Konsolide mevzuat metni degisir: her yeni Resmî Gazete
degisikligi metne islenir. Onbellekte duran eski bir
konsolide metinle karsilastirma yapmak, aradaki
degisiklikleri gormemis bir metne dayanmak demektir.

KAPSAM

Tazelik yalnizca KONSOLIDE kaynaklar icin anlamlidir:

    is_consolidated = True
    source_type     = mevzuat_gov_tr

Resmî Gazete arsivi tarihsel bir belgedir ve DEGISMEZ;
onu yenilemenin anlami yoktur (not_applicable).

DAVRANIS

    son dogrulama 24 saatten yeniyse -> fetch YOK
    eski ise                          -> refresh
    refresh basarisiz                 -> onbellekteki metin
                                         kullanilir ama
                                         durum acikca
                                         stale_unverified

Bu modul Gemini CAGIRMAZ.
"""

import logging

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.models.regulation_reference import RegulationReference
from app.repositories.regulation_reference_repository import (
    update_regulation_reference_content,
)
from app.services.regulation_reference_service import (
    RegulationReferenceFetchError,
    fetch_regulation_content,
)
from app.services.regulation_source_registry import (
    SOURCE_TYPE_MEVZUAT_GOV_TR,
)


logger = logging.getLogger(__name__)


# ---------------------------------------------------------
# TAZELIK DURUMLARI
# ---------------------------------------------------------

# Yakinda dogrulanmis; ag istegi yapilmadi.
FRESHNESS_FRESH = "fresh"

# Bu calistirmada yenilendi, icerik ayni cikti.
FRESHNESS_REFRESHED = "refreshed"

# Bu calistirmada yenilendi ve icerik DEGISTI.
FRESHNESS_REFRESHED_CHANGED = "refreshed_changed"

# Yenilenemedi; onbellekteki metin kullanildi.
FRESHNESS_STALE_UNVERIFIED = "stale_unverified"

# Konsolide olmayan kaynak; tazelik soz konusu degil.
FRESHNESS_NOT_APPLICABLE = "not_applicable"


FRESHNESS_STATUSES = (
    FRESHNESS_FRESH,
    FRESHNESS_REFRESHED,
    FRESHNESS_REFRESHED_CHANGED,
    FRESHNESS_STALE_UNVERIFIED,
    FRESHNESS_NOT_APPLICABLE,
)


# Bu sure icinde dogrulanmis referans TAZE sayilir.
#
# Amac her monitor calistirmasinda mevzuat.gov.tr'ye
# gereksiz istek atmamaktir.
FRESHNESS_WINDOW = timedelta(hours=24)


@dataclass
class FreshnessOutcome:
    status: str

    # Karsilastirmanin devam etmesi guvenli mi?
    #
    # Onbellekteki metin kullanilabilir oldugu surece
    # True'dur; yalnizca elde HICBIR guvenilir metin
    # yoksa False olur.
    usable: bool = True

    # Refresh sirasinda icerik degisti mi?
    content_changed: bool = False

    # Basarisiz yenilemenin TEMIZLENMIS sebebi.
    error: str | None = None


def is_consolidated_source(
    reference: RegulationReference,
) -> bool:
    """
    Bu referans tazelik kontrolune tabi mi?
    """

    return bool(
        reference.is_consolidated
        and reference.source_type == SOURCE_TYPE_MEVZUAT_GOV_TR
    )


def is_within_window(
    reference: RegulationReference,
    now: datetime | None = None,
) -> bool:
    """
    Referans yakinda dogrulanmis mi?
    """

    verified_at = reference.last_verified_at

    if verified_at is None:
        return False

    current = now or datetime.utcnow()

    return (current - verified_at) < FRESHNESS_WINDOW


def _sanitize(message: str) -> str:
    """
    Kullaniciya gosterilecek kisa hata.
    """

    cleaned = " ".join((message or "").split())

    return cleaned[:200] or "bilinmeyen hata"


async def ensure_reference_fresh(
    db: Session,
    reference: RegulationReference,
    now: datetime | None = None,
) -> FreshnessOutcome:
    """
    Referansi gerekiyorsa yeniler.

    Bu fonksiyon HICBIR ZAMAN hata firlatmaz. Yenileme
    basarisiz olsa bile onbellekteki metin kullanilabilir
    ve durum acikca stale_unverified olarak bildirilir.
    """

    # -----------------------------------------------------
    # 1. KAPSAM DISI
    #
    # Resmî Gazete arsivi tarihsel bir belgedir; yenilemek
    # anlamsizdir.
    # -----------------------------------------------------

    if not is_consolidated_source(reference):
        return FreshnessOutcome(
            status=FRESHNESS_NOT_APPLICABLE,
            usable=True,
        )

    # -----------------------------------------------------
    # 2. YAKINDA DOGRULANMIS
    #
    # Ag istegi YAPILMAZ.
    # -----------------------------------------------------

    if is_within_window(reference, now=now):
        return FreshnessOutcome(
            status=FRESHNESS_FRESH,
            usable=True,
        )

    # -----------------------------------------------------
    # 3. YENILE
    # -----------------------------------------------------

    try:
        content = await fetch_regulation_content(
            reference.source_url
        )

    except RegulationReferenceFetchError as exc:

        logger.warning(
            "Referans yenilenemedi, onbellekteki metin "
            "kullanilacak. reference_id=%s hata=%s",
            reference.id,
            exc,
        )

        return FreshnessOutcome(
            status=FRESHNESS_STALE_UNVERIFIED,

            # Onbellekte GECERLI bir metin var; karsilastirma
            # yapilabilir ama kullaniciya uyari gosterilir.
            usable=bool((reference.content or "").strip()),

            error=_sanitize(str(exc)),
        )

    except Exception as exc:  # noqa: BLE001

        logger.exception(
            "Referans yenilemede beklenmeyen hata. "
            "reference_id=%s",
            reference.id,
        )

        return FreshnessOutcome(
            status=FRESHNESS_STALE_UNVERIFIED,
            usable=bool((reference.content or "").strip()),
            error=_sanitize(
                f"{type(exc).__name__}: {exc}"
            ),
        )

    changed = update_regulation_reference_content(
        db=db,
        reference=reference,
        content=content.text,
        source_type=content.source_type,
        is_consolidated=content.is_consolidated,
        version_date=content.version_date,
    )

    return FreshnessOutcome(
        status=(
            FRESHNESS_REFRESHED_CHANGED
            if changed
            else FRESHNESS_REFRESHED
        ),
        usable=True,
        content_changed=changed,
    )
