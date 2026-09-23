"""
MEVZUAT KARSILASTIRMA KABUL TESTI

Sprint sartnamesindeki senaryoyu uctan uca dogrular:

    Referans:
        MADDE 12
        Lisans başvuruları otuz gün içerisinde değerlendirilir.

    Kaynak degisiklik:
        MADDE 1- Aynı Yönetmeliğin 12 nci maddesinde yer alan
        "otuz gün" ibaresi "on beş gün" şeklinde
        değiştirilmiştir.

    Beklenen:
        affected_article  = Madde 12
        change_type       = amended
        old_text          = "otuz gün"      (referansta exact)
        new_text          = "on beş gün"    (kaynakta exact)
        source_evidence   = kaynakta exact
        reference_evidence= referansta exact

MOD: FIXTURE

Fixture metinleri GERCEK MEVZUAT DEGILDIR; resmi mevzuat
diline benzetilmis TEST VERISIDIR.

Neden fixture:

  1. Turk resmi kaynaklarina (mevzuat.gov.tr,
     resmigazete.gov.tr) bu calisma ortamindan
     BAGLANILAMIYOR. Sprint 1 kabul testinde de ayni
     sonuc alinmisti.

  2. Karsilastirma adimi icin GERCEK Gemini cagrisi
     BILEREK yapilmaz; kota gereksiz tuketilmez. Model
     cevabi sahte istemciyle verilir, ancak SUNUCU
     TARAFI DOGRULAMA gercek koddur ve gercekten calisir.

Yani dogrulanan sey sudur: gercekci bir Turkce mevzuat
degisikligi verildiginde parser, aday secimi, exact
evidence dogrulamasi ve kalicilik dogru davraniyor mu.

Gercek Gemini cagrisi : YOK
Gercek SMTP cagrisi   : YOK
Kalici DB kaydi       : YOK (rollback)

Calistirma:
    python -m tests.acceptance_regulation_comparison
"""

import asyncio
import json
import smtplib
import sys
import warnings

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database import engine
from app.models.regulation_comparison import (
    RegulationComparison,
)
from app.repositories.ai_analysis_repository import (
    create_ai_analysis,
)
from app.repositories.change_repository import create_change
from app.repositories.monitor_repository import create_monitor
from app.repositories.regulation_reference_repository import (
    create_regulation_reference,
)
from app.repositories.snapshot_repository import create_snapshot
from app.services.regulation_candidate_matcher import (
    select_candidates,
)
from app.services.regulation_comparator import contains_exact
from app.services.regulation_monitor_service import (
    COMPARISON_COMPLETED,
    run_regulation_comparison,
)
from app.services.regulation_text_parser import (
    parse_regulation_segments,
)


warnings.filterwarnings("ignore")


# ---------------------------------------------------------
# FIXTURE
#
# TEST VERISIDIR. Gercek bir yonetmelik metni degildir.
# ---------------------------------------------------------

REFERENCE_TITLE = (
    "[TEST VERİSİ] Elektrik Piyasası Lisans Yönetmeliği"
)

REFERENCE_TEXT = """BİRİNCİ BÖLÜM
Amaç, Kapsam, Dayanak ve Tanımlar
MADDE 1 - Bu Yönetmeliğin amacı, elektrik piyasasında faaliyet
gösterecek tüzel kişilerin lisans alma işlemlerine ilişkin usul
ve esasları düzenlemektir.
MADDE 5 - Lisans başvurusunda bulunacak tüzel kişiler, Kurul
tarafından belirlenen belgeleri Kuruma sunar.
İKİNCİ BÖLÜM
Lisans Başvurularının Değerlendirilmesi
MADDE 12 - Lisans başvuruları otuz gün içerisinde değerlendirilir.
MADDE 18 - Lisans sahibi tüzel kişiler, faaliyetlerine ilişkin
denetim raporlarını Kuruma sunmakla yükümlüdür.
MADDE 25 - Lisans bedelleri her yıl Kurul kararı ile yeniden
belirlenir.
"""

AMENDMENT_TITLE = (
    "[TEST VERİSİ] Elektrik Piyasası Lisans Yönetmeliğinde "
    "Değişiklik Yapılmasına Dair Yönetmelik"
)

