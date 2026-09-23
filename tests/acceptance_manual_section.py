"""
manual_section GERCEK KABUL TESTI.

DIKKAT: Bu dosya otomatik test paketinin parcasi DEGILDIR.
Adi bilerek "test_" ile baslamaz; suite ile birlikte
calistirilmaz.

GEMINI CAGRISI GEREKMEZ VE YAPILMAZ.
Manuel bolum secimi tamamen local calisir; test suresince
Gemini istemcisi cagrildigi anda hata firlatan bir tuzakla
degistirilir.

Frontend'in attigi API cagrilari kullanilir:

    POST /api/monitors/section-candidates

Veritabani:
Monitor olusturma ve calistirma dis bir transaction icinde
yapilir ve sonunda ROLLBACK edilir. Cop monitor birakilmaz.

Calistirma:
    python -m tests.acceptance_manual_section
"""

import asyncio
import warnings

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database import SessionLocal, engine
from app.models.monitor import Monitor
from app.models.snapshot import Snapshot

from app.repositories.monitor_repository import create_monitor
from app.repositories.snapshot_repository import get_latest_snapshot

from app.services import ai_analyzer
from app.services import db_monitor_service
from app.services.content_extractor import extract_content
from app.services.db_monitor_service import run_database_monitor
from app.services.section_extractor import (
    normalize_label,
    resolve_section,
)
from app.services.web_fetcher import fetch_website

warnings.filterwarnings("ignore")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


PASSED = []
FAILED = []


# Semantik bolumleri olan gercek bir site.
ACCEPTANCE_URL = "https://www.techmeme.com/"

ACCEPTANCE_TOPIC = (
    "yaklaşan teknoloji etkinlikleri ve konferanslar"
)

# Kullanicinin listeden secmis olacagi bolum.
# Sayfanin en buyuk blogu DEGILDIR; boylece secimin
# gercekten kullaniciya ait oldugu gorulur.
TARGET_LABEL_HINT = "upcoming tech events"


def check(label: str, condition: bool, detail: str = "") -> None:

    status = "PASS" if condition else "FAIL"

    print(f"[{status}] {label}" + (f" -> {detail}" if detail else ""))

    if condition:
        PASSED.append(label)
    else:
        FAILED.append(f"{label} -> {detail}")


# ---------------------------------------------------------
# GEMINI TUZAGI
#
# Bu kabul testinde HICBIR Gemini cagrisi olmamalidir.
# ---------------------------------------------------------

GEMINI_CALLS = []


class TrapClient:
    def __init__(self):
        self.aio = self
        self.models = self

    async def generate_content(self, model, contents, config):
        GEMINI_CALLS.append(model)

        raise AssertionError(
            f"Kabul testinde Gemini cagrildi: {model}"
        )

    async def aclose(self):
        return None


# ---------------------------------------------------------
# INDIRILEN HTML'I PIPELINE'A VERME
#
# Sayfa iki kez GERCEKTEN indirilir; pipeline ayni
# trafigi tekrarlamasin diye indirilen HTML yeniden
# oynatilir.
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


