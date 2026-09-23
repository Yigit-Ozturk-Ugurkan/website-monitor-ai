"""
TAKIP PLANI / REGULATION MONITOR TESTLERI

Kapsam:
    1.  mevcut monitorler varsayilan website_monitor
    2.  website_monitor create davranisi DEGISMEDI
    3.  regulation_monitor referans ZORUNLU
    4.  gecerli regulation monitor kabul ediliyor
    5.  gecersiz reference id reddediliyor
    14. website -> regulation gecisi referans istiyor
    15. regulation -> website gecisi referansi temizliyor
    19. full_page regresyonu
    20. auto_section regresyonu
    21. manual_section regresyonu
    22. bildirim alanlari regresyonu
    23. scheduler regresyonu (alan okuma bozulmadi)
    24. e-mail alanlari regresyonu
    +   pipeline davranisi: regulation_comparison_status

Gercek PostgreSQL uzerinde calisir ama KALICI KAYIT BIRAKMAZ.
Dis transaction sonunda rollback edilir.

Ag istegi yapilmaz, Gemini cagrilmaz. Monitor /run bu testte
CALISTIRILMAZ; karsilastirma durumu dogrudan servis
fonksiyonu uzerinden dogrulanir.

Calistirma:
    python -m tests.test_regulation_monitor_api
"""

import warnings

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database import engine, get_db
from app.models.monitor import Monitor
from app.models.regulation_reference import RegulationReference
from app.repositories.monitor_repository import (
    create_monitor,
    should_reset_regulation_reference,
)
from app.repositories.regulation_reference_repository import (
    create_regulation_reference,
)
from app.services.regulation_monitor_service import (
    COMPARISON_COMPLETED,
    COMPARISON_FAILED,
    COMPARISON_NO_MATCH,
    COMPARISON_SKIPPED_FIRST_RUN,
    COMPARISON_SKIPPED_IRRELEVANT,
    COMPARISON_SKIPPED_NO_CHANGE,
    TRACKING_PLAN_REGULATION,
    TRACKING_PLAN_WEBSITE,
    is_regulation_monitor,
    resolve_tracking_plan,
    run_regulation_comparison,
)


warnings.filterwarnings("ignore")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


PASSED = []
FAILED = []


def check(label: str, condition: bool, detail: str = "") -> None:

    status = "PASS" if condition else "FAIL"

    print(f"[{status}] {label}" + (f" -> {detail}" if detail else ""))

    if condition:
        PASSED.append(label)
    else:
        FAILED.append(f"{label} -> {detail}")


BASE_PAYLOAD = {
    "name": "Regulation Plan Test Monitor",
    "url": "https://example.com/mevzuat",
    "topic": "Elektrik piyasasi lisans degisiklikleri",
    "scope_type": "full_page",
    "schedule_type": "daily",
    "schedule_time": "09:00",
    "is_unlimited": True,
    "notify_in_app": True,
    "notify_email": False,
    "notification_email": None,
}


def payload(**overrides) -> dict:
    data = dict(BASE_PAYLOAD)
    data.update(overrides)

    return data


def error_message(response) -> str:
    detail = response.json().get("detail")

    if isinstance(detail, list) and detail:
        return detail[0].get("msg", "")

    return str(detail)


