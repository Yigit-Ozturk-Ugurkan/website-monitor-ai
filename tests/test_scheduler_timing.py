"""
SCHEDULER ZAMANLAMA TESTLERI

SINIF: automated (deterministic, CI-safe)

Gercek kullanici kabulunde gozlenen sorun:

    scheduler basladi        : 09:16:33
    monitor schedule_time    : 09:30
    ILK due degerlendirmesi  : 09:37:28   <-- 7 dakika gec

Kok neden: dongu SABIT BEKLEMELI idi.

    await _tick()
    await asyncio.sleep(TICK_SECONDS)

Periyot "tick suresi + TICK_SECONDS" oluyordu ve gecikme
TELAFI EDILMIYORDU. Her tick'in maliyeti bir sonrakine
ekleniyor, kayma birikiyordu.

Bu dosya zamanlama sozunu dogrular:

    tick=T ise, due bir monitor normal sartlarda
    planlanan andan en fazla ~T sonra degerlendirilir.

Kapsam:

     1. bos tick'lerde periyot sabit
     2. tick maliyeti periyoda EKLENMEZ
     3. tekrarlayan maliyet kayma BIRIKTIRMEZ
     4. tek bir aksama sonraki periyotlari bozmaz
     5. cok gecikince ardi ardina tick PATLAMAZ
     6. gecikme yeniden hizalama ile loglanir
     7. yavas tick uyarisi uretilir
     8. 09:30 monitoru 09:30:xx tick'inde due
     9. planlanan andan once due DEGIL
    10. uzun monitor run diger monitoru geciktirmez
    11. uzun monitor run tick'i BEKLETMEZ
    12. fetch hatasi -> sonraki tick tekrar dener
    13. basarili run -> ayni slot tekrar CALISMAZ
    14. duplicate slot korumasi (calisan monitor atlanir)
    15. catch-up: gecikmis slot hala calisir
    16. manuel run ile cakisma korumasi
    17. tick due sayisini raporlar
    18. run suresi loglanir

GERCEK GEMINI YOK. GERCEK SMTP YOK. GERCEK AG YOK.
VERITABANI KULLANILMAZ (stub collect / stub run).

Calistirma:
    python -m tests.test_scheduler_timing
"""

import asyncio
import logging
import time

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from app.core import scheduler
from app.services import monitor_run_service
from app.services.web_fetcher import WebsiteFetchError


PASSED = []
FAILED = []


def check(label: str, condition: bool, detail: str = "") -> None:

    status = "PASS" if condition else "FAIL"

    print(f"[{status}] {label}" + (f" -> {detail}" if detail else ""))

    if condition:
        PASSED.append(label)
    else:
        FAILED.append(f"{label} -> {detail}")


# ---------------------------------------------------------
# LOG YAKALAYICI
# ---------------------------------------------------------

class LogCatcher(logging.Handler):

    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)

    def messages(self, level=None):
        return [
            record.getMessage()
            for record in self.records
            if level is None or record.levelno == level
        ]


# ---------------------------------------------------------
# DONGU KOSTURUCU
#
# Gercek _scheduler_loop kullanilir. Yalnizca _tick ve
# TICK_SECONDS degistirilir; zamanlama mantigi URETIM
# KODUDUR.
# ---------------------------------------------------------

async def run_loop(tick_body, ticks, tick_seconds, timeout=30.0):
    """
    Gercek _scheduler_loop'u calistirir ve tick anlarini
    dondurur.
    """

    fire_times = []

    original_tick = scheduler._tick
    original_seconds = scheduler.TICK_SECONDS

    start = None

    async def instrumented():
        nonlocal start

        now = time.monotonic()

        if start is None:
            start = now

        fire_times.append(now - start)

        await tick_body(len(fire_times))

    scheduler._tick = instrumented
    scheduler.TICK_SECONDS = tick_seconds

    task = asyncio.create_task(scheduler._scheduler_loop())

    deadline = time.monotonic() + timeout

    try:
        while (
            len(fire_times) < ticks
            and time.monotonic() < deadline
        ):
            await asyncio.sleep(0.005)

    finally:
        task.cancel()

        try:
            await task
        except asyncio.CancelledError:
            pass

        scheduler._tick = original_tick
        scheduler.TICK_SECONDS = original_seconds

    return fire_times


async def noop_tick(index):
    return None


def periods(fire_times):
    return [
        fire_times[i + 1] - fire_times[i]
        for i in range(len(fire_times) - 1)
    ]


# ---------------------------------------------------------
# SAHTE MONITOR
# ---------------------------------------------------------

@dataclass
class FakeMonitor:
    id: int
    name: str = "Test Monitor"
    is_active: bool = True
    schedule_type: str = "daily"
    schedule_time: str = "09:30"
    start_date: date | None = None
    end_date: date | None = None
    is_unlimited: bool = True
    last_checked_at: datetime | None = None
    created_at: datetime | None = None


