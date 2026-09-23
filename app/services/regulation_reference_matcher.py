"""
OTOMATIK REFERANS YONETMELIK ESLESTIRME

Bir Resmî Gazete degisikliginin HANGI kayitli
RegulationReference ile karsilastirilacagini belirler.

AKIS

    degisiklik metni
      -> base mevzuat adi        (regulation_title_parser)
      -> kayitli referanslarla   YEREL skorlama
      -> tek net kazanan?        -> matched
      -> birden fazla yakin?     -> Gemini (YALNIZ burada)
      -> hicbiri yeterli degil?  -> no_match

KURALLAR

  1. MANUEL SECIM DAIMA USTUNDUR.
     Kullanicinin monitore bagladigi referans otomatik
     eslestirmeyle DEGISTIRILMEZ. Otomatik sonuc yalnizca
     "ayni mi, farkli mi" sorusunu cevaplar; farkliysa
     needs_review uretilir ve kullanici karar verir.

  2. KONSOLIDE METIN TERCIH EDILIR.
     Iki referans ayni mevzuati gosteriyorsa konsolide
     olan secilir; boylece karsilastirma aradaki
     degisiklikleri gormus bir metne dayanir.

  3. GEMINI YALNIZCA BELIRSIZLIKTE.
     Net kazanan varsa AI CAGRILMAZ. Cagrildiginda en
     fazla bir mantiksal cagri yapilir ve modelin
     dondurdugu id SUNUCU TARAFINDA dogrulanir.

  4. EMIN DEGILSEK SECMEYIZ.
     Dusuk skor, esitlik veya cozumlenemeyen ad
     durumunda otomatik secim YAPILMAZ.
"""

import logging

from dataclasses import dataclass, field

from pydantic import BaseModel, Field, ValidationError

from app.models.regulation_reference import RegulationReference
from app.services.ai_analyzer import (
    build_generation_config,
    run_with_quota_fallback,
)
from app.services.regulation_title_parser import (
    BaseRegulationName,
    extract_base_regulation_names,
    name_tokens,
    normalize_regulation_name,
)


logger = logging.getLogger(__name__)


class ReferenceMatchError(Exception):
    """
    Eslestirme sirasinda AI hatasi.

    Pipeline'i DUSURMEZ; ust katman no_match/needs_review
    olarak devam eder.
    """


# ---------------------------------------------------------
# SONUC DURUMLARI
# ---------------------------------------------------------

# Tek ve yeterince guclu bir eslesme bulundu.
MATCH_STATUS_MATCHED = "matched"

# Aday var ama hicbiri guvenilir degil.
MATCH_STATUS_NO_MATCH = "no_match"

# Birden fazla aday birbirine cok yakin; ayirt edilemedi.
MATCH_STATUS_AMBIGUOUS = "ambiguous"

# Otomatik sonuc, kullanicinin manuel secimiyle CELISIYOR.
MATCH_STATUS_NEEDS_REVIEW = "needs_review"

# Degisiklik metninden base mevzuat adi cikarilamadi.
MATCH_STATUS_NO_BASE_NAME = "no_base_name"

# Sistemde hic kayitli referans yok.
MATCH_STATUS_NO_REFERENCES = "no_references"


MATCH_STATUSES = (
    MATCH_STATUS_MATCHED,
    MATCH_STATUS_NO_MATCH,
    MATCH_STATUS_AMBIGUOUS,
    MATCH_STATUS_NEEDS_REVIEW,
    MATCH_STATUS_NO_BASE_NAME,
    MATCH_STATUS_NO_REFERENCES,
)


# ---------------------------------------------------------
# ESIKLER
#
# Skor: base adin kelimelerinin referans adinda ne kadar
# karsilandigi (0.0 - 1.0).
# ---------------------------------------------------------

# Bu skorun altindaki hicbir aday kabul edilmez.
MIN_ACCEPTABLE_SCORE = 0.60

