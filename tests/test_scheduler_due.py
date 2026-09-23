"""
Scheduler due hesaplama testleri.

Veritabanına dokunmaz, ağ isteği yapmaz, Gemini çağırmaz.
Sadece deterministic evaluate_monitor_due() fonksiyonunu doğrular.

Çalıştırma:
    python -m tests.test_scheduler_due
"""

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from app.core.scheduler import (
    evaluate_monitor_due,
    parse_schedule_time,
    resolve_weekly_weekday,
)
from app.models.monitor import Monitor


TZ = ZoneInfo("Europe/Istanbul")


# 2026-09-01 Salı, 2026-09-05 Cumartesi, 2026-09-07 Pazartesi
TUESDAY = date(2026, 9, 1)
SATURDAY = date(2026, 9, 5)
MONDAY = date(2026, 9, 7)


PASSED = []
FAILED = []


def local(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(
        day.year,
        day.month,
        day.day,
        hour,
        minute,
        tzinfo=TZ,
    )


def utc_naive(day: date, hour: int, minute: int = 0) -> datetime:
    """
    Yerel saati veritabanındaki naive UTC formatına çevirir.

    Mevcut modeller datetime.utcnow() kullanıyor.
    """

    return (
        local(day, hour, minute)
        .astimezone(timezone.utc)
        .replace(tzinfo=None)
    )


def build_monitor(
    schedule_type: str = "daily",
    schedule_time: str | None = "09:00",
    last_checked_at: datetime | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    is_unlimited: bool = True,
    is_active: bool = True,
    created_at: datetime | None = None,
) -> Monitor:
    """
    DB'ye kaydedilmeyen, sadece bellekte duran Monitor nesnesi.
    """

    return Monitor(
        id=999,
        name="Test Monitor",
        url="https://example.com/",
        topic="test",
        scope_type="full_page",
        schedule_type=schedule_type,
        schedule_time=schedule_time,
        start_date=start_date,
        end_date=end_date,
        is_unlimited=is_unlimited,
        notify_in_app=True,
        notify_email=False,
        notification_email=None,
        notification_type="in_app",
        notification_target=None,
        is_active=is_active,
        created_at=(
            created_at
            if created_at is not None
            else utc_naive(TUESDAY, 8, 0)
        ),
        last_checked_at=last_checked_at,
    )


def check(
    label: str,
    monitor: Monitor,
    now_local: datetime,
    expected_due: bool,
) -> None:

    decision = evaluate_monitor_due(
        monitor=monitor,
        now_local=now_local,
        timezone_info=TZ,
    )

    ok = decision.due == expected_due

    status = "PASS" if ok else "FAIL"

    line = (
        f"[{status}] {label}\n"
        f"        beklenen due={expected_due} "
        f"gercek due={decision.due} "
        f"sebep={decision.reason}"
    )

    print(line)

    if ok:
        PASSED.append(label)
    else:
        FAILED.append(line)


def main() -> None:

    print("=" * 70)
    print("SCHEDULER DUE HESAPLAMA TESTLERI")
    print("=" * 70)
    print(f"Timezone     : {TZ}")
    print(f"Referans gun : {TUESDAY} (Sali)")
    print("-" * 70)

    # -----------------------------------------------------
    # YARDIMCI FONKSIYONLAR
    # -----------------------------------------------------

    assert parse_schedule_time("09:00") == time(9, 0)
    assert parse_schedule_time("9:5") == time(9, 5)
    assert parse_schedule_time(None) is None
    assert parse_schedule_time("") is None
    assert parse_schedule_time("bozuk") is None
    assert parse_schedule_time("25:00") is None

    print("[PASS] parse_schedule_time")
    PASSED.append("parse_schedule_time")

    # -----------------------------------------------------
    # DAILY
    # -----------------------------------------------------

    print("\n--- daily ---")

    check(
        "daily / saatten once (08:00 < 09:00) -> due degil",
        build_monitor(schedule_type="daily", schedule_time="09:00"),
        local(TUESDAY, 8, 0),
        False,
    )

    check(
        "daily / saatten sonra ve bugun calismamis -> due",
        build_monitor(
            schedule_type="daily",
            schedule_time="09:00",
            last_checked_at=utc_naive(TUESDAY - timedelta(days=1), 9, 5),
        ),
        local(TUESDAY, 9, 30),
        True,
    )

    check(
        "daily / hic calistirilmamis ve saat gecmis -> due",
        build_monitor(
            schedule_type="daily",
            schedule_time="09:00",
            last_checked_at=None,
        ),
        local(TUESDAY, 9, 30),
        True,
    )

    check(
        "daily / bugun 09:05'te calismis -> due degil",
        build_monitor(
            schedule_type="daily",
            schedule_time="09:00",
            last_checked_at=utc_naive(TUESDAY, 9, 5),
        ),
        local(TUESDAY, 9, 30),
        False,
    )

    check(
        "daily / uygulama 11:00'de acildi, bugunku slot kacmis -> bir kez due",
        build_monitor(
            schedule_type="daily",
            schedule_time="09:00",
            last_checked_at=utc_naive(TUESDAY - timedelta(days=2), 9, 1),
        ),
        local(TUESDAY, 11, 0),
        True,
    )

    # -----------------------------------------------------
    # MANUEL vs SCHEDULER
    # -----------------------------------------------------

    print("\n--- manuel vs scheduler ---")

    check(
        "08:00 manuel calisma, 09:00 slot, saat 09:01 -> scheduler yine due",
        build_monitor(
            schedule_type="daily",
            schedule_time="09:00",
            last_checked_at=utc_naive(TUESDAY, 8, 0),
        ),
        local(TUESDAY, 9, 1),
        True,
    )

    check(
        "10:00 manuel calisma, 09:00 slot, saat 10:30 -> scheduler due degil",
        build_monitor(
            schedule_type="daily",
            schedule_time="09:00",
            last_checked_at=utc_naive(TUESDAY, 10, 0),
        ),
        local(TUESDAY, 10, 30),
        False,
    )

    check(
        "scheduler 09:00'da calisti, 09:01 tick -> tekrar due degil",
        build_monitor(
            schedule_type="daily",
            schedule_time="09:00",
            last_checked_at=utc_naive(TUESDAY, 9, 0),
        ),
        local(TUESDAY, 9, 1),
        False,
    )

    # -----------------------------------------------------
    # WEEKDAYS
    # -----------------------------------------------------

    print("\n--- weekdays ---")

    check(
        "weekdays / hafta ici (Sali) saat gecmis -> due",
        build_monitor(
            schedule_type="weekdays",
            schedule_time="09:00",
            last_checked_at=None,
        ),
        local(TUESDAY, 9, 30),
        True,
    )

    check(
        "weekdays / hafta sonu (Cumartesi) -> due degil",
        build_monitor(
            schedule_type="weekdays",
            schedule_time="09:00",
            last_checked_at=None,
        ),
        local(SATURDAY, 9, 30),
        False,
    )

    # -----------------------------------------------------
    # WEEKLY
    # -----------------------------------------------------

    print("\n--- weekly ---")

    check(
        "weekly / start_date Sali, bugun Sali -> due",
        build_monitor(
            schedule_type="weekly",
            schedule_time="09:00",
            start_date=TUESDAY,
            last_checked_at=None,
        ),
        local(TUESDAY, 9, 30),
        True,
    )

    check(
        "weekly / start_date Sali, bugun Pazartesi -> due degil",
        build_monitor(
            schedule_type="weekly",
            schedule_time="09:00",
            start_date=TUESDAY,
            last_checked_at=None,
        ),
        local(MONDAY, 9, 30),
        False,
    )

    # start_date yoksa created_at gununden turetilir.
    weekly_from_created = build_monitor(
        schedule_type="weekly",
        schedule_time="09:00",
        start_date=None,
        created_at=utc_naive(TUESDAY, 8, 0),
        last_checked_at=None,
    )

    assert (
        resolve_weekly_weekday(weekly_from_created, TZ)
        == TUESDAY.weekday()
    )

    check(
        "weekly / start_date yok, created_at Sali, bugun Sali -> due",
        weekly_from_created,
        local(TUESDAY, 9, 30),
        True,
    )

    check(
        "weekly / start_date yok, created_at Sali, bugun Cumartesi -> due degil",
        build_monitor(
            schedule_type="weekly",
            schedule_time="09:00",
            start_date=None,
            created_at=utc_naive(TUESDAY, 8, 0),
            last_checked_at=None,
        ),
        local(SATURDAY, 9, 30),
        False,
    )

    # -----------------------------------------------------
    # TARIH ARALIGI
    # -----------------------------------------------------

    print("\n--- tarih araligi ---")

    check(
        "start_date gelecekte -> due degil",
        build_monitor(
            schedule_type="daily",
            schedule_time="09:00",
            start_date=TUESDAY + timedelta(days=3),
            last_checked_at=None,
        ),
        local(TUESDAY, 9, 30),
        False,
    )

    check(
        "start_date bugun -> due",
        build_monitor(
            schedule_type="daily",
            schedule_time="09:00",
            start_date=TUESDAY,
            last_checked_at=None,
        ),
        local(TUESDAY, 9, 30),
        True,
    )

    check(
        "end_date gecmis ve is_unlimited=False -> due degil",
        build_monitor(
            schedule_type="daily",
            schedule_time="09:00",
            end_date=TUESDAY - timedelta(days=1),
            is_unlimited=False,
            last_checked_at=None,
        ),
        local(TUESDAY, 9, 30),
        False,
    )

    check(
        "end_date gecmis ama is_unlimited=True -> due (end_date yok sayilir)",
        build_monitor(
            schedule_type="daily",
            schedule_time="09:00",
            end_date=TUESDAY - timedelta(days=1),
            is_unlimited=True,
            last_checked_at=None,
        ),
        local(TUESDAY, 9, 30),
        True,
    )

    check(
        "end_date bugun ve is_unlimited=False -> due",
        build_monitor(
            schedule_type="daily",
            schedule_time="09:00",
            end_date=TUESDAY,
            is_unlimited=False,
            last_checked_at=None,
        ),
        local(TUESDAY, 9, 30),
        True,
    )

    # -----------------------------------------------------
    # PASIF MONITOR
    # -----------------------------------------------------

    print("\n--- pasif monitor ---")

    check(
        "is_active=False -> asla due degil",
        build_monitor(
            schedule_type="daily",
            schedule_time="09:00",
            is_active=False,
            last_checked_at=None,
        ),
        local(TUESDAY, 9, 30),
        False,
    )

    # -----------------------------------------------------
    # GECERSIZ DEGERLER
    # -----------------------------------------------------

    print("\n--- gecersiz degerler ---")

    check(
        "schedule_time None -> due degil",
        build_monitor(
            schedule_type="daily",
            schedule_time=None,
            last_checked_at=None,
        ),
        local(TUESDAY, 9, 30),
        False,
    )

    check(
        "desteklenmeyen schedule_type -> due degil",
        build_monitor(
            schedule_type="hourly",
            schedule_time="09:00",
            last_checked_at=None,
        ),
        local(TUESDAY, 9, 30),
        False,
    )

    # -----------------------------------------------------
    # TIMEZONE
    # -----------------------------------------------------

    print("\n--- timezone ---")

    # 06:30 UTC = 09:30 Istanbul -> slot gecmis, due olmali.
    check(
        "UTC olarak verilen 'simdi' Istanbul saatine cevrilir",
        build_monitor(
            schedule_type="daily",
            schedule_time="09:00",
            last_checked_at=None,
        ),
        datetime(2026, 9, 1, 6, 30, tzinfo=timezone.utc),
        True,
    )

    # 05:30 UTC = 08:30 Istanbul -> slot henuz gelmemis.
    check(
        "UTC 05:30 -> Istanbul 08:30, slot henuz gelmemis",
        build_monitor(
            schedule_type="daily",
            schedule_time="09:00",
            last_checked_at=None,
        ),
        datetime(2026, 9, 1, 5, 30, tzinfo=timezone.utc),
        False,
    )

    # -----------------------------------------------------
    # SONUC
    # -----------------------------------------------------

    print("\n" + "=" * 70)
    print(f"BASARILI : {len(PASSED)}")
    print(f"BASARISIZ: {len(FAILED)}")

    if FAILED:
        print("-" * 70)
        for line in FAILED:
            print(line)

        raise SystemExit(1)

    print("TUM TESTLER BASARILI")
    print("=" * 70)


if __name__ == "__main__":
    main()
