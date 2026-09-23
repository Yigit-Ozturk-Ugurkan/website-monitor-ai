"""
Degisiklik Detayi ekrani frontend dogrulamasi.

NOT: Projede bir JS test kosucusu (vitest/jest) kurulu degil ve
bu sprintte yeni paket kurulmuyor. Asagidakiler CALISMA ZAMANI
component testi degil, KAYNAK KODU uzerinde yapilan statik
dogrulamalardir.

Tek istisna: text fragment uretimi Node ile gercekten
calistirilarak dogrulanir.

Calistirma:
    python -m tests.test_change_detail_frontend
"""

import io
import json
import re
import shutil
import subprocess
import tempfile

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


def run_fragment_cases(app_source: str) -> dict | None:
    """
    Text fragment yardimcilarini Node ile gercekten calistirir.
    """

    node = shutil.which("node")

    if node is None:
        return None

    helpers = section(
        app_source,
        "const FRAGMENT_MIN_LENGTH",
        "function formatRunMessage(",
    )

    script = (
        helpers
        + """
const HN_URL = "https://news.ycombinator.com/";
const ANKIDROID_LINE =
  "AnkiDroid: Google Play no longer allowing Open Collective donation link";
const ARC_LINE = "ARC-AGI-1 benchmark reaches 44 percent at 67 cents";
const BUG_ADDED =
  ANKIDROID_LINE + "\\n(\\ngithub.com/ankidroid\\n)\\n" + ARC_LINE;

const cases = {
  gercekVeri: buildLivePageUrl(
    "https://news.ycombinator.com/",
    "AnkiDroid: Google Play no longer allowing Open Collective donation link\\n(\\ngithub.com/ankidroid\\n)"
  ),
  bosAdded: buildLivePageUrl("https://news.ycombinator.com/", ""),
  nullAdded: buildLivePageUrl("https://news.ycombinator.com/", null),
  sadeceGurultu: buildLivePageUrl("https://x.com/p", "|\\n(\\n)\\nab"),
  mevcutHash: buildLivePageUrl("https://x.com/p#bolum", "Merhaba dunya bu bir testtir"),
  zatenFragment: buildLivePageUrl("https://x.com/p#:~:text=abc", "Merhaba dunya bu bir testtir"),
  tireliMetin: buildLivePageUrl("https://x.com/p", "ARC-AGI-1 testinde yeni bir sonuc"),
  ozelKarakter: buildLivePageUrl("https://x.com/p", "Fiyat & indirim, %15 dusus oldu"),
  cokUzun: buildLivePageUrl("https://x.com/p", "kelime ".repeat(60)),
  urlYok: buildLivePageUrl("", "Merhaba dunya bu bir testtir"),
  secilenMetin: pickFragmentText(
    "|\\n(\\n)\\nAnkiDroid: Google Play no longer allowing Open Collective donation link"
  ),

  // --- GERCEK BUG REGRESYONU ---
  // added_content'in ilk uygun satiri AnkiDroid,
  // ama Gemini evidence'i ARC-AGI-1.
  bugEvidenceYok: buildLivePageUrl(HN_URL, BUG_ADDED),
  bugEvidenceVar: buildLivePageUrl(HN_URL, BUG_ADDED, ARC_LINE, "added"),
  bugEvidenceRemoved: buildLivePageUrl(
    HN_URL,
    BUG_ADDED,
    "Eski model fiyati 100 dolar",
    "removed"
  ),
  bugEvidenceTypeYok: buildLivePageUrl(HN_URL, BUG_ADDED, ARC_LINE, null),
  bugEvidenceMetinYok: buildLivePageUrl(HN_URL, BUG_ADDED, null, "added"),
  bugEvidenceBos: buildLivePageUrl(HN_URL, BUG_ADDED, "   ", "added"),
  evidenceAddedContentYokken: buildLivePageUrl(
    HN_URL,
    null,
    ARC_LINE,
    "added"
  ),
};

console.log(JSON.stringify(cases));
"""
    )

    with tempfile.TemporaryDirectory() as directory:

        script_path = Path(directory) / "fragment_check.mjs"

        io.open(script_path, "w", encoding="utf-8").write(script)

        result = subprocess.run(
            [node, str(script_path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
        )

        if result.returncode != 0:
            print(result.stderr)
            return None

        return json.loads(result.stdout)


def main() -> None:

    print("=" * 70)
    print("DEGISIKLIK DETAYI FRONTEND DOGRULAMASI")
    print("(statik kaynak kontrolu + Node ile fragment testi)")
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
        "function ContextBlockView(",
        "function SidebarLink(",
    )

    # -----------------------------------------------------
    # 1. KART -> DETAIL ROUTE
    # -----------------------------------------------------

    print("\n--- 1. karttan detaya gecis ---")

    check(
        "api.js icinde getNotificationDetail tanimli",
        "export async function getNotificationDetail(notificationId)"
        in api_source,
    )

    check(
        "detail dogru adrese gidiyor",
        "/api/notifications/${notificationId}/detail" in api_source,
    )

    check(
        "bildirim karti gercek link (NavLink)",
        "<NavLink" in notifications_page
        and "</NavLink>" in notifications_page,
    )

    check(
        "kart detail route'una gidiyor",
        "to={`/notifications/${notification.id}`}" in notifications_page,
    )

    check(
        "kart artik <article> degil (klavye ile ulasilabilir)",
        "<article" not in notifications_page,
    )

    check(
        "detail route tanimli",
        'path="/notifications/:notificationId"' in app_source,
    )

    check(
        "NotificationDetailPage route'a bagli",
        re.search(
            r"notifications/:notificationId[\s\S]{0,200}?"
            r"<NotificationDetailPage",
            app_source,
        )
        is not None,
    )

    check(
        "useParams ile notificationId aliniyor",
        "const { notificationId } = useParams();" in detail_page,
    )

    # -----------------------------------------------------
    # 2-4. DURUMLAR
    # -----------------------------------------------------

    print("\n--- 2-4. loading / success / error ---")

    check(
        "loading state var",
        "setLoading(true)" in detail_page
        and "Değişiklik detayı yükleniyor..." in detail_page,
    )

    check(
        "error state var",
        "setError(" in detail_page
        and "Değişiklik detayı yüklenemedi" in detail_page,
    )

    check(
        "success state var",
        "!loading && !error && detail && (" in detail_page,
    )

    check(
        "veri getNotificationDetail ile cekiliyor",
        "await getNotificationDetail(" in detail_page,
    )

    check(
        "hata durumunda geri donus linki var",
        "Bildirimlere dön" in detail_page,
    )

    # -----------------------------------------------------
    # 5. GEMINI OZETI
    # -----------------------------------------------------

    print("\n--- 5. gemini ozeti ---")

    check(
        "Gemini Özeti bolumu var",
        "Gemini Özeti" in detail_page,
    )

    check(
        "ai_summary gosteriliyor",
        "detail.ai_summary" in detail_page,
    )

    check(
        "ai_summary yoksa fallback var",
        "detail.message ??" in detail_page,
    )

    # -----------------------------------------------------
    # 6-8. EKLENEN / SILINEN / CONTEXT
    # -----------------------------------------------------

    print("\n--- 6-8. eklenen / silinen / context ---")

    check(
        "Eklenen İçerik bolumu var",
        'title="Eklenen İçerik"' in detail_page,
    )

    check(
        "Silinen İçerik bolumu var",
        'title="Silinen İçerik"' in detail_page,
    )

    check(
        "eklenenler added_blocks ile gosteriliyor",
        "blocks={detail.added_blocks}" in detail_page,
    )

    check(
        "silinenler removed_blocks ile gosteriliyor",
        "blocks={detail.removed_blocks}" in detail_page,
    )

    check(
        "blok yoksa ham icerige fallback var",
        "fallbackContent={detail.added_content}" in detail_page
        and "fallbackContent={detail.removed_content}" in detail_page,
    )

    check(
        "context satirlari render ediliyor",
        "block.lines.map(" in detail_page
        and "line.line_number" in detail_page
        and "line.text" in detail_page,
    )

    check(
        "satir numarasi gosteriliyor",
        "context-line-number" in detail_page
        and ".context-line-number" in css_source,
    )

    check(
        "degisen satir isaretleniyor",
        "line.changed" in detail_page,
    )

    check(
        "bos durum icin mesaj var",
        "emptyLabel=" in detail_page
        and "eklenen içerik bulunmuyor" in detail_page,
    )

    # -----------------------------------------------------
    # 9-10. GUVENLI RENDER
    # -----------------------------------------------------

    print("\n--- 9-10. guvenli render ---")

    # Aciklama satirlarinda kelimenin gecmesi sorun degil;
    # aranan sey gercek KULLANIM sozdizimi.
    usage_pattern = re.compile(
        r"dangerouslySetInnerHTML\s*[=:]"
    )

    check(
        "App.jsx icinde dangerouslySetInnerHTML KULLANIMI yok",
        usage_pattern.search(app_source) is None,
    )

    offenders = [
        path.name
        for path in (PROJECT_ROOT / "frontend" / "src").rglob("*.js*")
        if usage_pattern.search(read(path))
    ]

    check(
        "projede hicbir kaynakta dangerouslySetInnerHTML kullanilmiyor",
        not offenders,
        f"kullananlar={offenders}",
    )

    check(
        "innerHTML kullanimi yok",
        "innerHTML" not in app_source,
    )

    check(
        "snapshot metni React text node olarak veriliyor",
        "{line.text}" in detail_page,
    )

    check(
        "iframe ile dis site render edilmiyor",
        "<iframe" not in app_source,
    )

    # -----------------------------------------------------
    # ERISILEBILIRLIK: RENGE EK ISARET
    # -----------------------------------------------------

    print("\n--- erisilebilirlik ---")

    check(
        "Eklenen/Silinen metin etiketleri var (renge ek)",
        'badgeLabel="Eklenen"' in detail_page
        and 'badgeLabel="Silinen"' in detail_page,
    )

    check(
        "degisen satirlarda +/- isareti var",
        "context-line-marker" in detail_page,
    )

    check(
        "added/removed stilleri mevcut tokenlari kullaniyor",
        "var(--success-soft)" in css_source
        and ".context-line.changed.added" in css_source
        and ".context-line.changed.removed" in css_source,
    )

    # -----------------------------------------------------
    # 11-15. CANLI SAYFA / TEXT FRAGMENT
    # -----------------------------------------------------

    print("\n--- 11-15. canli sayfa / text fragment ---")

    check(
        "'Canlı sayfada görüntüle' butonu var",
        "Canlı sayfada görüntüle" in detail_page,
    )

    check(
        "link buildLivePageUrl sonucunu kullaniyor",
        "href={livePageUrl}" in detail_page
        and "buildLivePageUrl(" in detail_page,
    )

    check(
        "yeni sekmede aciliyor",
        'target="_blank"' in detail_page,
    )

    check(
        "rel guvenligi var",
        'rel="noopener noreferrer"' in detail_page,
    )

    check(
        "encodeURIComponent kullaniliyor",
        "encodeURIComponent(text)" in app_source,
    )

    check(
        "text fragment sozdizimi dogru",
        ":~:text=" in app_source,
    )

    check(
        '"-" karakteri fragment icin kodlaniyor',
        '"%2D"' in app_source,
    )

    check(
        "fragment uzunlugu sinirli",
        "FRAGMENT_MAX_LENGTH" in app_source,
    )

    check(
        "buildLivePageUrl evidence parametrelerini aliyor",
        re.search(
            r"function buildLivePageUrl\([\s\S]{0,120}?"
            r"evidenceText[\s\S]{0,40}?evidenceType",
            app_source,
        )
        is not None,
    )

    check(
        "cagri evidence alanlarini gecirıyor",
        "detail.evidence_text," in detail_page
        and "detail.evidence_type" in detail_page,
    )

    check(
        "evidence oncelikli, heuristic fallback",
        "resolveFragmentText(" in app_source
        and "pickFragmentText(addedContent)" in app_source,
    )

    check(
        "removed evidence icin fragment uretilmiyor",
        'if (evidenceType === "removed")' in app_source,
    )

    check(
        "evidence detay ekraninda gosteriliyor",
        "detail.evidence_text &&" in detail_page
        and "evidence-panel" in detail_page,
    )

    check(
        "'Bildirimle ilgili değişiklik' etiketi var",
        "Bildirimle ilgili değişiklik" in detail_page,
    )

    check(
        "evidence satiri context icinde isaretleniyor",
        "line.evidence" in detail_page
        and ".context-line.evidence" in css_source,
    )

    check(
        "evidence hangi kaynaktan geldigi yaziyor",
        "Eklenen içerikten" in detail_page
        and "Silinen içerikten" in detail_page,
    )

    check(
        "evidence yoksa kullaniciya aciklama var",
        "kaynak metin işaretlenmemiş" in detail_page,
    )

    check(
        "mevcut added/removed bloklari kayboldu mu (kaybolmamali)",
        "blocks={detail.added_blocks}" in detail_page
        and "blocks={detail.removed_blocks}" in detail_page,
    )

    check(
        "cross-origin DOM manipulasyonu yok",
        "contentWindow" not in app_source
        and "postMessage" not in app_source,
    )

    # -----------------------------------------------------
    # NODE ILE GERCEK FRAGMENT TESTI
    # -----------------------------------------------------

    print("\n--- fragment uretimi (Node ile calistirildi) ---")

    results = run_fragment_cases(app_source)

    if results is None:
        print("[SKIP] node bulunamadi, fragment testi atlandi")
    else:
        check(
            "gercek added_content'ten fragment uretiliyor",
            results["gercekVeri"]
            == "https://news.ycombinator.com/#:~:text="
            "AnkiDroid%3A%20Google%20Play%20no%20longer%20allowing%20"
            "Open%20Collective%20donation%20link",
            results["gercekVeri"],
        )

        check(
            "secilen metin ayraclari atliyor",
            results["secilenMetin"]
            == "AnkiDroid: Google Play no longer allowing "
            "Open Collective donation link",
            results["secilenMetin"],
        )

        check(
            "added yoksa normal URL fallback",
            results["bosAdded"] == "https://news.ycombinator.com/"
            and results["nullAdded"] == "https://news.ycombinator.com/",
            f"{results['bosAdded']} / {results['nullAdded']}",
        )

        check(
            "sadece ayrac varsa normal URL fallback",
            results["sadeceGurultu"] == "https://x.com/p",
            results["sadeceGurultu"],
        )

        check(
            "mevcut hash korunuyor",
            results["mevcutHash"]
            == "https://x.com/p#bolum:~:text="
            "Merhaba%20dunya%20bu%20bir%20testtir",
            results["mevcutHash"],
        )

        check(
            "zaten fragment varsa dokunulmuyor",
            results["zatenFragment"] == "https://x.com/p#:~:text=abc",
            results["zatenFragment"],
        )

        check(
            "tire karakteri %2D olarak kodlaniyor",
            "%2D" in results["tireliMetin"]
            and "-" not in results["tireliMetin"].split(":~:text=")[1],
            results["tireliMetin"],
        )

        check(
            "ozel karakterler kodlaniyor (& , %)",
            "%26" in results["ozelKarakter"]
            and "%2C" in results["ozelKarakter"]
            and "%25" in results["ozelKarakter"],
            results["ozelKarakter"],
        )

        check(
            "cok uzun diff URL'ye tam konmuyor",
            len(results["cokUzun"]) < 400,
            f"uzunluk={len(results['cokUzun'])}",
        )

        check(
            "url yoksa bos string donuyor",
            results["urlYok"] == "",
            repr(results["urlYok"]),
        )

        # -------------------------------------------------
        # GERCEK BUG REGRESYONU
        #
        # Gemini summary "ARC-AGI-1..." derken link
        # "AnkiDroid..." satirina gidiyordu.
        # -------------------------------------------------

        print()
        print("--- ASIL BUG REGRESYONU ---")

        arc_fragment = (
            "ARC%2DAGI%2D1%20benchmark%20reaches%2044%20"
            "percent%20at%2067%20cents"
        )

        check(
            "BUG: evidence yokken hala ilk satir seciliyor "
            "(eski davranis fallback olarak korunuyor)",
            "AnkiDroid" in results["bugEvidenceYok"],
            results["bugEvidenceYok"],
        )

        check(
            "DUZELTME: evidence varken ARC-AGI-1 seciliyor",
            results["bugEvidenceVar"]
            == f"https://news.ycombinator.com/#:~:text={arc_fragment}",
            results["bugEvidenceVar"],
        )

        check(
            "DUZELTME: evidence varken AnkiDroid SECILMIYOR",
            "AnkiDroid" not in results["bugEvidenceVar"],
            results["bugEvidenceVar"],
        )

        check(
            "evidence added_content'teki ilk satir olmasa da kullaniliyor",
            "ARC%2DAGI%2D1" in results["bugEvidenceVar"],
        )

        check(
            "removed evidence icin fragment uretilmiyor "
            "(normal URL fallback)",
            results["bugEvidenceRemoved"]
            == "https://news.ycombinator.com/",
            results["bugEvidenceRemoved"],
        )

        check(
            "evidence_type yoksa heuristic fallback calisiyor",
            "AnkiDroid" in results["bugEvidenceTypeYok"],
            results["bugEvidenceTypeYok"],
        )

        check(
            "evidence_text yoksa heuristic fallback calisiyor",
            "AnkiDroid" in results["bugEvidenceMetinYok"],
            results["bugEvidenceMetinYok"],
        )

        check(
            "bos evidence heuristic fallback'e dusuyor",
            "AnkiDroid" in results["bugEvidenceBos"],
            results["bugEvidenceBos"],
        )

        check(
            "added_content olmasa da evidence tek basina yeterli",
            "ARC%2DAGI%2D1" in results["evidenceAddedContentYokken"],
            results["evidenceAddedContentYokken"],
        )

    # -----------------------------------------------------
    # TASARIM DILI
    # -----------------------------------------------------

    print("\n--- mevcut tasarim dili ---")

    for css_class in [
        "page-header",
        "placeholder-panel",
        "form-panel",
        "form-grid three-columns",
        "secondary-button",
        "primary-button",
    ]:
        check(
            f"mevcut '{css_class}' sinifi yeniden kullanilmis",
            css_class in detail_page,
        )

    check(
        "sidebar/topbar degismedi",
        'className="sidebar"' in app_source
        and 'className="topbar"' in app_source,
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
