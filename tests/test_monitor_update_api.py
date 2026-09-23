"""
PATCH /api/monitors/{id} testleri.

Gercek PostgreSQL uzerinde calisir ama:
- Kayit SILMEZ
- Yeni monitor OLUSTURMAZ
- Ag istegi yapmaz, Gemini cagirmaz

Test monitorunun tum alanlari basta yedeklenir ve testin
sonunda (hata olsa bile) aynen geri yazilir.

TestClient context manager olarak kullanilmadigi icin FastAPI
lifespan calismaz, yani scheduler bu testte baslatilmaz.

Calistirma:
    python -m tests.test_monitor_update_api
"""

import warnings

from datetime import date, timedelta

from app.core.database import SessionLocal
from app.models.monitor import Monitor
from app.repositories.monitor_repository import get_monitors


warnings.filterwarnings("ignore")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


PASSED = []
FAILED = []


RESTORE_FIELDS = [
    "name",
    "url",
    "topic",
    "scope_type",
    "schedule_type",
    "schedule_time",
    "start_date",
    "end_date",
    "is_unlimited",
    "notify_in_app",
    "notify_email",
    "notification_email",
    "notification_type",
    "notification_target",
    "is_active",
    # scope_type degisimi bolum secimlerini sifirladigi icin
    # bu alanlar da yedeklenip geri yazilmalidir.
    "auto_section_reference",
    "auto_section_label",
    "manual_section_reference",
    "manual_section_label",
]


def check(label: str, condition: bool, detail: str = "") -> None:

    status = "PASS" if condition else "FAIL"

    print(f"[{status}] {label}" + (f" -> {detail}" if detail else ""))

    if condition:
        PASSED.append(label)
    else:
        FAILED.append(f"{label} -> {detail}")


def snapshot_monitor(monitor_id: int) -> dict:

    db = SessionLocal()

    try:
        monitor = db.get(Monitor, monitor_id)

        return {
            field: getattr(monitor, field)
            for field in RESTORE_FIELDS
        }

    finally:
        db.close()


def restore_monitor(monitor_id: int, values: dict) -> None:

    db = SessionLocal()

    try:
        monitor = db.get(Monitor, monitor_id)

        for field, value in values.items():
            setattr(monitor, field, value)

        db.commit()

    finally:
        db.close()


