"""
Bildirim listesi endpoint testleri.

Veritabanini SADECE OKUR. Kayit eklemez, silmez, guncellemez.
Ag istegi yapmaz, Gemini cagirmaz.

TestClient context manager olarak kullanilmadigi icin FastAPI
lifespan calismaz, yani scheduler bu testte baslatilmaz.

Calistirma:
    python -m tests.test_notification_api
"""

import warnings

from sqlalchemy import select

from app.api import notification_routes
from app.core.database import SessionLocal
from app.models.notification import Notification


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


def main() -> None:

    print("=" * 70)
    print("BILDIRIM API TESTLERI")
    print("=" * 70)

    client = TestClient(app)

    from app.core import scheduler

    check(
        "test sirasinda scheduler baslatilmadi",
        scheduler._scheduler_task is None
        or scheduler._scheduler_task.done(),
    )

    # -----------------------------------------------------
    # 1. ENDPOINT 200
    # -----------------------------------------------------

    print("\n--- endpoint ---")

    response = client.get("/api/notifications")

    check(
        "GET /api/notifications 200 donuyor",
        response.status_code == 200,
        f"status={response.status_code}",
    )

    payload = response.json()

    check(
        "cevap bir liste",
        isinstance(payload, list),
        f"tip={type(payload).__name__}",
    )

    # -----------------------------------------------------
    # 2. GERCEK DB KAYITLARI
    # -----------------------------------------------------

    print("\n--- gercek veritabani karsilastirmasi ---")

    db = SessionLocal()

    try:
        db_rows = db.scalars(
            select(Notification).order_by(
                Notification.created_at.desc(),
                Notification.id.desc(),
            )
        ).all()

        db_ids = [row.id for row in db_rows]
        api_ids = [item["id"] for item in payload]

        check(
            "API kayit sayisi DB ile ayni",
            len(payload) == len(db_rows),
            f"api={len(payload)} db={len(db_rows)}",
        )

        check(
            "API id listesi DB ile ayni",
            api_ids == db_ids,
            f"api={api_ids} db={db_ids}",
        )

        if db_rows and payload:

            first_db = db_rows[0]
            first_api = payload[0]

            check(
                "title DB ile ayni",
                first_api["title"] == first_db.title,
            )

            check(
                "message DB ile ayni",
                first_api["message"] == first_db.message,
            )

            check(
                "importance DB ile ayni (yeniden yorumlanmamis)",
                first_api["importance"] == first_db.importance,
                f"api={first_api['importance']} db={first_db.importance}",
            )

            check(
                "category DB ile ayni (yeniden yorumlanmamis)",
                first_api["category"] == first_db.category,
                f"api={first_api['category']} db={first_db.category}",
            )

            check(
                "is_read DB ile ayni",
                first_api["is_read"] == first_db.is_read,
            )

            # monitor_name join ile geliyor, uydurulmus degil.
            from app.models.monitor import Monitor

            monitor = db.get(Monitor, first_db.monitor_id)

            check(
                "monitor_name gercek monitor kaydindan geliyor",
                first_api["monitor_name"] == monitor.name,
                f"api={first_api['monitor_name']} db={monitor.name}",
            )

        # -------------------------------------------------
        # 3. NEWEST-FIRST SIRALAMA
        # -------------------------------------------------

        print("\n--- siralama ---")

        created_values = [
            item["created_at"] for item in payload
        ]

        check(
            "created_at azalan sirada (newest-first)",
            created_values == sorted(created_values, reverse=True),
            f"{created_values}",
        )

        # -------------------------------------------------
        # 4. SEMA ALANLARI GERCEK KOLONLARDAN
        # -------------------------------------------------

        print("\n--- sema alanlari ---")

        real_columns = {
            column.name
            for column in Notification.__table__.columns
        }

        allowed = real_columns | {"monitor_name"}

        if payload:
            unknown = set(payload[0].keys()) - allowed

            check(
                "cevapta uydurulmus alan yok",
                not unknown,
                f"bilinmeyen={unknown}",
            )

            expected_fields = {
                "id",
                "monitor_id",
                "monitor_name",
                "change_id",
                "ai_analysis_id",
                "notification_type",
                "title",
                "message",
                "importance",
                "category",
                "status",
                "is_read",
                "created_at",
            }

            check(
                "beklenen alanlarin tamami var",
                expected_fields <= set(payload[0].keys()),
                f"eksik={expected_fields - set(payload[0].keys())}",
            )

    finally:
        db.close()

    # -----------------------------------------------------
    # 5. FILTRE VE LIMIT
    # -----------------------------------------------------

    print("\n--- filtre / limit ---")

    filtered = client.get(
        "/api/notifications",
        params={"notification_type": "in_app"},
    )

    check(
        "notification_type filtresi 200 donuyor",
        filtered.status_code == 200,
    )

    check(
        "filtre sonucundaki tum kayitlar in_app",
        all(
            item["notification_type"] == "in_app"
            for item in filtered.json()
        ),
    )

    limited = client.get(
        "/api/notifications",
        params={"limit": 1},
    )

    check(
        "limit=1 en fazla 1 kayit donduruyor",
        limited.status_code == 200
        and len(limited.json()) <= 1,
        f"adet={len(limited.json())}",
    )

    check(
        "gecersiz limit reddediliyor",
        client.get(
            "/api/notifications",
            params={"limit": 0},
        ).status_code == 422,
    )

    # -----------------------------------------------------
    # 6. BOS LISTE
    # -----------------------------------------------------

    print("\n--- bos liste ---")

    original = notification_routes.get_notifications

    # DB'ye dokunmadan bos sonuc senaryosu.
    notification_routes.get_notifications = (
        lambda db, notification_type=None, limit=None: []
    )

    try:
        empty_response = client.get("/api/notifications")

        check(
            "bos listede 200 donuyor",
            empty_response.status_code == 200,
        )

        check(
            "bos listede bos dizi donuyor",
            empty_response.json() == [],
            f"{empty_response.json()}",
        )

    finally:
        notification_routes.get_notifications = original

    restored = client.get("/api/notifications")

    check(
        "stub geri alindi, gercek veri yine donuyor",
        restored.status_code == 200
        and len(restored.json()) == len(payload),
    )

    # -----------------------------------------------------
    # SONUC
    # -----------------------------------------------------

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
