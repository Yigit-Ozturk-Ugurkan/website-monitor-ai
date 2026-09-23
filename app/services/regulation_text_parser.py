"""
TURKCE MEVZUAT METNI AYRISTIRMA

Bu modul TAMAMEN DETERMINISTIKTIR:

  - Gemini CAGIRMAZ
  - ag istegi yapmaz
  - veritabanina dokunmaz

Iki isi vardir:

  1. SEGMENTASYON
     Referans yonetmelik metnini MADDE bazli parcalara
     ayirir. 1.2 milyon karakterlik bir yonetmelik modele
     hic gonderilmez; yalnizca secilen birkac segment gider.

  2. MADDE REFERANSI COZUMLEME
     Degisiklik metnindeki "12 nci maddesinin" gibi resmi
     kaliplari yakalayip madde numarasina cevirir.

TAM BIR TURKCE DILBILGISI PARSER'I DEGILDIR. Yalnizca
resmi mevzuat dilinde yaygin olan kaliplar taninir; tanimayan
durumlar sessizce atlanir ve ust katman diger eslestirme
yontemlerine duser.
"""

import re

from dataclasses import dataclass, field
from datetime import date


# ---------------------------------------------------------
# MADDE BASLIKLARI
#
# Desteklenen bicimler:
#
#   MADDE 1          Madde 1
#   MADDE 12/A       Madde 12/A
#   GEÇİCİ MADDE 1   Geçici Madde 2
#   EK MADDE 3       Ek Madde 3
#
# Baslik satirin BASINDA olmalidir; metnin ortasindaki
# "12 nci maddesi" gibi atiflar baslik sayilmaz.
# ---------------------------------------------------------

ARTICLE_HEADING_PATTERN = re.compile(
    r"^\s*"
    r"(?P<prefix>GEÇİCİ|GECICI|Geçici|Gecici|EK|Ek)?"
    r"\s*"
    r"(?P<word>MADDE|Madde)"
    r"\s+"
    r"(?P<number>\d+(?:\s*/\s*[A-ZÇĞİÖŞÜa-zçğıöşü])?)"
    r"\s*"
    r"(?P<tail>[-–—:.)]|$|\s)",
    re.UNICODE,
)


# TEK BASINA DURAN ONEK SATIRI
#
# Gercek Resmî Gazete sayfalarinda baslik iki satira
# bolunebiliyor:
#
#     GEÇİCİ
#     MADDE 1 –
#
# Bu durumda "MADDE 1" satiri tek basina normal bir madde
# gibi gorunur ve yonetmeligin GERCEK Madde 1'i ile
# karisir. Onceki satir yalnizca onek kelimesinden
# olusuyorsa bu bir devam satiridir.
STANDALONE_PREFIX_PATTERN = re.compile(
    r"^\s*(?P<prefix>GEÇİCİ|GECICI|Geçici|Gecici|EK|Ek)\s*$",
    re.UNICODE,
)


# BIRINCI BÖLÜM / İKİNCİ BÖLÜM gibi bolum basliklari.
# Segmentin hangi bolume ait oldugunu gostermek icin
# saklanir; segment siniri OLUSTURMAZ.
SECTION_HEADING_PATTERN = re.compile(
    r"^\s*[A-ZÇĞİÖŞÜ\s]{3,40}\s+BÖLÜM\s*$",
    re.UNICODE,
)


# ---------------------------------------------------------
# KONSOLIDASYON ISARETLERI
#
# mevzuat.gov.tr konsolide metinlerinde, uzerine islenmis
# her degisiklik metnin icinde isaretlenir:
#
#   (Değişik:RG-23/6/2018-30457)
#   (Ek:RG-7/10/2023-32332)
#   (Mülga:RG-23/6/2018-30457)
#   (Değişik ibare:RG-31/5/2022-31852)
#   (Ek ibare:RG-4/9/2026-33360)
#   (Değişik:RG-25/8/2022-31934 Mükerrer)
#
# Bu isaretler metnin GERCEKTEN konsolide oldugunun
# kanitidir ve en son ne zaman guncellendigini soyler.
#
# Isaret metinde satira bolunebilir: "(Ek" satirin
# sonunda, "ibare:RG-..." bir sonraki satirda olabilir.
# Bu yuzden kalip satir sonlarina toleranslidir.
# ---------------------------------------------------------

