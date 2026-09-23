"""
MEVZUAT KAYNAK TANIMA

Bir referans adresinin NE OLDUGUNU belirler:

    - guncel KONSOLIDE mevzuat metni mi?
    - yoksa Resmî Gazete arsivindeki TARIHSEL yayin mi?

NEDEN GEREKLI

Resmî Gazete arsivi bir yonetmeligi YAYIMLANDIGI HALIYLE
saklar. 2021'de yayimlanmis bir yonetmeligin arsiv sayfasi
2022, 2024 ve 2025 degisikliklerini ICERMEZ.

2026 tarihli yeni bir degisikligi bu eski metinle
karsilastirmak yaniltici olur: karsilastirma, aradaki
degisiklikleri gormemis bir metne dayanir.

mevzuat.gov.tr ise KONSOLIDE metni yayimlar; ustune islenmiş
her degisiklik metnin icinde
"(Değişik:RG-23/6/2018-30457)" gibi isaretlerle durur.

TESPIT EDILEN ENDPOINT

mevzuat.gov.tr'de kullanicinin gordugu adres bir kabuk
sayfadir ve metni bir iframe icinde gosterir:

    https://www.mevzuat.gov.tr/mevzuat
        ?MevzuatNo=39080&MevzuatTur=7&MevzuatTertip=5
      -> kabuk sayfa, metin YOK (2201 karakter menu)

    https://www.mevzuat.gov.tr/anasayfa/MevzuatFihristDetayIframe
        ?MevzuatTur=7&MevzuatNo=39080&MevzuatTertip=5
      -> text/html, UTF-8, 264.048 karakter, 88 MADDE

Ayni belgenin .doc ve .pdf surumleri de var; ancak .doc
bozuk hizalanmis UTF-16 (kullanilamaz), .pdf ise bu
surumde desteklenmiyor. Kullanilan sey HTML iframe'idir.

BU MODUL

  - Gemini CAGIRMAZ
  - ag istegi YAPMAZ (yalnizca URL cozumler)
  - veritabanina dokunmaz

Otomatik mevzuat esleme YAPMAZ. Kullanicinin verdigi adresi
tanir ve gerekiyorsa ayni belgenin metin adresine cevirir.
"""

import re

from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse


# ---------------------------------------------------------
# KAYNAK TURLERI
# ---------------------------------------------------------

# mevzuat.gov.tr konsolide metni.
SOURCE_TYPE_MEVZUAT_GOV_TR = "mevzuat_gov_tr"

# Resmî Gazete arsivi: yayimlandigi ANDAKI metin.
SOURCE_TYPE_RESMI_GAZETE = "resmi_gazete_archive"

# Tanimadigimiz herhangi bir HTML kaynagi.
SOURCE_TYPE_GENERIC = "generic"

SOURCE_TYPES = (
    SOURCE_TYPE_MEVZUAT_GOV_TR,
    SOURCE_TYPE_RESMI_GAZETE,
    SOURCE_TYPE_GENERIC,
)


# Kullaniciya gosterilecek etiketler.
SOURCE_TYPE_LABELS = {
    SOURCE_TYPE_MEVZUAT_GOV_TR: "Mevzuat Bilgi Sistemi",
    SOURCE_TYPE_RESMI_GAZETE: "Resmî Gazete arşivi",
    SOURCE_TYPE_GENERIC: "Diğer kaynak",
}


MEVZUAT_HOSTS = {
    "mevzuat.gov.tr",
    "www.mevzuat.gov.tr",
}

RESMI_GAZETE_HOSTS = {
    "resmigazete.gov.tr",
    "www.resmigazete.gov.tr",
}


# Metni gercekten tasiyan adres.
MEVZUAT_TEXT_PATH = "/anasayfa/MevzuatFihristDetayIframe"

MEVZUAT_TEXT_URL = (
    "https://www.mevzuat.gov.tr" + MEVZUAT_TEXT_PATH
)