# ---------------------------------------------------------
# 1-7. ZAMANLAMA
# ---------------------------------------------------------

async def timing_tests():

    print()
    print("--- zamanlama ---")

    tick_seconds = 0.20

    # 1. bos tick
    fires = await run_loop(noop_tick, 6, tick_seconds)

    idle_periods = periods(fires)

    check(
        "1. bos tick'lerde periyot sabit",
        all(
            abs(p - tick_seconds) < 0.05
            for p in idle_periods
        ),
        f"{[round(p, 3) for p in idle_periods]}",
    )

    # 2 + 3. tick maliyeti periyoda eklenmemeli
    cost = 0.08

    async def costly(index):
        # Senkron blok: gercek run'lardaki CPU/DB isi.
        time.sleep(cost)

    fires = await run_loop(costly, 6, tick_seconds)

    costly_periods = periods(fires)

    check(
        "2. tick maliyeti periyoda EKLENMEZ",
        all(
            p < tick_seconds + (cost / 2)
            for p in costly_periods
        ),
        f"maliyet={cost}s periyotlar="
        f"{[round(p, 3) for p in costly_periods]}",
    )

    ideal_total = tick_seconds * (len(fires) - 1)

    accumulated = fires[-1] - fires[0] - ideal_total

    check(
        "3. tekrarlayan maliyet kayma BIRIKTIRMEZ",
        abs(accumulated) < tick_seconds,
        f"biriken kayma={accumulated:+.3f}s "
        f"(eski davranista ~{cost * (len(fires)-1):+.3f}s olurdu)",
    )

    # 4. tek aksama sonraki periyotlari bozmamali
    async def one_stall(index):
        if index == 3:
            time.sleep(tick_seconds * 3)

    fires = await run_loop(one_stall, 7, tick_seconds)

    after_stall = periods(fires)[3:]

    check(
        "4. tek bir aksama sonraki periyotlari bozmaz",
        all(
            abs(p - tick_seconds) < 0.05
            for p in after_stall
        ),
        f"{[round(p, 3) for p in after_stall]}",
    )

    # 5. cok gecikince tick patlamamali
    async def long_stall(index):
        if index == 2:
            time.sleep(tick_seconds * 5)

    fires = await run_loop(long_stall, 6, tick_seconds)

    burst = [p for p in periods(fires)[2:] if p < tick_seconds * 0.5]

    check(
        "5. cok gecikince ardi ardina tick PATLAMAZ",
        not burst,
        f"anlik tick sayisi={len(burst)}",
    )

    # 6 + 7. loglar
    catcher = LogCatcher()

    scheduler.logger.addHandler(catcher)
    original_level = scheduler.logger.level
    scheduler.logger.setLevel(logging.DEBUG)

    original_slow = scheduler.SLOW_TICK_SECONDS
    scheduler.SLOW_TICK_SECONDS = 0.05

    try:
        await run_loop(long_stall, 4, tick_seconds)

    finally:
        scheduler.SLOW_TICK_SECONDS = original_slow
        scheduler.logger.setLevel(original_level)
        scheduler.logger.removeHandler(catcher)

    warnings = catcher.messages(logging.WARNING)

    check(
        "6. gecikme yeniden hizalama ile loglanir",
        any("yeniden hizalandi" in m for m in warnings),
        f"{[m[:50] for m in warnings][:3]}",
    )

    check(
        "7. yavas tick uyarisi uretilir",
        any("tick yavas" in m for m in warnings),
        f"{[m[:40] for m in warnings][:3]}",
    )


# ---------------------------------------------------------
# 8-9. DUE DEGERLENDIRMESI
# ---------------------------------------------------------

def due_tests():

    print()
    print("--- due degerlendirmesi ---")

    timezone_info = scheduler.get_app_timezone()

    today = date(2026, 9, 7)

    # Gozlenen senaryo: 09:30 monitoru, tick 09:30:28'de.
    monitor = FakeMonitor(
        id=3659,
        name="Enerji Mevzuati Takibi",
        schedule_time="09:30",
        # Bir onceki gun calismis.
        last_checked_at=datetime(2026, 9, 6, 6, 30, 0),
    )

    at_slot = datetime(
        2026, 9, 7, 9, 30, 28,
        tzinfo=timezone_info,
    )

    decision = scheduler.evaluate_monitor_due(
        monitor=monitor,
        now_local=at_slot,
        timezone_info=timezone_info,
    )

    check(
        "8. 09:30 monitoru 09:30:xx tick'inde due",
        decision.due is True
        and decision.scheduled_at
        == datetime(2026, 9, 7, 9, 30, tzinfo=timezone_info),
        f"due={decision.due} sebep={decision.reason}",
    )

    before = datetime(
        2026, 9, 7, 9, 29, 58,
        tzinfo=timezone_info,
    )

    early = scheduler.evaluate_monitor_due(
        monitor=monitor,
        now_local=before,
        timezone_info=timezone_info,
    )

    check(
        "9. planlanan andan once due DEGIL",
        early.due is False
        and early.reason == "planlanan saatten once",
        f"due={early.due} sebep={early.reason}",
    )


