"""
MANUEL BETIK - SEMA HAZIRLIK ARACI

SINIF: manual tool

Bu dosya OTOMATIK TEST DEGILDIR. PostgreSQL baglantisini
dogrular ve Base.metadata.create_all() ile eksik tablolari
olusturur. Yani SEMAYI DEGISTIREBILIR.

Yeni bir gelistirme ortami kurarken bir kez calistirilir.

  GERCEK GEMINI : hayir
  GERCEK SMTP   : hayir
  GERCEK AG     : hayir
  DB YAZAR      : EVET (DDL calistirir)

run_automated_suite.py bu dosyayi TOPLAMAZ.

Calistirma:
    python -m tests.manual_database
"""

from sqlalchemy import text

from app.core.database import Base, engine

# Modeller Base metadata'ya kaydolmalı.
from app.models.ai_analysis import AIAnalysis
from app.models.change import Change
from app.models.monitor import Monitor
from app.models.notification import Notification
from app.models.snapshot import Snapshot


def main():
    print("=" * 60)
    print("DATABASE TEST")
    print("=" * 60)

    try:
        with engine.connect() as connection:
            result = connection.execute(
                text("SELECT 1")
            )

            value = result.scalar()

        print(f"PostgreSQL bağlantısı : BAŞARILI")
        print(f"SELECT 1 sonucu       : {value}")

        print("\nTablolar oluşturuluyor...")

        Base.metadata.create_all(
            bind=engine,
        )

        print("monitors      : hazır")
        print("snapshots     : hazır")
        print("changes       : hazır")
        print("ai_analyses   : hazır")
        print("notifications : hazır")
        print("\nTEST BAŞARILI")

    except Exception as exc:
        print("\nTEST BAŞARISIZ")
        print(f"{type(exc).__name__}: {exc}")


if __name__ == "__main__":
    main()