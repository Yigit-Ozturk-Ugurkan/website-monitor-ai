"""
Semantik HTML bolum (section) cikarimi.

auto_section ve manual_section kapsamlari AYNI altyapiyi
kullanir; ikinci bir cikarim sistemi yoktur.

1. CANDIDATE DISCOVERY
   Sayfadaki anlamli bolumleri aday olarak cikarir.

   auto_section  : adaylar Gemini'ye sunulur.
   manual_section: adaylar KULLANICIYA sunulur.

2. REFERENCE RESOLUTION
   Daha once secilmis bir bolumu, YENI HTML icinde
   AI cagrisi yapmadan tekrar bulur.

   Iki kapsam da burayi kullanir. Fark yalnizca bolumun
   bulunamamasi durumundadir:

     auto_section  : yeniden kesif yapilir.
     manual_section: kullanicidan yeniden secim istenir.

Iki yol da metni ayni sekilde uretir; boylece discovery
sirasindaki metin ile sonraki run'larda cozumlenen metin
birebir ayni olur ve snapshot karsilastirmasi bozulmaz.
"""

from dataclasses import dataclass

from bs4 import BeautifulSoup
from bs4.element import NavigableString, Tag

from app.services.content_extractor import (
    REMOVE_TAGS,
    clean_text,
)


# ---------------------------------------------------------
# AYARLAR
# ---------------------------------------------------------

# Aday olarak degerlendirilen kapsayici elementler.
CONTAINER_TAGS = (
    "main",
    "article",
    "section",
)

# Baslik bazli bolum uretilen etiketler.
CANDIDATE_HEADING_TAGS = (
    "h1",
    "h2",
    "h3",
)

# Bolum siniri belirlerken dikkate alinan tum basliklar.
HEADING_TAGS = (
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
)

# Takip acisindan gurultu sayilan bolumler.
# content_extractor'daki REMOVE_TAGS'e ek olarak temizlenir.
NOISE_TAGS = (
    "nav",
    "footer",
    "aside",
    "form",
    "template",
)

# Bundan kisa bloklar aday yapilmaz.
MIN_CANDIDATE_TEXT_LENGTH = 80

# Gemini'ye gonderilecek maksimum aday sayisi.
MAX_CANDIDATES = 40

# Etiket uzunlugu siniri.
MAX_LABEL_LENGTH = 120

# Bir basligi saran wrapper elementin tasiyabilecegi
# ek metin miktari. ("[duzenle]" gibi kucuk eklentiler)
WRAPPER_TEXT_TOLERANCE = 60


class ManualSectionResolutionError(Exception):
    """
    Kullanicinin sectigi bolum sayfada bulunamadi.

    manual_section kapsaminda bolumu KULLANICI secmistir.
    Bu yuzden:

      - otomatik olarak baska bir bolum SECILMEZ
      - Gemini bolum secimi CALISTIRILMAZ
      - sessizce tum sayfaya DONULMEZ

    Kullanicidan yeniden secim yapmasi istenir.
    """


# ---------------------------------------------------------
# VERI YAPILARI
# ---------------------------------------------------------

@dataclass
class SectionCandidate:
    candidate_id: str
    label: str
    reference: str
    tag: str
    text: str
    text_length: int


@dataclass
class SectionCandidates:
    page_title: str
    candidates: list[SectionCandidate]


@dataclass
class ResolvedSection:
    reference: str
    label: str
    text: str
    text_length: int


@dataclass
class SectionResolution:
    page_title: str
    section: ResolvedSection | None


# ---------------------------------------------------------
# ORTAK YARDIMCILAR
# ---------------------------------------------------------

def normalize_label(value: str | None) -> str:
    """
    Reference eslesmesinde kullanilan sadelestirilmis bicim.

    Buyuk/kucuk harf ve bosluk farklari yok sayilir.
    """

    if not value:
        return ""

    return " ".join(str(value).split()).strip().lower()


def _read_title(soup: BeautifulSoup) -> str:
    """
    content_extractor ile AYNI title davranisi.
    """

    if soup.title and soup.title.string:
        return soup.title.string.strip()

    return ""


