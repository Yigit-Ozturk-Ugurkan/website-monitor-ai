"""
auto_section GERCEK KABUL TESTI.

DIKKAT: Bu dosya otomatik test paketinin parcasi DEGILDIR.
Adi bilerek "test_" ile baslamaz; suite ile birlikte
calistirilmaz ve tekrar tekrar calistirilmamalidir.

Gercek Gemini kotasini korumak icin:

- EN FAZLA 1 gercek bolum secimi (section discovery) cagrisi yapilir.
- Sonraki calistirma bu cagriyi TEKRARLAMAZ; bu zaten kanitlanmak
  istenen davranistir.
- Degisiklik analizi (analyze_change_with_ai) burada sahtelenir;
  o davranis tests/test_auto_section.py ve tests/test_gemini_fallback.py
  icinde zaten kapsanmistir.

Veritabani:
Tum islemler dis bir transaction icinde yapilir ve sonunda
ROLLBACK edilir. Gercek monitor kayitlari bozulmaz, cop monitor
birakilmaz.

Calistirma:
    python -m tests.acceptance_auto_section
"""

import asyncio

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database import SessionLocal, engine
from app.models.monitor import Monitor
from app.models.snapshot import Snapshot

from app.repositories.monitor_repository import create_monitor
from app.repositories.snapshot_repository import get_latest_snapshot

from app.services import db_monitor_service
from app.services.ai_analyzer import (
    AIAnalysisOutcome,
    AIAnalysisResult,
)
from app.services.content_extractor import extract_content
from app.services.db_monitor_service import run_database_monitor
from app.services.section_extractor import (
    extract_section_candidates,
    normalize_label,
    resolve_section,
)
from app.services.section_selector import select_section_with_ai
from app.services.web_fetcher import fetch_website


PASSED = []
FAILED = []


# Semantik bolumleri olan gercek bir site.
ACCEPTANCE_URL = "https://www.techmeme.com/"

# Sayfanin yalnizca BELIRLI bir bolumuyle eslesen bir konu.
# Boylece modelin en buyuk blogu degil, konuya uygun blogu
# sectigi gorulebilir.
ACCEPTANCE_TOPIC = (
    "yaklaşan teknoloji etkinlikleri, konferanslar ve "
    "fuar takvimi"
)

# Secilen bolumun konuyla ilgili oldugunu dogrulamak icin
# etikette aranan ipuclari.
EXPECTED_LABEL_HINTS = (
    "event",
    "etkinlik",
    "conference",
    "upcoming",
)


def check(label: str, condition: bool, detail: str = "") -> None:

    status = "PASS" if condition else "FAIL"

    print(f"[{status}] {label}" + (f" -> {detail}" if detail else ""))

    if condition:
        PASSED.append(label)
    else:
        FAILED.append(f"{label} -> {detail}")


# ---------------------------------------------------------
# SAHTE DEGISIKLIK ANALIZI
#
# Bu kabul testinin konusu bolum secimi. Degisiklik analizi
# kotasini bosuna harcamamak icin sahtelenir.
# ---------------------------------------------------------

ANALYSIS_CALLS = []


async def fake_analyze_change_with_ai(**kwargs):

    ANALYSIS_CALLS.append(kwargs)

    return AIAnalysisOutcome(
        result=AIAnalysisResult(
            relevant=False,
            importance="low",
            category="other",
            summary="Kabul testi sirasinda analiz sahtelendi.",
            notification_recommended=False,
            evidence_text=None,
            evidence_type=None,
        ),
        model_name="acceptance-stub",
        used_fallback=False,
    )


# ---------------------------------------------------------
# SAHTE FETCH
#
# Gercek HTML iki kez indirilir; pipeline'a hangi indirmenin
# verilecegi buradan kontrol edilir. Boylece pipeline ayni
# ag trafigini tekrarlamaz.
# ---------------------------------------------------------

@dataclass
class ReplayFetchResult:
    url: str
    final_url: str
    status_code: int
    content_type: str
    html: str


CURRENT_HTML = {"value": ""}


