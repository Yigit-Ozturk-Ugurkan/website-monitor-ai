"""
REGULATION INTELLIGENCE - SPRINT 1 MIGRATION

Yaptigi is:

  1. regulation_references tablosunu olusturur
  2. monitors.tracking_plan kolonunu ekler
  3. monitors.reference_regulation_id kolonunu ekler
  4. foreign key + index tanimlar

Tamami IDEMPOTENT'tir: birden fazla kez calistirilabilir.

Mevcut monitor/snapshot/change/notification kayitlarina
DOKUNMAZ. tracking_plan NOT NULL DEFAULT 'website_monitor'
olarak eklendigi icin mevcut tum monitorler otomatik olarak
website_monitor kabul edilir.

Calistirma:
    python -m tests.migrate_regulation_tracking
"""

from sqlalchemy import text

from app.core.database import engine


MIGRATIONS = [
    # -----------------------------------------------------
    # 1. REFERANS YONETMELIK TABLOSU
    # -----------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS regulation_references (
        id SERIAL PRIMARY KEY,
        title VARCHAR(300) NOT NULL,
        source_url TEXT NOT NULL,
        content TEXT NOT NULL,
        content_hash VARCHAR(64) NOT NULL,
        created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
        updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL
    )
    """,

    """
    CREATE INDEX IF NOT EXISTS ix_regulation_references_id
    ON regulation_references (id)
    """,

    """
    CREATE INDEX IF NOT EXISTS
        ix_regulation_references_content_hash
    ON regulation_references (content_hash)
    """,

    # Ayni kaynaktan ikinci bir referans olusturulmasini
    # engeller. API bu durumu 409 ile bildirir.
    """
    CREATE UNIQUE INDEX IF NOT EXISTS
        uq_regulation_reference_source_url
    ON regulation_references (source_url)
    """,

    # -----------------------------------------------------
    # 2. TAKIP PLANI
    #
    # DEFAULT sayesinde mevcut satirlar website_monitor
    # olarak doldurulur; ek bir UPDATE gerekmez.
    # -----------------------------------------------------
    """
    ALTER TABLE monitors
    ADD COLUMN IF NOT EXISTS tracking_plan VARCHAR(30)
    NOT NULL DEFAULT 'website_monitor'
    """,

    # -----------------------------------------------------
    # 3. REFERANS YONETMELIK BAGLANTISI
    # -----------------------------------------------------
    """
    ALTER TABLE monitors
    ADD COLUMN IF NOT EXISTS reference_regulation_id INTEGER
    """,

    """
    CREATE INDEX IF NOT EXISTS
        ix_monitors_reference_regulation_id
    ON monitors (reference_regulation_id)
    """,
]


# Foreign key icin ADD CONSTRAINT IF NOT EXISTS yoktur.
# Bu yuzden kisit yalnizca YOKSA eklenir.
FOREIGN_KEY_SQL = """
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'fk_monitors_reference_regulation_id'
    ) THEN
        ALTER TABLE monitors
        ADD CONSTRAINT fk_monitors_reference_regulation_id
        FOREIGN KEY (reference_regulation_id)
        REFERENCES regulation_references (id)
        ON DELETE RESTRICT;
    END IF;
END
$$;
"""


# Eski kayitlarda tracking_plan bos/NULL kalmis olma
# ihtimaline karsi son emniyet. Normalde DEFAULT bunu
# zaten hallediyor; bu UPDATE bos kume uzerinde calisir.
BACKFILL_SQL = """
UPDATE monitors
SET tracking_plan = 'website_monitor'
WHERE tracking_plan IS NULL
   OR tracking_plan = ''
"""


def main():
    print("=" * 60)
    print("REGULATION TRACKING MIGRATION")
    print("=" * 60)

    with engine.begin() as connection:
        for migration in MIGRATIONS:
            connection.execute(
                text(migration)
            )

        connection.execute(
            text(FOREIGN_KEY_SQL)
        )

        result = connection.execute(
            text(BACKFILL_SQL)
        )

        backfilled = result.rowcount

    print("regulation_references       : hazır")
    print("monitors.tracking_plan      : hazır")
    print("monitors.reference_regulation_id : hazır")
    print("foreign key                 : hazır")
    print(f"backfill edilen satır       : {backfilled}")

    print("\nMIGRATION BAŞARILI")


if __name__ == "__main__":
    main()
