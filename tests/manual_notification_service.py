"""
MANUEL BETIK - GERCEK DB KULLANIR, SABIT ID BEKLER

SINIF: manual tool

Bu dosya OTOMATIK TEST DEGILDIR. MONITOR_ID=1,
CHANGE_ID=2, AI_ANALYSIS_ID=1 kayitlarinin veritabaninda
VAR OLMASINI bekler ve KALICI bir bildirim yazar. Bu
kayitlar yoksa sessizce cikar.

Bildirim uretimi AUTOMATED olarak rollback edilen bir
transaction icinde dogrulanir:
    tests/test_notification_detail_api.py
    tests/test_end_to_end_regulation.py

  GERCEK GEMINI : hayir
  GERCEK SMTP   : hayir
  GERCEK AG     : hayir
  DB YAZAR      : EVET (KALICI)

run_automated_suite.py bu dosyayi TOPLAMAZ.

Calistirma:
    python -m tests.manual_notification_service
"""

from app.core.database import SessionLocal

from app.models.ai_analysis import AIAnalysis
from app.models.change import Change

from app.repositories.monitor_repository import get_monitor
from app.repositories.notification_repository import (
    get_notifications_for_monitor,
)

from app.services.notification_service import (
    create_in_app_notification,
)


MONITOR_ID = 1
CHANGE_ID = 2
AI_ANALYSIS_ID = 1


def main():

    print("=" * 60)
    print("NOTIFICATION SERVICE TEST")
    print("=" * 60)

    db = SessionLocal()

    try:

        monitor = get_monitor(
            db=db,
            monitor_id=MONITOR_ID,
        )

        change = db.get(
            Change,
            CHANGE_ID,
        )

        ai_analysis = db.get(
            AIAnalysis,
            AI_ANALYSIS_ID,
        )

        if monitor is None:
            print("Monitor bulunamadı.")
            return

        if change is None:
            print("Change bulunamadı.")
            return

        if ai_analysis is None:
            print("AI Analysis bulunamadı.")
            return

        print(f"Monitor       : {monitor.name}")
        print(f"Change ID     : {change.id}")
        print(f"AI Analysis   : {ai_analysis.id}")

        print("\nAI SONUCU")
        print("-" * 60)

        print(
            f"Relevant   : "
            f"{ai_analysis.relevant}"
        )

        print(
            f"Importance : "
            f"{ai_analysis.importance}"
        )

        print(
            f"Category   : "
            f"{ai_analysis.category}"
        )

        print(
            f"Notify     : "
            f"{ai_analysis.notification_recommended}"
        )

        print(
            f"Summary    : "
            f"{ai_analysis.summary}"
        )

        notification = create_in_app_notification(
            db=db,
            monitor=monitor,
            change=change,
            ai_analysis=ai_analysis,
        )

        print("\nNOTIFICATION")
        print("-" * 60)

        if notification is None:

            print(
                "Bildirim oluşturulmadı."
            )

        else:

            print(
                f"Notification ID : "
                f"{notification.id}"
            )

            print(
                f"Type            : "
                f"{notification.notification_type}"
            )

            print(
                f"Title           : "
                f"{notification.title}"
            )

            print(
                f"Message         : "
                f"{notification.message}"
            )

            print(
                f"Importance      : "
                f"{notification.importance}"
            )

            print(
                f"Category        : "
                f"{notification.category}"
            )

            print(
                f"Status          : "
                f"{notification.status}"
            )

            print(
                f"Read            : "
                f"{notification.is_read}"
            )

        notifications = get_notifications_for_monitor(
            db=db,
            monitor_id=MONITOR_ID,
        )

        print("\nTOPLAM BİLDİRİM")
        print("-" * 60)

        print(len(notifications))

        print("\nTEST BAŞARILI")

    finally:
        db.close()


if __name__ == "__main__":
    main()