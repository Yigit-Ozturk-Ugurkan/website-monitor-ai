"""
MEVZUAT KARSILASTIRMA MOTORU TESTLERI

Kapsam:
    1.  website_monitor -> comparison yok
    2.  regulation ilk snapshot -> comparison yok
    3.  degisiklik yok -> comparison yok
    4.  ilgisiz degisiklik -> comparison yok
    5.  ilgili degisiklik -> comparison olusuyor
    6.  bir Change -> birden fazla comparison item
    11. Gemini YALNIZCA aday segmentleri aliyor
    12. uydurma candidate_id reddediliyor
    13. exact source_evidence dogrulamasi
    14. gecersiz source evidence reddediliyor
    15. exact reference_evidence dogrulamasi
    16. gecersiz old_text reddediliyor
    17. amended: old + new
    18. added: old null olabilir
    19. repealed: new null olabilir
    20. aday yok -> no_match
    21. AI hatasi -> failed satiri
    22. hata snapshot/change/AIAnalysis'i GERI ALMIYOR
    23. tekrar calistirma -> 0 ek Gemini cagrisi
    24. reference content hash saklaniyor

GERCEK GEMINI CAGRISI YOKTUR. genai.Client sahte bir
nesneyle degistirilir ve her cagri sayilir.

Gercek PostgreSQL uzerinde calisir ama KALICI KAYIT BIRAKMAZ;
dis transaction sonunda rollback edilir.

Calistirma:
    python -m tests.test_regulation_comparison
"""

import asyncio
import json
import warnings

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database import engine
from app.models.regulation_comparison import (
    RegulationComparison,
    RegulationComparisonItem,
)
from app.models.ai_analysis import AIAnalysis
from app.models.change import Change
from app.models.snapshot import Snapshot
from app.repositories.ai_analysis_repository import (
    create_ai_analysis,
)
from app.repositories.change_repository import create_change
from app.repositories.monitor_repository import create_monitor
from app.repositories.regulation_reference_repository import (
    create_regulation_reference,
)
from app.repositories.snapshot_repository import create_snapshot
from app.services.regulation_comparator import (
    contains_exact,
    normalize_for_match,
)
from app.services.regulation_monitor_service import (
    COMPARISON_COMPLETED,
    COMPARISON_FAILED,
    COMPARISON_NO_MATCH,
    COMPARISON_SKIPPED_FIRST_RUN,
    COMPARISON_SKIPPED_IRRELEVANT,
    COMPARISON_SKIPPED_NO_CHANGE,
    run_regulation_comparison,
)


warnings.filterwarnings("ignore")


PASSED = []
FAILED = []


def check(label: str, condition: bool, detail: str = "") -> None:

    status = "PASS" if condition else "FAIL"

    print(f"[{status}] {label}" + (f" -> {detail}" if detail else ""))

    if condition:
        PASSED.append(label)
    else:
        FAILED.append(f"{label} -> {detail}")


# ---------------------------------------------------------
# SAHTE GEMINI
#
# Gercek istemci hic kurulmaz. Her generate_content cagrisi
# GEMINI_CALLS listesine yazilir; testler bu sayiyi
# dogrudan kontrol eder.
# ---------------------------------------------------------

GEMINI_CALLS = []

# Bir sonraki cagrida donecek cevap veya firlatilacak hata.
NEXT_RESPONSE = {"payload": None, "error": None}


class FakeResponse:
    def __init__(self, text):
        self.text = text


class FakeModels:
    async def generate_content(self, model, contents, config):

        GEMINI_CALLS.append(
            {
                "model": model,
                "prompt": contents,
            }
        )

        if NEXT_RESPONSE["error"] is not None:
            raise NEXT_RESPONSE["error"]

        return FakeResponse(NEXT_RESPONSE["payload"])


class FakeAio:
    def __init__(self):
        self.models = FakeModels()

    async def aclose(self):
        return None


class FakeGeminiClient:
    def __init__(self, *args, **kwargs):
        self.aio = FakeAio()


# ---------------------------------------------------------
# FIXTURE METINLER
#
# Bunlar TEST VERISIDIR; gercek bir yonetmelik degildir.
# ---------------------------------------------------------