# Kazananin ikinciden en az bu kadar onde olmasi gerekir.
# Aksi halde ayirt edilemedi sayilir.
DECISIVE_MARGIN = 0.15

# Modele gonderilecek en fazla aday.
MAX_AI_CANDIDATES = 6


@dataclass
class ScoredReference:
    reference: RegulationReference
    score: float

    # Skorun nasil olustugu (teshis ve test icin).
    matched_tokens: int = 0
    total_tokens: int = 0

    # Yayim tarihi/RG sayisi da tuttu mu?
    publication_match: bool = False

    @property
    def reference_id(self) -> int:
        return self.reference.id

    @property
    def is_consolidated(self) -> bool:
        return bool(self.reference.is_consolidated)


@dataclass
class ReferenceMatchResult:
    """
    Otomatik eslestirmenin sonucu.

    reference None ise otomatik secim YAPILMAMISTIR.
    """

    status: str

    reference: RegulationReference | None = None

    # Cikarilan base mevzuat adi (bulunabildiyse).
    base_name: BaseRegulationName | None = None

    score: float = 0.0

    # Skoru gorulen tum adaylar (yuksekten dusuge).
    candidates: list[ScoredReference] = field(
        default_factory=list
    )

    # Belirsizlik icin Gemini cagrildi mi?
    ai_used: bool = False

    ai_model_name: str | None = None

    # Manuel secim ile otomatik sonuc celisiyorsa,
    # kullanicinin sectigi referans.
    manual_reference: RegulationReference | None = None

    # Insan tarafindan okunabilir kisa aciklama.
    detail: str = ""

    @property
    def reference_id(self) -> int | None:
        if self.reference is None:
            return None

        return self.reference.id

    @property
    def is_confident(self) -> bool:
        return self.status == MATCH_STATUS_MATCHED


# ---------------------------------------------------------
# YEREL SKORLAMA
# ---------------------------------------------------------

def score_name(
    base_name: BaseRegulationName,
    title: str | None,
) -> tuple[float, int, int]:
    """
    Base ad ile HERHANGI bir mevzuat adi arasindaki ortusme.

    Doner: (skor, eslesen kelime, toplam kelime)

    Olcu: base adin ayirt edici kelimelerinin KACI hedef
    adda geciyor.

    Hedef adin fazladan kelime tasimasi cezalandirilmaz;
    kullanicilar referanslari serbest adlandirir
    ("EPDK Lisans Yönetmeliği (güncel)" gibi).

    VERITABANI KAYDI GEREKTIRMEZ.

    Bu yuzden hem kayitli RegulationReference satirlari hem
    de mevzuat.gov.tr arama sonuclari AYNI olcutle
    puanlanabilir; iki yerde iki farkli skorlama mantigi
    olusmaz.
    """

    base_tokens = base_name.tokens

    if not base_tokens:
        return 0.0, 0, 0

    target_tokens = name_tokens(title or "")

    shared = base_tokens & target_tokens

    return (
        len(shared) / len(base_tokens),
        len(shared),
        len(base_tokens),
    )


def score_reference(
    base_name: BaseRegulationName,
    reference: RegulationReference,
) -> ScoredReference:
    """
    Base ad ile KAYITLI referans adi arasindaki ortusme.

    Skorlama mantigi score_name()'dedir; burada yalnizca
    veritabani kaydina baglanir.
    """

    score, matched, total = score_name(
        base_name=base_name,
        title=reference.title,
    )

    return ScoredReference(
        reference=reference,
        score=score,
        matched_tokens=matched,
        total_tokens=total,
    )


def score_all(
    base_name: BaseRegulationName,
    references: list[RegulationReference],
) -> list[ScoredReference]:
    """
    Tum referanslari skorlar ve siralar.

    Siralama:
      1. skor (yuksek once)
      2. KONSOLIDE olan once
      3. id (kararli siralama)
    """

    scored = [
        score_reference(base_name, reference)
        for reference in references
    ]

    scored.sort(
        key=lambda item: (
            -item.score,
            not item.is_consolidated,
            item.reference_id,
        )
    )

    return scored


