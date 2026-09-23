import asyncio
import logging

from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.core.config import settings
from app.core.database import SessionLocal

from app.models.monitor import Monitor

from app.repositories.monitor_repository import (
    get_active_monitors,
    get_monitor,
)
from app.services.monitor_run_service import (
    acquire_monitor_run,
    execute_monitor_run,
    release_monitor_run,
)
from app.services.web_fetcher import WebsiteFetchError


logger = logging.getLogger(__name__)


# Scheduler kaç saniyede bir kontrol etsin.
TICK_SECONDS = 60


# Tick'in kendisi bu sureden uzun surerse uyarilir.
#
# Tick yalnizca due degerlendirmesi yapar; monitor
# calistirmalari ayri task'lardir. Bu esigin asilmasi
# due degerlendirmesinin bloklandigi anlamina gelir.
SLOW_TICK_SECONDS = 5


# Pazartesi = 0, Cuma = 4
WEEKDAY_SCHEDULE_LIMIT = 5


_scheduler_task: asyncio.Task | None = None
_monitor_tasks: set[asyncio.Task] = set()


# ---------------------------------------------------------
# TIMEZONE
# ---------------------------------------------------------

def get_app_timezone() -> ZoneInfo:
    """
    Uygulama saat dilimini döndürür.

    schedule_time kullanıcının yerel saati olarak
    bu timezone içinde yorumlanır.
    """

    try:
        return ZoneInfo(settings.app_timezone)

    except (ZoneInfoNotFoundError, ValueError):
        logger.warning(
            "Geçersiz APP_TIMEZONE değeri: %s. "
            "Europe/Istanbul kullanılıyor.",
            settings.app_timezone,
        )

        return ZoneInfo("Europe/Istanbul")


def to_local(
    value: datetime,
    timezone_info: ZoneInfo,
) -> datetime:
    """
    Veritabanındaki naive UTC timestamp değerini
    yerel saate çevirir.

    Mevcut modeller datetime.utcnow() kullandığı için
    tzinfo taşımayan değerler UTC kabul edilir.
    """

    if value.tzinfo is None:
        value = value.replace(
            tzinfo=timezone.utc
        )

    return value.astimezone(
        timezone_info
    )


# ---------------------------------------------------------
# DUE HESAPLAMA
# ---------------------------------------------------------

@dataclass
class DueDecision:
    due: bool
    reason: str
    scheduled_at: datetime | None = None


def parse_schedule_time(
    value: str | None,
) -> time | None:
    """
    "HH:MM" formatındaki schedule_time değerini parse eder.
    """

    if not value:
        return None

    parts = value.strip().split(":")

    if len(parts) < 2:
        return None

    try:
        return time(
            hour=int(parts[0]),
            minute=int(parts[1]),
        )

    except (ValueError, TypeError):
        return None


def resolve_weekly_weekday(
    monitor: Monitor,
    timezone_info: ZoneInfo,
) -> int:
    """
    weekly schedule için haftanın gününü belirler.

    Öncelik start_date, yoksa created_at.
    Pazartesi = 0.
    """

    if monitor.start_date is not None:
        return monitor.start_date.weekday()

    if monitor.created_at is not None:
        return to_local(
            monitor.created_at,
            timezone_info,
        ).date().weekday()

    return 0


def runs_on_date(
    monitor: Monitor,
    day: date,
    timezone_info: ZoneInfo,
) -> tuple[bool, str]:
    """
    Monitor verilen takvim gününde çalışmalı mı?
    """

    if (
        monitor.start_date is not None
        and day < monitor.start_date
    ):
        return False, "start_date henuz gelmedi"

    if (
        not monitor.is_unlimited
        and monitor.end_date is not None
        and day > monitor.end_date
    ):
        return False, "end_date gecildi"

    schedule_type = monitor.schedule_type

    if schedule_type == "daily":
        return True, "daily"

    if schedule_type == "weekdays":

        if day.weekday() < WEEKDAY_SCHEDULE_LIMIT:
            return True, "weekdays"

        return False, "hafta sonu"

    if schedule_type == "weekly":

        target_weekday = resolve_weekly_weekday(
            monitor=monitor,
            timezone_info=timezone_info,
        )

        if day.weekday() == target_weekday:
            return True, "weekly"

        return False, "weekly icin farkli gun"

    return (
        False,
        f"desteklenmeyen schedule_type: {schedule_type}",
    )


