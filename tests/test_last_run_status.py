"""
SON CALISTIRMA DURUMU TESTLERI

SINIF: automated (deterministic, CI-safe)

Amac: kullanici monitor kartina baktiginda son
kontrolun NE SONUC verdigini gorebilmeli.

Onceden yalnizca "Son kontrol: 07.09.2026 09:38:28"
goruluyordu; sonucun ne oldugu SADECE backend
loglarindan anlasilabiliyordu.

Kapsam:

     1. ilk calistirma -> first_run
     2. degisiklik yok -> no_change
     3. standart degisiklik -> change_detected
     4. mevzuat karsilastirmasi -> comparison_completed
     5. referans bulunamadi -> reference_not_found
     6. referans belirsiz -> reference_ambiguous
     7. kesif basarisiz -> discovery_failed
     8. siteye erisilemedi -> fetch_failed
     9. sonraki basarili deneme fetch_failed'i EZER
    10. no_change YENI Notification URETMEZ
    11. no_change e-posta GONDERMEZ
    12. fetch_failed e-posta GONDERMEZ
    13. first_run e-posta GONDERMEZ
    14. gercek degisiklik bildirimi DEGISMEDI
    15. gercek degisiklik e-postasi DEGISMEDI
    16. manuel calistirma durumu yazar
    17. scheduler calistirmasi durumu yazar
    18. manuel ve scheduler AYNI durumu uretir
    19. monitor listesi alanlari doner
    20. fetch_failed last_checked_at'i DEGISTIRMEZ
    21. her durumun kullanici mesaji var
    22. siddet degerleri gecerli
    23. taninmayan durum uydurma mesaj URETMEZ
    24. AI hatasi -> ai_failed
    25. konuyla ilgisiz degisiklik -> change_irrelevant

GERCEK GEMINI YOK. GERCEK SMTP YOK. GERCEK AG YOK.

Gercek PostgreSQL uzerinde calisir ama KALICI KAYIT
BIRAKMAZ.

Calistirma:
    python -m tests.test_last_run_status
"""

import asyncio
import json
import smtplib
import warnings

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database import engine, get_db
from app.models.monitor import Monitor
from app.models.notification import Notification

from app.repositories.monitor_repository import create_monitor

from app.services import ai_analyzer
from app.services import db_monitor_service
from app.services import email_service
from app.services import notification_service

from app.services.monitor_run_service import execute_monitor_run
from app.services.monitor_run_status import (
    RUN_STATUSES,
    STATUS_AI_FAILED,
    STATUS_CHANGE_DETECTED,
    STATUS_CHANGE_IRRELEVANT,
    STATUS_COMPARISON_COMPLETED,
    STATUS_DISCOVERY_FAILED,
    STATUS_FETCH_FAILED,
    STATUS_FIRST_RUN,
    STATUS_NO_CHANGE,
    STATUS_REFERENCE_AMBIGUOUS,
    STATUS_REFERENCE_NOT_FOUND,
    describe,
    resolve_run_status,
)
from app.services.web_fetcher import WebsiteFetchError

warnings.filterwarnings("ignore")

from fastapi.testclient import TestClient  # noqa: E402

from app.core import scheduler  # noqa: E402
from app.main import app  # noqa: E402


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


def build_page(price: str) -> str:
    return f"""<html>
<head><title>Ornek Sayfa</title></head>
<body><main>
<section id="fiyat">
<h2>Fiyatlandirma</h2>
<p>Baslangic paketi aylik {price} olarak sunulmaktadir.</p>
<p>Kurumsal paket aylik 500 TL seviyesindedir ve sinirsizdir.</p>
</section>
</main></body></html>"""


PAGE_V1 = build_page("100 TL")
PAGE_V2 = build_page("150 TL")


CURRENT_HTML = {"value": PAGE_V1}

FETCH_SHOULD_FAIL = {"value": False}