AMENDMENT_MARKER_PATTERN = re.compile(
    r"\("
    r"(?:Değişik|Degisik|Ek|Mülga|Mulga|Yeniden\s+düzenleme)"
    r"[^)]{0,40}?"
    r"RG\s*-\s*"
    r"(?P<day>\d{1,2})\s*/\s*"
    r"(?P<month>\d{1,2})\s*/\s*"
    r"(?P<year>\d{4})"
    r"[^)]{0,40}?"
    r"\)",
    re.UNICODE,
)


@dataclass
class ConsolidationInfo:
    """
    Konsolide metinden cikarilan surum bilgisi.

    marker_count sifirsa metin ya hic degistirilmemistir ya
    da konsolide degildir. Bu ayrim TEK BASINA metinden
    yapilamaz; kaynak turu ile birlikte degerlendirilir.
    """

    marker_count: int = 0

    # Metne islenmis EN SON degisikligin Resmî Gazete
    # tarihi. Bulunamazsa None.
    latest_amendment_date: date | None = None

    earliest_amendment_date: date | None = None

    @property
    def has_markers(self) -> bool:
        return self.marker_count > 0


def extract_consolidation_info(text: str) -> ConsolidationInfo:
    """
    Metindeki degisiklik isaretlerini cikarir.

    Gecersiz tarihler (ornegin 31/2/2020) sessizce atlanir;
    tek bir bozuk isaret yuzunden surum bilgisi
    kaybedilmez.
    """

    if not text:
        return ConsolidationInfo()

    dates: list[date] = []
    count = 0

    for match in AMENDMENT_MARKER_PATTERN.finditer(text):

        count += 1

        try:
            dates.append(
                date(
                    int(match.group("year")),
                    int(match.group("month")),
                    int(match.group("day")),
                )
            )

        except ValueError:
            # Bozuk tarih; isaret sayilir ama tarihe
            # guvenilmez.
            continue

    if not dates:
        return ConsolidationInfo(marker_count=count)

    return ConsolidationInfo(
        marker_count=count,
        latest_amendment_date=max(dates),
        earliest_amendment_date=min(dates),
    )


# ---------------------------------------------------------
# MADDE ATIFLARI (degisiklik metninde)
#
# Desteklenen bicimler:
#
#   5 inci maddesi          5'inci maddesi
#   12 nci maddesinin       12. maddesi
#   12/A maddesi            14 üncü maddesinin ikinci fıkrası
#   geçici 3 üncü maddesi   MADDE 12
#
# Turkce sira sayisi ekleri (inci/nci/uncu/ucuncu...) ve
# kesme isareti opsiyoneldir.
# ---------------------------------------------------------

ORDINAL_SUFFIX = (
    r"(?:\s*['’]?\s*"
    r"(?:inci|ıncı|uncu|üncü|nci|ncı|ncu|ncü|ci|cı|cu|cü)"
    r")?"
)

ARTICLE_REFERENCE_PATTERN = re.compile(
    r"(?P<provisional>geçici|geçici\s|gecici\s)?"
    r"\s*"
    r"(?P<number>\d+(?:\s*/\s*[A-ZÇĞİÖŞÜa-zçğıöşü])?)"
    r"\s*\.?"
    + ORDINAL_SUFFIX +
    r"\s+madde",
    re.IGNORECASE | re.UNICODE,
)


# "MADDE 12" bicimindeki dogrudan atiflar.
DIRECT_ARTICLE_PATTERN = re.compile(
    r"(?P<prefix>geçici|gecici|ek)?"
    r"\s*madde\s+"
    r"(?P<number>\d+(?:\s*/\s*[A-ZÇĞİÖŞÜa-zçğıöşü])?)",
    re.IGNORECASE | re.UNICODE,
)


# Fallback segmentasyonda kullanilan parca boyutu.
FALLBACK_CHUNK_CHARS = 2000

# Cok kucuk artiklarin (yalnizca baslik satiri) gereksiz
# aday olmasini engeller.
MIN_SEGMENT_CHARS = 20

# Guvenlik siniri: bozuk bir metinde sonsuz segment olusmasin.
MAX_SEGMENTS = 4000