def evaluate_monitor_due(
    monitor: Monitor,
    now_local: datetime,
    timezone_info: ZoneInfo | None = None,
) -> DueDecision:
    """
    Monitor şu anda çalıştırılmalı mı?

    Deterministic fonksiyondur. Veritabanına dokunmaz.
    Yalnızca monitor alanları ve verilen "şu an"
    değeri üzerinden karar verir.
    """

    if timezone_info is None:
        timezone_info = get_app_timezone()

    if now_local.tzinfo is None:
        now_local = now_local.replace(
            tzinfo=timezone_info
        )
    else:
        now_local = now_local.astimezone(
            timezone_info
        )

    if not monitor.is_active:
        return DueDecision(
            due=False,
            reason="monitor pasif",
        )

    today_local = now_local.date()

    should_run_today, reason = runs_on_date(
        monitor=monitor,
        day=today_local,
        timezone_info=timezone_info,
    )

    if not should_run_today:
        return DueDecision(
            due=False,
            reason=reason,
        )

    scheduled_time = parse_schedule_time(
        monitor.schedule_time
    )

    if scheduled_time is None:
        return DueDecision(
            due=False,
            reason="schedule_time gecersiz",
        )

    scheduled_at = datetime.combine(
        today_local,
        scheduled_time,
        tzinfo=timezone_info,
    )

    if now_local < scheduled_at:
        return DueDecision(
            due=False,
            reason="planlanan saatten once",
            scheduled_at=scheduled_at,
        )

    if monitor.last_checked_at is None:
        return DueDecision(
            due=True,
            reason="hic calistirilmamis",
            scheduled_at=scheduled_at,
        )

    last_checked_local = to_local(
        monitor.last_checked_at,
        timezone_info,
    )

    # Bugünkü slot saatinden sonra herhangi bir çalışma
    # olduysa (manuel ya da otomatik) slot tüketilmiştir.
    if last_checked_local >= scheduled_at:
        return DueDecision(
            due=False,
            reason="bugunku slot zaten calistirilmis",
            scheduled_at=scheduled_at,
        )

    return DueDecision(
        due=True,
        reason="bugunku slot bekliyor",
        scheduled_at=scheduled_at,
    )


# ---------------------------------------------------------
# SCHEDULER DÖNGÜSÜ
# ---------------------------------------------------------

async def _execute_due_monitor(
    monitor_id: int,
    monitor_name: str,
) -> None:
    """
    Tek bir monitörü kendi DB session'ı ile çalıştırır.

    Hatalar burada yutulur, scheduler döngüsü ayakta kalır.

    Bu fonksiyon tick tarafindan BEKLENMEZ; ayri bir task
    olarak calisir. Suresi tick periyodunu etkilemez.
    """

    started = asyncio.get_running_loop().time()

    db = SessionLocal()

    try:
        monitor = get_monitor(
            db=db,
            monitor_id=monitor_id,
        )

        if monitor is None:
            logger.warning(
                "Scheduler: monitor bulunamadi. id=%s",
                monitor_id,
            )
            return

        outcome = await execute_monitor_run(
            db=db,
            monitor=monitor,
        )

        result = outcome.result

        logger.info(
            "Scheduler calistirmasi tamamlandi. "
            "id=%s name=%s sure=%.2fs first_run=%s changed=%s "
            "notification=%s ai_error=%s",
            monitor_id,
            monitor_name,
            asyncio.get_running_loop().time() - started,
            result.first_run,
            result.changed,
            outcome.notification is not None,
            result.ai_error,
        )

    except WebsiteFetchError as exc:
        # Site gecici olarak erisilemiyor olabilir.
        # last_checked_at guncellenmedigi icin sonraki
        # tick'lerde tekrar denenir.
        logger.warning(
            "Scheduler: site getirilemedi. id=%s name=%s hata=%s",
            monitor_id,
            monitor_name,
            exc,
        )

    except Exception:
        # Gemini hatasi veya beklenmeyen bir hata
        # scheduler process'ini cokertmemeli.
        logger.exception(
            "Scheduler calistirma hatasi. id=%s name=%s",
            monitor_id,
            monitor_name,
        )

    finally:
        db.close()
        release_monitor_run(monitor_id)


