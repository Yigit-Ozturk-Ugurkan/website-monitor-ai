"""
YEREL ADAY MADDE SECIMI

Gemini'ye TUM REFERANS YONETMELIK VERILMEZ. Once burada
deterministik olarak aday segmentler secilir; modele
yalnizca bu adaylar gonderilir.

Bu modul:

  - Gemini CAGIRMAZ
  - ag istegi yapmaz
  - veritabanina dokunmaz

Secim sirasi:

  1. Degisiklik metnindeki ACIK madde atiflari
     ("12 nci maddesinin") ile ayni numaraya sahip
     segmentler
  2. Baslik / lexical ortusme
  3. Kontrollu metin benzerligi (difflib)

Sonuc en fazla MAX_CANDIDATES adaydir.
"""

import re

from dataclasses import dataclass
from difflib import SequenceMatcher

from app.services.regulation_text_parser import (
    ArticleReference,
    RegulationSegment,
    extract_article_references,
    turkish_lower,
)


# Modele gonderilecek en fazla aday sayisi.
MAX_CANDIDATES = 8


# Benzerlik hesabinda kullanilan kaynak metin siniri.
# Cok uzun metinlerde difflib gereksiz yavaslar.
MAX_SIMILARITY_CHARS = 4000


# Benzerlik fallback'inin altina dusen adaylar alinmaz.
# Amac "hicbir ilgisi yok" segmentlerin listeye
# doldurulmasini engellemektir.
MIN_SIMILARITY_SCORE = 0.05


# Cok yaygin olduklari icin ayirt edici olmayan kelimeler.
STOP_WORDS = {
    "ve", "veya", "ile", "bu", "bir", "olan", "olarak",
    "için", "icin", "gibi", "daha", "kadar", "sonra",
    "madde", "maddesi", "maddesinin", "yönetmelik",
    "yonetmelik", "yönetmeliğin", "yonetmeligin",
    "aynı", "ayni", "şeklinde", "seklinde",
    "değiştirilmiştir", "degistirilmistir",
    "aşağıdaki", "asagidaki", "yer", "alan", "ibaresi",
    "fıkrası", "fikrasi", "bendi", "hükmü", "hukmu",
}


TOKEN_PATTERN = re.compile(
    r"[0-9a-zçğıöşü/]+",
    re.UNICODE,
)


# Aday secim gerekcesi.
REASON_EXPLICIT_REFERENCE = "explicit_reference"
REASON_LEXICAL_OVERLAP = "lexical_overlap"
REASON_SIMILARITY = "similarity"


@dataclass
class RegulationCandidate:
    """
    Modele gonderilecek aday segment.

    segment TAM METNIYLE sunucu tarafinda tutulur; boylece
    old_text / reference_evidence exact dogrulamasi tam
    metin uzerinde yapilabilir. Modele ise kisaltilmis
    onizleme gonderilir.
    """

    segment: RegulationSegment
    score: float
    reason: str

    @property
    def candidate_id(self) -> str:
        return self.segment.segment_id

    @property
    def article_label(self) -> str | None:
        return self.segment.article_label

    @property
    def text(self) -> str:
        return self.segment.text


def tokenize(text: str) -> set[str]:
    """
    Karsilastirma icin anlamli kelime kumesi uretir.
    """

    lowered = turkish_lower(text or "")

    return {
        token
        for token in TOKEN_PATTERN.findall(lowered)
        if len(token) > 2 and token not in STOP_WORDS
    }


def lexical_overlap_score(
    source_tokens: set[str],
    segment: RegulationSegment,
) -> float:
    """
    Kaynak degisiklik metni ile segment arasindaki
    kelime ortusmesi.

    Jaccard yerine "kaynagin ne kadari segmentte gecti"
    olcusu kullanilir; segment cok uzun oldugunda skor
    haksiz yere dusmesin.
    """

    if not source_tokens:
        return 0.0

    segment_tokens = tokenize(
        (segment.heading or "") + "\n" + segment.text
    )

    if not segment_tokens:
        return 0.0

    shared = source_tokens & segment_tokens

    return len(shared) / len(source_tokens)


def similarity_score(
    source_text: str,
    segment: RegulationSegment,
) -> float:
    """
    Kontrollu metin benzerligi.

    Yalnizca lexical ortusme hicbir aday uretemediginde
    kullanilir; sonuclari her zaman en dusuk oncelikte
    siralanir.
    """

    left = turkish_lower(source_text)[:MAX_SIMILARITY_CHARS]
    right = turkish_lower(segment.text)[:MAX_SIMILARITY_CHARS]

    if not left or not right:
        return 0.0

    return SequenceMatcher(None, left, right).ratio()