async def fake_fetch(url: str):

    if FETCH_SHOULD_FAIL["value"]:
        raise WebsiteFetchError(
            "baglanti kurulamadi"
        )

    return FakeFetchResult(
        url=url,
        final_url=url,
        status_code=200,
        content_type="text/html; charset=utf-8",
        html=CURRENT_HTML["value"],
    )


# ---------------------------------------------------------
# SAHTE GEMINI
# ---------------------------------------------------------

ANALYSIS = {"relevant": True, "fail": False}


def analysis_json():
    return json.dumps(
        {
            "relevant": ANALYSIS["relevant"],
            "importance": "high",
            "category": "pricing",
            "summary": "Baslangic paketi fiyati degisti.",
            "notification_recommended": ANALYSIS["relevant"],
            "evidence_text": (
                "Baslangic paketi aylik 150 TL olarak "
                "sunulmaktadir."
            ),
            "evidence_type": "added",
        },
        ensure_ascii=False,
    )


@dataclass
class FakeResponse:
    text: str


class FakeModels:

    async def generate_content(self, model, contents, config):

        if ANALYSIS["fail"]:
            raise RuntimeError("model gecici olarak yanit vermedi")

        return FakeResponse(text=analysis_json())


class FakeAio:

    def __init__(self):
        self.models = FakeModels()

    async def aclose(self):
        return None


class FakeGeminiClient:

    def __init__(self, *args, **kwargs):
        self.aio = FakeAio()


# ---------------------------------------------------------
# SAHTE SMTP
# ---------------------------------------------------------

SMTP_SESSIONS = []


class FakeSMTP:

    def __init__(self, host=None, port=None, timeout=None, context=None):
        self.session = {"messages": []}
        SMTP_SESSIONS.append(self.session)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def ehlo(self, *args, **kwargs):
        return None

    def starttls(self, *args, **kwargs):
        return None

    def login(self, username, password):
        return None

    def send_message(self, message):
        self.session["messages"].append(message)

    def quit(self):
        return None


class FakeSMTPSSL(FakeSMTP):
    pass


# ---------------------------------------------------------
# SAHTE KARSILASTIRMA SONUCU
#
# resolve_run_status'un mevzuat esleme dalini gercek
# alan adlariyla sinar.
# ---------------------------------------------------------

@dataclass
class FakeComparisonRun:
    status: str
    match_status: str | None = None
    discovery_status: str | None = None


@dataclass
class FakeAnalysisRecord:
    relevant: bool = True


@dataclass
class FakeResult:
    first_run: bool = False
    changed: bool = True
    ai_analysis_record: object = None
    ai_error: str | None = None


def make_monitor(session, name, notify_email=False):
    return create_monitor(
        db=session,
        name=name,
        url="https://ornek-site.test/urun",
        topic="fiyat degisiklikleri",
        scope_type="full_page",
        schedule_type="daily",
        schedule_time="09:00",
        notify_in_app=True,
        notify_email=notify_email,
        notification_email=(
            "bildirim@ornek.test" if notify_email else None
        ),
    )


def run(session, monitor, html=None, fail=False):

    if html is not None:
        CURRENT_HTML["value"] = html

    FETCH_SHOULD_FAIL["value"] = fail

    try:
        return asyncio.run(
            execute_monitor_run(
                db=session,
                monitor=monitor,
            )
        )

    finally:
        FETCH_SHOULD_FAIL["value"] = False


def _succeeded(session, client, monitor):
    """
    last_run_succeeded degerini GERCEK API sozlesmesi
    uzerinden okur; modelden dogrudan okumaz.
    """

    session.refresh(monitor)

    listing = client.get("/api/monitors")

    for item in listing.json():
        if item.get("id") == monitor.id:
            return item.get("last_run_succeeded")

    return None