@dataclass
class RegulationSegment:
    """
    Referans yonetmeligin tek bir parcasi.

    is_article=False ise bu bir MADDE degil, fallback
    metin blogudur. Bu durumda article_label null kalir ve
    ust katman sonucu "madde eslesmesi" gibi SUNMAZ.
    """

    segment_id: str

    # "Madde 12", "Geçici Madde 3" gibi kullaniciya
    # gosterilebilir etiket. Fallback'te None.
    article_label: str | None

    # "12", "12/A" gibi normalize numara. Fallback'te None.
    article_number: str | None

    # Ait oldugu bolum basligi (BİRİNCİ BÖLÜM vb.).
    heading: str | None

    text: str

    start_line: int
    end_line: int

    is_article: bool = True

    # Geçici madde mi?
    is_provisional: bool = False

    @property
    def text_length(self) -> int:
        return len(self.text)


@dataclass
class RegulationDocument:
    segments: list[RegulationSegment] = field(
        default_factory=list
    )

    # MADDE basligi bulunamadi ve fallback kullanildi mi?
    used_fallback: bool = False

    @property
    def article_count(self) -> int:
        return sum(
            1 for segment in self.segments
            if segment.is_article
        )


# ---------------------------------------------------------
# NORMALIZASYON
# ---------------------------------------------------------

# Python'un varsayilan lower() metodu Turkce buyuk I
# harfleri icin DOGRU SONUC VERMEZ:
#
#   "GEÇİCİ".lower() -> "geçi̇ci̇"
#
# U+0130 (İ) kucultuldugunde "i" + BIRLESIK NOKTA (U+0307)
# uretilir; bu da "geçici" ile karsilastirmayi bozar.
# Mevzuat metinleri buyuk harfle yazilmis basliklar
# icerdigi icin bu tuzak burada gercek bir hataya yol acar.

COMBINING_DOT_ABOVE = "̇"


def turkish_lower(value: str) -> str:
    """
    Turkce farkindalikli kucuk harfe cevirme.

    I -> ı ve İ -> i esleme ELDE yapilir; ardindan geriye
    kalan birlesik noktalar temizlenir.
    """

    if not value:
        return ""

    return (
        value
        .replace("İ", "i")
        .replace("I", "ı")
        .lower()
        .replace(COMBINING_DOT_ABOVE, "")
    )


def normalize_article_number(raw: str) -> str:
    """
    "12 / A" -> "12/A"
    " 5 "    -> "5"

    Harf kismi buyuk harfe cevrilir; Turkce 'i' sorununa
    girmemek icin yalnizca ASCII harfler etkilenir.
    """

    cleaned = re.sub(r"\s+", "", raw or "")

    if "/" in cleaned:
        number, _, letter = cleaned.partition("/")

        return f"{number}/{letter.upper()}"

    return cleaned


def build_article_label(
    article_number: str,
    is_provisional: bool = False,
    is_additional: bool = False,
) -> str:
    """
    Kullaniciya gosterilecek madde etiketi.
    """

    if is_provisional:
        return f"Geçici Madde {article_number}"

    if is_additional:
        return f"Ek Madde {article_number}"

    return f"Madde {article_number}"


def _previous_non_empty_line(
    lines: list[str],
    index: int,
) -> tuple[int, str] | None:
    """
    Verilen satirdan onceki ilk BOS OLMAYAN satir.

    Doner: (satir indeksi, satir metni) veya None.

    Yalnizca bir satir geriye bakilir; uzak satirlar
    yanlislikla onek sayilmasin diye arama sinirlidir.
    """

    position = index - 1

    while position >= 0:

        candidate = lines[position]

        if candidate.strip():
            return position, candidate

        position -= 1

        # Bos satirlarin arasindan cok geriye gidilmez.
        if index - position > 3:
            return None

    return None


def _is_provisional_prefix(prefix: str | None) -> bool:

    if not prefix:
        return False

    return turkish_lower(prefix.strip()) in (
        "geçici",
        "gecici",
    )


def _is_additional_prefix(prefix: str | None) -> bool:

    if not prefix:
        return False

    return turkish_lower(prefix.strip()) in ("ek",)


# ---------------------------------------------------------
# SEGMENTASYON
# ---------------------------------------------------------

