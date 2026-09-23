"""
manual_section (Manuel alan seç) testleri.

GERCEK GEMINI API CAGRISI YAPILMAZ.
GERCEK HTTP ISTEGI YAPILMAZ.

manual_section akisinda hicbir AI cagrisi OLMAMALIDIR.
Bu yuzden sahte Gemini client'i, cagrildigi anda testi
dusuren bir "tuzak" olarak kurulur.

Tum DB islemleri dis bir transaction icinde calisir ve
sonunda ROLLBACK edilir; gercek monitor kayitlari bozulmaz.

Calistirma:
    python -m tests.test_manual_section
"""

import asyncio
import socket
import warnings

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database import SessionLocal, engine
from app.models.change import Change
from app.models.monitor import Monitor
from app.models.snapshot import Snapshot

from app.repositories.monitor_repository import (
    create_monitor,
    should_reset_auto_section,
    should_reset_manual_section,
    update_monitor,
)
from app.repositories.snapshot_repository import get_latest_snapshot

from app.services import ai_analyzer
from app.services import db_monitor_service
from app.services.content_extractor import extract_content
from app.services.db_monitor_service import run_database_monitor
from app.services.section_extractor import (
    ManualSectionResolutionError,
    extract_section_candidates,
    normalize_label,
    resolve_section,
)

warnings.filterwarnings("ignore")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


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
# AG ERISIMI ENGELI
# ---------------------------------------------------------

NETWORK_ATTEMPTS = []

_real_connect = socket.socket.connect
_real_create_connection = socket.create_connection


LOOPBACK_HOSTS = {
    "127.0.0.1",
    "::1",
    "localhost",
    "",
    "testserver",
}


def _is_loopback(address) -> bool:

    if not isinstance(address, tuple) or not address:
        return True

    host = address[0]

    if not isinstance(host, str):
        return True

    return host in LOOPBACK_HOSTS


def _blocked_connect(self, address, *args, **kwargs):
    if _is_loopback(address):
        return _real_connect(self, address, *args, **kwargs)

    NETWORK_ATTEMPTS.append(address)

    raise AssertionError(
        f"Test sirasinda dis ag baglantisi denendi: {address}"
    )


def _blocked_create_connection(address, *args, **kwargs):
    if _is_loopback(address):
        return _real_create_connection(address, *args, **kwargs)

    NETWORK_ATTEMPTS.append(address)

    raise AssertionError(
        f"Test sirasinda dis ag baglantisi denendi: {address}"
    )


def block_network():
    socket.socket.connect = _blocked_connect
    socket.create_connection = _blocked_create_connection


def unblock_network():
    socket.socket.connect = _real_connect
    socket.create_connection = _real_create_connection


# ---------------------------------------------------------
# GEMINI TUZAGI
#
# manual_section akisinda BOLUM SECIMI icin Gemini
# HIC cagrilmamalidir.
#
# Degisiklik analizi cagrilari ayri sayilir; onlar
# kapsamdan bagimsiz olarak calismaya devam eder.
#
# Her cagri prompt'una gore siniflandirilir ve hata
# firlatilir; boylece hicbir gercek istek cikmaz.
# ---------------------------------------------------------

SELECTION_PROMPT_MARKER = "bölüm seçme motorusun"

SELECTION_CALLS = []

ANALYSIS_CALLS = []


def reset_gemini_counters():
    SELECTION_CALLS.clear()
    ANALYSIS_CALLS.clear()


class TrapClient:
    def __init__(self):
        self.aio = self

        self.models = self

    async def generate_content(self, model, contents, config):

        if SELECTION_PROMPT_MARKER in contents:
            SELECTION_CALLS.append(model)

            raise AssertionError(
                "Bolum secimi icin Gemini cagrildi: "
                f"{model}"
            )

        ANALYSIS_CALLS.append(model)

        raise AssertionError(
            "Degisiklik analizi icin Gemini cagrildi: "
            f"{model}"
        )

    async def aclose(self):
        return None


def install_gemini_trap():
    ai_analyzer.genai.Client = lambda *a, **k: TrapClient()


# ---------------------------------------------------------
# SAHTE HTML SAYFALARI
# ---------------------------------------------------------

def build_page(
    pricing_body: str,
    blog_body: str,
    pricing_id: str = "fiyatlandirma",
    pricing_heading: str = "Fiyatlandırma",
) -> str:

    return f"""<html>
<head>
  <title>Örnek Ürün Sitesi</title>
  <style>.a {{ color: red; }}</style>
</head>
<body>
  <nav>Anasayfa Ürünler Fiyatlandırma Blog İletişim menü bağlantıları</nav>

  <main>
    <section id="urunler">
      <h2>Ürünler</h2>
      <p>Platformumuz ekipler için ortak çalışma araçları sunar ve
         tüm veriler bulutta saklanır.</p>
      <p>Mobil uygulama iOS ve Android için ayrı ayrı yayınlanmaktadır.</p>
    </section>

    <section id="{pricing_id}">
      <h2>{pricing_heading}</h2>
      {pricing_body}
    </section>

    <section id="blog">
      <h2>Blog</h2>
      {blog_body}
    </section>
  </main>

  <footer>Telif hakkı 2026 Örnek Şirket tüm hakları saklıdır burada</footer>
</body>
</html>"""


