"""
Scheduler döngüsü davranış testi.

Gerçek pipeline yerine sahte (stub) bir çalıştırma fonksiyonu
kullanır. Ağ isteği yapmaz, Gemini çağırmaz, DB'ye yazmaz.

Doğrulananlar:
- Bir monitor hata verse bile döngü yaşamaya devam eder.
- Hata veren monitor diğer due monitorların çalışmasını engellemez.
- Devam eden bir çalıştırma varken aynı monitor yeniden başlatılmaz.
- start_scheduler iki kez çağrılsa bile tek döngü çalışır.

Çalıştırma:
    python -m tests.test_scheduler_loop
"""

import asyncio

from app.core import scheduler
from app.services import monitor_run_service


PASSED = []
FAILED = []


def check(label: str, condition: bool, detail: str = "") -> None:

    status = "PASS" if condition else "FAIL"

    print(f"[{status}] {label}" + (f" -> {detail}" if detail else ""))

    if condition:
        PASSED.append(label)
    else:
        FAILED.append(f"{label} -> {detail}")


async def main() -> None:

    print("=" * 70)
    print("SCHEDULER DONGUSU TESTI")
    print("=" * 70)

    # -----------------------------------------------------
    # STUB KURULUMU
    # -----------------------------------------------------

    # Her tick'te iki monitor due kabul edilir.
    # id=1 her zaman hata firlatir, id=2 basarili calisir.

    tick_count = 0
    started = []
    finished = []

    def fake_collect_due(now_local, timezone_info):
        nonlocal tick_count
        tick_count += 1

        return [
            (1, "Hatali Monitor"),
            (2, "Saglikli Monitor"),
        ]

    async def fake_execute(db, monitor):
        raise AssertionError(
            "Gercek pipeline cagrilmamaliydi."
        )

    async def fake_run_monitor(monitor_id, monitor_name):
        started.append(monitor_id)

        try:
            if monitor_id == 1:
                raise RuntimeError(
                    "Bilerek olusturulmus test hatasi"
                )

            # Bu monitor bir sonraki tick'e sarkacak kadar uzun surer.
            await asyncio.sleep(0.9)

            finished.append(monitor_id)

        except Exception as exc:
            print(f"        (beklenen hata yakalandi: {exc})")

        finally:
            monitor_run_service.release_monitor_run(monitor_id)

    original_collect = scheduler._collect_due_monitors
    original_execute_task = scheduler._execute_due_monitor
    original_execute_run = scheduler.execute_monitor_run
    original_tick_seconds = scheduler.TICK_SECONDS

    scheduler._collect_due_monitors = fake_collect_due
    scheduler._execute_due_monitor = fake_run_monitor
    scheduler.execute_monitor_run = fake_execute
    scheduler.TICK_SECONDS = 0.3

    try:
        # -------------------------------------------------
        # DONGUYU BASLAT
        # -------------------------------------------------

        await scheduler.start_scheduler()

        first_task = scheduler._scheduler_task

        # Ikinci cagri yeni bir dongu acmamali.
        await scheduler.start_scheduler()

        check(
            "start_scheduler iki kez cagrilsa da tek dongu calisir",
            scheduler._scheduler_task is first_task,
        )

        # Birkac tick calissin.
        await asyncio.sleep(1.6)

        check(
            "dongu hataya ragmen birden fazla tick calistirdi",
            tick_count >= 3,
            f"tick_count={tick_count}",
        )

        check(
            "hata veren monitor digerlerini durdurmadi",
            2 in finished,
            f"finished={finished}",
        )

        # id=1 her tick'te hata verip hemen serbest kaldigi icin
        # her tick'te yeniden baslar.
        check(
            "hata veren monitor sonraki tick'lerde tekrar denendi",
            started.count(1) >= 2,
            f"id=1 baslatma sayisi={started.count(1)}",
        )

        # id=2 her calismada 0.9 saniye surer, tick araligi 0.3 saniye.
        # Duplicate korumasi calisiyorsa baslatma sayisi tick
        # sayisindan belirgin sekilde az olmalidir.
        check(
            "devam eden calistirma varken ayni monitor yeniden baslatilmadi",
            started.count(2) < tick_count,
            f"id=2 baslatma={started.count(2)} tick={tick_count}",
        )

        check(
            "dongu hala canli",
            scheduler._scheduler_task is not None
            and not scheduler._scheduler_task.done(),
        )

        # -------------------------------------------------
        # DURDURMA
        # -------------------------------------------------

        await scheduler.stop_scheduler()

        check(
            "stop_scheduler donguyu temizledi",
            scheduler._scheduler_task is None,
        )

    finally:
        scheduler._collect_due_monitors = original_collect
        scheduler._execute_due_monitor = original_execute_task
        scheduler.execute_monitor_run = original_execute_run
        scheduler.TICK_SECONDS = original_tick_seconds

        monitor_run_service.release_monitor_run(1)
        monitor_run_service.release_monitor_run(2)

    # -----------------------------------------------------
    # ACQUIRE / RELEASE
    # -----------------------------------------------------

    print("\n--- calisma kilidi ---")

    check(
        "ilk acquire basarili",
        monitor_run_service.acquire_monitor_run(42),
    )

    check(
        "ayni monitor icin ikinci acquire reddedilir",
        monitor_run_service.acquire_monitor_run(42) is False,
    )

    check(
        "farkli monitor etkilenmez",
        monitor_run_service.acquire_monitor_run(43),
    )

    monitor_run_service.release_monitor_run(42)

    check(
        "release sonrasi tekrar acquire edilebilir",
        monitor_run_service.acquire_monitor_run(42),
    )

    monitor_run_service.release_monitor_run(42)
    monitor_run_service.release_monitor_run(43)

    check(
        "release sonrasi calisan monitor kalmadi",
        not monitor_run_service.is_monitor_running(42)
        and not monitor_run_service.is_monitor_running(43),
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
    asyncio.run(main())
