"""
MEVZUAT KARSILASTIRMA MOTORU

Kaynak degisiklik metnini referans yonetmeligin SECILMIS
adaylariyla karsilastirir ve madde bazli degisiklik
maddeleri uretir.

MODEL KULLANIMI

Mevcut ai_analyzer altyapisi AYNEN yeniden kullanilir:
run_with_quota_fallback + build_generation_config. Yeni bir
Gemini istemci sistemi kurulmaz.

Bir karsilastirma icin EN FAZLA bir mantiksal cagri yapilir
(primary + yalnizca kota durumunda tek fallback).

GUVEN KURALI

AI hukuki metin UYDURAMAZ. Modelin dondurdugu her
old_text / new_text / source_evidence / reference_evidence
degeri SUNUCU TARAFINDA ilgili kaynak icinde BIREBIR
aranir. Bulunamayan deger kabul EDILMEZ.

Normalizasyon YALNIZCA kontrollu bosluk normalizasyonudur.
Anlam benzerligi exact evidence yerine GECMEZ.

change_summary ve overall_summary birer OZETTIR; kanit
degildir ve exact dogrulamaya tabi tutulmaz. Kanit alanlari
ozetlerden ayri tutulur.
"""

import logging
import re

from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from app.services.ai_analyzer import (
    build_generation_config,
    run_with_quota_fallback,
)
from app.services.regulation_candidate_matcher import (
    RegulationCandidate,
)


logger = logging.getLogger(__name__)


class RegulationComparisonError(Exception):
    """
    Karsilastirma tamamlanamadi.

    Bu hata pipeline'i DUSURMEZ; ust katman comparison
    kaydini status="failed" ile yazar.
    """


# ---------------------------------------------------------
# DEGISIKLIK TURLERI
# ---------------------------------------------------------

CHANGE_TYPE_AMENDED = "amended"
CHANGE_TYPE_ADDED = "added"
CHANGE_TYPE_REPEALED = "repealed"
CHANGE_TYPE_RENAMED = "renamed"
CHANGE_TYPE_UNKNOWN = "unknown"

CHANGE_TYPES = (
    CHANGE_TYPE_AMENDED,
    CHANGE_TYPE_ADDED,
    CHANGE_TYPE_REPEALED,
    CHANGE_TYPE_RENAMED,
    CHANGE_TYPE_UNKNOWN,
)


# Modele gonderilen aday metni siniri.
# Tam segment sunucuda tutulur; dogrulama tam metne gore
# yapilir.
MAX_CANDIDATE_PROMPT_CHARS = 2500

# Modele gonderilen kaynak degisiklik metni siniri.
MAX_SOURCE_PROMPT_CHARS = 12000

# Tek bir item icin kabul edilen kanit uzunlugu.
# Bundan uzun "kanit" pratikte tum maddenin kopyasidir.
MAX_EVIDENCE_CHARS = 2000


class RegulationComparisonItemResult(BaseModel):
    candidate_id: str = Field(
        description=(
            "Bu degisikligin ilgili oldugu referans "
            "segmentin candidate_id degeri. YALNIZCA "
            "verilen listeden bir deger olabilir."
        )
    )

    affected_article: str | None = Field(
        default=None,
        description=(
            "Degisen madde etiketi. Ornek: 'Madde 12'. "
            "Emin degilsen null."
        ),
    )

    affected_paragraph: str | None = Field(
        default=None,
        description=(
            "Degisen fikra/bent. Ornek: '3. fıkra'. "
            "Emin degilsen null."
        ),
    )

    change_type: Literal[
        "amended",
        "added",
        "repealed",
        "renamed",
        "unknown",
    ] = Field(
        description=(
            "Degisiklik turu. "
            "'... seklinde degistirilmistir' -> amended, "
            "'... eklenmistir' -> added, "
            "'... yururlukten kaldirilmistir' -> repealed, "
            "baslik/ad degisikligi -> renamed."
        )
    )

    old_text: str | None = Field(
        default=None,
        description=(
            "ESKI ifade. REFERANS YONETMELIK metninden "
            "BIREBIR kopyalanmalidir. Yeniden yazma, "
            "ozetleme veya duzeltme YAPMA. Referansta "
            "birebir bulamiyorsan null."
        ),
    )

    new_text: str | None = Field(
        default=None,
        description=(
            "YENI ifade. KAYNAK DEGISIKLIK metninden "
            "BIREBIR kopyalanmalidir. Kaynakta birebir "
            "bulamiyorsan null."
        ),
    )

    source_evidence: str = Field(
        description=(
            "Bu maddenin degistigini gosteren KAYNAK "
            "DEGISIKLIK metninden BIREBIR alinti. "
            "Zorunludur ve uydurulamaz."
        )
    )

    reference_evidence: str | None = Field(
        default=None,
        description=(
            "REFERANS YONETMELIKTEN BIREBIR alinti. "
            "Ilgili maddenin mevcut halini gosterir. "
            "Birebir alinti veremiyorsan null."
        ),
    )

    change_summary: str = Field(
        description=(
            "Degisikligin kisa Turkce ozeti. Bu bir "
            "OZETTIR, kanit degildir."
        )
    )

    confidence: float = Field(
        description="0.0 - 1.0 arasi guven skoru."
    )


