"""
AUTOMATIC REFERENCE PIPELINE MIGRATION

Yaptigi is:

  1. monitors.reference_selection_mode
       automatic | manual
       NOT NULL, DEFAULT 'manual'

  2. regulation_comparisons uzerine eslestirme kimligi:
       reference_match_method      (nullable)
       reference_match_confidence  (nullable)
       reference_freshness_status  (nullable)

Tamami IDEMPOTENT'tir.

MEVCUT KAYITLAR NEDEN 'manual'?

Bugune kadar her regulation monitor referansini
KULLANICI secmisti. Bu kayitlari otomatik moda almak,
kullanicinin bilerek yaptigi secimi sessizce iptal etmek
olurdu.

Bu yuzden VERITABANI varsayilani 'manual'dir; boylece
mevcut satirlar oldugu gibi manual kalir.

YENI kayitlarin varsayilani ise 'automatic'tir ve bu deger
model katmaninda (Python default) tanimlidir. Iki
varsayilanin farkli olmasi BILEREKTIR.

website_monitor kayitlarinda bu alan anlamsizdir ve
okunmaz.

Calistirma:
    python -m tests.migrate_reference_selection_mode
"""

from sqlalchemy import text

from app.core.database import engine


MIGRATIONS = [
    # -----------------------------------------------------
    # 1. REFERANS SECIM MODU
    #
    # DEFAULT 'manual': mevcut satirlar kullanicinin
    # sectigi referansi korur.
    # -----------------------------------------------------
    """
    ALTER TABLE monitors
    ADD COLUMN IF NOT EXISTS reference_selection_mode
    VARCHAR(20) NOT NULL DEFAULT 'manual'
    """,

    """
    CREATE INDEX IF NOT EXISTS
        ix_monitors_reference_selection_mode
    ON monitors (reference_selection_mode)
    """,

    # -----------------------------------------------------
    # 2. ESLESTIRME KIMLIGI
    # -----------------------------------------------------
    """
    ALTER TABLE regulation_comparisons
    ADD COLUMN IF NOT EXISTS reference_match_method
    VARCHAR(20)
    """,

    """
    ALTER TABLE regulation_comparisons
    ADD COLUMN IF NOT EXISTS reference_match_confidence
    DOUBLE PRECISION
    """,

    """
    ALTER TABLE regulation_comparisons
    ADD COLUMN IF NOT EXISTS reference_freshness_status
    VARCHAR(20)
    """,
]


# Referansi olan mevcut regulation monitorler KESINLIKLE
# manual olmalidir. DEFAULT bunu zaten sagliyor; bu UPDATE
# son emniyettir ve normalde bos kume uzerinde calisir.
BACKFILL_SQL = """
UPDATE monitors
SET reference_selection_mode = 'manual'
WHERE tracking_plan = 'regulation_monitor'
  AND reference_regulation_id IS NOT NULL
  AND reference_selection_mode <> 'manual'
"""


REPORT_SQL = """
SELECT reference_selection_mode, COUNT(*)
FROM monitors
WHERE tracking_plan = 'regulation_monitor'
GROUP BY reference_selection_mode
"""


def main():
    print("=" * 60)
    print("AUTOMATIC REFERENCE PIPELINE MIGRATION")
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

        distribution = connection.execute(
            text(REPORT_SQL)
        ).fetchall()

    print("monitors.reference_selection_mode          : hazır")
    print("comparisons.reference_match_method         : hazır")
    print("comparisons.reference_match_confidence     : hazır")
    print("comparisons.reference_freshness_status     : hazır")
    print(f"backfill edilen satır                      : {backfilled}")

    print("\nregulation_monitor mod dağılımı:")

    if not distribution:
        print("  (kayıt yok)")

    for mode, count in distribution:
        print(f"  {mode:12s} {count}")

    print("\nMIGRATION BAŞARILI")


if __name__ == "__main__":
    main()
