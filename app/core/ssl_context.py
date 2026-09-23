"""
HTTPS DOGRULAMA BAGLAMI

Bu modul TEK bir sorunu cozer: bazi Turk kamu siteleri
(resmigazete.gov.tr, mevzuat.gov.tr) TLS el sikismasinda
ARA SERTIFIKAYI gondermiyor.

TESHIS

    Sunucu sertifikasi GECERLIDIR:
        CN=*.tccb.gov.tr
        issuer: CN=GeoTrust TLS RSA CA G1 (DigiCert)

    Ancak sunucu bu ara sertifikayi gondermiyor. Kok
    sertifika (DigiCert Global Root G2) zaten guvenilir,
    fakat zincirin ORTA halkasi eksik oldugu icin OpenSSL
    yolu kuramiyor:

        SSLCertVerificationError:
        unable to get local issuer certificate

    Tarayicilar bu halkayi sertifikadaki AIA adresinden
    indirip tamamlar; OpenSSL/Python bunu YAPMAZ.

COZUM

    Eksik ara sertifika app/certs/ altindan guven baglamina
    eklenir.

GUVENLIK

    Bu bir zayiflatma DEGILDIR:

        - sertifika dogrulamasi ACIK kalir
        - hostname kontrolu ACIK kalir
        - suresi dolmus / yanlis alan adli / kendinden
          imzali sertifikalar REDDEDILMEYE devam eder
        - yeni bir KOK sertifika EKLENMEZ

    Yalnizca zaten guvenilir bir koke baglanan zincirin
    eksik ara halkasi tamamlanir.

    Dogrulamayi kapatan (verify=False) bir secenek bu
    projede BILEREK YOKTUR.
"""

import logging
import ssl

from functools import lru_cache
from pathlib import Path

import certifi

from app.core.config import settings


logger = logging.getLogger(__name__)


# Projeyle birlikte gelen ara sertifikalar.
BUNDLED_CERT_DIRECTORY = (
    Path(__file__).resolve().parents[1] / "certs"
)

BUNDLED_INTERMEDIATES = (
    BUNDLED_CERT_DIRECTORY / "tr_gov_intermediates.pem"
)


def _read_pem(path: Path) -> str:
    """
    PEM dosyasini okur.

    Okunamayan dosya sessizce atlanir: eksik bir ara
    sertifika dogrulamayi zayiflatmaz, yalnizca ilgili
    siteye erisilemez.
    """

    try:
        return path.read_text(encoding="utf-8")

    except OSError as exc:
        logger.warning(
            "Ek CA dosyasi okunamadi: %s (%s)",
            path,
            exc,
        )

        return ""


def collect_extra_ca_sources() -> list[Path]:
    """
    Guven baglamina eklenecek ek CA dosyalari.

    1. Projeyle gelen ara sertifikalar
    2. EXTRA_CA_BUNDLE ile gosterilen kullanici dosyasi
    """

    sources: list[Path] = []

    if BUNDLED_INTERMEDIATES.exists():
        sources.append(BUNDLED_INTERMEDIATES)

    configured = (settings.extra_ca_bundle or "").strip()

    if configured:
        path = Path(configured)

        if path.exists():
            sources.append(path)
        else:
            logger.warning(
                "EXTRA_CA_BUNDLE bulunamadi: %s",
                path,
            )

    return sources


@lru_cache(maxsize=1)
def get_verification_context() -> ssl.SSLContext:
    """
    Dogrulama ACIK bir SSL baglami dondurur.

    certifi kok deposu + eksik ara sertifikalar.

    Baglam bir kez kurulup yeniden kullanilir; her istekte
    sertifika ayristirmak gereksiz maliyet olurdu.
    """

    context = ssl.create_default_context(
        cafile=certifi.where(),
    )

    # create_default_context zaten bunlari acar.
    # Burada ACIKCA tekrar edilmesinin nedeni, ileride
    # yanlislikla gevsetilmesini zorlastirmaktir.
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED

    loaded = 0

    for path in collect_extra_ca_sources():

        pem = _read_pem(path)

        if not pem.strip():
            continue

        try:
            context.load_verify_locations(cadata=pem)
            loaded += 1

        except ssl.SSLError as exc:
            # Bozuk bir dosya dogrulamayi bozmamali.
            logger.warning(
                "Ek CA yuklenemedi: %s (%s)",
                path,
                exc,
            )

    logger.debug(
        "HTTPS dogrulama baglami hazir. ek CA dosyasi=%s",
        loaded,
    )

    return context
