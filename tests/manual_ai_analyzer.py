"""
MANUEL BETIK - GERCEK GEMINI API CAGRISI YAPAR

SINIF: live external acceptance

Bu dosya OTOMATIK TEST DEGILDIR. Assertion icermez;
uc senaryonun model cevabi gozle degerlendirilir.

Modelin ilgililik kararini AUTOMATED olarak dogrulayan
testler ayridir ve sahte client kullanir:
    tests/test_gemini_fallback.py
    tests/test_ai_evidence.py

  GERCEK GEMINI : EVET (3 cagri, kota harcar)
  GERCEK SMTP   : hayir
  GERCEK AG     : EVET
  DB YAZAR      : hayir

run_automated_suite.py bu dosyayi TOPLAMAZ.

Calistirma:
    python -m tests.manual_ai_analyzer
"""

import asyncio

from app.services.ai_analyzer import (
    AIAnalyzerError,
    analyze_change_with_ai,
)


async def run_scenario(
    name: str,
    topic: str,
    added_lines: list[str],
    keyword_relevant: bool,
    keyword_score: float,
):

    print("\n" + "=" * 60)
    print(name)
    print("=" * 60)

    print(f"Topic             : {topic}")
    print(f"Keyword Relevant  : {keyword_relevant}")
    print(f"Keyword Score     : {keyword_score}")

    print("\nEklenen içerik:")
    print("-" * 60)

    for line in added_lines:
        print(f"+ {line}")

    print("\nGemini analiz ediyor...")

    outcome = await analyze_change_with_ai(
        topic=topic,
        added_lines=added_lines,
        removed_lines=[],
        keyword_relevant=keyword_relevant,
        keyword_score=keyword_score,
    )

    result = outcome.result

    print(
        f"\nCevap veren model        : "
        f"{outcome.model_name}"
        f"{' (fallback)' if outcome.used_fallback else ''}"
    )

    print("\nGEMINI ANALİZ SONUCU")
    print("-" * 60)

    print(
        f"Relevant                 : "
        f"{result.relevant}"
    )

    print(
        f"Importance               : "
        f"{result.importance}"
    )

    print(
        f"Category                 : "
        f"{result.category}"
    )

    print(
        f"Notification Recommended : "
        f"{result.notification_recommended}"
    )

    print(
        f"Summary                  : "
        f"{result.summary}"
    )

    return result


async def main():

    print("=" * 60)
    print("GEMINI AI ANALYZER TEST")
    print("=" * 60)

    try:

        # -------------------------------------------------
        # SENARYO 1
        # Keyword filtresi zaten yakalayabiliyor
        # -------------------------------------------------

        result_1 = await run_scenario(
            name="SENARYO 1 - API FİYAT DEĞİŞİKLİĞİ",
            topic=(
                "Yapay zeka modeli ve API güncellemeleri"
            ),
            added_lines=[
                (
                    "Model X API için yeni toplu kullanım "
                    "indirimi duyuruldu."
                )
            ],
            keyword_relevant=True,
            keyword_score=0.2,
        )

        # -------------------------------------------------
        # SENARYO 2
        # Tamamen ilgisiz değişiklik
        # -------------------------------------------------

        result_2 = await run_scenario(
            name="SENARYO 2 - İLGİSİZ DEĞİŞİKLİK",
            topic=(
                "Yapay zeka modeli ve API güncellemeleri"
            ),
            added_lines=[
                (
                    "Şirketin İstanbul ofisinin iletişim "
                    "telefonu değiştirildi."
                )
            ],
            keyword_relevant=False,
            keyword_score=0.0,
        )

        # -------------------------------------------------
        # SENARYO 3
        # Keyword filtresinin kaçırabileceği anlamsal ilişki
        # -------------------------------------------------

        result_3 = await run_scenario(
            name="SENARYO 3 - SEMANTİK EŞLEŞME",
            topic=(
                "NVIDIA ekran kartı fiyatları"
            ),
            added_lines=[
                (
                    "RTX 5090 satış fiyatı "
                    "1.999 dolara yükseltildi."
                )
            ],
            keyword_relevant=False,
            keyword_score=0.0,
        )

        print("\n" + "=" * 60)
        print("TEST ÖZETİ")
        print("=" * 60)

        print(
            f"Senaryo 1 Relevant : "
            f"{result_1.relevant}"
        )

        print(
            f"Senaryo 2 Relevant : "
            f"{result_2.relevant}"
        )

        print(
            f"Senaryo 3 Relevant : "
            f"{result_3.relevant}"
        )

        print("\nTEST TAMAMLANDI")

    except AIAnalyzerError as exc:

        print("\nTEST BAŞARISIZ")
        print("-" * 60)

        print(exc)

        raise


if __name__ == "__main__":
    asyncio.run(main())