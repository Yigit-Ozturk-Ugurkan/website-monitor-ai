"""
AUTOMATIC REFERENCE DISCOVERY MIGRATION

Yaptigi is:

  1. regulation_comparisons.reference_discovery_status
     (nullable, VARCHAR(20))

     Kayitli kutuphanede referans bulunamadiginda resmi
     kaynakta (mevzuat.gov.tr) yapilan aramanin sonucu:

       not_needed        yerel eslestirme zaten referans
                         verdi, ya da kesfin calismasi
                         dogru degildi
       not_applicable    manuel mod
       found_existing    resmi kayit bulundu, kutuphanede
                         zaten vardi
       discovered        resmi kayit bulundu ve YENI
                         referans olusturuldu
       no_match          guvenilir resmi aday yok
       ambiguous         birden fazla resmi aday ayirt
                         edilemedi
       discovery_failed  arama veya konsolide metin alma
                         teknik olarak basarisiz

     NULL = kesif hic calistirilmadi (eski kayitlar).

  2. regulation_comparisons.reference_match_method
     VARCHAR(20) -> VARCHAR(40)

     Kesifle bulunan referanslarin yontemi mevcut yerel
     eslestirme yontemlerinden AYRI adlandirilir:

       automatic_discovered_exact
       automatic_discovered_title
       automatic_discovered_meta
       automatic_discovered_ai

     Bu adlar 20 karakteri astigi icin sutun genisletilir.
     Genisletme mevcut degerleri BOZMAZ; kisaltma yapilmaz.

YENI TABLO YOK. RegulationReference semasi DEGISMEZ:
kesfedilen referansin nereden geldigi comparison
satirindaki yontem + kesif durumu ile izlenebilir.

Tamami IDEMPOTENT'tir.

Calistirma:
    python -m tests.migrate_regulation_reference_discovery
"""

from sqlalchemy import text

from app.core.database import engine


MIGRATIONS = [
    """
    ALTER TABLE regulation_comparisons
    ADD COLUMN IF NOT EXISTS reference_discovery_status
    VARCHAR(20)
    """,

    """
    ALTER TABLE regulation_comparisons
    ALTER COLUMN reference_match_method
    TYPE VARCHAR(40)
    """,

    """
    CREATE INDEX IF NOT EXISTS
        ix_regulation_comparisons_reference_discovery_status
    ON regulation_comparisons (reference_discovery_status)
    """,
]


COLUMN_INFO_SQL = """
SELECT character_maximum_length
FROM information_schema.columns
WHERE table_name = 'regulation_comparisons'
  AND column_name = :column
"""


def main():
    print("=" * 60)
    print("AUTOMATIC REFERENCE DISCOVERY MIGRATION")
    print("=" * 60)

    with engine.begin() as connection:

        for migration in MIGRATIONS:
            connection.execute(
                text(migration)
            )

    with engine.connect() as connection:

        discovery_length = connection.execute(
            text(COLUMN_INFO_SQL),
            {"column": "reference_discovery_status"},
        ).scalar()

        method_length = connection.execute(
            text(COLUMN_INFO_SQL),
            {"column": "reference_match_method"},
        ).scalar()

    print(
        f"reference_discovery_status : hazır "
        f"(VARCHAR({discovery_length}))"
    )
    print(
        f"reference_match_method     : hazır "
        f"(VARCHAR({method_length}))"
    )

    if method_length is not None and method_length < 40:
        print("\nMIGRATION BAŞARISIZ: sütun genişletilemedi")
        raise SystemExit(1)

    print("\nMIGRATION BAŞARILI")


if __name__ == "__main__":
    main()