async def replay_fetch_website(url: str) -> ReplayFetchResult:

    return ReplayFetchResult(
        url=url,
        final_url=url,
        status_code=200,
        content_type="text/html",
        html=CURRENT_HTML["value"],
    )


# ---------------------------------------------------------
# BOLUM SECIMI SAYACI
#
# Gercek secim YALNIZCA BIR KEZ yapilir. Pipeline icinde
# secim yeniden istenirse bu sarmalayici hata firlatir.
# ---------------------------------------------------------

SELECTION_INVOCATIONS = []

REAL_SELECTION = {"outcome": None}


async def counted_select_section_with_ai(**kwargs):

    SELECTION_INVOCATIONS.append(kwargs)

    outcome = REAL_SELECTION["outcome"]

    if outcome is None:
        raise AssertionError(
            "Kabul testi ikinci kez gercek bolum secimi istedi."
        )

    # Zaten yapilmis GERCEK secim tekrar kullanilir;
    # ikinci bir Gemini cagrisi yapilmaz.
    candidates = kwargs["candidates"]

    match = next(
        (
            candidate
            for candidate in candidates
            if candidate.reference == outcome.candidate.reference
        ),
        None,
    )

    if match is None:
        raise AssertionError(
            "Gercek secim yeni aday listesinde bulunamadi."
        )

    return type(outcome)(
        candidate=match,
        reason=outcome.reason,
        confidence=outcome.confidence,
        model_name=outcome.model_name,
        used_fallback=outcome.used_fallback,
    )


