"""
AI evidence sozlesmesi ve sunucu tarafi dogrulama testleri.

GEMINI CAGRILMAZ. Yalnizca deterministic validate_evidence()
fonksiyonu ve persistence katmani test edilir.

Veritabani testi dis transaction icinde calisir ve sonunda
rollback edilir; kalici kayit birakilmaz.

Calistirma:
    python -m tests.test_ai_evidence
"""

from sqlalchemy import func, inspect, select
from sqlalchemy.orm import Session

from app.core.database import engine
from app.models.ai_analysis import AIAnalysis
from app.models.change import Change
from app.models.monitor import Monitor
from app.models.snapshot import Snapshot
from app.repositories.ai_analysis_repository import (
    create_ai_analysis,
    get_ai_analysis_by_change,
)
from app.services.ai_analyzer import (
    AIAnalysisResult,
    find_evidence_in_lines,
    validate_evidence,
)
from app.services.change_context_service import (
    build_context_blocks,
    is_evidence_line,
)


PASSED = []
FAILED = []


# Gercek acceptance'ta bulunan bug senaryosu.
# added_content'in ILK uygun satiri AnkiDroid, ama Gemini'nin
# bildirime sebep gosterdigi degisiklik ARC-AGI-1.

ANKIDROID_LINE = (
    "AnkiDroid: Google Play no longer allowing "
    "Open Collective donation link"
)

ARC_LINE = "ARC-AGI-1 benchmark reaches 44 percent at 67 cents"

ADDED_LINES = [
    ANKIDROID_LINE,
    "(",
    "github.com/ankidroid",
    ")",
    "160 points",
    ARC_LINE,
    "(",
    "mvakde.github.io",
    ")",
    "70 points",
]

REMOVED_LINES = [
    "246 points",
    "5 hours ago",
    "Eski model fiyati 100 dolar",
]


def check(label: str, condition: bool, detail: str = "") -> None:

    status = "PASS" if condition else "FAIL"

    print(f"[{status}] {label}" + (f" -> {detail}" if detail else ""))

    if condition:
        PASSED.append(label)
    else:
        FAILED.append(f"{label} -> {detail}")


def make_result(
    evidence_text=None,
    evidence_type=None,
    relevant=True,
) -> AIAnalysisResult:

    return AIAnalysisResult(
        relevant=relevant,
        importance="medium",
        category="model_update",
        summary="ARC-AGI-1 testinde yeni bir sonuç paylaşıldı.",
        notification_recommended=True,
        evidence_text=evidence_text,
        evidence_type=evidence_type,
    )


def validate(result) -> AIAnalysisResult:
    return validate_evidence(
        result=result,
        added_lines=ADDED_LINES,
        removed_lines=REMOVED_LINES,
    )