class RegulationComparisonResult(BaseModel):
    has_regulatory_change: bool = Field(
        description=(
            "Kaynak metin referans yonetmelikte gercek bir "
            "degisiklik yapiyor mu?"
        )
    )

    overall_summary: str = Field(
        description=(
            "Tum degisikligin kisa Turkce ozeti."
        )
    )

    importance: Literal[
        "low",
        "medium",
        "high",
    ] = Field(
        description="Degisikligin onem seviyesi."
    )

    items: list[RegulationComparisonItemResult] = Field(
        default_factory=list,
        description=(
            "Degisen her madde icin bir kayit. Bir "
            "degisiklik birden fazla maddeyi etkileyebilir."
        ),
    )


@dataclass
class ValidatedItem:
    """
    Sunucu dogrulamasindan GECMIS item.

    Buradaki her metin alani ilgili kaynakta birebir
    bulunmustur veya None'dir.
    """

    candidate: RegulationCandidate

    affected_article: str | None
    affected_paragraph: str | None

    change_type: str

    old_text: str | None
    new_text: str | None

    source_evidence: str
    reference_evidence: str | None

    change_summary: str
    confidence: float


@dataclass
class RegulationComparisonOutcome:
    items: list[ValidatedItem] = field(default_factory=list)

    overall_summary: str = ""
    importance: str = "low"

    model_name: str = ""
    used_fallback: bool = False

    # Model bir sonuc uretti ama hicbiri dogrulanamadi.
    # Ust katman bunu no_match olarak degerlendirir.
    rejected_item_count: int = 0

    has_regulatory_change: bool = False


# ---------------------------------------------------------
# KONTROLLU BOSLUK NORMALIZASYONU
#
# Yalnizca sunlar yapilir:
#   - tirnak/tire varyantlari tek bicime indirilir
#   - ardisik bosluk/yeni satir tek bosluga indirilir
#   - bas/son bosluk kirpilir
#
# Kelime silme, ek atma, harf duzeltme YAPILMAZ.
# Anlam benzerligi ARANMAZ.
# ---------------------------------------------------------

QUOTE_VARIANTS = {
    "“": '"',
    "”": '"',
    "„": '"',
    "«": '"',
    "»": '"',
    "‘": "'",
    "’": "'",
    "ʼ": "'",
}

DASH_VARIANTS = {
    "‐": "-",
    "‑": "-",
    "‒": "-",
    "–": "-",
    "—": "-",
    "―": "-",
}

WHITESPACE_PATTERN = re.compile(r"\s+", re.UNICODE)


def normalize_for_match(value: str) -> str:
    """
    Exact karsilastirma icin kontrollu normalizasyon.

    Bu fonksiyon metnin ANLAMINI degistirmez; yalnizca
    kopyala-yapistir sirasinda bozulan bosluk ve tipografik
    tirnak farklarini esitler.
    """

    if not value:
        return ""

    text = value

    for source, target in QUOTE_VARIANTS.items():
        text = text.replace(source, target)

    for source, target in DASH_VARIANTS.items():
        text = text.replace(source, target)

    # Bolunemez bosluk da normal bosluk sayilir.
    text = text.replace(" ", " ")

    return WHITESPACE_PATTERN.sub(" ", text).strip()