# Kullanicinin gordugu KABUK sayfa adresi.
#
# Bir mevzuat kaydinin KANONIK kimligi budur: kayda
# yazilan source_url bu bicimde uretilir.
MEVZUAT_SHELL_PATH = "/mevzuat"

MEVZUAT_SHELL_URL = (
    "https://www.mevzuat.gov.tr" + MEVZUAT_SHELL_PATH
)


@dataclass(frozen=True)
class ResolvedSource:
    """
    Bir referans adresinin cozumlenmis hali.

    fetch_url: metnin GERCEKTEN alinacagi adres.
               Kullanicinin girdigi adresten farkli olabilir
               (kabuk sayfa -> metin adresi).

    source_url: kayda yazilacak adres. Kullanicinin girdigi
                adres korunur; boylece kullanici kendi
                verdigi baglantiyi listede taniyabilir.
    """

    source_url: str
    fetch_url: str

    source_type: str
    is_consolidated: bool

    # Kabuk sayfa metin adresine cevrildi mi?
    rewritten: bool = False

    @property
    def source_label(self) -> str:
        return SOURCE_TYPE_LABELS.get(
            self.source_type,
            SOURCE_TYPE_LABELS[SOURCE_TYPE_GENERIC],
        )


def _host_of(url: str) -> str:

    try:
        return (urlparse(url).hostname or "").lower()

    except ValueError:
        return ""


def _single_param(query: dict, *names: str) -> str | None:
    """
    Sorgu parametresini BUYUK/KUCUK harf duyarsiz okur.

    mevzuat.gov.tr adreslerinde parametre yazimi
    tutarli degil (MevzuatNo / mevzuatno).
    """

    lowered = {
        key.lower(): value
        for key, value in query.items()
    }

    for name in names:
        values = lowered.get(name.lower())

        if values:
            cleaned = (values[0] or "").strip()

            if cleaned:
                return cleaned

    return None


NUMERIC_PATTERN = re.compile(r"^\d{1,10}$")


def build_mevzuat_text_url(
    tur: str,
    no: str,
    tertip: str,
) -> str:
    """
    mevzuat.gov.tr metin (iframe) adresini kurar.

    Parametreler yalnizca RAKAM olabilir; boylece
    kullanicidan gelen deger adres icine olduğu gibi
    tasinmaz.
    """

    return (
        f"{MEVZUAT_TEXT_URL}"
        f"?MevzuatTur={tur}"
        f"&MevzuatNo={no}"
        f"&MevzuatTertip={tertip}"
    )


def build_mevzuat_shell_url(
    tur: str,
    no: str,
    tertip: str,
) -> str:
    """
    mevzuat.gov.tr KABUK (kullaniciya gosterilen) adresini
    KANONIK bicimde kurar.

    Parametre SIRASI sabittir:

        MevzuatNo -> MevzuatTur -> MevzuatTertip

    Ayni belge farkli sirayla gelse bile bu fonksiyondan
    HER ZAMAN ayni metin cikar; boylece source_url uzerinde
    duplicate olusmaz.

    Parametreler yalnizca RAKAM olabilir; kullanicidan
    (veya bir arama sonucundan) gelen deger adres icine
    olduğu gibi tasinmaz.
    """

    for value in (tur, no, tertip):
        if not NUMERIC_PATTERN.match((value or "").strip()):
            raise ValueError(
                "mevzuat.gov.tr adres parametreleri yalnızca "
                "rakamlardan oluşabilir."
            )

    return (
        f"{MEVZUAT_SHELL_URL}"
        f"?MevzuatNo={no.strip()}"
        f"&MevzuatTur={tur.strip()}"
        f"&MevzuatTertip={tertip.strip()}"
    )