def _collect_due_monitors(
    now_local: datetime,
    timezone_info: ZoneInfo,
) -> list[tuple[int, str]]:
    """
    Aktif monitorları okur ve due olanları döndürür.
    """

    db = SessionLocal()

    try:
        due_monitors = []

        for monitor in get_active_monitors(db):

            decision = evaluate_monitor_due(
                monitor=monitor,
                now_local=now_local,
                timezone_info=timezone_info,
            )

            if decision.due:

                logger.info(
                    "Scheduler: monitor due. "
                    "id=%s name=%s slot=%s sebep=%s",
                    monitor.id,
                    monitor.name,
                    decision.scheduled_at,
                    decision.reason,
                )

                due_monitors.append(
                    (monitor.id, monitor.name)
                )

        return due_monitors

    finally:
        db.close()


async def _tick() -> None:
    """
    Tek bir due degerlendirmesi.

    Bu fonksiyon monitor calistirmalarini BEKLEMEZ; her
    calistirma ayri bir task'tir. Yani uzun suren bir
    monitor run'i diger monitorlerin due
    degerlendirmesini geciktirmez.
    """

    loop = asyncio.get_running_loop()

    started = loop.time()

    timezone_info = get_app_timezone()

    now_local = datetime.now(
        timezone_info
    )

    logger.debug(
        "Scheduler tick basladi. simdi=%s",
        now_local.isoformat(timespec="seconds"),
    )

    # Senkron SQLAlchemy sorgusu event loop'u bloklamasin.
    due_monitors = await asyncio.to_thread(
        _collect_due_monitors,
        now_local,
        timezone_info,
    )

    started_count = 0

    for monitor_id, monitor_name in due_monitors:

        # Aynı monitor için önceki çalıştırma hâlâ
        # devam ediyorsa yeniden başlatma.
        if not acquire_monitor_run(monitor_id):

            logger.info(
                "Scheduler: monitor zaten calisiyor, atlandi. id=%s",
                monitor_id,
            )
            continue

        task = asyncio.create_task(
            _execute_due_monitor(
                monitor_id=monitor_id,
                monitor_name=monitor_name,
            )
        )

        _monitor_tasks.add(task)

        task.add_done_callback(
            _monitor_tasks.discard
        )

        started_count += 1

    duration = loop.time() - started

    # Sessiz tick'ler her 60 saniyede bir INFO uretmemeli.
    # Islem yapilan tick INFO, bos tick DEBUG.
    if due_monitors:
        logger.info(
            "Scheduler tick bitti. sure=%.3fs due=%s "
            "baslatilan=%s devam_eden=%s",
            duration,
            len(due_monitors),
            started_count,
            len(_monitor_tasks),
        )
    else:
        logger.debug(
            "Scheduler tick bitti. sure=%.3fs due=0 "
            "devam_eden=%s",
            duration,
            len(_monitor_tasks),
        )