def contains_exact(haystack: str, needle: str | None) -> bool:
    """
    needle, haystack icinde BIREBIR geciyor mu?

    Karsilastirma kontrollu normalizasyon sonrasi yapilir.
    Bos veya asiri uzun degerler kabul edilmez.
    """

    if not needle:
        return False

    normalized_needle = normalize_for_match(needle)

    if not normalized_needle:
        return False

    if len(normalized_needle) > MAX_EVIDENCE_CHARS:
        return False

    normalized_haystack = normalize_for_match(haystack)

    if not normalized_haystack:
        return False

    return normalized_needle in normalized_haystack


def _validated_or_none(
    haystack: str,
    value: str | None,
) -> str | None:
    """
    Kaynakta birebir bulunuyorsa degeri, aksi halde None
    dondurur.

    Uydurulmus metin SAKLANMAZ.
    """

    if contains_exact(haystack, value):
        return value.strip()

    return None


def is_item_acceptable(
    change_type: str,
    old_text: str | None,
    new_text: str | None,
) -> bool:
    """
    Degisiklik turu ile kanit tutarli mi?

    added    : new_text gerekli, old_text bos olabilir
    repealed : old_text gerekli, new_text bos olabilir
    amended  : ikisinden EN AZ BIRI dogrulanmis olmali

    Yeterli kanit yoksa item DUSURULUR; sahte metin
    uretilmez.
    """

    if change_type == CHANGE_TYPE_ADDED:
        return bool(new_text)

    if change_type == CHANGE_TYPE_REPEALED:
        return bool(old_text)

    if change_type == CHANGE_TYPE_AMENDED:
        # Ideali ikisi birden olmasidir; ancak resmi
        # metin her zaman eski ifadeyi tekrar etmez.
        # En az bir dogrulanmis taraf sart.
        return bool(old_text or new_text)

    # renamed / unknown icin source_evidence zaten
    # dogrulanmis durumda; ek metin sarti aranmaz.
    return True


def validate_items(
    items: list[RegulationComparisonItemResult],
    candidates_by_id: dict[str, RegulationCandidate],
    source_text: str,
) -> tuple[list[ValidatedItem], int]:
    """
    Modelin urettigi item'lari SUNUCU TARAFINDA dogrular.

    Reddedilme sebepleri:
      - candidate_id uydurma
      - source_evidence kaynakta birebir yok
      - degisiklik turu icin yeterli dogrulanmis metin yok

    Doner: (kabul edilen item'lar, reddedilen sayisi)
    """

    accepted: list[ValidatedItem] = []
    rejected = 0

    for item in items:

        candidate_id = (item.candidate_id or "").strip()

        candidate = candidates_by_id.get(candidate_id)

        if candidate is None:
            # Model aday listesinde olmayan bir id uydurdu.
            logger.warning(
                "Karsilastirma: uydurma candidate_id "
                "reddedildi. candidate_id=%r",
                candidate_id,
            )
            rejected += 1
            continue

        reference_text = candidate.text

        # -------------------------------------------------
        # SOURCE EVIDENCE - ZORUNLU
        #
        # Kaynak degisiklik metninde birebir bulunmalidir.
        # Bulunamazsa item TAMAMEN reddedilir.
        # -------------------------------------------------

        source_evidence = _validated_or_none(
            haystack=source_text,
            value=item.source_evidence,
        )

        if source_evidence is None:
            logger.warning(
                "Karsilastirma: source_evidence kaynakta "
                "birebir bulunamadi, item reddedildi."
            )
            rejected += 1
            continue

        # -------------------------------------------------
        # NEW TEXT -> kaynak degisiklik metninden
        # OLD TEXT -> referans yonetmelikten
        # -------------------------------------------------

        new_text = _validated_or_none(
            haystack=source_text,
            value=item.new_text,
        )

        old_text = _validated_or_none(
            haystack=reference_text,
            value=item.old_text,
        )

        reference_evidence = _validated_or_none(
            haystack=reference_text,
            value=item.reference_evidence,
        )

        change_type = item.change_type

        if change_type not in CHANGE_TYPES:
            change_type = CHANGE_TYPE_UNKNOWN

        if not is_item_acceptable(
            change_type=change_type,
            old_text=old_text,
            new_text=new_text,
        ):
            logger.warning(
                "Karsilastirma: %s turu icin yeterli "
                "dogrulanmis metin yok, item reddedildi.",
                change_type,
            )
            rejected += 1
            continue

        # -------------------------------------------------
        # MADDE ETIKETI
        #
        # Fallback segmentasyonda (is_article=False) madde
        # bilinmez. Bu durumda modelin urettigi etiket
        # KULLANILMAZ; sonuc madde eslesmesi gibi
        # sunulmaz.
        # -------------------------------------------------

        if candidate.segment.is_article:
            affected_article = (
                candidate.segment.article_label
            )
        else:
            affected_article = None

        accepted.append(
            ValidatedItem(
                candidate=candidate,
                affected_article=affected_article,
                affected_paragraph=(
                    (item.affected_paragraph or "").strip()
                    or None
                ),
                change_type=change_type,
                old_text=old_text,
                new_text=new_text,
                source_evidence=source_evidence,
                reference_evidence=reference_evidence,
                change_summary=(
                    item.change_summary or ""
                ).strip(),
                confidence=float(item.confidence or 0.0),
            )
        )

    return accepted, rejected


