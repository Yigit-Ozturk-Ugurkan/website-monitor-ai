"""
POST /api/monitors validation testleri.

Gercek PostgreSQL uzerinde calisir ama KALICI KAYIT BIRAKMAZ.

Tum istekler dis bir transaction'a bagli session uzerinden gider.
create_monitor() icindeki db.commit() savepoint'e yazar, testin
sonunda dis transaction rollback edilir. Hata veya cokme durumunda
da rollback calisir.

Ag istegi yapilmaz, Gemini cagrilmaz.
TestClient context manager olarak kullanilmadigi icin FastAPI
lifespan calismaz, yani scheduler bu testte baslatilmaz.

Calistirma:
    python -m tests.test_monitor_create_validation
"""

import warnings

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database import engine, get_db
from app.models.monitor import Monitor


warnings.filterwarnings("ignore")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


PASSED = []
FAILED = []


VALID_PAYLOAD = {
    "name": "Validation Test Monitor",
    "url": "https://example.com/",
    "topic": "Yapay zeka model güncellemeleri",
    "scope_type": "full_page",
    "schedule_type": "daily",
    "schedule_time": "09:00",
    "is_unlimited": True,
    "notify_in_app": True,
    "notify_email": False,
    "notification_email": None,
}


def check(label: str, condition: bool, detail: str = "") -> None:

    status = "PASS" if condition else "FAIL"

    print(f"[{status}] {label}" + (f" -> {detail}" if detail else ""))

    if condition:
        PASSED.append(label)
    else:
        FAILED.append(f"{label} -> {detail}")


def payload(**overrides) -> dict:
    data = dict(VALID_PAYLOAD)
    data.update(overrides)

    return data


def first_error_message(response) -> str:
    detail = response.json().get("detail")

    if isinstance(detail, list) and detail:
        return detail[0].get("msg", "")

    return str(detail)


