"""
GET /api/notifications/{id}/detail testleri.

Veritabanini SADECE OKUR. Kayit eklemez, silmez, guncellemez.
Ag istegi yapmaz, Gemini cagirmaz.

Eksik iliski senaryosu, gercek kayitlara dokunmadan repository
fonksiyonu stub'lanarak test edilir.

TestClient context manager olarak kullanilmadigi icin FastAPI
lifespan calismaz, yani scheduler bu testte baslatilmaz.

Calistirma:
    python -m tests.test_notification_detail_api
"""

import warnings

from sqlalchemy import select

from app.api import notification_routes
from app.core.database import SessionLocal
from app.models.ai_analysis import AIAnalysis
from app.models.change import Change
from app.models.monitor import Monitor
from app.models.notification import Notification
from app.models.snapshot import Snapshot
from app.services.change_context_service import (
    build_context_blocks,
    is_locatable,
    locate_changed_lines,
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


def main() -> None:

    print("=" * 70)
    print("BILDIRIM DETAY API TESTLERI")
    print("=" * 70)

    client = TestClient(app)

    from app.core import scheduler

    check(
        "test sirasinda scheduler baslatilmadi",
        scheduler._scheduler_task is None
        or scheduler._scheduler_task.done(),
    )

    db = SessionLocal()

    try:
        notifications = db.scalars(
            select(Notification).order_by(
                Notification.created_at.desc(),
                Notification.id.desc(),
            )
        ).all()

        if not notifications:
            print("Veritabaninda bildirim yok, test atlandi.")
            raise SystemExit(1)

        newest = notifications[0]

        print(f"En yeni bildirim: id={newest.id}")
        print(f"Toplam bildirim : {len(notifications)}")
        print("-" * 70)

        # -------------------------------------------------
        # 1. GECERLI DETAIL -> 200
        # -------------------------------------------------

        print("\n--- 1. gecerli detail ---")

        response = client.get(
            f"/api/notifications/{newest.id}/detail"
        )

        check(
            "gecerli detail -> 200",
            response.status_code == 200,
            f"status={response.status_code}",
        )

        detail = response.json()

        check(
            "notification_id dogru",
            detail["notification_id"] == newest.id,
        )

        check(
            "title DB ile ayni",
            detail["title"] == newest.title,
        )

        check(
            "message DB ile ayni",
            detail["message"] == newest.message,
        )

        check(
            "importance/category yeniden yorumlanmamis",
            detail["importance"] == newest.importance
            and detail["category"] == newest.category,
        )

        # -------------------------------------------------
        # 2. BILINMEYEN ID -> 404
        # -------------------------------------------------

        print("\n--- 2. bilinmeyen id ---")

        missing = client.get(
            "/api/notifications/999999/detail"
        )

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
        # 3. MONITOR BILGISI
        # -------------------------------------------------

        print("\n--- 3. monitor bilgisi ---")

        monitor = db.get(Monitor, newest.monitor_id)

        check(
            "monitor_id dogru",
            detail["monitor_id"] == monitor.id,
        )

        check(
            "monitor_name gercek kayittan geliyor",
            detail["monitor_name"] == monitor.name,
            f"{detail['monitor_name']!r}",
        )

        check(
            "url gercek monitor kaydindan geliyor",
            detail["url"] == monitor.url,
            f"{detail['url']!r}",
        )

        # -------------------------------------------------
        # 4. CHANGE BILGISI
        # -------------------------------------------------

        print("\n--- 4. change bilgisi ---")

        change = db.get(Change, newest.change_id)

        check(
            "change_id dogru",
            detail["change_id"] == change.id,
        )

        check(
            "relevance_score DB ile ayni",
            detail["relevance_score"] == change.relevance_score,
        )

        check(
            "detected_at dolu",
            detail["detected_at"] is not None,
        )

        analysis = db.get(AIAnalysis, newest.ai_analysis_id)

        check(
            "ai_summary gercek AIAnalysis kaydindan geliyor",
            detail["ai_summary"] == analysis.summary,
        )

        # -------------------------------------------------
        # 5. SNAPSHOT BAGLANTILARI
        # -------------------------------------------------

        print("\n--- 5. snapshot baglantilari ---")

        old_snapshot = db.get(Snapshot, change.old_snapshot_id)
        new_snapshot = db.get(Snapshot, change.new_snapshot_id)

        check(
            "old_snapshot_id dogru",
            detail["old_snapshot_id"] == change.old_snapshot_id,
        )

        check(
            "new_snapshot_id dogru",
            detail["new_snapshot_id"] == change.new_snapshot_id,
        )

        check(
            "old_snapshot_content DB ile ayni",
            detail["old_snapshot_content"] == old_snapshot.content,
        )

        check(
            "new_snapshot_content DB ile ayni",
            detail["new_snapshot_content"] == new_snapshot.content,
        )

        check(
            "iki snapshot farkli kayit",
            detail["old_snapshot_id"] != detail["new_snapshot_id"],
        )

        check(
            "snapshot_title yeni snapshot'tan geliyor",
            detail["snapshot_title"] == new_snapshot.title,
        )

        # -------------------------------------------------
        # 6. ADDED CONTENT
        # -------------------------------------------------

        print("\n--- 6. added_content ---")

        check(
            "added_content DB ile ayni",
            detail["added_content"] == change.added_content,
        )

        expected_added = build_context_blocks(
            new_snapshot.content,
            change.added_content,
        )

        check(
            "added_blocks deterministik hesapla ayni",
            len(detail["added_blocks"]) == len(expected_added),
            f"api={len(detail['added_blocks'])} "
            f"beklenen={len(expected_added)}",
        )

        if detail["added_blocks"]:
            block = detail["added_blocks"][0]

            check(
                "added blogunda en az bir degisen satir var",
                any(line["changed"] for line in block["lines"]),
            )

            check(
                "added blogunda context satirlari da var",
                any(not line["changed"] for line in block["lines"]),
            )

            check(
                "blok satirlari yeni snapshot ile birebir eslesiyor",
                all(
                    line["text"]
                    == new_snapshot.content.splitlines()[
                        line["line_number"] - 1
                    ]
                    for line in block["lines"]
                ),
            )

            changed_texts = [
                line["text"]
                for line in block["lines"]
                if line["changed"]
            ]

            added_lines = set(
                (change.added_content or "").splitlines()
            )

            check(
                "highlight edilen satirlarin tamami added_content icinde",
                all(text in added_lines for text in changed_texts),
            )

        # -------------------------------------------------
        # 7. REMOVED CONTENT
        # -------------------------------------------------

        print("\n--- 7. removed_content ---")

        check(
            "removed_content DB ile ayni",
            detail["removed_content"] == change.removed_content,
        )

        expected_removed = build_context_blocks(
            old_snapshot.content,
            change.removed_content,
        )

        check(
            "removed_blocks deterministik hesapla ayni",
            len(detail["removed_blocks"]) == len(expected_removed),
            f"api={len(detail['removed_blocks'])} "
            f"beklenen={len(expected_removed)}",
        )

        if detail["removed_blocks"]:
            removed_texts = [
                line["text"]
                for block in detail["removed_blocks"]
                for line in block["lines"]
                if line["changed"]
            ]

            removed_lines = set(
                (change.removed_content or "").splitlines()
            )

            check(
                "silinenler ESKI snapshot uzerinden gosteriliyor",
                all(
                    text in removed_lines
                    for text in removed_texts
                ),
            )

            old_lines = old_snapshot.content.splitlines()

            check(
                "removed blok satirlari eski snapshot ile eslesiyor",
                all(
                    line["text"] == old_lines[line["line_number"] - 1]
                    for block in detail["removed_blocks"]
                    for line in block["lines"]
                ),
            )

        # -------------------------------------------------
        # EVIDENCE
        # -------------------------------------------------

        print()
        print("--- evidence ---")

        check(
            "detail cevabinda evidence_text alani var",
            "evidence_text" in detail,
        )

        check(
            "detail cevabinda evidence_type alani var",
            "evidence_type" in detail,
        )

        check(
            "evidence DB kaydiyla ayni",
            detail["evidence_text"] == analysis.evidence_text
            and detail["evidence_type"] == analysis.evidence_type,
            f"api={detail['evidence_text']!r} "
            f"db={analysis.evidence_text!r}",
        )

        check(
            "context satirlarinda evidence bayragi var",
            all(
                "evidence" in line
                for block in detail["added_blocks"]
                for line in block["lines"]
            ),
        )

        if analysis.evidence_text is None:
            check(
                "eski kayit (evidence null) sorunsuz aciliyor",
                detail["evidence_text"] is None
                and detail["evidence_type"] is None
                and detail["ai_summary"] is not None,
            )

            check(
                "evidence yokken hicbir satir isaretlenmiyor",
                not any(
                    line["evidence"]
                    for block in (
                        detail["added_blocks"]
                        + detail["removed_blocks"]
                    )
                    for line in block["lines"]
                ),
            )

        # Evidence varsa dogru tarafta isaretlenmeli.
        # Mevcut kayitlarda evidence olmadigi icin bu kontrol
        # gecici olarak DB'ye yazip rollback ederek yapilir.
        from sqlalchemy.orm import Session as SqlSession

        from app.core.database import engine

        connection = engine.connect()
        transaction = connection.begin()

        temp_session = SqlSession(
            bind=connection,
            join_transaction_mode="create_savepoint",
        )

        from app.core.database import get_db

        app.dependency_overrides[get_db] = lambda: temp_session

        try:
            # Evidence, render edilen context bloklarinin ICINDE
            # olan bir satirdan secilir. Bloklar 60 satirda
            # kesildigi icin diff'in son satiri pencerenin
            # disinda kalabiliyor.
            rendered_changed_lines = [
                line["text"].strip()
                for block in detail["added_blocks"]
                for line in block["lines"]
                if line["changed"]
                and len(line["text"].strip()) >= 15
            ]

            if rendered_changed_lines:
                chosen = rendered_changed_lines[-1]

                temp_analysis = temp_session.get(
                    AIAnalysis,
                    analysis.id,
                )

                temp_analysis.evidence_text = chosen
                temp_analysis.evidence_type = "added"

                temp_session.flush()

                with_evidence = client.get(
                    f"/api/notifications/{newest.id}/detail"
                ).json()

                check(
                    "evidence detail cevabinda donuyor",
                    with_evidence["evidence_text"] == chosen
                    and with_evidence["evidence_type"] == "added",
                    f"{with_evidence['evidence_text']!r}",
                )

                marked = [
                    line
                    for block in with_evidence["added_blocks"]
                    for line in block["lines"]
                    if line["evidence"]
                ]

                check(
                    "evidence satiri eklenen bloklarinda isaretleniyor",
                    len(marked) >= 1
                    and all(
                        chosen in line["text"] for line in marked
                    ),
                    f"isaretli={len(marked)}",
                )

                check(
                    "evidence_type=added iken silinen tarafta "
                    "isaret yok",
                    not any(
                        line["evidence"]
                        for block in with_evidence["removed_blocks"]
                        for line in block["lines"]
                    ),
                )

        finally:
            app.dependency_overrides.pop(get_db, None)
            temp_session.close()
            transaction.rollback()
            connection.close()

        after_rollback = client.get(
            f"/api/notifications/{newest.id}/detail"
        ).json()

        check(
            "rollback sonrasi gercek kayit degismedi",
            after_rollback["evidence_text"] == analysis.evidence_text,
            f"{after_rollback['evidence_text']!r}",
        )

        # -------------------------------------------------
        # 8. TUM BILDIRIMLER ACILABILIYOR
        # -------------------------------------------------

        print("\n--- 8. tum bildirimler ---")

        statuses = {}

        for item in notifications:
            statuses[item.id] = client.get(
                f"/api/notifications/{item.id}/detail"
            ).status_code

        check(
            "mevcut tum bildirimler detail'den acilabiliyor",
            all(status == 200 for status in statuses.values()),
            f"{statuses}",
        )

        check(
            "en yeni bildirim mevcut veriden acilabiliyor",
            statuses.get(newest.id) == 200,
        )

        # -------------------------------------------------
        # BUYUK SNAPSHOT KORUMASI
        # -------------------------------------------------

        print("\n--- buyuk snapshot korumasi ---")

        snapshot_line_count = len(
            new_snapshot.content.splitlines()
        )

        rendered_lines = sum(
            len(block["lines"])
            for block in detail["added_blocks"]
        )

        check(
            "context tum snapshot'tan kucuk",
            rendered_lines < snapshot_line_count,
            f"context={rendered_lines} snapshot={snapshot_line_count}",
        )

        check(
            "blok sayisi sinirli",
            len(detail["added_blocks"]) <= 20
            and len(detail["removed_blocks"]) <= 20,
        )

        # -------------------------------------------------
        # DETERMINISTIKLIK
        # -------------------------------------------------

        print("\n--- deterministiklik ---")

        second = client.get(
            f"/api/notifications/{newest.id}/detail"
        ).json()

        check(
            "ayni istek ayni sonucu veriyor",
            second == detail,
        )

        check(
            "ayrac satirlari konumlandirilmiyor",
            not is_locatable("|")
            and not is_locatable("(")
            and is_locatable("16 comments"),
        )

        check(
            "tekrar eden satirlar ayri konumlara baglaniyor",
            locate_changed_lines(
                ["a", "b", "a", "c", "a"],
                ["a", "a"],
            )
            == [0, 2],
        )

        check(
            "snapshot'ta olmayan satir sessizce atlaniyor",
            locate_changed_lines(["a", "b"], ["zzz"]) == [],
        )

    finally:
        db.close()

    # -----------------------------------------------------
    # 9. EKSIK ILISKI - KONTROLLU DAVRANIS
    # -----------------------------------------------------

    print("\n--- 9. eksik iliski ---")

    original_get_change = notification_routes.get_change

    notification_routes.get_change = (
        lambda db, change_id: None
    )

    try:
        response = client.get(
            f"/api/notifications/{newest.id}/detail"
        )

        check(
            "change eksikken crash yok, 200 donuyor",
            response.status_code == 200,
            f"status={response.status_code}",
        )

        partial = response.json()

        check(
            "eksik iliskide bildirim alanlari yine dolu",
            partial["notification_id"] == newest.id
            and partial["monitor_name"],
        )

        check(
            "eksik iliskide change alanlari null",
            partial["added_content"] is None
            and partial["removed_content"] is None
            and partial["old_snapshot_id"] is None
            and partial["new_snapshot_id"] is None
            and partial["ai_summary"] is None,
        )

        check(
            "eksik iliskide bloklar bos",
            partial["added_blocks"] == []
            and partial["removed_blocks"] == [],
        )

    finally:
        notification_routes.get_change = original_get_change

    original_get_snapshot = notification_routes.get_snapshot

    notification_routes.get_snapshot = (
        lambda db, snapshot_id: None
    )

    try:
        response = client.get(
            f"/api/notifications/{newest.id}/detail"
        )

        check(
            "snapshot eksikken crash yok, 200 donuyor",
            response.status_code == 200,
            f"status={response.status_code}",
        )

        partial = response.json()

        check(
            "snapshot eksikken change alanlari yine dolu",
            partial["added_content"] is not None
            or partial["removed_content"] is not None,
        )

        check(
            "snapshot eksikken icerik null ve bloklar bos",
            partial["old_snapshot_content"] is None
            and partial["new_snapshot_content"] is None
            and partial["added_blocks"] == []
            and partial["removed_blocks"] == [],
        )

    finally:
        notification_routes.get_snapshot = original_get_snapshot

    original_get_monitor = notification_routes.get_monitor

    notification_routes.get_monitor = (
        lambda db, monitor_id: None
    )

    try:
        response = client.get(
            f"/api/notifications/{newest.id}/detail"
        )

        check(
            "monitor eksikken anlasilir 404",
            response.status_code == 404
            and "monitör" in str(response.json().get("detail")),
            f"status={response.status_code} "
            f"detail={response.json().get('detail')}",
        )

    finally:
        notification_routes.get_monitor = original_get_monitor

    # -----------------------------------------------------
    # 10. MEVCUT LISTE ENDPOINT'I BOZULMADI
    # -----------------------------------------------------

    print("\n--- 10. liste endpoint regresyonu ---")

    listing = client.get("/api/notifications")

    check(
        "GET /api/notifications hala 200",
        listing.status_code == 200,
    )

    rows = listing.json()

    check(
        "liste kayit sayisi degismedi",
        len(rows) == len(notifications),
        f"api={len(rows)} db={len(notifications)}",
    )

    check(
        "liste hala newest-first",
        [item["id"] for item in rows]
        == [item.id for item in notifications],
    )

    check(
        "liste alanlari degismedi",
        set(rows[0].keys())
        == {
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
        },
        f"{sorted(rows[0].keys())}",
    )

    check(
        "detail sonrasi da restore edildi",
        client.get(
            f"/api/notifications/{newest.id}/detail"
        ).status_code == 200,
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
