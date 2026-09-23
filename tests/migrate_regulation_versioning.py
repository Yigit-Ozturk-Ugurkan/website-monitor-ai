"""
REGULATION VERSIONING MIGRATION

Yaptigi is:

  1. regulation_references tablosuna surum alanlari ekler
       source_type       (NOT NULL, DEFAULT 'generic')
       is_consolidated   (NOT NULL, DEFAULT false)
       version_date      (nullable)
       last_verified_at  (nullable)

  2. regulation_comparisons tablosuna
       reference_is_consolidated (nullable)
     alanini ekler

Tamami IDEMPOTENT'tir: birden fazla kez calistirilabilir.

MEVCUT KAYITLARI BOZMAZ.

source_type ve is_consolidated NOT NULL + DEFAULT ile
eklendigi icin mevcut referanslar otomatik olarak
'generic' / false olur. Bu DOGRU davranistir: konsolide
olduklari KANITLANMAMIS metinler konsolide gibi
isaretlenmez.

reference_is_consolidated eski karsilastirmalarda NULL
kalir; bu "hangi metne dayandigi bilinmiyor" demektir ve
arayuzde oyle gosterilir.

Calistirma:
    python -m tests.migrate_regulation_versioning
"""

from sqlalchemy import text

from app.core.database import engine


MIGRATIONS = [
    # -----------------------------------------------------
    # 1. REFERANS SURUM ALANLARI
    # -----------------------------------------------------
    """
    ALTER TABLE regulation_references
    ADD COLUMN IF NOT EXISTS source_type VARCHAR(40)
    NOT NULL DEFAULT 'generic'
    """,

    """
    ALTER TABLE regulation_references
    ADD COLUMN IF NOT EXISTS is_consolidated BOOLEAN
    NOT NULL DEFAULT false
    """,

    """
    ALTER TABLE regulation_references
    ADD COLUMN IF NOT EXISTS version_date DATE
    """,

    """
    ALTER TABLE regulation_references
    ADD COLUMN IF NOT EXISTS last_verified_at
    TIMESTAMP WITHOUT TIME ZONE
    """,

    # Liste ekraninda konsolide/tarihsel filtrelemesi
    # yapilabilsin.
    """
    CREATE INDEX IF NOT EXISTS
        ix_regulation_references_source_type
    ON regulation_references (source_type)
    """,

    # -----------------------------------------------------
    # 2. KARSILASTIRMANIN DAYANDIGI METIN TURU
    # -----------------------------------------------------
    """
    ALTER TABLE regulation_comparisons
    ADD COLUMN IF NOT EXISTS reference_is_consolidated BOOLEAN
    """,
]


# Bos/NULL kalmis source_type degerleri icin son emniyet.
# Normalde DEFAULT bunu zaten hallediyor.
BACKFILL_SQL = """
UPDATE regulation_references
SET source_type = 'generic'
WHERE source_type IS NULL
   OR source_type = ''
"""


def main():
    print("=" * 60)
    print("REGULATION VERSIONING MIGRATION")
    print("=" * 60)

    with engine.begin() as connection:

        for migration in MIGRATIONS:
            connection.execute(
                text(migration)
            )

        result = connection.execute(
            text(BACKFILL_SQL)
        )

        backfilled = result.rowcount

    print("regulation_references.source_type       : hazır")
    print("regulation_references.is_consolidated   : hazır")
    print("regulation_references.version_date      : hazır")
    print("regulation_references.last_verified_at  : hazır")
    print("regulation_comparisons.reference_is_consolidated : hazır")
    print(f"backfill edilen satır                   : {backfilled}")

    print("\nMIGRATION BAŞARILI")


if __name__ == "__main__":
    main()
