"""
monitors tablosuna manual_section kolonlarini ekler.

Idempotenttir (ADD COLUMN IF NOT EXISTS), mevcut kayitlari
bozmaz. Kolonlar nullable oldugu icin eski monitorler
(full_page ve auto_section dahil) null kalir ve
davranislari degismez.

auto_section_reference / auto_section_label alanlari
manual_section icin KULLANILMAZ; iki kapsamin secim
durumu birbirinden bagimsiz tutulur.

Calistirma:
    python -m tests.migrate_manual_section_fields
"""

from sqlalchemy import inspect, text

from app.core.database import engine


MIGRATIONS = [
    """
    ALTER TABLE monitors
    ADD COLUMN IF NOT EXISTS manual_section_reference TEXT
    """,

    """
    ALTER TABLE monitors
    ADD COLUMN IF NOT EXISTS manual_section_label TEXT
    """,
]


NEW_COLUMNS = (
    "manual_section_reference",
    "manual_section_label",
)


# Bu migration bu alanlara DOKUNMAMALIDIR.
AUTO_SECTION_COLUMNS = (
    "auto_section_reference",
    "auto_section_label",
)


def main():
    print("=" * 60)
    print("MANUAL SECTION MIGRATION")
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

        auto_before = connection.execute(
            text(
                "SELECT COUNT(*) FROM monitors "
                "WHERE auto_section_reference IS NOT NULL"
            )
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
            print(f"{name:26}: EKLENEMEDI")
            raise SystemExit(1)

        print(
            f"{name:26}: hazir "
            f"({column['type']}, nullable={column['nullable']})"
        )

        if not column["nullable"]:
            print(f"{name} nullable olmali.")
            raise SystemExit(1)

    for name in AUTO_SECTION_COLUMNS:
        if name not in columns:
            print(f"{name} kayboldu. Migration guvenli degil.")
            raise SystemExit(1)

    with engine.begin() as connection:
        total_after = connection.execute(
            text("SELECT COUNT(*) FROM monitors")
        ).scalar()

        auto_after = connection.execute(
            text(
                "SELECT COUNT(*) FROM monitors "
                "WHERE auto_section_reference IS NOT NULL"
            )
        ).scalar()

        with_manual = connection.execute(
            text(
                "SELECT COUNT(*) FROM monitors "
                "WHERE manual_section_reference IS NOT NULL"
            )
        ).scalar()

    print()
    print(f"Migration oncesi monitor sayisi : {total_before}")
    print(f"Migration sonrasi monitor sayisi: {total_after}")
    print(f"Manuel bolum secimi olan monitor: {with_manual}")
    print(f"Auto bolum secimi olan monitor  : "
          f"{auto_before} -> {auto_after}")

    if total_before != total_after:
        print("Monitor sayisi degisti. Migration guvenli degil.")
        raise SystemExit(1)

    if auto_before != auto_after:
        print("auto_section secimleri etkilendi. Guvenli degil.")
        raise SystemExit(1)

    print("Eski monitorler korundu, yeni alanlar null birakildi.")

    print("\nMIGRATION BASARILI")


if __name__ == "__main__":
    main()
