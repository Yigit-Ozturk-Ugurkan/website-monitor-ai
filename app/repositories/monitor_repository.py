from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.monitor import Monitor


def resolve_notification_type(
    notify_in_app: bool,
    notify_email: bool,
) -> str:
    """
    Eski yapiyla uyumluluk icin tutulan notification_type
    kolonunu bildirim bayraklarindan turetir.
    """

    if notify_in_app and notify_email:
        return "both"

    if notify_email:
        return "email"

    if notify_in_app:
        return "in_app"

    return "none"


def create_monitor(
    db: Session,
    name: str,
    url: str,
    topic: str,
    tracking_plan: str = "website_monitor",
    reference_selection_mode: str = "manual",
    reference_regulation_id: int | None = None,
    scope_type: str = "auto_section",
    schedule_type: str = "daily",
    schedule_time: str | None = "09:00",
    start_date: date | None = None,
    end_date: date | None = None,
    is_unlimited: bool = True,
    notify_in_app: bool = True,
    notify_email: bool = False,
    notification_email: str | None = None,
    manual_section_reference: str | None = None,
    manual_section_label: str | None = None,
) -> Monitor:

    notification_type = resolve_notification_type(
        notify_in_app=notify_in_app,
        notify_email=notify_email,
    )

    monitor = Monitor(
        name=name,
        url=url,
        topic=topic,

        # Varsayilan website_monitor'dur; boylece bu
        # parametreyi hic gondermeyen eski cagrilar
        # mevcut davranisi aynen surdurur.
        tracking_plan=tracking_plan,

        # Referansi kim secer?
        #
        # Varsayilan "manual"dir: bu parametreyi hic
        # gondermeyen eski cagrilar mevcut davranisi
        # (kullanicinin sectigi referans) surdurur.
        reference_selection_mode=reference_selection_mode,

        # website_monitor kayitlarinda None kalir.
        reference_regulation_id=reference_regulation_id,

        scope_type=scope_type,

        # manual_section disinda None kalir.
        # auto_section alanlari ilk calistirmada pipeline
        # tarafindan doldurulur.
        manual_section_reference=manual_section_reference,
        manual_section_label=manual_section_label,

        schedule_type=schedule_type,
        schedule_time=schedule_time,

        start_date=start_date,
        end_date=end_date,
        is_unlimited=is_unlimited,

        notify_in_app=notify_in_app,
        notify_email=notify_email,
        notification_email=notification_email,

        # Eski yapıyla uyumluluk için
        notification_type=notification_type,
        notification_target=notification_email,

        is_active=True,
    )

    db.add(monitor)
    db.commit()
    db.refresh(monitor)

    return monitor


def get_monitor(
    db: Session,
    monitor_id: int,
) -> Monitor | None:

    return db.get(
        Monitor,
        monitor_id,
    )


def get_monitors(
    db: Session,
) -> list[Monitor]:

    statement = (
        select(Monitor)
        .order_by(
            Monitor.created_at.desc(),
            Monitor.id.desc(),
        )
    )

    return list(
        db.scalars(statement).all()
    )


def get_active_monitors(
    db: Session,
) -> list[Monitor]:

    statement = (
        select(Monitor)
        .where(
            Monitor.is_active.is_(True)
        )
        .order_by(
            Monitor.id
        )
    )

    return list(
        db.scalars(statement).all()
    )


# ---------------------------------------------------------
# AUTO SECTION SECIMI
#
# Secilmis bolum monitor uzerinde kalicidir; boylece her
# kontrolde Gemini'ye yeniden bolum sorulmaz.
# ---------------------------------------------------------

# Degistiginde kaydedilmis bolum secimini gecersiz kilan alanlar.
#
# topic:      secim eski konuya gore yapilmisti.
# url:        artik bambaska bir sayfa izleniyor.
# scope_type: kapsam degisti, secim yeniden yapilmali.
AUTO_SECTION_RESET_FIELDS = (
    "topic",
    "url",
    "scope_type",
)


