"""
FastAPI lifespan / scheduler startup testi.

Uygulamanın lifespan context'ini deterministik olarak çalıştırır:
startup -> scheduler ayakta mı, shutdown -> temiz kapanıyor mu.

Scheduler'ın gerçek monitor çalıştırmaması için tick fonksiyonu
sahte (stub) ile değiştirilir. Ağ isteği yapmaz, Gemini çağırmaz,
veritabanına yazmaz.

Çalıştırma:
    python -m tests.test_scheduler_startup
"""

import asyncio

from app.core import scheduler


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
    print("FASTAPI LIFESPAN / SCHEDULER STARTUP TESTI")
    print("=" * 70)

    tick_calls = 0

    async def fake_tick():
        nonlocal tick_calls
        tick_calls += 1

    original_tick = scheduler._tick
    original_tick_seconds = scheduler.TICK_SECONDS

    scheduler._tick = fake_tick
    scheduler.TICK_SECONDS = 0.2

    try:
        from app.main import app

        check(
            "startup oncesi scheduler calismiyor",
            scheduler._scheduler_task is None
            or scheduler._scheduler_task.done(),
        )

        async with app.router.lifespan_context(app):

            await asyncio.sleep(0.5)

            check(
                "lifespan startup scheduler'i baslatti",
                scheduler._scheduler_task is not None
                and not scheduler._scheduler_task.done(),
            )

            check(
                "scheduler tick calisiyor",
                tick_calls >= 2,
                f"tick_calls={tick_calls}",
            )

        check(
            "lifespan shutdown scheduler'i durdurdu",
            scheduler._scheduler_task is None,
        )

        calls_after_shutdown = tick_calls
        await asyncio.sleep(0.5)

        check(
            "shutdown sonrasi yeni tick yok",
            tick_calls == calls_after_shutdown,
            f"{calls_after_shutdown} -> {tick_calls}",
        )

    finally:
        scheduler._tick = original_tick
        scheduler.TICK_SECONDS = original_tick_seconds

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
