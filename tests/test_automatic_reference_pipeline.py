"""
OTOMATIK REFERANS PIPELINE TESTLERI

Kapsam:
     1. mevcut regulation monitor -> manual mod
     2. yeni regulation monitor -> varsayilan automatic
     3. automatic create referanssiz KABUL
     4. manual create referanssiz RED
     5. automatic exact match -> karsilastirma KENDILIGINDEN calisir
     6. kullanici onayi GEREKMEZ
     7. exact match -> matcher Gemini cagrisi 0
     8. automatic ambiguous -> karsilastirma YOK
     9. automatic no_match -> karsilastirma YOK
    10. automatic no_base_name -> karsilastirma YOK
    11. automatic no_references -> karsilastirma YOK
    12. manual mod secilen referansi kullanir
    13. manual secim ASLA degistirilmez
    14. otomatik secilen referans comparison'a yazilir
    15. match status/method/confidence kalici
    16. taze konsolide referans -> refresh YOK
    17. bayat konsolide referans -> refresh
    18. refresh degisti -> yeni hash ile karsilastirma
    19. refresh basarisiz -> kontrollu davranis
    20. manuel /run otomatik monitorde calisir
    21. tetikleme kosullari korunuyor
    22. arayuz otomatik/manuel modlari
    23. mevcut monitor duzenlemede MANUAL aciliyor
    24. otomatik modda secici gizli
    25. manuel modda secici gorunur
    26. oneri arayuzu duruyor
    27. oneri butonu otomatik pipeline icin GEREKLI DEGIL
    28-30. bildirim detayi durumlari

GERCEK GEMINI CAGRISI YOKTUR. genai.Client sahte nesneyle
degistirilir ve her cagri sayilir.

Gercek PostgreSQL uzerinde calisir ama KALICI KAYIT
BIRAKMAZ.

Calistirma:
    python -m tests.test_automatic_reference_pipeline
"""

import asyncio
import io
import json
import warnings

from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database import engine, get_db
from app.models.monitor import Monitor
from app.models.regulation_comparison import RegulationComparison
from app.models.regulation_reference import RegulationReference
from app.repositories.ai_analysis_repository import (
    create_ai_analysis,
)
from app.repositories.change_repository import create_change
from app.repositories.monitor_repository import create_monitor
from app.repositories.notification_repository import (
    create_notification,
)
from app.repositories.regulation_reference_repository import (
    create_regulation_reference,
)
from app.repositories.snapshot_repository import create_snapshot
from app.services import regulation_reference_service
from app.services.regulation_freshness_service import (
    FRESHNESS_FRESH,
    FRESHNESS_NOT_APPLICABLE,
    FRESHNESS_REFRESHED,
    FRESHNESS_REFRESHED_CHANGED,
    FRESHNESS_STALE_UNVERIFIED,
    ensure_reference_fresh,
)
from app.services.regulation_monitor_service import (
    COMPARISON_COMPLETED,
    COMPARISON_SKIPPED_FIRST_RUN,
    COMPARISON_SKIPPED_IRRELEVANT,
    COMPARISON_SKIPPED_NO_AUTO_MATCH,
    COMPARISON_SKIPPED_NO_CHANGE,
    MATCH_METHOD_AUTOMATIC_EXACT,
    MATCH_METHOD_MANUAL,
    REFERENCE_MODE_AUTOMATIC,
    REFERENCE_MODE_MANUAL,
    is_automatic_reference_mode,
    resolve_reference_selection_mode,
    run_regulation_comparison,
)
from app.services.regulation_reference_matcher import (
    MATCH_STATUS_AMBIGUOUS,
    MATCH_STATUS_MATCHED,
    MATCH_STATUS_NO_BASE_NAME,
    MATCH_STATUS_NO_MATCH,
    MATCH_STATUS_NO_REFERENCES,
)


warnings.filterwarnings("ignore")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


PROJECT_ROOT = Path(__file__).resolve().parents[1]

APP_JSX = PROJECT_ROOT / "frontend" / "src" / "App.jsx"


PASSED = []
FAILED = []


def check(label: str, condition: bool, detail: str = "") -> None:

    status = "PASS" if condition else "FAIL"

    print(f"[{status}] {label}" + (f" -> {detail}" if detail else ""))

    if condition:
        PASSED.append(label)
    else:
        FAILED.append(f"{label} -> {detail}")


def read(path: Path) -> str:
    return io.open(path, encoding="utf-8").read()


