"""
auto_section (Konuyla ilgili alanı otomatik bul) testleri.

GERCEK GEMINI API CAGRISI YAPILMAZ.
GERCEK HTTP ISTEGI YAPILMAZ.

- Gemini sahte (fake) client ile karsilanir.
- fetch_website sahte HTML dondurur.
- Test suresince dis ag baglantisi bloklanir ve
  hic denenmedigi ayrica assert edilir.
- Tum DB islemleri dis bir transaction icinde calisir ve
  sonunda ROLLBACK edilir; gercek monitor kayitlari bozulmaz,
  cop monitor birakilmaz.

Calistirma:
    python -m tests.test_auto_section
"""

import asyncio
import socket

from dataclasses import dataclass

from google.genai import errors as genai_errors
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database import SessionLocal, engine
from app.models.change import Change
from app.models.monitor import Monitor
from app.models.notification import Notification
from app.models.snapshot import Snapshot

from app.repositories.monitor_repository import (
    create_monitor,
    should_reset_auto_section,
    update_monitor,
)
from app.repositories.snapshot_repository import get_latest_snapshot

from app.services import ai_analyzer
from app.services import db_monitor_service
from app.services.content_extractor import extract_content
from app.services.db_monitor_service import run_database_monitor
from app.services.monitor_run_service import execute_monitor_run
from app.services.section_extractor import (
    MIN_CANDIDATE_TEXT_LENGTH,
    extract_section_candidates,
    normalize_label,
    resolve_section,
)
from app.services.section_selector import (
    AutoSectionSelectionError,
    build_selection_prompt,
    select_section_with_ai,
)


PASSED = []
FAILED = []


PRIMARY_MODEL = "gemini-3.6-flash"
FALLBACK_MODEL = "gemini-3.7-flash"


TOPIC = "fiyat değişiklikleri"


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


# Ilk hali
PAGE_V1 = build_page(PRICING_V1, BLOG_V1)

# Yalnizca SECILEN bolum degisti
PAGE_PRICING_CHANGED = build_page(PRICING_V2, BLOG_V1)

# Yalnizca BASKA bir bolum degisti (secilen bolum ayni)
PAGE_BLOG_CHANGED = build_page(PRICING_V1, BLOG_V2)

# Secilen bolum kayboldu (id ve baslik degisti)
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


async def fake_fetch_website(url: str) -> FakeFetchResult:

    return FakeFetchResult(
        url=url,
        final_url=url,
        status_code=200,
        content_type="text/html; charset=utf-8",
        html=CURRENT_HTML["value"],
    )


# ---------------------------------------------------------
# SAHTE GEMINI CLIENT
#
# Bolum secimi ve degisiklik analizi prompt'tan ayirt edilir;
# boylece "bu run'da bolum secimi cagrisi yapildi mi"
# kesin olarak sayilabilir.
# ---------------------------------------------------------

SELECTION_PROMPT_MARKER = "bölüm seçme motorusun"


ANALYSIS_JSON = (
    '{"relevant": true, "importance": "high", '
    '"category": "pricing", '
    '"summary": "Başlangıç paketi fiyatı güncellendi.", '
    '"notification_recommended": true, '
    '"evidence_text": "Başlangıç paketi aylık 150 TL olarak sunulmaktadır.", '
    '"evidence_type": "added"}'
)


@dataclass
class FakeResponse:
    text: str


class FakeModels:
    def __init__(self, controller):
        self._controller = controller

    async def generate_content(self, model, contents, config):
        return self._controller.handle(
            model=model,
            prompt=contents,
            config=config,
        )


class FakeAio:
    def __init__(self, controller):
        self.models = FakeModels(controller)
        self._controller = controller

    async def aclose(self):
        self._controller.closed += 1


class FakeClient:
    def __init__(self, controller):
        self.aio = FakeAio(controller)


class GeminiController:
    """
    Sahte Gemini davranisini yonetir ve cagrilari sayar.
    """

    def __init__(self):
        self.selection_calls = []
        self.analysis_calls = []
        self.closed = 0

        # model adi -> davranis (str cevap veya Exception)
        self.selection_behaviour = {}
        self.analysis_behaviour = {}

        # Sabit cevap yerine kullanilacak secici.
        self.selection_response = None

    def reset(self):
        self.selection_calls = []
        self.analysis_calls = []
        self.closed = 0

    def handle(self, model, prompt, config):

        is_selection = SELECTION_PROMPT_MARKER in prompt

        if is_selection:
            self.selection_calls.append(
                {"model": model, "prompt": prompt, "config": config}
            )

            behaviour = self.selection_behaviour.get(model)

            if behaviour is None:
                raise AssertionError(
                    f"Beklenmeyen bolum secimi model cagrisi: {model}"
                )

            if isinstance(behaviour, BaseException):
                raise behaviour

            if callable(behaviour):
                return FakeResponse(text=behaviour(prompt))

            return FakeResponse(text=behaviour)

        self.analysis_calls.append(
            {"model": model, "prompt": prompt, "config": config}
        )

        behaviour = self.analysis_behaviour.get(model)

        if behaviour is None:
            raise AssertionError(
                f"Beklenmeyen analiz model cagrisi: {model}"
            )

        if isinstance(behaviour, BaseException):
            raise behaviour

        return FakeResponse(text=behaviour)


