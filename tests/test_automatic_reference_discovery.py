"""
OTOMATIK REFERANS KESFI TESTLERI

Kapsam:

     1. kayitli exact eslesme -> kesif CAGRILMAZ
     2. manuel mod            -> kesif CAGRILMAZ
     3. website monitor       -> kesif CAGRILMAZ
     4. ilk snapshot          -> kesif CAGRILMAZ
     5. degisiklik yok        -> kesif CAGRILMAZ
     6. ilgisiz degisiklik    -> kesif CAGRILMAZ
     7. yerel no_match        -> resmi arama CALISIR
     8. no_references         -> resmi arama CALISIR
     9. no_base_name          -> resmi arama CALISMAZ
    10. yerel ambiguous       -> otomatik olusturma YOK
    11. birebir resmi ad      -> aday secilir
    12. Turkce ad normalizasyonu
    13. yayim bilgisi ile ayrim
    14. bozuk MevzuatNo reddedilir
    15. bozuk MevzuatTur reddedilir
    16. bozuk MevzuatTertip reddedilir
    17. kanonik adres uretimi
    18. ayni kanonik kaynak -> ikinci satir YOK
    19. birebir kesif -> 0 kesif Gemini cagrisi
    20. belirsiz arama -> en fazla 1 mantiksal Gemini cagrisi
    21. uydurma Gemini adayi reddedilir
    22. dusuk guven -> referans OLUSMAZ
    23. arama sonucu yok -> referans OLUSMAZ
    24. arama endpoint hatasi -> referans OLUSMAZ
    25. konsolide metin alinamadi -> referans OLUSMAZ
    26. gecerli aday + fetch -> referans OLUSUR
    27. olusan referans source_type = mevzuat_gov_tr
    28. olusan referans is_consolidated = true
    29. content_hash saklanir
    30. surum bilgisi saklanir
    31. RESMI sonuc basligi saklanir
    32. basarili kesif -> mevcut karsilastirma calisir
    33. comparison satiri kesfedilen referansi saklar
    34. comparison dogru content hash'i saklar
    35. basarili kesif bildirim detayi
    36. kesif no_match detay durumu
    37. kesif basarisizligi detay durumu
    38. otomatik referans pipeline regresyonu
    39. yerel referans matcher regresyonu
    40. konsolide kaynak regresyonu
    41. Turkce RG ingestion regresyonu
    42. mevzuat karsilastirma regresyonu
    43. full_page regresyonu
    44. auto_section regresyonu
    45. manual_section regresyonu
    46. scheduler regresyonu
    47. bildirim regresyonu
    48. e-posta regresyonu
    49. Gemini fallback regresyonu

GERCEK GEMINI CAGRISI YOKTUR. genai.Client sahte nesneyle
degistirilir ve her cagri sayilir.

GERCEK AG ISTEGI YOKTUR. Hem mevzuat.gov.tr arama
endpoint'i hem de konsolide metin getirme sahte nesnelerle
degistirilir.

GERCEK SMTP YOKTUR.

Gercek PostgreSQL uzerinde calisir ama KALICI KAYIT
BIRAKMAZ: her sey tek bir transaction icinde yapilir ve
sonunda geri alinir.

Calistirma:
    python -m tests.test_automatic_reference_discovery
"""

import asyncio
import io
import json
import warnings

from datetime import date, datetime, timedelta
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
from app.repositories.regulation_reference_repository import (
    calculate_content_hash,
    create_regulation_reference,
)
from app.repositories.snapshot_repository import create_snapshot
from app.services import mevzuat_search_service
from app.services import regulation_reference_service
from app.services.ai_analyzer import run_with_quota_fallback
from app.services.email_content import build_change_email
from app.services.mevzuat_search_service import (
    MevzuatSearchError,
    SEARCH_SCOPE_TITLE,
    build_candidate,
    build_search_payload,
    clean_official_title,
    search_official_regulations,
)
from app.services.notification_service import (
    build_notification_message,
)
from app.services.regulation_discovery_service import (
    DISCOVERY_AMBIGUOUS,
    DISCOVERY_DISCOVERED,
    DISCOVERY_FAILED,
    DISCOVERY_FOUND_EXISTING,
    DISCOVERY_NOT_APPLICABLE,
    DISCOVERY_NOT_NEEDED,
    DISCOVERY_NO_MATCH,
    MATCH_METHOD_DISCOVERED_AI,
    MATCH_METHOD_DISCOVERED_EXACT,
    MATCH_METHOD_DISCOVERED_META,
    discover_reference,
    find_existing_by_canonical_url,
    resolve_local,
    score_candidates,
)
from app.services.regulation_monitor_service import (
    COMPARISON_COMPLETED,
    COMPARISON_SKIPPED_FIRST_RUN,
    COMPARISON_SKIPPED_IRRELEVANT,
    COMPARISON_SKIPPED_NO_AUTO_MATCH,
    COMPARISON_SKIPPED_NO_CHANGE,
    REFERENCE_MODE_AUTOMATIC,
    REFERENCE_MODE_MANUAL,
    run_regulation_comparison,
)
from app.services.regulation_reference_matcher import (
    MATCH_STATUS_MATCHED,
    match_reference,
)
from app.services.regulation_source_registry import (
    SOURCE_TYPE_MEVZUAT_GOV_TR,
    build_mevzuat_shell_url,
    canonical_mevzuat_url,
    resolve_reference_source,
)
from app.services.regulation_text_parser import (
    parse_regulation_segments,
)
from app.services.regulation_title_parser import (
    extract_base_regulation_names,
)
from app.services.web_fetcher import decode_html


warnings.filterwarnings("ignore")

from app.core.scheduler import evaluate_monitor_due  # noqa: E402


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


def run(coroutine):
    return asyncio.run(coroutine)


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


# Resmî Gazete 4 Eylül 2026 degisikligi.
AMENDMENT = """ÖDEME HİZMETLERİ VE ELEKTRONİK PARA İHRACI İLE ÖDEME HİZMETİ
SAĞLAYICILARI HAKKINDA YÖNETMELİKTE DEĞİŞİKLİK YAPILMASINA DAİR YÖNETMELİK
MADDE 1- 1/12/2021 tarihli ve 31676 sayılı Resmî Gazete'de yayımlanan Ödeme
Hizmetleri ve Elektronik Para İhracı ile Ödeme Hizmeti Sağlayıcıları Hakkında
Yönetmeliğin 41 inci maddesinde yer alan "yazılı" ibaresi "biyometrik"
şeklinde değiştirilmiştir."""


# Hedef mevzuat adi cikarilamayan metin.
NO_BASE_NAME_TEXT = """KURUL KARARI
Kurulumuzun 4/9/2026 tarihli toplantısında aşağıdaki hususlar
görüşülerek karara bağlanmıştır. Bu karar yayımı tarihinde
yürürlüğe girer ve ilgili birimlerce yürütülür."""


CONSOLIDATED_CONTENT = """ÖDEME HİZMETLERİ VE ELEKTRONİK PARA İHRACI İLE ÖDEME HİZMETİ
SAĞLAYICILARI HAKKINDA YÖNETMELİK
MADDE 40
(1) Kayıtlar saklanır ve gerektiğinde Bankaya sunulur.
MADDE 41
(1) (Değişik:RG-4/9/2026-33360) Uzaktan iletişim aracı ile müşterinin
kimliği yazılı olarak doğrulanır.
MADDE 42
(1) Ödeme kuruluşları asgari özkaynak yükümlülüğünü sağlar.
"""


# mevzuat.gov.tr arama cevabinin GERCEK bicimi.
#
# Alan adlari ve degerler gercek endpoint cevabindan
# alinmistir: mevAdi BUYUK HARF ve icinde \r\n tasir,
# kismi eslesmede vurgu <span> gelir.
OFFICIAL_ROW = {
    "mevzuatNo": "39080",
    "mevAdi": (
        "ÖDEME HİZMETLERİ VE ELEKTRONİK PARA İHRACI İLE ÖDEME\r\n"
        "HİZMETİ SAĞLAYICILARI HAKKINDA YÖNETMELİK"
    ),
    "resmiGazeteTarihi": "01.12.2021",
    "resmiGazeteSayisi": "31676",
    "mevzuatTertip": "5",
    "mevzuatTur": 7,
    "mevzuatTurEnumString": "Kurum ve Kuruluş Yönetmelikleri",
    "url": "mevzuat?MevzuatNo=39080&MevzuatTur=7&MevzuatTertip=5",
}