def main() -> None:

    print("=" * 70)
    print("AUTO SECTION GERCEK KABUL TESTI")
    print(f"URL   : {ACCEPTANCE_URL}")
    print(f"KONU  : {ACCEPTANCE_TOPIC}")
    print("EN FAZLA 1 GERCEK GEMINI BOLUM SECIMI CAGRISI")
    print("=" * 70)

    # -----------------------------------------------------
    # 1. GERCEK SAYFA
    # -----------------------------------------------------

    print("\n--- 1. gercek sayfa indiriliyor ---")

    first_fetch = asyncio.run(
        fetch_website(ACCEPTANCE_URL)
    )

    full_content = extract_content(first_fetch.html)

    print(f"HTML uzunlugu      : {len(first_fetch.html)}")
    print(f"Tum sayfa metni    : {full_content.text_length}")
    print(f"Sayfa basligi      : {full_content.title}")

    check(
        "gercek sayfa indirildi",
        first_fetch.status_code == 200
        and len(first_fetch.html) > 1000,
    )

    # -----------------------------------------------------
    # 2. ADAY BOLUMLER (AI YOK)
    # -----------------------------------------------------

    print("\n--- 2. aday bolumler cikariliyor (AI cagrisi yok) ---")

    discovery = extract_section_candidates(first_fetch.html)

    for candidate in discovery.candidates:
        print(
            f"  {candidate.candidate_id:4} "
            f"{candidate.tag:8} "
            f"uzunluk={candidate.text_length:7} "
            f"etiket={candidate.label[:40]!r}"
        )

    check(
        "candidate sections bulundu",
        len(discovery.candidates) >= 3,
        f"aday={len(discovery.candidates)}",
    )

    check(
        "adaylar tum sayfadan kucuk",
        all(
            candidate.text_length < full_content.text_length
            for candidate in discovery.candidates
        ),
    )

    check(
        "her adayin yeniden bulunabilir referansi var",
        all(
            resolve_section(
                first_fetch.html,
                candidate.reference,
            ).section
            is not None
            for candidate in discovery.candidates
        ),
    )

    # -----------------------------------------------------
    # 3. TEK GERCEK GEMINI BOLUM SECIMI
    # -----------------------------------------------------

    print("\n--- 3. GERCEK Gemini bolum secimi (1 cagri) ---")

    outcome = asyncio.run(
        select_section_with_ai(
            topic=ACCEPTANCE_TOPIC,
            candidates=discovery.candidates,
            page_title=discovery.page_title,
        )
    )

    REAL_SELECTION["outcome"] = outcome

    selected = outcome.candidate

    print(f"Model              : {outcome.model_name}")
    print(f"Yedek model mi     : {outcome.used_fallback}")
    print(f"Secilen candidate  : {selected.candidate_id}")
    print(f"Secilen etiket     : {selected.label}")
    print(f"Uretilen referans  : {selected.reference}")
    print(f"Guven              : {outcome.confidence}")
    print(f"Gerekce            : {outcome.reason}")
    print(f"Secilen metin uzn. : {selected.text_length}")
    print()
    print("Secilen metin (ilk 300 karakter):")
    print("-" * 70)
    print(selected.text[:300])
    print("-" * 70)

    check(
        "Gemini gercekten bir aday secti",
        selected.candidate_id
        in {
            candidate.candidate_id
            for candidate in discovery.candidates
        },
        selected.candidate_id,
    )

    check(
        "secim konuyla ilgili bir bolum",
        any(
            hint in normalize_label(selected.label)
            for hint in EXPECTED_LABEL_HINTS
        ),
        f"{selected.label!r}",
    )

    check(
        "yeniden bulunabilir referans uretildi",
        bool(selected.reference) and ":" in selected.reference,
        selected.reference,
    )

    check(
        "referans kirilgan CSS zinciri degil",
        "nth-child" not in selected.reference
        and ">" not in selected.reference,
        selected.reference,
    )

    check(
        "secilen metin tum sayfadan KUCUK",
        selected.text_length < full_content.text_length,
        f"{selected.text_length} < {full_content.text_length}",
    )

    # -----------------------------------------------------
    # 4. AYNI REFERANS, YENI INDIRME, AI CAGRISI YOK
    # -----------------------------------------------------

    print("\n--- 4. sayfa yeniden indiriliyor (AI cagrisi yok) ---")

    second_fetch = asyncio.run(
        fetch_website(ACCEPTANCE_URL)
    )

    resolution = resolve_section(
        second_fetch.html,
        selected.reference,
    )

    check(
        "kaydedilen referans YENI indirmede de bulundu",
        resolution.section is not None,
        selected.reference,
    )

    if resolution.section is not None:

        second_full = extract_content(second_fetch.html)

        print(
            f"Yeni indirmede bolum uzunlugu: "
            f"{resolution.section.text_length}"
        )

        check(
            "yeniden cozumlenen bolum tum sayfadan KUCUK",
            resolution.section.text_length
            < second_full.text_length,
            f"{resolution.section.text_length} < "
            f"{second_full.text_length}",
        )

        check(
            "yeniden cozumlenen bolum ayni bolum",
            normalize_label(resolution.section.label)
            == normalize_label(selected.label),
            f"{resolution.section.label!r}",
        )

    check(
        "bu adimda hic Gemini cagrisi yapilmadi",
        len(SELECTION_INVOCATIONS) == 0,
    )

    # -----------------------------------------------------
    # 5. TAM PIPELINE (DB, ROLLBACK)
    # -----------------------------------------------------

    print("\n--- 5. tam pipeline (DB transaction, rollback) ---")

    original_fetch = db_monitor_service.fetch_website
    original_select = db_monitor_service.select_section_with_ai
    original_analyze = db_monitor_service.analyze_change_with_ai

    db_monitor_service.fetch_website = replay_fetch_website
    db_monitor_service.select_section_with_ai = (
        counted_select_section_with_ai
    )
    db_monitor_service.analyze_change_with_ai = (
        fake_analyze_change_with_ai
    )

    baseline = SessionLocal()

    try:
        monitors_before = baseline.scalar(
            select(func.count()).select_from(Monitor)
        )
        snapshots_before = baseline.scalar(
            select(func.count()).select_from(Snapshot)
        )
    finally:
        baseline.close()

    connection = engine.connect()
    transaction = connection.begin()

    session = Session(
        bind=connection,
        join_transaction_mode="create_savepoint",
    )

    try:
        monitor = create_monitor(
            db=session,
            name="auto_section kabul testi",
            url=ACCEPTANCE_URL,
            topic=ACCEPTANCE_TOPIC,
            scope_type="auto_section",
            schedule_type="daily",
            schedule_time="09:00",
        )

        # --- ILK RUN ---

        CURRENT_HTML["value"] = first_fetch.html

        first_result = asyncio.run(
            run_database_monitor(
                db=session,
                monitor=monitor,
            )
        )

        first_snapshot = get_latest_snapshot(
            db=session,
            monitor_id=monitor.id,
        )

        check(
            "ilk run: first_run=True changed=False",
            first_result.first_run is True
            and first_result.changed is False,
        )

        check(
            "ilk run: referans DB'ye kaydedildi",
            monitor.auto_section_reference == selected.reference,
            f"{monitor.auto_section_reference!r}",
        )

        check(
            "ilk run: etiket DB'ye kaydedildi",
            monitor.auto_section_label == selected.label,
            f"{monitor.auto_section_label!r}",
        )

        check(
            "ilk run: snapshot yalnizca secilen bolum",
            first_snapshot.content == selected.text,
        )

        check(
            "ilk run: snapshot tum sayfadan KUCUK",
            len(first_snapshot.content)
            < full_content.text_length,
            f"{len(first_snapshot.content)} < "
            f"{full_content.text_length}",
        )

        check(
            "ilk run: snapshot basligi sayfa basligi",
            first_snapshot.title == full_content.title,
            f"{first_snapshot.title!r}",
        )

        check(
            "ilk run: bolum secimi 1 kez istendi",
            len(SELECTION_INVOCATIONS) == 1,
            f"cagri={len(SELECTION_INVOCATIONS)}",
        )

        # --- IKINCI RUN: YENI INDIRME, AI YOK ---

        CURRENT_HTML["value"] = second_fetch.html

        second_result = asyncio.run(
            run_database_monitor(
                db=session,
                monitor=monitor,
            )
        )

        check(
            "ikinci run: bolum secimi AI'a HIC SORULMADI",
            len(SELECTION_INVOCATIONS) == 1,
            f"toplam cagri={len(SELECTION_INVOCATIONS)}",
        )

        check(
            "ikinci run: referans degismedi",
            monitor.auto_section_reference == selected.reference,
        )

        second_snapshot = get_latest_snapshot(
            db=session,
            monitor_id=monitor.id,
        )

        check(
            "ikinci run: izlenen icerik hala tum sayfa DEGIL",
            len(second_snapshot.content)
            < full_content.text_length,
            f"{len(second_snapshot.content)} < "
            f"{full_content.text_length}",
        )

        print(
            f"ikinci run changed = {second_result.changed} "
            f"(iki indirme arasinda bolum degistiyse True olabilir)"
        )

        session.flush()

    finally:
        session.close()
        transaction.rollback()
        connection.close()

        db_monitor_service.fetch_website = original_fetch
        db_monitor_service.select_section_with_ai = original_select
        db_monitor_service.analyze_change_with_ai = original_analyze

    # -----------------------------------------------------
    # 6. TEMIZLIK DOGRULAMASI
    # -----------------------------------------------------

    print("\n--- 6. cop kayit birakilmadi ---")

    verify = SessionLocal()

    try:
        monitors_after = verify.scalar(
            select(func.count()).select_from(Monitor)
        )
        snapshots_after = verify.scalar(
            select(func.count()).select_from(Snapshot)
        )
    finally:
        verify.close()

    check(
        "kabul testi monitoru DB'de birakilmadi",
        monitors_after == monitors_before,
        f"{monitors_before} -> {monitors_after}",
    )

    check(
        "kabul testi snapshotlari DB'de birakilmadi",
        snapshots_after == snapshots_before,
        f"{snapshots_before} -> {snapshots_after}",
    )

    # -----------------------------------------------------
    # SONUC
    # -----------------------------------------------------

    print("\n" + "=" * 70)
    print(f"GERCEK GEMINI BOLUM SECIMI CAGRISI : 1")
    print(f"GERCEK DEGISIKLIK ANALIZI CAGRISI  : 0 (sahtelendi)")
    print(f"BASARILI : {len(PASSED)}")
    print(f"BASARISIZ: {len(FAILED)}")

    if FAILED:
        print("-" * 70)
        for line in FAILED:
            print(line)

        raise SystemExit(1)

    print("KABUL TESTI BASARILI")
    print("=" * 70)


if __name__ == "__main__":
    main()