# ---------------------------------------------------------
# PROMPT
# ---------------------------------------------------------

def _preview(text: str, limit: int) -> str:

    if len(text) <= limit:
        return text

    return text[:limit] + " ..."


def build_comparison_prompt(
    topic: str,
    regulation_title: str,
    source_text: str,
    candidates: list[RegulationCandidate],
) -> str:
    """
    Karsilastirma promptunu uretir.

    Referans yonetmeligin TAMAMI GONDERILMEZ; yalnizca
    yerel olarak secilmis adaylar gonderilir.
    """

    blocks = []

    for candidate in candidates:
        segment = candidate.segment

        blocks.append(
            "\n".join(
                [
                    f"candidate_id: {candidate.candidate_id}",
                    f"madde: {segment.article_label or 'bilinmiyor'}",
                    f"bölüm: {segment.heading or '-'}",
                    "metin:",
                    _preview(
                        segment.text,
                        MAX_CANDIDATE_PROMPT_CHARS,
                    ),
                ]
            )
        )

    candidate_text = "\n\n---\n\n".join(blocks)

    allowed_ids = ", ".join(
        candidate.candidate_id
        for candidate in candidates
    )

    trimmed_source = _preview(
        source_text,
        MAX_SOURCE_PROMPT_CHARS,
    )

    return f"""
Sen bir mevzuat değişikliği karşılaştırma motorusun.

Kullanıcının takip ettiği konu:

{topic}

Referans yönetmelik: {regulation_title}

Aşağıda İKİ metin var.

=== 1. KAYNAK DEĞİŞİKLİK METNİ ===
Bu, izlenen sayfada YENİ ORTAYA ÇIKAN metindir.

{trimmed_source}

=== 2. REFERANS YÖNETMELİKTEN ADAY MADDELER ===
Bunlar referans yönetmeliğin ilgili olabilecek maddeleridir.

{candidate_text}

GÖREVİN:

1. Kaynak değişiklik metninin referans yönetmelikte hangi
   maddeleri değiştirdiğini belirle.

2. Değişen HER MADDE için ayrı bir item üret. Bir değişiklik
   birden fazla maddeyi etkileyebilir.

3. candidate_id alanı YALNIZCA şu değerlerden biri olabilir:

{allowed_ids}

   Bu listede olmayan bir candidate_id UYDURMA.

4. EN ÖNEMLİ KURAL — METİN UYDURMA:

   - old_text: REFERANS YÖNETMELİK metninden BİREBİR kopyala.
     Referansta birebir yoksa null yaz.

   - new_text: KAYNAK DEĞİŞİKLİK metninden BİREBİR kopyala.
     Kaynakta birebir yoksa null yaz.

   - source_evidence: KAYNAK DEĞİŞİKLİK metninden BİREBİR
     kopyalanmış, değişikliği gösteren cümle. Zorunludur.

   - reference_evidence: REFERANS YÖNETMELİKTEN BİREBİR
     kopyalanmış alıntı. Veremiyorsan null yaz.

   Bu alanları yeniden yazma, düzeltme, özetleme veya
   çevirme. Kopyaladığın metin kaynağında harfi harfine
   bulunmalıdır. Sunucu bunu doğrulayacak ve uydurulmuş
   metni REDDEDECEK.

5. change_type değerini metne göre seç:
   "... şeklinde değiştirilmiştir"     -> amended
   "... eklenmiştir"                   -> added
   "... yürürlükten kaldırılmıştır"    -> repealed
   başlık/ad değişikliği               -> renamed
   emin değilsen                       -> unknown

6. change_summary ve overall_summary Türkçe ve kısa olsun.
   Bunlar ÖZETTİR, kanıt değildir; buralarda serbest
   yazabilirsin ama hukuki tavsiye verme.

7. Gerçek bir mevzuat değişikliği yoksa
   has_regulatory_change=false yaz ve items listesini boş
   bırak. Zorlama eşleşme üretme.

8. confidence alanına 0.0 - 1.0 arasında güven skoru ver.

Çıktı şemaya uygun olmalıdır.
""".strip()