def parse_mevzuat_identity(
    url: str,
) -> tuple[str, str, str] | None:
    """
    Bir mevzuat.gov.tr adresinden belge kimligini cikarir.

    Doner: (tur, no, tertip) veya tanimadigi adres icin None.

    Hem kabuk hem iframe adresi tanninir; parametre sirasi
    ve buyuk/kucuk harf yazimi ONEMSIZDIR.
    """

    cleaned = (url or "").strip()

    if not cleaned:
        return None

    if _host_of(cleaned) not in MEVZUAT_HOSTS:
        return None

    try:
        parsed = urlparse(cleaned)

    except ValueError:
        return None

    query = parse_qs(parsed.query, keep_blank_values=False)

    tur = _single_param(query, "MevzuatTur")
    no = _single_param(query, "MevzuatNo")
    tertip = _single_param(query, "MevzuatTertip")

    if not all(
        value is not None and NUMERIC_PATTERN.match(value)
        for value in (tur, no, tertip)
    ):
        return None

    return tur, no, tertip


def canonical_mevzuat_url(url: str) -> str | None:
    """
    Bir mevzuat.gov.tr adresinin KANONIK kabuk bicimi.

    Ayni belgeyi gosteren farkli yazimlar (parametre sirasi,
    iframe adresi, harf yazimi) ayni metne indirgenir.

    Tanimadigi adres icin None doner; uydurma bir kanonik
    adres URETILMEZ.
    """

    identity = parse_mevzuat_identity(url)

    if identity is None:
        return None

    tur, no, tertip = identity

    return build_mevzuat_shell_url(
        tur=tur,
        no=no,
        tertip=tertip,
    )


def resolve_reference_source(url: str) -> ResolvedSource:
    """
    Referans adresini cozumler.

    Tanimadigi her adres GENERIC ve is_consolidated=False
    kabul edilir. Konsolide oldugu KANITLANAMAYAN bir metin
    asla konsolide gibi isaretlenmez.
    """

    cleaned = (url or "").strip()

    if not cleaned:
        return ResolvedSource(
            source_url=cleaned,
            fetch_url=cleaned,
            source_type=SOURCE_TYPE_GENERIC,
            is_consolidated=False,
        )

    host = _host_of(cleaned)

    # -----------------------------------------------------
    # RESMI GAZETE ARSIVI
    #
    # Yayimlandigi andaki metin. Sonraki degisiklikleri
    # ICERMEZ; bu yuzden konsolide DEGILDIR.
    # -----------------------------------------------------

    if host in RESMI_GAZETE_HOSTS:
        return ResolvedSource(
            source_url=cleaned,
            fetch_url=cleaned,
            source_type=SOURCE_TYPE_RESMI_GAZETE,
            is_consolidated=False,
        )

    # -----------------------------------------------------
    # MEVZUAT BILGI SISTEMI
    # -----------------------------------------------------

    if host in MEVZUAT_HOSTS:

        parsed = urlparse(cleaned)

        query = parse_qs(parsed.query, keep_blank_values=False)

        tur = _single_param(query, "MevzuatTur")
        no = _single_param(query, "MevzuatNo")
        tertip = _single_param(query, "MevzuatTertip")

        already_text_url = (
            parsed.path.lower() == MEVZUAT_TEXT_PATH.lower()
        )

        if all(
            value is not None and NUMERIC_PATTERN.match(value)
            for value in (tur, no, tertip)
        ):
            return ResolvedSource(
                source_url=cleaned,
                fetch_url=(
                    cleaned
                    if already_text_url
                    else build_mevzuat_text_url(
                        tur=tur,
                        no=no,
                        tertip=tertip,
                    )
                ),
                source_type=SOURCE_TYPE_MEVZUAT_GOV_TR,
                is_consolidated=True,
                rewritten=not already_text_url,
            )

        # mevzuat.gov.tr ama tanidigimiz bir belge adresi
        # degil (arama, yayinlar, ana sayfa...). Konsolide
        # oldugunu VARSAYMAYIZ.
        return ResolvedSource(
            source_url=cleaned,
            fetch_url=cleaned,
            source_type=SOURCE_TYPE_GENERIC,
            is_consolidated=False,
        )

    # -----------------------------------------------------
    # DIGER
    # -----------------------------------------------------

    return ResolvedSource(
        source_url=cleaned,
        fetch_url=cleaned,
        source_type=SOURCE_TYPE_GENERIC,
        is_consolidated=False,
    )