def _exact_name_match(
    base_name: BaseRegulationName,
    candidate: ScoredReference,
) -> bool:
    """
    Adlar normalize edildiginde birebir ayni mi?
    """

    return (
        normalize_regulation_name(candidate.reference.title)
        == base_name.normalized
    )


def resolve_local(
    base_name: BaseRegulationName,
    references: list[RegulationReference],
) -> tuple[str, list[ScoredReference]]:
    """
    YEREL karar. Gemini CAGRILMAZ.

    Doner: (durum, siralanmis adaylar)

    Durum:
      matched   -> ilk aday hem yeterli hem net onde
      ambiguous -> ilk iki aday ayirt edilemiyor
      no_match  -> hicbir aday esigin uzerinde degil
    """

    scored = score_all(base_name, references)

    if not scored:
        return MATCH_STATUS_NO_MATCH, scored

    best = scored[0]

    if best.score < MIN_ACCEPTABLE_SCORE:
        return MATCH_STATUS_NO_MATCH, scored

    # Esigi gecen tum adaylar.
    qualified = [
        candidate
        for candidate in scored
        if candidate.score >= MIN_ACCEPTABLE_SCORE
    ]

    if len(qualified) == 1:
        return MATCH_STATUS_MATCHED, scored

    runner_up = qualified[1]

    # Net fark varsa karar yerelde verilir.
    if best.score - runner_up.score >= DECISIVE_MARGIN:
        return MATCH_STATUS_MATCHED, scored

    # ---------------------------------------------------
    # ESIT SKORLU ADAYLAR
    #
    # Ayni mevzuatin iki kaydi olabilir: biri Resmî Gazete
    # arsivinden (tarihsel), digeri mevzuat.gov.tr'den
    # (konsolide). Bu durumda karar BELIRSIZ DEGILDIR:
    # konsolide olan tercih edilir.
    # ---------------------------------------------------

    tied = [
        candidate
        for candidate in qualified
        if abs(candidate.score - best.score) < 1e-9
    ]

    consolidated = [
        candidate
        for candidate in tied
        if candidate.is_consolidated
    ]

    if len(consolidated) == 1:
        # score_all zaten konsolide olani one aldi.
        return MATCH_STATUS_MATCHED, scored

    # Adlar birebir ayni ve tek bir aday oyleyse net kabul.
    exact = [
        candidate
        for candidate in qualified
        if _exact_name_match(base_name, candidate)
    ]

    if len(exact) == 1 and exact[0].reference_id == best.reference_id:
        return MATCH_STATUS_MATCHED, scored

    return MATCH_STATUS_AMBIGUOUS, scored


# ---------------------------------------------------------
# BELIRSIZLIK ICIN GEMINI
#
# Yalnizca yerel karar ayirt edemediginde calisir.
# Mevcut ai_analyzer altyapisi yeniden kullanilir; yeni bir
# istemci sistemi kurulmaz.
# ---------------------------------------------------------

class ReferenceSelection(BaseModel):
    reference_id: int = Field(
        description=(
            "Degisikligin hedefledigi mevzuata karsilik "
            "gelen referansin id degeri. YALNIZCA verilen "
            "listeden bir id olabilir."
        )
    )

    confident: bool = Field(
        description=(
            "Secimden emin misin? Emin degilsen false yaz; "
            "yanlis eslesme yerine secim yapilmamasi "
            "tercih edilir."
        )
    )

    reason: str = Field(
        description="Kisa Turkce gerekce."
    )


