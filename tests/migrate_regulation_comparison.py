"""
REGULATION INTELLIGENCE - SPRINT 2 MIGRATION

Yaptigi is:

  1. regulation_comparisons tablosunu olusturur
  2. regulation_comparison_items tablosunu olusturur
  3. foreign key + index tanimlar
  4. UNIQUE(change_id) kisitini kurar

Tamami IDEMPOTENT'tir: birden fazla kez calistirilabilir.

Mevcut monitor/snapshot/change/ai_analysis/notification/
regulation_reference kayitlarina DOKUNMAZ. Yalnizca yeni
tablo ekler.

Calistirma:
    python -m tests.migrate_regulation_comparison
"""

from sqlalchemy import text

from app.core.database import engine


MIGRATIONS = [
    # -----------------------------------------------------
    # 1. KARSILASTIRMA BASLIGI
    # -----------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS regulation_comparisons (
        id SERIAL PRIMARY KEY,
        monitor_id INTEGER NOT NULL,
        change_id INTEGER NOT NULL,
        ai_analysis_id INTEGER,
        reference_regulation_id INTEGER,
        reference_content_hash VARCHAR(64),
        status VARCHAR(20) NOT NULL,
        overall_summary TEXT,
        importance VARCHAR(20),
        model_name VARCHAR(100),
        error_message TEXT,
        created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL
    )
    """,

    """
    CREATE INDEX IF NOT EXISTS ix_regulation_comparisons_id
    ON regulation_comparisons (id)
    """,

    """
    CREATE INDEX IF NOT EXISTS
        ix_regulation_comparisons_monitor_id
    ON regulation_comparisons (monitor_id)
    """,

    """
    CREATE INDEX IF NOT EXISTS
        ix_regulation_comparisons_change_id
    ON regulation_comparisons (change_id)
    """,

    """
    CREATE INDEX IF NOT EXISTS
        ix_regulation_comparisons_ai_analysis_id
    ON regulation_comparisons (ai_analysis_id)
    """,

    """
    CREATE INDEX IF NOT EXISTS
        ix_regulation_comparisons_reference_regulation_id
    ON regulation_comparisons (reference_regulation_id)
    """,

    """
    CREATE INDEX IF NOT EXISTS
        ix_regulation_comparisons_status
    ON regulation_comparisons (status)
    """,

    # DUPLICATE KORUMASI
    #
    # Ayni degisiklik icin ikinci bir karsilastirma
    # (ve ikinci bir Gemini cagrisi) olusamaz.
    """
    CREATE UNIQUE INDEX IF NOT EXISTS
        uq_regulation_comparison_change
    ON regulation_comparisons (change_id)
    """,

    # -----------------------------------------------------
    # 2. MADDE SATIRLARI
    # -----------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS regulation_comparison_items (
        id SERIAL PRIMARY KEY,
        comparison_id INTEGER NOT NULL,
        affected_article VARCHAR(120),
        affected_paragraph VARCHAR(120),
        change_type VARCHAR(20) NOT NULL,
        old_text TEXT,
        new_text TEXT,
        source_evidence TEXT,
        reference_evidence TEXT,
        change_summary TEXT,
        confidence DOUBLE PRECISION,
        created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL
    )
    """,

    """
    CREATE INDEX IF NOT EXISTS
        ix_regulation_comparison_items_id
    ON regulation_comparison_items (id)
    """,

    """
    CREATE INDEX IF NOT EXISTS
        ix_regulation_comparison_items_comparison_id
    ON regulation_comparison_items (comparison_id)
    """,
]


# Foreign key icin ADD CONSTRAINT IF NOT EXISTS yoktur.
# Her kisit yalnizca YOKSA eklenir.
FOREIGN_KEYS = [
    (
        "fk_regulation_comparisons_monitor_id",
        """
        ALTER TABLE regulation_comparisons
        ADD CONSTRAINT fk_regulation_comparisons_monitor_id
        FOREIGN KEY (monitor_id)
        REFERENCES monitors (id)
        ON DELETE CASCADE
        """,
    ),
    (
        "fk_regulation_comparisons_change_id",
        """
        ALTER TABLE regulation_comparisons
        ADD CONSTRAINT fk_regulation_comparisons_change_id
        FOREIGN KEY (change_id)
        REFERENCES changes (id)
        ON DELETE CASCADE
        """,
    ),
    (
        "fk_regulation_comparisons_ai_analysis_id",
        """
        ALTER TABLE regulation_comparisons
        ADD CONSTRAINT fk_regulation_comparisons_ai_analysis_id
        FOREIGN KEY (ai_analysis_id)
        REFERENCES ai_analyses (id)
        ON DELETE CASCADE
        """,
    ),
    (
        "fk_regulation_comparisons_reference_id",
        """
        ALTER TABLE regulation_comparisons
        ADD CONSTRAINT fk_regulation_comparisons_reference_id
        FOREIGN KEY (reference_regulation_id)
        REFERENCES regulation_references (id)
        ON DELETE RESTRICT
        """,
    ),
    (
        "fk_regulation_comparison_items_comparison_id",
        """
        ALTER TABLE regulation_comparison_items
        ADD CONSTRAINT
            fk_regulation_comparison_items_comparison_id
        FOREIGN KEY (comparison_id)
        REFERENCES regulation_comparisons (id)
        ON DELETE CASCADE
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
    print("REGULATION COMPARISON MIGRATION")
    print("=" * 60)

    added_constraints = []

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

            added_constraints.append(name)

    print("regulation_comparisons        : hazır")
    print("regulation_comparison_items   : hazır")
    print("UNIQUE(change_id)             : hazır")
    print(
        f"bu çalıştırmada eklenen kısıt : "
        f"{len(added_constraints)}"
    )

    for name in added_constraints:
        print(f"  + {name}")

    print("\nMIGRATION BAŞARILI")


if __name__ == "__main__":
    main()