def main() -> None:

    print("=" * 70)
    print("AI EVIDENCE TESTLERI")
    print("=" * 70)

    # -----------------------------------------------------
    # 1. SEMA
    # -----------------------------------------------------

    print("\n--- 1. AI response semasi ---")

    fields = AIAnalysisResult.model_fields

    check(
        "AIAnalysisResult evidence_text iceriyor",
        "evidence_text" in fields,
    )

    check(
        "AIAnalysisResult evidence_type iceriyor",
        "evidence_type" in fields,
    )

    check(
        "mevcut alanlar korundu",
        {
            "relevant",
            "importance",
            "category",
            "summary",
            "notification_recommended",
        }
        <= set(fields),
    )

    check(
        "evidence alanlari opsiyonel (varsayilan None)",
        make_result().evidence_text is None
        and make_result().evidence_type is None,
    )

    from app.services.ai_analyzer import _build_prompt

    prompt = _build_prompt(
        topic="yapay zeka",
        added_lines=ADDED_LINES,
        removed_lines=REMOVED_LINES,
    )

    check(
        "prompt evidence talimati iceriyor",
        "evidence_text" in prompt and "evidence_type" in prompt,
    )

    check(
        "prompt birebir kopyalama sartini belirtiyor",
        "BİREBİR" in prompt,
    )

    check(
        "prompt ilk satiri degil en ilgili satiri istiyor",
        "İlk satırı değil" in prompt
        or "ilk satırı değil" in prompt.lower(),
    )

    check(
        "prompt relevant=false icin null izni veriyor",
        "relevant=false ise evidence_text ve evidence_type null"
        in prompt,
    )

    # -----------------------------------------------------
    # 2-3. GECERLI EVIDENCE KABUL
    # -----------------------------------------------------

    print("\n--- 2-3. gecerli evidence ---")

    result = validate(make_result(ARC_LINE, "added"))

    check(
        "added_content'te birebir olan evidence kabul ediliyor",
        result.evidence_text == ARC_LINE
        and result.evidence_type == "added",
        f"{result.evidence_text!r} / {result.evidence_type!r}",
    )

    result = validate(
        make_result("Eski model fiyati 100 dolar", "removed")
    )

    check(
        "removed_content'te birebir olan evidence kabul ediliyor",
        result.evidence_text == "Eski model fiyati 100 dolar"
        and result.evidence_type == "removed",
        f"{result.evidence_text!r}",
    )

    result = validate(make_result("ARC-AGI-1 benchmark", "added"))

    check(
        "satir icindeki alt dizge kabul ediliyor",
        result.evidence_text == "ARC-AGI-1 benchmark"
        and result.evidence_type == "added",
    )

    result = validate(make_result(f"  {ARC_LINE}  ", "added"))

    check(
        "bastaki/sondaki bosluklar temizleniyor",
        result.evidence_text == ARC_LINE,
        f"{result.evidence_text!r}",
    )

    # -----------------------------------------------------
    # 4. HALLUCINATED EVIDENCE REDDI
    # -----------------------------------------------------

    print("\n--- 4. uydurulmus evidence ---")

    hallucinations = [
        "Yeni bir yapay zeka modeli duyuruldu.",
        "ARC-AGI-1 testinde %44 başarı elde edildi",
        "ARC AGI 1 benchmark reaches 44 percent",
        "Model X fiyati dustu",
    ]

    for text in hallucinations:
        result = validate(make_result(text, "added"))

        check(
            f"uydurulmus evidence reddediliyor: {text[:38]!r}",
            result.evidence_text is None
            and result.evidence_type is None,
            f"{result.evidence_text!r}",
        )

    result = validate(
        make_result(f"{ANKIDROID_LINE} {ARC_LINE}", "added")
    )

    check(
        "iki satirin birlestirilmesi reddediliyor",
        result.evidence_text is None,
    )

    # -----------------------------------------------------
    # 5. YANLIS evidence_type REDDI
    # -----------------------------------------------------

    print("\n--- 5. yanlis evidence_type ---")

    result = validate(make_result(ARC_LINE, "removed"))

    check(
        "added metni removed olarak iddia edilirse reddediliyor",
        result.evidence_text is None
        and result.evidence_type is None,
    )

    result = validate(
        make_result("Eski model fiyati 100 dolar", "added")
    )

    check(
        "removed metni added olarak iddia edilirse reddediliyor",
        result.evidence_text is None
        and result.evidence_type is None,
    )

    result = validate(make_result(ARC_LINE, None))

    check(
        "evidence_type yoksa reddediliyor",
        result.evidence_text is None,
    )

    result = validate(make_result(None, "added"))

    check(
        "evidence_text yoksa type da temizleniyor",
        result.evidence_type is None,
    )

    for blank in ["", "   ", "\t"]:
        result = validate(make_result(blank, "added"))

        check(
            f"bos evidence reddediliyor: {blank!r}",
            result.evidence_text is None
            and result.evidence_type is None,
        )

    # -----------------------------------------------------
    # 6. EVIDENCE YOKSA ANALIZ YINE BASARILI
    # -----------------------------------------------------

    print("\n--- 6. evidence olmadan analiz ---")

    original = make_result(None, None)
    result = validate(original)

    check(
        "evidence yokken analiz alanlari korunuyor",
        result.relevant is True
        and result.importance == "medium"
        and result.category == "model_update"
        and result.summary == original.summary
        and result.notification_recommended is True,
    )

    rejected = validate(make_result("uydurma metin", "added"))

    check(
        "evidence reddedilse de analiz alanlari korunuyor",
        rejected.relevant is True
        and rejected.summary == original.summary
        and rejected.importance == "medium",
    )

    check(
        "relevant=false + evidence null gecerli",
        validate(
            make_result(None, None, relevant=False)
        ).relevant
        is False,
    )

    check(
        "orijinal nesne degistirilmiyor (model_copy)",
        make_result("uydurma", "added").evidence_text == "uydurma",
    )

    # -----------------------------------------------------
    # YARDIMCI FONKSIYON
    # -----------------------------------------------------

    print("\n--- find_evidence_in_lines ---")

    check(
        "bos satir listesinde bulunamaz",
        find_evidence_in_lines(ARC_LINE, []) is None,
    )

    check(
        "None evidence None doner",
        find_evidence_in_lines(None, ADDED_LINES) is None,
    )

    check(
        "bos satirlar atlanir",
        find_evidence_in_lines("", ["", "  ", ARC_LINE]) is None,
    )

    check(
        "satir sonu iceren evidence kabul edilmez",
        find_evidence_in_lines(
            f"{ANKIDROID_LINE}\n{ARC_LINE}",
            ADDED_LINES,
        )
        is None,
    )

    # -----------------------------------------------------
    # CONTEXT SERVISI EVIDENCE ISARETLEME
    # -----------------------------------------------------

    print("\n--- context servisi ---")

    check(
        "is_evidence_line dogru satiri buluyor",
        is_evidence_line(ARC_LINE, ARC_LINE)
        and not is_evidence_line(ANKIDROID_LINE, ARC_LINE),
    )

    check(
        "evidence yoksa hicbir satir isaretlenmiyor",
        not is_evidence_line(ARC_LINE, None)
        and not is_evidence_line(ARC_LINE, "   "),
    )

    snapshot = "\n".join(
        ["baslik", "nav"] + ADDED_LINES + ["altbilgi"]
    )

    blocks = build_context_blocks(
        snapshot_content=snapshot,
        changed_content="\n".join(ADDED_LINES),
        evidence_text=ARC_LINE,
    )

    evidence_lines = [
        line
        for block in blocks
        for line in block.lines
        if line.evidence
    ]

    check(
        "context blogunda tam olarak bir evidence satiri var",
        len(evidence_lines) == 1,
        f"adet={len(evidence_lines)}",
    )

    check(
        "isaretlenen satir dogru satir",
        evidence_lines and evidence_lines[0].text == ARC_LINE,
        evidence_lines[0].text if evidence_lines else "",
    )

    check(
        "AnkiDroid satiri evidence olarak isaretlenmedi",
        all(
            not line.evidence
            for block in blocks
            for line in block.lines
            if line.text == ANKIDROID_LINE
        ),
    )

    check(
        "evidence olmadan hicbir satir isaretlenmiyor",
        not any(
            line.evidence
            for block in build_context_blocks(
                snapshot_content=snapshot,
                changed_content="\n".join(ADDED_LINES),
            )
            for line in block.lines
        ),
    )

    # -----------------------------------------------------
    # 7. PERSISTENCE
    # -----------------------------------------------------

    print("\n--- 7. persistence ---")

    columns = {
        column["name"]: column
        for column in inspect(engine).get_columns("ai_analyses")
    }

    check(
        "ai_analyses.evidence_text kolonu var ve nullable",
        "evidence_text" in columns
        and columns["evidence_text"]["nullable"],
    )

    check(
        "ai_analyses.evidence_type kolonu var ve nullable",
        "evidence_type" in columns
        and columns["evidence_type"]["nullable"],
    )

    from app.core.database import SessionLocal

    baseline = SessionLocal()

    try:
        persistent_count = baseline.scalar(
            select(func.count()).select_from(AIAnalysis)
        )

        # Evidence sistemi devreye girmeden ONCE var olan kayitlar.
        # Bunlarin geriye donuk doldurulmadigi dogrulanacak.
        legacy_ids = [
            row.id
            for row in baseline.scalars(
                select(AIAnalysis).order_by(AIAnalysis.id)
            ).all()
        ][:4]
    finally:
        baseline.close()

    connection = engine.connect()
    transaction = connection.begin()

    session = Session(
        bind=connection,
        join_transaction_mode="create_savepoint",
    )

    try:
        count_before = session.scalar(
            select(func.count()).select_from(AIAnalysis)
        )

        monitor = session.scalars(
            select(Monitor).limit(1)
        ).first()

        old_snapshot = Snapshot(
            monitor_id=monitor.id,
            title="t",
            content="a",
            content_hash="h1",
        )

        new_snapshot = Snapshot(
            monitor_id=monitor.id,
            title="t",
            content="b",
            content_hash="h2",
        )

        session.add_all([old_snapshot, new_snapshot])
        session.flush()

        change = Change(
            monitor_id=monitor.id,
            old_snapshot_id=old_snapshot.id,
            new_snapshot_id=new_snapshot.id,
            relevant=True,
            relevance_score=1.0,
            added_content="\n".join(ADDED_LINES),
            removed_content="\n".join(REMOVED_LINES),
        )

        session.add(change)
        session.flush()

        stored = create_ai_analysis(
            db=session,
            change_id=change.id,
            model_name="test-model",
            keyword_relevant=True,
            keyword_score=1.0,
            relevant=True,
            importance="medium",
            category="model_update",
            summary="test",
            notification_recommended=True,
            evidence_text=ARC_LINE,
            evidence_type="added",
        )

        check(
            "repository evidence_text kaydediyor",
            stored.evidence_text == ARC_LINE,
            f"{stored.evidence_text!r}",
        )

        check(
            "repository evidence_type kaydediyor",
            stored.evidence_type == "added",
        )

        reloaded = get_ai_analysis_by_change(
            db=session,
            change_id=change.id,
        )

        check(
            "evidence DB'den geri okunabiliyor "
            "(tekrar Gemini gerekmiyor)",
            reloaded.evidence_text == ARC_LINE
            and reloaded.evidence_type == "added",
        )

        # change_id unique oldugu icin ikinci bir change gerekiyor.
        second_change = Change(
            monitor_id=monitor.id,
            old_snapshot_id=old_snapshot.id,
            new_snapshot_id=new_snapshot.id,
            relevant=False,
            relevance_score=0.0,
            added_content="baska bir degisiklik",
            removed_content=None,
        )

        session.add(second_change)
        session.flush()

        without = create_ai_analysis(
            db=session,
            change_id=second_change.id,
            model_name="test-model",
            keyword_relevant=True,
            keyword_score=1.0,
            relevant=True,
            importance="low",
            category="other",
            summary="test",
            notification_recommended=False,
        )

        check(
            "evidence parametreleri opsiyonel (varsayilan null)",
            without.evidence_text is None
            and without.evidence_type is None,
        )

        session.flush()

        count_after = session.scalar(
            select(func.count()).select_from(AIAnalysis)
        )

        check(
            "test icinde gecici kayit olustu",
            count_after > count_before,
            f"{count_before} -> {count_after}",
        )

    finally:
        session.close()
        transaction.rollback()
        connection.close()

    verify = SessionLocal()

    try:
        final_count = verify.scalar(
            select(func.count()).select_from(AIAnalysis)
        )

        check(
            "rollback sonrasi kalici kayit yok",
            final_count == persistent_count,
            f"once={persistent_count} sonra={final_count}",
        )

        legacy_with_evidence = [
            row.id
            for row in verify.scalars(
                select(AIAnalysis).where(
                    AIAnalysis.id.in_(legacy_ids)
                )
            ).all()
            if row.evidence_text is not None
        ]

        check(
            "eski kayitlar geriye donuk doldurulmadi "
            "(evidence null kaldi)",
            not legacy_with_evidence,
            f"dolduruldu={legacy_with_evidence}",
        )

    finally:
        verify.close()

    print("\n" + "=" * 70)
    print(f"BASARILI : {len(PASSED)}")
    print(f"BASARISIZ: {len(FAILED)}")

    if FAILED:
        print("-" * 70)
        for line in FAILED:
            print(line)

        raise SystemExit(1)

    print("TUM TESTLER BASARILI")
    print("=" * 70)


if __name__ == "__main__":
    main()