def parse_regulation_segments(
    content: str,
) -> RegulationDocument:
    """
    Yonetmelik metnini MADDE bazli segmentlere ayirir.

    MADDE basligi bulunamazsa fallback olarak sinirli
    boyutta metin bloklari uretilir. Fallback segmentlerde
    article_label None kalir; bunlar madde eslesmesi olarak
    SUNULMAZ.
    """

    if not content or not content.strip():
        return RegulationDocument(
            segments=[],
            used_fallback=False,
        )

    lines = content.splitlines()

    # -----------------------------------------------------
    # 1. MADDE BASLIKLARINI BUL
    # -----------------------------------------------------

    starts: list[tuple[int, str, bool, bool]] = []

    current_heading = None
    headings_by_line: dict[int, str | None] = {}

    for index, line in enumerate(lines):

        if SECTION_HEADING_PATTERN.match(line):
            current_heading = line.strip()

        headings_by_line[index] = current_heading

        match = ARTICLE_HEADING_PATTERN.match(line)

        if match is None:
            continue

        prefix = match.group("prefix")

        start_index = index

        # ONCEKI SATIRDAKI ONEK
        #
        # Gercek Resmî Gazete sayfalarinda baslik iki
        # satira bolunebiliyor:
        #
        #     GEÇİCİ
        #     MADDE 1 –
        #
        # Bu birlestirilmezse geçici madde, yonetmeligin
        # GERCEK Madde 1'i gibi gorunur ve yanlis eslesme
        # uretir.
        if not prefix:

            previous = _previous_non_empty_line(lines, index)

            if previous is not None:

                previous_index, previous_line = previous

                prefix_match = STANDALONE_PREFIX_PATTERN.match(
                    previous_line
                )

                if prefix_match is not None:
                    prefix = prefix_match.group("prefix")

                    # Onek satiri da segmentin parcasidir.
                    start_index = previous_index

        starts.append(
            (
                start_index,
                normalize_article_number(
                    match.group("number")
                ),
                _is_provisional_prefix(prefix),
                _is_additional_prefix(prefix),
            )
        )

        if len(starts) >= MAX_SEGMENTS:
            break

    # -----------------------------------------------------
    # 2. MADDE BULUNAMADI -> FALLBACK
    # -----------------------------------------------------

    if not starts:
        return RegulationDocument(
            segments=_build_fallback_segments(lines),
            used_fallback=True,
        )

    # -----------------------------------------------------
    # 3. MADDE SEGMENTLERI
    # -----------------------------------------------------

    segments: list[RegulationSegment] = []

    for position, (
        start_line,
        article_number,
        is_provisional,
        is_additional,
    ) in enumerate(starts):

        end_line = (
            starts[position + 1][0] - 1
            if position + 1 < len(starts)
            else len(lines) - 1
        )

        text = "\n".join(
            lines[start_line:end_line + 1]
        ).strip()

        if len(text) < MIN_SEGMENT_CHARS:
            continue

        label = build_article_label(
            article_number=article_number,
            is_provisional=is_provisional,
            is_additional=is_additional,
        )

        segments.append(
            RegulationSegment(
                segment_id=f"seg-{position + 1}",
                article_label=label,
                article_number=article_number,
                heading=headings_by_line.get(start_line),
                text=text,
                start_line=start_line + 1,
                end_line=end_line + 1,
                is_article=True,
                is_provisional=is_provisional,
            )
        )

    if not segments:
        # Basliklar vardi ama hepsi cok kisaydi.
        return RegulationDocument(
            segments=_build_fallback_segments(lines),
            used_fallback=True,
        )

    return RegulationDocument(
        segments=segments,
        used_fallback=False,
    )


def _build_fallback_segments(
    lines: list[str],
) -> list[RegulationSegment]:
    """
    MADDE basligi olmayan metin icin sinirli boyutta
    paragraf/blok segmentleri uretir.

    Bu segmentler MADDE DEGILDIR:
      article_label  = None
      article_number = None
      is_article     = False

    Ust katman bunlari madde eslesmesi gibi sunmaz.
    """

    segments: list[RegulationSegment] = []

    buffer: list[str] = []
    buffer_length = 0
    buffer_start = 0

    def flush(end_index: int) -> None:
        nonlocal buffer, buffer_length, buffer_start

        text = "\n".join(buffer).strip()

        if len(text) >= MIN_SEGMENT_CHARS:
            segments.append(
                RegulationSegment(
                    segment_id=f"chunk-{len(segments) + 1}",
                    article_label=None,
                    article_number=None,
                    heading=None,
                    text=text,
                    start_line=buffer_start + 1,
                    end_line=end_index + 1,
                    is_article=False,
                )
            )

        buffer = []
        buffer_length = 0

    for index, line in enumerate(lines):

        if not buffer:
            buffer_start = index

        buffer.append(line)
        buffer_length += len(line) + 1

        if buffer_length >= FALLBACK_CHUNK_CHARS:
            flush(index)

        if len(segments) >= MAX_SEGMENTS:
            return segments

    if buffer:
        flush(len(lines) - 1)

    return segments


