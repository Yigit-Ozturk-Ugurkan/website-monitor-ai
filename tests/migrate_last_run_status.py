"""
SON CALISTIRMA DURUMU MIGRATION

Yaptigi is:

  monitors tablosuna UC nullable sutun ekler:

    last_run_status   VARCHAR(30)
        Son calistirma denemesinin deterministik sonucu:

          first_run             ilk snapshot alindi
          no_change             kontrol basarili, degisiklik yok
          change_detected       ilgili degisiklik bulundu
          change_irrelevant     degisiklik konuyla ilgisiz
          comparison_completed  mevzuat karsilastirmasi tamam
          comparison_no_match   karsilastirma karsilik bulamadi
          comparison_failed     karsilastirma tamamlanamadi
          reference_not_found   referans mevzuat bulunamadi
          reference_ambiguous   referans icin manuel dogrulama
          discovery_failed      resmi metin alinamadi
          reference_unavailable referans metni dogrulanamadi
          ai_failed             AI analizi alinamadi
          fetch_failed          siteye erisilemedi
          section_failed        izlenecek bolum belirlenemedi
          run_failed            beklenmeyen hata

        NULL = bu surumden once hic calistirilmamis.

    last_run_message  VARCHAR(200)
        Kartta gosterilen kullanici cumlesi.

    last_run_at       TIMESTAMP
        Son DENEMENIN zamani.

NEDEN last_checked_at YETMIYOR

  last_checked_at yalnizca BASARILI kontrollerde
  guncellenir; siteye erisilemedigi calistirmalarda
  DEGISMEZ (scheduler retry davranisi buna baglidir).

  Bu yuzden "son deneme ne oldu" sorusu ayri alanlarda
  tutulur. last_checked_at semantigi DEGISMEDI.

YENI TABLO YOK. Bu bir gecmis/audit sistemi degildir:
yalnizca EN SON denemenin sonucu saklanir.

Tamami IDEMPOTENT'tir.

Calistirma:
    python -m tests.migrate_last_run_status
"""

from sqlalchemy import text

from app.core.database import engine


MIGRATIONS = [
    """
    ALTER TABLE monitors
    ADD COLUMN IF NOT EXISTS last_run_status
    VARCHAR(30)
    """,

    """
    ALTER TABLE monitors
    ADD COLUMN IF NOT EXISTS last_run_message
    VARCHAR(200)
    """,

    """
    ALTER TABLE monitors
    ADD COLUMN IF NOT EXISTS last_run_at
    TIMESTAMP
    """,
]


COLUMN_SQL = """
SELECT data_type, character_maximum_length, is_nullable
FROM information_schema.columns
WHERE table_name = 'monitors'
  AND column_name = :column
"""


EXPECTED = (
    "last_run_status",
    "last_run_message",
    "last_run_at",
)


def main():
    print("=" * 60)
    print("SON CALISTIRMA DURUMU MIGRATION")
    print("=" * 60)

    with engine.begin() as connection:

        for migration in MIGRATIONS:
            connection.execute(
                text(migration)
            )

    missing = []

    with engine.connect() as connection:

        for column in EXPECTED:

            row = connection.execute(
                text(COLUMN_SQL),
                {"column": column},
            ).first()

            if row is None:
                missing.append(column)

                print(f"{column:<18}: YOK")
                continue

            data_type, length, nullable = row

            detail = (
                f"{data_type}({length})"
                if length
                else data_type
            )

            print(
                f"{column:<18}: hazır "
                f"({detail}, nullable={nullable})"
            )

    if missing:
        print("\nMIGRATION BAŞARISIZ: " + ", ".join(missing))
        raise SystemExit(1)

    print("\nMIGRATION BAŞARILI")


if __name__ == "__main__":
    main()