def main() -> None:

    print("=" * 70)
    print("MANUAL SECTION GERCEK KABUL TESTI")
    print(f"URL  : {ACCEPTANCE_URL}")
    print(f"KONU : {ACCEPTANCE_TOPIC}")
    print("GEMINI CAGRISI BEKLENMIYOR: 0")
    print("=" * 70)

    original_client = ai_analyzer.genai.Client
    original_fetch = db_monitor_service.fetch_website

    ai_analyzer.genai.Client = lambda *a, **k: TrapClient()

    client = TestClient(app)

    # -----------------------------------------------------
    # 1. FRONTEND'IN ATTIGI ADAY ISTEGI
    # -----------------------------------------------------

    print("\n--- 1. POST /api/monitors/section-candidates ---")

    response = client.post(
        "/api/monitors/section-candidates",
        json={"url": ACCEPTANCE_URL},
    )

    check(
        "candidate endpoint 200 donuyor",
        response.status_code == 200,
        f"{response.status_code}",
    )

    payload = response.json()

    candidates = payload.get("candidates", [])

    print(f"Sayfa basligi : {payload.get('page_title')}")
    print(f"Aday sayisi   : {len(candidates)}")
    print()

    for candidate in candidates:
        print(
            f"  {candidate['candidate_id']:4} "
            f"{candidate['tag']:8} "
            f"uzunluk={candidate['text_length']:7} "
            f"etiket={candidate['label'][:38]!r}"
        )

    check(
        "gercek sitede aday bolumler bulundu",
        len(candidates) >= 3,
        f"aday={len(candidates)}",
    )

    check(
        "adaylar onizleme metni tasiyor",
        all(
            candidate["preview"].strip()
            for candidate in candidates
        ),
    )

    check(
        "onizleme duz metin (HTML etiketi yok)",
        all(
            "<" not in candidate["preview"]
            for candidate in candidates
        ),
    )

    check(
        "onizleme payload'i sinirli",
        all(
            len(candidate["preview"]) <= 520
            for candidate in candidates
        ),
        f"{max(len(c['preview']) for c in candidates)}",
    )

    check(
        "bu adimda Gemini cagrilmadi",
        GEMINI_CALLS == [],
        f"{GEMINI_CALLS}",
    )

    # -----------------------------------------------------
    # 2. KULLANICI SECIMI
    # -----------------------------------------------------

    print("\n--- 2. kullanici bir bolum seciyor ---")

    selected = next(
        (
            candidate
            for candidate in candidates
            if TARGET_LABEL_HINT
            in normalize_label(candidate["label"])
        ),
        None,
    )

    check(
        "hedef bolum aday listesinde var",
        selected is not None,
        TARGET_LABEL_HINT,
    )

    if selected is None:
        raise SystemExit(1)

    largest = max(
        candidates,
        key=lambda candidate: candidate["text_length"],
    )

    print(f"Secilen etiket   : {selected['label']}")
    print(f"Secilen referans : {selected['reference']}")
    print(f"Secilen uzunluk  : {selected['text_length']}")
    print(f"En buyuk aday    : {largest['label']} "
          f"({largest['text_length']})")

    check(
        "secim sayfanin en buyuk blogu DEGIL "
        "(gercekten kullanici tercihi)",
        selected["candidate_id"] != largest["candidate_id"],
        f"{selected['label']} vs {largest['label']}",
    )

    check(
        "secilen referans kirilgan CSS zinciri degil",
        "nth-child" not in selected["reference"]
        and ">" not in selected["reference"],
        selected["reference"],
    )

    # -----------------------------------------------------
    # 3. GERCEK SAYFADA REFERANS COZUMLEME
    # -----------------------------------------------------

    print("\n--- 3. referans gercek sayfada cozumleniyor ---")

    first_fetch = asyncio.run(
        fetch_website(ACCEPTANCE_URL)
    )

    full_content = extract_content(first_fetch.html)

    first_resolution = resolve_section(
        first_fetch.html,
        selected["reference"],
    )

    check(
        "referans indirilen sayfada bulundu",
        first_resolution.section is not None,
        selected["reference"],
    )

    check(
        "secilen metin TUM SAYFADAN kucuk",
        first_resolution.section is not None
        and first_resolution.section.text_length
        < full_content.text_length,
        f"{first_resolution.section.text_length} < "
        f"{full_content.text_length}",
    )

    print(
        f"Tum sayfa metni : {full_content.text_length}\n"
        f"Secilen bolum   : "
        f"{first_resolution.section.text_length}"
    )

    print()
    print("Secilen metin (ilk 250 karakter):")
    print("-" * 70)
    print(first_resolution.section.text[:250])
    print("-" * 70)

    # -----------------------------------------------------
    # 4. IKINCI INDIRME, AYNI REFERANS, AI YOK
    # -----------------------------------------------------

    print("\n--- 4. sayfa yeniden indiriliyor ---")

    second_fetch = asyncio.run(
        fetch_website(ACCEPTANCE_URL)
    )

    second_resolution = resolve_section(
        second_fetch.html,
        selected["reference"],
    )

    check(
        "ayni referans YENI indirmede de bulundu",
        second_resolution.section is not None,
    )

    check(
        "yeniden cozumlenen bolum ayni bolum",
        second_resolution.section is not None
        and normalize_label(second_resolution.section.label)
        == normalize_label(selected["label"]),
        f"{second_resolution.section.label!r}",
    )

    check(
        "ikinci cozumlemede de Gemini cagrilmadi",
        GEMINI_CALLS == [],
        f"{GEMINI_CALLS}",
    )

    # -----------------------------------------------------
    # 5. TAM PIPELINE (DB, ROLLBACK)
    # -----------------------------------------------------

    print("\n--- 5. tam pipeline (DB transaction, rollback) ---")

    db_monitor_service.fetch_website = replay_fetch_website

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
            name="manual_section kabul testi",
            url=ACCEPTANCE_URL,
            topic=ACCEPTANCE_TOPIC,
            scope_type="manual_section",
            schedule_type="daily",
            schedule_time="09:00",
            manual_section_reference=selected["reference"],
            manual_section_label=selected["label"],
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
            "ilk run: snapshot yalnizca secilen bolum",
            first_snapshot.content
            == first_resolution.section.text,
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
            "ilk run: secim DB'de degismedi",
            monitor.manual_section_reference
            == selected["reference"]
            and monitor.manual_section_label
            == selected["label"],
        )

        check(
            "ilk run: auto_section alanlari bos kaldi",
            monitor.auto_section_reference is None
            and monitor.auto_section_label is None,
        )

        check(
            "ilk run: Gemini bolum secimi YAPILMADI",
            GEMINI_CALLS == [],
            f"{GEMINI_CALLS}",
        )

        # --- IKINCI RUN: YENI INDIRME ---

        CURRENT_HTML["value"] = second_fetch.html

        second_result = asyncio.run(
            run_database_monitor(
                db=session,
                monitor=monitor,
            )
        )

        second_snapshot = get_latest_snapshot(
            db=session,
            monitor_id=monitor.id,
        )

        check(
            "ikinci run: ayni referans kullanildi",
            monitor.manual_section_reference
            == selected["reference"]
            and second_result.manual_section is not None
            and second_result.manual_section.reference
            == selected["reference"],
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
            f"(iki indirme arasinda bolum degistiyse True)"
        )

        session.flush()

    finally:
        session.close()
        transaction.rollback()
        connection.close()

        db_monitor_service.fetch_website = original_fetch
        ai_analyzer.genai.Client = original_client

    # -----------------------------------------------------
    # 6. TEMIZLIK
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

    check(
        "TUM KABUL TESTI BOYUNCA GEMINI CAGRISI = 0",
        GEMINI_CALLS == [],
        f"{GEMINI_CALLS}",
    )

    # -----------------------------------------------------
    # SONUC
    # -----------------------------------------------------

    print("\n" + "=" * 70)
    print(f"GERCEK GEMINI CAGRISI : {len(GEMINI_CALLS)}")
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
