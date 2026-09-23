"""
DEGISIKLIK METNINDEN BASE MEVZUAT ADI CIKARMA

Bir Resmî Gazete degisiklik yonetmeliginin HANGI mevzuati
degistirdigini bulur.

ORNEK

    Baslik:
      "ÖDEME HİZMETLERİ VE ELEKTRONİK PARA İHRACI İLE ÖDEME
       HİZMETİ SAĞLAYICILARI HAKKINDA YÖNETMELİKTE
       DEĞİŞİKLİK YAPILMASINA DAİR YÖNETMELİK"

    Cikan base ad:
      "ÖDEME HİZMETLERİ VE ELEKTRONİK PARA İHRACI İLE ÖDEME
       HİZMETİ SAĞLAYICILARI HAKKINDA YÖNETMELİK"

    Govde:
      "1/12/2021 tarihli ve 31676 sayılı Resmî Gazete'de
       yayımlanan Ödeme Hizmetleri ... Hakkında
       Yönetmeliğin 41 inci maddesinin ..."

    Ayni base ad + yayim tarihi + RG sayisi.

BU MODUL TAMAMEN DETERMINISTIKTIR:

  - Gemini CAGIRMAZ
  - ag istegi yapmaz
  - veritabanina dokunmaz

TAM BIR TURKCE DILBILGISI COZUMLEYICISI DEGILDIR. Resmî
Gazete'de yaygin olan kaliplar taninir; taninmayan bicimler
None dondurur ve ust katman guvenli tarafa duser
(otomatik secim yapilmaz).
"""

import re

from dataclasses import dataclass, field
from datetime import date

from app.services.regulation_text_parser import turkish_lower


# ---------------------------------------------------------
# MEVZUAT TURLERI
#
# Degisiklik basligi genellikle su kaliba uyar:
#
#   <BASE AD> <TUR EKI> DEĞİŞİKLİK YAPILMASINA DAİR <TUR>
#
# Tur ekleri Turkce bulunma/ilgi hali aldigi icin
# birden fazla bicimde gorunur:
#
#   YÖNETMELİKTE / YÖNETMELİĞİNDE / YÖNETMELİĞİN
#   TEBLİĞDE     / TEBLİĞİNDE     / TEBLİĞİN
# ---------------------------------------------------------

REGULATION_KIND_YONETMELIK = "yönetmelik"
REGULATION_KIND_TEBLIG = "tebliğ"
REGULATION_KIND_KARARNAME = "kararname"
REGULATION_KIND_KANUN = "kanun"


# Her tur icin: cekimli ekler -> kanonik ad.
#
# Sira ONEMLI: uzun ekler once denenir, aksi halde
# "YÖNETMELİĞİNDE" icindeki "YÖNETMELİĞİN" once eslesir.
KIND_SUFFIXES = (
    (
        REGULATION_KIND_YONETMELIK,
        (
            "yönetmeliğinin",
            "yönetmeliğinde",
            "yönetmelikte",
            "yönetmeliğine",
            "yönetmeliğin",
            "yönetmeliğe",
            "yönetmeliği",
            "yönetmelik",
        ),
    ),
    (
        REGULATION_KIND_TEBLIG,
        (
            "tebliğinin",
            "tebliğinde",
            "tebliğine",
            "tebliğde",
            "tebliğin",
            "tebliğe",
            "tebliği",
            "tebliğ",
        ),
    ),
    (
        REGULATION_KIND_KARARNAME,
        (
            "kararnamesinde",
            "kararnamesine",
            "kararnamesinin",
            "kararnamede",
            "kararnamesi",
            "kararnameye",
            "kararname",
        ),
    ),
    (
        REGULATION_KIND_KANUN,
        (
            "kanununda",
            "kanununa",
            "kanununun",
            "kanunda",
            "kanunu",
            "kanuna",
            "kanun",
        ),
    ),
)


# Kanonik tur adi (base ada geri eklenir).
KIND_CANONICAL = {
    REGULATION_KIND_YONETMELIK: "Yönetmelik",
    REGULATION_KIND_TEBLIG: "Tebliğ",
    REGULATION_KIND_KARARNAME: "Kararname",
    REGULATION_KIND_KANUN: "Kanun",
}


