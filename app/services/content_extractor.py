from dataclasses import dataclass

from bs4 import BeautifulSoup


REMOVE_TAGS = {
    "script",
    "style",
    "noscript",
    "svg",
    "iframe",
    "canvas",
}


@dataclass
class ExtractedContent:
    title: str
    text: str
    text_length: int


def clean_text(text: str) -> str:
    """
    Gereksiz boşlukları ve boş satırları temizler.
    """

    lines = []

    for line in text.splitlines():
        cleaned_line = " ".join(line.split())

        if cleaned_line:
            lines.append(cleaned_line)

    return "\n".join(lines)


def extract_content(html: str) -> ExtractedContent:
    """
    HTML içeriğini temizler ve karşılaştırmaya uygun
    sade metin haline getirir.
    """

    if not html:
        raise ValueError("HTML içeriği boş olamaz.")

    soup = BeautifulSoup(html, "html.parser")

    # Sayfa başlığını al
    title = ""

    if soup.title and soup.title.string:
        title = soup.title.string.strip()

    # Head içeriği ana metne dahil edilmesin
    if soup.head:
        soup.head.decompose()

    # Takip açısından anlamsız HTML elementlerini kaldır
    for tag_name in REMOVE_TAGS:
        for tag in soup.find_all(tag_name):
            tag.decompose()

    # HTML -> metin
    raw_text = soup.get_text(separator="\n")

    text = clean_text(raw_text)

    return ExtractedContent(
        title=title,
        text=text,
        text_length=len(text),
    )