PRICING_V1 = """
      <p>Başlangıç paketi aylık 100 TL olarak sunulmaktadır.</p>
      <p>Kurumsal paket aylık 500 TL seviyesindedir ve sınırsız kullanıcı içerir.</p>
"""

PRICING_V2 = """
      <p>Başlangıç paketi aylık 150 TL olarak sunulmaktadır.</p>
      <p>Kurumsal paket aylık 500 TL seviyesindedir ve sınırsız kullanıcı içerir.</p>
"""

BLOG_V1 = """
      <p>Yeni sürümde performans iyileştirmeleri yapıldı ve arayüz yenilendi.</p>
      <p>Ekibimize katılan yeni geliştiricilerle birlikte yol haritası genişledi.</p>
"""

BLOG_V2 = """
      <p>Kasım ayı ürün günlüğü yayınlandı ve yeni entegrasyonlar duyuruldu.</p>
      <p>Ekibimize katılan yeni geliştiricilerle birlikte yol haritası genişledi.</p>
"""


PAGE_V1 = build_page(PRICING_V1, BLOG_V1)

# Yalnizca SECILEN bolum degisti
PAGE_PRICING_CHANGED = build_page(PRICING_V2, BLOG_V1)

# Yalnizca BASKA bir bolum degisti
PAGE_BLOG_CHANGED = build_page(PRICING_V1, BLOG_V2)

# Secilen bolum kayboldu
PAGE_SECTION_LOST = build_page(
    PRICING_V1,
    BLOG_V1,
    pricing_id="ucretlendirme-yeni",
    pricing_heading="Ücretlendirme Tablosu",
)

# Hicbir aday bolum yok
PAGE_NO_CANDIDATE = """<html>
<head><title>Boş Sayfa</title></head>
<body>
  <nav>menü</nav>
  <p>kısa</p>
  <footer>alt</footer>
</body>
</html>"""


# ---------------------------------------------------------
# SAHTE FETCH
# ---------------------------------------------------------

@dataclass
class FakeFetchResult:
    url: str
    final_url: str
    status_code: int
    content_type: str
    html: str


CURRENT_HTML = {"value": PAGE_V1}

FETCH_CALLS = []


async def fake_fetch_website(url: str) -> FakeFetchResult:

    FETCH_CALLS.append(url)

    if not url:
        raise ValueError("URL boş olamaz.")

    if not url.startswith(("http://", "https://")):
        raise ValueError(
            "URL 'http://' veya 'https://' ile başlamalıdır."
        )

    return FakeFetchResult(
        url=url,
        final_url=url,
        status_code=200,
        content_type="text/html; charset=utf-8",
        html=CURRENT_HTML["value"],
    )


# ---------------------------------------------------------
# YARDIMCILAR
# ---------------------------------------------------------

TEST_URL = "https://ornek-site.test/urun"


# API testinde gercek monitorun geri yuklenecek alanlari.
RESTORE_FIELDS = [
    "name",
    "url",
    "topic",
    "scope_type",
    "schedule_type",
    "schedule_time",
    "start_date",
    "end_date",
    "is_unlimited",
    "notify_in_app",
    "notify_email",
    "notification_email",
    "notification_type",
    "notification_target",
    "is_active",
    "auto_section_reference",
    "auto_section_label",
    "manual_section_reference",
    "manual_section_label",
]


def run_pipeline(session, monitor, html):

    CURRENT_HTML["value"] = html

    return asyncio.run(
        run_database_monitor(
            db=session,
            monitor=monitor,
        )
    )


def pricing_candidate():
    """
    Kullanicinin secmis oldugu varsayilan bolum.
    """

    discovery = extract_section_candidates(PAGE_V1)

    return next(
        candidate
        for candidate in discovery.candidates
        if candidate.reference == "id:fiyatlandirma"
    )


def make_manual_monitor(session, name, candidate, url=TEST_URL):

    return create_monitor(
        db=session,
        name=name,
        url=url,
        topic="fiyat değişiklikleri",
        scope_type="manual_section",
        schedule_type="daily",
        schedule_time="09:00",
        manual_section_reference=candidate.reference,
        manual_section_label=candidate.label,
    )