# ---------------------------------------------------------
# MADDE ATIFI COZUMLEME
# ---------------------------------------------------------

@dataclass(frozen=True)
class ArticleReference:
    article_number: str
    is_provisional: bool = False

    # Atif nasil yazilmis?
    #
    # "inline"  : "12 nci maddesinde yer alan" gibi cekimli
    #             atif. Degisiklik metninin BASKA bir
    #             yonetmeligin maddesine yaptigi atiftir;
    #             en guvenilir sinyaldir.
    #
    # "direct"  : "MADDE 1-" gibi duz yazim.
    #             Satir basinda geciyorsa bu genellikle
    #             DEGISIKLIK METNININ KENDI madde
    #             numaralandirmasidir, hedef yonetmelige
    #             yapilan bir atif degildir.
    kind: str = "inline"

    # Satir basindaki "MADDE 1-" bicimi mi?
    is_own_numbering: bool = False

    def matches(self, segment: RegulationSegment) -> bool:
        """
        Bu atif verilen segmenti isaret ediyor mu?
        """

        if segment.article_number is None:
            return False

        if segment.article_number != self.article_number:
            return False

        # Geçici madde ile normal madde ayni numarayi
        # tasiyabilir; karistirilmaz.
        return segment.is_provisional == self.is_provisional


def extract_article_references(
    text: str,
) -> list[ArticleReference]:
    """
    Degisiklik metnindeki madde atiflarini cikarir.

    Ornekler:
        "5 inci maddesi"        -> 5
        "5'inci maddesi"        -> 5
        "12 nci maddesinin"     -> 12
        "12/A maddesi"          -> 12/A
        "geçici 3 üncü maddesi" -> geçici 3
        "MADDE 12"              -> 12

    Sonuc SIRALIDIR ve tekrar icermez; metinde once gecen
    atif once gelir.
    """

    if not text:
        return []

    found: list[ArticleReference] = []
    seen: set[tuple[str, bool]] = set()

    def add(
        number: str,
        provisional: bool,
        kind: str,
        is_own_numbering: bool = False,
    ) -> None:

        normalized = normalize_article_number(number)

        if not normalized:
            return

        key = (normalized, provisional)

        if key in seen:
            return

        seen.add(key)

        found.append(
            ArticleReference(
                article_number=normalized,
                is_provisional=provisional,
                kind=kind,
                is_own_numbering=is_own_numbering,
            )
        )

    def starts_line(position: int) -> bool:
        """
        Bu eslesme kendi satirinin BASINDA mi?

        "MADDE 1- Aynı Yönetmeliğin ..." satirindaki
        MADDE 1, degisiklik metninin KENDI numarasidir.

        Eslesme konumu degil, eslesmeyi ICEREN SATIR
        incelenir: DIRECT_ARTICLE_PATTERN'deki "\\s*" onceki
        satir sonunu yutabildigi icin match.start() tek
        basina guvenilir degildir.
        """

        line_start = text.rfind("\n", 0, position) + 1

        line_end = text.find("\n", position)

        if line_end == -1:
            line_end = len(text)

        line = text[line_start:line_end]

        # Satir basi "MADDE 1" / "GEÇİCİ MADDE 2" bicimi mi?
        return ARTICLE_HEADING_PATTERN.match(line) is not None

    # "12 nci maddesinin" bicimi.
    # Cekimli atif; hedef yonetmelige yapilan gercek atiftir.
    for match in ARTICLE_REFERENCE_PATTERN.finditer(text):
        add(
            number=match.group("number"),
            provisional=bool(match.group("provisional")),
            kind="inline",
        )

    # "MADDE 12" bicimi.
    for match in DIRECT_ARTICLE_PATTERN.finditer(text):
        prefix = match.group("prefix")

        add(
            number=match.group("number"),
            provisional=_is_provisional_prefix(prefix),
            kind="direct",
            is_own_numbering=starts_line(match.end() - 1),
        )

    return found