def set_auto_section_selection(
    db: Session,
    monitor: Monitor,
    reference: str,
    label: str | None,
) -> Monitor:
    """
    Secilen bolumu monitor uzerine kaydeder.
    """

    monitor.auto_section_reference = reference
    monitor.auto_section_label = label

    db.commit()
    db.refresh(monitor)

    return monitor


def clear_auto_section_selection(
    db: Session,
    monitor: Monitor,
) -> Monitor:
    """
    Kaydedilmis bolum secimini temizler.

    Sonraki calistirmada yeniden bolum kesfi yapilir.
    """

    monitor.auto_section_reference = None
    monitor.auto_section_label = None

    db.commit()
    db.refresh(monitor)

    return monitor


# ---------------------------------------------------------
# MANUAL SECTION SECIMI
#
# Bolumu KULLANICI secer. Bu yuzden auto_section'dan farkli
# davranir:
#
#   - topic degisimi secimi GECERSIZ KILMAZ.
#     Secim konuya degil kullanici tercihine baglidir.
#
#   - url degisimi secimi GECERSIZ KILAR.
#     Referans o sayfaya aitti; baska bir sayfada
#     sessizce kullanilamaz.
#
#   - scope_type degisimi secimi GECERSIZ KILAR.
# ---------------------------------------------------------

MANUAL_SECTION_RESET_FIELDS = (
    "url",
    "scope_type",
)


def set_manual_section_selection(
    db: Session,
    monitor: Monitor,
    reference: str,
    label: str | None,
) -> Monitor:
    """
    Kullanicinin sectigi bolumu monitor uzerine kaydeder.
    """

    monitor.manual_section_reference = reference
    monitor.manual_section_label = label

    db.commit()
    db.refresh(monitor)

    return monitor


def clear_manual_section_selection(
    db: Session,
    monitor: Monitor,
) -> Monitor:
    """
    Kaydedilmis manuel bolum secimini temizler.

    Yeniden secim KULLANICIDAN istenir; sistem kendi
    basina yeni bir bolum secmez.
    """

    monitor.manual_section_reference = None
    monitor.manual_section_label = None

    db.commit()
    db.refresh(monitor)

    return monitor


# ---------------------------------------------------------
# ORTAK RESET MANTIGI
# ---------------------------------------------------------

def _should_reset(
    monitor: Monitor,
    updates: dict,
    reset_fields: tuple[str, ...],
) -> bool:
    """
    Bu guncelleme kaydedilmis bolum secimini gecersiz kiliyor mu?

    Yalnizca deger GERCEKTEN degistiyse true doner; ayni
    degerin tekrar gonderilmesi secimi bosuna silmez.
    """

    for field in reset_fields:

        if field not in updates:
            continue

        if updates[field] != getattr(monitor, field):
            return True

    return False


def should_reset_auto_section(
    monitor: Monitor,
    updates: dict,
) -> bool:

    return _should_reset(
        monitor=monitor,
        updates=updates,
        reset_fields=AUTO_SECTION_RESET_FIELDS,
    )


def should_reset_manual_section(
    monitor: Monitor,
    updates: dict,
) -> bool:

    return _should_reset(
        monitor=monitor,
        updates=updates,
        reset_fields=MANUAL_SECTION_RESET_FIELDS,
    )


# ---------------------------------------------------------
# REFERANS YONETMELIK SECIMI
#
# Referansi KULLANICI secer ve secim TAKIP PLANINA baglidir.
#
#   - tracking_plan degisimi secimi GECERSIZ KILAR.
#
#     regulation -> website gecisinde referans temizlenir;
#     boylece standart bir web izlemesinde artik anlami
#     olmayan bir referans sessizce asili kalmaz.
#
#     website -> regulation gecisinde de temizlenir; bu
#     durumda birlesik dogrulama "referans secilmelidir"
#     hatasi verir ve kullanici bilerek secim yapar.
#
#   - url / topic / scope_type degisimi secimi BOZMAZ.
#     Referans izlenen sayfaya degil, mevzuata aittir.
# ---------------------------------------------------------