def main() -> None:

    print("=" * 70)
    print("TAKIP PLANI / REGULATION MONITOR TESTLERI")
    print("=" * 70)

    connection = engine.connect()
    transaction = connection.begin()

    session = Session(
        bind=connection,
        join_transaction_mode="create_savepoint",
    )

    app.dependency_overrides[get_db] = lambda: session

    client = TestClient(app)

    from app.core import scheduler

    check(
        "test sirasinda scheduler baslatilmadi",
        scheduler._scheduler_task is None
        or scheduler._scheduler_task.done(),
    )

    monitor_count_before = session.scalar(
        select(func.count()).select_from(Monitor)
    )

    print(f"Baslangic monitor sayisi: {monitor_count_before}")
    print("-" * 70)

    try:

        # -------------------------------------------------
        # HAZIRLIK: bir referans yonetmelik
        #
        # Kaynak indirilmez; kayit dogrudan repository ile
        # olusturulur. Bu test bir fetch testi degildir.
        # -------------------------------------------------

        reference = create_regulation_reference(
            db=session,
            title="Test Lisans Yonetmeligi",
            source_url="https://mevzuat.test.local/plan-testi",
            content="MADDE 1 - Test icerigi. " * 20,
        )

        second_reference = create_regulation_reference(
            db=session,
            title="Ikinci Test Yonetmeligi",
            source_url="https://mevzuat.test.local/plan-testi-2",
            content="MADDE 1 - Ikinci icerik. " * 20,
        )

        # -------------------------------------------------
        # 1. MEVCUT MONITORLER VARSAYILAN website_monitor
        # -------------------------------------------------

        print("\n--- 1. mevcut kayitlar website_monitor ---")

        existing = session.scalars(
            select(Monitor).limit(50)
        ).all()

        # DIKKAT: burada VERITABANININ TAMAMI okunuyor.
        #
        # Eskiden "her kayit website_monitor" iddia ediliyordu.
        # Bu, gelistirme veritabaninda hic mevzuat monitoru
        # olmadigi VARSAYIMINA dayaniyordu. Urun gercekten
        # kullanilmaya baslandiginda (gercek kullanici kabulu)
        # veritabaninda gercek bir regulation_monitor olustu ve
        # iddia coktu.
        #
        # Test kendi olusturmadigi satirlar hakkinda boyle bir
        # sey iddia ETMEMELIDIR. Kullanici verisinden BAGIMSIZ
        # olarak gecerli olan degismezler dogrulanir.
        check(
            "1. mevcut kayitlarin tracking_plan degeri gecerli",
            all(
                monitor.tracking_plan
                in (
                    TRACKING_PLAN_WEBSITE,
                    TRACKING_PLAN_REGULATION,
                )
                for monitor in existing
            ),
            f"planlar={sorted({m.tracking_plan for m in existing})}",
        )

        check(
            "1. yalnizca regulation_monitor referans tasiyabilir",
            all(
                monitor.reference_regulation_id is None
                or monitor.tracking_plan
                == TRACKING_PLAN_REGULATION
                for monitor in existing
            ),
        )

        # Takip plani parametresi GONDERILMEYEN eski cagri.
        legacy = create_monitor(
            db=session,
            name="Legacy Repository Monitor",
            url="https://example.com/legacy",
            topic="eski cagri",
            scope_type="full_page",
            schedule_time="10:00",
        )

        check(
            "1. tracking_plan gondermeyen create_monitor "
            "website_monitor uretiyor",
            legacy.tracking_plan == TRACKING_PLAN_WEBSITE,
            f"plan={legacy.tracking_plan}",
        )

        check(
            "1. eski cagri referans olusturmuyor",
            legacy.reference_regulation_id is None,
        )

        check(
            "1. bos/None plan website_monitor'e normalize "
            "ediliyor",
            resolve_tracking_plan(None) == TRACKING_PLAN_WEBSITE
            and resolve_tracking_plan("")
            == TRACKING_PLAN_WEBSITE
            and resolve_tracking_plan("   ")
            == TRACKING_PLAN_WEBSITE,
        )

        # -------------------------------------------------
        # 2. website_monitor CREATE DEGISMEDI
        # -------------------------------------------------

        print("\n--- 2. website_monitor create degismedi ---")

        # tracking_plan HIC gonderilmiyor: eski istemci.
        legacy_api = client.post(
            "/api/monitors",
            json=payload(),
        )

        check(
            "2. tracking_plan gondermeyen istek 201 aliyor",
            legacy_api.status_code == 201,
            f"status={legacy_api.status_code} "
            f"body={legacy_api.text[:200]}",
        )

        legacy_body = legacy_api.json()

        check(
            "2. yanit website_monitor bildiriyor",
            legacy_body["tracking_plan"] == TRACKING_PLAN_WEBSITE,
            f"plan={legacy_body['tracking_plan']}",
        )

        check(
            "2. yanitta referans null",
            legacy_body["reference_regulation_id"] is None
            and legacy_body["reference_regulation_title"] is None,
        )

        check(
            "2. mevcut alanlar bozulmadi",
            legacy_body["name"] == BASE_PAYLOAD["name"]
            and legacy_body["url"] == BASE_PAYLOAD["url"]
            and legacy_body["topic"] == BASE_PAYLOAD["topic"]
            and legacy_body["scope_type"] == "full_page"
            and legacy_body["schedule_type"] == "daily"
            and legacy_body["schedule_time"] == "09:00"
            and legacy_body["is_active"] is True,
        )

        website_monitor_id = legacy_body["id"]

        explicit = client.post(
            "/api/monitors",
            json=payload(
                tracking_plan=TRACKING_PLAN_WEBSITE,
            ),
        )

        check(
            "2. acikca website_monitor gondermek de kabul "
            "ediliyor",
            explicit.status_code == 201,
            f"status={explicit.status_code}",
        )

        website_with_reference = client.post(
            "/api/monitors",
            json=payload(
                tracking_plan=TRACKING_PLAN_WEBSITE,
                reference_regulation_id=reference.id,
            ),
        )

        check(
            "2. website_monitor referans ile kaydedilemiyor",
            website_with_reference.status_code == 422,
            f"status={website_with_reference.status_code}",
        )

        check(
            "2. gecersiz takip plani reddediliyor",
            client.post(
                "/api/monitors",
                json=payload(tracking_plan="uydurma_plan"),
            ).status_code == 422,
        )

        # -------------------------------------------------
        # 3. MANUEL MODDA REFERANS ZORUNLU
        #
        # NOT: Referans zorunlulugu artik TAKIP PLANINA
        # degil REFERANS SECIM MODUNA baglidir. automatic
        # modda referans her calistirmada metinden
        # belirlenir ve olusturmada istenmez.
        # -------------------------------------------------

        print("\n--- 3. manuel modda referans zorunlu ---")

        missing_reference = client.post(
            "/api/monitors",
            json=payload(
                tracking_plan=TRACKING_PLAN_REGULATION,
                reference_selection_mode="manual",
            ),
        )

        check(
            "3. MANUEL modda referanssiz monitor "
            "reddediliyor",
            missing_reference.status_code == 422,
            f"status={missing_reference.status_code}",
        )

        check(
            "3. hata mesaji referans istedigini soyluyor",
            "referans" in error_message(missing_reference).lower(),
            error_message(missing_reference),
        )

        null_reference = client.post(
            "/api/monitors",
            json=payload(
                tracking_plan=TRACKING_PLAN_REGULATION,
                reference_selection_mode="manual",
                reference_regulation_id=None,
            ),
        )

        check(
            "3. manuel modda acikca null gonderilen "
            "referans da reddediliyor",
            null_reference.status_code == 422,
            f"status={null_reference.status_code}",
        )

        # Sayim TESTIN KENDI kayitlariyla sinirlanir.
        # Butun tabloyu saymak, veritabanindaki gercek
        # kullanici monitorlerini de sayardi.
        check(
            "3. reddedilen istekler kayit olusturmadi",
            session.scalar(
                select(func.count())
                .select_from(Monitor)
                .where(
                    Monitor.tracking_plan
                    == TRACKING_PLAN_REGULATION,
                    Monitor.url.like("https://example.com/%"),
                )
            ) == 0,
        )

        # OTOMATIK modda referans ZORUNLU DEGIL.
        automatic_without_reference = client.post(
            "/api/monitors",
            json=payload(
                name="Otomatik Referans Monitor",
                tracking_plan=TRACKING_PLAN_REGULATION,
                reference_selection_mode="automatic",
            ),
        )

        check(
            "3. OTOMATIK modda referanssiz monitor "
            "KABUL EDILIYOR",
            automatic_without_reference.status_code == 201,
            f"status={automatic_without_reference.status_code} "
            f"body={automatic_without_reference.text[:160]}",
        )

        check(
            "3. otomatik monitorde referans null kaliyor",
            automatic_without_reference.status_code == 201
            and automatic_without_reference.json()[
                "reference_regulation_id"
            ] is None,
        )

        check(
            "3. yanit secim modunu bildiriyor",
            automatic_without_reference.status_code == 201
            and automatic_without_reference.json()[
                "reference_selection_mode"
            ] == "automatic",
        )

        # -------------------------------------------------
        # 4. GECERLI regulation_monitor
        # -------------------------------------------------

        print("\n--- 4. gecerli regulation monitor ---")

        valid = client.post(
            "/api/monitors",
            json=payload(
                name="Mevzuat Takibi",
                tracking_plan=TRACKING_PLAN_REGULATION,
                reference_regulation_id=reference.id,
            ),
        )

        check(
            "4. gecerli regulation monitor 201 aliyor",
            valid.status_code == 201,
            f"status={valid.status_code} body={valid.text[:200]}",
        )

        valid_body = valid.json()
        regulation_monitor_id = valid_body["id"]

        check(
            "4. yanit regulation_monitor bildiriyor",
            valid_body["tracking_plan"]
            == TRACKING_PLAN_REGULATION,
        )

        check(
            "4. referans id yanitta",
            valid_body["reference_regulation_id"] == reference.id,
        )

        check(
            "4. referans BASLIGI yanitta (kart icin)",
            valid_body["reference_regulation_title"]
            == reference.title,
            f"baslik={valid_body['reference_regulation_title']}",
        )

        check(
            "4. yanit referansin TAM METNINI tasimiyor",
            "reference_regulation_content" not in valid_body
            and "content" not in valid_body,
            f"alanlar={sorted(valid_body.keys())}",
        )

        stored_monitor = session.get(
            Monitor,
            regulation_monitor_id,
        )

        check(
            "4. veritabaninda plan ve referans kayitli",
            stored_monitor.tracking_plan
            == TRACKING_PLAN_REGULATION
            and stored_monitor.reference_regulation_id
            == reference.id,
        )

        check(
            "4. ayni referans BIRDEN FAZLA monitorde "
            "kullanilabiliyor",
            client.post(
                "/api/monitors",
                json=payload(
                    name="Ikinci Mevzuat Takibi",
                    tracking_plan=TRACKING_PLAN_REGULATION,
                    reference_regulation_id=reference.id,
                ),
            ).status_code == 201,
        )

        check(
            "4. referans metni kopyalanmadi (tek kayit)",
            session.scalar(
                select(func.count())
                .select_from(RegulationReference)
                .where(
                    RegulationReference.source_url
                    == reference.source_url
                )
            ) == 1,
        )

        # -------------------------------------------------
        # 5. GECERSIZ REFERANS
        # -------------------------------------------------

        print("\n--- 5. gecersiz referans reddediliyor ---")

        invalid = client.post(
            "/api/monitors",
            json=payload(
                tracking_plan=TRACKING_PLAN_REGULATION,
                reference_regulation_id=99999999,
            ),
        )

        check(
            "5. olmayan referans id reddediliyor (422)",
            invalid.status_code == 422,
            f"status={invalid.status_code}",
        )

        check(
            "5. hata mesaji anlasilir",
            "bulunamadı" in error_message(invalid).lower()
            or "bulunamadi" in error_message(invalid).lower(),
            error_message(invalid),
        )

        check(
            "5. gecersiz referans PATCH ile de kabul "
            "edilmiyor",
            client.patch(
                f"/api/monitors/{regulation_monitor_id}",
                json={"reference_regulation_id": 99999999},
            ).status_code == 422,
        )

        check(
            "5. reddedilen PATCH mevcut referansi bozmadi",
            session.get(
                Monitor,
                regulation_monitor_id,
            ).reference_regulation_id == reference.id,
        )

        # -------------------------------------------------
        # 14. WEBSITE -> REGULATION
        # -------------------------------------------------

        print("\n--- 14. website -> regulation ---")

        # MANUEL moda gecerken referans ZORUNLU.
        without_reference = client.patch(
            f"/api/monitors/{website_monitor_id}",
            json={
                "tracking_plan": TRACKING_PLAN_REGULATION,
                "reference_selection_mode": "manual",
            },
        )

        check(
            "14. manuel moda referans gondermeden gecis "
            "reddediliyor",
            without_reference.status_code == 422,
            f"status={without_reference.status_code}",
        )

        check(
            "14. hata referans istedigini soyluyor",
            "referans"
            in error_message(without_reference).lower(),
            error_message(without_reference),
        )

        session.expire_all()

        check(
            "14. reddedilen gecis plani DEGISTIRMEDI",
            session.get(
                Monitor,
                website_monitor_id,
            ).tracking_plan == TRACKING_PLAN_WEBSITE,
        )

        # OTOMATIK moda gecerken referans gerekmez.
        automatic_switch = client.patch(
            f"/api/monitors/{website_monitor_id}",
            json={
                "tracking_plan": TRACKING_PLAN_REGULATION,
                "reference_selection_mode": "automatic",
            },
        )

        check(
            "14. OTOMATIK moda referanssiz gecis kabul "
            "ediliyor",
            automatic_switch.status_code == 200
            and automatic_switch.json()[
                "reference_regulation_id"
            ] is None,
            f"status={automatic_switch.status_code}",
        )

        with_reference = client.patch(
            f"/api/monitors/{website_monitor_id}",
            json={
                "tracking_plan": TRACKING_PLAN_REGULATION,
                "reference_selection_mode": "manual",
                "reference_regulation_id": reference.id,
            },
        )

        check(
            "14. referansla birlikte gecis kabul ediliyor",
            with_reference.status_code == 200,
            f"status={with_reference.status_code}",
        )

        check(
            "14. gecis sonrasi plan ve referans dogru",
            with_reference.json()["tracking_plan"]
            == TRACKING_PLAN_REGULATION
            and with_reference.json()["reference_regulation_id"]
            == reference.id,
        )

        # -------------------------------------------------
        # 15. REGULATION -> WEBSITE
        # -------------------------------------------------

        print("\n--- 15. regulation -> website ---")

        back_to_website = client.patch(
            f"/api/monitors/{website_monitor_id}",
            json={
                "tracking_plan": TRACKING_PLAN_WEBSITE,
            },
        )

        check(
            "15. referans gondermeden geri donus kabul "
            "ediliyor",
            back_to_website.status_code == 200,
            f"status={back_to_website.status_code} "
            f"body={back_to_website.text[:200]}",
        )

        check(
            "15. referans OTOMATIK temizlendi",
            back_to_website.json()["reference_regulation_id"]
            is None,
            f"referans="
            f"{back_to_website.json()['reference_regulation_id']}",
        )

        check(
            "15. referans basligi da dusuyor",
            back_to_website.json()["reference_regulation_title"]
            is None,
        )

        session.expire_all()

        check(
            "15. veritabaninda da referans temizlendi",
            session.get(
                Monitor,
                website_monitor_id,
            ).reference_regulation_id is None,
        )

        check(
            "15. referans KAYDI silinmedi (paylasilan varlik)",
            session.get(
                RegulationReference,
                reference.id,
            ) is not None,
        )

        # Plan degismiyorsa referans korunmali.
        unrelated = client.patch(
            f"/api/monitors/{regulation_monitor_id}",
            json={"topic": "guncellenmis konu"},
        )

        check(
            "15. ilgisiz PATCH referansi BOZMUYOR",
            unrelated.status_code == 200
            and unrelated.json()["reference_regulation_id"]
            == reference.id,
            f"referans="
            f"{unrelated.json().get('reference_regulation_id')}",
        )

        url_change = client.patch(
            f"/api/monitors/{regulation_monitor_id}",
            json={"url": "https://example.com/baska-sayfa"},
        )

        check(
            "15. URL degisimi referansi BOZMUYOR "
            "(referans sayfaya degil mevzuata ait)",
            url_change.status_code == 200
            and url_change.json()["reference_regulation_id"]
            == reference.id,
        )

        swap = client.patch(
            f"/api/monitors/{regulation_monitor_id}",
            json={
                "reference_regulation_id": second_reference.id,
            },
        )

        check(
            "15. referans baska bir referansla "
            "degistirilebiliyor",
            swap.status_code == 200
            and swap.json()["reference_regulation_id"]
            == second_reference.id,
            f"status={swap.status_code}",
        )

        check(
            "15. reset yalnizca tracking_plan degisimine "
            "bagli",
            should_reset_regulation_reference(
                monitor=session.get(
                    Monitor,
                    regulation_monitor_id,
                ),
                updates={
                    "tracking_plan": TRACKING_PLAN_WEBSITE,
                },
            ) is True
            and should_reset_regulation_reference(
                monitor=session.get(
                    Monitor,
                    regulation_monitor_id,
                ),
                updates={"url": "https://example.com/x"},
            ) is False,
        )

        # -------------------------------------------------
        # PIPELINE DAVRANISI
        # -------------------------------------------------

        print("\n--- pipeline: karsilastirma durumu ---")

        website_record = session.get(
            Monitor,
            website_monitor_id,
        )

        regulation_record = session.get(
            Monitor,
            regulation_monitor_id,
        )

        check(
            "regulation monitor dogru taniniyor",
            is_regulation_monitor(regulation_record) is True
            and is_regulation_monitor(website_record) is False,
        )

        # Sprint 2: karsilastirma artik GERCEKTEN calisiyor.
        # Durum sabit bir placeholder degil, calistirmanin
        # kendisinden gelir.

        import asyncio

        class _FirstRunResult:
            first_run = True
            changed = False
            change_record = None
            ai_analysis_record = None

        check(
            "website_monitor icin karsilastirma durumu None",
            asyncio.run(
                run_regulation_comparison(
                    db=session,
                    monitor=website_record,
                    result=_FirstRunResult(),
                )
            ).status is None,
        )

        check(
            "regulation monitor ilk calistirmada "
            "karsilastirma YAPMIYOR",
            asyncio.run(
                run_regulation_comparison(
                    db=session,
                    monitor=regulation_record,
                    result=_FirstRunResult(),
                )
            ).status == COMPARISON_SKIPPED_FIRST_RUN,
        )

        check(
            "artik 'not_run' placeholder'i kullanilmiyor",
            "not_run" not in (
                COMPARISON_COMPLETED,
                COMPARISON_NO_MATCH,
                COMPARISON_FAILED,
                COMPARISON_SKIPPED_FIRST_RUN,
                COMPARISON_SKIPPED_NO_CHANGE,
                COMPARISON_SKIPPED_IRRELEVANT,
            ),
        )

        from app.schemas.monitor import MonitorRunResponse

        run_fields = MonitorRunResponse.model_fields

        check(
            "calistirma yaniti karsilastirma durumu alani "
            "tasiyor",
            "regulation_comparison_status" in run_fields,
        )

        check(
            "calistirma yaniti takip planini tasiyor",
            "tracking_plan" in run_fields,
        )

        check(
            "calistirma yaniti karsilastirma SONUCU alani "
            "TASIMIYOR",
            not any(
                field in run_fields
                for field in (
                    "affected_article",
                    "old_text",
                    "new_text",
                    "change_summary",
                    "source_evidence",
                    "confidence",
                    "regulation_title",
                )
            ),
            f"alanlar={sorted(run_fields.keys())}",
        )

        # -------------------------------------------------
        # 19-21. KAPSAM REGRESYONU
        # -------------------------------------------------

        print("\n--- 19-21. kapsam regresyonu ---")

        full_page = client.post(
            "/api/monitors",
            json=payload(
                name="Full Page Regresyon",
                scope_type="full_page",
            ),
        )

        check(
            "19. full_page monitor hala olusturulabiliyor",
            full_page.status_code == 201
            and full_page.json()["scope_type"] == "full_page",
            f"status={full_page.status_code}",
        )

        auto_section = client.post(
            "/api/monitors",
            json=payload(
                name="Auto Section Regresyon",
                scope_type="auto_section",
            ),
        )

        check(
            "20. auto_section monitor hala olusturulabiliyor",
            auto_section.status_code == 201
            and auto_section.json()["scope_type"]
            == "auto_section",
            f"status={auto_section.status_code}",
        )

        check(
            "20. auto_section secimi bos basliyor "
            "(pipeline dolduracak)",
            auto_section.json()["auto_section_label"] is None,
        )

        manual_missing = client.post(
            "/api/monitors",
            json=payload(
                name="Manual Section Eksik",
                scope_type="manual_section",
            ),
        )

        check(
            "21. manual_section secimsiz hala reddediliyor",
            manual_missing.status_code == 422,
            f"status={manual_missing.status_code}",
        )

        manual_ok = client.post(
            "/api/monitors",
            json=payload(
                name="Manual Section Regresyon",
                scope_type="manual_section",
                manual_section_reference="main>div:nth-of-type(2)",
                manual_section_label="Duyurular",
            ),
        )

        check(
            "21. manual_section secimle kabul ediliyor",
            manual_ok.status_code == 201
            and manual_ok.json()["manual_section_label"]
            == "Duyurular",
            f"status={manual_ok.status_code}",
        )

        check(
            "21. kapsam regresyon kayitlari website_monitor",
            all(
                response.json()["tracking_plan"]
                == TRACKING_PLAN_WEBSITE
                for response in (
                    full_page,
                    auto_section,
                    manual_ok,
                )
            ),
        )

        # Mevzuat plani her kapsamla birlikte calisabilmeli.
        regulation_manual = client.post(
            "/api/monitors",
            json=payload(
                name="Mevzuat + Manuel Alan",
                tracking_plan=TRACKING_PLAN_REGULATION,
                reference_regulation_id=reference.id,
                scope_type="manual_section",
                manual_section_reference="main>div",
                manual_section_label="Mevzuat Listesi",
            ),
        )

        check(
            "takip plani ve kapsam BAGIMSIZ calisiyor",
            regulation_manual.status_code == 201
            and regulation_manual.json()["scope_type"]
            == "manual_section"
            and regulation_manual.json()["tracking_plan"]
            == TRACKING_PLAN_REGULATION,
            f"status={regulation_manual.status_code}",
        )

        check(
            "mevzuat planinda da manuel secim ZORUNLU "
            "kalmaya devam ediyor",
            client.post(
                "/api/monitors",
                json=payload(
                    name="Mevzuat + Eksik Manuel",
                    tracking_plan=TRACKING_PLAN_REGULATION,
                    reference_regulation_id=reference.id,
                    scope_type="manual_section",
                ),
            ).status_code == 422,
        )

        # -------------------------------------------------
        # 22 + 24. BILDIRIM / E-MAIL REGRESYONU
        # -------------------------------------------------

        print("\n--- 22/24. bildirim ve e-mail regresyonu ---")

        email_monitor = client.post(
            "/api/monitors",
            json=payload(
                name="Email Regresyon",
                notify_email=True,
                notification_email="test@example.com",
            ),
        )

        check(
            "24. e-mail bildirimi hala kaydedilebiliyor",
            email_monitor.status_code == 201
            and email_monitor.json()["notify_email"] is True
            and email_monitor.json()["notification_email"]
            == "test@example.com",
            f"status={email_monitor.status_code}",
        )

        check(
            "24. e-mail acikken adres hala zorunlu",
            client.post(
                "/api/monitors",
                json=payload(
                    name="Adressiz",
                    notify_email=True,
                    notification_email=None,
                ),
            ).status_code == 422,
        )

        check(
            "24. bozuk e-mail adresi hala reddediliyor",
            client.post(
                "/api/monitors",
                json=payload(
                    name="Bozuk Adres",
                    notify_email=True,
                    notification_email="gecersiz-adres",
                ),
            ).status_code == 422,
        )

        check(
            "22. uygulama ici bildirim bayragi korunuyor",
            client.post(
                "/api/monitors",
                json=payload(
                    name="In App Kapali",
                    notify_in_app=False,
                ),
            ).json()["notify_in_app"] is False,
        )

        check(
            "22. mevzuat planinda bildirim alanlari ayni "
            "sekilde calisiyor",
            client.post(
                "/api/monitors",
                json=payload(
                    name="Mevzuat + Email",
                    tracking_plan=TRACKING_PLAN_REGULATION,
                    reference_regulation_id=reference.id,
                    notify_email=True,
                    notification_email="mevzuat@example.com",
                ),
            ).json()["notification_email"]
            == "mevzuat@example.com",
        )

        # -------------------------------------------------
        # 23. SCHEDULER REGRESYONU
        # -------------------------------------------------

        print("\n--- 23. scheduler regresyonu ---")

        import inspect

        scheduler_source = inspect.getsource(scheduler)

        check(
            "23. scheduler takip planina gore dallanmiyor",
            "tracking_plan" not in scheduler_source,
            "scheduler bu sprintte degistirilmedi",
        )

        check(
            "23. scheduler referans yonetmelik bilmiyor",
            "reference_regulation" not in scheduler_source
            and "regulation_reference" not in scheduler_source,
        )

        check(
            "23. scheduler ortak execute_monitor_run "
            "kullanmaya devam ediyor",
            "execute_monitor_run" in scheduler_source,
        )

        check(
            "23. zamanlama alanlari hala kaydediliyor",
            client.post(
                "/api/monitors",
                json=payload(
                    name="Zamanlama Regresyon",
                    schedule_type="weekly",
                    schedule_time="23:45",
                ),
            ).json()["schedule_time"] == "23:45",
        )

        check(
            "23. gecersiz kontrol saati hala reddediliyor",
            client.post(
                "/api/monitors",
                json=payload(
                    name="Bozuk Saat",
                    schedule_time="25:99",
                ),
            ).status_code == 422,
        )

        # -------------------------------------------------
        # PIPELINE MODULU REGRESYONU
        # -------------------------------------------------

        print("\n--- pipeline modulu regresyonu ---")

        from app.services import db_monitor_service

        pipeline_source = inspect.getsource(db_monitor_service)

        check(
            "pipeline takip planina gore dallanmiyor",
            "tracking_plan" not in pipeline_source,
            "regulation monitor ayni pipeline'i kullaniyor",
        )

        check(
            "pipeline karsilastirma iddiasinda bulunmuyor",
            "comparison" not in pipeline_source.lower(),
        )

        check(
            "pipeline hala uc kapsami uyguluyor",
            "SCOPE_AUTO_SECTION" in pipeline_source
            and "SCOPE_MANUAL_SECTION" in pipeline_source
            and "SCOPE_FULL_PAGE" in pipeline_source,
        )

    finally:
        app.dependency_overrides.clear()

        session.close()
        transaction.rollback()
        connection.close()

        print("\n" + "-" * 70)
        print("Dis transaction geri alindi.")

    # -----------------------------------------------------
    # KALICI KAYIT BIRAKILMADI MI
    # -----------------------------------------------------

    from app.core.database import SessionLocal

    verify = SessionLocal()

    try:
        monitor_count_after = verify.scalar(
            select(func.count()).select_from(Monitor)
        )

        # "%Mevzuat%" KALDIRILDI: bu desen gercek kullanici
        # monitorlerini de yakaliyordu ("Enerji Mevzuati
        # Takibi"). Artik yalnizca bu testin oldugu adresler
        # taranir; ad degil URL kullanilmasi daha guvenlidir.
        leftover_monitors = verify.scalars(
            select(Monitor).where(
                Monitor.url.like("https://example.com/%")
                | Monitor.url.like("https://mevzuat.test.local/%")
            )
        ).all()

        leftover_references = verify.scalars(
            select(RegulationReference).where(
                RegulationReference.source_url.like(
                    "%mevzuat.test.local%"
                )
            )
        ).all()

        check(
            "veritabaninda monitor sayisi degismedi",
            monitor_count_after == monitor_count_before,
            f"once={monitor_count_before} "
            f"sonra={monitor_count_after}",
        )

        check(
            "test monitorleri veritabaninda kalmadi",
            not leftover_monitors,
            f"kalanlar={[m.id for m in leftover_monitors]}",
        )

        check(
            "test referanslari veritabaninda kalmadi",
            not leftover_references,
            f"kalanlar={[r.id for r in leftover_references]}",
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
