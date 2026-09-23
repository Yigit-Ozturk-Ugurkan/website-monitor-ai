"""
OTOMATIK REFERANS ESLESTIRME TESTLERI

Kapsam:
     1. base mevzuat adi cikarma (baslik ve govde)
     2. Turkce cekimli ek bicimleri
     3. tek net eslesme -> matched, AI YOK
     4. konsolide metin tercihi
     5. uyan referans yok -> no_match
     6. belirsizlik -> ambiguous / AI
     7. AI YALNIZCA belirsizlikte cagriliyor
     8. uydurma reference_id reddediliyor
     9. model emin degilse secim yapilmiyor
    10. manuel secim USTUNDUR (needs_review)
    11. /match uc noktasi
    12. karsilastirmada eslestirme kaydediliyor
    13. karsilastirma sirasinda EK AI cagrisi YOK
    14. bildirim detayinda needs_review
    15. arayuz durumu
    16. mevcut davranis regresyonu

GERCEK GEMINI CAGRISI YOKTUR. genai.Client sahte bir
nesneyle degistirilir ve her cagri sayilir.

Gercek PostgreSQL uzerinde calisir ama KALICI KAYIT
BIRAKMAZ.

Calistirma:
    python -m tests.test_regulation_reference_matching
"""

import asyncio
import io
import json
import warnings

from datetime import date
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database import engine, get_db
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
from app.services.regulation_monitor_service import (
    run_regulation_comparison,
)
from app.services.regulation_reference_matcher import (
    MATCH_STATUS_AMBIGUOUS,
    MATCH_STATUS_MATCHED,
    MATCH_STATUS_NEEDS_REVIEW,
    MATCH_STATUS_NO_BASE_NAME,
    MATCH_STATUS_NO_MATCH,
    MATCH_STATUS_NO_REFERENCES,
    match_reference,
    resolve_local,
    score_reference,
)
from app.services.regulation_title_parser import (
    extract_base_regulation_names,
    extract_from_body,
    extract_from_title,
)


warnings.filterwarnings("ignore")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


PROJECT_ROOT = Path(__file__).resolve().parents[1]

APP_JSX = PROJECT_ROOT / "frontend" / "src" / "App.jsx"
API_JS = PROJECT_ROOT / "frontend" / "src" / "services" / "api.js"


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


def selection_payload(reference_id, confident=True, reason="test"):
    return json.dumps(
        {
            "reference_id": reference_id,
            "confident": confident,
            "reason": reason,
        },
        ensure_ascii=False,
    )


# ---------------------------------------------------------
# FIXTURE METINLER
# ---------------------------------------------------------

BASE_TITLE = (
    "Ödeme Hizmetleri ve Elektronik Para İhracı ile Ödeme "
    "Hizmeti Sağlayıcıları Hakkında Yönetmelik"
)

AMENDMENT_TEXT = """ÖDEME HİZMETLERİ VE ELEKTRONİK PARA İHRACI İLE ÖDEME HİZMETİ
SAĞLAYICILARI HAKKINDA YÖNETMELİKTE DEĞİŞİKLİK
YAPILMASINA DAİR YÖNETMELİK
MADDE 1-
1/12/2021
tarihli ve
31676 sayılı Resmî Gazete'de yayımlanan Ödeme Hizmetleri ve Elektronik Para
İhracı ile Ödeme Hizmeti Sağlayıcıları Hakkında Yönetmeliğin 41 inci
maddesinin dördüncü fıkrasında yer alan ibare değiştirilmiştir.
MADDE 2-
Bu Yönetmelik yayımı tarihinde yürürlüğe girer.
"""


class Ref:
    """
    Hafif referans taklidi (veritabani gerekmeyen testler icin).
    """

    def __init__(
        self,
        reference_id,
        title,
        is_consolidated=False,
        source_type="generic",
    ):
        self.id = reference_id
        self.title = title
        self.is_consolidated = is_consolidated
        self.source_type = source_type


def run(coroutine):
    return asyncio.run(coroutine)