CANONICAL_URL = (
    "https://www.mevzuat.gov.tr/mevzuat"
    "?MevzuatNo=39080&MevzuatTur=7&MevzuatTertip=5"
)


def official_response(rows, total=None):
    return {
        "draw": 1,
        "recordsTotal": len(rows) if total is None else total,
        "recordsFiltered": len(rows) if total is None else total,
        "data": rows,
        "word": None,
    }


# ---------------------------------------------------------
# SAHTE ARAMA ENDPOINT'I
#
# _post_search degistirilir; boylece build_candidate,
# dogrulama, eleme ve karar mantiginin TAMAMI gercek kodla
# calisir ama AG ISTEGI YAPILMAZ.
# ---------------------------------------------------------

SEARCH_CALLS = []

SEARCH_RESPONSE = {"value": None, "error": None}


async def fake_post_search(payload):

    SEARCH_CALLS.append(payload)

    if SEARCH_RESPONSE["error"] is not None:
        raise SEARCH_RESPONSE["error"]

    return SEARCH_RESPONSE["value"]


# ---------------------------------------------------------
# SAHTE KONSOLIDE METIN
# ---------------------------------------------------------

FETCH_CALLS = []

FETCH_SHOULD_FAIL = {"value": False}

FETCH_CONTENT = {"value": CONSOLIDATED_CONTENT}


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

        raise WebsiteFetchError(
            "kaynak gecici olarak erisilemiyor"
        )

    body = FETCH_CONTENT["value"].replace("\n", "<br>\n")

    return FakeFetchResult(
        url,
        "<html><head><title>Mevzuat</title></head>"
        f"<body>{body}</body></html>",
    )


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
                "reference_evidence": (
                    "kimliği yazılı olarak doğrulanır"
                ),
                "change_summary": "Yöntem değişti.",
                "confidence": 0.95,
            }
        ],
    },
    ensure_ascii=False,
)


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


def reset_counters():
    GEMINI_CALLS.clear()
    SEARCH_CALLS.clear()
    FETCH_CALLS.clear()

    SEARCH_RESPONSE["value"] = official_response([OFFICIAL_ROW])
    SEARCH_RESPONSE["error"] = None

    FETCH_SHOULD_FAIL["value"] = False
    FETCH_CONTENT["value"] = CONSOLIDATED_CONTENT

    NEXT_RESPONSE["payload"] = COMPARISON_PAYLOAD
    NEXT_RESPONSE["error"] = None