def build_selection_prompt(
    base_name: BaseRegulationName,
    candidates: list[ScoredReference],
) -> str:

    blocks = []

    for candidate in candidates:
        reference = candidate.reference

        blocks.append(
            "\n".join(
                [
                    f"reference_id: {reference.id}",
                    f"ad: {reference.title}",
                    f"kaynak_türü: {reference.source_type}",
                    (
                        "konsolide_mi: "
                        f"{'evet' if reference.is_consolidated else 'hayır'}"
                    ),
                ]
            )
        )

    candidate_text = "\n\n---\n\n".join(blocks)

    allowed = ", ".join(
        str(candidate.reference_id)
        for candidate in candidates
    )

    publication = ""

    if base_name.published_on:
        publication = (
            f"\nHedef mevzuatın yayım tarihi: "
            f"{base_name.published_on}"
        )

        if base_name.official_gazette_issue:
            publication += (
                f" (Resmî Gazete sayı: "
                f"{base_name.official_gazette_issue})"
            )

    return f"""
Sen bir mevzuat eşleştirme motorusun.

Yeni yayımlanan bir değişiklik metni aşağıdaki mevzuatı
değiştiriyor:

{base_name.name}{publication}

Sistemde kayıtlı referans mevzuatlar:

{candidate_text}

GÖREVİN:

1. Değişikliğin hedeflediği mevzuata karşılık gelen
   referansı seç.

2. reference_id alanı YALNIZCA şu değerlerden biri
   olabilir:

{allowed}

   Bu listede olmayan bir id UYDURMA.

3. Aynı mevzuatın birden fazla kaydı varsa KONSOLİDE
   (güncel) olanı tercih et.

4. Hiçbiri açıkça aynı mevzuat değilse confident=false yaz.
   Yanlış eşleştirme yapmaktansa seçim yapılmaması
   tercih edilir.

5. reason alanını Türkçe ve kısa yaz.

Çıktı şemaya uygun olmalıdır.
""".strip()


async def select_with_ai(
    base_name: BaseRegulationName,
    candidates: list[ScoredReference],
) -> tuple[ScoredReference | None, str, str]:
    """
    Belirsiz adaylar arasindan secim yaptirir.

    Doner: (secilen aday veya None, model adi, gerekce)

    Modelin dondurdugu id SUNUCU TARAFINDA dogrulanir;
    listede olmayan bir id kabul EDILMEZ.
    """

    if not candidates:
        return None, "", "aday yok"

    limited = candidates[:MAX_AI_CANDIDATES]

    prompt = build_selection_prompt(
        base_name=base_name,
        candidates=limited,
    )

    by_id = {
        candidate.reference_id: candidate
        for candidate in limited
    }

    async def generate(client, model_name):

        response = await client.aio.models.generate_content(
            model=model_name,
            contents=prompt,
            config=build_generation_config(
                model_name,
                response_schema=ReferenceSelection,
            ),
        )

        if not response.text:
            raise ReferenceMatchError(
                "Gemini referans seçimi için boş cevap döndürdü."
            )

        try:
            return ReferenceSelection.model_validate_json(
                response.text
            )

        except ValidationError as exc:
            raise ReferenceMatchError(
                "Gemini referans seçimi cevabı beklenen "
                "şemaya uygun değil."
            ) from exc

    outcome = await run_with_quota_fallback(
        generate=generate,
        error_class=ReferenceMatchError,
        error_prefix="Gemini referans seçimi hatası",
    )

    selection: ReferenceSelection = outcome.result

    if not selection.confident:
        return (
            None,
            outcome.model_name,
            selection.reason or "model emin değil",
        )

    candidate = by_id.get(selection.reference_id)

    if candidate is None:
        logger.warning(
            "Referans eslestirme: uydurma reference_id "
            "reddedildi. id=%r",
            selection.reference_id,
        )

        return (
            None,
            outcome.model_name,
            "model aday listesinde olmayan bir id döndürdü",
        )

    return candidate, outcome.model_name, selection.reason


# ---------------------------------------------------------
# ANA GIRIS NOKTASI
# ---------------------------------------------------------