REFERENCE_TEXT = """BİRİNCİ BÖLÜM
Amaç ve Kapsam
MADDE 1 - Bu Yönetmeliğin amacı test amaçlı lisans
işlemlerini düzenlemektir.
MADDE 12 - Lisans başvuruları otuz gün içerisinde
değerlendirilir.
MADDE 18 - Denetim raporları Kuruma sunulur.
MADDE 20 - Lisans bedelleri her yıl yeniden belirlenir.
"""

AMENDMENT_TEXT = """Test Lisans Yönetmeliğinde Değişiklik Yapılmasına Dair Yönetmelik
MADDE 1- Aynı Yönetmeliğin 12 nci maddesinde yer alan "otuz gün"
ibaresi "on beş gün" şeklinde değiştirilmiştir.
MADDE 2- Aynı Yönetmeliğin 18 inci maddesi yürürlükten
kaldırılmıştır.
MADDE 3- Aynı Yönetmeliğe aşağıdaki madde eklenmiştir.
"MADDE 21 - Elektronik başvurular kabul edilir."
"""


def response_payload(items, **overrides):

    body = {
        "has_regulatory_change": True,
        "overall_summary": "Test degisiklik ozeti.",
        "importance": "high",
        "items": items,
    }

    body.update(overrides)

    return json.dumps(body, ensure_ascii=False)


class FakeRunResult:
    """
    db_monitor_service.run_database_monitor() ciktisinin
    karsilastirma icin kullanilan alanlari.
    """

    def __init__(
        self,
        first_run=False,
        changed=True,
        change_record=None,
        ai_analysis_record=None,
    ):
        self.first_run = first_run
        self.changed = changed
        self.change_record = change_record
        self.ai_analysis_record = ai_analysis_record