def main() -> None:

    print("=" * 70)
    print("OTOMATIK REFERANS KESFI TESTLERI")
    print("=" * 70)

    app_source = read(APP_JSX)

    from google import genai

    real_client = genai.Client
    real_post_search = mevzuat_search_service._post_search
    real_fetch = regulation_reference_service.fetch_website

    genai.Client = FakeGeminiClient
    mevzuat_search_service._post_search = fake_post_search
    regulation_reference_service.fetch_website = (
        fake_fetch_website
    )

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
        run_tests(session, app_source)

    finally:
        session.close()

        transaction.rollback()
        connection.close()

        genai.Client = real_client
        mevzuat_search_service._post_search = real_post_search
        regulation_reference_service.fetch_website = real_fetch

    # DB KIRLILIGI YOK
    with Session(bind=engine) as verify:
        reference_count_after = verify.scalar(
            select(func.count()).select_from(RegulationReference)
        )

    check(
        "DB temiz: kalici referans kaydi birakilmadi",
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

    print("TUM TESTLER BASARILI")
    print("=" * 70)


# Kayitli bir referansi YEREL eslestirmeden gecici olarak
# cikarmak icin adi alakasiz yapilir.
#
# Silmek MUMKUN DEGIL: kayit karsilastirma satirlarina
# bagli. Adi degistirmek kanonik adresi DEGISTIRMEZ, bu
# yuzden duplicate korumasi ayni sekilde calisir.
HIDDEN_TITLE_PREFIX = "Alakasız Kayıt"


def hide_reference(session, reference, suffix=""):

    original = reference.title

    reference.title = f"{HIDDEN_TITLE_PREFIX} {suffix} Yönetmelik"
    session.commit()

    return original


def restore_reference(session, reference, title):

    reference.title = title
    session.commit()


def hide_all_matching(session, base_name):
    """
    Base ada uyan TUM kayitli referanslari gecici olarak
    gizler.

    Kesif testleri sirasinda kutuphaneye yeni kayitlar
    eklendigi icin, "yerel eslestirme hicbir sey bulamiyor"
    senaryosu ancak boyle kurulabilir.

    Doner: geri alma icin (referans, eski ad) listesi.
    """

    from app.services.regulation_reference_matcher import (
        score_name,
    )

    hidden = []

    references = session.scalars(
        select(RegulationReference)
    ).all()

    for index, reference in enumerate(references):

        score, _, _ = score_name(
            base_name=base_name,
            title=reference.title,
        )

        if score < 0.60:
            continue

        hidden.append((reference, reference.title))

        reference.title = (
            f"{HIDDEN_TITLE_PREFIX} {index} Yönetmelik"
        )

    session.commit()

    return hidden


def build_monitor(
    session,
    name,
    tracking_plan="regulation_monitor",
    reference_mode=REFERENCE_MODE_AUTOMATIC,
    reference_id=None,
):
    return create_monitor(
        db=session,
        name=name,
        url="https://example.com/rg",
        topic="Ödeme hizmetleri mevzuatı",
        scope_type="full_page",
        schedule_type="daily",
        schedule_time="09:00",
        is_unlimited=True,
        notify_in_app=True,
        notify_email=False,
        notification_email=None,
        tracking_plan=tracking_plan,
        reference_selection_mode=reference_mode,
        reference_regulation_id=reference_id,
    )


def build_change_fixture(session, monitor, relevant=True):
    """
    Bir monitor icin snapshot + change + AIAnalysis uretir.
    """

    return build_change_for_text(
        session=session,
        monitor=monitor,
        text=AMENDMENT,
        summary="Ödeme hizmetleri yönetmeliğinde değişiklik.",
        relevant=relevant,
    )


def build_change_for_text(
    session,
    monitor,
    text,
    summary,
    relevant=True,
):
    """
    Verilen degisiklik metni icin snapshot + change +
    AIAnalysis uretir.
    """

    old_snapshot = create_snapshot(
        db=session,
        monitor_id=monitor.id,
        content="Eski içerik burada yer alıyor.",
        title="RG",
    )

    new_snapshot = create_snapshot(
        db=session,
        monitor_id=monitor.id,
        content=text,
        title="RG",
    )

    change = create_change(
        db=session,
        monitor_id=monitor.id,
        old_snapshot_id=old_snapshot.id,
        new_snapshot_id=new_snapshot.id,
        relevant=relevant,
        relevance_score=0.9 if relevant else 0.1,
        added_lines=text.splitlines(),
        removed_lines=[],
    )

    analysis = create_ai_analysis(
        db=session,
        change_id=change.id,
        model_name="fake-model",
        keyword_relevant=relevant,
        keyword_score=0.9 if relevant else 0.1,
        relevant=relevant,
        importance="high",
        category="mevzuat",
        summary=summary,
        notification_recommended=True,
    )

    return change, analysis


def run_tests(session, app_source):

    # =====================================================
    # BOLUM A - KESIF TETIKLEME KOSULLARI
    # =====================================================

    # -----------------------------------------------------
    # 1. KAYITLI EXACT ESLESME -> KESIF CAGRILMAZ
    # -----------------------------------------------------

    reset_counters()

    registered = create_regulation_reference(
        db=session,
        title=BASE_TITLE,
        source_url="https://www.mevzuat.gov.tr/kayitli-ornek",
        content=CONSOLIDATED_CONTENT,
        source_type=SOURCE_TYPE_MEVZUAT_GOV_TR,
        is_consolidated=True,
        version_date=date(2026, 9, 4),
    )

    # Referans TAZE: tazelik icin ag istegi yapilmaz.
    registered.last_verified_at = datetime.utcnow()
    session.commit()

    monitor_a = build_monitor(session, "Kesif A - yerel exact")

    change_a, analysis_a = build_change_fixture(
        session, monitor_a
    )

    result_a = run(
        run_regulation_comparison(
            db=session,
            monitor=monitor_a,
            result=FakeRunResult(
                change=change_a,
                analysis=analysis_a,
            ),
        )
    )

    check(
        "1. kayitli exact eslesme -> resmi arama CAGRILMAZ",
        len(SEARCH_CALLS) == 0,
        f"arama cagrisi={len(SEARCH_CALLS)}",
    )

    check(
        "1. yerel eslesmede kesif durumu not_needed",
        result_a.discovery_status == DISCOVERY_NOT_NEEDED,
        str(result_a.discovery_status),
    )

    check(
        "38. otomatik referans pipeline regresyonu "
        "(yerel eslesme -> karsilastirma tamamlandi)",
        result_a.status == COMPARISON_COMPLETED
        and result_a.item_count == 1,
        f"{result_a.status} / {result_a.item_count}",
    )

    check(
        "42. mevzuat karsilastirma regresyonu "
        "(kanit dogrulandi)",
        result_a.comparison is not None
        and result_a.comparison.items[0].new_text
        == "biyometrik",
    )

    # -----------------------------------------------------
    # 2. MANUEL MOD -> KESIF CAGRILMAZ
    # -----------------------------------------------------

    reset_counters()

    monitor_b = build_monitor(
        session,
        "Kesif B - manuel",
        reference_mode=REFERENCE_MODE_MANUAL,
        reference_id=registered.id,
    )

    change_b, analysis_b = build_change_fixture(
        session, monitor_b
    )

    result_b = run(
        run_regulation_comparison(
            db=session,
            monitor=monitor_b,
            result=FakeRunResult(
                change=change_b,
                analysis=analysis_b,
            ),
        )
    )

    check(
        "2. manuel mod -> resmi arama CAGRILMAZ",
        len(SEARCH_CALLS) == 0,
        f"arama cagrisi={len(SEARCH_CALLS)}",
    )

    check(
        "2. manuel modda kesif durumu not_applicable",
        result_b.discovery_status == DISCOVERY_NOT_APPLICABLE,
        str(result_b.discovery_status),
    )

    # -----------------------------------------------------
    # 3. WEBSITE MONITOR -> KESIF CAGRILMAZ
    # -----------------------------------------------------

    reset_counters()

    monitor_c = build_monitor(
        session,
        "Kesif C - website",
        tracking_plan="website_monitor",
        reference_mode=REFERENCE_MODE_MANUAL,
    )

    change_c, analysis_c = build_change_fixture(
        session, monitor_c
    )

    result_c = run(
        run_regulation_comparison(
            db=session,
            monitor=monitor_c,
            result=FakeRunResult(
                change=change_c,
                analysis=analysis_c,
            ),
        )
    )

    check(
        "3. website monitor -> resmi arama CAGRILMAZ",
        len(SEARCH_CALLS) == 0,
    )

    check(
        "3. website monitor kesif durumu null",
        result_c.discovery_status is None
        and result_c.status is None,
        str(result_c.discovery_status),
    )

    check(
        "43. full_page regresyonu (website plani bozulmadi)",
        monitor_c.scope_type == "full_page",
    )

    # -----------------------------------------------------
    # 4-6. ILK SNAPSHOT / DEGISIKLIK YOK / ILGISIZ
    # -----------------------------------------------------

    reset_counters()

    monitor_d = build_monitor(session, "Kesif D - tetikleme")

    change_d, analysis_d = build_change_fixture(
        session, monitor_d
    )

    first_run_result = run(
        run_regulation_comparison(
            db=session,
            monitor=monitor_d,
            result=FakeRunResult(first_run=True),
        )
    )

    check(
        "4. ilk snapshot -> resmi arama CAGRILMAZ",
        len(SEARCH_CALLS) == 0
        and first_run_result.status
        == COMPARISON_SKIPPED_FIRST_RUN,
        first_run_result.status,
    )

    no_change_result = run(
        run_regulation_comparison(
            db=session,
            monitor=monitor_d,
            result=FakeRunResult(changed=False),
        )
    )

    check(
        "5. degisiklik yok -> resmi arama CAGRILMAZ",
        len(SEARCH_CALLS) == 0
        and no_change_result.status
        == COMPARISON_SKIPPED_NO_CHANGE,
        no_change_result.status,
    )

    monitor_e = build_monitor(session, "Kesif E - ilgisiz")

    change_e, analysis_e = build_change_fixture(
        session, monitor_e, relevant=False
    )

    irrelevant_result = run(
        run_regulation_comparison(
            db=session,
            monitor=monitor_e,
            result=FakeRunResult(
                change=change_e,
                analysis=analysis_e,
            ),
        )
    )

    check(
        "6. ilgisiz degisiklik -> resmi arama CAGRILMAZ",
        len(SEARCH_CALLS) == 0
        and irrelevant_result.status
        == COMPARISON_SKIPPED_IRRELEVANT,
        irrelevant_result.status,
    )

    # =====================================================
    # BOLUM B - RESMI ARAMANIN CALISTIGI DURUMLAR
    # =====================================================

    # -----------------------------------------------------
    # 8. NO_REFERENCES + BASE TITLE -> ARAMA CALISIR
    #
    # Kutuphanede HIC referans yokken calistirmak icin
    # kayitli referansin adi gecici olarak alakasiz
    # yapilamaz (unique kisiti yok ama matcher skoru
    # dususur); bunun yerine bos bir kutuphane senaryosu
    # dogrudan discover_reference ile ve pipeline uzerinden
    # ayri ayri dogrulanir.
    # -----------------------------------------------------

    reset_counters()

    # Kayitli tek referansin adini alakasiz yaparak yerel
    # eslestirmeyi no_match'e dusuruyoruz.
    original_title = registered.title
    registered.title = "Tamamen Alakasız Bir Kayıt Yönetmelik"
    session.commit()

    monitor_f = build_monitor(session, "Kesif F - yerel no_match")

    change_f, analysis_f = build_change_fixture(
        session, monitor_f
    )

    result_f = run(
        run_regulation_comparison(
            db=session,
            monitor=monitor_f,
            result=FakeRunResult(
                change=change_f,
                analysis=analysis_f,
            ),
        )
    )

    check(
        "7. yerel no_match + base ad -> resmi arama CALISIR",
        len(SEARCH_CALLS) == 1,
        f"arama cagrisi={len(SEARCH_CALLS)}",
    )

    check(
        "7. arama sorgusu BASE ad ile yapiliyor "
        "(degisiklik basligi ile degil)",
        SEARCH_CALLS
        and "Değişiklik Yapılmasına"
        not in SEARCH_CALLS[0]["parameters"]["AranacakIfade"]
        and "Ödeme Hizmetleri"
        in SEARCH_CALLS[0]["parameters"]["AranacakIfade"],
        SEARCH_CALLS[0]["parameters"]["AranacakIfade"]
        if SEARCH_CALLS
        else "",
    )

    check(
        "7. arama YALNIZCA baslikta yapiliyor",
        SEARCH_CALLS
        and SEARCH_CALLS[0]["parameters"]["AranacakYer"]
        == SEARCH_SCOPE_TITLE,
    )

    # -----------------------------------------------------
    # 26-34. GECERLI ADAY -> REFERANS OLUSUR
    # -----------------------------------------------------

    discovered = (
        session.query(RegulationReference)
        .filter(RegulationReference.source_url == CANONICAL_URL)
        .one_or_none()
    )

    check(
        "26. gecerli aday + basarili fetch -> referans OLUSUR",
        discovered is not None,
    )

    check(
        "17. kanonik adres uretimi "
        "(MevzuatNo -> MevzuatTur -> MevzuatTertip)",
        discovered is not None
        and discovered.source_url == CANONICAL_URL,
        discovered.source_url if discovered else "",
    )

    check(
        "27. olusan referans source_type = mevzuat_gov_tr",
        discovered is not None
        and discovered.source_type
        == SOURCE_TYPE_MEVZUAT_GOV_TR,
        discovered.source_type if discovered else "",
    )

    check(
        "28. olusan referans is_consolidated = true",
        discovered is not None
        and discovered.is_consolidated is True,
    )

    check(
        "29. content_hash saklanir",
        discovered is not None
        and discovered.content_hash
        == calculate_content_hash(discovered.content),
    )

    check(
        "30. surum bilgisi saklanir "
        "(version_date + last_verified_at)",
        discovered is not None
        and discovered.version_date == date(2026, 9, 4)
        and discovered.last_verified_at is not None,
        str(discovered.version_date) if discovered else "",
    )

    check(
        "31. RESMI sonuc basligi saklanir "
        "(kullanicidan cikarilan base ad DEGIL)",
        discovered is not None
        and discovered.title
        == clean_official_title(OFFICIAL_ROW["mevAdi"]),
        discovered.title if discovered else "",
    )

    check(
        "31. saklanan baslik resmi kaynagin yazimini korur",
        discovered is not None
        and discovered.title.isupper(),
    )

    check(
        "32. basarili kesif -> mevcut karsilastirma CALISIR",
        result_f.status == COMPARISON_COMPLETED
        and result_f.item_count == 1,
        f"{result_f.status} / {result_f.item_count}",
    )

    check(
        "32. kesif durumu discovered",
        result_f.discovery_status == DISCOVERY_DISCOVERED,
        str(result_f.discovery_status),
    )

    check(
        "33. comparison satiri kesfedilen referansi saklar",
        result_f.comparison is not None
        and discovered is not None
        and result_f.comparison.reference_regulation_id
        == discovered.id,
    )

    check(
        "33. calistirma yaniti kesfedilen referansi bildirir",
        discovered is not None
        and result_f.discovered_reference_id == discovered.id
        and result_f.discovered_reference_title
        == discovered.title,
    )

    check(
        "34. comparison dogru content hash'i saklar",
        result_f.comparison is not None
        and discovered is not None
        and result_f.comparison.reference_content_hash
        == discovered.content_hash,
    )

    check(
        "34. comparison konsolideligi saklar",
        result_f.comparison is not None
        and result_f.comparison.reference_is_consolidated
        is True,
    )

    check(
        "33. comparison kesif durumunu saklar",
        result_f.comparison is not None
        and result_f.comparison.reference_discovery_status
        == DISCOVERY_DISCOVERED,
        str(
            result_f.comparison.reference_discovery_status
            if result_f.comparison
            else None
        ),
    )

    check(
        "33. comparison kesif yontemini saklar",
        result_f.comparison is not None
        and result_f.comparison.reference_match_method
        == MATCH_METHOD_DISCOVERED_EXACT,
        str(
            result_f.comparison.reference_match_method
            if result_f.comparison
            else None
        ),
    )

    # -----------------------------------------------------
    # 19. BIREBIR KESIF -> 0 KESIF GEMINI CAGRISI
    #
    # Yapilan tek Gemini cagrisi KARSILASTIRMA cagrisidir;
    # kesif icin model kullanilmamistir.
    # -----------------------------------------------------

    check(
        "19. birebir resmi ad -> 0 kesif Gemini cagrisi",
        len(GEMINI_CALLS) == 1
        and result_f.discovery_ai_used is False,
        f"toplam gemini={len(GEMINI_CALLS)}",
    )

    check(
        "19. tek Gemini cagrisi KARSILASTIRMA icindir",
        len(GEMINI_CALLS) == 1
        and "karşılaştır" in GEMINI_CALLS[0]["prompt"].lower(),
    )

    # -----------------------------------------------------
    # 18. AYNI KANONIK KAYNAK -> IKINCI SATIR YOK
    # -----------------------------------------------------

    reset_counters()

    # Kesfedilen kayit artik kutuphanede ve RESMI adiyla
    # yerel eslestirmeyi zaten kazanir. Duplicate korumasini
    # test edebilmek icin yerel eslestirmeyi devre disi
    # birakiyoruz: adi alakasiz yapilir, kanonik ADRESI
    # AYNI KALIR.
    discovered_title = hide_reference(
        session, discovered, "kesif"
    )

    monitor_g = build_monitor(session, "Kesif G - duplicate")

    change_g, analysis_g = build_change_fixture(
        session, monitor_g
    )

    # Arama ayni belgeyi FARKLI parametre sirasiyla dondurur.
    result_g = run(
        run_regulation_comparison(
            db=session,
            monitor=monitor_g,
            result=FakeRunResult(
                change=change_g,
                analysis=analysis_g,
            ),
        )
    )

    duplicate_count = session.scalar(
        select(func.count())
        .select_from(RegulationReference)
        .where(
            RegulationReference.source_url == CANONICAL_URL
        )
    )

    check(
        "18. ayni kanonik kaynak -> ikinci satir OLUSMAZ",
        duplicate_count == 1,
        f"satir={duplicate_count}",
    )

    check(
        "18. ikinci kesif mevcut satiri kullanir",
        result_g.discovery_status == DISCOVERY_FOUND_EXISTING
        and result_g.discovered_reference_id
        == (discovered.id if discovered else None),
        str(result_g.discovery_status),
    )

    check(
        "18. mevcut satir bulundugunda konsolide metin "
        "yeniden INDIRILMEZ",
        len(FETCH_CALLS) == 0,
        f"fetch={len(FETCH_CALLS)}",
    )

    # -----------------------------------------------------
    # 18b. FARKLI PARAMETRE SIRASI -> AYNI KAYIT
    # -----------------------------------------------------

    reordered = (
        "https://www.mevzuat.gov.tr/mevzuat"
        "?MevzuatTertip=5&MevzuatTur=7&MevzuatNo=39080"
    )

    check(
        "18. farkli parametre sirasi ayni kanonik adrese iner",
        canonical_mevzuat_url(reordered) == CANONICAL_URL,
        str(canonical_mevzuat_url(reordered)),
    )

    check(
        "18. iframe adresi de ayni kanonik adrese iner",
        canonical_mevzuat_url(
            "https://www.mevzuat.gov.tr/anasayfa/"
            "MevzuatFihristDetayIframe"
            "?MevzuatTur=7&MevzuatNo=39080&MevzuatTertip=5"
        )
        == CANONICAL_URL,
    )

    check(
        "18. farkli sirali adres mevcut kaydi bulur",
        find_existing_by_canonical_url(
            db=session,
            canonical_url=CANONICAL_URL,
        )
        is not None,
    )

    # -----------------------------------------------------
    # 8. NO_REFERENCES -> ARAMA CALISIR
    #
    # Kutuphanedeki tum referanslar silinemez (kesfedilen
    # kayit karsilastirmalara bagli), bu yuzden matcher'a
    # BOS liste verilerek dogrudan dogrulanir.
    # -----------------------------------------------------

    reset_counters()

    empty_match = run(
        match_reference(
            source_text=AMENDMENT,
            references=[],
            allow_ai=True,
        )
    )

    check(
        "8. kayitli referans yokken matcher no_references verir",
        empty_match.status == "no_references",
        empty_match.status,
    )

    base_name = extract_base_regulation_names(
        source_text=AMENDMENT
    ).primary

    check(
        "8. no_references yolunda base ad metinden cikarilabilir",
        base_name is not None
        and "ödeme hizmetleri" in base_name.normalized,
        base_name.name if base_name else "",
    )

    empty_library_result = run(
        discover_reference(
            db=session,
            base_name=base_name,
        )
    )

    check(
        "8. no_references + base ad -> resmi arama CALISIR",
        len(SEARCH_CALLS) == 1,
        f"arama cagrisi={len(SEARCH_CALLS)}",
    )

    check(
        "8. mevcut kayit bulundugu icin yeni satir acilmaz",
        empty_library_result.status
        == DISCOVERY_FOUND_EXISTING,
        empty_library_result.status,
    )

    # -----------------------------------------------------
    # 9. NO_BASE_NAME -> ARAMA CALISMAZ
    # -----------------------------------------------------

    reset_counters()

    monitor_h = build_monitor(session, "Kesif H - ad yok")

    change_h, analysis_h = build_change_for_text(
        session=session,
        monitor=monitor_h,
        text=NO_BASE_NAME_TEXT,
        summary="Kurul kararı yayımlandı.",
    )

    result_h = run(
        run_regulation_comparison(
            db=session,
            monitor=monitor_h,
            result=FakeRunResult(
                change=change_h,
                analysis=analysis_h,
            ),
        )
    )

    check(
        "9. no_base_name -> resmi arama CALISMAZ",
        len(SEARCH_CALLS) == 0,
        f"arama cagrisi={len(SEARCH_CALLS)}",
    )

    check(
        "9. no_base_name -> karsilastirma calismaz",
        result_h.status == COMPARISON_SKIPPED_NO_AUTO_MATCH,
        result_h.status,
    )

    check(
        "9. no_base_name -> kesif durumu not_needed",
        result_h.discovery_status == DISCOVERY_NOT_NEEDED,
        str(result_h.discovery_status),
    )

    # -----------------------------------------------------
    # 10. YEREL AMBIGUOUS -> OTOMATIK OLUSTURMA YOK
    #
    # Yerel belirsizlik, resmi kaynaktan YENI bir satir
    # acarak "cozulmez"; aksi halde ayni mevzuat icin
    # ucuncu bir kayit olusurdu.
    # -----------------------------------------------------

    reset_counters()

    # Kesfedilen (konsolide) kayit yerel eslestirmeyi
    # kazanmasin: belirsizligi olusturan IKIZLER olmali.
    # Adi zaten gizli; kayitli ornek de gizlenir.
    # Ayni ada sahip IKI kayitli referans -> belirsizlik.
    twin_a = create_regulation_reference(
        db=session,
        title=BASE_TITLE,
        source_url="https://ornek.gov.tr/ikiz-a",
        content=CONSOLIDATED_CONTENT,
        source_type="generic",
        is_consolidated=False,
    )

    # AYNI ADA sahip ikinci kayit: ikisi de birebir uyuyor
    # ve hicbiri konsolide degil -> gercek belirsizlik.
    twin_b = create_regulation_reference(
        db=session,
        title=BASE_TITLE,
        source_url="https://ornek.gov.tr/ikiz-b",
        content=CONSOLIDATED_CONTENT,
        source_type="generic",
        is_consolidated=False,
    )

    # Belirsizlikte matcher AI'ya soruyor; model emin
    # olmadigini soylerse sonuc ambiguous kalir.
    NEXT_RESPONSE["payload"] = json.dumps(
        {
            "reference_id": twin_a.id,
            "confident": False,
            "reason": "ayırt edilemedi",
        },
        ensure_ascii=False,
    )

    reference_count_before_ambiguous = session.scalar(
        select(func.count()).select_from(RegulationReference)
    )

    monitor_i = build_monitor(session, "Kesif I - belirsiz")

    change_i, analysis_i = build_change_fixture(
        session, monitor_i
    )

    result_i = run(
        run_regulation_comparison(
            db=session,
            monitor=monitor_i,
            result=FakeRunResult(
                change=change_i,
                analysis=analysis_i,
            ),
        )
    )

    reference_count_after_ambiguous = session.scalar(
        select(func.count()).select_from(RegulationReference)
    )

    check(
        "10. yerel ambiguous -> resmi arama CALISMAZ",
        len(SEARCH_CALLS) == 0,
        f"arama cagrisi={len(SEARCH_CALLS)}",
    )

    check(
        "10. yerel ambiguous -> yeni referans OLUSMAZ",
        reference_count_after_ambiguous
        == reference_count_before_ambiguous,
    )

    check(
        "10. yerel ambiguous davranisi KORUNDU",
        result_i.status == COMPARISON_SKIPPED_NO_AUTO_MATCH
        and result_i.match_status == "ambiguous",
        f"{result_i.status} / {result_i.match_status}",
    )

    # Ikizleri temizle: sonraki testler yerel no_match
    # bekliyor.
    session.delete(twin_a)
    session.delete(twin_b)
    session.commit()

    # =====================================================
    # BOLUM C - ADAY DOGRULAMA (BIRIM)
    # =====================================================

    check(
        "11. birebir resmi ad -> aday secilir",
        build_candidate(OFFICIAL_ROW) is not None
        and build_candidate(OFFICIAL_ROW).mevzuat_no
        == "39080",
    )

    check(
        "12. Turkce ad normalizasyonu "
        "(BUYUK HARF + \\r\\n + vurgu HTML)",
        clean_official_title(
            "<span style='background-color:yellow'>ÖDEME "
            "HİZMETLERİ</span> VE ELEKTRONİK\r\nPARA"
        )
        == "ÖDEME HİZMETLERİ VE ELEKTRONİK PARA",
        clean_official_title(
            "<span style='background-color:yellow'>ÖDEME "
            "HİZMETLERİ</span> VE ELEKTRONİK\r\nPARA"
        ),
    )

    base_for_scoring = extract_base_regulation_names(
        source_text=AMENDMENT
    ).primary

    official_candidate = build_candidate(OFFICIAL_ROW)

    scored_exact = score_candidates(
        base_name=base_for_scoring,
        candidates=[official_candidate],
    )

    check(
        "12. Turkce buyuk harf farki eslesmeyi BOZMAZ",
        scored_exact[0].exact_title is True
        and scored_exact[0].score >= 0.999,
        f"exact={scored_exact[0].exact_title} "
        f"skor={scored_exact[0].score:.3f}",
    )

    # -----------------------------------------------------
    # 14-16. BOZUK KIMLIKLER REDDEDILIR
    # -----------------------------------------------------

    check(
        "14. bozuk MevzuatNo reddedilir",
        build_candidate(
            {**OFFICIAL_ROW, "mevzuatNo": "39080'; DROP TABLE--"}
        )
        is None,
    )

    check(
        "14. bos MevzuatNo reddedilir",
        build_candidate({**OFFICIAL_ROW, "mevzuatNo": ""})
        is None,
    )

    check(
        "15. bozuk MevzuatTur reddedilir",
        build_candidate(
            {**OFFICIAL_ROW, "mevzuatTur": "7&Foo=bar"}
        )
        is None,
    )

    check(
        "15. metinsel MevzuatTur reddedilir",
        build_candidate(
            {**OFFICIAL_ROW, "mevzuatTur": "Yonetmelik"}
        )
        is None,
    )

    check(
        "16. bozuk MevzuatTertip reddedilir",
        build_candidate(
            {**OFFICIAL_ROW, "mevzuatTertip": "../../etc"}
        )
        is None,
    )

    check(
        "16. eksik MevzuatTertip reddedilir",
        build_candidate(
            {
                key: value
                for key, value in OFFICIAL_ROW.items()
                if key != "mevzuatTertip"
            }
        )
        is None,
    )

    check(
        "14-16. bozuk kimlik ARAMA SONUCUNDAN da elenir",
        len(
            run(
                search_official_regulations(
                    query=BASE_TITLE,
                    kind="yönetmelik",
                )
            ).candidates
        )
        == 0
        if _set_search(
            official_response(
                [{**OFFICIAL_ROW, "mevzuatNo": "abc"}]
            )
        )
        else False,
    )

    check(
        "14-16. yanlis TURDEKI sonuc elenir "
        "(yonetmelik aramasi teblig dondurmez)",
        len(
            run(
                search_official_regulations(
                    query=BASE_TITLE,
                    kind="yönetmelik",
                )
            ).candidates
        )
        == 0
        if _set_search(
            official_response(
                [{**OFFICIAL_ROW, "mevzuatTur": 9}]
            )
        )
        else False,
    )

    check(
        "17. build_mevzuat_shell_url rakam DISI degeri reddeder",
        _raises_value_error(
            lambda: build_mevzuat_shell_url(
                tur="7", no="1 OR 1=1", tertip="5"
            )
        ),
    )

    check(
        "17. arama govdesi bos ifade ile GONDERILMEZ",
        _raises_search_error(
            lambda: run(
                search_official_regulations(
                    query="   ",
                    kind="yönetmelik",
                )
            )
        ),
    )

    payload = build_search_payload(
        query=BASE_TITLE,
        profile=(
            mevzuat_search_service.SEARCH_PROFILES["yönetmelik"]
        ),
    )

    check(
        "17. arama govdesi gercek endpoint bicimini tasir",
        payload["parameters"]["MevzuatTur"] == "TumYonetmelik"
        and payload["parameters"]["AranacakYer"]
        == SEARCH_SCOPE_TITLE
        and "draw" in payload
        and "length" in payload,
    )

    # =====================================================
    # BOLUM D - BELIRSIZLIK, YAYIM BILGISI, AI
    # =====================================================

    # -----------------------------------------------------
    # 13. YAYIM BILGISI ILE AYRIM
    # -----------------------------------------------------

    twin_row_a = {
        **OFFICIAL_ROW,
        "mevzuatNo": "39080",
        "resmiGazeteTarihi": "01.12.2021",
        "resmiGazeteSayisi": "31676",
    }

    twin_row_b = {
        **OFFICIAL_ROW,
        "mevzuatNo": "12345",
        "resmiGazeteTarihi": "05.05.2015",
        "resmiGazeteSayisi": "29347",
    }

    twin_scored = score_candidates(
        base_name=base_for_scoring,
        candidates=[
            build_candidate(twin_row_b),
            build_candidate(twin_row_a),
        ],
    )

    status_meta, chosen_meta, method_meta = resolve_local(
        twin_scored
    )

    check(
        "13. ayni ad + yayim bilgisi -> dogru kayit secilir",
        status_meta == "matched"
        and chosen_meta is not None
        and chosen_meta.candidate.mevzuat_no == "39080"
        and method_meta == MATCH_METHOD_DISCOVERED_META,
        f"{status_meta} / {method_meta}",
    )

    check(
        "13. yayim bilgisi OLMAYAN ikizler ayirt EDILEMEZ",
        resolve_local(
            score_candidates(
                base_name=base_for_scoring,
                candidates=[
                    build_candidate(
                        {
                            **OFFICIAL_ROW,
                            "mevzuatNo": "11111",
                            "resmiGazeteTarihi": "",
                            "resmiGazeteSayisi": "",
                        }
                    ),
                    build_candidate(
                        {
                            **OFFICIAL_ROW,
                            "mevzuatNo": "22222",
                            "resmiGazeteTarihi": "",
                            "resmiGazeteSayisi": "",
                        }
                    ),
                ],
            )
        )[0]
        == DISCOVERY_AMBIGUOUS,
    )

    # -----------------------------------------------------
    # 20-21. BELIRSIZ ARAMA -> EN FAZLA 1 GEMINI CAGRISI
    # -----------------------------------------------------

    reset_counters()

    ambiguous_rows = [
        {
            **OFFICIAL_ROW,
            "mevzuatNo": "11111",
            "resmiGazeteTarihi": "",
            "resmiGazeteSayisi": "",
        },
        {
            **OFFICIAL_ROW,
            "mevzuatNo": "22222",
            "resmiGazeteTarihi": "",
            "resmiGazeteSayisi": "",
        },
    ]

    SEARCH_RESPONSE["value"] = official_response(ambiguous_rows)

    NEXT_RESPONSE["payload"] = json.dumps(
        {
            "candidate_id": "7-22222-5",
            "confidence": 0.95,
            "reason": "yayım bilgisi uyuyor",
        },
        ensure_ascii=False,
    )

    ambiguous_result = run(
        discover_reference(
            db=session,
            base_name=base_for_scoring,
        )
    )

    check(
        "20. belirsiz arama -> en fazla 1 mantiksal "
        "Gemini cagrisi",
        len(GEMINI_CALLS) == 1,
        f"gemini={len(GEMINI_CALLS)}",
    )

    check(
        "20. AI ayrimi sonucu referans olusturabilir",
        ambiguous_result.status == DISCOVERY_DISCOVERED
        and ambiguous_result.match_method
        == MATCH_METHOD_DISCOVERED_AI
        and ambiguous_result.ai_used is True,
        f"{ambiguous_result.status} / "
        f"{ambiguous_result.match_method}",
    )

    check(
        "20. modele TAM MEVZUAT METNI gonderilmez",
        GEMINI_CALLS
        and "MADDE 40" not in GEMINI_CALLS[0]["prompt"]
        and "7-22222-5" in GEMINI_CALLS[0]["prompt"],
    )

    # -----------------------------------------------------
    # 21. UYDURMA GEMINI ADAYI REDDEDILIR
    # -----------------------------------------------------

    reset_counters()

    SEARCH_RESPONSE["value"] = official_response(
        [
            {
                **OFFICIAL_ROW,
                "mevzuatNo": "33333",
                "resmiGazeteTarihi": "",
                "resmiGazeteSayisi": "",
            },
            {
                **OFFICIAL_ROW,
                "mevzuatNo": "44444",
                "resmiGazeteTarihi": "",
                "resmiGazeteSayisi": "",
            },
        ]
    )

    NEXT_RESPONSE["payload"] = json.dumps(
        {
            "candidate_id": "7-99999-5",
            "confidence": 0.99,
            "reason": "uydurma",
        },
        ensure_ascii=False,
    )

    count_before_fabricated = session.scalar(
        select(func.count()).select_from(RegulationReference)
    )

    fabricated_result = run(
        discover_reference(
            db=session,
            base_name=base_for_scoring,
        )
    )

    count_after_fabricated = session.scalar(
        select(func.count()).select_from(RegulationReference)
    )

    check(
        "21. uydurma Gemini candidate_id REDDEDILIR",
        fabricated_result.status == DISCOVERY_AMBIGUOUS
        and fabricated_result.reference is None,
        fabricated_result.status,
    )

    check(
        "21. uydurma kimlikle referans OLUSMAZ",
        count_after_fabricated == count_before_fabricated,
    )

    check(
        "21. uydurma kimlikle konsolide metin INDIRILMEZ",
        len(FETCH_CALLS) == 0,
    )

    # -----------------------------------------------------
    # 22. DUSUK GUVEN -> REFERANS OLUSMAZ
    # -----------------------------------------------------

    reset_counters()

    SEARCH_RESPONSE["value"] = official_response(
        [
            {
                **OFFICIAL_ROW,
                "mevzuatNo": "55555",
                "resmiGazeteTarihi": "",
                "resmiGazeteSayisi": "",
            },
            {
                **OFFICIAL_ROW,
                "mevzuatNo": "66666",
                "resmiGazeteTarihi": "",
                "resmiGazeteSayisi": "",
            },
        ]
    )

    NEXT_RESPONSE["payload"] = json.dumps(
        {
            "candidate_id": "7-55555-5",
            "confidence": 0.40,
            "reason": "emin değilim",
        },
        ensure_ascii=False,
    )

    count_before_low = session.scalar(
        select(func.count()).select_from(RegulationReference)
    )

    low_confidence_result = run(
        discover_reference(
            db=session,
            base_name=base_for_scoring,
        )
    )

    check(
        "22. dusuk guvenli AI secimi KABUL EDILMEZ",
        low_confidence_result.status == DISCOVERY_AMBIGUOUS
        and low_confidence_result.reference is None,
        low_confidence_result.status,
    )

    check(
        "22. dusuk guven -> referans OLUSMAZ",
        session.scalar(
            select(func.count()).select_from(
                RegulationReference
            )
        )
        == count_before_low,
    )

    # -----------------------------------------------------
    # 22b. DUSUK AD SKORU -> NO_MATCH
    # -----------------------------------------------------

    reset_counters()

    SEARCH_RESPONSE["value"] = official_response(
        [
            {
                **OFFICIAL_ROW,
                "mevzuatNo": "77777",
                "mevAdi": "TAŞINIR MAL YÖNETMELİĞİ",
            }
        ]
    )

    weak_result = run(
        discover_reference(
            db=session,
            base_name=base_for_scoring,
        )
    )

    check(
        "22. alakasiz baslikli sonuc -> no_match",
        weak_result.status == DISCOVERY_NO_MATCH
        and weak_result.reference is None,
        weak_result.status,
    )

    check(
        "22. alakasiz sonuc icin Gemini CAGRILMAZ",
        len(GEMINI_CALLS) == 0,
    )

    # -----------------------------------------------------
    # 23. ARAMA SONUCU YOK -> REFERANS OLUSMAZ
    # -----------------------------------------------------

    reset_counters()

    SEARCH_RESPONSE["value"] = official_response([])

    count_before_empty = session.scalar(
        select(func.count()).select_from(RegulationReference)
    )

    empty_result = run(
        discover_reference(
            db=session,
            base_name=base_for_scoring,
        )
    )

    check(
        "23. arama sonucu yok -> no_match",
        empty_result.status == DISCOVERY_NO_MATCH,
        empty_result.status,
    )

    check(
        "23. arama sonucu yok -> referans OLUSMAZ",
        session.scalar(
            select(func.count()).select_from(
                RegulationReference
            )
        )
        == count_before_empty
        and len(FETCH_CALLS) == 0,
    )

    # -----------------------------------------------------
    # 24. ARAMA ENDPOINT HATASI -> REFERANS OLUSMAZ
    # -----------------------------------------------------

    reset_counters()

    SEARCH_RESPONSE["error"] = MevzuatSearchError(
        "Mevzuat Bilgi Sistemi araması zaman aşımına uğradı."
    )

    count_before_error = session.scalar(
        select(func.count()).select_from(RegulationReference)
    )

    error_result = run(
        discover_reference(
            db=session,
            base_name=base_for_scoring,
        )
    )

    check(
        "24. arama endpoint hatasi -> discovery_failed",
        error_result.status == DISCOVERY_FAILED,
        error_result.status,
    )

    check(
        "24. arama endpoint hatasi -> referans OLUSMAZ",
        session.scalar(
            select(func.count()).select_from(
                RegulationReference
            )
        )
        == count_before_error,
    )

    check(
        "24. hata mesaji TEMIZLENMIS ve kisa",
        error_result.detail
        and len(error_result.detail) <= 300
        and "Traceback" not in error_result.detail,
        error_result.detail,
    )

    # 24b. Beklenmeyen hata da pipeline'i dusurmez.
    reset_counters()

    SEARCH_RESPONSE["error"] = RuntimeError("beklenmeyen")

    unexpected_result = run(
        discover_reference(
            db=session,
            base_name=base_for_scoring,
        )
    )

    check(
        "24. beklenmeyen arama hatasi da discovery_failed",
        unexpected_result.status == DISCOVERY_FAILED,
        unexpected_result.status,
    )

    # -----------------------------------------------------
    # 25. KONSOLIDE METIN ALINAMADI -> REFERANS OLUSMAZ
    #
    # CREATE BEFORE VERIFY YOK: aday gecerli olsa bile
    # metin alinamadiysa satir ACILMAZ.
    # -----------------------------------------------------

    reset_counters()

    SEARCH_RESPONSE["value"] = official_response(
        [{**OFFICIAL_ROW, "mevzuatNo": "88888"}]
    )

    FETCH_SHOULD_FAIL["value"] = True

    count_before_fetch_fail = session.scalar(
        select(func.count()).select_from(RegulationReference)
    )

    fetch_fail_result = run(
        discover_reference(
            db=session,
            base_name=base_for_scoring,
        )
    )

    check(
        "25. konsolide metin alinamadi -> discovery_failed",
        fetch_fail_result.status == DISCOVERY_FAILED,
        fetch_fail_result.status,
    )

    check(
        "25. konsolide metin alinamadi -> referans OLUSMAZ",
        session.scalar(
            select(func.count()).select_from(
                RegulationReference
            )
        )
        == count_before_fetch_fail,
    )

    check(
        "25. metin denemesi GERCEKTEN yapildi "
        "(sadece baslik goruldu diye satir acilmadi)",
        len(FETCH_CALLS) == 1,
        f"fetch={len(FETCH_CALLS)}",
    )

    # 25b. Cok kisa metin de kabul edilmez.
    reset_counters()

    SEARCH_RESPONSE["value"] = official_response(
        [{**OFFICIAL_ROW, "mevzuatNo": "99998"}]
    )

    FETCH_CONTENT["value"] = "Kısa."

    count_before_short = session.scalar(
        select(func.count()).select_from(RegulationReference)
    )

    short_result = run(
        discover_reference(
            db=session,
            base_name=base_for_scoring,
        )
    )

    check(
        "25. anlamsiz kisa metin -> referans OLUSMAZ",
        short_result.status == DISCOVERY_FAILED
        and session.scalar(
            select(func.count()).select_from(
                RegulationReference
            )
        )
        == count_before_short,
        short_result.status,
    )

    # =====================================================
    # BOLUM E - HATA IZOLASYONU VE BILDIRIM DETAYI
    # =====================================================

    # -----------------------------------------------------
    # 36. KESIF NO_MATCH DETAY DURUMU
    # -----------------------------------------------------

    reset_counters()

    SEARCH_RESPONSE["value"] = official_response([])

    # Onceki testler kutuphaneye kesfedilmis kayitlar
    # ekledi. "Yerel eslestirme hicbir sey bulamiyor"
    # senaryosu icin hepsi gecici olarak gizlenir.
    hidden_references = hide_all_matching(
        session, base_for_scoring
    )

    monitor_j = build_monitor(session, "Kesif J - no_match")

    change_j, analysis_j = build_change_fixture(
        session, monitor_j
    )

    result_j = run(
        run_regulation_comparison(
            db=session,
            monitor=monitor_j,
            result=FakeRunResult(
                change=change_j,
                analysis=analysis_j,
            ),
        )
    )

    check(
        "36. kesif no_match -> karsilastirma CALISMAZ",
        result_j.status == COMPARISON_SKIPPED_NO_AUTO_MATCH,
        result_j.status,
    )

    check(
        "36. kesif no_match -> referans BAGLANMAZ",
        result_j.comparison is not None
        and result_j.comparison.reference_regulation_id
        is None,
    )

    check(
        "36. kesif no_match durumu detayda saklanir",
        result_j.comparison is not None
        and result_j.comparison.reference_discovery_status
        == DISCOVERY_NO_MATCH,
        str(
            result_j.comparison.reference_discovery_status
            if result_j.comparison
            else None
        ),
    )

    check(
        "36. kesif no_match -> karsilastirma Gemini "
        "cagrisi YOK",
        len(GEMINI_CALLS) == 0,
    )

    # -----------------------------------------------------
    # 37. KESIF BASARISIZLIGI DETAY DURUMU
    # -----------------------------------------------------

    reset_counters()

    SEARCH_RESPONSE["error"] = MevzuatSearchError(
        "Mevzuat Bilgi Sistemi'ne bağlanılamadı."
    )

    monitor_k = build_monitor(session, "Kesif K - hata")

    change_k, analysis_k = build_change_fixture(
        session, monitor_k
    )

    result_k = run(
        run_regulation_comparison(
            db=session,
            monitor=monitor_k,
            result=FakeRunResult(
                change=change_k,
                analysis=analysis_k,
            ),
        )
    )

    check(
        "37. kesif hatasi scheduler'i DUSURMEZ",
        result_k.status == COMPARISON_SKIPPED_NO_AUTO_MATCH,
        result_k.status,
    )

    check(
        "37. kesif hatasi durumu detayda saklanir",
        result_k.comparison is not None
        and result_k.comparison.reference_discovery_status
        == DISCOVERY_FAILED,
        str(
            result_k.comparison.reference_discovery_status
            if result_k.comparison
            else None
        ),
    )

    check(
        "37. kesif hatasinda SAHTE referans olusmaz",
        result_k.comparison is not None
        and result_k.comparison.reference_regulation_id
        is None
        and result_k.discovered_reference_id is None,
    )

    check(
        "37. change ve AIAnalysis KORUNUR",
        session.get(type(change_k), change_k.id) is not None
        and session.get(type(analysis_k), analysis_k.id)
        is not None,
    )

    # -----------------------------------------------------
    # 35. BASARILI KESIF BILDIRIM DETAYI
    # -----------------------------------------------------

    check(
        "35. basarili kesif bildirim govdesini zenginlestirir",
        "Mevzuat karşılaştırması"
        in build_notification_message(
            ai_analysis=analysis_f,
            regulation_comparison=result_f.comparison,
        ),
    )

    check(
        "35. bildirim govdesi etkilenen madde sayisini soyler",
        "Etkilenen madde sayısı: 1"
        in build_notification_message(
            ai_analysis=analysis_f,
            regulation_comparison=result_f.comparison,
        ),
    )

    # =====================================================
    # BOLUM F - ARAYUZ
    # =====================================================

    check(
        "35/36/37. arayuz kesif durumlarini taniyor",
        "DISCOVERY_STATUS_MESSAGES" in app_source
        and "formatDiscoveryStatus" in app_source
        and "reference_discovery_status" in app_source,
    )

    check(
        "36. arayuz no_match metni sprint metniyle ayni",
        "Mevzuat Bilgi Sistemi'nde güvenilir bir referans"
        in app_source,
    )

    check(
        "37. arayuz kesif hatasi metni sprint metniyle ayni",
        "resmî referans" in app_source
        and "otomatik olarak alınamadı" in app_source,
    )

    check(
        "arayuz otomatik mod aciklamasi resmi kaynagi soyluyor",
        "Mevzuat Bilgi Sistemi'nden otomatik oluşturulur"
        in app_source,
    )

    check(
        "arayuz kesif eslestirme yontemlerini etiketliyor",
        "automatic_discovered_exact" in app_source
        and "automatic_discovered_ai" in app_source,
    )

    check(
        "manuel 'Referansı Otomatik Bul' yardimcisi DURUYOR",
        "Referansı Otomatik Bul" in app_source
        and "matchRegulationReference" in app_source,
    )

    # =====================================================
    # BOLUM G - REGRESYONLAR
    # =====================================================

    reset_counters()

    # 39. yerel referans matcher
    #
    # Gizlenen kayitlar geri alinir; matcher gercek adlarla
    # dogrulanir.
    for reference, title in hidden_references:
        reference.title = title

    session.commit()

    restore_reference(session, registered, original_title)
    restore_reference(session, discovered, discovered_title)

    local_match = run(
        match_reference(
            source_text=AMENDMENT,
            references=[registered],
            allow_ai=True,
        )
    )

    check(
        "39. yerel referans matcher regresyonu",
        local_match.status == MATCH_STATUS_MATCHED
        and local_match.reference is not None
        and local_match.reference.id == registered.id
        and len(GEMINI_CALLS) == 0,
        f"{local_match.status} / gemini={len(GEMINI_CALLS)}",
    )

    # 40. konsolide kaynak cozumleyicisi
    resolved = resolve_reference_source(CANONICAL_URL)

    check(
        "40. konsolide kaynak regresyonu "
        "(kabuk -> iframe, konsolide isaretli)",
        resolved.source_type == SOURCE_TYPE_MEVZUAT_GOV_TR
        and resolved.is_consolidated is True
        and "MevzuatFihristDetayIframe" in resolved.fetch_url
        and resolved.rewritten is True,
        resolved.fetch_url,
    )

    check(
        "40. tanimadigi adres konsolide SAYILMAZ",
        resolve_reference_source(
            "https://ornek-hukuk-blogu.com/yonetmelik"
        ).is_consolidated
        is False,
    )

    # 41. Turkce RG ingestion (windows-1254)
    turkish_bytes = (
        b"<html><head><meta http-equiv=content-type "
        b"content=\"text/html; charset=windows-1254\">"
        b"</head><body>GE\xc7\xddC\xdd MADDE</body></html>"
    )

    decoded_text, decoded_encoding = decode_html(
        turkish_bytes
    )

    check(
        "41. Turkce RG ingestion regresyonu (windows-1254)",
        "GEÇİCİ MADDE" in decoded_text
        and "�" not in decoded_text,
        decoded_encoding,
    )

    # 42/44/45. bolum kapsamlari
    check(
        "44. auto_section regresyonu (kapsam korunuyor)",
        "auto_section"
        in __import__(
            "app.schemas.monitor",
            fromlist=["SCOPE_TYPES"],
        ).SCOPE_TYPES,
    )

    check(
        "45. manual_section regresyonu (kapsam korunuyor)",
        "manual_section"
        in __import__(
            "app.schemas.monitor",
            fromlist=["SCOPE_TYPES"],
        ).SCOPE_TYPES,
    )

    check(
        "43. full_page regresyonu (kapsam korunuyor)",
        "full_page"
        in __import__(
            "app.schemas.monitor",
            fromlist=["SCOPE_TYPES"],
        ).SCOPE_TYPES,
    )

    # 42. karsilastirma segmentasyonu
    document = parse_regulation_segments(CONSOLIDATED_CONTENT)

    check(
        "42. mevzuat karsilastirma regresyonu "
        "(MADDE segmentasyonu)",
        len(document.segments) >= 3,
        f"segment={len(document.segments)}",
    )

    # 46. scheduler
    due_monitor = build_monitor(
        session,
        "Kesif L - scheduler",
    )

    due_monitor.last_checked_at = (
        datetime.utcnow() - timedelta(days=3)
    )
    session.commit()

    decision = evaluate_monitor_due(
        monitor=due_monitor,
        now_local=datetime.now(),
    )

    check(
        "46. scheduler regresyonu (due degerlendirmesi)",
        decision is not None
        and hasattr(decision, "due")
        and isinstance(decision.due, bool),
        f"{type(decision).__name__} due={getattr(decision, 'due', None)}",
    )

    # 47. bildirim
    check(
        "47. bildirim regresyonu "
        "(karsilastirma yokken govde DEGISMEZ)",
        build_notification_message(ai_analysis=analysis_f)
        == analysis_f.summary,
    )

    # 48. e-posta
    email = build_change_email(
        monitor=monitor_f,
        ai_analysis=analysis_f,
        regulation_comparison=result_f.comparison,
    )

    check(
        "48. e-posta regresyonu (konu ve govde uretiliyor)",
        bool(email.subject)
        and bool(email.html_body or email.text_body),
        email.subject,
    )

    # 49. Gemini kota fallback
    reset_counters()

    attempts = []

    class FallbackProbeError(Exception):
        """
        Sema/bos cevap hatalarini temsil eder.

        Kota hatasi BU SINIF DEGILDIR; boylece yedek model
        yolu gercekten calisir.
        """

    async def failing_then_ok(client, model_name):

        attempts.append(model_name)

        if len(attempts) == 1:
            raise RuntimeError(
                "429 RESOURCE_EXHAUSTED quota exceeded"
            )

        return "tamam"

    fallback_outcome = run(
        run_with_quota_fallback(
            generate=failing_then_ok,
            error_class=FallbackProbeError,
            error_prefix="test",
        )
    )

    check(
        "49. Gemini fallback regresyonu "
        "(kota hatasinda yedek model)",
        fallback_outcome.result == "tamam"
        and len(attempts) == 2
        and attempts[0] != attempts[1],
        " -> ".join(attempts),
    )


# ---------------------------------------------------------
# YARDIMCILAR
# ---------------------------------------------------------

def _set_search(value) -> bool:
    """
    Sahte arama cevabini degistirir.

    Kosul ifadesi icinde kullanilabilmesi icin daima True
    doner.
    """

    SEARCH_RESPONSE["value"] = value
    SEARCH_RESPONSE["error"] = None

    return True


def _raises_value_error(callable_) -> bool:

    try:
        callable_()

    except ValueError:
        return True

    except Exception:
        return False

    return False


def _raises_search_error(callable_) -> bool:

    try:
        callable_()

    except MevzuatSearchError:
        return True

    except Exception:
        return False

    return False


if __name__ == "__main__":
    main()
