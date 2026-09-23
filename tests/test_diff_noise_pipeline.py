"""
DIFF GURULTU AZALTMA - PIPELINE ENTEGRASYONU

SINIF: automated (deterministic, CI-safe)

Azaltmanin pipeline'a dogru baglandigini dogrular:

  - Change kaydi TEMIZ diff'i tasir
  - Gemini TEMIZ diff'i gorur
  - exact evidence dogrulamasi ayni korpusta calisir
  - anlamli degisiklik kalmadiysa Gemini/bildirim/e-posta YOK
  - ham snapshot'lar DEGISMEZ
  - mevzuat monitoru azaltmadan ETKILENMEZ

Kapsam:

     1. yeniden siralama -> changed=False
     2. yeniden siralama -> Gemini 0 cagri
     3. yeniden siralama -> Change kaydi YOK
     4. yeniden siralama -> bildirim YOK
     5. yeniden siralama -> e-posta YOK
     6. yeniden siralama -> yeni snapshot YAZILIR (taban ilerler)
     7. ham snapshot icerigi DEGISTIRILMEZ
     8. gercek degisiklik -> normal akis calisir
     9. Change.added_content TEMIZ diff tasir
    10. Gemini prompt'u TEMIZ diff gorur
    11. Gemini prompt'u gurultu satirlarini GORMEZ
    12. exact evidence dogrulanir
    13. gurultuye dayali uydurma evidence REDDEDILIR
    14. bildirim detayi TEMIZ diff kullanir
    15. context blocklari ham snapshot'ta dogru konumlanir
    16. mevzuat monitorunde HAM diff korunur
    17. eski snapshot gecmisi DEGISMEZ

GERCEK GEMINI YOK. GERCEK SMTP YOK. GERCEK AG YOK.

Gercek PostgreSQL uzerinde calisir ama KALICI KAYIT
BIRAKMAZ.

Calistirma:
    python -m tests.test_diff_noise_pipeline
"""

import asyncio
import json
import warnings

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database import engine
from app.models.change import Change
from app.models.notification import Notification
from app.models.snapshot import Snapshot

from app.repositories.monitor_repository import create_monitor
from app.repositories.regulation_reference_repository import (
    create_regulation_reference,
)

from app.services import ai_analyzer
from app.services import db_monitor_service
from app.services.change_context_service import (
    build_context_blocks,
)
from app.services.monitor_run_service import execute_monitor_run

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
# SAHTE FETCH
# ---------------------------------------------------------

@dataclass
class FakeFetchResult:
    url: str
    final_url: str
    status_code: int
    content_type: str
    html: str


CURRENT_HTML = {"value": ""}


async def fake_fetch(url: str):
    return FakeFetchResult(
        url=url,
        final_url=url,
        status_code=200,
        content_type="text/html; charset=utf-8",
        html=CURRENT_HTML["value"],
    )


def page(lines: list[str]) -> str:

    body = NEWLINE.join(
        f"<p>{line}</p>" for line in lines
    )

    return (
        "<html><head><title>Haber</title></head>"
        f"<body><main>{body}</main></body></html>"
    )


# ---------------------------------------------------------
# SAHTE GEMINI
# ---------------------------------------------------------

GEMINI_PROMPTS = []

EVIDENCE = {"text": None, "type": "added"}


@dataclass
class FakeResponse:
    text: str


class FakeModels:

    async def generate_content(self, model, contents, config):

        GEMINI_PROMPTS.append(contents)

        return FakeResponse(
            text=json.dumps(
                {
                    "relevant": True,
                    "importance": "high",
                    "category": "news",
                    "summary": "Yeni bir haber eklendi.",
                    "notification_recommended": True,
                    "evidence_text": EVIDENCE["text"],
                    "evidence_type": EVIDENCE["type"],
                },
                ensure_ascii=False,
            )
        )


class FakeAio:

    def __init__(self):
        self.models = FakeModels()

    async def aclose(self):
        return None


class FakeGeminiClient:

    def __init__(self, *args, **kwargs):
        self.aio = FakeAio()


# ---------------------------------------------------------
# FIXTURE
# ---------------------------------------------------------

SOURCES_OLD = [
    "Business Insider",
    "The Information",
    "Security Affairs",
    "The Indian Express",
    "Digit",
    "TechCrunch",
]

SOURCES_NEW = [
    "Business Insider",
    "Security Affairs",
    "The Information",
    "Digit",
    "The Indian Express",
    "TechCrunch",
]


BIG_STORY = (
    "OpenAI says it is working on a framework for reporting "
    "misalignment incidents during training and deployment"
)


def make_monitor(session, name, plan="website_monitor", reference=None):
    return create_monitor(
        db=session,
        name=name,
        url="https://haber.test/ana-sayfa",
        topic="yapay zeka haberleri",
        tracking_plan=plan,
        reference_selection_mode=(
            "manual" if plan == "regulation_monitor" else "manual"
        ),
        reference_regulation_id=reference,
        scope_type="full_page",
        schedule_type="daily",
        schedule_time="09:00",
        notify_in_app=True,
        notify_email=False,
    )