# "... DEĞİŞİKLİK YAPILMASINA DAİR ..." kalibi.
#
# Bu ifade basligi IKIYE boler:
#   solda  -> degistirilen mevzuat
#   sagda  -> degistiren mevzuatin turu
AMENDMENT_SPLIT_PATTERN = re.compile(
    r"\bdeğişiklik\s+yapılmasına\s+dair\b"
    r"|\bdeğişiklik\s+yapılması\s+hakkında\b"
    r"|\bdeğişiklik\s+yapılmasına\s+ilişkin\b",
    re.UNICODE,
)


# Govdedeki "... yayımlanan <AD> Yönetmeliğin ..." kalibi.
#
# Resmî Gazete degisiklik metinleri ilk maddede hedef
# mevzuati bu sekilde tanimlar.
# Ad, MADDE atifindan hemen once biter:
#
#   "... Yönetmeliğin 41 inci maddesinin ..."
#   "... Yönetmeliğe aşağıdaki madde eklenmiştir."
#
# Ileriye bakis MADDE atifina cirpilir; boylece cekimli ek
# ("...ğin") ADIN ICINDE kalir ve tur eki dogru ayrilabilir.
PUBLISHED_REFERENCE_PATTERN = re.compile(
    r"yayımlanan\s+(?P<name>.{10,300}?)"
    r"\s+(?="
    r"\d+\s*(?:['’]?\s*"
    r"(?:inci|ıncı|uncu|üncü|nci|ncı|ncu|ncü|ci|cı|cu|cü)"
    r")?\s*\.?\s*madde"
    r"|aşağıdaki"
    r"|geçici\s+\d"
    r"|ek\s+madde"
    r")",
    re.UNICODE | re.IGNORECASE,
)


# Yayim bilgisi: "1/12/2021 tarihli ve 31676 sayılı"
PUBLICATION_PATTERN = re.compile(
    r"(?P<day>\d{1,2})\s*/\s*(?P<month>\d{1,2})\s*/\s*"
    r"(?P<year>\d{4})\s*tarihli\s*ve\s*"
    r"(?P<issue>\d{3,6})\s*(?:mükerrer\s*)?sayılı",
    re.UNICODE | re.IGNORECASE,
)


# RESMÎ GAZETE UST BILGISI
#
# Gercek sayfalarda baslikten once su blok bulunur:
#
#   4 Eylül 2026 CUMA
#   Resmî Gazete
#   Sayı : 33360
#   YÖNETMELİK
#   Türkiye Cumhuriyet Merkez Bankasından:
#
# Bu blok base ada karisirsa ad, kayitli referans
# adlariyla eslesmez. Metin duzlestirildigi icin blok
# tek satir halinde gelir ve buradan temizlenir.
GAZETTE_HEADER_PATTERN = re.compile(
    r"^.*?resmî\s+gazete\s+sayı\s*:\s*\d+\s*",
    re.IGNORECASE | re.UNICODE,
)


# Ust bilgiden sonra gelen "<KURUM>ndan:" satiri.
ISSUER_PATTERN = re.compile(
    r"^\s*(?:yönetmelik|tebliğ|karar|genelge)?\s*"
    r"[^:]{0,120}?(?:dan|den|ndan|nden)\s*:\s*",
    re.IGNORECASE | re.UNICODE,
)


# Baslikta gecmemesi gereken bas kisimlar (RG ust bilgisi).
NOISE_PREFIXES = (
    "resmî gazete",
    "resmi gazete",
    "yönetmelik",
    "tebliğ",
    "sayı",
    "karar sayısı",
)


# Base adin anlamli sayilmasi icin gereken en az uzunluk.
MIN_BASE_NAME_LENGTH = 12

# Cok uzun "ad" genellikle yanlis yakalanmis paragraftir.
MAX_BASE_NAME_LENGTH = 300


@dataclass
class BaseRegulationName:
    """
    Degisikligin hedefledigi mevzuatin kimligi.

    name: kullaniciya gosterilebilir base ad.
          "Ödeme Hizmetleri ... Hakkında Yönetmelik"

    kind: yönetmelik | tebliğ | kararname | kanun
    """

    name: str
    kind: str

    # Nereden cikarildi: "title" | "body"
    origin: str = "title"

    # Govdede bulunduysa hedef mevzuatin yayim bilgisi.
    published_on: date | None = None
    official_gazette_issue: str | None = None

    @property
    def normalized(self) -> str:
        return normalize_regulation_name(self.name)

    @property
    def tokens(self) -> set[str]:
        return name_tokens(self.name)