def run_tests(session, client) -> None:

    # =====================================================
    # 1-3. TEMEL DURUMLAR
    # =====================================================

    print()
    print("--- temel durumlar ---")

    monitor = make_monitor(session, "Durum Testi")

    run(session, monitor, PAGE_V1)

    check(
        "1. ilk calistirma -> first_run",
        monitor.last_run_status == STATUS_FIRST_RUN
        and monitor.last_run_message
        == "İlk snapshot oluşturuldu",
        f"{monitor.last_run_status} / {monitor.last_run_message}",
    )

    # Ayni sayfa: degisiklik yok.
    run(session, monitor, PAGE_V1)

    check(
        "2. degisiklik yok -> no_change",
        monitor.last_run_status == STATUS_NO_CHANGE
        and monitor.last_run_message
        == "Kontrol başarılı — değişiklik tespit edilmedi",
        f"{monitor.last_run_status} / {monitor.last_run_message}",
    )

    notifications_before = session.scalar(
        select(func.count()).select_from(Notification)
    )

    ANALYSIS["relevant"] = True

    run(session, monitor, PAGE_V2)

    check(
        "3. standart degisiklik -> change_detected",
        monitor.last_run_status == STATUS_CHANGE_DETECTED
        and monitor.last_run_message
        == "Değişiklik tespit edildi",
        f"{monitor.last_run_status} / {monitor.last_run_message}",
    )

    check(
        "14. gercek degisiklik bildirimi DEGISMEDI",
        session.scalar(
            select(func.count()).select_from(Notification)
        )
        == notifications_before + 1,
        f"once={notifications_before}",
    )

    # =====================================================
    # 25. KONUYLA ILGISIZ
    # =====================================================

    irrelevant_monitor = make_monitor(session, "Ilgisiz Testi")

    run(session, irrelevant_monitor, PAGE_V1)

    ANALYSIS["relevant"] = False

    run(session, irrelevant_monitor, PAGE_V2)

    check(
        "25. konuyla ilgisiz degisiklik -> change_irrelevant",
        irrelevant_monitor.last_run_status
        == STATUS_CHANGE_IRRELEVANT,
        f"{irrelevant_monitor.last_run_status}",
    )

    ANALYSIS["relevant"] = True

    # =====================================================
    # 24. AI HATASI
    # =====================================================

    ai_monitor = make_monitor(session, "AI Hata Testi")

    run(session, ai_monitor, PAGE_V1)

    ANALYSIS["fail"] = True

    run(session, ai_monitor, PAGE_V2)

    ANALYSIS["fail"] = False

    check(
        "24. AI hatasi -> ai_failed",
        ai_monitor.last_run_status == STATUS_AI_FAILED,
        f"{ai_monitor.last_run_status}",
    )

    # =====================================================
    # 8-9 + 20. FETCH HATASI VE TOPARLANMA
    # =====================================================

    print()
    print("--- fetch hatasi ---")

    fetch_monitor = make_monitor(session, "Fetch Testi")

    run(session, fetch_monitor, PAGE_V1)

    checked_before = fetch_monitor.last_checked_at

    smtp_before = len(SMTP_SESSIONS)

    notifications_before = session.scalar(
        select(func.count()).select_from(Notification)
    )

    raised = False

    try:
        run(session, fetch_monitor, fail=True)

    except WebsiteFetchError:
        raised = True

    session.refresh(fetch_monitor)

    check(
        "8. siteye erisilemedi -> fetch_failed",
        raised is True
        and fetch_monitor.last_run_status == STATUS_FETCH_FAILED
        and fetch_monitor.last_run_message
        == "Son kontrol başarısız — siteye erişilemedi",
        f"{fetch_monitor.last_run_status} / "
        f"{fetch_monitor.last_run_message}",
    )

    check(
        "20. fetch_failed last_checked_at'i DEGISTIRMEZ",
        fetch_monitor.last_checked_at == checked_before,
        f"{checked_before} -> {fetch_monitor.last_checked_at}",
    )

    check(
        "12. fetch_failed e-posta GONDERMEZ",
        len(SMTP_SESSIONS) == smtp_before,
        f"{smtp_before} -> {len(SMTP_SESSIONS)}",
    )

    check(
        "8. fetch_failed bildirim URETMEZ",
        session.scalar(
            select(func.count()).select_from(Notification)
        )
        == notifications_before,
    )

    # Sonraki deneme basarili: durum EZILMELI.
    run(session, fetch_monitor, PAGE_V1)

    check(
        "9. sonraki basarili deneme fetch_failed'i EZER",
        fetch_monitor.last_run_status == STATUS_NO_CHANGE,
        f"{fetch_monitor.last_run_status}",
    )

    check(
        "9. basarili denemede last_checked_at ilerler",
        fetch_monitor.last_checked_at != checked_before,
    )

    # =====================================================
    # 10-13. BILDIRIM VE E-POSTA SESSIZLIGI
    # =====================================================

    print()
    print("--- bildirim / e-posta sessizligi ---")

    quiet = make_monitor(
        session,
        "Sessizlik Testi",
        notify_email=True,
    )

    notifications_before = session.scalar(
        select(func.count()).select_from(Notification)
    )

    SMTP_SESSIONS.clear()

    run(session, quiet, PAGE_V1)

    check(
        "13. first_run e-posta GONDERMEZ",
        len(SMTP_SESSIONS) == 0,
        f"oturum={len(SMTP_SESSIONS)}",
    )

    check(
        "first_run bildirim URETMEZ",
        session.scalar(
            select(func.count()).select_from(Notification)
        )
        == notifications_before,
    )

    run(session, quiet, PAGE_V1)

    check(
        "10. no_change YENI Notification URETMEZ",
        session.scalar(
            select(func.count()).select_from(Notification)
        )
        == notifications_before,
        f"{notifications_before}",
    )

    check(
        "11. no_change e-posta GONDERMEZ",
        len(SMTP_SESSIONS) == 0,
        f"oturum={len(SMTP_SESSIONS)}",
    )

    # Gercek degisiklik: mevcut davranis korunmali.
    outcome = run(session, quiet, PAGE_V2)

    check(
        "15. gercek degisiklik e-postasi DEGISMEDI",
        len(SMTP_SESSIONS) == 1
        and outcome.email_notification is not None,
        f"oturum={len(SMTP_SESSIONS)}",
    )

    # =====================================================
    # 4-7. MEVZUAT DURUM ESLEMESI
    # =====================================================

    print()
    print("--- mevzuat durum eslemesi ---")

    from app.services.regulation_discovery_service import (
        DISCOVERY_AMBIGUOUS,
        DISCOVERY_FAILED,
        DISCOVERY_NO_MATCH,
    )
    from app.services.regulation_monitor_service import (
        COMPARISON_COMPLETED,
        COMPARISON_SKIPPED_NO_AUTO_MATCH,
    )

    relevant_result = FakeResult(
        ai_analysis_record=FakeAnalysisRecord(relevant=True),
    )

    completed = resolve_run_status(
        result=relevant_result,
        regulation_comparison=FakeComparisonRun(
            status=COMPARISON_COMPLETED,
        ),
    )

    check(
        "4. mevzuat karsilastirmasi -> comparison_completed",
        completed == STATUS_COMPARISON_COMPLETED
        and describe(completed).message
        == "Mevzuat değişikliği bulundu — karşılaştırma "
        "tamamlandı",
        f"{completed}",
    )

    not_found = resolve_run_status(
        result=relevant_result,
        regulation_comparison=FakeComparisonRun(
            status=COMPARISON_SKIPPED_NO_AUTO_MATCH,
            discovery_status=DISCOVERY_NO_MATCH,
        ),
    )

    check(
        "5. referans bulunamadi -> reference_not_found",
        not_found == STATUS_REFERENCE_NOT_FOUND
        and "referans mevzuat bulunamadı"
        in describe(not_found).message,
        f"{not_found}",
    )

    ambiguous = resolve_run_status(
        result=relevant_result,
        regulation_comparison=FakeComparisonRun(
            status=COMPARISON_SKIPPED_NO_AUTO_MATCH,
            discovery_status=DISCOVERY_AMBIGUOUS,
        ),
    )

    check(
        "6. referans belirsiz -> reference_ambiguous",
        ambiguous == STATUS_REFERENCE_AMBIGUOUS
        and "manuel doğrulama"
        in describe(ambiguous).message,
        f"{ambiguous}",
    )

    discovery_failed = resolve_run_status(
        result=relevant_result,
        regulation_comparison=FakeComparisonRun(
            status=COMPARISON_SKIPPED_NO_AUTO_MATCH,
            discovery_status=DISCOVERY_FAILED,
        ),
    )

    check(
        "7. kesif basarisiz -> discovery_failed",
        discovery_failed == STATUS_DISCOVERY_FAILED
        and "resmî referans metin"
        in describe(discovery_failed).message,
        f"{discovery_failed}",
    )

    # =====================================================
    # 16-18. MANUEL / SCHEDULER TUTARLILIGI
    # =====================================================

    print()
    print("--- manuel / scheduler tutarliligi ---")

    manual_monitor = make_monitor(session, "Manuel Calistirma")

    run(session, manual_monitor, PAGE_V1)

    manual_response = client.post(
        f"/api/monitors/{manual_monitor.id}/run"
    )

    session.refresh(manual_monitor)

    check(
        "16. manuel calistirma durumu yazar",
        manual_response.status_code == 200
        and manual_monitor.last_run_status == STATUS_NO_CHANGE,
        f"status={manual_response.status_code} "
        f"{manual_monitor.last_run_status}",
    )

    manual_status = manual_monitor.last_run_status
    manual_message = manual_monitor.last_run_message

    # Scheduler yolu: ayni entrypoint, ayri session.
    scheduler_monitor = make_monitor(
        session,
        "Scheduler Calistirma",
    )

    run(session, scheduler_monitor, PAGE_V1)

    class NonClosingSession:

        def __init__(self, inner):
            self._inner = inner

        def __getattr__(self, name):
            return getattr(self._inner, name)

        def close(self):
            return None

    original_session_local = scheduler.SessionLocal

    scheduler.SessionLocal = lambda: NonClosingSession(session)

    try:
        asyncio.run(
            scheduler._execute_due_monitor(
                monitor_id=scheduler_monitor.id,
                monitor_name=scheduler_monitor.name,
            )
        )

    finally:
        scheduler.SessionLocal = original_session_local

    session.refresh(scheduler_monitor)

    check(
        "17. scheduler calistirmasi durumu yazar",
        scheduler_monitor.last_run_status == STATUS_NO_CHANGE,
        f"{scheduler_monitor.last_run_status}",
    )

    check(
        "18. manuel ve scheduler AYNI durumu uretir",
        scheduler_monitor.last_run_status == manual_status
        and scheduler_monitor.last_run_message
        == manual_message,
        f"manuel={manual_status} "
        f"scheduler={scheduler_monitor.last_run_status}",
    )

    # =====================================================
    # 19. LISTE API
    # =====================================================

    print()
    print("--- liste API ---")

    listing = client.get("/api/monitors")

    rows = listing.json() if listing.status_code == 200 else []

    row = next(
        (
            item
            for item in rows
            if item.get("id") == manual_monitor.id
        ),
        None,
    )

    check(
        "19. monitor listesi alanlari doner",
        listing.status_code == 200
        and row is not None
        and row.get("last_run_status") == STATUS_NO_CHANGE
        and row.get("last_run_message")
        == "Kontrol başarılı — değişiklik tespit edilmedi"
        and row.get("last_run_severity") == "success"
        and row.get("last_run_at") is not None,
        f"status={listing.status_code} "
        f"{row.get('last_run_status') if row else None} / "
        f"{row.get('last_run_severity') if row else None}",
    )

    # Hic calistirilmamis monitor: alanlar None kalmali.
    fresh = make_monitor(session, "Hic Calismamis")

    listing = client.get("/api/monitors")

    fresh_row = next(
        (
            item
            for item in listing.json()
            if item.get("id") == fresh.id
        ),
        None,
    )

    check(
        "19. hic calistirilmamis monitorde alanlar None",
        fresh_row is not None
        and fresh_row.get("last_run_status") is None
        and fresh_row.get("last_run_message") is None
        and fresh_row.get("last_run_severity") is None
        and fresh_row.get("last_run_succeeded") is None,
        f"{fresh_row.get('last_run_status') if fresh_row else None}",
    )

    # =====================================================
    # SON DENEME GORUNURLUGU
    #
    # BUG: basarili calistirmada da "Son deneme" satiri
    # gorunuyordu. Sebep, last_checked_at ile last_run_at'in
    # AYRI datetime.utcnow() cagrilariyla yazilmasi ve
    # frontend'in iki damgayi karsilastirmasiydi. Ayni
    # calistirmaya ait olsalar bile milisaniye farkla
    # yazilirlar.
    #
    # Karar artik zaman damgasindan DEGIL, durumun
    # pipeline'i tamamlayip tamamlamadigindan gelir.
    # =====================================================

    print()
    print("--- son deneme gorunurlugu ---")

    visibility_monitor = make_monitor(
        session,
        "Deneme Gorunurlugu",
    )

    # 4. first_run
    run(session, visibility_monitor, PAGE_V1)

    check(
        "4. first_run -> Son deneme GIZLI",
        visibility_monitor.last_run_status == STATUS_FIRST_RUN
        and _succeeded(session, client, visibility_monitor)
        is True,
        f"{visibility_monitor.last_run_status}",
    )

    # Basarili calistirmada iki damga FARKLI olsa bile
    # satir gizlenmelidir. Hatanin ta kendisi buydu.
    check(
        "4. basarili calistirmada zaman damgalari farkli "
        "olabilir",
        visibility_monitor.last_run_at
        != visibility_monitor.last_checked_at,
        f"checked={visibility_monitor.last_checked_at} "
        f"run={visibility_monitor.last_run_at}",
    )

    # 1. no_change
    run(session, visibility_monitor, PAGE_V1)

    check(
        "1. no_change -> Son deneme GIZLI",
        visibility_monitor.last_run_status == STATUS_NO_CHANGE
        and _succeeded(session, client, visibility_monitor)
        is True,
        f"{visibility_monitor.last_run_status}",
    )

    # 2. change_detected
    ANALYSIS["relevant"] = True

    run(session, visibility_monitor, PAGE_V2)

    check(
        "2. change_detected -> Son deneme GIZLI",
        visibility_monitor.last_run_status
        == STATUS_CHANGE_DETECTED
        and _succeeded(session, client, visibility_monitor)
        is True,
        f"{visibility_monitor.last_run_status}",
    )

    success_checked_at = visibility_monitor.last_checked_at

    # 5. fetch_failed
    try:
        run(session, visibility_monitor, fail=True)
    except WebsiteFetchError:
        pass

    session.refresh(visibility_monitor)

    check(
        "5. fetch_failed -> Son deneme GORUNUR",
        visibility_monitor.last_run_status == STATUS_FETCH_FAILED
        and _succeeded(session, client, visibility_monitor)
        is False,
        f"{visibility_monitor.last_run_status}",
    )

    check(
        "7. basarisiz deneme last_checked_at'i DEGISTIRMEZ",
        visibility_monitor.last_checked_at
        == success_checked_at,
        f"{success_checked_at} -> "
        f"{visibility_monitor.last_checked_at}",
    )

    check(
        "5. basarisiz denemede iki damga AYRI "
        "calistirmalara ait",
        visibility_monitor.last_run_at
        > visibility_monitor.last_checked_at,
        f"checked={visibility_monitor.last_checked_at} "
        f"run={visibility_monitor.last_run_at}",
    )

    # 8. sonraki basarili deneme
    run(session, visibility_monitor, PAGE_V2)

    check(
        "8. sonraki basarili deneme -> Son deneme YENIDEN "
        "GIZLI",
        visibility_monitor.last_run_status == STATUS_NO_CHANGE
        and _succeeded(session, client, visibility_monitor)
        is True,
        f"{visibility_monitor.last_run_status}",
    )

    # 3. comparison_completed + 6. run_failed sozlesme
    # tarafinda dogrulanir (contract_tests): bu durumlar
    # gercek pipeline'da mevzuat referansi / beklenmeyen
    # istisna gerektirir.

    # 10. API sozlesmesi
    listing = client.get("/api/monitors")

    row = next(
        (
            item
            for item in listing.json()
            if item.get("id") == visibility_monitor.id
        ),
        None,
    )

    check(
        "10. monitor API last_run_succeeded tasiyor",
        row is not None
        and row.get("last_run_succeeded") is True
        and isinstance(row.get("last_run_succeeded"), bool),
        f"{row.get('last_run_succeeded') if row else None}",
    )


