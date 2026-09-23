"""
Okundu/okunmadi ve Bell badge frontend dogrulamasi.

NOT: Projede bir JS test kosucusu (vitest/jest) kurulu degil ve
bu sprintte yeni paket kurulmuyor. Asagidakiler CALISMA ZAMANI
component testi degil, KAYNAK KODU uzerinde yapilan statik
dogrulamalardir.

Calistirma:
    python -m tests.test_notification_read_frontend
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


def section(source: str, start_marker: str, end_marker: str) -> str:
    start = source.index(start_marker)
    end = source.index(end_marker, start)

    return source[start:end]


def _poll_interval_ms(source: str) -> int | None:
    """
    Yoklama araligini kaynaktan okur.
    """

    match = re.search(
        r"UNREAD_POLL_INTERVAL_MS\s*=\s*(\d+)",
        source,
    )

    return int(match.group(1)) if match else None


def main() -> None:

    print("=" * 70)
    print("OKUNDU / BELL BADGE FRONTEND DOGRULAMASI")
    print("(statik kaynak kodu kontrolu)")
    print("=" * 70)

    app_source = read(APP_JSX)
    api_source = read(API_JS)
    css_source = read(INDEX_CSS)

    notifications_page = section(
        app_source,
        "function NotificationsPage(",
        "function ContextBlockView(",
    )

    detail_page = section(
        app_source,
        "function NotificationDetailPage(",
        "function SidebarLink(",
    )

    app_component = section(
        app_source,
        "function App() {",
        "export default App",
    ) if "export default App" in app_source else app_source[
        app_source.index("function App() {"):
    ]

    # -----------------------------------------------------
    # 1. UNREAD COUNT API
    # -----------------------------------------------------

    print("\n--- 1. unread count API ---")

    check(
        "api.js icinde getUnreadNotificationCount tanimli",
        "export async function getUnreadNotificationCount()"
        in api_source,
    )

    check(
        "dogru adrese gidiyor",
        "/api/notifications/unread-count" in api_source,
    )

    check(
        "api.js icinde markNotificationAsRead tanimli",
        "export async function markNotificationAsRead(notificationId)"
        in api_source,
    )

    check(
        "markNotificationAsRead PATCH kullaniyor",
        re.search(
            r"markNotificationAsRead[\s\S]{0,300}method:\s*\"PATCH\"",
            api_source,
        )
        is not None,
    )

    check(
        "read endpoint adresi dogru",
        "/api/notifications/${notificationId}/read" in api_source,
    )

    for existing in [
        "getMonitors",
        "getMonitor",
        "createMonitor",
        "updateMonitor",
        "runMonitor",
        "getNotifications",
        "getNotificationDetail",
    ]:
        check(
            f"mevcut {existing} bozulmadi",
            f"export async function {existing}(" in api_source,
        )

    check(
        "App unread count API'sini kullaniyor",
        "await getUnreadNotificationCount()" in app_component,
    )

    check(
        "sayac frontend'de hesaplanmiyor (backend source of truth)",
        "data.count" in app_component
        and "notifications.filter(" not in app_component,
    )

    # -----------------------------------------------------
    # 2-3. BELL BADGE
    # -----------------------------------------------------

    print("\n--- 2-3. bell badge ---")

    check(
        "unreadCount state'i var",
        "const [unreadCount, setUnreadCount] = useState(0);"
        in app_component,
    )

    check(
        "count > 0 iken badge gosteriliyor",
        "{unreadCount > 0 && (" in app_component
        and "bell-badge" in app_component,
    )

    check(
        "count = 0 iken badge gosterilmiyor (kosullu render)",
        re.search(
            r"\{unreadCount > 0 && \([\s\S]{0,200}bell-badge",
            app_component,
        )
        is not None,
    )

    check(
        "99+ kisaltmasi var",
        '"99+"' in app_component,
    )

    check(
        "bell-badge stili tanimli",
        ".bell-badge" in css_source,
    )

    check(
        "badge erisilebilir (aria-label sayiyi iceriyor)",
        "okunmamış" in app_component
        and "aria-label" in app_component,
    )

    # -----------------------------------------------------
    # 4. BELL -> /notifications
    # -----------------------------------------------------

    print("\n--- 4. bell linki ---")

    check(
        "Bell artik gercek link (NavLink)",
        re.search(
            r"<NavLink\s+to=\"/notifications\"[\s\S]{0,200}bell-button",
            app_component,
        )
        is not None,
    )

    check(
        "mevcut icon-button sinifi korunmus",
        "icon-button bell-button" in app_component,
    )

    check(
        "sidebar Bildirimler linki korunmus",
        '<SidebarLink\n            to="/notifications"' in app_component
        or 'to="/notifications"' in app_component,
    )

    check(
        "topbar yapisi korunmus",
        'className="topbar"' in app_component
        and 'className="topbar-title"' in app_component,
    )

    # -----------------------------------------------------
    # 5-6. LISTEDE YENI GOSTERGESI
    # -----------------------------------------------------

    print("\n--- 5-6. listede 'Yeni' gostergesi ---")

    check(
        "okunmamis kartta 'Yeni' etiketi var",
        "{!notification.is_read && (" in notifications_page
        and "Yeni" in notifications_page,
    )

    check(
        "okunmus kartta 'Yeni' gosterilmiyor (kosullu)",
        re.search(
            r"\{!notification\.is_read && \([\s\S]{0,200}unread-tag",
            notifications_page,
        )
        is not None,
    )

    check(
        "gosterge metinsel (sadece renk degil)",
        "unread-tag" in notifications_page
        and ">\n                      Yeni\n" in notifications_page,
    )

    check(
        "okunmamis kart gorsel olarak da ayrisiyor",
        "notification-item unread" in notifications_page
        and ".notification-item.unread" in css_source,
    )

    check(
        "unread-tag stili tanimli",
        ".unread-tag" in css_source,
    )

    # -----------------------------------------------------
    # 7-8. DETAIL FLOW
    # -----------------------------------------------------

    print("\n--- 7-8. detay akisi ---")

    check(
        "detay acilinca markNotificationAsRead cagriliyor",
        "await markNotificationAsRead(" in detail_page,
    )

    check(
        "yalnizca okunmamis kayitta cagriliyor",
        "if (data.is_read === false) {" in detail_page,
    )

    check(
        "yerel state is_read=true yapiliyor",
        "is_read: true" in detail_page,
    )

    check(
        "read sonrasi sayac yeniden cekiliyor",
        "onNotificationRead()" in detail_page,
    )

    check(
        "sayac refresh fonksiyonu App'ten geciyor",
        "onNotificationRead={refreshUnreadCount}" in app_component,
    )

    check(
        "refreshUnreadCount backend'e gidiyor",
        re.search(
            r"refreshUnreadCount = useCallback\([\s\S]{0,300}"
            r"getUnreadNotificationCount\(\)",
            app_component,
        )
        is not None,
    )

    # -----------------------------------------------------
    # 9. MARK-READ HATASI ENGELLEMEZ
    # -----------------------------------------------------

    print("\n--- 9. read hatasi engellemiyor ---")

    check(
        "read hatasi ayri state'te tutuluyor",
        "readError" in detail_page
        and "setReadError(" in detail_page,
    )

    check(
        "read hatasi detay gosterimini engellemiyor",
        "!loading && !error && detail && (" in detail_page
        and "readError &&" in detail_page,
    )

    check(
        "read hatasi ana error state'ini kirletmiyor",
        re.search(
            r"catch \(readError\) \{[\s\S]{0,200}setReadError\(",
            detail_page,
        )
        is not None,
    )

    check(
        "read-error stili tanimli",
        ".read-error" in css_source,
    )

    # -----------------------------------------------------
    # SAYAC YENILEME
    # -----------------------------------------------------

    print("\n--- sayac yenileme ---")

    check(
        "route degisiminde sayac yenileniyor",
        "location.pathname" in app_component
        and "useLocation()" in app_component,
    )

    # ARKA PLAN YOKLAMASI ARTIK VAR
    #
    # Bu test eskiden "polling YOK" iddia ediyordu. O
    # zamanki tasarim karariydi; ancak scheduler'in arka
    # planda urettigi bildirimler zil rozetine hic
    # yansimiyordu (kullanici kabulunde dogrulandi).
    #
    # NOTIFICATION_LIVE_REFRESH sprintinde bilincli olarak
    # hafif bir yoklama eklendi. Iddia kaldirilmadi,
    # GUNCELLENDI: yoklama VAR ama SINIRLI olmali.
    check(
        "arka plan yoklamasi sinirli ve temizleniyor",
        "UNREAD_POLL_INTERVAL_MS" in app_source
        and "clearInterval" in app_source
        and "removeEventListener" in app_source,
    )

    check(
        "yoklama araligi agresif degil",
        _poll_interval_ms(app_source) is not None
        and _poll_interval_ms(app_source) >= 15000,
        f"{_poll_interval_ms(app_source)} ms",
    )

    # WebSocket/SSE hala YOK: bu karar DEGISMEDI.
    # Aciklama satirinda gecmesi serbest, aranan sey
    # gercek kullanimdir.
    check(
        "WebSocket / SSE KULLANIMI yok",
        re.search(r"new\s+WebSocket\s*\(", app_source) is None
        and re.search(r"new\s+EventSource\s*\(", app_source)
        is None,
    )

    # -----------------------------------------------------
    # 10. MEVCUT DETAY / HIGHLIGHT BOZULMADI
    # -----------------------------------------------------

    print("\n--- 10. mevcut detay bozulmadi ---")

    for marker in [
        "Gemini Özeti",
        'title="Eklenen İçerik"',
        'title="Silinen İçerik"',
        "blocks={detail.added_blocks}",
        "blocks={detail.removed_blocks}",
        "Canlı sayfada görüntüle",
        "href={livePageUrl}",
        "detail.evidence_text",
        "Bildirimle ilgili değişiklik",
    ]:
        check(
            f"detayda korunmus: {marker[:38]}",
            marker in detail_page,
        )

    check(
        "text fragment mantigi korunmus",
        "buildLivePageUrl(" in app_source
        and ":~:text=" in app_source,
    )

    check(
        "dangerouslySetInnerHTML kullanimi hala yok",
        re.search(
            r"dangerouslySetInnerHTML\s*[=:]",
            app_source,
        )
        is None,
    )

    check(
        "dis link guvenligi korunmus",
        'target="_blank"' in detail_page
        and 'rel="noopener noreferrer"' in detail_page,
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
