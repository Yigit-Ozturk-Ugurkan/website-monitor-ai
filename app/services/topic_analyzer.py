from dataclasses import dataclass
import re


TURKISH_STOP_WORDS = {
    "ve",
    "veya",
    "ile",
    "için",
    "bir",
    "bu",
    "şu",
    "olan",
    "olarak",
    "ilgili",
    "hakkında",
    "gibi",
    "da",
    "de",
}


@dataclass
class TopicAnalysisResult:
    relevant: bool
    relevance_score: float
    matched_keywords: list[str]
    topic_keywords: list[str]


def _normalize_text(text: str) -> str:
    """
    Karşılaştırma için metni normalize eder.
    """

    # TURKCE "İ" SORUNU
    #
    # Python'un lower() metodu "İ" (U+0130) harfini
    # "i" + U+0307 (COMBINING DOT ABOVE) olarak cozer.
    # Birlesik nokta \w sinifina GIRMEZ, bu yuzden
    # asagidaki regex kelimeyi ortasindan parcalar:
    #
    #   "DEĞİŞİKLİĞİ" -> "deği" "şi" "kli" "ği"
    #
    # Parcalar 3 harf esiginin altinda kalip elendigi icin
    # anahtar kelime tamamen KAYBOLUR. Resmî Gazete
    # basliklari BUYUK HARF yazildigi icin bu durum
    # mevzuat takibinde surekli yasanir.
    #
    # "I" harfi BILEREK cevrilmez: Turkce dogru karsiligi
    # "ı" olurdu ama takip konulari Turkce ve Ingilizce
    # karisiktir ("API", "AI"). "API" -> "apı" cevrimi
    # Ingilizce kaynak metinle eslesmeyi bozar.
    text = text.replace("İ", "i")

    text = text.lower()

    # lower() sonrasi kalan birlesik noktalari temizle.
    text = text.replace("̇", "")

    text = re.sub(
        r"[^\wçğıöşü]+",
        " ",
        text,
        flags=re.UNICODE,
    )

    return " ".join(
        text.split()
    )


def _extract_keywords(topic: str) -> list[str]:
    """
    Takip edilen konudan temel anahtar kelimeleri çıkarır.
    """

    normalized_topic = _normalize_text(topic)

    words = normalized_topic.split()

    keywords = []

    for word in words:

        if word in TURKISH_STOP_WORDS:
            continue

        if len(word) < 3:
            continue

        if word not in keywords:
            keywords.append(word)

    return keywords


def analyze_topic_relevance(
    topic: str,
    added_lines: list[str],
    removed_lines: list[str] | None = None,
) -> TopicAnalysisResult:
    """
    Değişikliklerin takip edilen konuyla ilişkisini
    temel anahtar kelime eşleşmesi ile analiz eder.
    """

    if not topic.strip():
        raise ValueError(
            "Takip edilen konu boş olamaz."
        )

    removed_lines = removed_lines or []

    topic_keywords = _extract_keywords(
        topic
    )

    if not topic_keywords:
        return TopicAnalysisResult(
            relevant=False,
            relevance_score=0.0,
            matched_keywords=[],
            topic_keywords=[],
        )

    change_text = "\n".join(
        added_lines + removed_lines
    )

    normalized_change = _normalize_text(
        change_text
    )

    matched_keywords = []

    for keyword in topic_keywords:

        if keyword in normalized_change:
            matched_keywords.append(keyword)

    relevance_score = (
        len(matched_keywords)
        / len(topic_keywords)
    )

    return TopicAnalysisResult(
        relevant=len(matched_keywords) > 0,
        relevance_score=round(
            relevance_score,
            4,
        ),
        matched_keywords=matched_keywords,
        topic_keywords=topic_keywords,
    )