async def match_reference(
    source_text: str,
    references: list[RegulationReference],
    title: str | None = None,
    manual_reference: RegulationReference | None = None,
    allow_ai: bool = True,
) -> ReferenceMatchResult:
    """
    Degisiklik metni icin dogru referansi belirler.

    manual_reference verilirse SONUC ONU DEGISTIRMEZ:
    otomatik eslestirme yalnizca dogrulama amaciyla
    calisir ve celiski varsa needs_review uretir.

    allow_ai=False iken hicbir kosulda Gemini cagrilmaz.
    """

    if not references:
        return ReferenceMatchResult(
            status=MATCH_STATUS_NO_REFERENCES,
            manual_reference=manual_reference,
            detail="Sistemde kayıtlı referans yönetmelik yok.",
        )

    extraction = extract_base_regulation_names(
        source_text=source_text,
        title=title,
    )

    base_name = extraction.primary

    if base_name is None:
        return ReferenceMatchResult(
            status=MATCH_STATUS_NO_BASE_NAME,
            manual_reference=manual_reference,
            detail=(
                "Değişiklik metninden hedef mevzuat adı "
                "çıkarılamadı."
            ),
        )

    local_status, scored = resolve_local(
        base_name=base_name,
        references=references,
    )

    result = ReferenceMatchResult(
        status=local_status,
        base_name=base_name,
        candidates=scored,
        manual_reference=manual_reference,
    )

    if local_status == MATCH_STATUS_MATCHED:
        result.reference = scored[0].reference
        result.score = scored[0].score
        result.detail = (
            f"'{base_name.name}' adı "
            f"'{scored[0].reference.title}' ile eşleşti."
        )

    elif local_status == MATCH_STATUS_AMBIGUOUS and allow_ai:

        # -------------------------------------------------
        # YALNIZCA BURADA GEMINI
        # -------------------------------------------------

        qualified = [
            candidate
            for candidate in scored
            if candidate.score >= MIN_ACCEPTABLE_SCORE
        ]

        try:
            chosen, model_name, reason = await select_with_ai(
                base_name=base_name,
                candidates=qualified,
            )

            result.ai_used = True
            result.ai_model_name = model_name

            if chosen is not None:
                result.status = MATCH_STATUS_MATCHED
                result.reference = chosen.reference
                result.score = chosen.score
                result.detail = (
                    f"Belirsizlik yapay zeka ile çözüldü: "
                    f"{reason}"
                )
            else:
                result.status = MATCH_STATUS_AMBIGUOUS
                result.detail = (
                    f"Birden fazla aday ayırt edilemedi: "
                    f"{reason}"
                )

        except ReferenceMatchError as exc:
            # AI hatasi eslestirmeyi DUSURUR ama pipeline'i
            # dusurmez; guvenli tarafta kalinir.
            logger.warning(
                "Referans eslestirme AI hatasi: %s",
                exc,
            )

            result.ai_used = True
            result.detail = (
                "Belirsizlik çözülemedi (yapay zeka "
                "seçimi tamamlanamadı)."
            )

    elif local_status == MATCH_STATUS_AMBIGUOUS:
        result.detail = (
            "Birden fazla referans aynı derecede uyuyor; "
            "otomatik seçim yapılmadı."
        )

    else:
        result.detail = (
            f"'{base_name.name}' adına yeterince uyan "
            "kayıtlı referans bulunamadı."
        )

    # -----------------------------------------------------
    # MANUEL SECIM USTUNLUGU
    #
    # Kullanicinin sectigi referans DEGISTIRILMEZ. Otomatik
    # sonuc farkliysa yalnizca isaret birakilir.
    # -----------------------------------------------------

    if manual_reference is not None:

        if (
            result.reference is not None
            and result.reference.id != manual_reference.id
        ):
            result.status = MATCH_STATUS_NEEDS_REVIEW
            result.detail = (
                "Otomatik eşleştirme farklı bir referans "
                f"öneriyor: '{result.reference.title}'. "
                "Karşılaştırma sizin seçtiğiniz referansla "
                "yapıldı."
            )

    return result