REGULATION_REFERENCE_RESET_FIELDS = (
    "tracking_plan",

    # Mod degisimi de secimi gecersiz kilar:
    #
    #   manual -> automatic
    #     referans artik her calistirmada belirlenecek;
    #     eski secim asili kalmaz.
    #
    #   automatic -> manual
    #     kullanici bilerek bir referans secmelidir;
    #     birlesik dogrulama bunu zorunlu kilar.
    "reference_selection_mode",
)


def should_reset_regulation_reference(
    monitor: Monitor,
    updates: dict,
) -> bool:

    return _should_reset(
        monitor=monitor,
        updates=updates,
        reset_fields=REGULATION_REFERENCE_RESET_FIELDS,
    )


def update_monitor(
    db: Session,
    monitor: Monitor,
    updates: dict,
) -> Monitor:
    """
    Monitor uzerinde partial update uygular.

    Yalnizca updates icinde gelen alanlar degistirilir.
    created_at ve last_checked_at buraya hic ulasmaz.

    Secimler birbirinden BAGIMSIZ temizlenir:

      auto_section         : topic / url / scope_type degisince
      manual_section       : url / scope_type degisince
                             (topic degisimi secimi bozmaz)
      regulation reference : tracking_plan degisince
                             (url / topic / scope_type
                              secimi bozmaz)

    Guncellemede ACIKCA yeni bir secim gonderildiyse o
    secim korunur; reset yalnizca gonderilmemis alanlari
    temizler.
    """

    reset_auto_section = should_reset_auto_section(
        monitor=monitor,
        updates=updates,
    )

    reset_manual_section = should_reset_manual_section(
        monitor=monitor,
        updates=updates,
    )

    reset_regulation_reference = (
        should_reset_regulation_reference(
            monitor=monitor,
            updates=updates,
        )
    )

    for field, value in updates.items():
        setattr(monitor, field, value)

    if reset_auto_section:
        if "auto_section_reference" not in updates:
            monitor.auto_section_reference = None

        if "auto_section_label" not in updates:
            monitor.auto_section_label = None

    if reset_manual_section:
        if "manual_section_reference" not in updates:
            monitor.manual_section_reference = None

        if "manual_section_label" not in updates:
            monitor.manual_section_label = None

    if reset_regulation_reference:
        if "reference_regulation_id" not in updates:
            monitor.reference_regulation_id = None

    # Eski yapiyla uyumluluk icin tutulan alanlari
    # guncel bayraklarla tutarli birak.
    monitor.notification_type = resolve_notification_type(
        notify_in_app=monitor.notify_in_app,
        notify_email=monitor.notify_email,
    )

    monitor.notification_target = monitor.notification_email

    db.commit()
    db.refresh(monitor)

    return monitor


def update_last_checked(
    db: Session,
    monitor: Monitor,
) -> None:

    monitor.last_checked_at = datetime.utcnow()

    db.commit()
    db.refresh(monitor)


def update_last_run_status(
    db: Session,
    monitor: Monitor,
    status: str,
    message: str,
) -> None:
    """
    Son calistirma denemesinin sonucunu yazar.

    last_checked_at'e DOKUNMAZ. Basarisiz denemeler de
    buraya yazildigi icin iki alan ayri tutulur:

        last_checked_at : son BASARILI kontrol
        last_run_at     : son DENEME

    Boylece siteye erisilemeyen bir calistirma karta
    yansir ama scheduler'in retry karari degismez.
    """

    monitor.last_run_status = status
    monitor.last_run_message = message
    monitor.last_run_at = datetime.utcnow()

    db.commit()
    db.refresh(monitor)