async def _scheduler_loop() -> None:
    """
    Tick dongusu.

    ZAMANLAMA KURALI

    Tick'ler SABIT ARALIKLIDIR (fixed-rate), sabit
    BEKLEMELI (fixed-delay) degil.

    Eski davranis:

        await _tick()
        await asyncio.sleep(TICK_SECONDS)

    Bu, periyodu "tick suresi + TICK_SECONDS" yapiyordu.
    Gecikme TELAFI EDILMIYORDU: bir tick 3 saniye
    gecikirse sonraki BUTUN tick'ler kalici olarak 3
    saniye ileri kayiyordu. Kayma birikimliydi ve ust
    siniri yoktu; TICK_SECONDS=60 iken bu, planlanan
    saatin dakikalarca kacirilmasi demekti.

    Yeni davranis: bir sonraki tick'in ne zaman olmasi
    GEREKTIGI monotonik saat uzerinde tutulur. Tick ne
    kadar surerse sursun, sonraki tick hedef anina
    planlanir; gecikme bir sonraki tick'e TASINMAZ.

    Cok gecikilmisse (uzun bir aksama, host'un askiya
    alinmasi, saat sicramasi) kacirilan slotlar icin
    ardi ardina tick PATLATILMAZ; hedef bir sonraki
    siniri gosterecek sekilde yeniden hizalanir.
    Kacirilan bir calistirma kaybolmaz: due
    degerlendirmesi last_checked_at'e bakar, tick
    sayisina degil.
    """

    logger.info(
        "Scheduler dongusu calisiyor. tick=%s saniye timezone=%s",
        TICK_SECONDS,
        settings.app_timezone,
    )

    loop = asyncio.get_running_loop()

    # Bir sonraki tick'in hedef ani (monotonik saat).
    next_tick = loop.time()

    while True:

        started = loop.time()

        try:
            await _tick()

        except asyncio.CancelledError:
            raise

        except Exception:
            logger.exception(
                "Scheduler tick hatasi."
            )

        duration = loop.time() - started

        if duration > SLOW_TICK_SECONDS:
            # Tick'in kendisi uzun surduyse due
            # degerlendirmesi gecikiyor demektir.
            logger.warning(
                "Scheduler: tick yavas. sure=%.2fs esik=%ss",
                duration,
                SLOW_TICK_SECONDS,
            )

        next_tick += TICK_SECONDS

        now = loop.time()

        if next_tick <= now:

            behind = now - next_tick

            missed = int(behind // TICK_SECONDS) + 1

            next_tick += missed * TICK_SECONDS

            # Gozlenen gecikmenin acik izi. Bu satir
            # gorunuyorsa dongu planlanan tick anini
            # kacirmis demektir.
            logger.warning(
                "Scheduler: tick gecikti, yeniden hizalandi. "
                "gecikme=%.1fs atlanan_slot=%s",
                behind + TICK_SECONDS,
                missed,
            )

        await asyncio.sleep(next_tick - now)


def _ensure_logging() -> None:
    """
    Uvicorn root logger'a handler eklemediği için
    scheduler INFO logları görünmeyebilir.
    """

    if logger.handlers or logging.getLogger().handlers:
        return

    handler = logging.StreamHandler()

    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
        )
    )

    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


async def start_scheduler() -> None:
    """
    FastAPI lifespan içinde tek bir scheduler başlatır.
    """

    global _scheduler_task

    _ensure_logging()

    if (
        _scheduler_task is not None
        and not _scheduler_task.done()
    ):
        logger.warning(
            "Scheduler zaten calisiyor. Yeni kopya baslatilmadi."
        )
        return

    _scheduler_task = asyncio.create_task(
        _scheduler_loop()
    )

    logger.info(
        "Scheduler baslatildi."
    )


async def stop_scheduler() -> None:
    """
    Scheduler döngüsünü ve devam eden monitor
    çalıştırmalarını durdurur.
    """

    global _scheduler_task

    if _scheduler_task is not None:

        _scheduler_task.cancel()

        try:
            await _scheduler_task

        except asyncio.CancelledError:
            pass

        _scheduler_task = None

    running_tasks = list(_monitor_tasks)

    for task in running_tasks:
        task.cancel()

    if running_tasks:
        await asyncio.gather(
            *running_tasks,
            return_exceptions=True,
        )

    logger.info(
        "Scheduler durduruldu."
    )
