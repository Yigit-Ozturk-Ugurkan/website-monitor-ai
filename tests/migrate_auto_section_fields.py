"""
monitors tablosuna auto_section kolonlarini ekler.

Idempotenttir (ADD COLUMN IF NOT EXISTS), mevcut kayitlari
bozmaz. Kolonlar nullable oldugu icin eski monitorler
(full_page dahil) null kalir ve davranislari degismez.

Calistirma:
    python -m tests.migrate_auto_section_fields
"""

from sqlalchemy import inspect, text

from app.core.database import engine


MIGRATIONS = [
    """
    ALTER TABLE monitors
    ADD COLUMN IF NOT EXISTS auto_section_reference TEXT
    """,

    """
    ALTER TABLE monitors
    ADD COLUMN IF NOT EXISTS auto_section_label TEXT
    """,
]


NEW_COLUMNS = (
    "auto_section_reference",
    "auto_section_label",
)


def main():
    print("=" * 60)
    print("AUTO SECTION MIGRATION")
    print("=" * 60)

    inspector = inspect(engine)

    before = {
        column["name"]
        for column in inspector.get_columns("monitors")
    }

    print(f"Once kolon sayisi : {len(before)}")

    with engine.begin() as connection:
        total_before = connection.execute(
            text("SELECT COUNT(*) FROM monitors")
        ).scalar()

        for migration in MIGRATIONS:
            connection.execute(
                text(migration)
            )

    columns = {
        column["name"]: column
        for column in inspect(engine).get_columns("monitors")
    }

    print(f"Sonra kolon sayisi: {len(columns)}")
    print()

    for name in NEW_COLUMNS:
        column = columns.get(name)

        if column is None:
            print(f"{name:24}: EKLENEMEDI")
            raise SystemExit(1)

        print(
            f"{name:24}: hazir "
            f"({column['type']}, nullable={column['nullable']})"
        )

        if not column["nullable"]:
            print(f"{name} nullable olmali.")
            raise SystemExit(1)

    with engine.begin() as connection:
        total_after = connection.execute(
            text("SELECT COUNT(*) FROM monitors")
        ).scalar()

        with_reference = connection.execute(
            text(
                "SELECT COUNT(*) FROM monitors "
                "WHERE auto_section_reference IS NOT NULL"
            )
        ).scalar()

    print()
    print(f"Migration oncesi monitor sayisi : {total_before}")
    print(f"Migration sonrasi monitor sayisi: {total_after}")
    print(f"Bolum referansi olan monitor    : {with_reference}")

    if total_before != total_after:
        print("Monitor sayisi degisti. Migration guvenli degil.")
        raise SystemExit(1)

    print("Eski monitorler korundu, yeni alanlar null birakildi.")

    print("\nMIGRATION BASARILI")


if __name__ == "__main__":
    main()
