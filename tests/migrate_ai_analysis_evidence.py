"""
ai_analyses tablosuna evidence kolonlarini ekler.

Idempotenttir (ADD COLUMN IF NOT EXISTS), mevcut kayitlari bozmaz.
Kolonlar nullable oldugu icin eski AIAnalysis kayitlari null kalir.

Calistirma:
    python -m tests.migrate_ai_analysis_evidence
"""

from sqlalchemy import inspect, text

from app.core.database import engine


MIGRATIONS = [
    """
    ALTER TABLE ai_analyses
    ADD COLUMN IF NOT EXISTS evidence_text TEXT
    """,

    """
    ALTER TABLE ai_analyses
    ADD COLUMN IF NOT EXISTS evidence_type VARCHAR(20)
    """,
]


def main():
    print("=" * 60)
    print("AI ANALYSIS EVIDENCE MIGRATION")
    print("=" * 60)

    inspector = inspect(engine)

    before = {
        column["name"]
        for column in inspector.get_columns("ai_analyses")
    }

    print(f"Once kolon sayisi : {len(before)}")

    with engine.begin() as connection:
        for migration in MIGRATIONS:
            connection.execute(
                text(migration)
            )

    after_inspector = inspect(engine)

    columns = {
        column["name"]: column
        for column in after_inspector.get_columns("ai_analyses")
    }

    print(f"Sonra kolon sayisi: {len(columns)}")
    print()

    for name in ("evidence_text", "evidence_type"):
        column = columns.get(name)

        if column is None:
            print(f"{name:14}: EKLENEMEDI")
            raise SystemExit(1)

        print(
            f"{name:14}: hazir "
            f"({column['type']}, nullable={column['nullable']})"
        )

        if not column["nullable"]:
            print(f"{name} nullable olmali.")
            raise SystemExit(1)

    with engine.begin() as connection:
        total = connection.execute(
            text("SELECT COUNT(*) FROM ai_analyses")
        ).scalar()

        with_evidence = connection.execute(
            text(
                "SELECT COUNT(*) FROM ai_analyses "
                "WHERE evidence_text IS NOT NULL"
            )
        ).scalar()

    print()
    print(f"Mevcut AI analiz kaydi   : {total}")
    print(f"Evidence iceren kayit    : {with_evidence}")
    print("Eski kayitlar korundu, evidence null birakildi.")

    print("\nMIGRATION BASARILI")


if __name__ == "__main__":
    main()