def run(session, monitor, lines):
    CURRENT_HTML["value"] = page(lines)

    return asyncio.run(
        execute_monitor_run(db=session, monitor=monitor)
    )


def run_tests(session) -> None:

    # =====================================================
    # 1-7. YENIDEN SIRALAMA
    # =====================================================

    print()
    print("--- yalnizca yeniden siralama ---")

    monitor = make_monitor(session, "Siralama Testi")

    run(session, monitor, SOURCES_OLD)

    first_snapshot = session.scalars(
        select(Snapshot)
        .where(Snapshot.monitor_id == monitor.id)
        .order_by(Snapshot.id)
    ).first()

    original_content = first_snapshot.content

    changes_before = session.scalar(
        select(func.count())
        .select_from(Change)
        .where(Change.monitor_id == monitor.id)
    )

    notifications_before = session.scalar(
        select(func.count()).select_from(Notification)
    )

    GEMINI_PROMPTS.clear()

    outcome = run(session, monitor, SOURCES_NEW)

    check(
        "1. yeniden siralama -> changed=False",
        outcome.result.changed is False,
        f"changed={outcome.result.changed}",
    )

    check(
        "2. yeniden siralama -> Gemini 0 cagri",
        len(GEMINI_PROMPTS) == 0,
        f"cagri={len(GEMINI_PROMPTS)}",
    )

    check(
        "3. yeniden siralama -> Change kaydi YOK",
        session.scalar(
            select(func.count())
            .select_from(Change)
            .where(Change.monitor_id == monitor.id)
        )
        == changes_before,
        f"{changes_before}",
    )

    check(
        "4. yeniden siralama -> bildirim YOK",
        outcome.notification is None
        and session.scalar(
            select(func.count()).select_from(Notification)
        )
        == notifications_before,
    )

    check(
        "5. yeniden siralama -> e-posta YOK",
        outcome.email_notification is None,
    )

    snapshots = session.scalars(
        select(Snapshot)
        .where(Snapshot.monitor_id == monitor.id)
        .order_by(Snapshot.id)
    ).all()

    check(
        "6. yeniden siralama -> yeni snapshot YAZILIR",
        len(snapshots) == 2
        and outcome.result.new_snapshot is not None,
        f"snapshot={len(snapshots)}",
    )

    check(
        "7. ham snapshot icerigi DEGISTIRILMEZ",
        snapshots[0].content == original_content
        and "The Information" in snapshots[1].content,
        "ham gercek korunur",
    )

    # =====================================================
    # 8-13. GERCEK DEGISIKLIK
    # =====================================================

    print()
    print("--- gercek degisiklik ---")

    GEMINI_PROMPTS.clear()

    EVIDENCE["text"] = BIG_STORY
    EVIDENCE["type"] = "added"

    outcome = run(
        session,
        monitor,
        # Yeniden siralama DEVAM ediyor + gercek yeni haber.
        SOURCES_OLD + [BIG_STORY],
    )

    result = outcome.result

    check(
        "8. gercek degisiklik -> normal akis calisir",
        result.changed is True
        and result.change_record is not None
        and result.ai_analysis_record is not None,
        f"changed={result.changed}",
    )

    change = result.change_record

    added_content = change.added_content or ""

    check(
        "9. Change.added_content TEMIZ diff tasir",
        BIG_STORY in added_content
        and "Business Insider" not in added_content,
        f"{len(added_content.splitlines())} satir",
    )

    prompt = GEMINI_PROMPTS[0] if GEMINI_PROMPTS else ""

    check(
        "10. Gemini prompt'u TEMIZ diff gorur",
        BIG_STORY in prompt,
    )

    check(
        "11. Gemini prompt'u gurultu satirlarini GORMEZ",
        "Business Insider" not in prompt
        and "Security Affairs" not in prompt,
        "yeniden siralanan kaynak adlari prompt'a girmez",
    )

    analysis = result.ai_analysis_record

    check(
        "12. exact evidence dogrulanir",
        analysis.evidence_text is not None
        and analysis.evidence_text in added_content,
        f"{(analysis.evidence_text or '')[:50]}",
    )

    # Gurultu satirini evidence olarak vermeyi dene:
    # temiz korpusta olmadigi icin REDDEDILMELI.
    GEMINI_PROMPTS.clear()

    EVIDENCE["text"] = "Business Insider"
    EVIDENCE["type"] = "added"

    outcome = run(
        session,
        monitor,
        SOURCES_NEW + [BIG_STORY, "Ikinci gercek haber basligi"],
    )

    rejected_analysis = outcome.result.ai_analysis_record

    check(
        "13. gurultuye dayali uydurma evidence REDDEDILIR",
        rejected_analysis is not None
        and rejected_analysis.evidence_text is None,
        f"{rejected_analysis.evidence_text if rejected_analysis else None}",
    )

    # =====================================================
    # 14-15. BILDIRIM DETAYI
    # =====================================================

    print()
    print("--- bildirim detayi ---")

    latest_change = session.scalars(
        select(Change)
        .where(Change.monitor_id == monitor.id)
        .order_by(Change.id.desc())
    ).first()

    new_snapshot = session.get(
        Snapshot,
        latest_change.new_snapshot_id,
    )

    blocks = build_context_blocks(
        snapshot_content=new_snapshot.content,
        changed_content=latest_change.added_content,
        evidence_text=None,
    )

    check(
        "14. bildirim detayi TEMIZ diff kullanir",
        latest_change.added_content is not None
        and "Business Insider"
        not in latest_change.added_content,
        "yeniden siralanan satirlar 'eklendi' olarak gosterilmez",
    )

    snapshot_lines = new_snapshot.content.splitlines()

    located_ok = True

    for block in blocks:
        for line in block.lines:
            if line.changed:
                index = line.line_number - 1

                if not (
                    0 <= index < len(snapshot_lines)
                    and snapshot_lines[index] == line.text
                ):
                    located_ok = False

    check(
        "15. context blocklari ham snapshot'ta dogru konumlanir",
        blocks and located_ok,
        f"blok={len(blocks)}",
    )

    # =====================================================
    # 16. MEVZUAT MUAFIYETI
    # =====================================================

    print()
    print("--- mevzuat muafiyeti ---")

    reference = create_regulation_reference(
        db=session,
        title="Test Yonetmeligi",
        source_url="https://mevzuat.test.local/diff-testi",
        content="MADDE 1 - Test icerigi. " * 20,
    )

    regulation_monitor = make_monitor(
        session,
        "Mevzuat Diff Testi",
        plan="regulation_monitor",
        reference=reference.id,
    )

    run(session, regulation_monitor, SOURCES_OLD)

    GEMINI_PROMPTS.clear()

    EVIDENCE["text"] = None
    EVIDENCE["type"] = None

    outcome = run(session, regulation_monitor, SOURCES_NEW)

    # AZALTMANIN CALISMADIGINI KANITLAMA
    #
    # difflib'in hangi satirlari "replace" blokuna koydugu
    # hizalamaya baglidir; belirli bir satir adi aramak
    # kirilgan olurdu.
    #
    # Kanit su: azaltma calissaydi hem eklenenlerde hem
    # silinenlerde bulunan satirlar IPTAL EDILIRDI. Bu
    # kesisim BOS DEGILSE azaltma uygulanmamistir.
    regulation_change = outcome.result.change_record

    added_set = set(
        (regulation_change.added_content or "").splitlines()
    )

    removed_set = set(
        (regulation_change.removed_content or "").splitlines()
    )

    both_sides = added_set & removed_set

    check(
        "16. mevzuat monitorunde HAM diff korunur",
        outcome.result.changed is True
        and regulation_change is not None
        and bool(both_sides),
        f"iki tarafta da bulunan satirlar={sorted(both_sides)} "
        "(azaltma calissaydi bunlar iptal edilirdi)",
    )

    # =====================================================
    # 17. SNAPSHOT GECMISI
    # =====================================================

    print()
    print("--- snapshot gecmisi ---")

    all_snapshots = session.scalars(
        select(Snapshot)
        .where(Snapshot.monitor_id == monitor.id)
        .order_by(Snapshot.id)
    ).all()

    check(
        "17. eski snapshot gecmisi DEGISMEZ",
        all_snapshots[0].content == original_content,
        "ilk snapshot ham haliyle duruyor",
    )


def main() -> None:

    print("=" * 70)
    print("DIFF GURULTU AZALTMA - PIPELINE ENTEGRASYONU")
    print("=" * 70)

    original_fetch = db_monitor_service.fetch_website
    original_client = ai_analyzer.genai.Client

    db_monitor_service.fetch_website = fake_fetch
    ai_analyzer.genai.Client = FakeGeminiClient

    connection = engine.connect()
    transaction = connection.begin()

    session = Session(
        bind=connection,
        join_transaction_mode="create_savepoint",
    )

    snapshot_count_before = session.scalar(
        select(func.count()).select_from(Snapshot)
    )

    try:
        run_tests(session)

    finally:
        session.close()

        transaction.rollback()
        connection.close()

        db_monitor_service.fetch_website = original_fetch
        ai_analyzer.genai.Client = original_client

    with Session(bind=engine) as verify:
        snapshot_count_after = verify.scalar(
            select(func.count()).select_from(Snapshot)
        )

    check(
        "DB kirliligi YOK",
        snapshot_count_after == snapshot_count_before,
        f"{snapshot_count_before} -> {snapshot_count_after}",
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

    print("TUM TESTLER BASARILI")
    print("=" * 70)


if __name__ == "__main__":
    main()