AMENDMENT_TEXT = """Elektrik Piyasası Lisans Yönetmeliğinde Değişiklik Yapılmasına Dair Yönetmelik
MADDE 1- Aynı Yönetmeliğin 12 nci maddesinde yer alan "otuz gün"
ibaresi "on beş gün" şeklinde değiştirilmiştir.
MADDE 2- Bu Yönetmelik yayımı tarihinde yürürlüğe girer.
"""


# Modelin dondurecegi cevap.
# Gercek Gemini cagrilmaz; ancak asagidaki her kanit
# SUNUCU TARAFINDA gercekten dogrulanir.
MODEL_PAYLOAD = json.dumps(
    {
        "has_regulatory_change": True,
        "overall_summary": (
            "Lisans başvurularının değerlendirme süresi "
            "otuz günden on beş güne indirilmiştir."
        ),
        "importance": "high",
        "items": [
            {
                "candidate_id": "seg-3",
                "affected_article": "Madde 12",
                "affected_paragraph": None,
                "change_type": "amended",
                "old_text": "otuz gün",
                "new_text": "on beş gün",
                "source_evidence": (
                    '"otuz gün"\nibaresi "on beş gün" '
                    "şeklinde değiştirilmiştir."
                ),
                "reference_evidence": (
                    "Lisans başvuruları otuz gün içerisinde "
                    "değerlendirilir."
                ),
                "change_summary": (
                    "Değerlendirme süresi otuz günden on beş "
                    "güne indirildi."
                ),
                "confidence": 0.96,
            }
        ],
    },
    ensure_ascii=False,
)


GEMINI_CALLS = []

SMTP_ATTEMPTS = []

_real_smtp = smtplib.SMTP
_real_smtp_ssl = smtplib.SMTP_SSL


class BlockedSMTP:
    def __init__(self, *args, **kwargs):
        SMTP_ATTEMPTS.append(args)

        raise AssertionError(
            "Kabul testinde gercek SMTP baglantisi denendi."
        )


class FakeResponse:
    def __init__(self, text):
        self.text = text


class FakeModels:
    async def generate_content(self, model, contents, config):
        GEMINI_CALLS.append(
            {"model": model, "prompt": contents}
        )

        return FakeResponse(MODEL_PAYLOAD)


class FakeAio:
    def __init__(self):
        self.models = FakeModels()

    async def aclose(self):
        return None


class FakeGeminiClient:
    def __init__(self, *args, **kwargs):
        self.aio = FakeAio()


class FakeRunResult:
    def __init__(self, change, analysis):
        self.first_run = False
        self.changed = True
        self.change_record = change
        self.ai_analysis_record = analysis


def line(char: str = "-") -> None:
    print(char * 70)