# ---------------------------------------------------------
# 10-11. UZUN RUN DIGERLERINI GECIKTIRMEMELI
# ---------------------------------------------------------

async def isolation_tests():

    print()
    print("--- uzun run izolasyonu ---")

    tick_seconds = 0.20

    # Iki monitor ayni anda due. id=1 UZUN surer.
    started = []
    finished = []

    long_run_seconds = tick_seconds * 6

    original_collect = scheduler._collect_due_monitors
    original_execute = scheduler.execute_monitor_run
    original_get = scheduler.get_monitor
    original_session = scheduler.SessionLocal

    def fake_collect(now_local, timezone_info):
        return [(1, "Uzun Monitor"), (2, "Kisa Monitor")]

    class FakeSession:
        def close(self):
            return None

    def fake_get_monitor(db, monitor_id):
        return FakeMonitor(id=monitor_id)

    async def fake_execute(db, monitor):
        started.append((monitor.id, time.monotonic()))

        if monitor.id == 1:
            # Uzun ama AWAIT edilen is (fetch / Gemini).
            await asyncio.sleep(long_run_seconds)

        finished.append((monitor.id, time.monotonic()))

        class Result:
            first_run = False
            changed = False
            ai_error = None

        class Outcome:
            result = Result()
            notification = None

        return Outcome()

    scheduler._collect_due_monitors = fake_collect
    scheduler.execute_monitor_run = fake_execute
    scheduler.get_monitor = fake_get_monitor
    scheduler.SessionLocal = FakeSession

    # GERCEK _tick calistirilir. run_loop tick govdesine
    # sira numarasi gecirdigi icin ince bir sarmalayici
    # gerekir; _tick parametre almaz.
    real_tick = scheduler._tick

    async def call_real_tick(index):
        await real_tick()

    try:
        fires = await run_loop(
            call_real_tick,
            3,
            tick_seconds,
            timeout=15.0,
        )

    finally:
        scheduler._collect_due_monitors = original_collect
        scheduler.execute_monitor_run = original_execute
        scheduler.get_monitor = original_get
        scheduler.SessionLocal = original_session

        for monitor_id in (1, 2):
            monitor_run_service.release_monitor_run(monitor_id)

        # Arka plan task'lari bitsin.
        await asyncio.sleep(long_run_seconds + 0.2)

    short_starts = [t for mid, t in started if mid == 2]
    long_starts = [t for mid, t in started if mid == 1]

    check(
        "10. uzun monitor run diger monitoru geciktirmez",
        bool(short_starts)
        and bool(long_starts)
        and abs(short_starts[0] - long_starts[0]) < 0.05,
        f"uzun={long_starts[:1]} kisa={short_starts[:1]}",
    )

    loop_periods = periods(fires)

    check(
        "11. uzun monitor run tick'i BEKLETMEZ",
        all(
            p < tick_seconds + 0.1
            for p in loop_periods
        ),
        f"run={long_run_seconds}s periyotlar="
        f"{[round(p, 3) for p in loop_periods]}",
    )


# ---------------------------------------------------------
# 12-16. RETRY / SLOT / LOCK
# ---------------------------------------------------------