# ---------------------------------------------------------
# NORMALIZASYON
# ---------------------------------------------------------

# Ad karsilastirmasinda ayirt edici olmayan kelimeler.
NAME_STOP_WORDS = {
    "ve", "ile", "veya", "hakkında", "hakkinda",
    "dair", "ilişkin", "iliskin", "usul", "esaslar",
    "esaslara", "esasları", "hakkındaki",
    "bazı", "bazi",
}


TOKEN_PATTERN = re.compile(
    r"[0-9a-zçğıöşü]+",
    re.UNICODE,
)


def normalize_regulation_name(name: str) -> str:
    """
    Ad karsilastirmasi icin kontrollu normalizasyon.

    Turkce buyuk harf tuzagina karsi turkish_lower
    kullanilir; noktalama ve fazla bosluk temizlenir.
    """

    lowered = turkish_lower(name or "")

    cleaned = re.sub(r"[^0-9a-zçğıöşü\s]", " ", lowered)

    return " ".join(cleaned.split())


def name_tokens(name: str) -> set[str]:
    """
    Adin ayirt edici kelime kumesi.
    """

    normalized = normalize_regulation_name(name)

    return {
        token
        for token in TOKEN_PATTERN.findall(normalized)
        if len(token) > 2 and token not in NAME_STOP_WORDS
    }


def _strip_kind_suffix(text: str) -> tuple[str, str] | None:
    """
    Metnin sonundaki mevzuat turu ekini bulur ve ayirir.

    Doner: (tur eki olmadan govde, kanonik tur) veya None.
    """

    normalized = turkish_lower(text or "").strip()

    normalized = re.sub(r"[\s ]+", " ", normalized)

    for kind, suffixes in KIND_SUFFIXES:
        for suffix in suffixes:

            if normalized.endswith(" " + suffix):
                cut = len(normalized) - len(suffix) - 1

                return text[:cut].strip(), kind

            if normalized == suffix:
                return "", kind

    return None


def _clean_candidate(raw: str) -> str:
    """
    Yakalanan ham metni temizler.

    Basindaki RG ust bilgisi satirlari ve tarih/sayi
    ifadeleri atilir.
    """

    text = " ".join((raw or "").split())

    # Resmî Gazete ust bilgisini at.
    text = GAZETTE_HEADER_PATTERN.sub("", text, count=1)

    # Ardindan gelen kurum satirini at.
    text = ISSUER_PATTERN.sub("", text, count=1)

    # Basindaki yayim bilgisini at.
    text = PUBLICATION_PATTERN.sub(" ", text)

    text = re.sub(
        r"^\s*resmî\s+gazete[’'`]?\s*de\s*",
        "",
        text,
        flags=re.IGNORECASE | re.UNICODE,
    )

    text = " ".join(text.split())

    # Bastaki gurultu satirlarini at.
    lowered = turkish_lower(text)

    for prefix in NOISE_PREFIXES:
        if lowered.startswith(prefix + " "):
            text = text[len(prefix):].strip()
            lowered = turkish_lower(text)

    return text.strip(" -–—:;,.")


def _build_base_name(body: str, kind: str) -> str | None:
    """
    Govde + tur -> kullaniciya gosterilebilir base ad.
    """

    cleaned = _clean_candidate(body)

    if len(cleaned) < MIN_BASE_NAME_LENGTH:
        return None

    if len(cleaned) > MAX_BASE_NAME_LENGTH:
        return None

    return f"{cleaned} {KIND_CANONICAL[kind]}"


def extract_from_title(title: str) -> BaseRegulationName | None:
    """
    Degisiklik BASLIGINDAN base mevzuat adini cikarir.

    "X YÖNETMELİĞİNDE DEĞİŞİKLİK YAPILMASINA DAİR YÖNETMELİK"
      -> "X Yönetmelik"
    """

    if not title:
        return None

    flat = " ".join(title.split())

    lowered = turkish_lower(flat)

    match = AMENDMENT_SPLIT_PATTERN.search(lowered)

    if match is None:
        return None

    left = flat[:match.start()].strip()

    stripped = _strip_kind_suffix(left)

    if stripped is None:
        return None

    body, kind = stripped

    name = _build_base_name(body, kind)

    if name is None:
        return None

    return BaseRegulationName(
        name=name,
        kind=kind,
        origin="title",
    )