def main() -> None:

    print("=" * 70)
    print("MEVZUAT KARSILASTIRMA KABUL TESTI")
    print("=" * 70)
    print("MOD                   : FIXTURE")
    print("Gercek Gemini cagrisi : YOK")
    print("Gercek SMTP cagrisi   : YOK")
    print("Kalici DB kaydi       : YOK (rollback)")
    line()

    problems = []

    from google import genai

    real_client = genai.Client

    genai.Client = FakeGeminiClient
    smtplib.SMTP = BlockedSMTP
    smtplib.SMTP_SSL = BlockedSMTP

    # -----------------------------------------------------
    # 1. YEREL SEGMENTASYON (Gemini'siz)
    # -----------------------------------------------------

    document = parse_regulation_segments(REFERENCE_TEXT)

    print("YEREL SEGMENTASYON")
    print(f"  fallback kullanildi : {document.used_fallback}")
    print(f"  bulunan madde       : {document.article_count}")

    for segment in document.segments:
        print(
            f"    {segment.segment_id:8s} "
            f"{segment.article_label}"
        )

    if document.used_fallback:
        problems.append(
            "MADDE basliklari taninmadi, fallback'e dusuldu"
        )

    line()

    # -----------------------------------------------------
    # 2. ADAY SECIMI (Gemini'siz)
    # -----------------------------------------------------

    candidates = select_candidates(
        source_text=AMENDMENT_TEXT,
        segments=document.segments,
    )

    print("ADAY SECIMI")

    for candidate in candidates:
        print(
            f"  {candidate.candidate_id:8s} "
            f"{str(candidate.article_label):16s} "
            f"{candidate.reason:20s} "
            f"skor={candidate.score:.3f}"
        )

    if not candidates:
        problems.append("hic aday secilemedi")

    elif candidates[0].article_label != "Madde 12":
        problems.append(
            "ilk aday Madde 12 degil: "
            f"{candidates[0].article_label}"
        )

    line()

    # -----------------------------------------------------
    # 3. UCTAN UCA KARSILASTIRMA
    # -----------------------------------------------------

    connection = engine.connect()
    transaction = connection.begin()

    session = Session(
        bind=connection,
        join_transaction_mode="create_savepoint",
    )

    count_before = session.scalar(
        select(func.count()).select_from(RegulationComparison)
    )

    try:
        reference = create_regulation_reference(
            db=session,
            title=REFERENCE_TITLE,
            source_url=(
                "https://mevzuat.test.local/kabul-testi"
            ),
            content=REFERENCE_TEXT,
        )

        monitor = create_monitor(
            db=session,
            name="[KABUL TESTİ] Resmî Gazete Enerji Takibi",
            url="https://example.com/resmi-gazete",
            topic=(
                "Enerji, elektrik piyasası ve lisans "
                "yönetmelikleri"
            ),
            tracking_plan="regulation_monitor",
            reference_regulation_id=reference.id,
            scope_type="full_page",
            schedule_time="09:00",
        )

        old_snapshot = create_snapshot(
            session,
            monitor.id,
            "Resmî Gazete",
            "önceki yayımlar",
        )

        new_snapshot = create_snapshot(
            session,
            monitor.id,
            "Resmî Gazete",
            "önceki yayımlar\n" + AMENDMENT_TEXT,
        )

        change = create_change(
            db=session,
            monitor_id=monitor.id,
            old_snapshot_id=old_snapshot.id,
            new_snapshot_id=new_snapshot.id,
            relevant=True,
            relevance_score=0.92,
            added_lines=AMENDMENT_TEXT.splitlines(),
            removed_lines=[],
        )

        analysis = create_ai_analysis(
            db=session,
            change_id=change.id,
            model_name="acceptance-fake",
            keyword_relevant=True,
            keyword_score=0.92,
            relevant=True,
            importance="high",
            category="policy",
            summary=(
                "Elektrik piyasası lisans yönetmeliğinde "
                "değişiklik yayımlandı."
            ),
            notification_recommended=True,
        )

        outcome = asyncio.run(
            run_regulation_comparison(
                db=session,
                monitor=monitor,
                result=FakeRunResult(change, analysis),
            )
        )

        print("KARSILASTIRMA SONUCU")
        print(f"  durum          : {outcome.status}")
        print(f"  Gemini cagrisi : {len(GEMINI_CALLS)}")
        print(f"  item sayisi    : {outcome.item_count}")

        comparison = outcome.comparison

        if outcome.status != COMPARISON_COMPLETED:
            problems.append(
                f"durum completed degil: {outcome.status}"
            )

        if len(GEMINI_CALLS) != 1:
            problems.append(
                "bir mantiksal karsilastirma icin "
                f"{len(GEMINI_CALLS)} cagri yapildi"
            )

        if comparison is None or not comparison.items:
            problems.append("dogrulanmis item uretilemedi")

        else:
            print(
                f"  ozet           : "
                f"{comparison.overall_summary}"
            )
            print(
                f"  onem           : {comparison.importance}"
            )
            print(
                f"  referans hash  : "
                f"{comparison.reference_content_hash[:16]}..."
            )

            line()

            item = comparison.items[0]

            print("BEKLENEN / BULUNAN")
            print(
                f"  affected_article  : Madde 12 / "
                f"{item.affected_article}"
            )
            print(
                f"  change_type       : amended / "
                f"{item.change_type}"
            )
            print(
                f"  old_text          : 'otuz gün' / "
                f"{item.old_text!r}"
            )
            print(
                f"  new_text          : 'on beş gün' / "
                f"{item.new_text!r}"
            )
            print(
                f"  source_evidence   : {item.source_evidence!r}"
            )
            print(
                f"  reference_evidence: "
                f"{item.reference_evidence!r}"
            )
            print(f"  confidence        : {item.confidence}")

            if item.affected_article != "Madde 12":
                problems.append(
                    "affected_article beklenen degil: "
                    f"{item.affected_article}"
                )

            if item.change_type != "amended":
                problems.append(
                    f"change_type beklenen degil: "
                    f"{item.change_type}"
                )

            if item.old_text != "otuz gün":
                problems.append(
                    f"old_text beklenen degil: {item.old_text!r}"
                )

            if item.new_text != "on beş gün":
                problems.append(
                    f"new_text beklenen degil: {item.new_text!r}"
                )

            # SUNUCU TARAFI DOGRULAMANIN GERCEKTEN
            # CALISTIGININ BAGIMSIZ KONTROLU
            line()
            print("BAGIMSIZ EXACT DOGRULAMA")

            checks = [
                (
                    "old_text referansta birebir var",
                    contains_exact(
                        REFERENCE_TEXT, item.old_text
                    ),
                ),
                (
                    "new_text kaynakta birebir var",
                    contains_exact(
                        AMENDMENT_TEXT, item.new_text
                    ),
                ),
                (
                    "source_evidence kaynakta birebir var",
                    contains_exact(
                        AMENDMENT_TEXT, item.source_evidence
                    ),
                ),
                (
                    "reference_evidence referansta birebir var",
                    contains_exact(
                        REFERENCE_TEXT,
                        item.reference_evidence,
                    ),
                ),
            ]

            for label, ok in checks:
                print(f"  [{'OK ' if ok else 'HATA'}] {label}")

                if not ok:
                    problems.append(label + " -> DOGRULANAMADI")

            # Prompt tam referansi tasimamali.
            prompt = GEMINI_CALLS[0]["prompt"]

            print()
            print(f"  prompt uzunlugu   : {len(prompt)}")
            print(
                f"  referans uzunlugu : {len(REFERENCE_TEXT)}"
            )

            # -------------------------------------------------
            # DUPLICATE
            # -------------------------------------------------

            GEMINI_CALLS.clear()

            repeat = asyncio.run(
                run_regulation_comparison(
                    db=session,
                    monitor=monitor,
                    result=FakeRunResult(change, analysis),
                )
            )

            line()
            print("TEKRAR CALISTIRMA")
            print(f"  durum        : {repeat.status}")
            print(
                f"  yeniden kullanildi : "
                f"{repeat.reused_existing}"
            )
            print(f"  ek Gemini cagrisi  : {len(GEMINI_CALLS)}")

            if GEMINI_CALLS:
                problems.append(
                    "tekrar calistirmada ek Gemini cagrisi "
                    "yapildi"
                )

    finally:
        session.close()
        transaction.rollback()
        connection.close()

        genai.Client = real_client
        smtplib.SMTP = _real_smtp
        smtplib.SMTP_SSL = _real_smtp_ssl

    # -----------------------------------------------------
    # KALICI KAYIT BIRAKILMADI MI
    # -----------------------------------------------------

    from app.core.database import SessionLocal

    verify = SessionLocal()

    try:
        count_after = verify.scalar(
            select(func.count()).select_from(RegulationComparison)
        )

    finally:
        verify.close()

    line()
    print(f"Karsilastirma sayisi once : {count_before}")
    print(f"Karsilastirma sayisi sonra: {count_after}")

    if count_after != count_before:
        problems.append(
            "veritabaninda kalici kayit birakildi"
        )

    if SMTP_ATTEMPTS:
        problems.append(
            f"gercek SMTP baglantisi denendi: {SMTP_ATTEMPTS}"
        )

    print(f"Gercek SMTP cagrisi       : {len(SMTP_ATTEMPTS)}")

    line("=")
    print("KABUL TESTI MODU : FIXTURE")
    print(
        "Fixture metinleri gercek mevzuat DEGILDIR; "
        "resmi mevzuat diline benzetilmis test verisidir."
    )

    if problems:
        print("\nSORUNLAR:")

        for problem in problems:
            print(f"  - {problem}")

        print("\nKABUL TESTI BASARISIZ")
        line("=")

        sys.exit(1)

    print("\nKABUL TESTI BASARILI")
    line("=")


if __name__ == "__main__":
    main()
