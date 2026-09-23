"""
AUTOMATIC REFERENCE MATCHING MIGRATION

Yaptigi is:

  regulation_comparisons tablosuna iki alan ekler:

    reference_match_status  (nullable)
      Otomatik eslestirmenin ne dedigi.
      matched | needs_review | no_match | ambiguous |
      no_base_name | no_references

    suggested_reference_id  (nullable FK)
      needs_review durumunda otomatik eslestirmenin
      onerdigi referans. Karsilastirmada KULLANILMAZ.

Tamami IDEMPOTENT'tir.

MEVCUT KAYITLARI BOZMAZ. Iki alan da nullable oldugu icin
eski karsilastirmalar NULL kalir; bu "otomatik eslestirme
calistirilmadi" anlamina gelir ve arayuzde oyle gosterilir.

ON DELETE SET NULL: onerilen referans silinirse
karsilastirma kaydi KAYBOLMAZ, yalnizca oneri dusulur.

Calistirma:
    python -m tests.migrate_regulation_reference_matching
"""

from sqlalchemy import text

from app.core.database import engine


MIGRATIONS = [
    """
    ALTER TABLE regulation_comparisons
    ADD COLUMN IF NOT EXISTS reference_match_status
    VARCHAR(20)
    """,

    """
    ALTER TABLE regulation_comparisons
    ADD COLUMN IF NOT EXISTS suggested_reference_id INTEGER
    """,

    """
    CREATE INDEX IF NOT EXISTS
        ix_regulation_comparisons_reference_match_status
    ON regulation_comparisons (reference_match_status)
    """,
]


FOREIGN_KEYS = [
    (
        "fk_regulation_comparisons_suggested_reference_id",
        """
        ALTER TABLE regulation_comparisons
        ADD CONSTRAINT
            fk_regulation_comparisons_suggested_reference_id
        FOREIGN KEY (suggested_reference_id)
        REFERENCES regulation_references (id)
        ON DELETE SET NULL
        """,
    ),
]


CONSTRAINT_EXISTS_SQL = """
SELECT 1
FROM pg_constraint
WHERE conname = :name
"""


def main():
    print("=" * 60)
    print("AUTOMATIC REFERENCE MATCHING MIGRATION")
    print("=" * 60)

    added = []

    with engine.begin() as connection:

        for migration in MIGRATIONS:
            connection.execute(
                text(migration)
            )

        for name, statement in FOREIGN_KEYS:

            exists = connection.execute(
                text(CONSTRAINT_EXISTS_SQL),
                {"name": name},
            ).scalar()

            if exists:
                continue

            connection.execute(
                text(statement)
            )

            added.append(name)

    print("reference_match_status : hazır")
    print("suggested_reference_id : hazır")
    print(f"bu çalıştırmada eklenen kısıt : {len(added)}")

    for name in added:
        print(f"  + {name}")

    print("\nMIGRATION BAŞARILI")


if __name__ == "__main__":
    main()