CONTROLLER = GeminiController()


def install_fake_client():

    def factory(*args, **kwargs):
        return FakeClient(CONTROLLER)

    ai_analyzer.genai.Client = factory


def selection_json(candidate_id: str, confidence: float = 0.92) -> str:
    return (
        f'{{"candidate_id": "{candidate_id}", '
        f'"reason": "Bu bölüm fiyat bilgilerini içeriyor.", '
        f'"confidence": {confidence}}}'
    )


def pricing_selector(prompt: str) -> str:
    """
    Prompt icindeki adaylardan basligi "fiyat" iceren ve
    metni en kisa olani secer.

    Boylece sahte model, gercek bir modelin yapacagi gibi
    konuya en yakin ve en dar bolumu secmis olur.
    """

    blocks = prompt.split("\n\n---\n\n")

    best_id = None
    best_length = None

    for block in blocks:

        candidate_id = None
        label = ""
        length = None

        for line in block.splitlines():

            if line.startswith("candidate_id: "):
                candidate_id = line.split(": ", 1)[1].strip()

            elif line.startswith("başlık: "):
                label = line.split(": ", 1)[1].strip()

            elif line.startswith("metin_uzunluğu: "):
                length = int(line.split(": ", 1)[1].strip())

        if candidate_id is None or length is None:
            continue

        normalized = normalize_label(label)

        if "fiyat" not in normalized and "ücret" not in normalized:
            continue

        if best_length is None or length < best_length:
            best_id = candidate_id
            best_length = length

    if best_id is None:
        # Fiyat bolumu yoksa ilk adayi sec.
        for block in blocks:
            for line in block.splitlines():
                if line.startswith("candidate_id: "):
                    best_id = line.split(": ", 1)[1].strip()
                    break
            if best_id:
                break

    return selection_json(best_id)


def api_error(code: int, status: str, message: str = "hata"):
    return genai_errors.APIError(
        code,
        {
            "error": {
                "code": code,
                "status": status,
                "message": message,
            }
        },
    )


def use_default_behaviour():
    CONTROLLER.selection_behaviour = {
        PRIMARY_MODEL: pricing_selector,
    }
    CONTROLLER.analysis_behaviour = {
        PRIMARY_MODEL: ANALYSIS_JSON,
    }


# ---------------------------------------------------------
# YARDIMCILAR
# ---------------------------------------------------------

def run_pipeline(session, monitor, html):
    """
    Verilen HTML ile bir monitor calistirmasi yapar ve
    bu calistirmadaki bolum secimi cagrisi sayisini dondurur.
    """

    CURRENT_HTML["value"] = html

    CONTROLLER.reset()

    result = asyncio.run(
        run_database_monitor(
            db=session,
            monitor=monitor,
        )
    )

    return result, len(CONTROLLER.selection_calls)


def make_monitor(session, name, scope_type, topic=TOPIC):

    return create_monitor(
        db=session,
        name=name,
        url="https://ornek-site.test/urun",
        topic=topic,
        scope_type=scope_type,
        schedule_type="daily",
        schedule_time="09:00",
    )


