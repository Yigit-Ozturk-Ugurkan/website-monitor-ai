"""
MANUEL BETIK - GERCEK DB VE GERCEK AG KULLANIR

SINIF: manual tool

Bu dosya OTOMATIK TEST DEGILDIR. Veritabaninda
MONITOR_ID=1 kaydinin VAR OLMASINI bekler, o monitorun
gercek URL'ini indirir ve snapshot/change/AIAnalysis
kayitlarini KALICI olarak yazar.

Ayni pipeline AUTOMATED olarak sahte fetch + sahte Gemini
ile ve rollback edilen bir transaction icinde dogrulanir:
    tests/test_end_to_end_regulation.py
    tests/test_auto_section.py

  GERCEK GEMINI : EVET (degisiklik varsa)
  GERCEK SMTP   : hayir
  GERCEK AG     : EVET
  DB YAZAR      : EVET (KALICI)

run_automated_suite.py bu dosyayi TOPLAMAZ.

Calistirma:
    python -m tests.manual_database_monitor
"""

import asyncio

from app.core.database import SessionLocal
from app.repositories.monitor_repository import get_monitor
from app.services.db_monitor_service import run_database_monitor


MONITOR_ID = 1


async def main():

    print("=" * 60)
    print("DATABASE WEBSITE MONITOR TEST")
    print("=" * 60)

    db = SessionLocal()

    try:

        monitor = get_monitor(
            db=db,
            monitor_id=MONITOR_ID,
        )

        if monitor is None:
            print(
                f"\nMonitor bulunamadı. ID: {MONITOR_ID}"
            )
            return

        print(f"Monitor ID : {monitor.id}")
        print(f"Name       : {monitor.name}")
        print(f"URL        : {monitor.url}")
        print(f"Topic      : {monitor.topic}")

        print("\nSite kontrol ediliyor...")

        result = await run_database_monitor(
            db=db,
            monitor=monitor,
        )

        print("\nSONUÇ")
        print("-" * 60)

        print(
            f"Status Code : "
            f"{result.fetch_result.status_code}"
        )

        print(
            f"Title       : "
            f"{result.content.title}"
        )

        print(
            f"Text Length : "
            f"{result.content.text_length}"
        )

        # -------------------------------------------------
        # İLK SNAPSHOT
        # -------------------------------------------------

        if result.first_run:

            print("\nİLK DATABASE SNAPSHOT")
            print("-" * 60)

            print(
                f"Snapshot ID : "
                f"{result.new_snapshot.id}"
            )

            print(
                "İlk içerik PostgreSQL'e kaydedildi."
            )

            print("\nTEST BAŞARILI")
            return

        # -------------------------------------------------
        # DEĞİŞİKLİK YOK
        # -------------------------------------------------

        if not result.changed:

            print("\nDEĞİŞİKLİK KONTROLÜ")
            print("-" * 60)

            print("Changed : False")

            print(
                f"Mevcut Snapshot ID : "
                f"{result.previous_snapshot.id}"
            )

            print(
                "İçerik değişmediği için "
                "yeni snapshot oluşturulmadı."
            )

            print("\nTEST BAŞARILI")
            return

        # -------------------------------------------------
        # DEĞİŞİKLİK VAR
        # -------------------------------------------------

        change = result.change_result
        topic = result.topic_analysis

        print("\nDEĞİŞİKLİK BULUNDU")
        print("-" * 60)

        print("Changed    : True")
        print(
            f"Similarity : "
            f"{change.similarity_ratio}"
        )

        print(
            f"Eski Snapshot ID : "
            f"{result.previous_snapshot.id}"
        )

        print(
            f"Yeni Snapshot ID : "
            f"{result.new_snapshot.id}"
        )

        # -------------------------------------------------
        # SİLİNENLER
        # -------------------------------------------------

        print("\nSİLİNENLER")
        print("-" * 60)

        if change.removed_lines:
            for line in change.removed_lines:
                print(f"- {line}")
        else:
            print("Yok")

        # -------------------------------------------------
        # EKLENENLER
        # -------------------------------------------------

        print("\nEKLENENLER")
        print("-" * 60)

        if change.added_lines:
            for line in change.added_lines:
                print(f"+ {line}")
        else:
            print("Yok")

        # -------------------------------------------------
        # KEYWORD ANALYSIS
        # -------------------------------------------------

        print("\nKEYWORD KONU ANALİZİ")
        print("-" * 60)

        print(
            f"Relevant        : "
            f"{topic.relevant}"
        )

        print(
            f"Relevance Score : "
            f"{topic.relevance_score}"
        )

        print(
            f"Matched         : "
            f"{topic.matched_keywords}"
        )

        # -------------------------------------------------
        # CHANGE DB
        # -------------------------------------------------

        print("\nCHANGE DATABASE KAYDI")
        print("-" * 60)

        print(
            f"Change ID       : "
            f"{result.change_record.id}"
        )

        print(
            f"Old Snapshot ID : "
            f"{result.change_record.old_snapshot_id}"
        )

        print(
            f"New Snapshot ID : "
            f"{result.change_record.new_snapshot_id}"
        )

        # -------------------------------------------------
        # GEMINI
        # -------------------------------------------------

        print("\nGEMINI AI ANALİZİ")
        print("-" * 60)

        if result.ai_error is not None:

            print("AI ANALİZ BAŞARISIZ")

            print(
                f"Hata : "
                f"{result.ai_error}"
            )

        elif result.ai_analysis_result is not None:

            ai = result.ai_analysis_result

            print(
                f"Relevant                 : "
                f"{ai.relevant}"
            )

            print(
                f"Importance               : "
                f"{ai.importance}"
            )

            print(
                f"Category                 : "
                f"{ai.category}"
            )

            print(
                f"Notification Recommended : "
                f"{ai.notification_recommended}"
            )

            print(
                f"Summary                  : "
                f"{ai.summary}"
            )

        else:

            print(
                "AI analiz sonucu bulunamadı."
            )

        # -------------------------------------------------
        # AI DATABASE RECORD
        # -------------------------------------------------

        print("\nAI ANALYSIS DATABASE KAYDI")
        print("-" * 60)

        if result.ai_analysis_record is not None:

            record = result.ai_analysis_record

            print(
                f"AI Analysis ID   : "
                f"{record.id}"
            )

            print(
                f"Change ID        : "
                f"{record.change_id}"
            )

            print(
                f"Model            : "
                f"{record.model_name}"
            )

            print(
                f"Keyword Relevant : "
                f"{record.keyword_relevant}"
            )

            print(
                f"Keyword Score    : "
                f"{record.keyword_score}"
            )

            print(
                f"AI Relevant      : "
                f"{record.relevant}"
            )

            print(
                f"Importance       : "
                f"{record.importance}"
            )

            print(
                f"Category         : "
                f"{record.category}"
            )

            print(
                f"Notify           : "
                f"{record.notification_recommended}"
            )

        else:

            print(
                "AI database kaydı oluşturulmadı."
            )

        print("\nTEST BAŞARILI")

    except Exception as exc:

        print("\nTEST BAŞARISIZ")
        print("-" * 60)

        print(
            f"{type(exc).__name__}: {exc}"
        )

        raise

    finally:
        db.close()


if __name__ == "__main__":
    asyncio.run(main())