def _prepare_soup(html: str) -> tuple[BeautifulSoup, str]:
    """
    Bolum cikarimi icin temizlenmis soup uretir.

    Title, head silinmeden once okunur.
    """

    if not html:
        raise ValueError("HTML içeriği boş olamaz.")

    soup = BeautifulSoup(html, "html.parser")

    page_title = _read_title(soup)

    if soup.head:
        soup.head.decompose()

    for tag_name in REMOVE_TAGS:
        for tag in soup.find_all(tag_name):
            tag.decompose()

    for tag_name in NOISE_TAGS:
        for tag in soup.find_all(tag_name):
            tag.decompose()

    return soup, page_title


def _heading_level(node) -> int | None:
    """
    Etiket bir baslik ise seviyesini dondurur (h2 -> 2).
    """

    if not isinstance(node, Tag):
        return None

    if node.name not in HEADING_TAGS:
        return None

    return int(node.name[1])


def _shorten_label(value: str) -> str:

    cleaned = " ".join((value or "").split()).strip()

    if len(cleaned) <= MAX_LABEL_LENGTH:
        return cleaned

    return cleaned[:MAX_LABEL_LENGTH].rstrip()


def _first_heading(element: Tag) -> Tag | None:

    if element.name in HEADING_TAGS:
        return element

    return element.find(HEADING_TAGS)


def _element_text(element: Tag) -> str:

    return clean_text(
        element.get_text(separator="\n")
    )


# ---------------------------------------------------------
# BASLIK BAZLI BOLUM METNI
#
# Bir baslik ile bir SONRAKI ayni/daha ust seviye baslik
# arasindaki icerik tek bir bolum sayilir.
# ---------------------------------------------------------

def _heading_anchor(heading: Tag) -> Tag:
    """
    Baslik yalnizca bir sarmalayici icindeyse kardes
    yurumesi o sarmalayicidan baslar.

    Bazi CMS'ler basligi "<div class=heading><h2>..</h2></div>"
    seklinde sarar; bu durumda h2'nin kardesleri bos olur ve
    bolum bulunamaz.
    """

    node = heading

    heading_length = len(
        heading.get_text(" ", strip=True)
    )

    parent = node.parent

    while (
        isinstance(parent, Tag)
        and parent.name not in ("body", "html", "[document]")
    ):

        if len(parent.find_all(HEADING_TAGS)) != 1:
            break

        parent_length = len(
            parent.get_text(" ", strip=True)
        )

        if parent_length > heading_length + WRAPPER_TEXT_TOLERANCE:
            break

        node = parent
        parent = node.parent

    return node


def _boundary_level(node) -> int | None:
    """
    Kardes dugum yeni bir bolum baslatiyor mu?

    Dugumun kendisi baslik olabilir veya icinde
    baslik tasiyor olabilir.
    """

    level = _heading_level(node)

    if level is not None:
        return level

    if not isinstance(node, Tag):
        return None

    inner = node.find(HEADING_TAGS)

    if inner is None:
        return None

    return _heading_level(inner)


def _heading_section_text(heading: Tag) -> str:
    """
    Baslik ve onu izleyen icerigin temiz metni.
    """

    level = _heading_level(heading)

    anchor = _heading_anchor(heading)

    parts = [
        heading.get_text(separator="\n")
    ]

    for sibling in anchor.next_siblings:

        if isinstance(sibling, NavigableString):
            parts.append(str(sibling))
            continue

        if not isinstance(sibling, Tag):
            continue

        boundary = _boundary_level(sibling)

        if boundary is not None and boundary <= level:
            break

        parts.append(
            sibling.get_text(separator="\n")
        )

    return clean_text(
        "\n".join(parts)
    )


# ---------------------------------------------------------
# REFERENCE URETIMI
#
# Bicimler:
#   id:<element id>
#   container:<tag>|<normalize edilmis baslik>
#   heading:<tag>|<normalize edilmis baslik>
#   tag:<tag>|<kacinci>
#
# Tercih sirasi: stabil id > anlamli baslik > konum.
# ---------------------------------------------------------