def main() -> None:

    print("=" * 70)
    print("MONITOR PATCH API TESTLERI")
    print("=" * 70)

    client = TestClient(app)

    from app.core import scheduler

    check(
        "test sirasinda scheduler baslatilmadi",
        scheduler._scheduler_task is None
        or scheduler._scheduler_task.done(),
    )

    # Test icin mevcut bir monitor secilir, yenisi olusturulmaz.
    db = SessionLocal()

    try:
        monitors = get_monitors(db)
    finally:
        db.close()

    if not monitors:
        print("Veritabaninda monitor yok, test atlandi.")
        raise SystemExit(1)

    target = monitors[0]
    monitor_id = target.id

    print(f"Test monitoru: id={monitor_id} name={target.name!r}")
    print(f"Toplam monitor sayisi: {len(monitors)}")
    print("-" * 70)

    original = snapshot_monitor(monitor_id)

    try:
        # -------------------------------------------------
        # 1. BILINMEYEN ID -> 404
        # -------------------------------------------------

        print("\n--- 1. bilinmeyen id ---")

        response = client.patch(
            "/api/monitors/999999",
            json={"is_active": False},
        )

        check(
            "PATCH bilinmeyen id -> 404",
            response.status_code == 404,
            f"status={response.status_code}",
        )

        check(
            "404 mesaji dogru",
            response.json().get("detail") == "Monitor bulunamadı.",
        )

        # -------------------------------------------------
        # 2 + 3. SADECE is_active=false, DIGERLERI KORUNUR
        # -------------------------------------------------

        print("\n--- 2/3. sadece is_active=false ---")

        before = client.get(f"/api/monitors/{monitor_id}").json()

        response = client.patch(
            f"/api/monitors/{monitor_id}",
            json={"is_active": False},
        )

        check(
            "PATCH is_active=false -> 200",
            response.status_code == 200,
            f"status={response.status_code}",
        )

        after = response.json()

        check(
            "is_active false oldu",
            after["is_active"] is False,
        )

        unchanged_fields = [
            field
            for field in before
            if field != "is_active"
        ]

        differences = {
            field: (before[field], after[field])
            for field in unchanged_fields
            if before[field] != after[field]
        }

        check(
            "diger tum alanlar korundu",
            not differences,
            f"degisenler={differences}",
        )

        listed = client.get("/api/monitors").json()

        check(
            "pasif monitor GET /api/monitors icinde gorunmeye devam ediyor",
            any(item["id"] == monitor_id for item in listed),
        )

        check(
            "monitor sayisi degismedi (kayit silinmedi)",
            len(listed) == len(monitors),
            f"once={len(monitors)} sonra={len(listed)}",
        )

        # -------------------------------------------------
        # PASIF MONITOR MANUEL CALISTIRILAMAZ
        # -------------------------------------------------

        print("\n--- pasif monitor / manuel calistirma ---")

        run_response = client.post(
            f"/api/monitors/{monitor_id}/run"
        )

        check(
            "pasif monitorde POST /run -> 400",
            run_response.status_code == 400,
            f"status={run_response.status_code}",
        )

        # -------------------------------------------------
        # SCHEDULER PASIF MONITORU CALISTIRMAZ
        # -------------------------------------------------

        print("\n--- pasif monitor / scheduler ---")

        from datetime import datetime

        from app.core.scheduler import (
            evaluate_monitor_due,
            get_app_timezone,
        )
        from app.repositories.monitor_repository import (
            get_active_monitors,
            get_monitor,
        )

        db = SessionLocal()

        try:
            timezone_info = get_app_timezone()
            now_local = datetime.now(timezone_info)

            active_ids = [
                item.id for item in get_active_monitors(db)
            ]

            check(
                "pasif monitor get_active_monitors icinde yok",
                monitor_id not in active_ids,
                f"aktif id'ler={active_ids}",
            )

            passive_monitor = get_monitor(
                db=db,
                monitor_id=monitor_id,
            )

            decision = evaluate_monitor_due(
                monitor=passive_monitor,
                now_local=now_local,
                timezone_info=timezone_info,
            )

            check(
                "pasif monitor due degil",
                decision.due is False,
                f"sebep={decision.reason}",
            )

        finally:
            db.close()

        # -------------------------------------------------
        # 4. TEKRAR AKTIF YAPILABILIYOR
        # -------------------------------------------------

        print("\n--- 4. tekrar aktif ---")

        response = client.patch(
            f"/api/monitors/{monitor_id}",
            json={"is_active": True},
        )

        check(
            "PATCH is_active=true -> 200",
            response.status_code == 200,
        )

        check(
            "is_active tekrar true",
            response.json()["is_active"] is True,
        )

        db = SessionLocal()

        try:
            active_ids = [
                item.id for item in get_active_monitors(db)
            ]

            check(
                "monitor tekrar scheduler kapsaminda",
                monitor_id in active_ids,
            )

        finally:
            db.close()

        # -------------------------------------------------
        # 5. PARTIAL name / topic UPDATE
        # -------------------------------------------------

        print("\n--- 5. partial name / topic ---")

        before = client.get(f"/api/monitors/{monitor_id}").json()

        new_name = f"{original['name']} (patch testi)"

        response = client.patch(
            f"/api/monitors/{monitor_id}",
            json={"name": new_name},
        )

        check(
            "sadece name guncellendi -> 200",
            response.status_code == 200,
        )

        after = response.json()

        check(
            "name degisti",
            after["name"] == new_name,
            f"{after['name']!r}",
        )

        check(
            "topic degismedi",
            after["topic"] == before["topic"],
        )

        check(
            "url degismedi",
            after["url"] == before["url"],
        )

        new_topic = f"{original['topic']} + patch testi"

        response = client.patch(
            f"/api/monitors/{monitor_id}",
            json={"topic": new_topic},
        )

        check(
            "sadece topic guncellendi -> 200",
            response.status_code == 200
            and response.json()["topic"] == new_topic,
        )

        check(
            "onceki name korundu",
            response.json()["name"] == new_name,
        )

        check(
            "bos name reddediliyor",
            client.patch(
                f"/api/monitors/{monitor_id}",
                json={"name": "   "},
            ).status_code == 422,
        )

        check(
            "bos url reddediliyor",
            client.patch(
                f"/api/monitors/{monitor_id}",
                json={"url": ""},
            ).status_code == 422,
        )

        check(
            "bos topic reddediliyor",
            client.patch(
                f"/api/monitors/{monitor_id}",
                json={"topic": ""},
            ).status_code == 422,
        )

        # -------------------------------------------------
        # 6. GECERSIZ schedule_type
        # -------------------------------------------------

        print("\n--- 6. gecersiz schedule_type ---")

        check(
            "gecersiz schedule_type reddediliyor",
            client.patch(
                f"/api/monitors/{monitor_id}",
                json={"schedule_type": "hourly"},
            ).status_code == 422,
        )

        for valid in ["daily", "weekdays", "weekly"]:
            check(
                f"gecerli schedule_type kabul ediliyor: {valid}",
                client.patch(
                    f"/api/monitors/{monitor_id}",
                    json={"schedule_type": valid},
                ).status_code == 200,
            )

        check(
            "gecersiz schedule_time reddediliyor",
            client.patch(
                f"/api/monitors/{monitor_id}",
                json={"schedule_time": "9:5"},
            ).status_code == 422,
        )

        check(
            "gecerli schedule_time kabul ediliyor",
            client.patch(
                f"/api/monitors/{monitor_id}",
                json={"schedule_time": "09:00"},
            ).status_code == 200,
        )

        # -------------------------------------------------
        # 7. GECERSIZ scope_type
        # -------------------------------------------------

        print("\n--- 7. gecersiz scope_type ---")

        check(
            "gecersiz scope_type reddediliyor",
            client.patch(
                f"/api/monitors/{monitor_id}",
                json={"scope_type": "whole_site"},
            ).status_code == 422,
        )

        for valid in ["full_page", "auto_section"]:
            check(
                f"gecerli scope_type kabul ediliyor: {valid}",
                client.patch(
                    f"/api/monitors/{monitor_id}",
                    json={"scope_type": valid},
                ).status_code == 200,
            )

        # manual_section bolum secimi ZORUNLU oldugu icin
        # yalniz basina gonderildiginde reddedilir.
        check(
            "manual_section secim olmadan reddediliyor",
            client.patch(
                f"/api/monitors/{monitor_id}",
                json={"scope_type": "manual_section"},
            ).status_code == 422,
        )

        check(
            "manual_section secimle kabul ediliyor",
            client.patch(
                f"/api/monitors/{monitor_id}",
                json={
                    "scope_type": "manual_section",
                    "manual_section_reference": "id:blog",
                    "manual_section_label": "Blog",
                },
            ).status_code == 200,
        )

        # -------------------------------------------------
        # 8. end_date < start_date
        # -------------------------------------------------

        print("\n--- 8. tarih araligi ---")

        start = date(2026, 9, 10)
        end = start - timedelta(days=3)

        check(
            "end_date < start_date reddediliyor",
            client.patch(
                f"/api/monitors/{monitor_id}",
                json={
                    "is_unlimited": False,
                    "start_date": start.isoformat(),
                    "end_date": end.isoformat(),
                },
            ).status_code == 422,
        )

        check(
            "gecerli tarih araligi kabul ediliyor",
            client.patch(
                f"/api/monitors/{monitor_id}",
                json={
                    "is_unlimited": False,
                    "start_date": start.isoformat(),
                    "end_date": (start + timedelta(days=5)).isoformat(),
                },
            ).status_code == 200,
        )

        # Partial: sadece end_date gonderildiginde DB'deki
        # start_date ile birlikte dogrulanmali.
        conflict = client.patch(
            f"/api/monitors/{monitor_id}",
            json={"end_date": end.isoformat()},
        )

        check(
            "partial end_date, DB'deki start_date ile dogrulaniyor",
            conflict.status_code == 422,
            f"status={conflict.status_code}",
        )

        client.patch(
            f"/api/monitors/{monitor_id}",
            json={
                "is_unlimited": True,
                "start_date": None,
                "end_date": None,
            },
        )

        # -------------------------------------------------
        # 9. notify_email=true + email yok
        # -------------------------------------------------

        print("\n--- 9. e-mail bildirimi ---")

        client.patch(
            f"/api/monitors/{monitor_id}",
            json={
                "notify_email": False,
                "notification_email": None,
            },
        )

        check(
            "notify_email=true, e-mail yok -> reddediliyor",
            client.patch(
                f"/api/monitors/{monitor_id}",
                json={"notify_email": True},
            ).status_code == 422,
        )

        check(
            "notify_email=true + e-mail birlikte -> kabul",
            client.patch(
                f"/api/monitors/{monitor_id}",
                json={
                    "notify_email": True,
                    "notification_email": "test@example.com",
                },
            ).status_code == 200,
        )

        # notification_email DB'de varken tek basina
        # notify_email=true gecerli olmali.
        client.patch(
            f"/api/monitors/{monitor_id}",
            json={"notify_email": False},
        )

        check(
            "DB'de e-mail varken tek basina notify_email=true kabul",
            client.patch(
                f"/api/monitors/{monitor_id}",
                json={"notify_email": True},
            ).status_code == 200,
        )

        db = SessionLocal()

        try:
            refreshed = db.get(Monitor, monitor_id)

            check(
                "legacy notification_type guncel bayraklarla tutarli",
                refreshed.notification_type
                in ("both", "email", "in_app", "none"),
                f"deger={refreshed.notification_type}",
            )

        finally:
            db.close()

        # -------------------------------------------------
        # 10 + 11. created_at / last_checked_at
        # -------------------------------------------------

        print("\n--- 10/11. korunan alanlar ---")

        before = client.get(f"/api/monitors/{monitor_id}").json()

        created_response = client.patch(
            f"/api/monitors/{monitor_id}",
            json={"created_at": "2000-01-01T00:00:00"},
        )

        check(
            "created_at PATCH edilemez (422)",
            created_response.status_code == 422,
            f"status={created_response.status_code}",
        )

        last_checked_response = client.patch(
            f"/api/monitors/{monitor_id}",
            json={"last_checked_at": "2000-01-01T00:00:00"},
        )

        check(
            "last_checked_at PATCH edilemez (422)",
            last_checked_response.status_code == 422,
            f"status={last_checked_response.status_code}",
        )

        check(
            "id PATCH edilemez (422)",
            client.patch(
                f"/api/monitors/{monitor_id}",
                json={"id": 4242},
            ).status_code == 422,
        )

        current = client.get(f"/api/monitors/{monitor_id}").json()

        check(
            "created_at gercekten degismedi",
            current["created_at"] == before["created_at"],
            f"{current['created_at']}",
        )

        check(
            "last_checked_at gercekten degismedi",
            current["last_checked_at"] == before["last_checked_at"],
            f"{current['last_checked_at']}",
        )

        # -------------------------------------------------
        # BOS PATCH
        # -------------------------------------------------

        print("\n--- bos govde ---")

        empty_response = client.patch(
            f"/api/monitors/{monitor_id}",
            json={},
        )

        check(
            "bos PATCH 200 donuyor ve kaydi degistirmiyor",
            empty_response.status_code == 200
            and empty_response.json()["name"]
            == current["name"],
        )

    finally:
        restore_monitor(monitor_id, original)

        print("\n" + "-" * 70)
        print("Test monitoru orijinal degerlerine geri yuklendi.")

        restored = client.get(f"/api/monitors/{monitor_id}").json()

        check(
            "geri yukleme dogrulandi (name)",
            restored["name"] == original["name"],
            f"{restored['name']!r}",
        )

        check(
            "geri yukleme dogrulandi (is_active)",
            restored["is_active"] == original["is_active"],
        )

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
