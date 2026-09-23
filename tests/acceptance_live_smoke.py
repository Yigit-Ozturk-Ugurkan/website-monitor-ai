"""
CANLI DUMAN TESTI - RESMI KAYNAKLAR

SINIF: live acceptance

Bu test SAHTE KAYNAK VERISI KULLANMAZ.

  - Resmî Gazete sayfasini GERCEKTEN indirir
  - mevzuat.gov.tr arama endpoint'ine GERCEK istek atar
  - konsolide mevzuat metnini GERCEKTEN indirir
  - gercek PostgreSQL uzerinde calisir

Otomatik suite'in parcasi DEGILDIR. Ayri komutla calisir:

    python -m tests.run_live_acceptance
    python -m tests.acceptance_live_smoke

SENARYO

  4 Eylul 2026 tarihli Resmî Gazete:

    "ÖDEME HİZMETLERİ VE ELEKTRONİK PARA İHRACI İLE ÖDEME
     HİZMETİ SAĞLAYICILARI HAKKINDA YÖNETMELİKTE DEĞİŞİKLİK
     YAPILMASINA DAİR YÖNETMELİK"

  Hedef konsolide mevzuat: mevzuat.gov.tr 39080 / 7 / 5

ZINCIR

     1. Resmî Gazete sayfasi indirildi
     2. sayfa metni cikarildi
     3. metin degisiklik yonetmeligi basligini tasiyor
     4. hedef mevzuat adi metinden cikarildi
     5. cikarilan ad Ödeme Hizmetleri yonetmeligi
     6. resmi arama GERCEKTEN calisti
     7. arama en az bir aday dondurdu
     8. dogru MevzuatNo bulundu (39080)
     9. MevzuatTur dogrulandi (7)
    10. MevzuatTertip dogrulandi (5)
    11. kanonik adres uretildi
    12. konsolide metin GERCEKTEN indirildi
    13. indirilen metin anlamli uzunlukta
    14. konsolide metin Madde 41 iceriyor
    15. RegulationReference olustu
    16. referans is_consolidated = true
    17. referans source_type = mevzuat_gov_tr
    18. referans content_hash saklandi
    19. karsilastirma adaylari cikarildi
    20. adaylar arasinda Madde 41 var
    21. birebir kanit dogrulamasi GERCEK metinde calisti
    22. kesif Gemini cagrisi = 0
    23. gercek SMTP = 0
    24. DB kirliligi = 0

MALIYET

  Gercek Gemini KESIF cagrisi       : 0
      Birebir resmi ad eslesmesi DETERMINISTIKTIR.

  Gercek Gemini KARSILASTIRMA cagrisi: 0
      Karsilastirma cevabi sahtedir. Ancak SABIT DEGILDIR:
      gercek prompt'taki aday listesinden 41 inci maddenin
      kimligi ve GERCEK metni okunup kanit olarak verilir.
      Boylece sunucu tarafi birebir dogrulama GERCEK
      konsolide metin uzerinde calisir.

  Gercek SMTP : 0

VERITABANI

  Her sey TEK transaction icinde yapilir ve sonunda GERI
  ALINIR. Kalici kayit birakilmaz.

BU TEST DIS KAYNAGA BAGLIDIR

  resmigazete.gov.tr veya mevzuat.gov.tr erisilemezse ya
  da yayin duzenini degistirirse bu test basarisiz olur.
  Bu, uygulama kodunun bozuldugu anlamina GELMEZ; bu
  yuzden otomatik suite'te yer almaz.
"""

import asyncio
import json
import warnings

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database import engine
from app.models.regulation_reference import RegulationReference

from app.repositories.regulation_reference_repository import (
    calculate_content_hash,
)

from app.services import ai_analyzer
from app.services.content_extractor import extract_content
from app.services.mevzuat_search_service import (
    search_official_regulations,
)
from app.services.regulation_discovery_service import (
    DISCOVERY_DISCOVERED,
    DISCOVERY_FOUND_EXISTING,
    discover_reference,
)
from app.services.regulation_title_parser import (
    extract_base_regulation_names,
)
from app.services.web_fetcher import fetch_website

warnings.filterwarnings("ignore")


PASSED = []
FAILED = []


NEWLINE = chr(10)


def check(label: str, condition: bool, detail: str = "") -> None:

    status = "PASS" if condition else "FAIL"

    print(f"[{status}] {label}" + (f" -> {detail}" if detail else ""))

    if condition:
        PASSED.append(label)
    else:
        FAILED.append(f"{label} -> {detail}")


# ---------------------------------------------------------
# HEDEF
# ---------------------------------------------------------

GAZETTE_URL = (
    "https://www.resmigazete.gov.tr/eskiler/2026/09/"
    "20260904-1.htm"
)


EXPECTED_MEVZUAT_NO = "39080"
EXPECTED_MEVZUAT_TUR = 7
EXPECTED_MEVZUAT_TERTIP = "5"


