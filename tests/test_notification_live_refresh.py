"""
BILDIRIM CANLI TAZELEME DOGRULAMASI

SINIF: automated (deterministic, CI-safe)

GERCEK KULLANICI KABULUNDE GORULEN HATA

  1. "Şimdi Kontrol Et" -> degisiklik bulundu
  2. backend bildirimi OLUSTURDU
  3. arayuz "bildirim oluşturuldu" dedi
  4. AMA zil rozeti 2'de KALDI
  5. Bildirimler sayfasina girilince 3 oldu

  Yani veritabani ve backend dogruydu; frontend'in global
  okunmamis sayaci BAYAT kaliyordu.

  Kok neden: sayac YALNIZCA route degisiminde
  (location.pathname) yeniden okunuyordu. Manuel
  calistirmadan sonra hicbir sey onu gecersiz kilmiyordu
  ve arka plan yoklamasi da yoktu.

Kapsam:

     1. acilista okunmamis sayaci cekilir
     2. bildirim olusmayan calistirma istek ATMAZ
     3. bildirim olusturan calistirma sayaci HEMEN tazeler
     4. route degisimi GEREKMEZ
     5. sayac tazelemesi calistirma arayuzunu BOZMAZ
     6. arka plan yoklamasi sayac degisimini yakalar
     7. Bildirimler ekrani aciksa liste tazelenir
     8. yoklama zamanlayicisi COGALTILMAZ
     9. unmount'ta temizlenir
    10. sekme gizliyken yoklama durur
    11. okundu isaretleme sayaci tazeler
    12. bildirim detayi sayac ile tutarli
    13. yoklama yalnizca in_app sayaci icindir
    14. cok agresif yoklama YOK
    15. ust uste binen istek engellenir
    16. yeni state kutuphanesi EKLENMEDI
    17. WebSocket / SSE EKLENMEDI

NOT: Projede bir JS test kosucusu (vitest/jest) kurulu
degil ve yeni paket kurulmuyor. Asagidakiler CALISMA
ZAMANI component testi degil, KAYNAK KODU uzerinde
yapilan statik dogrulamalardir. Bu, mevcut frontend
testleriyle ayni yaklasimdir.

Backend tarafi da okunur: yoklamanin kullandigi endpoint
ve calistirma yanitindaki alan GERCEKTEN var mi?

Veritabani kullanilmaz, ag istegi yapilmaz.

Calistirma:
    python -m tests.test_notification_live_refresh
"""

import io
import re

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]

APP_JSX = PROJECT_ROOT / "frontend" / "src" / "App.jsx"
API_JS = PROJECT_ROOT / "frontend" / "src" / "services" / "api.js"

NOTIFICATION_ROUTES = (
    PROJECT_ROOT / "app" / "api" / "notification_routes.py"
)
MONITOR_SCHEMA = (
    PROJECT_ROOT / "app" / "schemas" / "monitor.py"
)


PASSED = []
FAILED = []


# Yoklama araligi icin makul sinirlar.
#
# Cok sik: backend'e gereksiz yuk.
# Cok seyrek: kullanici yeni bildirimi fark etmez.
MIN_POLL_MS = 15000
MAX_POLL_MS = 60000


def check(label: str, condition: bool, detail: str = "") -> None:

    status = "PASS" if condition else "FAIL"

    print(f"[{status}] {label}" + (f" -> {detail}" if detail else ""))

    if condition:
        PASSED.append(label)
    else:
        FAILED.append(f"{label} -> {detail}")


def read(path: Path) -> str:
    return io.open(path, encoding="utf-8").read()


def block(source: str, start: str, end: str) -> str:
    """
    Iki isaret arasindaki bolumu dondurur.

    Bitis isareti bolumun ICINDE de gecebilir (ornegin
    "return (" bir temizleme fonksiyonunun icinde). Bu
    yuzden cagiran taraf yeterince ayirt edici bir isaret
    vermelidir.
    """

    begin = source.index(start)
    after = begin + len(start)

    return source[begin : source.index(end, after)]