async def retry_and_lock_tests():

    print()
    print("--- retry, slot ve lock ---")

    timezone_info = scheduler.get_app_timezone()

    scheduled = datetime(
        2026, 9, 7, 9, 30, tzinfo=timezone_info,
    )

    # 12. fetch hatasi -> last_checked_at guncellenmez ->
    #     sonraki tick tekrar dener.
    monitor = FakeMonitor(
        id=3659,
        last_checked_at=datetime(2026, 9, 6, 6, 30, 0),
    )

    first_try = scheduler.evaluate_monitor_due(
        monitor=monitor,
        now_local=scheduled + timedelta(seconds=28),
        timezone_info=timezone_info,
    )

    # Fetch basarisiz: last_checked_at DEGISMEDI.
    retry = scheduler.evaluate_monitor_due(
        monitor=monitor,
        now_local=scheduled + timedelta(seconds=88),
        timezone_info=timezone_info,
    )

    check(
        "12. fetch hatasi -> sonraki tick tekrar dener",
        first_try.due is True and retry.due is True,
        f"ilk={first_try.due} tekrar={retry.due}",
    )

    # 13. basarili run -> slot tuketildi
    monitor.last_checked_at = datetime(2026, 9, 7, 6, 38, 28)

    after_success = scheduler.evaluate_monitor_due(
        monitor=monitor,
        now_local=scheduled + timedelta(seconds=148),
        timezone_info=timezone_info,
    )

    check(
        "13. basarili run -> ayni slot tekrar CALISMAZ",
        after_success.due is False
        and after_success.reason
        == "bugunku slot zaten calistirilmis",
        f"{after_success.reason}",
    )

    # 14. duplicate slot korumasi
    monitor_run_service.release_monitor_run(4242)

    first_claim = monitor_run_service.acquire_monitor_run(4242)
    second_claim = monitor_run_service.acquire_monitor_run(4242)

    check(
        "14. duplicate slot korumasi (calisan monitor atlanir)",
        first_claim is True and second_claim is False,
        f"1={first_claim} 2={second_claim}",
    )

    # 16. manuel run ile cakisma
    check(
        "16. manuel run ile cakisma korumasi",
        monitor_run_service.is_monitor_running(4242) is True,
        "lock tutuluyor",
    )

    monitor_run_service.release_monitor_run(4242)

    check(
        "16. lock birakildiktan sonra tekrar alinabilir",
        monitor_run_service.acquire_monitor_run(4242) is True,
    )

    monitor_run_service.release_monitor_run(4242)

    # 15. catch-up: scheduler gec baslasa bile slot calisir
    late_monitor = FakeMonitor(
        id=2,
        schedule_time="09:00",
        last_checked_at=datetime(2026, 9, 6, 6, 0, 0),
    )

    catch_up = scheduler.evaluate_monitor_due(
        monitor=late_monitor,
        now_local=datetime(
            2026, 9, 7, 9, 16, 33, tzinfo=timezone_info,
        ),
        timezone_info=timezone_info,
    )

    check(
        "15. catch-up: gecikmis slot hala calisir",
        catch_up.due is True
        and catch_up.reason == "bugunku slot bekliyor",
        f"due={catch_up.due} sebep={catch_up.reason}",
    )


# ---------------------------------------------------------
# 17-18. TESHIS LOGLARI
# ---------------------------------------------------------

async def diagnostic_tests():

    print()
    print("--- teshis loglari ---")

    catcher = LogCatcher()

    scheduler.logger.addHandler(catcher)
    original_level = scheduler.logger.level
    scheduler.logger.setLevel(logging.DEBUG)

    original_collect = scheduler._collect_due_monitors
    original_execute = scheduler.execute_monitor_run
    original_get = scheduler.get_monitor
    original_session = scheduler.SessionLocal

    class FakeSession:
        def close(self):
            return None

    def fake_collect(now_local, timezone_info):
        return [(7, "Teshis Monitoru")]

    def fake_get_monitor(db, monitor_id):
        return FakeMonitor(id=monitor_id)

    async def fake_execute(db, monitor):
        await asyncio.sleep(0.05)

        class Result:
            first_run = False
            changed = False
            ai_error = None

        class Outcome:
            result = Result()
            notification = None

        return Outcome()

    scheduler._collect_due_monitors = fake_collect
    scheduler.execute_monitor_run = fake_execute
    scheduler.get_monitor = fake_get_monitor
    scheduler.SessionLocal = FakeSession

    try:
        await scheduler._tick()

        # Run task'i bitsin.
        await asyncio.sleep(0.3)

    finally:
        scheduler._collect_due_monitors = original_collect
        scheduler.execute_monitor_run = original_execute
        scheduler.get_monitor = original_get
        scheduler.SessionLocal = original_session

        monitor_run_service.release_monitor_run(7)

        scheduler.logger.setLevel(original_level)
        scheduler.logger.removeHandler(catcher)

    messages = catcher.messages()

    check(
        "17. tick due sayisini raporlar",
        any(
            "tick bitti" in m and "due=1" in m
            for m in messages
        ),
        f"{[m[:60] for m in messages if 'tick' in m][:3]}",
    )

    check(
        "17. tick baslangici loglanir",
        any("tick basladi" in m for m in messages),
    )

    check(
        "18. run suresi loglanir",
        any(
            "calistirmasi tamamlandi" in m and "sure=" in m
            for m in messages
        ),
        f"{[m[:70] for m in messages if 'tamamlandi' in m][:2]}",
    )


async def main() -> None:

    print("=" * 70)
    print("SCHEDULER ZAMANLAMA TESTLERI")
    print("=" * 70)

    await timing_tests()

    due_tests()

    await isolation_tests()

    await retry_and_lock_tests()

    await diagnostic_tests()

    print()
    print("=" * 70)
    print(f"BASARILI : {len(PASSED)}")
    print(f"BASARISIZ: {len(FAILED)}")

    if FAILED:
        print("-" * 70)

        for item in FAILED:
            print(f"  {item}")

        print("=" * 70)
        raise SystemExit(1)

    print("TUM TESTLER BASARILI")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