def contract_tests() -> None:
    """
    Durum sozlesmesi (DB gerektirmez).
    """

    print()
    print("--- durum sozlesmesi ---")

    missing_message = [
        status
        for status in RUN_STATUSES
        if not (describe(status) or {}).message
    ]

    check(
        "21. her durumun kullanici mesaji var",
        not missing_message,
        f"eksik={missing_message}",
    )

    valid_severities = {"success", "info", "warning", "error"}

    bad_severity = [
        status
        for status in RUN_STATUSES
        if describe(status).severity not in valid_severities
    ]

    check(
        "22. siddet degerleri gecerli",
        not bad_severity,
        f"bozuk={bad_severity}",
    )

    check(
        "23. taninmayan durum uydurma mesaj URETMEZ",
        describe("uydurma_durum") is None
        and describe(None) is None
        and describe("") is None,
    )

    # -----------------------------------------------------
    # DENEME BASARILI MI
    #
    # "Son deneme" satirinin gorunurlugu bu semantige
    # baglidir. SIDDET ILE AYNI SEY DEGILDIR.
    # -----------------------------------------------------

    from app.services.monitor_run_status import (
        FAILURE_STATUSES,
        STATUS_RUN_FAILED,
        STATUS_SECTION_FAILED,
        attempt_succeeded,
        resolve_failure_status,
    )

    check(
        "3. comparison_completed -> deneme BASARILI",
        describe(STATUS_COMPARISON_COMPLETED).succeeded is True,
    )

    check(
        "6. run_failed -> deneme BASARISIZ",
        describe(STATUS_RUN_FAILED).succeeded is False,
    )

    check(
        "6. section_failed -> deneme BASARISIZ",
        describe(STATUS_SECTION_FAILED).succeeded is False,
    )

    # KRITIK: siddet ile karistirilmamalidir.
    # ai_failed bir "warning"dir ama pipeline TAMAMLANMIS,
    # last_checked_at ILERLEMISTIR.
    warning_but_succeeded = [
        status
        for status in RUN_STATUSES
        if describe(status).severity == "warning"
        and not describe(status).succeeded
    ]

    check(
        "warning durumlar deneme basarisizligi ANLAMINA "
        "GELMEZ",
        not warning_but_succeeded,
        f"{warning_but_succeeded}",
    )

    check(
        "ai_failed warning ama deneme BASARILI",
        describe(STATUS_AI_FAILED).severity == "warning"
        and describe(STATUS_AI_FAILED).succeeded is True,
    )

    # FAILURE_STATUSES elle bakimi yapilan bir liste
    # OLMAMALIDIR: istisna yolunun uretebilecegi butun
    # degerler tam olarak bu kume olmalidir.
    from app.services.section_selector import (
        AutoSectionSelectionError,
    )
    from app.services.db_monitor_service import (
        ManualSectionResolutionError,
    )

    produced = {
        resolve_failure_status(WebsiteFetchError("x")),
        resolve_failure_status(AutoSectionSelectionError("x")),
        resolve_failure_status(ManualSectionResolutionError("x")),
        resolve_failure_status(RuntimeError("x")),
        resolve_failure_status(ValueError("x")),
    }

    check(
        "FAILURE_STATUSES istisna yolunun ciktisiyla birebir",
        produced == set(FAILURE_STATUSES),
        f"uretilen={sorted(produced)} "
        f"kume={sorted(FAILURE_STATUSES)}",
    )

    # Basari yolunun urettigi HICBIR durum basarisiz
    # sayilmamalidir.
    success_path = set(RUN_STATUSES) - set(FAILURE_STATUSES)

    check(
        "basari yolundaki durumlarin tamami succeeded=True",
        all(
            attempt_succeeded(status) for status in success_path
        ),
        f"{sorted(success_path)}",
    )

    check(
        "bilinmeyen durum basarisizlik VARSAYMAZ",
        attempt_succeeded("uydurma") is True
        and attempt_succeeded(None) is True,
    )

    # Mesajlar teknik terim sizdirmamali.
    leaking = [
        status
        for status in RUN_STATUSES
        if status in describe(status).message
    ]

    check(
        "21. mesajlar durum adini sizdirmaz",
        not leaking,
        f"{leaking}",
    )