def main() -> None:

    print("=" * 70)
    print("BILDIRIM CANLI TAZELEME DOGRULAMASI")
    print("=" * 70)

    app_source = read(APP_JSX)
    api_source = read(API_JS)
    routes_source = read(NOTIFICATION_ROUTES)
    schema_source = read(MONITOR_SCHEMA)

    # -----------------------------------------------------
    # 1. ACILIS
    # -----------------------------------------------------

    print()
    print("--- acilis ---")

    check(
        "1. okunmamis sayaci icin ortak fonksiyon var",
        "refreshUnreadCount" in app_source
        and "getUnreadNotificationCount" in app_source,
    )

    check(
        "1. route degisiminde sayac okunur",
        "location.pathname" in app_source
        and "loadUnreadCount" in app_source,
    )

    # -----------------------------------------------------
    # 2-5. MANUEL CALISTIRMA HIZLI YOLU
    # -----------------------------------------------------

    print()
    print("--- manuel calistirma hizli yolu ---")

    check(
        "3. calistirma sonrasi geri cagri var",
        "onRunCompleted" in app_source
        and "handleRunCompleted" in app_source,
    )

    fast_path = block(
        app_source,
        "const handleRunCompleted",
        "// App ilk mount",
    )

    check(
        "3. hizli yol sayaci tazeler",
        "refreshUnreadCount()" in fast_path,
    )

    check(
        "2. bildirim olusmadiysa istek ATILMAZ",
        "notification_created" in fast_path
        and "return;" in fast_path,
        "no-change calistirmasi bosuna GET uretmez",
    )

    check(
        "4. route degisimi gerekmez "
        "(dogrudan state guncellenir)",
        "setNotificationsVersion" in fast_path,
    )

    # Calistirma sonucunu bozmamali.
    run_handler = block(
        app_source,
        "async function handleRunMonitor",
        "async function handleToggleActive",
    )

    check(
        "3. calistirma yaniti geri cagriya iletilir",
        "onRunCompleted(result)" in run_handler,
    )

    check(
        "5. sayac tazelemesi try/catch ile yalitilmis",
        "onRunCompleted(result)" in run_handler
        and run_handler.count("try {") >= 2,
        "run sonucu bozulmamali",
    )

    check(
        "5. sayac hatasi rozeti sifirlamaz",
        "onceki rozet degeri korunur" in app_source,
    )

    # -----------------------------------------------------
    # 6, 8-10, 14-15. YOKLAMA
    # -----------------------------------------------------

    print()
    print("--- arka plan yoklamasi ---")

    check(
        "6. yoklama zamanlayicisi var",
        "UNREAD_POLL_INTERVAL_MS" in app_source
        and "setInterval" in app_source,
    )

    interval_match = re.search(
        r"UNREAD_POLL_INTERVAL_MS\s*=\s*(\d+)",
        app_source,
    )

    interval = (
        int(interval_match.group(1))
        if interval_match
        else None
    )

    check(
        "14. yoklama araligi makul",
        interval is not None
        and MIN_POLL_MS <= interval <= MAX_POLL_MS,
        f"{interval} ms",
    )

    # Temizleme fonksiyonu "return () => {" iceriyor;
    # bitis isareti bilesenin JSX donusu olmalidir.
    poll_block = block(
        app_source,
        "// ARKA PLAN YOKLAMASI",
        '<div className="app-shell">',
    )

    check(
        "8. ayni anda ikinci zamanlayici kurulmaz",
        "if (timerId !== null)" in poll_block
        and "return;" in poll_block,
    )

    check(
        "9. unmount'ta zamanlayici temizlenir",
        "clearInterval" in poll_block
        and "stopPolling()" in poll_block,
    )

    check(
        "9. olay dinleyicisi de kaldirilir",
        "removeEventListener" in poll_block,
    )

    check(
        "10. sekme gizliyken yoklama durur",
        "document.hidden" in poll_block
        and "visibilitychange" in poll_block,
    )

    check(
        "10. sekme geri gelince hemen okunur",
        "refreshUnreadCount();" in poll_block,
    )

    check(
        "15. ust uste binen istek engellenir",
        "unreadRequestRef" in app_source,
    )

    # -----------------------------------------------------
    # 7. LISTE TAZELEME
    # -----------------------------------------------------

    print()
    print("--- bildirim listesi tazeleme ---")

    check(
        "7. Bildirimler ekrani tazeleme sinyali aliyor",
        "refreshSignal" in app_source
        and "notificationsVersion" in app_source,
    )

    # Ic fonksiyonlar da "function " ile basliyor; yalnizca
    # SATIR BASINDAKI tanim bir sonraki bilesendir.
    notifications_page = block(
        app_source,
        "function NotificationsPage",
        chr(10) + "function ",
    )

    check(
        "7. liste efekti sinyale bagli",
        "[refreshSignal]" in notifications_page,
    )

    check(
        "7. sayac degisimi sinyali yukseltir",
        "lastUnreadRef" in app_source,
        "scheduler'in urettigi bildirim boyle yakalanir",
    )

    # -----------------------------------------------------
    # 11-12. OKUNDU / DETAY
    # -----------------------------------------------------

    print()
    print("--- okundu / detay ---")

    check(
        "11. okundu isaretleme sayaci tazeler",
        "onNotificationRead={refreshUnreadCount}" in app_source,
    )

    check(
        "12. detay ekrani geri cagriyi kullaniyor",
        "onNotificationRead" in app_source
        and "NotificationDetailPage" in app_source,
    )

    # -----------------------------------------------------
    # 13. KAPSAM
    # -----------------------------------------------------

    print()
    print("--- kapsam ---")

    check(
        "13. sayac yalnizca in_app kayitlarini sayar",
        "IN_APP_NOTIFICATION_QUERY" in api_source
        and "notification_type=in_app" in api_source,
    )

    unread_api = block(
        api_source,
        "export async function getUnreadNotificationCount",
        "export async function",
    )

    check(
        "13. unread-count in_app filtresiyle cagriliyor",
        "IN_APP_NOTIFICATION_QUERY" in unread_api,
    )

    check(
        "13. e-posta icin yoklama YOK",
        "notification_type=email" not in app_source,
    )

    # -----------------------------------------------------
    # 16-17. YASAKLAR
    # -----------------------------------------------------

    print()
    print("--- yasaklar ---")

    # Aciklama satirinda gecmesi serbest; ARANAN sey
    # gercek kullanimdir.
    for label, pattern in (
        ("WebSocket", r"new\s+WebSocket\s*\("),
        ("EventSource", r"new\s+EventSource\s*\("),
        ("socket.io", r"socket\.io"),
    ):
        check(
            f"17. {label} KULLANIMI yok",
            re.search(pattern, app_source) is None
            and re.search(pattern, api_source) is None,
            "yalnizca aciklama satirinda gecmesi serbest",
        )

    package_json = read(
        PROJECT_ROOT / "frontend" / "package.json"
    )

    for library in (
        "redux",
        "zustand",
        "jotai",
        "recoil",
        "react-query",
        "@tanstack/react-query",
        "swr",
    ):
        check(
            f"16. {library} eklenmedi",
            library not in package_json,
        )

    # -----------------------------------------------------
    # BACKEND SOZLESMESI
    # -----------------------------------------------------

    print()
    print("--- backend sozlesmesi ---")

    check(
        "unread-count endpoint'i mevcut",
        "unread-count" in routes_source,
    )

    check(
        "unread-count notification_type filtresi kabul eder",
        "notification_type" in routes_source,
    )

    check(
        "calistirma yaniti notification_created tasiyor",
        "notification_created" in schema_source,
    )

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
    main()