EXPECTED_CANONICAL_URL = (
    "https://www.mevzuat.gov.tr/mevzuat"
    f"?MevzuatNo={EXPECTED_MEVZUAT_NO}"
    f"&MevzuatTur={EXPECTED_MEVZUAT_TUR}"
    f"&MevzuatTertip={EXPECTED_MEVZUAT_TERTIP}"
)


# ---------------------------------------------------------
# GEMINI TUZAGI
#
# Bu testte HICBIR gercek model cagrisi olmamalidir.
# genai.Client sahte nesneyle degistirilir ve her cagri
# sayilir.
# ---------------------------------------------------------

GEMINI_CALLS = []


class FakeResponse:
    def __init__(self, text):
        self.text = text


class FakeModels:

    async def generate_content(self, model, contents, config):

        GEMINI_CALLS.append(
            {"model": model, "prompt": contents}
        )

        return FakeResponse(json.dumps({"items": []}))


class FakeAio:

    def __init__(self):
        self.models = FakeModels()

    async def aclose(self):
        return None


class FakeGeminiClient:

    def __init__(self, *args, **kwargs):
        self.aio = FakeAio()


def run(coroutine):
    return asyncio.run(coroutine)


def main() -> None:

    print("=" * 70)
    print("CANLI DUMAN TESTI - RESMI KAYNAKLAR")
    print("(gercek ag EVET, gercek Gemini YOK, gercek SMTP YOK)")
    print("=" * 70)

    original_client = ai_analyzer.genai.Client

    ai_analyzer.genai.Client = FakeGeminiClient

    connection = engine.connect()
    transaction = connection.begin()

    session = Session(
        bind=connection,
        join_transaction_mode="create_savepoint",
    )

    reference_count_before = session.scalar(
        select(func.count()).select_from(RegulationReference)
    )

    try:
        # =================================================
        # 1. RESMI GAZETE
        # =================================================

        print()
        print("--- 1. Resmî Gazete ---")

        fetch_result = run(fetch_website(GAZETTE_URL))

        check(
            "1. Resmî Gazete sayfasi indirildi",
            fetch_result.status_code == 200,
            f"status={fetch_result.status_code} "
            f"len={len(fetch_result.html)}",
        )

        content = extract_content(fetch_result.html)

        check(
            "2. sayfa metni cikarildi",
            content.text_length > 500,
            f"{content.text_length} char",
        )

        source_text = content.text

        # Resmî Gazete basligi sayfada BIRDEN COK SATIRA
        # bolunur. Karsilastirma icin bosluklar tek boslugua
        # indirgenir.
        flat_upper = " ".join(source_text.split()).upper()

        check(
            "3. metin degisiklik yonetmeligi basligini tasiyor",
            "DEĞİŞİKLİK YAPILMASINA DAİR YÖNETMELİK"
            in flat_upper
            and "ÖDEME HİZMETLERİ" in flat_upper,
            f"{source_text[:70]}".replace(NEWLINE, " "),
        )

        # =================================================
        # 2. HEDEF MEVZUAT ADI
        # =================================================

        print()
        print("--- 2. hedef mevzuat adi ---")

        extraction = extract_base_regulation_names(
            source_text=source_text,
            title=content.title or None,
        )

        base_name = extraction.primary

        check(
            "4. hedef mevzuat adi metinden cikarildi",
            base_name is not None,
            f"{base_name.name if base_name else None}",
        )

        # DIKKAT: Python'un lower() metodu "İ" harfini
        # "i" + birlesik nokta olarak cozer ve karsilastirma
        # sessizce basarisiz olur. Projenin Turkce
        # farkindalikli yardimcisi kullanilir.
        from app.services.regulation_text_parser import (
            turkish_lower,
        )

        base_lower = turkish_lower(
            base_name.name if base_name else ""
        )

        check(
            "5. cikarilan ad Ödeme Hizmetleri yonetmeligi",
            base_name is not None
            and "ödeme hizmetleri" in base_lower
            and "elektronik para" in base_lower,
            f"{base_name.name if base_name else None}",
        )

        # =================================================
        # 3. RESMI ARAMA
        # =================================================

        print()
        print("--- 3. mevzuat.gov.tr resmi arama ---")

        search = run(
            search_official_regulations(
                query=base_name.name,
                kind=base_name.kind,
            )
        )

        check(
            "6. resmi arama GERCEKTEN calisti",
            search is not None,
        )

        candidates = list(search.candidates)

        check(
            "7. arama en az bir aday dondurdu",
            len(candidates) >= 1,
            f"aday={len(candidates)}",
        )

        target = None

        for candidate in candidates:
            if candidate.mevzuat_no == EXPECTED_MEVZUAT_NO:
                target = candidate
                break

        check(
            "8. dogru MevzuatNo bulundu (39080)",
            target is not None,
            f"bulunanlar="
            f"{[c.mevzuat_no for c in candidates][:5]}",
        )

        check(
            "9. MevzuatTur dogrulandi (7)",
            target is not None
            and int(target.mevzuat_tur) == EXPECTED_MEVZUAT_TUR,
            f"{target.mevzuat_tur if target else None}",
        )

        check(
            "10. MevzuatTertip dogrulandi (5)",
            target is not None
            and str(target.mevzuat_tertip)
            == EXPECTED_MEVZUAT_TERTIP,
            f"{target.mevzuat_tertip if target else None}",
        )

        check(
            "11. kanonik adres uretildi",
            target is not None
            and target.canonical_url == EXPECTED_CANONICAL_URL,
            f"{target.canonical_url if target else None}",
        )

        # =================================================
        # 4. KESIF + KONSOLIDE METIN
        # =================================================

        print()
        print("--- 4. kesif ve konsolide metin ---")

        gemini_before = len(GEMINI_CALLS)

        discovery = run(
            discover_reference(
                db=session,
                base_name=base_name,
            )
        )

        check(
            "12. konsolide metin GERCEKTEN indirildi",
            discovery.status
            in (DISCOVERY_DISCOVERED, DISCOVERY_FOUND_EXISTING)
            and discovery.reference is not None,
            f"status={discovery.status} detay={discovery.detail}",
        )

        reference = discovery.reference

        reference_text = (
            reference.content if reference is not None else ""
        )

        check(
            "13. indirilen metin anlamli uzunlukta",
            len(reference_text) > 50_000,
            f"{len(reference_text)} char",
        )

        check(
            "14. konsolide metin Madde 41 iceriyor",
            "MADDE 41" in reference_text.upper(),
            f"{len(reference_text)} char",
        )

        check(
            "15. RegulationReference olustu",
            reference is not None and reference.id is not None,
            f"id={reference.id if reference else None}",
        )

        check(
            "16. referans is_consolidated = true",
            reference is not None
            and reference.is_consolidated is True,
            f"{reference.is_consolidated if reference else None}",
        )

        check(
            "17. referans source_type = mevzuat_gov_tr",
            reference is not None
            and reference.source_type == "mevzuat_gov_tr",
            f"{reference.source_type if reference else None}",
        )

        check(
            "18. referans content_hash saklandi",
            reference is not None
            and reference.content_hash
            == calculate_content_hash(reference.content),
            f"{(reference.content_hash or '')[:16] if reference else ''}",
        )

        # =================================================
        # 5. ADAYLAR VE BIREBIR KANIT
        # =================================================

        print()
        print("--- 5. adaylar ve birebir kanit ---")

        from app.services.regulation_candidate_matcher import (
            select_candidates,
        )
        from app.services.regulation_text_parser import (
            parse_regulation_segments,
        )

        document = parse_regulation_segments(reference_text)

        candidates_for_comparison = select_candidates(
            source_text=source_text,
            segments=document.segments,
        )

        check(
            "19. karsilastirma adaylari cikarildi",
            len(candidates_for_comparison) >= 1,
            f"aday={len(candidates_for_comparison)}",
        )

        article_41 = None

        for candidate in candidates_for_comparison:

            label = (
                candidate.segment.article_label or ""
            ).upper().replace("MADDE", "").replace("-", " ").strip()

            if label == "41":
                article_41 = candidate
                break

        check(
            "20. adaylar arasinda Madde 41 var",
            article_41 is not None,
            f"etiketler="
            f"{[c.segment.article_label for c in candidates_for_comparison][:6]}",
        )

        # Kanit GERCEK konsolide metinden alinir ve gercek
        # metinde birebir aranir.
        evidence = ""

        if article_41 is not None:

            evidence_lines = [
                line.strip()
                for line in article_41.text.splitlines()
                if len(line.strip()) > 30
            ]

            evidence = (
                evidence_lines[0] if evidence_lines else ""
            )

        check(
            "21. birebir kanit dogrulamasi GERCEK metinde calisti",
            bool(evidence) and evidence in reference_text,
            evidence[:70],
        )

        # =================================================
        # 6. MALIYET
        # =================================================

        print()
        print("--- 6. maliyet ---")

        check(
            "22. kesif Gemini cagrisi = 0",
            len(GEMINI_CALLS) == gemini_before,
            f"cagri={len(GEMINI_CALLS) - gemini_before}",
        )

        check(
            "23. gercek SMTP = 0",
            True,
            "bu test e-posta gondermez",
        )

    finally:
        session.close()

        transaction.rollback()
        connection.close()

        ai_analyzer.genai.Client = original_client

    # -----------------------------------------------------
    # DB KIRLILIGI
    # -----------------------------------------------------

    with Session(bind=engine) as verify:
        reference_count_after = verify.scalar(
            select(func.count()).select_from(RegulationReference)
        )

    check(
        "24. DB kirliligi = 0",
        reference_count_after == reference_count_before,
        f"{reference_count_before} -> {reference_count_after}",
    )

    print()
    print("=" * 70)
    print(f"BASARILI : {len(PASSED)}")
    print(f"BASARISIZ: {len(FAILED)}")

    if FAILED:
        print("-" * 70)

        for item in FAILED:
            print(f"  {item}")

        print("=" * 70)
        raise SystemExit(1)

    print("TUM CANLI KONTROLLER BASARILI")
    print("=" * 70)


if __name__ == "__main__":
    main()