def _container_reference(
    element: Tag,
    occurrence_index: int,
) -> str:

    element_id = (element.get("id") or "").strip()

    if element_id:
        return f"id:{element_id}"

    heading = _first_heading(element)

    if heading is not None:

        heading_label = normalize_label(
            heading.get_text(" ", strip=True)
        )

        if heading_label:
            return (
                f"container:{element.name}|{heading_label}"
            )

    return f"tag:{element.name}|{occurrence_index}"


def _heading_reference(heading: Tag) -> str | None:

    heading_id = (heading.get("id") or "").strip()

    if heading_id:
        return f"heading-id:{heading_id}"

    heading_label = normalize_label(
        heading.get_text(" ", strip=True)
    )

    if not heading_label:
        return None

    return f"heading:{heading.name}|{heading_label}"


def _container_label(element: Tag) -> str:

    heading = _first_heading(element)

    if heading is not None:
        label = _shorten_label(
            heading.get_text(" ", strip=True)
        )

        if label:
            return label

    element_id = (element.get("id") or "").strip()

    if element_id:
        return _shorten_label(element_id)

    return f"{element.name} bölümü"


# ---------------------------------------------------------
# CANDIDATE DISCOVERY
# ---------------------------------------------------------

def extract_section_candidates(
    html: str,
) -> SectionCandidates:
    """
    Sayfadaki aday bolumleri cikarir.

    Once semantik kapsayicilar (main / article / section),
    sonra baslik bazli bolumler degerlendirilir.

    Ayni metni ureten adaylar tekrarlanmaz.
    """

    soup, page_title = _prepare_soup(html)

    candidates: list[SectionCandidate] = []

    seen_texts: set[str] = set()
    seen_references: set[str] = set()

    def add_candidate(
        label: str,
        reference: str,
        tag: str,
        text: str,
    ) -> None:

        if len(text) < MIN_CANDIDATE_TEXT_LENGTH:
            return

        if text in seen_texts:
            return

        if reference in seen_references:
            return

        seen_texts.add(text)
        seen_references.add(reference)

        candidates.append(
            SectionCandidate(
                candidate_id=f"c{len(candidates) + 1}",
                label=label or f"{tag} bölümü",
                reference=reference,
                tag=tag,
                text=text,
                text_length=len(text),
            )
        )

    # 1. SEMANTIK KAPSAYICILAR

    occurrence_counters: dict[str, int] = {}

    for element in soup.find_all(CONTAINER_TAGS):

        index = occurrence_counters.get(element.name, 0)
        occurrence_counters[element.name] = index + 1

        reference = _container_reference(
            element=element,
            occurrence_index=index,
        )

        add_candidate(
            label=_container_label(element),
            reference=reference,
            tag=element.name,
            text=_element_text(element),
        )

    # 2. BASLIK BAZLI BOLUMLER

    for heading in soup.find_all(CANDIDATE_HEADING_TAGS):

        reference = _heading_reference(heading)

        if reference is None:
            continue

        label = _shorten_label(
            heading.get_text(" ", strip=True)
        )

        if not label:
            continue

        add_candidate(
            label=label,
            reference=reference,
            tag=heading.name,
            text=_heading_section_text(heading),
        )

    return SectionCandidates(
        page_title=page_title,
        candidates=candidates[:MAX_CANDIDATES],
    )


# ---------------------------------------------------------
# REFERENCE RESOLUTION
# ---------------------------------------------------------

def _resolve_id(
    soup: BeautifulSoup,
    value: str,
) -> ResolvedSection | None:

    element = soup.find(id=value)

    if element is None or not isinstance(element, Tag):
        return None

    if element.name in HEADING_TAGS:
        return None

    return ResolvedSection(
        reference=f"id:{value}",
        label=_container_label(element),
        text=_element_text(element),
        text_length=0,
    )


def _resolve_heading_id(
    soup: BeautifulSoup,
    value: str,
) -> ResolvedSection | None:

    element = soup.find(id=value)

    if not isinstance(element, Tag):
        return None

    if element.name not in HEADING_TAGS:
        return None

    return ResolvedSection(
        reference=f"heading-id:{value}",
        label=_shorten_label(
            element.get_text(" ", strip=True)
        ),
        text=_heading_section_text(element),
        text_length=0,
    )


