"""
MANUEL BETIK - MONITOR REPOSITORY DUMANI

SINIF: manual tool  (onceden: stale automated test)

STALE GECMISI

Bu dosya create_monitor() fonksiyonunu artik VAR OLMAYAN
notification_type / notification_target parametreleriyle
cagiriyordu:

    create_monitor(..., notification_type="email",
                   notification_target="test@example.com")

Bildirim tercihi o zamandan beri notify_in_app /
notify_email / notification_email alanlarina tasindi.
notification_type ve notification_target sutunlari Monitor
MODELINDE hala duruyor ama artik create_monitor tarafindan
resolve_notification_type() ile TURETILIYOR; disaridan
verilemiyor.

Sonuc: bu dosya calistirildiginda TypeError firlatiyordu.
Otomatik suite onu hic toplamadigi icin hata gorunmuyordu.

Cagri guncel API contract'ina uyarlandi ve turetilmis
alanlarin dogru doldugu ayrica yazdiriliyor.

Ayrica: eski surum her calistirmada veritabanina KALICI
bir monitor kaydi birakiyordu. Artik her sey tek bir
transaction icinde yapilir ve sonunda GERI ALINIR.

NEDEN OTOMATIK SUITE'TE DEGIL

Assertion icermez; ciktisi gozle okunur. create_monitor()
ve get_active_monitors() davranisi AUTOMATED olarak
zaten dogrulaniyor:
    tests/test_monitor_create_validation.py
    tests/test_monitor_update_api.py
    tests/test_regulation_monitor_api.py

  GERCEK GEMINI : hayir
  GERCEK SMTP   : hayir
  GERCEK AG     : hayir
  DB YAZAR      : hayir (transaction rollback edilir)

run_automated_suite.py bu dosyayi TOPLAMAZ.

Calistirma:
    python -m tests.manual_monitor_repository
"""

from sqlalchemy.orm import Session

from app.core.database import engine
from app.repositories.monitor_repository import (
    create_monitor,
    get_active_monitors,
)


def main():
    print("=" * 60)
    print("MONITOR REPOSITORY MANUEL BETIK")
    print("=" * 60)

    # Kalici kayit birakilmaz: dis transaction sonunda
    # geri alinir.
    connection = engine.connect()
    transaction = connection.begin()

    db = Session(
        bind=connection,
        join_transaction_mode="create_savepoint",
    )

    try:
        monitor = create_monitor(
            db=db,
            name="Local AI News Monitor",
            url="http://localhost:8000",
            topic="Yapay zeka modeli ve API güncellemeleri",
            scope_type="full_page",
            schedule_type="daily",
            schedule_time="09:00",
            notify_in_app=True,
            notify_email=True,
            notification_email="test@example.com",
        )

        print("\nMONITOR OLUŞTURULDU")
        print("-" * 60)

        print(f"ID       : {monitor.id}")
        print(f"Name     : {monitor.name}")
        print(f"URL      : {monitor.url}")
        print(f"Topic    : {monitor.topic}")
        print(f"Plan     : {monitor.tracking_plan}")
        print(f"Scope    : {monitor.scope_type}")
        print(f"Schedule : {monitor.schedule_type}")
        print(f"Time     : {monitor.schedule_time}")
        print(f"Active   : {monitor.is_active}")

        print("\nBİLDİRİM TERCİHİ")
        print("-" * 60)

        print(f"notify_in_app      : {monitor.notify_in_app}")
        print(f"notify_email       : {monitor.notify_email}")
        print(f"notification_email : {monitor.notification_email}")

        # Eski sutunlar artik TURETILIYOR.
        print("\nTÜRETİLEN ESKİ ALANLAR")
        print("-" * 60)

        print(f"notification_type   : {monitor.notification_type}")
        print(f"notification_target : {monitor.notification_target}")

        monitors = get_active_monitors(db)

        print("\nAKTİF MONITOR SAYISI")
        print("-" * 60)
        print(len(monitors))

        print("\nBETİK TAMAMLANDI")

    finally:
        db.close()

        transaction.rollback()
        connection.close()

        print("\nTransaction geri alındı. Kalıcı kayıt yok.")


if __name__ == "__main__":
    main()