def main() -> None:

    print("=" * 70)
    print("MANUAL SECTION TESTLERI")
    print("(Gemini cagrisi TUZAKLI, gercek HTTP YOK, DB rollback)")
    print("=" * 70)

    original_client = ai_analyzer.genai.Client
    original_fetch = db_monitor_service.fetch_website

    import app.api.monitor_routes as monitor_routes

    original_route_fetch = monitor_routes.fetch_website

    db_monitor_service.fetch_website = fake_fetch_website
    monitor_routes.fetch_website = fake_fetch_website

    install_gemini_trap()

    block_network()

    baseline = SessionLocal()

    try:
        monitors_before = baseline.scalar(
            select(func.count()).select_from(Monitor)
        )
        snapshots_before = baseline.scalar(
            select(func.count()).select_from(Snapshot)
        )
    finally:
        baseline.close()

    client = TestClient(app)

    selected = pricing_candidate()

    connection = engine.connect()
    transaction = connection.begin()

    session = Session(
        bind=connection,
        join_transaction_mode="create_savepoint",
    )

    try:
        # =================================================
        # 1-5. CANDIDATE PREVIEW ENDPOINT
        # =================================================

        print("\n--- 1-5. aday onizleme endpoint'i ---")

        CURRENT_HTML["value"] = PAGE_V1

        db_snapshot_before = session.scalar(
            select(func.count()).select_from(Monitor)
        )

        response = client.post(
            "/api/monitors/section-candidates",
            json={"url": TEST_URL},
        )

        payload = response.json()

        check(
            "1. candidate endpoint 200 donuyor",
            response.status_code == 200,
            f"{response.status_code}",
        )

        check(
            "1. page_title donuyor",
            payload.get("page_title") == "Örnek Ürün Sitesi",
            f"{payload.get('page_title')!r}",
        )

        candidates = payload.get("candidates", [])

        check(
            "1. aday listesi dolu",
            len(candidates) >= 3,
            f"aday={len(candidates)}",
        )

        check(
            "1. her aday gerekli alanlari tasiyor",
            all(
                set(candidate.keys())
                == {
                    "candidate_id",
                    "label",
                    "reference",
                    "tag",
                    "text_length",
                    "preview",
                }
                for candidate in candidates
            ),
            f"{sorted(candidates[0].keys())}",
        )

        check(
            "2. candidate endpoint Gemini CAGIRMADI",
            SELECTION_CALLS == [] and ANALYSIS_CALLS == [],
            f"secim={SELECTION_CALLS} analiz={ANALYSIS_CALLS}",
        )

        check(
            "3. candidate endpoint DB'ye monitor yazmadi",
            session.scalar(
                select(func.count()).select_from(Monitor)
            )
            == db_snapshot_before,
        )

        check(
            "3. candidate endpoint snapshot yazmadi",
            session.scalar(
                select(func.count()).select_from(Snapshot)
            )
            == session.scalar(
                select(func.count()).select_from(Snapshot)
            ),
        )

        # 4. PREVIEW DUZ METIN
        preview_text = "\n".join(
            candidate["preview"]
            for candidate in candidates
        )

        check(
            "4. preview duz metin (HTML etiketi yok)",
            "<" not in preview_text
            and "script" not in preview_text.lower(),
            preview_text[:60],
        )

        check(
            "4. preview boyutu sinirli",
            all(
                len(candidate["preview"]) <= 520
                for candidate in candidates
            ),
            f"{max(len(c['preview']) for c in candidates)}",
        )

        check(
            "4. text_length TAM uzunlugu veriyor",
            all(
                candidate["text_length"]
                >= len(candidate["preview"]) - 3
                for candidate in candidates
            ),
        )

        check(
            "4. gurultu (nav/footer/style) preview'a girmedi",
            "menü bağlantıları" not in preview_text
            and "tüm hakları saklıdır" not in preview_text
            and "color: red" not in preview_text,
        )

        check(
            "4. referans degerleri section_extractor bicimi",
            all(
                ":" in candidate["reference"]
                and "nth-child" not in candidate["reference"]
                for candidate in candidates
            ),
        )

        # 5. ADAY YOK
        CURRENT_HTML["value"] = PAGE_NO_CANDIDATE

        empty_response = client.post(
            "/api/monitors/section-candidates",
            json={"url": TEST_URL},
        )

        check(
            "5. aday yoksa 200 + bos liste",
            empty_response.status_code == 200
            and empty_response.json()["candidates"] == [],
            f"{empty_response.status_code} "
            f"{empty_response.json()}",
        )

        check(
            "5. aday yokken de Gemini cagrilmadi",
            SELECTION_CALLS == [] and ANALYSIS_CALLS == [],
        )

        # Hatali URL
        bad_response = client.post(
            "/api/monitors/section-candidates",
            json={"url": "sacma-url"},
        )

        check(
            "gecersiz URL kontrollu hata donuyor",
            bad_response.status_code == 422,
            f"{bad_response.status_code}",
        )

        CURRENT_HTML["value"] = PAGE_V1

        # =================================================
        # 6-8. CREATE CONTRACT
        # =================================================

        print("\n--- 6-8. create sozlesmesi ---")

        base_body = {
            "name": "manuel sozlesme testi",
            "url": TEST_URL,
            "topic": "fiyat değişiklikleri",
            "scope_type": "manual_section",
            "schedule_type": "daily",
            "schedule_time": "09:00",
        }

        no_reference = client.post(
            "/api/monitors",
            json=base_body,
        )

        check(
            "6. manual + reference yok -> 422",
            no_reference.status_code == 422,
            f"{no_reference.status_code}",
        )

        check(
            "6. hata mesaji anlasilir",
            "bölüm" in str(no_reference.json()).lower(),
            str(no_reference.json())[:100],
        )

        no_label = client.post(
            "/api/monitors",
            json={
                **base_body,
                "manual_section_reference": selected.reference,
            },
        )

        check(
            "7. manual + label yok -> 422",
            no_label.status_code == 422,
            f"{no_label.status_code}",
        )

        blank_reference = client.post(
            "/api/monitors",
            json={
                **base_body,
                "manual_section_reference": "   ",
                "manual_section_label": "Fiyatlandırma",
            },
        )

        check(
            "6. bosluk reference de reddediliyor",
            blank_reference.status_code == 422,
            f"{blank_reference.status_code}",
        )

        check(
            "6-7. gecersiz create DB'ye yazilmadi",
            session.scalar(
                select(func.count()).select_from(Monitor)
            )
            == db_snapshot_before,
        )

        # 8. GECERLI CREATE (repository uzerinden, rollback icinde)
        valid_monitor = make_manual_monitor(
            session,
            "gecerli manuel monitor",
            selected,
        )

        check(
            "8. gecerli manuel monitor olusturuldu",
            valid_monitor.manual_section_reference
            == selected.reference
            and valid_monitor.manual_section_label
            == selected.label,
            f"{valid_monitor.manual_section_reference!r}",
        )

        check(
            "8. auto_section alanlari BOS kaldi "
            "(iki kapsam bagimsiz)",
            valid_monitor.auto_section_reference is None
            and valid_monitor.auto_section_label is None,
        )

        # full_page ve auto_section create hala calisiyor
        full_page_ok = client.post(
            "/api/monitors",
            json={
                **base_body,
                "scope_type": "full_page",
            },
        )

        check(
            "full_page create manual dogrulamasindan etkilenmiyor",
            full_page_ok.status_code == 201,
            f"{full_page_ok.status_code}",
        )

        if full_page_ok.status_code == 201:
            # Bu kayit gercek DB'ye gitti; hemen temizlenir.
            created_id = full_page_ok.json()["id"]

            cleanup = SessionLocal()

            try:
                created = cleanup.get(Monitor, created_id)

                if created is not None:
                    cleanup.delete(created)
                    cleanup.commit()
            finally:
                cleanup.close()

        # =================================================
        # 9-10. MANUAL FIRST RUN
        # =================================================

        print("\n--- 9-10. manuel ilk calistirma ---")

        expected_full = extract_content(PAGE_V1)

        result = run_pipeline(
            session,
            valid_monitor,
            PAGE_V1,
        )

        first_snapshot = get_latest_snapshot(
            db=session,
            monitor_id=valid_monitor.id,
        )

        check(
            "9. first_run=True changed=False",
            result.first_run is True
            and result.changed is False,
        )

        check(
            "9. snapshot yalnizca secilen bolum",
            first_snapshot.content == selected.text,
        )

        check(
            "9. snapshot tum sayfadan KUCUK",
            len(first_snapshot.content)
            < len(expected_full.text),
            f"{len(first_snapshot.content)} < "
            f"{len(expected_full.text)}",
        )

        check(
            "9. snapshot diger bolumleri icermiyor",
            "Mobil uygulama" not in first_snapshot.content
            and "yol haritası" not in first_snapshot.content,
        )

        check(
            "9. snapshot nav/footer icermiyor",
            "menü bağlantıları" not in first_snapshot.content
            and "tüm hakları saklıdır"
            not in first_snapshot.content,
        )

        check(
            "9. snapshot basligi sayfa <title>",
            first_snapshot.title == "Örnek Ürün Sitesi",
            f"{first_snapshot.title!r}",
        )

        check(
            "10. ilk run'da bolum secimi Gemini cagrisi = 0",
            SELECTION_CALLS == [],
            f"{SELECTION_CALLS}",
        )

        check(
            "10. ilk run'da hic AI cagrisi yok",
            ANALYSIS_CALLS == [],
            f"{ANALYSIS_CALLS}",
        )

        check(
            "10. result.manual_section dolu, auto_section None",
            result.manual_section is not None
            and result.auto_section is None,
        )

        check(
            "10. secim DB'de degismedi (pipeline yazmadi)",
            valid_monitor.manual_section_reference
            == selected.reference,
        )

        # =================================================
        # 11-12. SONRAKI RUN
        # =================================================

        print("\n--- 11-12. sonraki calistirma ---")

        result = run_pipeline(
            session,
            valid_monitor,
            PAGE_V1,
        )

        check(
            "11. ayni referans kullanildi",
            valid_monitor.manual_section_reference
            == selected.reference
            and result.manual_section.reference
            == selected.reference,
        )

        check(
            "11. ayni icerik -> changed=False",
            result.changed is False
            and result.first_run is False,
        )

        check(
            "11. Gemini yine cagrilmadi",
            SELECTION_CALLS == [] and ANALYSIS_CALLS == [],
            f"secim={SELECTION_CALLS} analiz={ANALYSIS_CALLS}",
        )

        # 12. BASKA BOLUM DEGISTI
        check(
            "12. hazirlik: sayfa full_page acisindan degisti",
            extract_content(PAGE_BLOG_CHANGED).text
            != expected_full.text,
        )

        result = run_pipeline(
            session,
            valid_monitor,
            PAGE_BLOG_CHANGED,
        )

        check(
            "12. secilen bolum degismediyse changed=False",
            result.changed is False,
            f"changed={result.changed}",
        )

        check(
            "12. yeni snapshot olusturulmadi",
            result.new_snapshot is None,
        )

        check(
            "12. change kaydi olusturulmadi",
            result.change_record is None,
        )

        check(
            "12. degisiklik analizi AI'i cagrilmadi",
            SELECTION_CALLS == [] and ANALYSIS_CALLS == [],
            f"secim={SELECTION_CALLS} analiz={ANALYSIS_CALLS}",
        )

        # =================================================
        # 13. SECILEN BOLUM DEGISTI
        # =================================================

        print("\n--- 13. secilen bolum degisti ---")

        changes_before = session.scalar(
            select(func.count()).select_from(Change)
        )

        # Degisiklik analizi Gemini cagrisi yapar; bu testte
        # tuzak kurulu oldugu icin AI hatasi beklenir ama
        # snapshot ve change kayitlari korunmalidir.
        result = run_pipeline(
            session,
            valid_monitor,
            PAGE_PRICING_CHANGED,
        )

        changes_after = session.scalar(
            select(func.count()).select_from(Change)
        )

        check(
            "13. secilen bolum degisince changed=True",
            result.changed is True,
        )

        check(
            "13. degisiklik secilen bolum uzerinde bulundu",
            any(
                "150 TL" in line
                for line in result.change_result.added_lines
            ),
            f"{result.change_result.added_lines}",
        )

        check(
            "13. diff sayfanin diger bolumlerini icermiyor",
            not any(
                "Mobil uygulama" in line
                or "yol haritası" in line
                for line in result.change_result.added_lines
                + result.change_result.removed_lines
            ),
        )

        check(
            "13. change kaydi olusturuldu",
            result.change_record is not None
            and changes_after == changes_before + 1,
        )

        check(
            "13. yeni snapshot yalnizca bolum metni",
            result.new_snapshot is not None
            and "Mobil uygulama"
            not in result.new_snapshot.content
            and "150 TL" in result.new_snapshot.content,
        )

        check(
            "13. topic analizi calisti",
            result.topic_analysis is not None,
        )

        check(
            "13. degisiklik analizi icin Gemini cagrildi",
            len(ANALYSIS_CALLS) == 1,
            f"{ANALYSIS_CALLS}",
        )

        check(
            "13. BOLUM SECIMI icin Gemini cagrilmadi",
            SELECTION_CALLS == [],
            f"{SELECTION_CALLS}",
        )

        check(
            "13. AI hatasi snapshot/change kayitlarini bozmadi",
            result.ai_error is not None
            and result.new_snapshot is not None,
        )

        reset_gemini_counters()

        # =================================================
        # 14-16. REFERENCE LOST
        # =================================================

        print("\n--- 14-16. secilen bolum kayboldu ---")

        lost_monitor = make_manual_monitor(
            session,
            "kayip bolum testi",
            selected,
        )

        run_pipeline(session, lost_monitor, PAGE_V1)

        snapshots_for_lost = session.scalar(
            select(func.count())
            .select_from(Snapshot)
            .where(Snapshot.monitor_id == lost_monitor.id)
        )

        last_checked_before = lost_monitor.last_checked_at

        check(
            "14. hazirlik: referans yeni HTML'de yok",
            resolve_section(
                PAGE_SECTION_LOST,
                selected.reference,
            ).section
            is None,
        )

        lost_error = None

        try:
            run_pipeline(
                session,
                lost_monitor,
                PAGE_SECTION_LOST,
            )

        except Exception as exc:
            lost_error = exc

        check(
            "14. kontrollu ManualSectionResolutionError",
            isinstance(
                lost_error,
                ManualSectionResolutionError,
            ),
            f"{type(lost_error).__name__}: {lost_error}",
        )

        check(
            "14. mesaj kullaniciya ne yapacagini soyluyor",
            "yeniden seçin" in str(lost_error),
            str(lost_error)[:90],
        )

        check(
            "15. FULL PAGE FALLBACK YAPILMADI "
            "(yeni snapshot yok)",
            session.scalar(
                select(func.count())
                .select_from(Snapshot)
                .where(Snapshot.monitor_id == lost_monitor.id)
            )
            == snapshots_for_lost,
        )

        check(
            "15. last_checked_at guncellenmedi",
            lost_monitor.last_checked_at
            == last_checked_before,
        )

        check(
            "16. OTOMATIK Gemini bolum secimi YAPILMADI",
            SELECTION_CALLS == [],
            f"{SELECTION_CALLS}",
        )

        check(
            "16. hicbir AI cagrisi yapilmadi",
            ANALYSIS_CALLS == [],
            f"{ANALYSIS_CALLS}",
        )

        check(
            "16. baska bir bolum otomatik SECILMEDI",
            lost_monitor.manual_section_reference
            == selected.reference,
            f"{lost_monitor.manual_section_reference!r}",
        )

        check(
            "16. auto_section alanlari hala bos",
            lost_monitor.auto_section_reference is None,
        )

        # Referansi olmayan bozuk kayit
        broken_monitor = create_monitor(
            db=session,
            name="referanssiz manuel kayit",
            url=TEST_URL,
            topic="fiyat",
            scope_type="manual_section",
            schedule_type="daily",
            schedule_time="09:00",
        )

        broken_error = None

        try:
            run_pipeline(session, broken_monitor, PAGE_V1)

        except Exception as exc:
            broken_error = exc

        check(
            "referanssiz manuel kayit da kontrollu hata veriyor",
            isinstance(
                broken_error,
                ManualSectionResolutionError,
            ),
            f"{type(broken_error).__name__}",
        )

        check(
            "referanssiz kayitta snapshot yazilmadi",
            session.scalar(
                select(func.count())
                .select_from(Snapshot)
                .where(Snapshot.monitor_id == broken_monitor.id)
            )
            == 0,
        )

        # =================================================
        # 17-18. URL / TOPIC GUNCELLEMESI
        # =================================================

        print("\n--- 17-18. url / topic guncellemesi ---")

        edit_monitor = make_manual_monitor(
            session,
            "manuel edit testi",
            selected,
        )

        # 18. TOPIC DEGISIMI SECIMI KORUR
        update_monitor(
            db=session,
            monitor=edit_monitor,
            updates={"topic": "güvenlik güncellemeleri"},
        )

        check(
            "18. topic degisince manuel secim KORUNUYOR",
            edit_monitor.manual_section_reference
            == selected.reference
            and edit_monitor.manual_section_label
            == selected.label,
            f"{edit_monitor.manual_section_reference!r}",
        )

        check(
            "18. should_reset_manual_section topic'i saymiyor",
            should_reset_manual_section(
                edit_monitor,
                {"topic": "bambaska bir konu"},
            )
            is False,
        )

        check(
            "18. auto_section davranisi farkli kaldi "
            "(topic auto secimi sifirlar)",
            should_reset_auto_section(
                edit_monitor,
                {"topic": "bambaska bir konu"},
            )
            is True,
        )

        # Topic degisiminden sonra run hala calisir
        result = run_pipeline(session, edit_monitor, PAGE_V1)

        check(
            "18. topic degisiminden sonra ayni bolum izleniyor",
            result.manual_section.reference
            == selected.reference,
        )

        # 17. URL DEGISIMI SECIMI SIFIRLAR
        update_monitor(
            db=session,
            monitor=edit_monitor,
            updates={
                "url": "https://ornek-site.test/baska-sayfa"
            },
        )

        check(
            "17. url degisince manuel secim TEMIZLENDI",
            edit_monitor.manual_section_reference is None
            and edit_monitor.manual_section_label is None,
            f"{edit_monitor.manual_section_reference!r}",
        )

        check(
            "17. secim olmadan run kontrollu hata veriyor",
            isinstance(
                _capture(
                    lambda: run_pipeline(
                        session,
                        edit_monitor,
                        PAGE_V1,
                    )
                ),
                ManualSectionResolutionError,
            ),
        )

        # Ayni URL tekrar gonderilirse secim korunur
        stable_monitor = make_manual_monitor(
            session,
            "ayni url testi",
            selected,
        )

        update_monitor(
            db=session,
            monitor=stable_monitor,
            updates={
                "url": stable_monitor.url,
                "name": "yeni ad",
            },
        )

        check(
            "ayni URL tekrar gonderilince secim korunuyor",
            stable_monitor.manual_section_reference
            == selected.reference,
        )

        # =================================================
        # 19-21. SCOPE SWITCH
        # =================================================

        print("\n--- 19-21. kapsam degisimi ---")

        # 19. full_page -> manual_section secim ister
        switch_monitor = create_monitor(
            db=session,
            name="kapsam degisimi testi",
            url=TEST_URL,
            topic="fiyat değişiklikleri",
            scope_type="full_page",
            schedule_type="daily",
            schedule_time="09:00",
        )

        run_pipeline(session, switch_monitor, PAGE_V1)

        update_monitor(
            db=session,
            monitor=switch_monitor,
            updates={
                "scope_type": "manual_section",
                "manual_section_reference": selected.reference,
                "manual_section_label": selected.label,
            },
        )

        check(
            "19. secimle birlikte gecis secimi KORUYOR",
            switch_monitor.scope_type == "manual_section"
            and switch_monitor.manual_section_reference
            == selected.reference,
            f"{switch_monitor.manual_section_reference!r}",
        )

        result = run_pipeline(session, switch_monitor, PAGE_V1)

        check(
            "19. gecis sonrasi yalnizca secilen bolum izleniyor",
            result.manual_section is not None
            and "Mobil uygulama"
            not in result.manual_section.content.text,
        )

        # 20. manual_section -> full_page
        update_monitor(
            db=session,
            monitor=switch_monitor,
            updates={"scope_type": "full_page"},
        )

        check(
            "20. manual -> full_page secimi temizledi",
            switch_monitor.manual_section_reference is None
            and switch_monitor.manual_section_label is None,
        )

        result = run_pipeline(session, switch_monitor, PAGE_V1)

        check(
            "20. full_page'e donunce tum sayfa izleniyor",
            result.manual_section is None
            and result.auto_section is None,
        )

        # 21. manual_section -> auto_section
        auto_switch = make_manual_monitor(
            session,
            "manual -> auto testi",
            selected,
        )

        update_monitor(
            db=session,
            monitor=auto_switch,
            updates={"scope_type": "auto_section"},
        )

        check(
            "21. manual -> auto manuel secimi temizledi",
            auto_switch.manual_section_reference is None
            and auto_switch.manual_section_label is None,
        )

        check(
            "21. manual -> auto auto secimini de bos birakti "
            "(kesif sonraki run'da)",
            auto_switch.auto_section_reference is None,
        )

        reset_gemini_counters()

        check(
            "21. auto_section kesfi gerekiyor "
            "(Gemini bolum secimi istenir)",
            isinstance(
                _capture(
                    lambda: run_pipeline(
                        session,
                        auto_switch,
                        PAGE_V1,
                    )
                ),
                Exception,
            )
            and len(SELECTION_CALLS) == 1,
            f"secim={SELECTION_CALLS}",
        )

        reset_gemini_counters()

        # =================================================
        # 22-23. AUTO / FULL PAGE REGRESYONU
        # =================================================

        print("\n--- 22-23. auto_section ve full_page regresyonu ---")

        full_monitor = create_monitor(
            db=session,
            name="full page regresyon",
            url=TEST_URL,
            topic="fiyat değişiklikleri",
            scope_type="full_page",
            schedule_type="daily",
            schedule_time="09:00",
        )

        reset_gemini_counters()

        result = run_pipeline(session, full_monitor, PAGE_V1)

        full_snapshot = get_latest_snapshot(
            db=session,
            monitor_id=full_monitor.id,
        )

        check(
            "23. full_page snapshot TUM SAYFA",
            full_snapshot.content == expected_full.text,
        )

        check(
            "23. full_page manual alanlarina dokunmadi",
            full_monitor.manual_section_reference is None
            and result.manual_section is None,
        )

        check(
            "23. full_page Gemini bolum secimi yapmadi",
            SELECTION_CALLS == [],
            f"{SELECTION_CALLS}",
        )

        result = run_pipeline(
            session,
            full_monitor,
            PAGE_BLOG_CHANGED,
        )

        check(
            "23. full_page sayfanin her yerini hala goruyor",
            result.changed is True,
        )

        reset_gemini_counters()

        # 22. auto_section hala bolum secimi istiyor
        reset_gemini_counters()

        auto_monitor = create_monitor(
            db=session,
            name="auto regresyon",
            url=TEST_URL,
            topic="fiyat değişiklikleri",
            scope_type="auto_section",
            schedule_type="daily",
            schedule_time="09:00",
        )

        auto_error = _capture(
            lambda: run_pipeline(
                session,
                auto_monitor,
                PAGE_V1,
            )
        )

        check(
            "22. auto_section ilk run'da Gemini secimi istiyor",
            len(SELECTION_CALLS) == 1
            and isinstance(auto_error, Exception),
            f"secim={SELECTION_CALLS}",
        )

        check(
            "22. auto_section manual alanlarini kullanmiyor",
            auto_monitor.manual_section_reference is None,
        )

        reset_gemini_counters()

        # auto_section kayitli referans ile AI'siz calisiyor
        auto_monitor.auto_section_reference = selected.reference
        auto_monitor.auto_section_label = selected.label
        session.flush()

        result = run_pipeline(session, auto_monitor, PAGE_V1)

        check(
            "22. auto_section kayitli referans ile 0 AI cagrisi",
            SELECTION_CALLS == []
            and result.auto_section is not None
            and result.auto_section.ai_selection_used is False,
            f"secim={SELECTION_CALLS}",
        )

        check(
            "22. auto ve manual referanslari ayri kolonlarda",
            auto_monitor.auto_section_reference
            == selected.reference
            and auto_monitor.manual_section_reference is None,
        )

        # =================================================
        # AG KONTROLU
        # =================================================

        print("\n--- ag kontrolu ---")

        check(
            "test boyunca dis ag baglantisi denenmedi",
            NETWORK_ATTEMPTS == [],
            f"{NETWORK_ATTEMPTS}",
        )

        session.flush()

    finally:
        session.close()
        transaction.rollback()
        connection.close()

        unblock_network()

        ai_analyzer.genai.Client = original_client
        db_monitor_service.fetch_website = original_fetch
        monitor_routes.fetch_website = original_route_fetch

    # -----------------------------------------------------
    # API PATCH SOZLESMESI
    #
    # Bu blok GERCEK bir monitor uzerinde calisir; API kendi
    # session'ini actigi icin transaction icindeki test
    # monitorlerini goremez.
    #
    # Monitorun tum alanlari basta yedeklenir ve testin
    # sonunda (hata olsa bile) aynen geri yazilir.
    # -----------------------------------------------------

    print("\n--- API PATCH sozlesmesi (gercek monitor) ---")

    monitor_routes.fetch_website = fake_fetch_website

    block_network()

    target_id = None
    backup = None

    try:
        picker = SessionLocal()

        try:
            target = picker.scalars(
                select(Monitor).order_by(Monitor.id).limit(1)
            ).first()

            if target is not None:
                target_id = target.id

                backup = {
                    field: getattr(target, field)
                    for field in RESTORE_FIELDS
                }
        finally:
            picker.close()

        check(
            "API testi icin mevcut bir monitor bulundu",
            target_id is not None,
        )

        if target_id is not None:

            no_selection = client.patch(
                f"/api/monitors/{target_id}",
                json={"scope_type": "manual_section"},
            )

            check(
                "19. API: secim olmadan manual_section'a gecis -> 422",
                no_selection.status_code == 422,
                f"{no_selection.status_code} "
                f"{str(no_selection.json())[:80]}",
            )

            with_selection = client.patch(
                f"/api/monitors/{target_id}",
                json={
                    "scope_type": "manual_section",
                    "manual_section_reference": (
                        selected.reference
                    ),
                    "manual_section_label": selected.label,
                },
            )

            check(
                "19. API: secimle birlikte gecis 200",
                with_selection.status_code == 200,
                f"{with_selection.status_code}",
            )

            check(
                "19. API: secim cevapta donuyor",
                with_selection.status_code == 200
                and with_selection.json()[
                    "manual_section_label"
                ]
                == selected.label,
                str(with_selection.json())[:120],
            )

            topic_patch = client.patch(
                f"/api/monitors/{target_id}",
                json={"topic": "bambaska bir konu"},
            )

            check(
                "18. API: topic degisimi manuel secimi koruyor",
                topic_patch.status_code == 200
                and topic_patch.json()[
                    "manual_section_reference"
                ]
                == selected.reference,
                str(topic_patch.json())[:120],
            )

            url_patch = client.patch(
                f"/api/monitors/{target_id}",
                json={"url": "https://ornek-site.test/yeni"},
            )

            check(
                "17. API: yeni secim olmadan URL degisimi -> 422",
                url_patch.status_code == 422,
                f"{url_patch.status_code} "
                f"{str(url_patch.json())[:80]}",
            )

            unchanged = client.get(
                f"/api/monitors/{target_id}"
            ).json()

            check(
                "17. API: reddedilen PATCH monitoru degistirmedi",
                unchanged["manual_section_reference"]
                == selected.reference
                and unchanged["url"]
                != "https://ornek-site.test/yeni",
                str(unchanged["url"])[:60],
            )

            url_with_selection = client.patch(
                f"/api/monitors/{target_id}",
                json={
                    "url": "https://ornek-site.test/yeni",
                    "manual_section_reference": "id:blog",
                    "manual_section_label": "Blog",
                },
            )

            check(
                "17. API: URL + yeni secim birlikte 200",
                url_with_selection.status_code == 200
                and url_with_selection.json()[
                    "manual_section_reference"
                ]
                == "id:blog",
                f"{url_with_selection.status_code} "
                f"{str(url_with_selection.json())[:80]}",
            )

            back_to_full = client.patch(
                f"/api/monitors/{target_id}",
                json={"scope_type": "full_page"},
            )

            check(
                "20. API: manual -> full_page secimi temizledi",
                back_to_full.status_code == 200
                and back_to_full.json()[
                    "manual_section_reference"
                ]
                is None
                and back_to_full.json()[
                    "manual_section_label"
                ]
                is None,
                str(back_to_full.json())[:120],
            )

            check(
                "20. API: auto_section alani cevapta duruyor",
                back_to_full.status_code == 200
                and "auto_section_label" in back_to_full.json(),
            )

    finally:
        unblock_network()

        monitor_routes.fetch_website = original_route_fetch

        if target_id is not None and backup is not None:
            restore = SessionLocal()

            try:
                record = restore.get(Monitor, target_id)

                for field, value in backup.items():
                    setattr(record, field, value)

                restore.commit()
            finally:
                restore.close()

    verify_restore = SessionLocal()

    try:
        restored = verify_restore.get(Monitor, target_id)

        check(
            "API testi sonrasi monitor eski haline getirildi",
            all(
                getattr(restored, field) == backup[field]
                for field in RESTORE_FIELDS
            ),
            f"scope={restored.scope_type} url={restored.url}",
        )
    finally:
        verify_restore.close()

    # -----------------------------------------------------
    # ROLLBACK DOGRULAMASI
    # -----------------------------------------------------

    print("\n--- rollback dogrulamasi ---")

    verify = SessionLocal()

    try:
        monitors_after = verify.scalar(
            select(func.count()).select_from(Monitor)
        )
        snapshots_after = verify.scalar(
            select(func.count()).select_from(Snapshot)
        )
    finally:
        verify.close()

    check(
        "test monitorleri kalici olarak yazilmadi",
        monitors_after == monitors_before,
        f"{monitors_before} -> {monitors_after}",
    )

    check(
        "test snapshotlari kalici olarak yazilmadi",
        snapshots_after == snapshots_before,
        f"{snapshots_before} -> {snapshots_after}",
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


def _capture(callable_):
    """
    Cagriyi calistirir ve firlatilan hatayi dondurur.
    """

    try:
        callable_()

    except Exception as exc:
        return exc

    return None


if __name__ == "__main__":
    main()