def extract_from_body(text: str) -> BaseRegulationName | None:
    """
    Degisiklik GOVDESINDEN hedef mevzuati cikarir.

    "1/12/2021 tarihli ve 31676 sayılı Resmî Gazete'de
     yayımlanan X Yönetmeliğin 41 inci maddesinin ..."
    """

    if not text:
        return None

    flat = " ".join(text.split())

    match = PUBLISHED_REFERENCE_PATTERN.search(flat)

    if match is None:
        return None

    raw = match.group("name")

    stripped = _strip_kind_suffix(raw)

    if stripped is None:
        return None

    body, kind = stripped

    name = _build_base_name(body, kind)

    if name is None:
        return None

    published_on = None
    issue = None

    publication = PUBLICATION_PATTERN.search(flat)

    if publication is not None:
        issue = publication.group("issue")

        try:
            published_on = date(
                int(publication.group("year")),
                int(publication.group("month")),
                int(publication.group("day")),
            )

        except ValueError:
            published_on = None

    return BaseRegulationName(
        name=name,
        kind=kind,
        origin="body",
        published_on=published_on,
        official_gazette_issue=issue,
    )


@dataclass
class BaseNameExtraction:
    """
    Bir degisiklik metninden cikarilan TUM aday base adlar.

    Birden fazla aday olabilir: baslik bir ad, govde baska
    (daha tam) bir ad verebilir. Ikisi de degerlendirilir.
    """

    candidates: list[BaseRegulationName] = field(
        default_factory=list
    )

    @property
    def primary(self) -> BaseRegulationName | None:
        """
        En guvenilir aday.

        GOVDE adi tercih edilir: baslik kisaltilmis veya
        buyuk harfle bozulmus olabilirken govde hedef
        mevzuati tam adiyla ve yayim bilgisiyle tanimlar.
        """

        if not self.candidates:
            return None

        for candidate in self.candidates:
            if candidate.origin == "body":
                return candidate

        return self.candidates[0]

    @property
    def found(self) -> bool:
        return bool(self.candidates)


# Baslik araması icin metnin bas kismi yeter.
TITLE_SCAN_CHARS = 1200


def extract_base_regulation_names(
    source_text: str,
    title: str | None = None,
) -> BaseNameExtraction:
    """
    Degisiklik metninden hedef mevzuat adlarini cikarir.

    Once acik baslik (verildiyse), sonra metnin bas
    kismindaki baslik satirlari, sonra govde kalibi
    denenir.

    Hicbiri tutmazsa BOS sonuc doner; ust katman otomatik
    secim YAPMAZ.
    """

    candidates: list[BaseRegulationName] = []
    by_key: dict[str, BaseRegulationName] = {}

    def add(candidate: BaseRegulationName | None) -> None:
        """
        Adayi ekler.

        Ayni ad iki kez cikarsa (baslik ve govde ayni
        mevzuati gosteriyorsa) kayit ATILMAZ: govdeden
        gelen yayim bilgisi mevcut adayin uzerine islenir.
        Aksi halde tarih/RG sayisi bosuna kaybolurdu.
        """

        if candidate is None:
            return

        key = candidate.normalized

        if not key:
            return

        existing = by_key.get(key)

        if existing is None:
            by_key[key] = candidate
            candidates.append(candidate)
            return

        if (
            existing.published_on is None
            and candidate.published_on is not None
        ):
            existing.published_on = candidate.published_on

        if (
            existing.official_gazette_issue is None
            and candidate.official_gazette_issue is not None
        ):
            existing.official_gazette_issue = (
                candidate.official_gazette_issue
            )

    # 1. Acikca verilen baslik
    add(extract_from_title(title or ""))

    # 2. Metnin bas kismindaki baslik satirlari
    #
    # Resmî Gazete sayfalarinda baslik birden fazla satira
    # bolunur; satirlar birlestirilerek taranir.
    head = (source_text or "")[:TITLE_SCAN_CHARS]

    add(extract_from_title(head))

    # 3. Govdedeki "yayımlanan ..." kalibi
    add(extract_from_body(source_text or ""))

    return BaseNameExtraction(candidates=candidates)