def main() -> None:

    print("=" * 70)
    print("MONITOR CREATE VALIDATION TESTLERI")
    print("=" * 70)

    # -----------------------------------------------------
    # DIS TRANSACTION
    #
    # Test boyunca yazilan her sey sonunda geri alinir.
    # -----------------------------------------------------

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

    created_ids = []

    try:
        # -------------------------------------------------
        # 1. GECERLI CREATE -> 201
        # -------------------------------------------------

        print("\n--- 1. gecerli create ---")

        response = client.post("/api/monitors", json=payload())

        check(
            "gecerli create -> 201",
            response.status_code == 201,
            f"status={response.status_code}",
        )

        if response.status_code == 201:
            body = response.json()
            created_ids.append(body["id"])

            check(
                "kaydedilen schedule_time dogru",
                body["schedule_time"] == "09:00",
                f"{body['schedule_time']!r}",
            )

            check(
                "kaydedilen scope_type dogru",
                body["scope_type"] == "full_page",
            )

            check(
                "yeni monitor aktif olarak olusuyor",
                body["is_active"] is True,
            )

        # -------------------------------------------------
        # 2. GECERSIZ scope_type
        # -------------------------------------------------

        print("\n--- 2. gecersiz scope_type ---")

        for bad in ["whole_site", "FULL_PAGE", "", "auto"]:
            response = client.post(
                "/api/monitors",
                json=payload(scope_type=bad),
            )

            check(
                f"scope_type={bad!r} -> 422",
                response.status_code == 422,
                f"status={response.status_code}",
            )

        for good in ["full_page", "auto_section"]:
            response = client.post(
                "/api/monitors",
                json=payload(scope_type=good),
            )

            check(
                f"scope_type={good!r} -> 201",
                response.status_code == 201,
                f"status={response.status_code}",
            )

            if response.status_code == 201:
                created_ids.append(response.json()["id"])

        # manual_section bolum secimi ZORUNLU oldugu icin
        # yalniz basina gonderildiginde reddedilir.
        manual_without_selection = client.post(
            "/api/monitors",
            json=payload(scope_type="manual_section"),
        )

        check(
            "scope_type='manual_section' secim olmadan -> 422",
            manual_without_selection.status_code == 422,
            f"status={manual_without_selection.status_code}",
        )

        manual_with_selection = client.post(
            "/api/monitors",
            json=payload(
                scope_type="manual_section",
                manual_section_reference="id:fiyatlandirma",
                manual_section_label="Fiyatlandırma",
            ),
        )

        check(
            "scope_type='manual_section' secimle -> 201",
            manual_with_selection.status_code == 201,
            f"status={manual_with_selection.status_code}",
        )

        if manual_with_selection.status_code == 201:
            created_ids.append(
                manual_with_selection.json()["id"]
            )

            check(
                "manual_section secimi kayitta duruyor",
                manual_with_selection.json()[
                    "manual_section_label"
                ]
                == "Fiyatlandırma",
                str(manual_with_selection.json())[:100],
            )

        # -------------------------------------------------
        # 3. GECERSIZ schedule_type
        # -------------------------------------------------

        print("\n--- 3. gecersiz schedule_type ---")

        for bad in ["hourly", "DAILY", "", "monthly"]:
            response = client.post(
                "/api/monitors",
                json=payload(schedule_type=bad),
            )

            check(
                f"schedule_type={bad!r} -> 422",
                response.status_code == 422,
                f"status={response.status_code}",
            )

        for good in ["daily", "weekdays", "weekly"]:
            response = client.post(
                "/api/monitors",
                json=payload(schedule_type=good),
            )

            check(
                f"schedule_type={good!r} -> 201",
                response.status_code == 201,
                f"status={response.status_code}",
            )

            if response.status_code == 201:
                created_ids.append(response.json()["id"])

        # -------------------------------------------------
        # 4-9. schedule_time
        # -------------------------------------------------

        print("\n--- 4-9. schedule_time ---")

        response = client.post(
            "/api/monitors",
            json=payload(schedule_time=None),
        )

        check(
            "schedule_time=null -> 422",
            response.status_code == 422,
            f"status={response.status_code}",
        )

        check(
            "null icin anlasilir mesaj",
            "Kontrol saati zorunludur" in first_error_message(response),
            first_error_message(response),
        )

        response = client.post(
            "/api/monitors",
            json=payload(schedule_time=""),
        )

        check(
            'schedule_time="" -> 422',
            response.status_code == 422,
            f"status={response.status_code}",
        )

        check(
            "bos string icin anlasilir mesaj",
            "Kontrol saati zorunludur" in first_error_message(response),
            first_error_message(response),
        )

        for bad in [
            "25:00",
            "09:60",
            "9:00",
            "24:00",
            "-1:00",
            "0900",
            "09:00:00",
            "   ",
            "abc",
        ]:
            response = client.post(
                "/api/monitors",
                json=payload(schedule_time=bad),
            )

            check(
                f"schedule_time={bad!r} -> 422",
                response.status_code == 422,
                f"status={response.status_code}",
            )

        for good in ["09:00", "00:00", "23:59", "12:30"]:
            response = client.post(
                "/api/monitors",
                json=payload(schedule_time=good),
            )

            check(
                f"schedule_time={good!r} -> 201",
                response.status_code == 201,
                f"status={response.status_code}",
            )

            if response.status_code == 201:
                created_ids.append(response.json()["id"])

        response = client.post(
            "/api/monitors",
            json={
                key: value
                for key, value in payload().items()
                if key != "schedule_time"
            },
        )

        check(
            "schedule_time hic gonderilmedi -> 422",
            response.status_code == 422,
            f"status={response.status_code}",
        )

        # -------------------------------------------------
        # 10. MEVCUT VALIDATION KORUNUYOR
        # -------------------------------------------------

        print("\n--- 10. mevcut kurallar korunuyor ---")

        response = client.post(
            "/api/monitors",
            json=payload(
                notify_email=True,
                notification_email=None,
            ),
        )

        check(
            "notify_email=true + e-mail yok -> 422",
            response.status_code == 422,
            f"status={response.status_code}",
        )

        check(
            "e-mail mesaji korundu",
            "E-mail bildirimi" in first_error_message(response),
            first_error_message(response),
        )

        response = client.post(
            "/api/monitors",
            json=payload(
                notify_email=True,
                notification_email="test@example.com",
            ),
        )

        check(
            "notify_email=true + e-mail var -> 201",
            response.status_code == 201,
            f"status={response.status_code}",
        )

        if response.status_code == 201:
            created_ids.append(response.json()["id"])

        response = client.post(
            "/api/monitors",
            json=payload(
                is_unlimited=False,
                start_date="2026-09-10",
                end_date="2026-09-05",
            ),
        )

        check(
            "end_date < start_date -> 422",
            response.status_code == 422,
            f"status={response.status_code}",
        )

        check(
            "tarih mesaji korundu",
            "Bitiş tarihi" in first_error_message(response),
            first_error_message(response),
        )

        response = client.post(
            "/api/monitors",
            json=payload(
                is_unlimited=False,
                start_date="2026-09-05",
                end_date="2026-09-10",
            ),
        )

        check(
            "gecerli tarih araligi -> 201",
            response.status_code == 201,
            f"status={response.status_code}",
        )

        if response.status_code == 201:
            created_ids.append(response.json()["id"])

        for field in ["name", "url", "topic"]:
            response = client.post(
                "/api/monitors",
                json=payload(**{field: "   "}),
            )

            check(
                f"bos {field} -> 422",
                response.status_code == 422,
                f"status={response.status_code}",
            )

        # -------------------------------------------------
        # CREATE / UPDATE TUTARLILIGI
        # -------------------------------------------------

        print("\n--- create / update tutarliligi ---")

        if created_ids:
            existing_id = created_ids[0]

            consistency_cases = [
                ("scope_type", "whole_site"),
                ("schedule_type", "hourly"),
                ("schedule_time", "25:00"),
                ("schedule_time", "9:00"),
                ("schedule_time", ""),
                ("schedule_time", None),
                ("name", "  "),
            ]

            for field, bad_value in consistency_cases:
                post_status = client.post(
                    "/api/monitors",
                    json=payload(**{field: bad_value}),
                ).status_code

                patch_status = client.patch(
                    f"/api/monitors/{existing_id}",
                    json={field: bad_value},
                ).status_code

                check(
                    f"POST ve PATCH ayni sonucu veriyor: "
                    f"{field}={bad_value!r}",
                    post_status == patch_status == 422,
                    f"POST={post_status} PATCH={patch_status}",
                )

            check(
                "PATCH schedule_time gonderilmezse etkilenmiyor",
                client.patch(
                    f"/api/monitors/{existing_id}",
                    json={"name": "Partial guncelleme"},
                ).status_code == 200,
            )

        # -------------------------------------------------
        # SCHEDULER SILENT FAILURE ARTIK MUMKUN DEGIL
        # -------------------------------------------------

        print("\n--- silent failure kontrolu ---")

        from app.core.scheduler import parse_schedule_time

        session.flush()

        created_monitors = list(
            session.scalars(
                select(Monitor).where(
                    Monitor.id.in_(created_ids)
                )
            ).all()
        ) if created_ids else []

        check(
            "olusturulan tum monitorlerin saati scheduler tarafindan "
            "parse edilebiliyor",
            all(
                parse_schedule_time(item.schedule_time) is not None
                for item in created_monitors
            ),
            f"kontrol edilen={len(created_monitors)}",
        )

        check(
            "olusturulan tum monitorlerin schedule_type destekleniyor",
            all(
                item.schedule_type in ("daily", "weekdays", "weekly")
                for item in created_monitors
            ),
        )

    finally:
        app.dependency_overrides.pop(get_db, None)

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

        leftovers = verify.scalars(
            select(Monitor).where(
                Monitor.name.like("%Validation Test Monitor%")
            )
        ).all()

        check(
            "veritabaninda monitor sayisi degismedi",
            monitor_count_after == monitor_count_before,
            f"once={monitor_count_before} sonra={monitor_count_after}",
        )

        check(
            "test monitoru veritabaninda kalmadi",
            not leftovers,
            f"kalanlar={[m.id for m in leftovers]}",
        )

        print(f"Olusturulan gecici kayit sayisi: {len(created_ids)}")

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