def main() -> None:

    print("=" * 70)
    print("MEVZUAT KARSILASTIRMA MOTORU TESTLERI")
    print("=" * 70)

    from google import genai

    real_client = genai.Client
    genai.Client = FakeGeminiClient

    connection = engine.connect()
    transaction = connection.begin()

    session = Session(
        bind=connection,
        join_transaction_mode="create_savepoint",
    )

    comparison_count_before = session.scalar(
        select(func.count()).select_from(RegulationComparison)
    )

    print(
        f"Baslangic karsilastirma sayisi: "
        f"{comparison_count_before}"
    )
    print("-" * 70)

    def build_monitor(name, plan, reference_id):
        return create_monitor(
            db=session,
            name=name,
            url="https://example.com/resmi-gazete",
            topic="Enerji, elektrik piyasası ve lisans yönetmelikleri",
            tracking_plan=plan,
            reference_regulation_id=reference_id,
            scope_type="full_page",
            schedule_time="09:00",
        )

    def build_change(monitor, added_text, relevant=True):

        old_snapshot = create_snapshot(
            session,
            monitor.id,
            "Resmî Gazete",
            "önceki içerik",
        )

        new_snapshot = create_snapshot(
            session,
            monitor.id,
            "Resmî Gazete",
            "önceki içerik\n" + added_text,
        )

        return create_change(
            db=session,
            monitor_id=monitor.id,
            old_snapshot_id=old_snapshot.id,
            new_snapshot_id=new_snapshot.id,
            relevant=relevant,
            relevance_score=0.9 if relevant else 0.1,
            added_lines=added_text.splitlines(),
            removed_lines=[],
        )

    def build_analysis(change, relevant=True):
        return create_ai_analysis(
            db=session,
            change_id=change.id,
            model_name="test-model",
            keyword_relevant=relevant,
            keyword_score=0.9 if relevant else 0.1,
            relevant=relevant,
            importance="high" if relevant else "low",
            category="policy",
            summary="Test analiz ozeti.",
            notification_recommended=relevant,
        )

    def run(monitor, result):
        return asyncio.run(
            run_regulation_comparison(
                db=session,
                monitor=monitor,
                result=result,
            )
        )

    try:

        reference = create_regulation_reference(
            db=session,
            title="Test Lisans Yönetmeliği",
            source_url="https://mevzuat.test.local/karsilastirma",
            content=REFERENCE_TEXT,
        )

        # -------------------------------------------------
        # 1. website_monitor -> COMPARISON YOK
        # -------------------------------------------------

        print("\n--- 1. website_monitor ---")

        website_monitor = build_monitor(
            "Karsilastirma Website Monitor",
            "website_monitor",
            None,
        )

        website_change = build_change(
            website_monitor,
            AMENDMENT_TEXT,
        )

        website_analysis = build_analysis(website_change)

        GEMINI_CALLS.clear()

        website_result = run(
            website_monitor,
            FakeRunResult(
                change_record=website_change,
                ai_analysis_record=website_analysis,
            ),
        )

        check(
            "1. website_monitor icin durum None",
            website_result.status is None,
            f"durum={website_result.status}",
        )

        check(
            "1. website_monitor karsilastirma KAYDI "
            "olusturmadi",
            website_result.comparison is None,
        )

        check(
            "1. website_monitor Gemini CAGIRMADI",
            len(GEMINI_CALLS) == 0,
            f"cagri={len(GEMINI_CALLS)}",
        )

        # -------------------------------------------------
        # 2-4. TETIKLEME KOSULLARI
        # -------------------------------------------------

        regulation_monitor = build_monitor(
            "Karsilastirma Mevzuat Monitor",
            "regulation_monitor",
            reference.id,
        )

        print("\n--- 2. ilk snapshot ---")

        GEMINI_CALLS.clear()

        first_run_result = run(
            regulation_monitor,
            FakeRunResult(
                first_run=True,
                changed=False,
            ),
        )

        check(
            "2. ilk snapshot'ta karsilastirma yok",
            first_run_result.status
            == COMPARISON_SKIPPED_FIRST_RUN,
            f"durum={first_run_result.status}",
        )

        check(
            "2. ilk snapshot Gemini CAGIRMADI",
            len(GEMINI_CALLS) == 0,
        )

        print("\n--- 3. degisiklik yok ---")

        GEMINI_CALLS.clear()

        no_change_result = run(
            regulation_monitor,
            FakeRunResult(
                changed=False,
                change_record=None,
            ),
        )

        check(
            "3. degisiklik yokken karsilastirma yok",
            no_change_result.status
            == COMPARISON_SKIPPED_NO_CHANGE,
            f"durum={no_change_result.status}",
        )

        check(
            "3. degisiklik yokken Gemini CAGIRMADI",
            len(GEMINI_CALLS) == 0,
        )

        print("\n--- 4. ilgisiz degisiklik ---")

        irrelevant_change = build_change(
            regulation_monitor,
            "Sitede yeni bir görsel yayımlandı.",
            relevant=False,
        )

        irrelevant_analysis = build_analysis(
            irrelevant_change,
            relevant=False,
        )

        GEMINI_CALLS.clear()

        irrelevant_result = run(
            regulation_monitor,
            FakeRunResult(
                change_record=irrelevant_change,
                ai_analysis_record=irrelevant_analysis,
            ),
        )

        check(
            "4. ilgisiz degisiklikte karsilastirma yok",
            irrelevant_result.status
            == COMPARISON_SKIPPED_IRRELEVANT,
            f"durum={irrelevant_result.status}",
        )

        check(
            "4. ilgisiz degisiklikte Gemini CAGIRMADI "
            "(kota korunuyor)",
            len(GEMINI_CALLS) == 0,
        )

        check(
            "4. bu asamada hic karsilastirma satiri yok",
            session.scalar(
                select(func.count())
                .select_from(RegulationComparison)
            ) == comparison_count_before,
        )

        # -------------------------------------------------
        # 5 + 6 + 17-19. GERCEK KARSILASTIRMA
        # -------------------------------------------------

        print("\n--- 5/6/17-19. gercek karsilastirma ---")

        change = build_change(
            regulation_monitor,
            AMENDMENT_TEXT,
        )

        analysis = build_analysis(change)

        NEXT_RESPONSE["error"] = None
        NEXT_RESPONSE["payload"] = response_payload(
            [
                {
                    # amended: old + new
                    "candidate_id": "seg-2",
                    "affected_article": "Madde 12",
                    "affected_paragraph": "1. fıkra",
                    "change_type": "amended",
                    "old_text": "otuz gün",
                    "new_text": "on beş gün",
                    "source_evidence": (
                        '"otuz gün"\nibaresi "on beş gün" '
                        "şeklinde değiştirilmiştir."
                    ),
                    "reference_evidence": (
                        "Lisans başvuruları otuz gün içerisinde"
                    ),
                    "change_summary": (
                        "Değerlendirme süresi kısaltıldı."
                    ),
                    "confidence": 0.96,
                },
                {
                    # repealed: new_text null olabilir
                    "candidate_id": "seg-3",
                    "affected_article": "Madde 18",
                    "affected_paragraph": None,
                    "change_type": "repealed",
                    "old_text": "Denetim raporları Kuruma sunulur.",
                    "new_text": None,
                    "source_evidence": (
                        "18 inci maddesi yürürlükten"
                    ),
                    "reference_evidence": (
                        "Denetim raporları Kuruma sunulur."
                    ),
                    "change_summary": "Madde kaldırıldı.",
                    "confidence": 0.91,
                },
                {
                    # added: old_text null olabilir
                    "candidate_id": "seg-4",
                    "affected_article": "Madde 20",
                    "affected_paragraph": None,
                    "change_type": "added",
                    "old_text": None,
                    "new_text": (
                        "MADDE 21 - Elektronik başvurular "
                        "kabul edilir."
                    ),
                    "source_evidence": (
                        "aşağıdaki madde eklenmiştir."
                    ),
                    "reference_evidence": None,
                    "change_summary": "Yeni madde eklendi.",
                    "confidence": 0.88,
                },
            ]
        )

        GEMINI_CALLS.clear()

        outcome = run(
            regulation_monitor,
            FakeRunResult(
                change_record=change,
                ai_analysis_record=analysis,
            ),
        )

        check(
            "5. ilgili degisiklikte karsilastirma yapildi",
            outcome.status == COMPARISON_COMPLETED,
            f"durum={outcome.status}",
        )

        check(
            "5. karsilastirma kaydi olustu",
            outcome.comparison is not None,
        )

        check(
            "5. TEK bir mantiksal Gemini cagrisi yapildi",
            len(GEMINI_CALLS) == 1,
            f"cagri={len(GEMINI_CALLS)}",
        )

        comparison = outcome.comparison

        check(
            "6. bir Change icin BIRDEN FAZLA item olusabiliyor",
            len(comparison.items) == 3,
            f"item={len(comparison.items)}",
        )

        items_by_article = {
            item.affected_article: item
            for item in comparison.items
        }

        amended = items_by_article.get("Madde 12")
        repealed = items_by_article.get("Madde 18")
        added = items_by_article.get("Madde 20")

        check(
            "17. amended item old + new tasiyor",
            amended is not None
            and amended.old_text == "otuz gün"
            and amended.new_text == "on beş gün",
            f"old={amended.old_text if amended else None} "
            f"new={amended.new_text if amended else None}",
        )

        check(
            "17. amended item fikra bilgisini koruyor",
            amended is not None
            and amended.affected_paragraph == "1. fıkra",
        )

        check(
            "18. added item old_text null ile kabul edildi",
            added is not None
            and added.old_text is None
            and added.new_text is not None,
            f"old={added.old_text if added else None}",
        )

        check(
            "19. repealed item new_text null ile kabul edildi",
            repealed is not None
            and repealed.new_text is None
            and repealed.old_text is not None,
            f"new={repealed.new_text if repealed else None}",
        )

        check(
            "degisiklik turleri kontrollu degerlerden",
            all(
                item.change_type
                in (
                    "amended",
                    "added",
                    "repealed",
                    "renamed",
                    "unknown",
                )
                for item in comparison.items
            ),
        )

        check(
            "overall_summary saklandi",
            comparison.overall_summary
            == "Test degisiklik ozeti.",
        )

        check(
            "cevabi ureten model kaydedildi",
            bool(comparison.model_name),
            f"model={comparison.model_name}",
        )

        # -------------------------------------------------
        # 11. PROMPT YALNIZCA ADAYLARI TASIYOR
        # -------------------------------------------------

        print("\n--- 11. prompt icerigi ---")

        prompt = GEMINI_CALLS[0]["prompt"]

        check(
            "11. prompt kaynak degisiklik metnini iceriyor",
            "on beş gün" in prompt,
        )

        check(
            "11. prompt aday madde metinlerini iceriyor",
            "otuz gün içerisinde" in prompt,
        )

        check(
            "11. prompt TUM referans yonetmeligi "
            "GONDERMIYOR (yalnizca adaylar)",
            len(prompt) < len(REFERENCE_TEXT) * 20,
            f"prompt={len(prompt)} referans={len(REFERENCE_TEXT)}",
        )

        check(
            "11. prompt izin verilen candidate_id "
            "listesini bildiriyor",
            "candidate_id" in prompt
            and "UYDURMA" in prompt,
        )

        check(
            "11. prompt BIREBIR kopyalama kuralini "
            "iceriyor",
            "BİREBİR" in prompt,
        )

        # -------------------------------------------------
        # 24. REFERENCE CONTENT HASH
        # -------------------------------------------------

        print("\n--- 24. referans surum izi ---")

        check(
            "24. referans content hash saklandi",
            comparison.reference_content_hash
            == reference.content_hash,
            f"hash={comparison.reference_content_hash}",
        )

        check(
            "24. referans id saklandi",
            comparison.reference_regulation_id == reference.id,
        )

        check(
            "24. tam referans metni KOPYALANMADI",
            not hasattr(comparison, "reference_content"),
        )

        # -------------------------------------------------
        # 23. DUPLICATE KORUMASI
        # -------------------------------------------------

        print("\n--- 23. duplicate koruma ---")

        GEMINI_CALLS.clear()

        repeat = run(
            regulation_monitor,
            FakeRunResult(
                change_record=change,
                ai_analysis_record=analysis,
            ),
        )

        check(
            "23. tekrar calistirmada 0 EK Gemini cagrisi",
            len(GEMINI_CALLS) == 0,
            f"cagri={len(GEMINI_CALLS)}",
        )

        check(
            "23. mevcut karsilastirma yeniden kullanildi",
            repeat.reused_existing is True
            and repeat.comparison is not None
            and repeat.comparison.id == comparison.id,
        )

        check(
            "23. ikinci karsilastirma satiri OLUSMADI",
            session.scalar(
                select(func.count())
                .select_from(RegulationComparison)
                .where(
                    RegulationComparison.change_id == change.id
                )
            ) == 1,
        )

        # -------------------------------------------------
        # 12-16. EVIDENCE DOGRULAMASI
        # -------------------------------------------------

        print("\n--- 12-16. evidence dogrulamasi ---")

        check(
            "13. dogrulanan source_evidence saklandi",
            amended is not None
            and amended.source_evidence is not None,
        )

        check(
            "13. saklanan source_evidence kaynakta "
            "GERCEKTEN var",
            amended is not None
            and contains_exact(
                AMENDMENT_TEXT,
                amended.source_evidence,
            ),
        )

        check(
            "15. dogrulanan reference_evidence referansta "
            "GERCEKTEN var",
            amended is not None
            and contains_exact(
                REFERENCE_TEXT,
                amended.reference_evidence,
            ),
        )

        check(
            "13. bosluk normalizasyonu satir sonunu "
            "esitliyor",
            normalize_for_match("otuz   gün\n\nibaresi")
            == "otuz gün ibaresi",
        )

        check(
            "13. tipografik tirnak normalize ediliyor",
            contains_exact(
                'ibaresi "otuz gün" olarak',
                "“otuz gün”",
            ),
        )

        check(
            "13. ANLAM benzerligi exact yerine GECMIYOR",
            not contains_exact(
                "Lisans başvuruları otuz gün içerisinde",
                "başvurular 30 gün içinde",
            ),
        )

        # Yeni bir change uzerinde kotu cevaplar denenir.
        bad_change = build_change(
            regulation_monitor,
            AMENDMENT_TEXT,
        )

        bad_analysis = build_analysis(bad_change)

        NEXT_RESPONSE["payload"] = response_payload(
            [
                {
                    # 12. uydurma candidate_id
                    "candidate_id": "seg-999-uydurma",
                    "affected_article": "Madde 99",
                    "change_type": "amended",
                    "old_text": "otuz gün",
                    "new_text": "on beş gün",
                    "source_evidence": "otuz gün",
                    "reference_evidence": None,
                    "change_summary": "uydurma aday",
                    "confidence": 0.99,
                },
                {
                    # 14. kaynakta OLMAYAN source_evidence
                    "candidate_id": "seg-2",
                    "affected_article": "Madde 12",
                    "change_type": "amended",
                    "old_text": "otuz gün",
                    "new_text": "on beş gün",
                    "source_evidence": (
                        "Bu cümle kaynak metinde hiç geçmiyor."
                    ),
                    "reference_evidence": None,
                    "change_summary": "uydurma kanit",
                    "confidence": 0.99,
                },
                {
                    # 16. referansta OLMAYAN old_text
                    # + uydurma reference_evidence
                    "candidate_id": "seg-2",
                    "affected_article": "Madde 12",
                    "change_type": "amended",
                    "old_text": "kırk beş gün",
                    "new_text": "on beş gün",
                    "source_evidence": (
                        'ibaresi "on beş gün" şeklinde'
                    ),
                    "reference_evidence": (
                        "Bu alıntı referansta yok."
                    ),
                    "change_summary": "uydurma old_text",
                    "confidence": 0.99,
                },
            ]
        )

        GEMINI_CALLS.clear()

        bad_outcome = run(
            regulation_monitor,
            FakeRunResult(
                change_record=bad_change,
                ai_analysis_record=bad_analysis,
            ),
        )

        bad_items = (
            bad_outcome.comparison.items
            if bad_outcome.comparison is not None
            else []
        )

        check(
            "12+14. uydurma candidate_id ve uydurma "
            "kanit REDDEDILDI",
            len(bad_items) == 1,
            f"kabul edilen={len(bad_items)}",
        )

        surviving = bad_items[0] if bad_items else None

        check(
            "16. referansta bulunmayan old_text SAKLANMADI",
            surviving is not None
            and surviving.old_text is None,
            f"old_text={surviving.old_text if surviving else None}",
        )

        check(
            "15. referansta bulunmayan reference_evidence "
            "SAKLANMADI",
            surviving is not None
            and surviving.reference_evidence is None,
        )

        check(
            "14. dogrulanan new_text korundu",
            surviving is not None
            and surviving.new_text == "on beş gün",
            f"new_text={surviving.new_text if surviving else None}",
        )

        check(
            "uydurma metin veritabanina HIC yazilmadi",
            session.scalar(
                select(func.count())
                .select_from(RegulationComparisonItem)
                .where(
                    RegulationComparisonItem.old_text
                    == "kırk beş gün"
                )
            ) == 0,
        )

        # -------------------------------------------------
        # 20. ADAY YOK -> NO_MATCH
        # -------------------------------------------------

        print("\n--- 20. aday yok -> no_match ---")

        empty_reference = create_regulation_reference(
            db=session,
            title="Boş Referans",
            source_url="https://mevzuat.test.local/bos-referans",
            content="   ",
        )

        empty_monitor = build_monitor(
            "Karsilastirma Bos Referans",
            "regulation_monitor",
            empty_reference.id,
        )

        empty_change = build_change(
            empty_monitor,
            AMENDMENT_TEXT,
        )

        empty_analysis = build_analysis(empty_change)

        GEMINI_CALLS.clear()

        empty_outcome = run(
            empty_monitor,
            FakeRunResult(
                change_record=empty_change,
                ai_analysis_record=empty_analysis,
            ),
        )

        check(
            "20. aday bulunamayinca durum no_match",
            empty_outcome.status == COMPARISON_NO_MATCH,
            f"durum={empty_outcome.status}",
        )

        check(
            "20. aday yokken Gemini CAGIRILMADI",
            len(GEMINI_CALLS) == 0,
            f"cagri={len(GEMINI_CALLS)}",
        )

        check(
            "20. no_match kaydi olustu (izi kayboldu degil)",
            empty_outcome.comparison is not None
            and empty_outcome.comparison.status
            == COMPARISON_NO_MATCH,
        )

        check(
            "20. no_match kaydinda uydurma item YOK",
            empty_outcome.comparison is not None
            and len(empty_outcome.comparison.items) == 0,
        )

        # Model "degisiklik var" dese bile dogrulanmis item
        # yoksa sonuc no_match olmalidir.
        all_rejected_change = build_change(
            regulation_monitor,
            AMENDMENT_TEXT,
        )

        all_rejected_analysis = build_analysis(
            all_rejected_change
        )

        NEXT_RESPONSE["payload"] = response_payload(
            [
                {
                    "candidate_id": "seg-2",
                    "affected_article": "Madde 12",
                    "change_type": "amended",
                    "old_text": "uydurma",
                    "new_text": "uydurma",
                    "source_evidence": "tamamen uydurma cumle",
                    "reference_evidence": None,
                    "change_summary": "x",
                    "confidence": 0.99,
                }
            ]
        )

        all_rejected = run(
            regulation_monitor,
            FakeRunResult(
                change_record=all_rejected_change,
                ai_analysis_record=all_rejected_analysis,
            ),
        )

        check(
            "20. tum item'lar reddedilirse sonuc no_match",
            all_rejected.status == COMPARISON_NO_MATCH,
            f"durum={all_rejected.status}",
        )

        check(
            "20. model 'degisiklik var' dese bile "
            "dogrulanmamis sonuc COMPLETED sayilmiyor",
            all_rejected.comparison is not None
            and len(all_rejected.comparison.items) == 0,
        )

        # -------------------------------------------------
        # 21-22. AI HATASI
        # -------------------------------------------------

        print("\n--- 21-22. AI hatasi ---")

        failing_change = build_change(
            regulation_monitor,
            AMENDMENT_TEXT,
        )

        failing_analysis = build_analysis(failing_change)

        snapshot_count_before = session.scalar(
            select(func.count()).select_from(Snapshot)
        )

        change_count_before = session.scalar(
            select(func.count()).select_from(Change)
        )

        analysis_count_before = session.scalar(
            select(func.count()).select_from(AIAnalysis)
        )

        NEXT_RESPONSE["error"] = RuntimeError(
            "Gemini gecici olarak kullanilamiyor"
        )

        failing_outcome = run(
            regulation_monitor,
            FakeRunResult(
                change_record=failing_change,
                ai_analysis_record=failing_analysis,
            ),
        )

        NEXT_RESPONSE["error"] = None

        check(
            "21. AI hatasi failed satiri uretti "
            "(pipeline cokmedi)",
            failing_outcome.status == COMPARISON_FAILED,
            f"durum={failing_outcome.status}",
        )

        check(
            "21. failed satiri veritabanina yazildi",
            failing_outcome.comparison is not None
            and failing_outcome.comparison.status
            == COMPARISON_FAILED,
        )

        error_message = (
            failing_outcome.comparison.error_message
            if failing_outcome.comparison is not None
            else ""
        )

        check(
            "21. hata mesaji saklandi",
            bool(error_message),
            f"mesaj={error_message}",
        )

        check(
            "21. hata mesaji TEMIZLENMIS "
            "(stack trace yok)",
            "Traceback" not in error_message
            and "File \"" not in error_message
            and len(error_message) <= 300,
            f"uzunluk={len(error_message)}",
        )

        check(
            "22. hata snapshot kayitlarini GERI ALMADI",
            session.scalar(
                select(func.count()).select_from(Snapshot)
            ) == snapshot_count_before,
        )

        check(
            "22. hata change kayitlarini GERI ALMADI",
            session.scalar(
                select(func.count()).select_from(Change)
            ) == change_count_before,
        )

        check(
            "22. hata AIAnalysis kayitlarini GERI ALMADI",
            session.scalar(
                select(func.count()).select_from(AIAnalysis)
            ) == analysis_count_before,
        )

        check(
            "22. failed durumunda item yazilmadi",
            failing_outcome.comparison is not None
            and len(failing_outcome.comparison.items) == 0,
        )

        check(
            "22. failed kaydi da referans surumunu tasiyor",
            failing_outcome.comparison is not None
            and failing_outcome.comparison.reference_content_hash
            == reference.content_hash,
        )

    finally:
        session.close()
        transaction.rollback()
        connection.close()

        genai.Client = real_client

        print("\n" + "-" * 70)
        print("Dis transaction geri alindi.")

    # -----------------------------------------------------
    # KALICI KAYIT BIRAKILMADI MI
    # -----------------------------------------------------

    from app.core.database import SessionLocal

    verify = SessionLocal()

    try:
        comparison_count_after = verify.scalar(
            select(func.count()).select_from(RegulationComparison)
        )

        item_count_after = verify.scalar(
            select(func.count())
            .select_from(RegulationComparisonItem)
        )

        check(
            "veritabaninda karsilastirma sayisi degismedi",
            comparison_count_after == comparison_count_before,
            f"once={comparison_count_before} "
            f"sonra={comparison_count_after}",
        )

        check(
            "test madde satiri veritabaninda kalmadi",
            item_count_after == 0,
            f"kalan={item_count_after}",
        )

    finally:
        verify.close()

    print("\n" + "=" * 70)
    print(f"BASARILI : {len(PASSED)}")
    print(f"BASARISIZ: {len(FAILED)}")
    print(f"Toplam sahte Gemini cagrisi: {len(GEMINI_CALLS)}")

    if FAILED:
        print("-" * 70)
        for line in FAILED:
            print(line)

        raise SystemExit(1)

    print("TUM TESTLER BASARILI")
    print("=" * 70)


if __name__ == "__main__":
    main()