def main() -> None:

    print("=" * 70)
    print("SON CALISTIRMA DURUMU TESTLERI")
    print("=" * 70)

    contract_tests()

    original_fetch = db_monitor_service.fetch_website
    original_client = ai_analyzer.genai.Client
    original_smtp = smtplib.SMTP
    original_smtp_ssl = smtplib.SMTP_SSL
    original_send = notification_service.send_email

    original_settings = {
        field: getattr(email_service.settings, field)
        for field in (
            "smtp_host",
            "smtp_port",
            "smtp_username",
            "smtp_password",
            "smtp_from_email",
            "smtp_use_tls",
            "smtp_use_ssl",
        )
    }

    db_monitor_service.fetch_website = fake_fetch
    ai_analyzer.genai.Client = FakeGeminiClient
    smtplib.SMTP = FakeSMTP
    smtplib.SMTP_SSL = FakeSMTPSSL
    notification_service.send_email = email_service.send_email

    email_service.settings.smtp_host = "smtp.ornek.test"
    email_service.settings.smtp_port = 587
    email_service.settings.smtp_username = "sahte@ornek.test"
    email_service.settings.smtp_password = "sahte-parola-12345"
    email_service.settings.smtp_from_email = "bot@ornek.test"
    email_service.settings.smtp_use_tls = True
    email_service.settings.smtp_use_ssl = False

    connection = engine.connect()
    transaction = connection.begin()

    session = Session(
        bind=connection,
        join_transaction_mode="create_savepoint",
    )

    app.dependency_overrides[get_db] = lambda: session

    client = TestClient(app)

    monitor_before = session.scalar(
        select(func.count()).select_from(Monitor)
    )

    try:
        run_tests(session, client)

    finally:
        app.dependency_overrides.clear()

        session.close()

        transaction.rollback()
        connection.close()

        db_monitor_service.fetch_website = original_fetch
        ai_analyzer.genai.Client = original_client
        smtplib.SMTP = original_smtp
        smtplib.SMTP_SSL = original_smtp_ssl
        notification_service.send_email = original_send

        for field, value in original_settings.items():
            setattr(email_service.settings, field, value)

    with Session(bind=engine) as verify:
        monitor_after = verify.scalar(
            select(func.count()).select_from(Monitor)
        )

    check(
        "DB kirliligi YOK",
        monitor_after == monitor_before,
        f"{monitor_before} -> {monitor_after}",
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
