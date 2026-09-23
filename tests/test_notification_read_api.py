"""
Bildirim okundu/okunmadi ve okunmamis sayaci testleri.

Gercek PostgreSQL uzerinde calisir ama KALICI DEGISIKLIK BIRAKMAZ.
Tum istekler dis bir transaction'a bagli session uzerinden gider,
testin sonunda (hata olsa bile) rollback edilir.

Ag istegi yapmaz, Gemini cagirmaz.
TestClient context manager olarak kullanilmadigi icin FastAPI
lifespan calismaz, yani scheduler bu testte baslatilmaz.

Calistirma:
    python -m tests.test_notification_read_api
"""

import warnings

from sqlalchemy import func, inspect, select
from sqlalchemy.orm import Session

from app.core.database import engine, get_db
from app.models.notification import Notification
from app.repositories.notification_repository import (
    count_unread_notifications,
)


warnings.filterwarnings("ignore")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


PASSED = []
FAILED = []


# is_read disinda degismemesi gereken alanlar
PRESERVED_FIELDS = [
    "monitor_id",
    "change_id",
    "ai_analysis_id",
    "notification_type",
    "recipient",
    "title",
    "message",
    "importance",
    "category",
    "status",
    "error_message",
    "created_at",
    "sent_at",
]


def check(label: str, condition: bool, detail: str = "") -> None:

    status = "PASS" if condition else "FAIL"

    print(f"[{status}] {label}" + (f" -> {detail}" if detail else ""))

    if condition:
        PASSED.append(label)
    else:
        FAILED.append(f"{label} -> {detail}")


def snapshot_fields(notification) -> dict:
    return {
        field: getattr(notification, field)
        for field in PRESERVED_FIELDS
    }


