"""
Bildirimler ekrani frontend dogrulamasi.

NOT: Projede bir JS test kosucusu (vitest/jest) kurulu degil ve
bu sprintte yeni paket kurulmuyor. Bu yuzden asagidakiler
CALISMA ZAMANI component testi degil, KAYNAK KODU uzerinde
yapilan statik dogrulamalardir.

Calisma zamani dogrulamasi icin `npm run build` ve tarayici
kullanilir.

Calistirma:
    python -m tests.test_notifications_frontend
"""

import io
import re

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]

APP_JSX = PROJECT_ROOT / "frontend" / "src" / "App.jsx"
API_JS = PROJECT_ROOT / "frontend" / "src" / "services" / "api.js"
INDEX_CSS = PROJECT_ROOT / "frontend" / "src" / "index.css"


PASSED = []
FAILED = []


def check(label: str, condition: bool, detail: str = "") -> None:

    status = "PASS" if condition else "FAIL"

    print(f"[{status}] {label}" + (f" -> {detail}" if detail else ""))

    if condition:
        PASSED.append(label)
    else:
        FAILED.append(f"{label} -> {detail}")


def read(path: Path) -> str:
    return io.open(path, encoding="utf-8").read()


def extract_notifications_page(source: str) -> str:
    """
    NotificationsPage fonksiyonunun govdesini kabaca cikarir.
    """

    start = source.index("function NotificationsPage(")
    end = source.index("function SidebarLink(", start)

    return source[start:end]


def main() -> None:

    print("=" * 70)
    print("BILDIRIMLER EKRANI FRONTEND DOGRULAMASI")
    print("(statik kaynak kodu kontrolu)")
    print("=" * 70)

    app_source = read(APP_JSX)
    api_source = read(API_JS)
    css_source = read(INDEX_CSS)

    page = extract_notifications_page(app_source)

    # -----------------------------------------------------
    # API KULLANIMI
    # -----------------------------------------------------

    print("\n--- api kullanimi ---")

    check(
        "api.js icinde getNotifications tanimli",
        "export async function getNotifications()" in api_source,
    )

    check(
        "getNotifications /api/notifications adresini cagiriyor",
        "/api/notifications" in api_source,
    )

    check(
        "App.jsx getNotifications fonksiyonunu import ediyor",
        re.search(
            r"import\s*\{[^}]*getNotifications[^}]*\}\s*from\s*\"\./services/api\"",
            app_source,
            re.DOTALL,
        )
        is not None,
    )

    check(
        "NotificationsPage getNotifications cagiriyor",
        "await getNotifications()" in page,
    )

    check(
        "veri useEffect icinde yukleniyor",
        "useEffect(" in page,
    )

    # -----------------------------------------------------
    # DURUMLAR
    # -----------------------------------------------------

    print("\n--- loading / empty / error ---")

    check(
        "loading state var",
        "setLoading(true)" in page
        and "Bildirimler yükleniyor..." in page,
    )

    check(
        "error state var",
        "setError(" in page
        and "Bildirimler yüklenemedi" in page,
    )

    check(
        "empty state var",
        "notifications.length === 0" in page
        and "Henüz bildirim yok" in page,
    )

    check(
        "veri state'i var",
        "notifications.length > 0" in page
        and "notifications.map(" in page,
    )

    check(
        "hata durumunda liste gosterilmiyor",
        "!error &&" in page,
    )

    # -----------------------------------------------------
    # GERCEK ALANLAR
    # -----------------------------------------------------

    print("\n--- backend alanlari kullaniliyor ---")

    for field in [
        "notification.monitor_name",
        "notification.title",
        "notification.message",
        "notification.importance",
        "notification.category",
        "notification.created_at",
    ]:
        check(
            f"{field} kartta kullaniliyor",
            field in page,
        )

    check(
        "liste key olarak gercek id kullaniyor",
        "key={notification.id}" in page,
    )

    # -----------------------------------------------------
    # MOCK / STATIC VERI KALMADI
    # -----------------------------------------------------

    print("\n--- mock / static veri ---")

    check(
        "eski statik placeholder metni kaldirildi",
        "Henüz gösterilecek bildirim bulunmuyor" not in app_source,
    )

    check(
        "NotificationsPage icinde sabit dizi yok",
        not re.search(r"=\s*\[\s*\{", page),
    )

    for word in ["mock", "dummy", "fakeNotification", "sampleNotification"]:
        check(
            f"'{word}' gecmiyor",
            word.lower() not in page.lower(),
        )

    # -----------------------------------------------------
    # TASARIM DILI
    # -----------------------------------------------------

    print("\n--- mevcut tasarim dili ---")

    for css_class in [
        "page-header",
        "placeholder-panel",
        "empty-state",
        "form-panel",
    ]:
        check(
            f"mevcut '{css_class}' sinifi yeniden kullanilmis",
            css_class in page,
        )

    for css_class in [
        ".notification-badge",
        ".notification-message",
        ".notification-meta",
        ".notification-tag",
        ".notification-heading-text",
    ]:
        check(
            f"{css_class} index.css icinde tanimli",
            css_class in css_source,
        )

    # -----------------------------------------------------
    # TARIH YORUMLAMASI
    # -----------------------------------------------------

    print("\n--- tarih ---")

    check(
        "naive UTC timestamp UTC olarak parse ediliyor",
        "function parseServerDate(" in app_source
        and "`${value}Z`" in app_source,
    )

    check(
        "kartta formatDateTime kullaniliyor",
        "formatDateTime(" in page,
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