def select_candidates(
    source_text: str,
    segments: list[RegulationSegment],
    max_candidates: int = MAX_CANDIDATES,
) -> list[RegulationCandidate]:
    """
    Degisiklik metni icin aday referans segmentleri secer.

    Donen liste ONCELIK SIRASINDADIR:

      1. acik madde atifiyla eslesenler
      2. lexical ortusmesi olanlar
      3. benzerlik fallback'i

    Hicbir aday bulunamazsa BOS LISTE doner. Bos liste
    ust katmanda no_match anlamina gelir; uydurma bir
    eslesme URETILMEZ.
    """

    if not segments:
        return []

    references = extract_article_references(source_text)

    selected: list[RegulationCandidate] = []
    used_ids: set[str] = set()

    def add(
        segment: RegulationSegment,
        score: float,
        reason: str,
    ) -> None:

        if segment.segment_id in used_ids:
            return

        if len(selected) >= max_candidates:
            return

        used_ids.add(segment.segment_id)

        selected.append(
            RegulationCandidate(
                segment=segment,
                score=score,
                reason=reason,
            )
        )

    # -----------------------------------------------------
    # 1. ACIK MADDE ATIFLARI
    #
    # En guvenilir sinyal. Metin "12 nci maddesi" diyorsa
    # aday listesinin basinda Madde 12 olmalidir.
    #
    # SIRALAMA ONEMLI:
    #
    # Degisiklik yonetmelikleri kendi maddelerini de
    # "MADDE 1-" diye numaralandirir. Satir basindaki bu
    # numaralandirma HEDEF yonetmelige yapilan bir atif
    # DEGILDIR; bu yuzden cekimli atiflardan ("12 nci
    # maddesinde") sonraya birakilir.
    # -----------------------------------------------------

    ordered_references = sorted(
        references,
        key=lambda reference: (
            reference.is_own_numbering,
            reference.kind != "inline",
        ),
    )

    for reference in ordered_references:

        score = (
            0.6
            if reference.is_own_numbering
            else 1.0
        )

        for segment in segments:
            if reference.matches(segment):
                add(
                    segment=segment,
                    score=score,
                    reason=REASON_EXPLICIT_REFERENCE,
                )

    # -----------------------------------------------------
    # 2. LEXICAL ORTUSME
    # -----------------------------------------------------

    source_tokens = tokenize(source_text)

    if len(selected) < max_candidates:

        scored = [
            (
                lexical_overlap_score(
                    source_tokens=source_tokens,
                    segment=segment,
                ),
                index,
                segment,
            )
            for index, segment in enumerate(segments)
            if segment.segment_id not in used_ids
        ]

        scored.sort(key=lambda row: (-row[0], row[1]))

        for score, _, segment in scored:

            if score <= 0.0:
                break

            add(
                segment=segment,
                score=score,
                reason=REASON_LEXICAL_OVERLAP,
            )

    # -----------------------------------------------------
    # 3. BENZERLIK FALLBACK
    #
    # Yalnizca hala hic aday yoksa calisir. Amac bos bir
    # aday listesiyle her degisikligi no_match yapmamaktir.
    # -----------------------------------------------------

    if not selected:

        scored = [
            (
                similarity_score(
                    source_text=source_text,
                    segment=segment,
                ),
                index,
                segment,
            )
            for index, segment in enumerate(segments)
        ]

        scored.sort(key=lambda row: (-row[0], row[1]))

        for score, _, segment in scored:

            if score < MIN_SIMILARITY_SCORE:
                break

            add(
                segment=segment,
                score=score,
                reason=REASON_SIMILARITY,
            )

    return selected


def has_explicit_reference_match(
    candidates: list[RegulationCandidate],
) -> bool:
    """
    Adaylardan en az biri ACIK madde atifiyla mi secildi?

    Bu bilgi guven degerlendirmesinde kullanilir; acik
    atif varsa eslesme cok daha saglamdir.
    """

    return any(
        candidate.reason == REASON_EXPLICIT_REFERENCE
        for candidate in candidates
    )


def find_referenced_articles(
    source_text: str,
) -> list[ArticleReference]:
    """
    Disaridan kullanilabilen ince sarmalayici.

    Test ve loglama tek bir yerden okusun diye burada.
    """

    return extract_article_references(source_text)