def main() -> None:

    print("=" * 70)
    print("BILDIRIM OKUNDU / OKUNMAMIS SAYACI TESTLERI")
    print("=" * 70)

    # -----------------------------------------------------
    # 10. MIGRATION YOK
    # -----------------------------------------------------

    print("\n--- 10. sema degismedi ---")

    columns = {
        column["name"]
        for column in inspect(engine).get_columns("notifications")
    }

    check(
        "notifications tablosu 15 kolon (yeni kolon eklenmedi)",
        len(columns) == 15,
        f"kolon={len(columns)}",
    )

    check(
        "is_read kolonu zaten mevcut",
        "is_read" in columns,
    )

    check(
        "beklenen kolon kumesi degismedi",
        columns
        == {
            "id",
            "monitor_id",
            "change_id",
            "ai_analysis_id",
            "notification_type",
            "recipient",
            "title",
            "message",
            "importance",
            "category",
            "status",
            "is_read",
            "error_message",
            "created_at",
            "sent_at",
        },
        f"{sorted(columns)}",
    )

    # -----------------------------------------------------
    # DIS TRANSACTION
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

    try:
        notifications = session.scalars(
            select(Notification).order_by(Notification.id)
        ).all()

        if not notifications:
            print("Veritabaninda bildirim yok, test atlandi.")
            raise SystemExit(1)

        total = len(notifications)

        # Rollback dogrulamasi icin GERCEK ambient deger.
        # Asagidaki hazirlik bu sayiyi degistirebilir, bu
        # yuzden ONCE okunur.
        ambient_unread = count_unread_notifications(session)

        # ---------------------------------------------
        # OKUNMAMIS KAYIT GARANTISI
        #
        # Bu test okunmamis BIR kayit uzerinde calisir.
        # Eskiden boyle bir kaydin veritabaninda HAZIR
        # BULUNMASINA guveniyordu; kullanici uygulamada
        # butun bildirimleri okundu isaretleyince test
        # "atlandi" deyip 1 ile cikiyor ve suite kirmizi
        # oluyordu.
        #
        # Kayit artik test tarafindan hazirlanir. Dis
        # transaction sonunda geri alindigi icin gercek
        # veri DEGISMEZ; testin sonundaki rollback
        # dogrulamasi bunu ayrica kontrol eder.
        # ---------------------------------------------

        if all(item.is_read for item in notifications):

            notifications[0].is_read = False

            session.flush()

            print(
                "Okunmamis kayit yoktu; test kendi "
                "kaydini hazirladi."
            )

        unread_before = count_unread_notifications(session)

        print(f"Toplam bildirim   : {total}")
        print(f"Okunmamis (baslangic): {unread_before}")
        print("-" * 70)

        # -------------------------------------------------
        # 1-3. UNREAD COUNT
        # -------------------------------------------------

        print("\n--- 1-3. okunmamis sayaci ---")

        response = client.get("/api/notifications/unread-count")

        check(
            "GET /api/notifications/unread-count -> 200",
            response.status_code == 200,
            f"status={response.status_code}",
        )

        check(
            "cevap {count: N} biciminde",
            set(response.json().keys()) == {"count"}
            and isinstance(response.json()["count"], int),
            f"{response.json()}",
        )

        check(
            "sayac DB ile ayni",
            response.json()["count"] == unread_before,
            f"api={response.json()['count']} db={unread_before}",
        )

        expected_unread = session.scalar(
            select(func.count())
            .select_from(Notification)
            .where(Notification.is_read.is_(False))
        )

        check(
            "is_read=false kayitlar sayiliyor",
            response.json()["count"] == expected_unread,
            f"{expected_unread}",
        )

        # Bir kaydi okundu yap, sayac dusmeli
        target = next(
            (item for item in notifications if not item.is_read),
            None,
        )

        if target is None:
            print("Okunmamis bildirim yok, test atlandi.")
            raise SystemExit(1)

        preserved_before = snapshot_fields(target)

        read_response = client.patch(
            f"/api/notifications/{target.id}/read"
        )

        after_count = client.get(
            "/api/notifications/unread-count"
        ).json()["count"]

        check(
            "is_read=true olan kayit sayilmiyor",
            after_count == unread_before - 1,
            f"once={unread_before} sonra={after_count}",
        )

        # -------------------------------------------------
        # 5. MARK AS READ
        # -------------------------------------------------

        print("\n--- 5. okundu isaretleme ---")

        check(
            "PATCH .../read -> 200",
            read_response.status_code == 200,
            f"status={read_response.status_code}",
        )

        check(
            "cevap id ve is_read iceriyor",
            read_response.json() == {
                "id": target.id,
                "is_read": True,
            },
            f"{read_response.json()}",
        )

        session.refresh(target)

        check(
            "DB'de is_read=true oldu",
            target.is_read is True,
        )

        listed = client.get("/api/notifications").json()

        check(
            "liste endpoint'i guncel is_read donuyor",
            next(
                item["is_read"]
                for item in listed
                if item["id"] == target.id
            )
            is True,
        )

        # -------------------------------------------------
        # 6. IDEMPOTENT
        # -------------------------------------------------

        print("\n--- 6. idempotentlik ---")

        second = client.patch(
            f"/api/notifications/{target.id}/read"
        )

        check(
            "zaten okunmus kayitta tekrar PATCH -> 200",
            second.status_code == 200,
            f"status={second.status_code}",
        )

        check(
            "ikinci cevap ayni",
            second.json() == read_response.json(),
            f"{second.json()}",
        )

        third_count = client.get(
            "/api/notifications/unread-count"
        ).json()["count"]

        check(
            "tekrar isaretleme sayaci degistirmiyor",
            third_count == after_count,
            f"{after_count} -> {third_count}",
        )

        # -------------------------------------------------
        # 7. DIGER ALANLAR DEGISMIYOR
        # -------------------------------------------------

        print("\n--- 7. diger alanlar korunuyor ---")

        session.refresh(target)

        preserved_after = snapshot_fields(target)

        differences = {
            field: (preserved_before[field], preserved_after[field])
            for field in PRESERVED_FIELDS
            if preserved_before[field] != preserved_after[field]
        }

        check(
            "is_read disinda hicbir alan degismedi",
            not differences,
            f"degisenler={differences}",
        )

        check(
            "kayit silinmedi, toplam sayi ayni",
            session.scalar(
                select(func.count()).select_from(Notification)
            )
            == total,
        )

        # -------------------------------------------------
        # 4. BILINMEYEN ID
        # -------------------------------------------------

        print("\n--- 4. bilinmeyen id ---")

        missing = client.patch("/api/notifications/999999/read")

        check(
            "bilinmeyen bildirim -> 404",
            missing.status_code == 404,
            f"status={missing.status_code}",
        )

        check(
            "404 mesaji anlasilir",
            missing.json().get("detail") == "Bildirim bulunamadı.",
            str(missing.json().get("detail")),
        )

        # -------------------------------------------------
        # 8-9. LISTE VE DETAY is_read
        # -------------------------------------------------

        print("\n--- 8-9. liste / detay is_read ---")

        db_read_map = {
            item.id: item.is_read
            for item in session.scalars(
                select(Notification)
            ).all()
        }

        listed = client.get("/api/notifications").json()

        check(
            "liste tum kayitlarda dogru is_read donuyor",
            all(
                item["is_read"] == db_read_map[item["id"]]
                for item in listed
            ),
            f"{[(i['id'], i['is_read']) for i in listed]}",
        )

        detail = client.get(
            f"/api/notifications/{target.id}/detail"
        ).json()

        check(
            "detay okunmus kayitta is_read=true",
            detail["is_read"] is True,
        )

        unread_item = next(
            (
                item
                for item in session.scalars(
                    select(Notification)
                ).all()
                if not item.is_read
            ),
            None,
        )

        if unread_item is not None:
            unread_detail = client.get(
                f"/api/notifications/{unread_item.id}/detail"
            ).json()

            check(
                "detay okunmamis kayitta is_read=false",
                unread_detail["is_read"] is False,
            )

            check(
                "detay GET'i tek basina okundu yapmiyor",
                client.get(
                    "/api/notifications/unread-count"
                ).json()["count"]
                == third_count,
            )

        # -------------------------------------------------
        # FILTRE
        # -------------------------------------------------

        print("\n--- tur filtresi ---")

        check(
            "notification_type filtresi 200 donuyor",
            client.get(
                "/api/notifications/unread-count",
                params={"notification_type": "in_app"},
            ).status_code
            == 200,
        )

        check(
            "bilinmeyen turde sayac 0",
            client.get(
                "/api/notifications/unread-count",
                params={"notification_type": "sms"},
            ).json()["count"]
            == 0,
        )

        # -------------------------------------------------
        # ROTA CAKISMASI
        # -------------------------------------------------

        print("\n--- rota cakismasi ---")

        check(
            "unread-count, /{id}/detail rotasiyla cakismiyor",
            client.get(
                "/api/notifications/unread-count"
            ).status_code
            == 200
            and client.get(
                f"/api/notifications/{target.id}/detail"
            ).status_code
            == 200,
        )

    finally:
        app.dependency_overrides.pop(get_db, None)

        session.close()
        transaction.rollback()
        connection.close()

        print("\n" + "-" * 70)
        print("Dis transaction geri alindi.")

    # -----------------------------------------------------
    # KALICI DEGISIKLIK YOK
    # -----------------------------------------------------

    from app.core.database import SessionLocal

    verify = SessionLocal()

    try:
        final_unread = count_unread_notifications(verify)

        # Testin hazirladigi okunmamis kayit da geri
        # alinmis olmalidir; bu yuzden karsilastirma
        # GERCEK ambient degerle yapilir.
        check(
            "rollback sonrasi okunmamis sayisi degismedi",
            final_unread == ambient_unread,
            f"once={ambient_unread} sonra={final_unread}",
        )

        check(
            "rollback sonrasi kayit sayisi degismedi",
            verify.scalar(
                select(func.count()).select_from(Notification)
            )
            == total,
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
