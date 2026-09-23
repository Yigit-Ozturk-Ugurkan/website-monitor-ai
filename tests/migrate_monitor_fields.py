from sqlalchemy import text

from app.core.database import engine


MIGRATIONS = [
    """
    ALTER TABLE monitors
    ADD COLUMN IF NOT EXISTS scope_type VARCHAR(30)
    NOT NULL DEFAULT 'auto_section'
    """,

    """
    ALTER TABLE monitors
    ADD COLUMN IF NOT EXISTS start_date DATE
    """,

    """
    ALTER TABLE monitors
    ADD COLUMN IF NOT EXISTS end_date DATE
    """,

    """
    ALTER TABLE monitors
    ADD COLUMN IF NOT EXISTS is_unlimited BOOLEAN
    NOT NULL DEFAULT TRUE
    """,

    """
    ALTER TABLE monitors
    ADD COLUMN IF NOT EXISTS notify_in_app BOOLEAN
    NOT NULL DEFAULT TRUE
    """,

    """
    ALTER TABLE monitors
    ADD COLUMN IF NOT EXISTS notify_email BOOLEAN
    NOT NULL DEFAULT FALSE
    """,

    """
    ALTER TABLE monitors
    ADD COLUMN IF NOT EXISTS notification_email VARCHAR(255)
    """,
]


def main():
    print("=" * 60)
    print("MONITOR TABLE MIGRATION")
    print("=" * 60)

    with engine.begin() as connection:
        for migration in MIGRATIONS:
            connection.execute(
                text(migration)
            )

    print("scope_type         : hazır")
    print("start_date         : hazır")
    print("end_date           : hazır")
    print("is_unlimited       : hazır")
    print("notify_in_app      : hazır")
    print("notify_email       : hazır")
    print("notification_email : hazır")

    print("\nMIGRATION BAŞARILI")


if __name__ == "__main__":
    main()