def main() -> None:

    print("=" * 70)
    print("AUTO SECTION TESTLERI")
    print("(gercek Gemini cagrisi YOK, gercek HTTP YOK, DB rollback)")
    print("=" * 70)

    original_client = ai_analyzer.genai.Client
    original_fetch = db_monitor_service.fetch_website
    original_primary = ai_analyzer.settings.gemini_model
    original_fallback = ai_analyzer.settings.gemini_fallback_model

    ai_analyzer.settings.gemini_model = PRIMARY_MODEL
    ai_analyzer.settings.gemini_fallback_model = FALLBACK_MODEL

    db_monitor_service.fetch_website = fake_fetch_website

    install_fake_client()
    use_default_behaviour()

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

    connection = engine.connect()
    transaction = connection.begin()

    session = Session(
        bind=connection,
        join_transaction_mode="create_savepoint",
    )

    try:
        # =================================================
        # BOLUM CIKARIMI (birim)
        # =================================================

        print("\n--- birim: aday bolum cikarimi ---")

        discovery = extract_section_candidates(PAGE_V1)

        check(
            "sayfa basligi korunuyor",
            discovery.page_title == "Örnek Ürün Sitesi",
            discovery.page_title,
        )

        check(
            "aday bolumler bulundu",
            len(discovery.candidates) >= 3,
            f"aday={len(discovery.candidates)}",
        )

        labels = [
            normalize_label(candidate.label)
            for candidate in discovery.candidates
        ]

        check(
            "fiyatlandirma adayi var",
            any("fiyat" in label for label in labels),
            f"{labels}",
        )

        candidate_text = "\n".join(
            candidate.text
            for candidate in discovery.candidates
        )

        check(
            "navigasyon adaylara girmedi",
            "menü bağlantıları" not in candidate_text,
        )

        check(
            "footer adaylara girmedi",
            "tüm hakları saklıdır" not in candidate_text,
        )

        check(
            "style icerigi adaylara girmedi",
            "color: red" not in candidate_text,
        )

        check(
            "cok kucuk bloklar aday yapilmadi",
            all(
                candidate.text_length >= MIN_CANDIDATE_TEXT_LENGTH
                for candidate in discovery.candidates
            ),
        )

        check(
            "her adayin candidate_id / label / reference / text alani var",
            all(
                candidate.candidate_id
                and candidate.label
                and candidate.reference
                and candidate.text
                for candidate in discovery.candidates
            ),
        )

        check(
            "candidate_id degerleri benzersiz",
            len({c.candidate_id for c in discovery.candidates})
            == len(discovery.candidates),
        )

        # -------------------------------------------------
        # REFERENCE STABILITESI (birim)
        # -------------------------------------------------

        print("\n--- birim: reference stabilitesi ---")

        stable = all(
            (
                resolve_section(
                    PAGE_V1,
                    candidate.reference,
                ).section
                is not None
            )
            for candidate in discovery.candidates
        )

        check(
            "tum adaylar kendi reference'i ile yeniden bulunuyor",
            stable,
        )

        identical = all(
            resolve_section(
                PAGE_V1,
                candidate.reference,
            ).section.text
            == candidate.text
            for candidate in discovery.candidates
        )

        check(
            "yeniden bulunan metin discovery metniyle BIREBIR ayni",
            identical,
        )

        id_references = [
            candidate.reference
            for candidate in discovery.candidates
            if candidate.reference.startswith("id:")
        ]

        check(
            "stabil element id'si varsa reference id: ile uretiliyor",
            len(id_references) >= 3,
            f"{id_references}",
        )

        check(
            "kirilgan nth-child CSS zinciri uretilmiyor",
            not any(
                "nth-child" in candidate.reference
                or ">" in candidate.reference
                for candidate in discovery.candidates
            ),
        )

        check(
            "bilinmeyen reference sessizce None doner",
            resolve_section(
                PAGE_V1,
                "id:boyle-bir-sey-yok",
            ).section
            is None,
        )

        check(
            "bozuk reference cokmez",
            resolve_section(PAGE_V1, "").section is None
            and resolve_section(PAGE_V1, None).section is None
            and resolve_section(PAGE_V1, "sacma").section is None,
        )

        # -------------------------------------------------
        # 8 / prompt: aday yoksa
        # -------------------------------------------------

        print("\n--- birim: aday bulunamayan sayfa ---")

        empty_discovery = extract_section_candidates(
            PAGE_NO_CANDIDATE
        )

        check(
            "8. uygun bolum yoksa aday listesi bos",
            empty_discovery.candidates == [],
            f"{len(empty_discovery.candidates)}",
        )

        # -------------------------------------------------
        # PROMPT ICERIGI
        # -------------------------------------------------

        print("\n--- birim: bolum secimi promptu ---")

        prompt = build_selection_prompt(
            topic=TOPIC,
            candidates=discovery.candidates,
            page_title=discovery.page_title,
        )

        check(
            "prompt tum HTML'i icermiyor",
            "<section" not in prompt
            and "<html" not in prompt
            and "<h2>" not in prompt,
        )

        check(
            "prompt yalnizca izin verilen candidate_id'leri listeliyor",
            all(
                candidate.candidate_id in prompt
                for candidate in discovery.candidates
            )
            and "UYDURMA" in prompt,
        )

        check(
            "prompt takip konusunu tasiyor",
            TOPIC in prompt,
        )

        check(
            "bolum secimi promptu degisiklik analizi promptundan ayri",
            SELECTION_PROMPT_MARKER in prompt
            and "EKLENEN içerikler" not in prompt,
        )

        # =================================================
        # 1. FULL PAGE DAVRANISI BIREBIR KORUNUYOR
        # =================================================

        print("\n--- 1. full_page regresyonu ---")

        full_monitor = make_monitor(
            session,
            "full page test",
            "full_page",
        )

        result, selection_calls = run_pipeline(
            session,
            full_monitor,
            PAGE_V1,
        )

        expected_full = extract_content(PAGE_V1)

        full_snapshot = get_latest_snapshot(
            db=session,
            monitor_id=full_monitor.id,
        )

        check(
            "1. full_page ilk run: first_run=True changed=False",
            result.first_run is True and result.changed is False,
        )

        check(
            "1. full_page snapshot icerigi TUM SAYFA",
            full_snapshot.content == expected_full.text,
        )

        check(
            "1. full_page snapshot basligi degismedi",
            full_snapshot.title == expected_full.title
            == "Örnek Ürün Sitesi",
        )

        check(
            "1. full_page icin bolum secimi AI cagrisi YOK",
            selection_calls == 0,
            f"cagri={selection_calls}",
        )

        check(
            "1. full_page monitorde auto_section alanlari null",
            full_monitor.auto_section_reference is None
            and full_monitor.auto_section_label is None,
        )

        check(
            "1. full_page result.auto_section None",
            result.auto_section is None,
        )

        # full_page ikinci run: baska bolum degisirse degisiklik gorulur
        result, selection_calls = run_pipeline(
            session,
            full_monitor,
            PAGE_BLOG_CHANGED,
        )

        check(
            "1. full_page sayfanin herhangi bir yerindeki "
            "degisikligi hala goruyor",
            result.changed is True,
        )

        check(
            "1. full_page akisinda hic bolum secimi cagrisi olmadi",
            selection_calls == 0,
        )

        # =================================================
        # 12. MANUAL SECTION AUTO PIPELINE'INA GIRMIYOR
        #
        # manual_section artik uygulanmis durumda; burada
        # dogrulanan sey auto_section'in ona karismamasidir.
        # manual_section'in kendi davranisi
        # tests/test_manual_section.py icinde test edilir.
        # =================================================

        print("\n--- 12. manual_section ayriligi ---")

        manual_candidate = next(
            candidate
            for candidate in discovery.candidates
            if candidate.reference == "id:urunler"
        )

        manual_monitor = create_monitor(
            db=session,
            name="manual section test",
            url="https://ornek-site.test/urun",
            topic=TOPIC,
            scope_type="manual_section",
            schedule_type="daily",
            schedule_time="09:00",
            manual_section_reference=manual_candidate.reference,
            manual_section_label=manual_candidate.label,
        )

        result, selection_calls = run_pipeline(
            session,
            manual_monitor,
            PAGE_V1,
        )

        manual_snapshot = get_latest_snapshot(
            db=session,
            monitor_id=manual_monitor.id,
        )

        check(
            "12. manual_section KULLANICI secimini izliyor",
            manual_snapshot.content == manual_candidate.text,
        )

        check(
            "12. manual_section tum sayfayi izlemiyor",
            manual_snapshot.content != expected_full.text
            and len(manual_snapshot.content)
            < len(expected_full.text),
        )

        check(
            "12. manual_section auto_section pipeline'ina girmedi",
            selection_calls == 0
            and manual_monitor.auto_section_reference is None,
            f"cagri={selection_calls}",
        )

        check(
            "12. manual_section result.auto_section None",
            result.auto_section is None,
        )

        check(
            "12. manual secim auto secimden BAGIMSIZ kolonlarda",
            manual_monitor.manual_section_reference
            == manual_candidate.reference
            and manual_monitor.auto_section_label is None,
        )

        # =================================================
        # 2 / 13 / 16. AUTO SECTION ILK RUN
        # =================================================

        print("\n--- 2, 13, 16. auto_section ilk run ---")

        auto_monitor = make_monitor(
            session,
            "auto section test",
            "auto_section",
        )

        check(
            "2. ilk run oncesi kayitli bolum yok",
            auto_monitor.auto_section_reference is None,
        )

        result, selection_calls = run_pipeline(
            session,
            auto_monitor,
            PAGE_V1,
        )

        auto_snapshot = get_latest_snapshot(
            db=session,
            monitor_id=auto_monitor.id,
        )

        check(
            "2. ilk run: first_run=True changed=False",
            result.first_run is True and result.changed is False,
        )

        check(
            "13. bolum secimi primary model ile yapildi",
            selection_calls == 1
            and CONTROLLER.selection_calls[0]["model"] == PRIMARY_MODEL,
            f"cagri={selection_calls}",
        )

        check(
            "2. secilen bolum referansi DB'ye kaydedildi",
            bool(auto_monitor.auto_section_reference),
            f"{auto_monitor.auto_section_reference!r}",
        )

        check(
            "2. secilen bolum etiketi DB'ye kaydedildi",
            "fiyat"
            in normalize_label(auto_monitor.auto_section_label),
            f"{auto_monitor.auto_section_label!r}",
        )

        check(
            "2. AI konuyla ilgili bolumu secti",
            "Başlangıç paketi aylık 100 TL"
            in auto_snapshot.content,
        )

        check(
            "2. snapshot yalnizca secilen bolumun metni",
            "Mobil uygulama" not in auto_snapshot.content
            and "yol haritası" not in auto_snapshot.content,
        )

        check(
            "2. snapshot navigasyon/footer icermiyor",
            "menü bağlantıları" not in auto_snapshot.content
            and "tüm hakları saklıdır" not in auto_snapshot.content,
        )

        check(
            "2. snapshot basligi sayfa <title> davranisini koruyor",
            auto_snapshot.title == "Örnek Ürün Sitesi",
            f"{auto_snapshot.title!r}",
        )

        selected_candidate = next(
            (
                candidate
                for candidate in discovery.candidates
                if candidate.reference
                == auto_monitor.auto_section_reference
            ),
            None,
        )

        check(
            "16. secilen icerik aday listesindeki bir adayla BIREBIR ayni",
            selected_candidate is not None
            and selected_candidate.text == auto_snapshot.content,
            f"{auto_monitor.auto_section_reference!r}",
        )

        check(
            "16. secilen icerik tum sayfa icerigiden KUCUK",
            len(auto_snapshot.content) < len(expected_full.text),
            f"{len(auto_snapshot.content)} < {len(expected_full.text)}",
        )

        check(
            "16. result.auto_section bilgisi dolu",
            result.auto_section is not None
            and result.auto_section.ai_selection_used is True
            and result.auto_section.rediscovered is False,
        )

        first_reference = auto_monitor.auto_section_reference
        first_label = auto_monitor.auto_section_label

        # =================================================
        # 3 / 5 / 15. SONRAKI RUN
        # =================================================

        print("\n--- 3, 5, 15. sonraki run (degisiklik yok) ---")

        result, selection_calls = run_pipeline(
            session,
            auto_monitor,
            PAGE_V1,
        )

        check(
            "3. kayitli reference kullanildi, AI'a SORULMADI",
            selection_calls == 0,
            f"cagri={selection_calls}",
        )

        check(
            "15. normal sonraki run'da 0 bolum secimi cagrisi",
            len(CONTROLLER.selection_calls) == 0,
        )

        check(
            "3. reference degismedi",
            auto_monitor.auto_section_reference == first_reference,
        )

        check(
            "3. ayni icerik -> changed=False",
            result.changed is False and result.first_run is False,
        )

        check(
            "3. result.auto_section ai_selection_used=False",
            result.auto_section is not None
            and result.auto_section.ai_selection_used is False,
        )

        # -------------------------------------------------
        # 5. BASKA BOLUM DEGISTI -> DEGISIKLIK YOK
        # -------------------------------------------------

        print("\n--- 5. sayfanin BASKA bolumu degisti ---")

        check(
            "5. hazirlik: full_page acisindan sayfa gercekten degisti",
            extract_content(PAGE_BLOG_CHANGED).text
            != expected_full.text,
        )

        result, selection_calls = run_pipeline(
            session,
            auto_monitor,
            PAGE_BLOG_CHANGED,
        )

        check(
            "5. secilen bolum degismediyse changed=False",
            result.changed is False,
            f"changed={result.changed}",
        )

        check(
            "5. yeni snapshot olusturulmadi",
            result.new_snapshot is None,
        )

        check(
            "5. change kaydi olusturulmadi",
            result.change_record is None,
        )

        check(
            "5. bu run'da da bolum secimi cagrisi yok",
            selection_calls == 0,
        )

        check(
            "5. degisiklik analizi AI'i da cagrilmadi",
            len(CONTROLLER.analysis_calls) == 0,
        )

        # =================================================
        # 4 / 17. SECILEN BOLUM DEGISTI
        # =================================================

        print("\n--- 4, 17. secilen bolum degisti ---")

        changes_before = session.scalar(
            select(func.count()).select_from(Change)
        )

        result, selection_calls = run_pipeline(
            session,
            auto_monitor,
            PAGE_PRICING_CHANGED,
        )

        changes_after = session.scalar(
            select(func.count()).select_from(Change)
        )

        check(
            "4. secilen bolum degisince changed=True",
            result.changed is True,
        )

        check(
            "4. degisiklik secilen bolum uzerinde bulundu",
            any(
                "150 TL" in line
                for line in result.change_result.added_lines
            ),
            f"{result.change_result.added_lines}",
        )

        check(
            "4. degisiklik satirlari sayfanin diger bolumlerini icermiyor",
            not any(
                "Mobil uygulama" in line
                for line in result.change_result.added_lines
                + result.change_result.removed_lines
            ),
        )

        check(
            "4. bolum secimi icin YINE AI cagrilmadi",
            selection_calls == 0,
        )

        check(
            "17. change kaydi olusturuldu",
            result.change_record is not None
            and changes_after == changes_before + 1,
        )

        check(
            "17. yeni snapshot yalnizca bolum metni",
            result.new_snapshot is not None
            and "Mobil uygulama" not in result.new_snapshot.content
            and "150 TL" in result.new_snapshot.content,
        )

        check(
            "17. keyword topic analizi calisti",
            result.topic_analysis is not None,
        )

        check(
            "17. Gemini degisiklik analizi calisti (bolum metni uzerinde)",
            len(CONTROLLER.analysis_calls) == 1
            and result.ai_analysis_record is not None
            and result.ai_error is None,
            f"analiz cagrisi={len(CONTROLLER.analysis_calls)}",
        )

        check(
            "17. degisiklik analizi promptu bozulmadi",
            "EKLENEN içerikler"
            in CONTROLLER.analysis_calls[0]["prompt"],
        )

        check(
            "17. AI analiz kaydi dogru model adiyla saklandi",
            result.ai_analysis_record.model_name == PRIMARY_MODEL,
        )

        check(
            "17. evidence dogrulamasi calisiyor",
            result.ai_analysis_record.evidence_text
            == "Başlangıç paketi aylık 150 TL olarak sunulmaktadır."
            and result.ai_analysis_record.evidence_type == "added",
            f"{result.ai_analysis_record.evidence_text!r}",
        )

        # -------------------------------------------------
        # 17. NOTIFICATION PIPELINE
        # -------------------------------------------------

        print("\n--- 17. notification pipeline ---")

        notifications_before = session.scalar(
            select(func.count()).select_from(Notification)
        )

        CURRENT_HTML["value"] = build_page(
            """
      <p>Başlangıç paketi aylık 180 TL olarak sunulmaktadır.</p>
      <p>Kurumsal paket aylık 500 TL seviyesindedir ve sınırsız kullanıcı içerir.</p>
""",
            BLOG_V1,
        )

        CONTROLLER.reset()

        CONTROLLER.analysis_behaviour = {
            PRIMARY_MODEL: ANALYSIS_JSON.replace(
                "150 TL", "180 TL"
            ),
        }

        outcome = asyncio.run(
            execute_monitor_run(
                db=session,
                monitor=auto_monitor,
            )
        )

        notifications_after = session.scalar(
            select(func.count()).select_from(Notification)
        )

        check(
            "17. auto_section run'i uygulama ici bildirim uretti",
            outcome.notification is not None
            and notifications_after == notifications_before + 1,
        )

        check(
            "17. bildirim uretilirken bolum secimi cagrisi yapilmadi",
            len(CONTROLLER.selection_calls) == 0,
        )

        CONTROLLER.analysis_behaviour = {
            PRIMARY_MODEL: ANALYSIS_JSON,
        }

        # =================================================
        # 6. SECILEN BOLUM KAYBOLDU -> REDISCOVERY
        # =================================================

        print("\n--- 6. secilen bolum kayboldu ---")

        check(
            "6. hazirlik: kayitli reference yeni HTML'de bulunamiyor",
            resolve_section(
                PAGE_SECTION_LOST,
                auto_monitor.auto_section_reference,
            ).section
            is None,
            f"{auto_monitor.auto_section_reference!r}",
        )

        reference_before_loss = auto_monitor.auto_section_reference

        result, selection_calls = run_pipeline(
            session,
            auto_monitor,
            PAGE_SECTION_LOST,
        )

        check(
            "6. kayip bolum crash uretmedi",
            result is not None,
        )

        check(
            "6. yeniden bolum kesfi icin 1 AI cagrisi yapildi",
            selection_calls == 1,
            f"cagri={selection_calls}",
        )

        check(
            "6. yeni reference kaydedildi",
            auto_monitor.auto_section_reference
            != reference_before_loss
            and bool(auto_monitor.auto_section_reference),
            f"{auto_monitor.auto_section_reference!r}",
        )

        check(
            "6. yeni etiket kaydedildi",
            "ücret"
            in normalize_label(auto_monitor.auto_section_label),
            f"{auto_monitor.auto_section_label!r}",
        )

        check(
            "6. rediscovered bayragi True",
            result.auto_section is not None
            and result.auto_section.rediscovered is True
            and result.auto_section.ai_selection_used is True,
        )

        check(
            "6. rediscovery sonrasi snapshot yine tum sayfa DEGIL",
            result.new_snapshot is None
            or "Mobil uygulama" not in result.new_snapshot.content,
        )

        # Rediscovery sonrasi normal run yine AI'siz olmali
        result, selection_calls = run_pipeline(
            session,
            auto_monitor,
            PAGE_SECTION_LOST,
        )

        check(
            "6. rediscovery sonrasi normal run yine AI'siz",
            selection_calls == 0,
            f"cagri={selection_calls}",
        )

        # =================================================
        # 7. GECERSIZ CANDIDATE_ID
        # =================================================

        print("\n--- 7. gecersiz candidate_id ---")

        invalid_monitor = make_monitor(
            session,
            "gecersiz candidate testi",
            "auto_section",
        )

        CONTROLLER.selection_behaviour = {
            PRIMARY_MODEL: selection_json("c999-uydurma"),
        }

        snapshots_for_invalid_before = session.scalar(
            select(func.count())
            .select_from(Snapshot)
            .where(Snapshot.monitor_id == invalid_monitor.id)
        )

        invalid_error = None

        try:
            run_pipeline(session, invalid_monitor, PAGE_V1)

        except Exception as exc:
            invalid_error = exc

        check(
            "7. uydurma candidate_id reddedildi",
            isinstance(invalid_error, AutoSectionSelectionError),
            f"{type(invalid_error).__name__}: {invalid_error}",
        )

        check(
            "7. hata mesaji uydurma id'yi soyluyor",
            "c999-uydurma" in str(invalid_error),
            str(invalid_error)[:80],
        )

        check(
            "7. gecersiz secimde snapshot YAZILMADI",
            session.scalar(
                select(func.count())
                .select_from(Snapshot)
                .where(Snapshot.monitor_id == invalid_monitor.id)
            )
            == snapshots_for_invalid_before,
        )

        check(
            "7. gecersiz secimde reference kaydedilmedi",
            invalid_monitor.auto_section_reference is None,
        )

        # Bos / bicimsiz cevaplar
        for label, behaviour in [
            ("bos cevap", ""),
            ("gecersiz json", "bu json degil"),
            ("sema disi cevap", '{"candidate_id": 5}'),
        ]:
            CONTROLLER.selection_behaviour = {
                PRIMARY_MODEL: behaviour,
            }

            error = None

            try:
                run_pipeline(session, invalid_monitor, PAGE_V1)

            except Exception as exc:
                error = exc

            check(
                f"7. {label}: AutoSectionSelectionError",
                isinstance(error, AutoSectionSelectionError),
                f"{type(error).__name__}",
            )

            check(
                f"7. {label}: fallback modele GECILMEDI",
                len(CONTROLLER.selection_calls) == 1,
                f"cagri={len(CONTROLLER.selection_calls)}",
            )

        use_default_behaviour()

        # =================================================
        # 8. ADAY YOK -> KONTROLLU HATA
        # =================================================

        print("\n--- 8. aday bolum yok ---")

        empty_monitor = make_monitor(
            session,
            "aday yok testi",
            "auto_section",
        )

        CONTROLLER.reset()

        empty_error = None

        try:
            run_pipeline(session, empty_monitor, PAGE_NO_CANDIDATE)

        except Exception as exc:
            empty_error = exc

        check(
            "8. kontrollu AutoSectionSelectionError firlatildi",
            isinstance(empty_error, AutoSectionSelectionError),
            f"{type(empty_error).__name__}: {empty_error}",
        )

        check(
            "8. AI'a hic sorulmadi (aday yokken cagri yapilmaz)",
            len(CONTROLLER.selection_calls) == 0,
        )

        check(
            "8. FULL PAGE FALLBACK YAPILMADI (snapshot yok)",
            session.scalar(
                select(func.count())
                .select_from(Snapshot)
                .where(Snapshot.monitor_id == empty_monitor.id)
            )
            == 0,
        )

        check(
            "8. last_checked_at guncellenmedi",
            empty_monitor.last_checked_at is None,
        )

        # =================================================
        # 14. SECIM: PRIMARY QUOTA -> FALLBACK
        # =================================================

        print("\n--- 14. bolum secimi kota fallback ---")

        fallback_monitor = make_monitor(
            session,
            "fallback secim testi",
            "auto_section",
        )

        CONTROLLER.selection_behaviour = {
            PRIMARY_MODEL: api_error(
                429, "RESOURCE_EXHAUSTED", "Quota exceeded"
            ),
            FALLBACK_MODEL: pricing_selector,
        }

        result, selection_calls = run_pipeline(
            session,
            fallback_monitor,
            PAGE_V1,
        )

        check(
            "14. kota hatasinda yedek model denendi",
            selection_calls == 2,
            f"cagri={selection_calls}",
        )

        check(
            "14. sira: once 3.6 sonra 3.7",
            [
                call["model"]
                for call in CONTROLLER.selection_calls
            ]
            == [PRIMARY_MODEL, FALLBACK_MODEL],
        )

        check(
            "14. fallback ile bolum secimi tamamlandi",
            bool(fallback_monitor.auto_section_reference),
            f"{fallback_monitor.auto_section_reference!r}",
        )

        check(
            "14. fallback secimi de yalnizca bolum metnini snapshot etti",
            "Mobil uygulama"
            not in get_latest_snapshot(
                db=session,
                monitor_id=fallback_monitor.id,
            ).content,
        )

        check(
            "14. 3.7 istegine sampling parametresi gonderilmedi",
            not {"temperature", "top_p", "top_k"}
            & set(
                CONTROLLER.selection_calls[1]["config"].model_dump(
                    exclude_none=True
                )
            ),
        )

        check(
            "14. 3.6 istegi temperature=0.0 kullaniyor",
            CONTROLLER.selection_calls[0]["config"].model_dump(
                exclude_none=True
            ).get("temperature")
            == 0.0,
        )

        check(
            "14. iki istek de structured output kullaniyor",
            all(
                call["config"].response_mime_type
                == "application/json"
                for call in CONTROLLER.selection_calls
            ),
        )

        # Iki model de kota -> kontrollu hata
        CONTROLLER.selection_behaviour = {
            PRIMARY_MODEL: api_error(429, "RESOURCE_EXHAUSTED"),
            FALLBACK_MODEL: api_error(429, "RESOURCE_EXHAUSTED"),
        }

        both_quota_monitor = make_monitor(
            session,
            "iki model kota testi",
            "auto_section",
        )

        quota_error = None

        try:
            run_pipeline(session, both_quota_monitor, PAGE_V1)

        except Exception as exc:
            quota_error = exc

        check(
            "14. iki model de kota -> AutoSectionSelectionError",
            isinstance(quota_error, AutoSectionSelectionError),
            f"{type(quota_error).__name__}",
        )

        check(
            "14. ucuncu model denenmedi (maksimum 2 cagri)",
            len(CONTROLLER.selection_calls) == 2,
            f"cagri={len(CONTROLLER.selection_calls)}",
        )

        check(
            "14. iki model de kota olunca snapshot yazilmadi",
            session.scalar(
                select(func.count())
                .select_from(Snapshot)
                .where(Snapshot.monitor_id == both_quota_monitor.id)
            )
            == 0,
        )

        # Kimlik dogrulama hatasi fallback tetiklememeli
        CONTROLLER.selection_behaviour = {
            PRIMARY_MODEL: api_error(401, "UNAUTHENTICATED"),
            FALLBACK_MODEL: pricing_selector,
        }

        auth_monitor = make_monitor(
            session,
            "auth hatasi testi",
            "auto_section",
        )

        auth_error = None

        try:
            run_pipeline(session, auth_monitor, PAGE_V1)

        except Exception as exc:
            auth_error = exc

        check(
            "14. 401 hatasinda yedek model DENENMEDI",
            len(CONTROLLER.selection_calls) == 1
            and isinstance(auth_error, AutoSectionSelectionError),
            f"cagri={len(CONTROLLER.selection_calls)}",
        )

        use_default_behaviour()

        # =================================================
        # 9 / 10 / 11. EDIT DAVRANISI
        # =================================================

        print("\n--- 9, 10, 11. edit davranisi ---")

        edit_monitor = make_monitor(
            session,
            "edit testi",
            "auto_section",
        )

        run_pipeline(session, edit_monitor, PAGE_V1)

        check(
            "edit oncesi bolum secimi mevcut",
            bool(edit_monitor.auto_section_reference),
        )

        # 9. TOPIC DEGISTI
        update_monitor(
            db=session,
            monitor=edit_monitor,
            updates={"topic": "güvenlik güncellemeleri"},
        )

        check(
            "9. topic degisince reference TEMIZLENDI",
            edit_monitor.auto_section_reference is None
            and edit_monitor.auto_section_label is None,
            f"{edit_monitor.auto_section_reference!r}",
        )

        result, selection_calls = run_pipeline(
            session,
            edit_monitor,
            PAGE_V1,
        )

        check(
            "9. topic degisiminden sonra yeniden kesif yapildi",
            selection_calls == 1
            and bool(edit_monitor.auto_section_reference),
            f"cagri={selection_calls}",
        )

        # 10. URL DEGISTI
        update_monitor(
            db=session,
            monitor=edit_monitor,
            updates={"url": "https://ornek-site.test/baska-sayfa"},
        )

        check(
            "10. url degisince reference TEMIZLENDI",
            edit_monitor.auto_section_reference is None
            and edit_monitor.auto_section_label is None,
        )

        run_pipeline(session, edit_monitor, PAGE_V1)

        check(
            "10. url degisiminden sonra yeniden kesif yapildi",
            bool(edit_monitor.auto_section_reference),
        )

        # AYNI DEGER TEKRAR GONDERILIRSE SECIM SILINMEZ
        stable_reference = edit_monitor.auto_section_reference

        update_monitor(
            db=session,
            monitor=edit_monitor,
            updates={
                "topic": edit_monitor.topic,
                "url": edit_monitor.url,
                "name": "yeni ad",
            },
        )

        check(
            "ayni topic/url tekrar gonderilince secim korunuyor",
            edit_monitor.auto_section_reference == stable_reference,
            f"{edit_monitor.auto_section_reference!r}",
        )

        check(
            "ilgisiz alan guncellemesi secimi silmiyor",
            should_reset_auto_section(
                edit_monitor,
                {"schedule_time": "10:00", "is_active": False},
            )
            is False,
        )

        # 11. FULL PAGE -> AUTO SECTION
        print("\n--- 11. full_page -> auto_section gecisi ---")

        switch_monitor = make_monitor(
            session,
            "kapsam degisimi testi",
            "full_page",
        )

        run_pipeline(session, switch_monitor, PAGE_V1)

        check(
            "11. full_page monitorde reference yok",
            switch_monitor.auto_section_reference is None,
        )

        update_monitor(
            db=session,
            monitor=switch_monitor,
            updates={"scope_type": "auto_section"},
        )

        check(
            "11. kapsam auto_section oldu, reference hala bos",
            switch_monitor.scope_type == "auto_section"
            and switch_monitor.auto_section_reference is None,
        )

        result, selection_calls = run_pipeline(
            session,
            switch_monitor,
            PAGE_V1,
        )

        check(
            "11. gecisten sonraki ilk run kesif yapti",
            selection_calls == 1
            and bool(switch_monitor.auto_section_reference),
            f"cagri={selection_calls}",
        )

        # auto_section -> full_page
        update_monitor(
            db=session,
            monitor=switch_monitor,
            updates={"scope_type": "full_page"},
        )

        check(
            "auto_section -> full_page gecisinde secim temizlendi",
            switch_monitor.auto_section_reference is None
            and switch_monitor.auto_section_label is None,
        )

        result, selection_calls = run_pipeline(
            session,
            switch_monitor,
            PAGE_V1,
        )

        check(
            "full_page'e donunce tekrar tum sayfa izleniyor",
            selection_calls == 0
            and result.auto_section is None,
        )

        # =================================================
        # AG KONTROLU
        # =================================================

        print("\n--- ag kontrolu ---")

        check(
            "test boyunca hicbir dis ag baglantisi denenmedi",
            NETWORK_ATTEMPTS == [],
            f"{NETWORK_ATTEMPTS}",
        )

        check(
            "sahte Gemini client her cagrida kapatildi",
            CONTROLLER.closed > 0,
        )

        session.flush()

    finally:
        session.close()
        transaction.rollback()
        connection.close()

        unblock_network()

        ai_analyzer.genai.Client = original_client
        db_monitor_service.fetch_website = original_fetch
        ai_analyzer.settings.gemini_model = original_primary
        ai_analyzer.settings.gemini_fallback_model = original_fallback

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


if __name__ == "__main__":
    main()