def section(source: str, start_marker: str, end_marker: str) -> str:
    start = source.index(start_marker)
    end = source.index(end_marker, start)

    return source[start:end]


# ---------------------------------------------------------
# SAHTE GEMINI
# ---------------------------------------------------------

GEMINI_CALLS = []

NEXT_RESPONSE = {"payload": None, "error": None}


class FakeResponse:
    def __init__(self, text):
        self.text = text


class FakeModels:
    async def generate_content(self, model, contents, config):
        GEMINI_CALLS.append(
            {"model": model, "prompt": contents}
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
# FIXTURE
# ---------------------------------------------------------

BASE_TITLE = (
    "Ödeme Hizmetleri ve Elektronik Para İhracı ile Ödeme "
    "Hizmeti Sağlayıcıları Hakkında Yönetmelik"
)

AMENDMENT = """ÖDEME HİZMETLERİ VE ELEKTRONİK PARA İHRACI İLE ÖDEME HİZMETİ
SAĞLAYICILARI HAKKINDA YÖNETMELİKTE DEĞİŞİKLİK YAPILMASINA DAİR YÖNETMELİK
MADDE 1- 1/12/2021 tarihli ve 31676 sayılı Resmî Gazete'de yayımlanan Ödeme
Hizmetleri ve Elektronik Para İhracı ile Ödeme Hizmeti Sağlayıcıları Hakkında
Yönetmeliğin 41 inci maddesinde yer alan "yazılı" ibaresi "biyometrik"
şeklinde değiştirilmiştir."""

REFERENCE_CONTENT = """MADDE 40
(1) Kayıtlar saklanır ve gerektiğinde Bankaya sunulur.
MADDE 41
(1) Uzaktan iletişim aracı ile müşterinin kimliği yazılı olarak doğrulanır.
MADDE 42
(1) Ödeme kuruluşları asgari özkaynak yükümlülüğünü sağlar.
"""

REFERENCE_CONTENT_UPDATED = REFERENCE_CONTENT + """MADDE 43
(1) Sonradan eklenen hüküm burada yer alır ve yeterince uzundur.
"""


COMPARISON_PAYLOAD = json.dumps(
    {
        "has_regulatory_change": True,
        "overall_summary": "Madde 41 değişti.",
        "importance": "high",
        "items": [
            {
                "candidate_id": "seg-2",
                "affected_article": "Madde 41",
                "affected_paragraph": None,
                "change_type": "amended",
                "old_text": "yazılı",
                "new_text": "biyometrik",
                "source_evidence": '"yazılı" ibaresi "biyometrik"',
                "reference_evidence": "kimliği yazılı olarak doğrulanır",
                "change_summary": "Yöntem değişti.",
                "confidence": 0.95,
            }
        ],
    },
    ensure_ascii=False,
)


BASE_PAYLOAD = {
    "name": "Otomatik Pipeline Monitor",
    "url": "https://example.com/rg",
    "topic": "Ödeme hizmetleri mevzuatı",
    "scope_type": "full_page",
    "schedule_type": "daily",
    "schedule_time": "09:00",
    "is_unlimited": True,
    "notify_in_app": True,
    "notify_email": False,
    "notification_email": None,
}


def payload(**overrides):
    data = dict(BASE_PAYLOAD)
    data.update(overrides)

    return data


# Referans yenileme fixture'i
FETCH_CALLS = []

CURRENT_CONTENT = {"value": REFERENCE_CONTENT}

FETCH_SHOULD_FAIL = {"value": False}


class FakeFetchResult:
    def __init__(self, url, html):
        self.url = url
        self.final_url = url
        self.status_code = 200
        self.content_type = "text/html; charset=utf-8"
        self.html = html
        self.encoding = "utf-8"


async def fake_fetch_website(url: str):

    FETCH_CALLS.append(url)

    if FETCH_SHOULD_FAIL["value"]:
        from app.services.web_fetcher import WebsiteFetchError

        raise WebsiteFetchError("kaynak gecici olarak erisilemiyor")

    body = CURRENT_CONTENT["value"].replace("\n", "<br>\n")

    return FakeFetchResult(
        url,
        f"<html><head><title>Test</title></head><body>{body}</body></html>",
    )


def run(coroutine):
    return asyncio.run(coroutine)


class FakeRunResult:
    def __init__(
        self,
        change=None,
        analysis=None,
        first_run=False,
        changed=True,
    ):
        self.first_run = first_run
        self.changed = changed
        self.change_record = change
        self.ai_analysis_record = analysis
        self.new_snapshot = None


def main() -> None:

    print("=" * 70)
    print("OTOMATIK REFERANS PIPELINE TESTLERI")
    print("=" * 70)

    app_source = read(APP_JSX)

    from google import genai

    real_client = genai.Client
    real_fetch = regulation_reference_service.fetch_website

    genai.Client = FakeGeminiClient
    regulation_reference_service.fetch_website = fake_fetch_website

    connection = engine.connect()
    transaction = connection.begin()

    session = Session(
        bind=connection,
        join_transaction_mode="create_savepoint",
    )

    app.dependency_overrides[get_db] = lambda: session

    client = TestClient(app)

    monitor_count_before = session.scalar(
        select(func.count()).select_from(Monitor)
    )

    comparison_count_before = session.scalar(
        select(func.count()).select_from(RegulationComparison)
    )

    try:

        # -------------------------------------------------
        # HAZIRLIK
        # -------------------------------------------------

        distractor = create_regulation_reference(
            db=session,
            title="Elektrik Piyasası Lisans Yönetmeliği",
            source_url="https://mevzuat.test.local/pipeline-1",
            content="MADDE 1 - elektrik içerik " * 20,
        )

        archive = create_regulation_reference(
            db=session,
            title=BASE_TITLE,
            source_url="https://mevzuat.test.local/pipeline-2",
            content=REFERENCE_CONTENT,
        )

        # Konsolide referans URETIM YOLUYLA olusturulur:
        # adres gercek mevzuat.gov.tr bicimindedir ve icerik
        # ayni fetch/extraction hattindan gecer. Boylece
        # yenileme kaynagi 'generic'e DUSURMEZ ve icerik
        # hash'i tutarli kalir.
        consolidated_url = (
            "https://www.mevzuat.gov.tr/mevzuat"
            "?MevzuatNo=39080&MevzuatTur=7&MevzuatTertip=5"
        )

        consolidated_content = run(
            regulation_reference_service
            .fetch_regulation_content(consolidated_url)
        )

        consolidated = create_regulation_reference(
            db=session,
            title=BASE_TITLE,
            source_url=consolidated_url,
            content=consolidated_content.text,
            source_type=consolidated_content.source_type,
            is_consolidated=consolidated_content.is_consolidated,
            version_date=consolidated_content.version_date,
        )

        check(
            "hazirlik: konsolide referans dogru "
            "siniflandirildi",
            consolidated.is_consolidated is True
            and consolidated.source_type == "mevzuat_gov_tr",
            f"type={consolidated.source_type} "
            f"cons={consolidated.is_consolidated}",
        )

        def build_change(monitor, added, relevant=True):

            old_snapshot = create_snapshot(
                session, monitor.id, "RG", "eski"
            )

            new_snapshot = create_snapshot(
                session, monitor.id, "RG", "eski\n" + added
            )

            change = create_change(
                db=session,
                monitor_id=monitor.id,
                old_snapshot_id=old_snapshot.id,
                new_snapshot_id=new_snapshot.id,
                relevant=relevant,
                relevance_score=0.9 if relevant else 0.1,
                added_lines=added.splitlines(),
                removed_lines=[],
            )

            analysis = create_ai_analysis(
                db=session,
                change_id=change.id,
                model_name="test-model",
                keyword_relevant=relevant,
                keyword_score=0.9 if relevant else 0.1,
                relevant=relevant,
                importance="high" if relevant else "low",
                category="policy",
                summary="Yönetmelik değişikliği.",
                notification_recommended=relevant,
            )

            return change, analysis

        # -------------------------------------------------
        # 1-4. SECIM MODU
        # -------------------------------------------------

        print("\n--- 1-4. referans secim modu ---")

        legacy = create_monitor(
            db=session,
            name="Mevcut Manuel Monitor",
            url="https://example.com/legacy",
            topic="konu",
            tracking_plan="regulation_monitor",
            reference_regulation_id=consolidated.id,
            scope_type="full_page",
            schedule_time="09:00",
        )

        check(
            "1. mod gondermeyen create_monitor MANUAL "
            "uretiyor",
            legacy.reference_selection_mode
            == REFERENCE_MODE_MANUAL,
            f"mod={legacy.reference_selection_mode}",
        )

        check(
            "1. bos/None mod MANUAL'e normalize ediliyor",
            resolve_reference_selection_mode(None)
            == REFERENCE_MODE_MANUAL
            and resolve_reference_selection_mode("")
            == REFERENCE_MODE_MANUAL
            and resolve_reference_selection_mode("uydurma")
            == REFERENCE_MODE_MANUAL,
        )

        automatic_response = client.post(
            "/api/monitors",
            json=payload(
                tracking_plan="regulation_monitor",
            ),
        )

        check(
            "2. yeni regulation monitor varsayilani "
            "AUTOMATIC",
            automatic_response.status_code == 201
            and automatic_response.json()[
                "reference_selection_mode"
            ] == REFERENCE_MODE_AUTOMATIC,
            f"status={automatic_response.status_code} "
            f"body={automatic_response.text[:150]}",
        )

        check(
            "3. automatic create referanssiz KABUL",
            automatic_response.status_code == 201
            and automatic_response.json()[
                "reference_regulation_id"
            ] is None,
        )

        manual_without_reference = client.post(
            "/api/monitors",
            json=payload(
                name="Manuel Referanssiz",
                tracking_plan="regulation_monitor",
                reference_selection_mode="manual",
            ),
        )

        check(
            "4. manual create referanssiz RED",
            manual_without_reference.status_code == 422,
            f"status={manual_without_reference.status_code}",
        )

        check(
            "4. gecersiz mod reddediliyor",
            client.post(
                "/api/monitors",
                json=payload(
                    tracking_plan="regulation_monitor",
                    reference_selection_mode="uydurma_mod",
                ),
            ).status_code == 422,
        )

        # -------------------------------------------------
        # 5-7. OTOMATIK ESLESME -> KARSILASTIRMA
        # -------------------------------------------------

        print("\n--- 5-7. otomatik eslesme ---")

        automatic_monitor = create_monitor(
            db=session,
            name="Otomatik Monitor",
            url="https://example.com/rg",
            topic="Ödeme hizmetleri mevzuatı",
            tracking_plan="regulation_monitor",
            reference_selection_mode=REFERENCE_MODE_AUTOMATIC,
            reference_regulation_id=None,
            scope_type="full_page",
            schedule_time="09:00",
        )

        check(
            "5. otomatik monitorde referans NULL",
            automatic_monitor.reference_regulation_id is None
            and is_automatic_reference_mode(automatic_monitor),
        )

        change, analysis = build_change(
            automatic_monitor,
            AMENDMENT,
        )

        NEXT_RESPONSE["error"] = None
        NEXT_RESPONSE["payload"] = COMPARISON_PAYLOAD

        GEMINI_CALLS.clear()

        outcome = run(
            run_regulation_comparison(
                db=session,
                monitor=automatic_monitor,
                result=FakeRunResult(change, analysis),
            )
        )

        check(
            "5. karsilastirma KENDILIGINDEN calisti",
            outcome.status == COMPARISON_COMPLETED,
            f"durum={outcome.status}",
        )

        check(
            "6. kullanici onayi GEREKMEDI "
            "(monitorde referans hala null)",
            automatic_monitor.reference_regulation_id is None
            and outcome.comparison is not None,
        )

        check(
            "7. matcher GEMINI CAGIRMADI "
            "(toplam 1 = yalniz karsilastirma)",
            len(GEMINI_CALLS) == 1
            and outcome.matcher_ai_used is False,
            f"cagri={len(GEMINI_CALLS)} "
            f"matcher_ai={outcome.matcher_ai_used}",
        )

        comparison = outcome.comparison

        check(
            "14. otomatik secilen referans comparison'a "
            "yazildi",
            comparison.reference_regulation_id
            == consolidated.id,
            f"kullanilan={comparison.reference_regulation_id} "
            f"beklenen={consolidated.id}",
        )

        check(
            "14. KONSOLIDE referans tercih edildi "
            "(arsiv degil)",
            comparison.reference_regulation_id != archive.id,
        )

        check(
            "14. alakasiz referans KULLANILMADI",
            comparison.reference_regulation_id
            != distractor.id,
        )

        check(
            "15. match status kalici",
            comparison.reference_match_status
            == MATCH_STATUS_MATCHED,
            f"durum={comparison.reference_match_status}",
        )

        check(
            "15. match method kalici",
            comparison.reference_match_method
            == MATCH_METHOD_AUTOMATIC_EXACT,
            f"yontem={comparison.reference_match_method}",
        )

        check(
            "15. match confidence kalici",
            comparison.reference_match_confidence is not None
            and comparison.reference_match_confidence >= 0.99,
            f"guven={comparison.reference_match_confidence}",
        )

        check(
            "15. freshness kalici",
            comparison.reference_freshness_status
            == FRESHNESS_FRESH,
            f"tazelik={comparison.reference_freshness_status}",
        )

        check(
            "5. karsilastirma dogru maddeyi buldu",
            len(comparison.items) == 1
            and comparison.items[0].affected_article
            == "Madde 41",
        )

        # -------------------------------------------------
        # 8-11. GUVENILIR ESLESME YOKSA CALISMAZ
        # -------------------------------------------------

        print("\n--- 8-11. guvenilir eslesme yoksa ---")

        def automatic_case(name, added_text, references_exist=True):
            """
            Otomatik monitorde tek bir calistirma yapar.
            """

            monitor = create_monitor(
                db=session,
                name=name,
                url="https://example.com/rg",
                topic="Ödeme hizmetleri mevzuatı",
                tracking_plan="regulation_monitor",
                reference_selection_mode=(
                    REFERENCE_MODE_AUTOMATIC
                ),
                reference_regulation_id=None,
                scope_type="full_page",
                schedule_time="09:00",
            )

            case_change, case_analysis = build_change(
                monitor,
                added_text,
            )

            GEMINI_CALLS.clear()

            return run(
                run_regulation_comparison(
                    db=session,
                    monitor=monitor,
                    result=FakeRunResult(
                        case_change,
                        case_analysis,
                    ),
                )
            )

        # no_match: kayitli referanslarla ilgisiz degisiklik
        unrelated = """SU KİRLİLİĞİ KONTROLÜ YÖNETMELİĞİNDE DEĞİŞİKLİK
YAPILMASINA DAİR YÖNETMELİK
MADDE 1- 1/1/2010 tarihli ve 27000 sayılı Resmî Gazete'de yayımlanan Su
Kirliliği Kontrolü Yönetmeliğinin 5 inci maddesi değiştirilmiştir."""

        no_match_outcome = automatic_case(
            "Otomatik NoMatch",
            unrelated,
        )

        check(
            "9. no_match -> karsilastirma YOK",
            no_match_outcome.status
            == COMPARISON_SKIPPED_NO_AUTO_MATCH
            and no_match_outcome.match_status
            == MATCH_STATUS_NO_MATCH,
            f"durum={no_match_outcome.status} "
            f"match={no_match_outcome.match_status}",
        )

        check(
            "9. no_match kaydinda referans NULL "
            "(uydurma referans yok)",
            no_match_outcome.comparison is not None
            and no_match_outcome.comparison
            .reference_regulation_id is None,
        )

        check(
            "9. no_match kaydinda madde YOK",
            no_match_outcome.comparison is not None
            and len(no_match_outcome.comparison.items) == 0,
        )

        # no_base_name
        no_base = automatic_case(
            "Otomatik NoBaseName",
            "Sitede yeni bir duyuru yayımlandı ve güncellendi.",
        )

        check(
            "10. no_base_name -> karsilastirma YOK",
            no_base.status == COMPARISON_SKIPPED_NO_AUTO_MATCH
            and no_base.match_status
            == MATCH_STATUS_NO_BASE_NAME,
            f"match={no_base.match_status}",
        )

        check(
            "10. base ad cikmayinca GEMINI CAGRILMADI",
            len(GEMINI_CALLS) == 0,
            f"cagri={len(GEMINI_CALLS)}",
        )

        # -------------------------------------------------
        # 12-13. MANUEL MOD
        # -------------------------------------------------

        print("\n--- 12-13. manuel mod ---")

        manual_monitor = create_monitor(
            db=session,
            name="Manuel Monitor",
            url="https://example.com/rg",
            topic="Ödeme hizmetleri mevzuatı",
            tracking_plan="regulation_monitor",
            reference_selection_mode=REFERENCE_MODE_MANUAL,

            # Kullanici BILEREK arsiv surumunu secti.
            reference_regulation_id=archive.id,
            scope_type="full_page",
            schedule_time="09:00",
        )

        manual_change, manual_analysis = build_change(
            manual_monitor,
            AMENDMENT,
        )

        NEXT_RESPONSE["payload"] = COMPARISON_PAYLOAD
        GEMINI_CALLS.clear()

        manual_outcome = run(
            run_regulation_comparison(
                db=session,
                monitor=manual_monitor,
                result=FakeRunResult(
                    manual_change,
                    manual_analysis,
                ),
            )
        )

        check(
            "12. manuel mod SECILEN referansi kullandi",
            manual_outcome.comparison is not None
            and manual_outcome.comparison
            .reference_regulation_id == archive.id,
            f"kullanilan={manual_outcome.comparison.reference_regulation_id if manual_outcome.comparison else None}",
        )

        check(
            "13. manuel secim DEGISTIRILMEDI "
            "(konsolide daha iyi olsa bile)",
            manual_monitor.reference_regulation_id
            == archive.id,
        )

        check(
            "13. manuel modda yontem 'manual'",
            manual_outcome.comparison is not None
            and manual_outcome.comparison
            .reference_match_method == MATCH_METHOD_MANUAL,
            f"yontem={manual_outcome.comparison.reference_match_method if manual_outcome.comparison else None}",
        )

        check(
            "13. manuel mod EK GEMINI MALIYETI URETMEDI",
            len(GEMINI_CALLS) == 1,
            f"cagri={len(GEMINI_CALLS)} (1 = yalniz karsilastirma)",
        )

        # -------------------------------------------------
        # 16-19. TAZELIK
        # -------------------------------------------------

        print("\n--- 16-19. referans tazeligi ---")

        FETCH_CALLS.clear()
        FETCH_SHOULD_FAIL["value"] = False
        CURRENT_CONTENT["value"] = REFERENCE_CONTENT

        fresh_outcome = run(
            ensure_reference_fresh(
                db=session,
                reference=consolidated,
            )
        )

        check(
            "16. taze konsolide referans -> FETCH YOK",
            fresh_outcome.status == FRESHNESS_FRESH
            and len(FETCH_CALLS) == 0,
            f"durum={fresh_outcome.status} "
            f"fetch={len(FETCH_CALLS)}",
        )

        check(
            "16. arsiv referansi tazelik kapsaminda DEGIL",
            run(
                ensure_reference_fresh(
                    db=session,
                    reference=archive,
                )
            ).status == FRESHNESS_NOT_APPLICABLE,
        )

        # Bayat yap
        consolidated.last_verified_at = (
            datetime.utcnow() - timedelta(hours=48)
        )
        session.commit()

        FETCH_CALLS.clear()

        stale_outcome = run(
            ensure_reference_fresh(
                db=session,
                reference=consolidated,
            )
        )

        check(
            "17. bayat konsolide referans YENILENDI",
            stale_outcome.status == FRESHNESS_REFRESHED
            and len(FETCH_CALLS) == 1,
            f"durum={stale_outcome.status} "
            f"fetch={len(FETCH_CALLS)}",
        )

        check(
            "17. yenileme sonrasi last_verified_at guncel",
            consolidated.last_verified_at is not None
            and (
                datetime.utcnow()
                - consolidated.last_verified_at
            ) < timedelta(minutes=5),
        )

        # Icerik degisti
        consolidated.last_verified_at = (
            datetime.utcnow() - timedelta(hours=48)
        )
        session.commit()

        CURRENT_CONTENT["value"] = REFERENCE_CONTENT_UPDATED

        previous_hash = consolidated.content_hash

        changed_outcome = run(
            ensure_reference_fresh(
                db=session,
                reference=consolidated,
            )
        )

        check(
            "18. degisen icerik refreshed_changed",
            changed_outcome.status
            == FRESHNESS_REFRESHED_CHANGED
            and changed_outcome.content_changed is True,
            f"durum={changed_outcome.status}",
        )

        check(
            "18. yeni hash ile karsilastirma yapilabilir",
            consolidated.content_hash != previous_hash
            and "MADDE 43" in consolidated.content,
        )

        # Yenileme basarisiz
        consolidated.last_verified_at = (
            datetime.utcnow() - timedelta(hours=48)
        )
        session.commit()

        FETCH_SHOULD_FAIL["value"] = True

        failed_outcome = run(
            ensure_reference_fresh(
                db=session,
                reference=consolidated,
            )
        )

        FETCH_SHOULD_FAIL["value"] = False

        check(
            "19. yenileme basarisiz -> stale_unverified",
            failed_outcome.status
            == FRESHNESS_STALE_UNVERIFIED,
            f"durum={failed_outcome.status}",
        )

        check(
            "19. onbellekteki metin hala KULLANILABILIR",
            failed_outcome.usable is True,
        )

        check(
            "19. hata mesaji TEMIZ",
            failed_outcome.error is not None
            and len(failed_outcome.error) <= 200
            and "Traceback" not in failed_outcome.error,
        )

        check(
            "19. basarisiz yenileme icerigi BOZMADI",
            "MADDE 43" in consolidated.content,
        )

        # -------------------------------------------------
        # 20-21. CALISTIRMA VE TETIKLEME
        # -------------------------------------------------

        print("\n--- 20-21. calistirma ve tetikleme ---")

        run_monitor = session.get(
            Monitor,
            automatic_response.json()["id"],
        )

        check(
            "21. ilk snapshot -> matcher CALISMAZ",
            run(
                run_regulation_comparison(
                    db=session,
                    monitor=run_monitor,
                    result=FakeRunResult(
                        first_run=True,
                        changed=False,
                    ),
                )
            ).status == COMPARISON_SKIPPED_FIRST_RUN,
        )

        check(
            "21. degisiklik yok -> matcher CALISMAZ",
            run(
                run_regulation_comparison(
                    db=session,
                    monitor=run_monitor,
                    result=FakeRunResult(changed=False),
                )
            ).status == COMPARISON_SKIPPED_NO_CHANGE,
        )

        irrelevant_change, irrelevant_analysis = build_change(
            run_monitor,
            AMENDMENT,
            relevant=False,
        )

        GEMINI_CALLS.clear()

        check(
            "21. ilgisiz degisiklik -> matcher CALISMAZ",
            run(
                run_regulation_comparison(
                    db=session,
                    monitor=run_monitor,
                    result=FakeRunResult(
                        irrelevant_change,
                        irrelevant_analysis,
                    ),
                )
            ).status == COMPARISON_SKIPPED_IRRELEVANT
            and len(GEMINI_CALLS) == 0,
            f"cagri={len(GEMINI_CALLS)}",
        )

        website_monitor = create_monitor(
            db=session,
            name="Pipeline Website Monitor",
            url="https://example.com/x",
            topic="konu",
            scope_type="full_page",
            schedule_time="09:00",
        )

        check(
            "21. website_monitor -> matcher CALISMAZ",
            run(
                run_regulation_comparison(
                    db=session,
                    monitor=website_monitor,
                    result=FakeRunResult(),
                )
            ).status is None,
        )

        # -------------------------------------------------
        # 28-30. BILDIRIM DETAYI
        # -------------------------------------------------

        print("\n--- 28-30. bildirim detayi ---")

        def build_notification(monitor, change, analysis):
            return create_notification(
                db=session,
                monitor_id=monitor.id,
                change_id=change.id,
                ai_analysis_id=analysis.id,
                notification_type="in_app",
                title="Test",
                message="Test",
                importance="high",
                category="policy",
                recipient=None,
            )

        matched_notification = build_notification(
            automatic_monitor,
            change,
            analysis,
        )

        matched_detail = client.get(
            f"/api/notifications/"
            f"{matched_notification.id}/detail"
        ).json()

        nested = (
            matched_detail.get("regulation_comparison") or {}
        )

        check(
            "28. detay otomatik eslestirme bilgisini "
            "tasiyor",
            nested.get("reference_match_method")
            == MATCH_METHOD_AUTOMATIC_EXACT
            and nested.get("reference_match_confidence")
            is not None,
            f"yontem={nested.get('reference_match_method')}",
        )

        check(
            "28. detay tazelik bilgisini tasiyor",
            nested.get("reference_freshness_status")
            is not None,
        )

        check(
            "28. detay kullanilan referansin ADINI tasiyor",
            nested.get("reference_regulation_title")
            == BASE_TITLE,
        )

        # no_match bildirimi
        no_match_monitor_change = (
            no_match_outcome.comparison.change_id
        )

        no_match_change = session.get(
            RegulationComparison,
            no_match_outcome.comparison.id,
        )

        check(
            "29. no_match kaydi detayda gorunebiliyor",
            no_match_change is not None
            and no_match_change.reference_match_status
            == MATCH_STATUS_NO_MATCH,
        )

        check(
            "30. ambiguous/no_match durumlari ayirt "
            "edilebiliyor",
            MATCH_STATUS_AMBIGUOUS != MATCH_STATUS_NO_MATCH
            and MATCH_STATUS_NO_REFERENCES
            != MATCH_STATUS_NO_MATCH,
        )

    finally:
        app.dependency_overrides.clear()

        session.close()
        transaction.rollback()
        connection.close()

        genai.Client = real_client
        regulation_reference_service.fetch_website = real_fetch

        print("\n" + "-" * 70)
        print("Dis transaction geri alindi.")

    # -----------------------------------------------------
    # 22-27. ARAYUZ
    # -----------------------------------------------------

    print("\n--- 22-27. arayuz ---")

    monitor_form = section(
        app_source,
        "function MonitorForm(",
        "function NewMonitorPage()",
    )

    defaults = section(
        app_source,
        "const NEW_MONITOR_DEFAULTS",
        "function monitorToFormValues(",
    )

    form_values = section(
        app_source,
        "function monitorToFormValues(monitor)",
        "// Yeni izleme ve izleme düzenleme ekranları",
    )

    comparison_section = section(
        app_source,
        "function RegulationComparisonSection({",
        "function ChangeSection({",
    )

    check(
        "22. referans secim modu basligi var",
        "Referans seçimi" in monitor_form,
    )

    check(
        "22. iki mod secenegi var",
        "Otomatik belirle" in monitor_form
        and "Manuel seç" in monitor_form,
    )

    check(
        # OTOMATIK MOD ACIKLAMASI
        #
        # Artik yalnizca KAYITLI mevzuatlara bakilmiyor:
        # bulunamazsa Mevzuat Bilgi Sistemi'nde resmi arama
        # yapiliyor. Aciklama bunu soylemeli; aksi halde
        # arayuz gercekten yapilandan azini anlatir.
        "22. otomatik mod aciklamasi dogru",
        "Referans önce kayıtlı mevzuatlarda aranır"
        in monitor_form
        and "Mevzuat Bilgi Sistemi" in monitor_form,
    )

    check(
        "22. manuel mod aciklamasi dogru",
        "referans mevzuatı siz seçersiniz" in monitor_form,
    )

    check(
        "22. yeni izleme varsayilani OTOMATIK",
        "reference_selection_mode: REFERENCE_MODE_AUTOMATIC"
        in defaults,
    )

    check(
        "23. mevcut monitor MANUAL aciliyor "
        "(sessizce otomatige gecmiyor)",
        "REFERENCE_MODE_MANUAL" in form_values,
        "monitorToFormValues varsayilani manual",
    )

    check(
        "24. otomatik modda secici GIZLI",
        "{!isAutomaticReference &&" in monitor_form,
    )

    check(
        "24. otomatik modda kullaniciya aciklama var",
        "İlgili referans önce kayıtlı mevzuatlar"
        in monitor_form
        and "konsolide referans" in monitor_form,
    )

    check(
        "25. manuel modda secici GORUNUR",
        "reference-select" in monitor_form,
    )

    check(
        "25. referans zorunlulugu YALNIZCA manuel modda",
        "!isAutomaticReference &&" in monitor_form
        and "!referenceSelected" in monitor_form,
    )

    check(
        "26. oneri arayuzu duruyor",
        "Referansı Otomatik Bul" in monitor_form
        and "matchRegulationReference" in app_source,
    )

    check(
        "27. otomatik pipeline oneri butonuna BAGLI DEGIL",
        "suggestOpen" not in section(
            app_source,
            "function RegulationComparisonSection({",
            "function ChangeSection({",
        ),
        "karsilastirma paneli oneri state'ini kullanmiyor",
    )

    check(
        "28. detayda eslestirme yontemi gosteriliyor",
        "formatMatchMethod(" in comparison_section
        and "Eşleşme" in comparison_section,
    )

    check(
        "28. detayda guven gosteriliyor",
        "formatConfidencePercent(" in comparison_section,
    )

    check(
        "29. no_match mesaji var",
        "otomatik olarak bulunamadı" in app_source,
    )

    check(
        "30. ambiguous mesaji var",
        "Birden fazla olası referans bulundu"
        in app_source
        and "doğrulama gerekiyor" in app_source,
    )

    check(
        "29-30. bu mesajlar YALNIZCA referans yokken "
        "gosteriliyor",
        "reference_regulation_id === null"
        in comparison_section,
    )

    check(
        "bayat referans uyarisi var",
        "STALE_REFERENCE_WARNING" in comparison_section,
    )

    check(
        "guvenli render korunuyor",
        "dangerouslySetInnerHTML" not in monitor_form
        and "dangerouslySetInnerHTML" not in comparison_section,
    )

    # -----------------------------------------------------
    # KALICI KAYIT BIRAKILMADI MI
    # -----------------------------------------------------

    from app.core.database import SessionLocal

    verify = SessionLocal()

    try:
        monitor_count_after = verify.scalar(
            select(func.count()).select_from(Monitor)
        )

        comparison_count_after = verify.scalar(
            select(func.count()).select_from(RegulationComparison)
        )

        check(
            "veritabaninda monitor sayisi degismedi",
            monitor_count_after == monitor_count_before,
            f"once={monitor_count_before} "
            f"sonra={monitor_count_after}",
        )

        check(
            "test karsilastirmasi kalmadi",
            comparison_count_after == comparison_count_before,
            f"once={comparison_count_before} "
            f"sonra={comparison_count_after}",
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
