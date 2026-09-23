"""
auto_section bolum secimi.

Gemini'ye TUM HTML VERILMEZ. Once local olarak aday bolumler
cikarilir; modele yalnizca aday ozetleri gonderilir ve
verilen candidate_id degerlerinden BIRINI secmesi istenir.

Kota korumasi:
Bolum secimi yalnizca ilk auto_section calismasinda veya
kaydedilmis bolum kaybolduğunda yapilir. Normal run'larda
HIC AI cagrisi yapilmaz.

Model / fallback davranisi degisiklik analiziyle aynidir ve
ai_analyzer.run_with_quota_fallback uzerinden yeniden kullanilir.
"""

import logging

from dataclasses import dataclass

from pydantic import BaseModel, Field, ValidationError

from app.services.ai_analyzer import (
    build_generation_config,
    run_with_quota_fallback,
)
from app.services.section_extractor import SectionCandidate


logger = logging.getLogger(__name__)


class AutoSectionSelectionError(Exception):
    """
    auto_section icin izlenecek bolum belirlenemedi.

    Bu durumda SESSIZCE tum sayfaya donulmez; kullanici
    auto_section sectigi icin davranis degistirilmez.
    """


# Modele gonderilen aday metninin ust siniri.
MAX_CANDIDATE_PREVIEW_CHARS = 900


class SectionSelection(BaseModel):
    candidate_id: str = Field(
        description=(
            "Takip konusuyla EN ILGILI bolumun candidate_id "
            "degeri. YALNIZCA listede verilen candidate_id "
            "degerlerinden biri olabilir."
        )
    )

    reason: str = Field(
        description=(
            "Bu bolumun neden secildigini anlatan kisa "
            "Türkçe gerekçe."
        )
    )

    confidence: float = Field(
        description=(
            "Secimin guven skoru. 0.0 ile 1.0 arasinda."
        )
    )


@dataclass
class SectionSelectionOutcome:
    candidate: SectionCandidate
    reason: str
    confidence: float
    model_name: str
    used_fallback: bool


def _preview(text: str) -> str:

    if len(text) <= MAX_CANDIDATE_PREVIEW_CHARS:
        return text

    return text[:MAX_CANDIDATE_PREVIEW_CHARS] + " ..."


def build_selection_prompt(
    topic: str,
    candidates: list[SectionCandidate],
    page_title: str = "",
) -> str:
    """
    Aday bolumlerden secim yapmasi icin prompt uretir.

    Degisiklik analizi promptundan tamamen bagimsizdir.
    """

    blocks = []

    for candidate in candidates:
        blocks.append(
            "\n".join(
                [
                    f"candidate_id: {candidate.candidate_id}",
                    f"başlık: {candidate.label}",
                    f"html_etiketi: {candidate.tag}",
                    f"metin_uzunluğu: {candidate.text_length}",
                    "metin:",
                    _preview(candidate.text),
                ]
            )
        )

    candidate_text = "\n\n---\n\n".join(blocks)

    allowed_ids = ", ".join(
        candidate.candidate_id
        for candidate in candidates
    )

    title_line = (
        f"Sayfa başlığı: {page_title}\n"
        if page_title
        else ""
    )

    return f"""
Sen bir web sitesi değişiklik izleme sisteminin
bölüm seçme motorusun.

{title_line}Kullanıcının takip ettiği konu:

{topic}

Aşağıda bu sayfadan çıkarılmış aday bölümler var.
Her adayın bir candidate_id değeri vardır.

{candidate_text}

Görevin:

1. Kullanıcının takip ettiği konuyla EN İLGİLİ tek bölümü seç.

2. Seçtiğin bölüm, konuyla ilgili güncellemelerin
   gelecekte görüneceği bölüm olmalıdır.

3. Sayfanın tamamını kapsayan çok genel bir bloğu değil,
   konuya odaklanmış en uygun bölümü tercih et.
   Ancak konuyla ilgili içerik yalnızca geniş bir blokta
   duruyorsa o bloğu seçebilirsin.

4. Menü, çerez uyarısı, altbilgi gibi gezinme amaçlı
   blokları seçme.

5. candidate_id alanı YALNIZCA şu değerlerden biri olabilir:

{allowed_ids}

   Bu listede olmayan bir candidate_id UYDURMA.

6. reason alanını Türkçe yaz ve kısa tut.

7. confidence alanına 0.0 - 1.0 arasında bir güven skoru ver.

Çıktı şemaya uygun olmalıdır.
""".strip()


async def select_section_with_ai(
    topic: str,
    candidates: list[SectionCandidate],
    page_title: str = "",
) -> SectionSelectionOutcome:
    """
    Aday bolumler arasindan konuyla ilgili olani sectirir.

    Bir secim icin en fazla iki model cagrisi yapilir
    (primary + kota fallback).

    Modelin dondurdugu candidate_id SUNUCU TARAFINDA
    dogrulanir; listede olmayan bir deger kabul edilmez.
    """

    if not topic or not topic.strip():
        raise ValueError(
            "Takip edilen konu boş olamaz."
        )

    if not candidates:
        raise AutoSectionSelectionError(
            "Sayfada izlenebilecek uygun bir bölüm bulunamadı."
        )

    prompt = build_selection_prompt(
        topic=topic,
        candidates=candidates,
        page_title=page_title,
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
                response_schema=SectionSelection,
            ),
        )

        if not response.text:
            raise AutoSectionSelectionError(
                "Gemini bölüm seçimi için boş cevap döndürdü."
            )

        try:
            selection = SectionSelection.model_validate_json(
                response.text
            )

        except ValidationError as exc:
            raise AutoSectionSelectionError(
                "Gemini bölüm seçimi cevabı beklenen "
                "şemaya uygun değil."
            ) from exc

        # ---------------------------------------------
        # SUNUCU TARAFI DOGRULAMA
        #
        # Model uydurma bir candidate_id dondurduyse
        # secim BASARISIZ sayilir.
        # ---------------------------------------------

        candidate_id = (selection.candidate_id or "").strip()

        candidate = candidates_by_id.get(candidate_id)

        if candidate is None:
            raise AutoSectionSelectionError(
                "Gemini aday listesinde olmayan bir bölüm "
                f"seçti: {candidate_id!r}"
            )

        return selection, candidate

    outcome = await run_with_quota_fallback(
        generate=generate,
        error_class=AutoSectionSelectionError,
        error_prefix="Gemini bölüm seçimi hatası",
    )

    selection, candidate = outcome.result

    logger.info(
        "auto_section bolum secildi. model=%s candidate=%s "
        "label=%s confidence=%s",
        outcome.model_name,
        candidate.candidate_id,
        candidate.label,
        selection.confidence,
    )

    return SectionSelectionOutcome(
        candidate=candidate,
        reason=selection.reason,
        confidence=selection.confidence,
        model_name=outcome.model_name,
        used_fallback=outcome.used_fallback,
    )