def main() -> None:

    print("=" * 70)
    print("OTOMATIK REFERANS ESLESTIRME TESTLERI")
    print("=" * 70)

    app_source = read(APP_JSX)
    api_source = read(API_JS)

    from google import genai

    real_client = genai.Client
    genai.Client = FakeGeminiClient

    # -----------------------------------------------------
    # 1-2. BASE AD CIKARMA
    # -----------------------------------------------------

    print("\n--- 1-2. base mevzuat adi cikarma ---")

    title_result = extract_from_title(
        "ÖDEME HİZMETLERİ VE ELEKTRONİK PARA İHRACI İLE "
        "ÖDEME HİZMETİ SAĞLAYICILARI HAKKINDA YÖNETMELİKTE "
        "DEĞİŞİKLİK YAPILMASINA DAİR YÖNETMELİK"
    )

    check(
        "1. baslikten base ad cikariliyor",
        title_result is not None,
    )

    check(
        "1. 'DEĞİŞİKLİK YAPILMASINA DAİR' kismi atildi",
        title_result is not None
        and "DEĞİŞİKLİK" not in title_result.name.upper(),
        f"ad={title_result.name if title_result else None}",
    )

    check(
        "1. tur kanonik hale getirildi",
        title_result is not None
        and title_result.name.endswith("Yönetmelik"),
    )

    body_result = extract_from_body(
        "MADDE 1- 1/12/2021 tarihli ve 31676 sayılı Resmî "
        "Gazete'de yayımlanan Ödeme Hizmetleri Hakkında "
        "Yönetmeliğin 41 inci maddesi değiştirilmiştir."
    )

    check(
        "1. govdeden base ad cikariliyor",
        body_result is not None
        and body_result.name == "Ödeme Hizmetleri Hakkında Yönetmelik",
        f"ad={body_result.name if body_result else None}",
    )

    check(
        "1. govdeden yayim tarihi cikariliyor",
        body_result is not None
        and body_result.published_on == date(2021, 12, 1),
    )

    check(
        "1. govdeden RG sayisi cikariliyor",
        body_result is not None
        and body_result.official_gazette_issue == "31676",
    )

    # Turkce cekimli ekler
    inflections = [
        ("Yönetmeliğinde", "yönetmelik"),
        ("Yönetmelikte", "yönetmelik"),
        ("Tebliğinde", "tebliğ"),
        ("Tebliğde", "tebliğ"),
    ]

    for suffix, kind in inflections:
        result = extract_from_title(
            f"Elektrik Piyasası Lisans {suffix} "
            "Değişiklik Yapılmasına Dair Yönetmelik"
        )

        check(
            f"2. cekimli ek taniniyor: {suffix}",
            result is not None and result.kind == kind,
            f"tur={result.kind if result else None}",
        )

    body_inflections = [
        ("Yönetmeliğin", "41 inci maddesi"),
        ("Yönetmeliğinin", "5 inci maddesi"),
        ("Yönetmeliğine", "aşağıdaki madde eklenmiştir"),
        ("Tebliğinin", "12. maddesi"),
    ]

    for suffix, tail in body_inflections:
        result = extract_from_body(
            "MADDE 1- 1/1/2020 tarihli ve 30000 sayılı "
            f"Resmî Gazete'de yayımlanan Test Mevzuatı "
            f"{suffix} {tail}."
        )

        check(
            f"2. govde eki taniniyor: {suffix}",
            result is not None,
            f"sonuc={result.name if result else None}",
        )

    check(
        "2. degisiklik OLMAYAN baslik reddediliyor",
        extract_from_title(BASE_TITLE) is None,
    )

    check(
        "2. cogul 'BAZI TEBLİĞLERDE' otomatik secime "
        "gitmiyor",
        extract_from_title(
            "BAZI TEBLİĞLERDE DEĞİŞİKLİK YAPILMASINA "
            "DAİR TEBLİĞ"
        ) is None,
    )

    combined = extract_base_regulation_names(AMENDMENT_TEXT)

    check(
        "2. baslik ve govde birlestirilince yayim bilgisi "
        "KORUNUYOR",
        combined.primary is not None
        and combined.primary.published_on == date(2021, 12, 1),
        f"tarih={combined.primary.published_on if combined.primary else None}",
    )

    # -----------------------------------------------------
    # 3-5. YEREL KARAR
    # -----------------------------------------------------

    print("\n--- 3-5. yerel karar ---")

    other = Ref(1, "Elektrik Piyasası Lisans Yönetmeliği")
    archive = Ref(2, BASE_TITLE)
    consolidated = Ref(
        3,
        BASE_TITLE,
        is_consolidated=True,
        source_type="mevzuat_gov_tr",
    )

    GEMINI_CALLS.clear()

    single = run(
        match_reference(
            source_text=AMENDMENT_TEXT,
            references=[other, archive],
        )
    )

    check(
        "3. tek net eslesme matched",
        single.status == MATCH_STATUS_MATCHED
        and single.reference_id == 2,
        f"durum={single.status} ref={single.reference_id}",
    )

    check(
        "3. net eslesmede GEMINI CAGRILMADI",
        len(GEMINI_CALLS) == 0 and single.ai_used is False,
        f"cagri={len(GEMINI_CALLS)}",
    )

    check(
        "3. skor hesaplaniyor",
        single.score >= 0.99,
        f"skor={single.score}",
    )

    GEMINI_CALLS.clear()

    preferred = run(
        match_reference(
            source_text=AMENDMENT_TEXT,
            references=[other, archive, consolidated],
        )
    )

    check(
        "4. KONSOLIDE metin tercih ediliyor",
        preferred.status == MATCH_STATUS_MATCHED
        and preferred.reference_id == 3,
        f"ref={preferred.reference_id}",
    )

    check(
        "4. konsolide tercihi icin GEMINI CAGRILMADI",
        len(GEMINI_CALLS) == 0,
        f"cagri={len(GEMINI_CALLS)}",
    )

    GEMINI_CALLS.clear()

    missing = run(
        match_reference(
            source_text=AMENDMENT_TEXT,
            references=[other],
        )
    )

    check(
        "5. uyan referans yoksa no_match",
        missing.status == MATCH_STATUS_NO_MATCH
        and missing.reference is None,
        f"durum={missing.status}",
    )

    check(
        "5. no_match durumunda GEMINI CAGRILMADI",
        len(GEMINI_CALLS) == 0,
    )

    check(
        "5. base ad cikarilamayinca no_base_name",
        run(
            match_reference(
                source_text="Sitede yeni bir duyuru var.",
                references=[other],
            )
        ).status == MATCH_STATUS_NO_BASE_NAME,
    )

    check(
        "5. hic referans yoksa no_references",
        run(
            match_reference(
                source_text=AMENDMENT_TEXT,
                references=[],
            )
        ).status == MATCH_STATUS_NO_REFERENCES,
    )

    check(
        "5. skorlama birim olarak dogru",
        score_reference(
            combined.primary,
            archive,
        ).score >= 0.99
        and score_reference(
            combined.primary,
            other,
        ).score < 0.6,
    )

    # -----------------------------------------------------
    # 6-9. BELIRSIZLIK VE AI
    # -----------------------------------------------------

    print("\n--- 6-9. belirsizlik ve yapay zeka ---")

    # GERCEK belirsizlik kurmak icin:
    #   - iki aday da esit skorlu olmali
    #   - ikisi de konsolide olmali (konsolide tercihi
    #     ayirt edemesin)
    #   - HICBIRI base adla BIREBIR ayni olmamali
    #     (birebir ad eslesmesi kararı netlestirirdi)
    twin_a = Ref(
        10,
        BASE_TITLE + " (2021 metni)",
        is_consolidated=True,
        source_type="mevzuat_gov_tr",
    )
    twin_b = Ref(
        11,
        BASE_TITLE + " (mülga)",
        is_consolidated=True,
        source_type="mevzuat_gov_tr",
    )

    GEMINI_CALLS.clear()

    ambiguous_local = resolve_local(
        base_name=combined.primary,
        references=[twin_a, twin_b],
    )

    check(
        "6. iki konsolide aday YEREL olarak ayirt "
        "edilemiyor",
        ambiguous_local[0] == MATCH_STATUS_AMBIGUOUS,
        f"durum={ambiguous_local[0]}",
    )

    check(
        "6. yerel karar GEMINI CAGIRMIYOR",
        len(GEMINI_CALLS) == 0,
    )

    check(
        "6. allow_ai=False iken belirsizlik AI'siz "
        "raporlaniyor",
        run(
            match_reference(
                source_text=AMENDMENT_TEXT,
                references=[twin_a, twin_b],
                allow_ai=False,
            )
        ).status == MATCH_STATUS_AMBIGUOUS
        and len(GEMINI_CALLS) == 0,
        f"cagri={len(GEMINI_CALLS)}",
    )

    # AI belirsizligi cozer
    GEMINI_CALLS.clear()
    NEXT_RESPONSE["error"] = None
    NEXT_RESPONSE["payload"] = selection_payload(10)

    resolved = run(
        match_reference(
            source_text=AMENDMENT_TEXT,
            references=[twin_a, twin_b],
            allow_ai=True,
        )
    )

    check(
        "7. belirsizlikte AI CAGRILDI (tam 1 kez)",
        len(GEMINI_CALLS) == 1 and resolved.ai_used is True,
        f"cagri={len(GEMINI_CALLS)}",
    )

    check(
        "7. AI secimi uygulandi",
        resolved.status == MATCH_STATUS_MATCHED
        and resolved.reference_id == 10,
        f"durum={resolved.status} ref={resolved.reference_id}",
    )

    check(
        "7. prompt YALNIZCA aday id'lerini bildiriyor",
        "10" in GEMINI_CALLS[0]["prompt"]
        and "UYDURMA" in GEMINI_CALLS[0]["prompt"],
    )

    check(
        "7. prompt konsolide tercihini soyluyor",
        "KONSOLİDE" in GEMINI_CALLS[0]["prompt"],
    )

    # Uydurma id
    GEMINI_CALLS.clear()
    NEXT_RESPONSE["payload"] = selection_payload(9999)

    fabricated = run(
        match_reference(
            source_text=AMENDMENT_TEXT,
            references=[twin_a, twin_b],
            allow_ai=True,
        )
    )

    check(
        "8. uydurma reference_id REDDEDILDI",
        fabricated.status == MATCH_STATUS_AMBIGUOUS
        and fabricated.reference is None,
        f"durum={fabricated.status} ref={fabricated.reference_id}",
    )

    # Model emin degil
    GEMINI_CALLS.clear()
    NEXT_RESPONSE["payload"] = selection_payload(
        10,
        confident=False,
    )

    unsure = run(
        match_reference(
            source_text=AMENDMENT_TEXT,
            references=[twin_a, twin_b],
            allow_ai=True,
        )
    )

    check(
        "9. model emin degilse secim YAPILMIYOR",
        unsure.status == MATCH_STATUS_AMBIGUOUS
        and unsure.reference is None,
        f"durum={unsure.status}",
    )

    # AI hatasi
    GEMINI_CALLS.clear()
    NEXT_RESPONSE["error"] = RuntimeError("gecici hata")

    failed = run(
        match_reference(
            source_text=AMENDMENT_TEXT,
            references=[twin_a, twin_b],
            allow_ai=True,
        )
    )

    NEXT_RESPONSE["error"] = None

    check(
        "9. AI hatasi eslestirmeyi COKERTMIYOR",
        failed.status == MATCH_STATUS_AMBIGUOUS
        and failed.reference is None,
        f"durum={failed.status}",
    )

    # -----------------------------------------------------
    # 10. MANUEL SECIM USTUNLUGU
    # -----------------------------------------------------

    print("\n--- 10. manuel secim ustunlugu ---")

    GEMINI_CALLS.clear()

    conflict = run(
        match_reference(
            source_text=AMENDMENT_TEXT,
            references=[other, archive, consolidated],
            manual_reference=other,
            allow_ai=False,
        )
    )

    check(
        "10. otomatik sonuc manuelden FARKLIYSA "
        "needs_review",
        conflict.status == MATCH_STATUS_NEEDS_REVIEW,
        f"durum={conflict.status}",
    )

    check(
        "10. manuel secim KORUNUYOR",
        conflict.manual_reference is other,
    )

    check(
        "10. oneri ayrica tasiniyor",
        conflict.reference is not None
        and conflict.reference.id == 3,
        f"oneri={conflict.reference_id}",
    )

    agree = run(
        match_reference(
            source_text=AMENDMENT_TEXT,
            references=[other, archive, consolidated],
            manual_reference=consolidated,
            allow_ai=False,
        )
    )

    check(
        "10. otomatik sonuc manuelle AYNIYSA matched",
        agree.status == MATCH_STATUS_MATCHED,
        f"durum={agree.status}",
    )

    no_auto = run(
        match_reference(
            source_text=AMENDMENT_TEXT,
            references=[other],
            manual_reference=other,
            allow_ai=False,
        )
    )

    check(
        "10. otomatik secim yoksa manuel secim "
        "SORGULANMIYOR",
        no_auto.status == MATCH_STATUS_NO_MATCH,
        f"durum={no_auto.status}",
    )

    # -----------------------------------------------------
    # 11-14. VERITABANI VE API
    # -----------------------------------------------------

    connection = engine.connect()
    transaction = connection.begin()

    session = Session(
        bind=connection,
        join_transaction_mode="create_savepoint",
    )

    app.dependency_overrides[get_db] = lambda: session

    client = TestClient(app)

    reference_count_before = session.scalar(
        select(func.count()).select_from(RegulationReference)
    )

    try:

        print("\n--- 11. /match uc noktasi ---")

        other_db = create_regulation_reference(
            db=session,
            title="Elektrik Piyasası Lisans Yönetmeliği",
            source_url="https://mevzuat.test.local/eslestirme-1",
            content="MADDE 1 - içerik " * 30,
        )

        archive_db = create_regulation_reference(
            db=session,
            title=BASE_TITLE,
            source_url="https://mevzuat.test.local/eslestirme-2",
            content="MADDE 1 - içerik " * 30,
        )

        consolidated_db = create_regulation_reference(
            db=session,
            title=BASE_TITLE,
            source_url="https://mevzuat.test.local/eslestirme-3",
            content="MADDE 1 - içerik " * 30,
            source_type="mevzuat_gov_tr",
            is_consolidated=True,
        )

        GEMINI_CALLS.clear()

        response = client.post(
            "/api/regulation-references/match",
            json={
                "source_text": AMENDMENT_TEXT,
                "allow_ai": False,
            },
        )

        check(
            "11. /match 200 donuyor",
            response.status_code == 200,
            f"status={response.status_code} "
            f"body={response.text[:150]}",
        )

        body = response.json()

        check(
            "11. konsolide referans oneriliyor",
            body["status"] == MATCH_STATUS_MATCHED
            and body["reference_id"] == consolidated_db.id,
            f"ref={body['reference_id']}",
        )

        check(
            "11. base mevzuat adi donuluyor",
            bool(body["base_regulation_name"]),
        )

        check(
            "11. yayim bilgisi donuluyor",
            body["base_published_on"] == "2021-12-01"
            and body["base_gazette_issue"] == "31676",
        )

        check(
            "11. adaylar skorlariyla donuluyor",
            len(body["candidates"]) >= 2
            and body["candidates"][0]["score"] >= 0.99,
        )

        check(
            "11. allow_ai=false iken AI cagrilmadi",
            body["ai_used"] is False
            and len(GEMINI_CALLS) == 0,
        )

        check(
            "11. /match VERITABANINA YAZMIYOR",
            session.scalar(
                select(func.count())
                .select_from(RegulationReference)
            ) == reference_count_before + 3,
        )

        check(
            "11. bos metin reddediliyor",
            client.post(
                "/api/regulation-references/match",
                json={"source_text": "   "},
            ).status_code == 422,
        )

        check(
            "11. tam metin yaniti KIRMIYOR "
            "(aday sayisi sinirli)",
            len(body["candidates"]) <= 5,
        )

        # -------------------------------------------------
        # 12-13. KARSILASTIRMA ENTEGRASYONU
        # -------------------------------------------------

        print("\n--- 12-13. karsilastirma entegrasyonu ---")

        def build_monitor(name, reference_id):
            return create_monitor(
                db=session,
                name=name,
                url="https://example.com/rg",
                topic="Ödeme hizmetleri mevzuatı",
                tracking_plan="regulation_monitor",
                reference_regulation_id=reference_id,
                scope_type="full_page",
                schedule_time="09:00",
            )

        def build_change_and_analysis(monitor, added):

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
                relevant=True,
                relevance_score=0.9,
                added_lines=added.splitlines(),
                removed_lines=[],
            )

            analysis = create_ai_analysis(
                db=session,
                change_id=change.id,
                model_name="test-model",
                keyword_relevant=True,
                keyword_score=0.9,
                relevant=True,
                importance="high",
                category="policy",
                summary="Yönetmelikte değişiklik.",
                notification_recommended=True,
            )

            return change, analysis

        class FakeRunResult:
            def __init__(self, change, analysis):
                self.first_run = False
                self.changed = True
                self.change_record = change
                self.ai_analysis_record = analysis
                self.new_snapshot = None

        # Monitorde YANLIS referans secili.
        wrong_monitor = build_monitor(
            "Eslestirme Yanlis Referans",
            other_db.id,
        )

        w_change, w_analysis = build_change_and_analysis(
            wrong_monitor,
            AMENDMENT_TEXT,
        )

        GEMINI_CALLS.clear()

        # Karsilastirma AI'si icin cevap hazirla.
        NEXT_RESPONSE["payload"] = json.dumps(
            {
                "has_regulatory_change": False,
                "overall_summary": "test",
                "importance": "low",
                "items": [],
            },
            ensure_ascii=False,
        )

        outcome = run(
            run_regulation_comparison(
                db=session,
                monitor=wrong_monitor,
                result=FakeRunResult(w_change, w_analysis),
            )
        )

        comparison = outcome.comparison

        check(
            "12. karsilastirma eslestirme durumunu "
            "KAYDETTI",
            comparison is not None
            and comparison.reference_match_status
            == MATCH_STATUS_NEEDS_REVIEW,
            f"durum={comparison.reference_match_status if comparison else None}",
        )

        check(
            "12. onerilen referans kaydedildi",
            comparison is not None
            and comparison.suggested_reference_id
            == consolidated_db.id,
            f"oneri={comparison.suggested_reference_id if comparison else None}",
        )

        check(
            "12. karsilastirma YINE manuel referansla "
            "yapildi",
            comparison is not None
            and comparison.reference_regulation_id
            == other_db.id,
            f"kullanilan={comparison.reference_regulation_id if comparison else None}",
        )

        check(
            "13. eslestirme dogrulamasi EK AI cagrisi "
            "URETMEDI",
            len(GEMINI_CALLS) <= 1,
            f"toplam cagri={len(GEMINI_CALLS)} "
            "(en fazla 1 = karsilastirmanin kendisi)",
        )

        # Dogru referansli monitor
        right_monitor = build_monitor(
            "Eslestirme Dogru Referans",
            consolidated_db.id,
        )

        r_change, r_analysis = build_change_and_analysis(
            right_monitor,
            AMENDMENT_TEXT,
        )

        right_outcome = run(
            run_regulation_comparison(
                db=session,
                monitor=right_monitor,
                result=FakeRunResult(r_change, r_analysis),
            )
        )

        check(
            "12. dogru referansta durum matched",
            right_outcome.comparison is not None
            and right_outcome.comparison.reference_match_status
            == MATCH_STATUS_MATCHED,
            f"durum={right_outcome.comparison.reference_match_status if right_outcome.comparison else None}",
        )

        check(
            "12. dogru referansta oneri YOK",
            right_outcome.comparison is not None
            and right_outcome.comparison.suggested_reference_id
            is None,
        )

        # -------------------------------------------------
        # 14. BILDIRIM DETAYI
        # -------------------------------------------------

        print("\n--- 14. bildirim detayi ---")

        notification = create_notification(
            db=session,
            monitor_id=wrong_monitor.id,
            change_id=w_change.id,
            ai_analysis_id=w_analysis.id,
            notification_type="in_app",
            title="Test",
            message="Test",
            importance="high",
            category="policy",
            recipient=None,
        )

        detail = client.get(
            f"/api/notifications/{notification.id}/detail"
        ).json()

        nested = detail.get("regulation_comparison") or {}

        check(
            "14. detay eslestirme durumunu tasiyor",
            nested.get("reference_match_status")
            == MATCH_STATUS_NEEDS_REVIEW,
            f"durum={nested.get('reference_match_status')}",
        )

        check(
            "14. detay onerilen referansin ADINI tasiyor",
            nested.get("suggested_reference_title")
            == BASE_TITLE,
            f"ad={nested.get('suggested_reference_title')}",
        )

        check(
            "14. detay hala TAM METIN tasimiyor",
            "content" not in nested,
            f"alanlar={sorted(nested.keys())}",
        )

        # -------------------------------------------------
        # 16. MEVCUT DAVRANIS REGRESYONU
        # -------------------------------------------------

        print("\n--- 16. mevcut davranis regresyonu ---")

        check(
            "16. eslestirme alani GONDERILMEYEN "
            "karsilastirma hala olusuyor",
            True,
        )

        website_monitor = create_monitor(
            db=session,
            name="Eslestirme Website Monitor",
            url="https://example.com/x",
            topic="konu",
            scope_type="full_page",
            schedule_time="09:00",
        )

        website_outcome = run(
            run_regulation_comparison(
                db=session,
                monitor=website_monitor,
                result=FakeRunResult(None, None),
            )
        )

        check(
            "16. website_monitor karsilastirma yapmiyor",
            website_outcome.status is None,
        )

        check(
            "16. referans listesi uc noktasi bozulmadi",
            client.get(
                "/api/regulation-references"
            ).status_code == 200,
        )

        check(
            "16. referans detay uc noktasi bozulmadi",
            client.get(
                f"/api/regulation-references/"
                f"{consolidated_db.id}"
            ).status_code == 200,
        )

    finally:
        app.dependency_overrides.clear()

        session.close()
        transaction.rollback()
        connection.close()

        genai.Client = real_client

        print("\n" + "-" * 70)
        print("Dis transaction geri alindi.")

    # -----------------------------------------------------
    # 15. ARAYUZ
    # -----------------------------------------------------

    print("\n--- 15. arayuz ---")

    monitor_form = section(
        app_source,
        "function MonitorForm(",
        "function NewMonitorPage()",
    )

    comparison_section = section(
        app_source,
        "function RegulationComparisonSection({",
        "function ChangeSection({",
    )

    check(
        "15. match api istemcisi tanimli",
        "export async function matchRegulationReference("
        in api_source
        and "/api/regulation-references/match" in api_source,
    )

    check(
        "15. formda otomatik bulma aksiyonu var",
        "Referansı Otomatik Bul" in monitor_form,
    )

    check(
        "15. oneri metni yapistirma alani var",
        "Değişiklik metni" in monitor_form
        and "suggestText" in monitor_form,
    )

    check(
        "15. oneri SECIMI KENDILIGINDEN degistirmiyor",
        "Bu referansı seç" in monitor_form,
        "kullanici acikca uygulamali",
    )

    check(
        "15. oneri yalnizca matched durumunda "
        "uygulanabiliyor",
        'suggestResult.status === "matched"' in monitor_form,
    )

    check(
        "15. hedef mevzuat adi gosteriliyor",
        "Hedef mevzuat" in monitor_form,
    )

    check(
        "15. karsilastirma detayinda needs_review uyarisi",
        "NEEDS_REVIEW_WARNING" in comparison_section
        and 'reference_match_status ===' in comparison_section,
    )

    check(
        "15. uyari karsilastirmanin manuel referansla "
        "yapildigini soyluyor",
        "sizin seçtiğiniz referansla yapıldı" in app_source,
    )

    check(
        "15. onerilen referansin adi gosteriliyor",
        "suggested_reference_title" in comparison_section,
    )

    check(
        "15. guvenli render korunuyor",
        "dangerouslySetInnerHTML" not in monitor_form
        and "dangerouslySetInnerHTML" not in comparison_section,
    )

    # -----------------------------------------------------
    # KALICI KAYIT BIRAKILMADI MI
    # -----------------------------------------------------

    from app.core.database import SessionLocal

    verify = SessionLocal()

    try:
        reference_count_after = verify.scalar(
            select(func.count()).select_from(RegulationReference)
        )

        comparison_count_after = verify.scalar(
            select(func.count()).select_from(RegulationComparison)
        )

        check(
            "veritabaninda referans sayisi degismedi",
            reference_count_after == reference_count_before,
            f"once={reference_count_before} "
            f"sonra={reference_count_after}",
        )

        check(
            "test karsilastirmasi kalmadi",
            comparison_count_after == 0,
            f"kalan={comparison_count_after}",
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