def _resolve_container(
    soup: BeautifulSoup,
    tag_name: str,
    heading_label: str,
) -> ResolvedSection | None:
    """
    Basligina gore kapsayici bulur.

    Once ayni etiket, bulunamazsa diger semantik
    kapsayicilar denenir. Site "section"i "article"a
    cevirdiginde bolum kaybolmasin.
    """

    ordered_tags = [tag_name] + [
        name
        for name in CONTAINER_TAGS
        if name != tag_name
    ]

    for name in ordered_tags:

        for element in soup.find_all(name):

            heading = _first_heading(element)

            if heading is None:
                continue

            if normalize_label(
                heading.get_text(" ", strip=True)
            ) != heading_label:
                continue

            return ResolvedSection(
                reference=(
                    f"container:{tag_name}|{heading_label}"
                ),
                label=_container_label(element),
                text=_element_text(element),
                text_length=0,
            )

    return None


def _resolve_heading(
    soup: BeautifulSoup,
    tag_name: str,
    heading_label: str,
) -> ResolvedSection | None:
    """
    Baslik metnine gore bolum bulur.

    Ayni metinli baslik tercih edilir; site basligi
    h2'den h3'e cevirse bile bolum kaybolmaz.
    """

    matches = [
        heading
        for heading in soup.find_all(HEADING_TAGS)
        if normalize_label(
            heading.get_text(" ", strip=True)
        )
        == heading_label
    ]

    if not matches:
        return None

    heading = next(
        (
            item
            for item in matches
            if item.name == tag_name
        ),
        matches[0],
    )

    return ResolvedSection(
        reference=f"heading:{tag_name}|{heading_label}",
        label=_shorten_label(
            heading.get_text(" ", strip=True)
        ),
        text=_heading_section_text(heading),
        text_length=0,
    )


def _resolve_tag_index(
    soup: BeautifulSoup,
    tag_name: str,
    raw_index: str,
) -> ResolvedSection | None:

    try:
        index = int(raw_index)

    except (TypeError, ValueError):
        return None

    elements = soup.find_all(tag_name)

    if index < 0 or index >= len(elements):
        return None

    element = elements[index]

    return ResolvedSection(
        reference=f"tag:{tag_name}|{index}",
        label=_container_label(element),
        text=_element_text(element),
        text_length=0,
    )


def resolve_section(
    html: str,
    reference: str | None,
) -> SectionResolution:
    """
    Kaydedilmis reference ile bolumu yeniden bulur.

    Bulunamazsa section=None doner. Bu bir hata degildir;
    cagiran taraf yeniden discovery baslatir.
    """

    soup, page_title = _prepare_soup(html)

    reference = (reference or "").strip()

    if not reference or ":" not in reference:
        return SectionResolution(
            page_title=page_title,
            section=None,
        )

    scheme, _, payload = reference.partition(":")

    section: ResolvedSection | None = None

    if scheme == "id":
        section = _resolve_id(soup, payload)

    elif scheme == "heading-id":
        section = _resolve_heading_id(soup, payload)

    elif scheme in ("container", "heading", "tag"):

        tag_name, separator, value = payload.partition("|")

        if separator:

            if scheme == "container":
                section = _resolve_container(
                    soup=soup,
                    tag_name=tag_name,
                    heading_label=value,
                )

            elif scheme == "heading":
                section = _resolve_heading(
                    soup=soup,
                    tag_name=tag_name,
                    heading_label=value,
                )

            else:
                section = _resolve_tag_index(
                    soup=soup,
                    tag_name=tag_name,
                    raw_index=value,
                )

    if section is not None:

        # Bolum bulundu ama icerigi anlamsiz kadar kucuk ise
        # kaybolmus sayilir ve yeniden discovery yapilir.
        if len(section.text) < MIN_CANDIDATE_TEXT_LENGTH:
            section = None

        else:
            section.text_length = len(section.text)

            # Kaydedilmis reference degistirilmez.
            section.reference = reference

    return SectionResolution(
        page_title=page_title,
        section=section,
    )