# ---------------------------------------------------------
# KARSILASTIRMA
# ---------------------------------------------------------

async def compare_regulation_change(
    topic: str,
    regulation_title: str,
    source_text: str,
    candidates: list[RegulationCandidate],
) -> RegulationComparisonOutcome:
    """
    Kaynak degisikligi aday maddelerle karsilastirir.

    Bir karsilastirma icin EN FAZLA iki model cagrisi
    yapilir (primary + yalnizca kota fallback). Retry
    dongusu yoktur.

    Modelin dondurdugu her kanit SUNUCU TARAFINDA birebir
    dogrulanir.
    """

    if not candidates:
        raise ValueError(
            "Karsilastirma icin aday madde bulunamadi."
        )

    if not (source_text or "").strip():
        raise ValueError(
            "Karsilastirilacak kaynak metin bos olamaz."
        )

    prompt = build_comparison_prompt(
        topic=topic,
        regulation_title=regulation_title,
        source_text=source_text,
        candidates=candidates,
    )

    candidates_by_id = {
        candidate.candidate_id: candidate
        for candidate in candidates
    }

    async def generate(client, model_name):

        response = await client.aio.models.generate_content(
            model=model_name,
            contents=prompt,
            config=build_generation_config(
                model_name,
                response_schema=RegulationComparisonResult,
            ),
        )

        if not response.text:
            raise RegulationComparisonError(
                "Gemini karşılaştırma için boş cevap döndürdü."
            )

        try:
            return RegulationComparisonResult.model_validate_json(
                response.text
            )

        except ValidationError as exc:
            raise RegulationComparisonError(
                "Gemini karşılaştırma cevabı beklenen "
                "şemaya uygun değil."
            ) from exc

    outcome = await run_with_quota_fallback(
        generate=generate,
        error_class=RegulationComparisonError,
        error_prefix="Gemini karşılaştırma hatası",
    )

    result: RegulationComparisonResult = outcome.result

    accepted, rejected = validate_items(
        items=result.items,
        candidates_by_id=candidates_by_id,
        source_text=source_text,
    )

    logger.info(
        "Mevzuat karsilastirmasi. model=%s kabul=%s red=%s",
        outcome.model_name,
        len(accepted),
        rejected,
    )

    return RegulationComparisonOutcome(
        items=accepted,
        overall_summary=(result.overall_summary or "").strip(),
        importance=result.importance,
        model_name=outcome.model_name,
        used_fallback=outcome.used_fallback,
        rejected_item_count=rejected,
        has_regulatory_change=result.has_regulatory_change,
    )
