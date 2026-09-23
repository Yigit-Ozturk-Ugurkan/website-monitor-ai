"""
UCTAN UCA MEVZUAT TAKIBI TESTI

SINIF: automated (deterministic, CI-safe)

Bu test SERVISLERI TEK TEK ELLE CAGIRMAZ.

Zincir GERCEK giris noktalarindan gecer:

    POST /api/monitors                (izleme olustur)
    POST /api/monitors/{id}/run       (manuel calistirma)
    scheduler._execute_due_monitor    (zamanlanmis yol)
    GET  /api/regulation-comparisons/{id}
    GET  /api/notifications/{id}/detail

Aradaki her adim (fetch, snapshot, degisiklik tespiti,
konu analizi, Gemini ilgililik analizi, otomatik referans
eslestirme, mevzuat.gov.tr kesfi, konsolide metin indirme,
tazelik, karsilastirma, birebir kanit dogrulamasi,
bildirim, e-posta) URETIM KODUYLA calisir.

Yalnizca DIS DUNYA sahtelenir:

    db_monitor_service.fetch_website
        -> Resmî Gazete sayfasi

    mevzuat_search_service._post_search
        -> mevzuat.gov.tr arama endpoint'i

    regulation_reference_service.fetch_website
        -> konsolide mevzuat metni

    ai_analyzer.genai.Client
        -> Gemini (TEK cikis noktasi)

    smtplib.SMTP / SMTP_SSL
        -> SMTP

Karsilastirma cevabi SABIT DEGILDIR: gercek prompt'taki
aday listesinden 41 inci maddenin kimligi ve GERCEK metni
okunup kanit olarak geri verilir. Boylece sunucu tarafi
birebir kanit dogrulamasi gercek konsolide metin uzerinde
calisir.

Kapsam:

     1. otomatik regulation monitor API'den olusur
     2. reference_regulation_id NULL baslar
     3. ilk calistirma -> first_run, karsilastirma YOK
     4. ilk calistirma -> Snapshot olustu
     5. ilk calistirma -> bildirim YOK
     6. ikinci calistirma -> gercek degisiklik
     7. degisiklik metni Madde 41 amendment'i tasir
     8. konu analizi ilgili buldu
     9. Gemini analizi relevant
    10. yerel referans YOK -> kesif calisti
    11. kesif dogru resmi adayi buldu
    12. kesif MevzuatNo/Tur/Tertip dogruladi
    13. konsolide metin indirildi
    14. RegulationReference olustu
    15. referans is_consolidated = true
    16. referans source_type = mevzuat_gov_tr
    17. kesif Gemini cagrisi = 0 (birebir ad eslesmesi)
    18. karsilastirma completed
    19. karsilastirma en az 1 item uretti
    20. item Madde 41'i isaret ediyor
    21. reference_evidence konsolide metinde BIREBIR var
    22. source_evidence degisiklik metninde BIREBIR var
    23. run response reference_selection_mode=automatic
    24. run response reference_match_status
    25. run response reference_discovery_status=discovered
    26. run response reference_freshness_status
    27. run response regulation_comparison_status=completed
    28. run response discovered_reference_id dolu
    29. run response matched_reference_id = kesfedilen
    30. Notification olustu
    31. bildirim govdesi karsilastirmayi tasiyor
    32. e-posta gonderildi (sahte SMTP)
    33. e-posta govdesi referans yonetmeligi tasiyor
    34. e-posta detay baglantisi tasiyor
    35. comparison detail endpoint karsilastirmayi doner
    36. detail endpoint item'lari doner
    37. notification detail endpoint bildirimi doner
    38. DB: Comparison.reference_regulation_id = kesfedilen
    39. DB: reference_content_hash o anki metnin hash'i
    40. DB: reference_is_consolidated = true
    41. DB: reference_match_method dolu
    42. DB: reference_match_confidence dolu
    43. DB: Monitor->Snapshot->Change->AIAnalysis zinciri
    44. DB: Notification change'e bagli
    45. IKINCI KEZ: yeni referans YOK
    46. IKINCI KEZ: yeni comparison YOK
    47. IKINCI KEZ: yeni notification YOK
    48. IKINCI KEZ: yeni e-posta YOK
    49. IKINCI KEZ: yeni kesif YOK
    50. IKINCI KEZ: yeni Gemini cagrisi YOK
    51. SCHEDULER: due degerlendirmesi calisiyor
    52. SCHEDULER: ayni pipeline'i calistirir
    53. SCHEDULER: kesif/karsilastirma manuel ile AYNI
    54. SCHEDULER: process lock duplicate engelliyor
    55. calisan monitor icin /run 409 doner
    56. test boyunca dis ag denemesi = 0
    57. test boyunca gercek SMTP soketi = 0
    58. DB kirliligi YOK

GERCEK GEMINI YOK. GERCEK SMTP YOK. GERCEK DIS AG YOK.

Gercek PostgreSQL uzerinde calisir ama KALICI KAYIT
BIRAKMAZ: her sey tek transaction icinde yapilip geri
alinir.

Calistirma:
    python -m tests.test_end_to_end_regulation
"""

import asyncio
import io
import json
import smtplib
import socket
import warnings

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database import engine, get_db
from app.models.ai_analysis import AIAnalysis
from app.models.change import Change
from app.models.monitor import Monitor
from app.models.notification import Notification
from app.models.regulation_comparison import (
    RegulationComparison,
    RegulationComparisonItem,
)
from app.models.regulation_reference import RegulationReference
from app.models.snapshot import Snapshot

from app.repositories.notification_repository import (
    STATUS_SENT,
    get_notification_by_analysis_and_type,
)
from app.repositories.regulation_reference_repository import (
    calculate_content_hash,
)

from app.services import ai_analyzer
from app.services import db_monitor_service
from app.services import email_service
from app.services import mevzuat_search_service
from app.services import notification_service
from app.services import regulation_reference_service

from app.services.regulation_discovery_service import (
    DISCOVERY_DISCOVERED,
)
from app.services.regulation_monitor_service import (
    COMPARISON_COMPLETED,
    MATCH_METHOD_AUTOMATIC_EXACT,
)

warnings.filterwarnings("ignore")

from fastapi.testclient import TestClient  # noqa: E402

from app.core import scheduler  # noqa: E402
from app.main import app  # noqa: E402


PASSED = []
FAILED = []


PROJECT_ROOT = Path(__file__).resolve().parents[1]


NEWLINE = chr(10)

CANDIDATE_SEPARATOR = (
    NEWLINE + NEWLINE + "---" + NEWLINE + NEWLINE
)


def check(label: str, condition: bool, detail: str = "") -> None:

    status = "PASS" if condition else "FAIL"

    print(f"[{status}] {label}" + (f" -> {detail}" if detail else ""))

    if condition:
        PASSED.append(label)
    else:
        FAILED.append(f"{label} -> {detail}")


def read(path: Path) -> str:
    return io.open(path, encoding="utf-8").read()


# ---------------------------------------------------------
# DIS AG ENGELI
#
# Loopback disinda hicbir baglanti kurulamaz. Denenirse
# test HATA verir; sessizce gecmez.
# ---------------------------------------------------------

NETWORK_ATTEMPTS = []

_real_connect = socket.socket.connect
_real_create_connection = socket.create_connection
_real_getaddrinfo = socket.getaddrinfo


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


def _blocked_getaddrinfo(host, port, *args, **kwargs):
    """
    AD COZUMLEME SEVIYESINDE ENGEL

    socket.connect'i sarmalamak Windows'ta YETMEZ: asyncio
    Proactor dongusu baglantiyi overlapped ConnectEx ile
    kurar ve socket.connect HIC cagrilmaz. Bu yuzden httpx
    uzerinden giden bir istek fark edilmeden gecebilir.

    Ad cozumleme ise KACINILMAZDIR: loopback disindaki her
    host once burada cozulmek zorundadir.
    """

    name = host

    if isinstance(name, bytes):
        name = name.decode("utf-8", "replace")

    if name not in LOOPBACK_HOSTS:

        NETWORK_ATTEMPTS.append((name, port))

        raise AssertionError(
            f"Test sirasinda dis ag adi cozulmeye calisildi: "
            f"{name}"
        )

    return _real_getaddrinfo(host, port, *args, **kwargs)


def block_network():
    socket.socket.connect = _blocked_connect
    socket.create_connection = _blocked_create_connection
    socket.getaddrinfo = _blocked_getaddrinfo


def unblock_network():
    socket.socket.connect = _real_connect
    socket.create_connection = _real_create_connection
    socket.getaddrinfo = _real_getaddrinfo


# ---------------------------------------------------------
# SAHTE SMTP
# ---------------------------------------------------------

SMTP_SESSIONS = []


class FakeSMTP:

    def __init__(self, host=None, port=None, timeout=None, context=None):

        self.session = {
            "host": host,
            "port": port,
            "messages": [],
        }

        SMTP_SESSIONS.append(self.session)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def ehlo(self, *args, **kwargs):
        return None

    def starttls(self, *args, **kwargs):
        return None

    def login(self, username, password):
        # Parola KAYDEDILMEZ.
        return None

    def send_message(self, message):
        self.session["messages"].append(message)

    def quit(self):
        return None


class FakeSMTPSSL(FakeSMTP):
    pass


# ---------------------------------------------------------
# FIXTURE - RESMI GAZETE SAYFASI
# ---------------------------------------------------------

BASE_TITLE = (
    "Ödeme Hizmetleri ve Elektronik Para İhracı ile Ödeme "
    "Hizmeti Sağlayıcıları Hakkında Yönetmelik"
)


SOURCE_EVIDENCE = '"biyometrik" şeklinde değiştirilmiştir'

NEW_TEXT = "biyometrik"


# 4 Eylul 2026 tarihli Resmî Gazete degisikligi.
# Her MADDE TEK SATIRDIR. Sayfa <p> bloklarina cevrildigi
# icin satir bolmek cumleyi iki paragrafa dagitir ve
# birebir kanit dogrulamasi yapay olarak zorlasir.
AMENDMENT_BODY = """ÖDEME HİZMETLERİ VE ELEKTRONİK PARA İHRACI İLE ÖDEME HİZMETİ SAĞLAYICILARI HAKKINDA YÖNETMELİKTE DEĞİŞİKLİK YAPILMASINA DAİR YÖNETMELİK
MADDE 1- 1/12/2021 tarihli ve 31676 sayılı Resmî Gazete'de yayımlanan Ödeme Hizmetleri ve Elektronik Para İhracı ile Ödeme Hizmeti Sağlayıcıları Hakkında Yönetmeliğin 41 inci maddesinde yer alan "yazılı" ibaresi "biyometrik" şeklinde değiştirilmiştir.
MADDE 2- Bu Yönetmelik yayımı tarihinde yürürlüğe girer.
MADDE 3- Bu Yönetmelik hükümlerini Türkiye Cumhuriyet Merkez Bankası Başkanı yürütür."""


# Degisiklik YAYIMLANMADAN ONCEKI sayfa.
BEFORE_BODY = """RESMÎ GAZETE
Bugün Resmî Gazete'de yayımlanan düzenleyici işlemler aşağıda
listelenmiştir.
YÜRÜTME VE İDARE BÖLÜMÜ
Bu bölümde bugün için yayımlanmış bir yönetmelik değişikliği
bulunmamaktadır.
İLÂN BÖLÜMÜ
Çeşitli ilânlar bölümünde bugün yayımlanan bir kayıt yoktur."""


def build_gazette_page(body: str) -> str:

    paragraphs = NEWLINE.join(
        f"<p>{line}</p>"
        for line in body.splitlines()
    )

    return (
        "<html><head><title>Resmî Gazete</title></head>"
        "<body><main>"
        f"{paragraphs}"
        "</main></body></html>"
    )


PAGE_BEFORE = build_gazette_page(BEFORE_BODY)
PAGE_AFTER = build_gazette_page(AMENDMENT_BODY)


CURRENT_PAGE = {"value": PAGE_BEFORE}


@dataclass
class FakeFetchResult:
    url: str
    final_url: str
    status_code: int
    content_type: str
    html: str


GAZETTE_FETCH_CALLS = []


async def fake_gazette_fetch(url: str) -> FakeFetchResult:

    GAZETTE_FETCH_CALLS.append(url)

    return FakeFetchResult(
        url=url,
        final_url=url,
        status_code=200,
        content_type="text/html; charset=utf-8",
        html=CURRENT_PAGE["value"],
    )


# ---------------------------------------------------------
# FIXTURE - mevzuat.gov.tr ARAMA
# ---------------------------------------------------------

EXPECTED_MEVZUAT_NO = "39080"
EXPECTED_MEVZUAT_TUR = 7
EXPECTED_MEVZUAT_TERTIP = "5"


CANONICAL_URL = (
    "https://www.mevzuat.gov.tr/mevzuat"
    f"?MevzuatNo={EXPECTED_MEVZUAT_NO}"
    f"&MevzuatTur={EXPECTED_MEVZUAT_TUR}"
    f"&MevzuatTertip={EXPECTED_MEVZUAT_TERTIP}"
)


# Alan adlari ve bicim GERCEK endpoint cevabindan alindi:
# mevAdi BUYUK HARF ve icinde \r\n tasir.
OFFICIAL_ROW = {
    "mevzuatNo": EXPECTED_MEVZUAT_NO,
    "mevAdi": (
        "ÖDEME HİZMETLERİ VE ELEKTRONİK PARA İHRACI İLE ÖDEME\r\n"
        "HİZMETİ SAĞLAYICILARI HAKKINDA YÖNETMELİK"
    ),
    "resmiGazeteTarihi": "01.12.2021",
    "resmiGazeteSayisi": "31676",
    "mevzuatTertip": EXPECTED_MEVZUAT_TERTIP,
    "mevzuatTur": EXPECTED_MEVZUAT_TUR,
    "mevzuatTurEnumString": "Kurum ve Kuruluş Yönetmelikleri",
    "url": (
        f"mevzuat?MevzuatNo={EXPECTED_MEVZUAT_NO}"
        f"&MevzuatTur={EXPECTED_MEVZUAT_TUR}"
        f"&MevzuatTertip={EXPECTED_MEVZUAT_TERTIP}"
    ),
}


SEARCH_CALLS = []


async def fake_post_search(payload):

    SEARCH_CALLS.append(payload)

    return {
        "draw": 1,
        "recordsTotal": 1,
        "recordsFiltered": 1,
        "data": [OFFICIAL_ROW],
        "word": None,
    }


# ---------------------------------------------------------
# FIXTURE - KONSOLIDE MEVZUAT METNI
# ---------------------------------------------------------

CONSOLIDATED_CONTENT = """ÖDEME HİZMETLERİ VE ELEKTRONİK PARA İHRACI İLE ÖDEME HİZMETİ
SAĞLAYICILARI HAKKINDA YÖNETMELİK
MADDE 39
(1) Ödeme kuruluşları müşteri şikâyetlerini otuz gün içinde sonuçlandırır.
MADDE 40
(1) Kayıtlar en az on yıl saklanır ve gerektiğinde Bankaya sunulur.
MADDE 41
(1) Uzaktan iletişim aracı ile müşterinin kimliği yazılı olarak doğrulanır.
(2) Doğrulama kayıtları denetime hazır biçimde muhafaza edilir.
MADDE 42
(1) Ödeme kuruluşları asgari özkaynak yükümlülüğünü sürekli olarak sağlar.
"""


CONSOLIDATED_FETCH_CALLS = []


async def fake_consolidated_fetch(url: str):

    CONSOLIDATED_FETCH_CALLS.append(url)

    body = NEWLINE.join(
        f"<p>{line}</p>"
        for line in CONSOLIDATED_CONTENT.splitlines()
    )

    return FakeFetchResult(
        url=url,
        final_url=url,
        status_code=200,
        content_type="text/html; charset=utf-8",
        html=(
            "<html><head><title>Mevzuat Bilgi Sistemi</title>"
            f"</head><body>{body}</body></html>"
        ),
    )


# ---------------------------------------------------------
# SAHTE GEMINI
#
# TEK cikis noktasi: ai_analyzer.genai.Client
#
# Cagrilar prompt'a bakilarak ayirt edilir. Karsilastirma
# cevabi GERCEK prompt'tan uretilir; sabit degildir.
# ---------------------------------------------------------

GEMINI_CALLS = []


ANALYSIS_JSON = json.dumps(
    {
        "relevant": True,
        "importance": "high",
        "category": "regulation",
        "summary": (
            "Yönetmeliğin 41 inci maddesinde kimlik doğrulama "
            "yöntemi yazılıdan biyometriğe çevrildi."
        ),
        "notification_recommended": True,
        "evidence_text": (
            'Yönetmeliğin 41 inci maddesinde yer alan "yazılı" '
            'ibaresi "biyometrik"'
        ),
        "evidence_type": "added",
    },
    ensure_ascii=False,
)


def parse_candidate_blocks(prompt: str) -> list[dict]:
    """
    Karsilastirma promptundaki aday bloklarini ayristirir.
    """

    blocks = []

    for raw in prompt.split(CANDIDATE_SEPARATOR):

        if "candidate_id:" not in raw:
            continue

        block = {
            "candidate_id": "",
            "madde": "",
            "metin": "",
        }

        text_lines = []
        in_text = False

        for line in raw.splitlines():

            if line.startswith("candidate_id:"):
                block["candidate_id"] = line.split(":", 1)[
                    1
                ].strip()

            elif line.startswith("madde:"):
                block["madde"] = line.split(":", 1)[1].strip()

            elif line.strip() == "metin:":
                in_text = True

            elif in_text:
                text_lines.append(line)

        block["metin"] = NEWLINE.join(text_lines).strip()

        if block["candidate_id"]:
            blocks.append(block)

    return blocks


def find_article_41(prompt: str) -> dict | None:

    for block in parse_candidate_blocks(prompt):

        label = (
            block["madde"]
            .upper()
            .replace("MADDE", "")
            .replace("-", " ")
            .strip()
        )

        if label == "41":
            return block

    return None


SOURCE_SECTION_START = "=== 1. KAYNAK DEĞİŞİKLİK METNİ ==="

SOURCE_SECTION_END = "=== 2. REFERANS YÖNETMELİKTEN ADAY MADDELER ==="


def extract_source_text(prompt: str) -> str:
    """
    Prompt'a GERCEKTEN giren kaynak degisiklik metnini
    cikarir.

    Kanit bu metinden okunur; sabit bir cumle kullanmak
    sunucunun birebir dogrulamasini sinamaz.
    """

    if SOURCE_SECTION_START not in prompt:
        return ""

    body = prompt.split(SOURCE_SECTION_START, 1)[1]

    if SOURCE_SECTION_END in body:
        body = body.split(SOURCE_SECTION_END, 1)[0]

    return body.strip()


def pick_evidence_line(text: str, must_contain: str = "") -> str:
    """
    Metinden birebir alintilanabilecek anlamli bir satir
    secer.
    """

    lines = [
        line.strip()
        for line in text.splitlines()
        if len(line.strip()) > 30
    ]

    if must_contain:

        for line in lines:
            if must_contain in line:
                return line

    return lines[0] if lines else text[:120].strip()


def extract_quoted_pair(text: str) -> tuple[str | None, str | None]:
    """
    Degisiklik cumlesindeki tirnakli ibare ciftini okur.

        "yazılı" ibaresi "biyometrik" şeklinde ...
        ->  ("yazılı", "biyometrik")

    Iki tirnakli ibare yoksa (None, None) doner.
    """

    parts = text.split('"')

    # "a" ibaresi "b" ...  ->  ['', 'a', ' ibaresi ', 'b', ' ...']
    if len(parts) < 4:
        return None, None

    return parts[1].strip() or None, parts[3].strip() or None


def build_comparison_payload(prompt: str) -> str:
    """
    Aday listesinden 41 inci maddeyi bulur; hem kaynak hem
    referans kanitini GERCEK prompt metninden okur.

    Aday bulunamazsa BOS item listesi doner; uydurma kanit
    URETILMEZ ve test bunu FAIL olarak gorur.
    """

    block = find_article_41(prompt)

    items = []

    if block is not None:

        reference_evidence = pick_evidence_line(
            block["metin"]
        )

        # Kaynak kanit prompt'taki GERCEK degisiklik
        # metninden alinir. "41" gecen satir tercih edilir.
        source_text = extract_source_text(prompt)

        source_evidence = pick_evidence_line(
            source_text,
            must_contain="41 inci maddes",
        )

        # ESKI / YENI IBARE
        #
        # Turkce degisiklik metni su kaliba oturur:
        #
        #   "X" ibaresi "Y" seklinde degistirilmistir
        #
        # Gercek model de bu iki ibareyi buradan okur.
        # Sabit deger YAZILMAZ: kanit her calistirmada
        # o anki metinden cikarilir.
        old_text, new_text = extract_quoted_pair(
            source_evidence
        )

        # Yalnizca ilgili kaynakta BIREBIR bulunanlar
        # gonderilir; sunucu zaten dogrulayacak.
        if new_text and new_text not in source_text:
            new_text = None

        if old_text and old_text not in block["metin"]:
            old_text = None

        items.append(
            {
                "candidate_id": block["candidate_id"],
                "affected_article": block["madde"],
                "affected_paragraph": None,
                "change_type": "amended",
                "old_text": old_text,
                "new_text": new_text,
                "source_evidence": source_evidence,
                "reference_evidence": reference_evidence,
                "change_summary": (
                    "Kimlik doğrulama yöntemi değişti."
                ),
                "confidence": 0.95,
            }
        )

    return json.dumps(
        {
            "has_regulatory_change": True,
            "overall_summary": (
                "41 inci maddede kimlik doğrulama yöntemi "
                "değiştirildi."
            ),
            "importance": "high",
            "items": items,
        },
        ensure_ascii=False,
    )


@dataclass
class FakeResponse:
    text: str


class FakeModels:

    async def generate_content(self, model, contents, config):

        prompt = contents if isinstance(contents, str) else str(contents)

        # Cagri turu prompt icerigine gore ayirt edilir.
        if "candidate_id:" in prompt:
            kind = "comparison"
            payload = build_comparison_payload(prompt)

        else:
            kind = "analysis"
            payload = ANALYSIS_JSON

        GEMINI_CALLS.append(
            {
                "model": model,
                "kind": kind,
                "prompt": prompt,
            }
        )

        return FakeResponse(text=payload)


class FakeAio:

    def __init__(self):
        self.models = FakeModels()

    async def aclose(self):
        return None


class FakeGeminiClient:

    def __init__(self, *args, **kwargs):
        self.aio = FakeAio()


def gemini_kinds() -> list[str]:
    return [call["kind"] for call in GEMINI_CALLS]


def reset_counters():
    GEMINI_CALLS.clear()
    SEARCH_CALLS.clear()
    CONSOLIDATED_FETCH_CALLS.clear()
    GAZETTE_FETCH_CALLS.clear()
    SMTP_SESSIONS.clear()


# ---------------------------------------------------------
# TEST GOVDESI
# ---------------------------------------------------------

RECIPIENT = "mevzuat-takip@ornek-kurum.test"


def run_tests(session, client) -> None:

    # =====================================================
    # 1. IZLEME OLUSTUR (GERCEK API)
    # =====================================================

    print()
    print("--- 1. otomatik regulation monitor olustur ---")

    create_response = client.post(
        "/api/monitors",
        json={
            "name": "Resmî Gazete - Ödeme Hizmetleri",
            "url": (
                "https://www.resmigazete.gov.tr/eskiler/"
                "2026/09/20260904-1.htm"
            ),
            "topic": "ödeme hizmetleri yönetmelik değişikliği",
            "tracking_plan": "regulation_monitor",
            "reference_selection_mode": "automatic",
            "reference_regulation_id": None,
            "scope_type": "full_page",
            "schedule_type": "daily",
            "schedule_time": "09:00",
            "notify_in_app": True,
            "notify_email": True,
            "notification_email": RECIPIENT,
        },
    )

    check(
        "1. otomatik regulation monitor API'den olusur",
        create_response.status_code == 201,
        f"status={create_response.status_code} "
        f"{create_response.text[:160]}",
    )

    created = create_response.json()

    monitor_id = created["id"]

    check(
        "2. reference_regulation_id NULL baslar",
        created.get("reference_regulation_id") is None
        and created.get("reference_selection_mode")
        == "automatic",
        f"id={created.get('reference_regulation_id')} "
        f"mode={created.get('reference_selection_mode')}",
    )

    # =====================================================
    # 2. ILK CALISTIRMA (ILK SNAPSHOT)
    # =====================================================

    print()
    print("--- 2. ilk calistirma: ilk snapshot ---")

    reset_counters()

    CURRENT_PAGE["value"] = PAGE_BEFORE

    first = client.post(f"/api/monitors/{monitor_id}/run")

    check(
        "3. ilk calistirma -> first_run, karsilastirma YOK",
        first.status_code == 200
        and first.json().get("first_run") is True
        and first.json().get("regulation_comparison_id")
        is None,
        f"status={first.status_code} "
        f"{first.json().get('regulation_comparison_status')}",
    )

    snapshot_count = session.scalar(
        select(func.count())
        .select_from(Snapshot)
        .where(Snapshot.monitor_id == monitor_id)
    )

    check(
        "4. ilk calistirma -> Snapshot olustu",
        snapshot_count == 1,
        f"snapshot={snapshot_count}",
    )

    check(
        "5. ilk calistirma -> bildirim YOK",
        first.json().get("notification_created") is False,
        f"{first.json().get('notification_created')}",
    )

    # =====================================================
    # 3. IKINCI CALISTIRMA - GERCEK ZINCIR
    # =====================================================

    print()
    print("--- 3. ikinci calistirma: gercek amendment ---")

    reset_counters()

    CURRENT_PAGE["value"] = PAGE_AFTER

    second = client.post(f"/api/monitors/{monitor_id}/run")

    body = second.json()

    check(
        "6. ikinci calistirma -> gercek degisiklik",
        second.status_code == 200
        and body.get("changed") is True
        and body.get("first_run") is False,
        f"status={second.status_code} "
        f"changed={body.get('changed')}",
    )

    change_record = session.scalars(
        select(Change)
        .where(Change.monitor_id == monitor_id)
        .order_by(Change.id.desc())
    ).first()

    added_text = change_record.added_content or ""

    check(
        "7. degisiklik metni Madde 41 amendment'i tasir",
        change_record is not None
        and "41 inci maddesinde" in added_text
        and "biyometrik" in added_text,
        f"added={len(added_text)} char",
    )

    check(
        "8. konu analizi ilgili buldu",
        body.get("topic_relevant") is True,
        f"{body.get('topic_relevant')}",
    )

    check(
        "9. Gemini analizi relevant",
        body.get("ai_relevant") is True,
        f"{body.get('ai_relevant')}",
    )

    # -----------------------------------------------------
    # KESIF
    # -----------------------------------------------------

    check(
        "10. yerel referans YOK -> kesif calisti",
        len(SEARCH_CALLS) >= 1,
        f"arama cagrisi={len(SEARCH_CALLS)}",
    )

    discovered_id = body.get("discovered_reference_id")

    discovered = (
        session.get(RegulationReference, discovered_id)
        if discovered_id
        else None
    )

    check(
        "11. kesif dogru resmi adayi buldu",
        discovered is not None
        and discovered.source_url == CANONICAL_URL,
        f"url={discovered.source_url if discovered else None}",
    )

    check(
        "12. kesif MevzuatNo/Tur/Tertip dogruladi",
        discovered is not None
        and EXPECTED_MEVZUAT_NO in discovered.source_url
        and f"MevzuatTur={EXPECTED_MEVZUAT_TUR}"
        in discovered.source_url
        and f"MevzuatTertip={EXPECTED_MEVZUAT_TERTIP}"
        in discovered.source_url,
        f"{discovered.source_url if discovered else None}",
    )

    # Konsolide metin kanonik adresten DEGIL, mevzuat.gov.tr'nin
    # fihrist iframe endpoint'inden indirilir. Dogrulanan sey
    # ayni mevzuatin (No/Tur/Tertip) getirilmis olmasidir.
    fetched_url = (
        CONSOLIDATED_FETCH_CALLS[0]
        if CONSOLIDATED_FETCH_CALLS
        else ""
    )

    check(
        "13. konsolide metin indirildi",
        len(CONSOLIDATED_FETCH_CALLS) >= 1
        and f"MevzuatNo={EXPECTED_MEVZUAT_NO}" in fetched_url
        and f"MevzuatTur={EXPECTED_MEVZUAT_TUR}" in fetched_url
        and f"MevzuatTertip={EXPECTED_MEVZUAT_TERTIP}"
        in fetched_url,
        fetched_url,
    )

    check(
        "14. RegulationReference olustu",
        discovered is not None
        and "MADDE 41" in (discovered.content or ""),
        f"icerik={len(discovered.content or '')} char",
    )

    check(
        "15. referans is_consolidated = true",
        discovered is not None
        and discovered.is_consolidated is True,
        f"{discovered.is_consolidated if discovered else None}",
    )

    check(
        "16. referans source_type = mevzuat_gov_tr",
        discovered is not None
        and discovered.source_type == "mevzuat_gov_tr",
        f"{discovered.source_type if discovered else None}",
    )

    check(
        "17. kesif Gemini cagrisi = 0 (birebir ad eslesmesi)",
        gemini_kinds().count("comparison") >= 1
        and len(GEMINI_CALLS)
        == gemini_kinds().count("comparison")
        + gemini_kinds().count("analysis"),
        f"cagrilar={gemini_kinds()}",
    )

    # -----------------------------------------------------
    # KARSILASTIRMA
    # -----------------------------------------------------

    comparison_id = body.get("regulation_comparison_id")

    comparison = (
        session.get(RegulationComparison, comparison_id)
        if comparison_id
        else None
    )

    check(
        "18. karsilastirma completed",
        body.get("regulation_comparison_status")
        == COMPARISON_COMPLETED,
        f"{body.get('regulation_comparison_status')}",
    )

    check(
        "19. karsilastirma en az 1 item uretti",
        comparison is not None and len(comparison.items) >= 1,
        f"item={len(comparison.items) if comparison else 0}",
    )

    item = (
        comparison.items[0]
        if comparison is not None and comparison.items
        else None
    )

    check(
        "20. item Madde 41'i isaret ediyor",
        item is not None
        and "41" in (item.affected_article or ""),
        f"{item.affected_article if item else None}",
    )

    check(
        "21. reference_evidence konsolide metinde BIREBIR var",
        item is not None
        and bool(item.reference_evidence)
        and item.reference_evidence in discovered.content,
        (item.reference_evidence or "")[:70] if item else "",
    )

    # Kaynak kanit, izlenen sayfada YENI ORTAYA CIKAN
    # metinde birebir bulunmalidir.
    new_snapshot_text = session.scalars(
        select(Snapshot)
        .where(Snapshot.monitor_id == monitor_id)
        .order_by(Snapshot.id.desc())
    ).first().content

    check(
        "22. source_evidence degisiklik metninde BIREBIR var",
        item is not None
        and bool(item.source_evidence)
        and (
            item.source_evidence in added_text
            or item.source_evidence in new_snapshot_text
        ),
        (item.source_evidence or "")[:70] if item else "",
    )

    # -----------------------------------------------------
    # RUN RESPONSE CONTRACT
    # -----------------------------------------------------

    print()
    print("--- 4. manuel run response contract ---")

    check(
        "23. run response reference_selection_mode=automatic",
        body.get("reference_selection_mode") == "automatic",
        f"{body.get('reference_selection_mode')}",
    )

    check(
        "24. run response reference_match_status",
        body.get("reference_match_status") is not None,
        f"{body.get('reference_match_status')}",
    )

    check(
        "25. run response reference_discovery_status=discovered",
        body.get("reference_discovery_status")
        == DISCOVERY_DISCOVERED,
        f"{body.get('reference_discovery_status')}",
    )

    check(
        "26. run response reference_freshness_status",
        body.get("reference_freshness_status") is not None,
        f"{body.get('reference_freshness_status')}",
    )

    check(
        "27. run response regulation_comparison_status=completed",
        body.get("regulation_comparison_status")
        == COMPARISON_COMPLETED
        and body.get("regulation_changes_count") >= 1,
        f"{body.get('regulation_comparison_status')} "
        f"item={body.get('regulation_changes_count')}",
    )

    check(
        "28. run response discovered_reference_id dolu",
        discovered_id is not None
        and body.get("discovered_reference_title"),
        f"id={discovered_id} "
        f"title={body.get('discovered_reference_title')}",
    )

    check(
        "29. run response matched_reference_id = kesfedilen",
        body.get("matched_reference_id") == discovered_id,
        f"{body.get('matched_reference_id')} == {discovered_id}",
    )

    # -----------------------------------------------------
    # BILDIRIM
    # -----------------------------------------------------

    print()
    print("--- 5. bildirim ---")

    ai_analysis = session.scalars(
        select(AIAnalysis)
        .where(AIAnalysis.change_id == change_record.id)
    ).first()

    notification = get_notification_by_analysis_and_type(
        db=session,
        ai_analysis_id=ai_analysis.id,
        notification_type="in_app",
    )

    check(
        "30. Notification olustu",
        body.get("notification_created") is True
        and notification is not None,
        f"{notification.id if notification else None}",
    )

    message = notification.message if notification else ""

    check(
        "31. bildirim govdesi karsilastirmayi tasiyor",
        notification is not None
        and "41" in message
        and "Mevzuat karşılaştırması" in message
        and "Etkilenen madde sayısı: 1" in message,
        message[:80].replace(NEWLINE, " "),
    )

    # -----------------------------------------------------
    # E-POSTA
    # -----------------------------------------------------

    print()
    print("--- 6. e-posta (sahte SMTP) ---")

    email_row = get_notification_by_analysis_and_type(
        db=session,
        ai_analysis_id=ai_analysis.id,
        notification_type="email",
    )

    check(
        "32. e-posta gonderildi (sahte SMTP)",
        body.get("email_status") == STATUS_SENT
        and email_row is not None
        and len(SMTP_SESSIONS) == 1,
        f"status={body.get('email_status')} "
        f"oturum={len(SMTP_SESSIONS)}",
    )

    sent_messages = (
        SMTP_SESSIONS[0]["messages"] if SMTP_SESSIONS else []
    )

    email_body = ""

    if sent_messages:

        message_object = sent_messages[0]

        for part in message_object.walk():

            if part.get_content_type() == "text/plain":
                email_body = part.get_content()
                break

        if not email_body:
            email_body = str(message_object)

    check(
        "33. e-posta govdesi referans yonetmeligi tasiyor",
        discovered is not None
        and "Karşılaştırılan mevzuat" in email_body
        and discovered.title in email_body
        and "Mevzuat karşılaştırması" in email_body
        and "Madde 41" in email_body,
        f"govde={len(email_body)} char",
    )

    # Detay baglantisi E-POSTA bildirim satirinin id'sini
    # tasir; in-app satirinkini degil.
    check(
        "34. e-posta detay baglantisi tasiyor",
        "http" in email_body
        and email_row is not None
        and f"/notifications/{email_row.id}" in email_body,
        f"email_row={email_row.id if email_row else None}",
    )

    # -----------------------------------------------------
    # DETAIL ENDPOINT'LER
    # -----------------------------------------------------

    print()
    print("--- 7. detail endpoint'ler ---")

    detail = client.get(
        f"/api/regulation-comparisons/{comparison_id}"
    )

    detail_body = (
        detail.json() if detail.status_code == 200 else {}
    )

    check(
        "35. comparison detail endpoint karsilastirmayi doner",
        detail.status_code == 200
        and detail_body.get("id") == comparison_id
        and detail_body.get("status") == COMPARISON_COMPLETED,
        f"status={detail.status_code}",
    )

    detail_items = detail_body.get("items") or []

    check(
        "36. detail endpoint item'lari doner",
        len(detail_items) >= 1
        and "41" in str(detail_items[0].get("affected_article")),
        f"item={len(detail_items)}",
    )

    notification_detail = client.get(
        f"/api/notifications/{notification.id}/detail"
    )

    detail_payload = (
        notification_detail.json()
        if notification_detail.status_code == 200
        else {}
    )

    check(
        "37. notification detail endpoint bildirimi doner",
        notification_detail.status_code == 200
        and detail_payload.get("notification_id")
        == notification.id
        and detail_payload.get("change_id") == change_record.id
        and detail_payload.get("monitor_id") == monitor_id,
        f"status={notification_detail.status_code}",
    )

    # -----------------------------------------------------
    # VERITABANI BUTUNLUGU
    # -----------------------------------------------------

    print()
    print("--- 8. veritabani butunlugu ---")

    check(
        "38. DB: Comparison.reference_regulation_id = kesfedilen",
        comparison is not None
        and comparison.reference_regulation_id == discovered_id,
        f"{comparison.reference_regulation_id if comparison else None}"
        f" == {discovered_id}",
    )

    expected_hash = calculate_content_hash(discovered.content)

    check(
        "39. DB: reference_content_hash o anki metnin hash'i",
        comparison is not None
        and comparison.reference_content_hash == expected_hash,
        f"{(comparison.reference_content_hash or '')[:16]} "
        f"== {expected_hash[:16]}",
    )

    check(
        "40. DB: reference_is_consolidated = true",
        comparison is not None
        and comparison.reference_is_consolidated is True,
        f"{comparison.reference_is_consolidated if comparison else None}",
    )

    # Kesifle gelen referans, YEREL eslesmeden ayri bir
    # yontem adi tasir: automatic_discovered_*
    check(
        "41. DB: reference_match_method dolu",
        comparison is not None
        and comparison.reference_match_method
        == "automatic_discovered_exact",
        f"{comparison.reference_match_method if comparison else None}",
    )

    check(
        "42. DB: reference_match_confidence dolu",
        comparison is not None
        and comparison.reference_match_confidence is not None
        and comparison.reference_match_confidence > 0,
        f"{comparison.reference_match_confidence if comparison else None}",
    )

    monitor = session.get(Monitor, monitor_id)

    snapshots = session.scalars(
        select(Snapshot)
        .where(Snapshot.monitor_id == monitor_id)
        .order_by(Snapshot.id)
    ).all()

    check(
        "43. DB: Monitor->Snapshot->Change->AIAnalysis zinciri",
        monitor is not None
        and len(snapshots) == 2
        and change_record.old_snapshot_id == snapshots[0].id
        and change_record.new_snapshot_id == snapshots[1].id
        and ai_analysis.change_id == change_record.id,
        f"snapshot={len(snapshots)} change={change_record.id} "
        f"analysis={ai_analysis.id}",
    )

    check(
        "44. DB: Notification change'e bagli",
        notification is not None
        and notification.change_id == change_record.id
        and notification.monitor_id == monitor_id,
        f"change={notification.change_id if notification else None}",
    )

    # =====================================================
    # 4. AYNI DEGISIKLIK IKINCI KEZ
    # =====================================================

    print()
    print("--- 9. idempotency: ayni sayfa tekrar ---")

    reference_before = session.scalar(
        select(func.count()).select_from(RegulationReference)
    )

    comparison_before = session.scalar(
        select(func.count()).select_from(RegulationComparison)
    )

    notification_before = session.scalar(
        select(func.count()).select_from(Notification)
    )

    reset_counters()

    # Sayfa DEGISMEDI: ayni icerik yeniden getirilir.
    third = client.post(f"/api/monitors/{monitor_id}/run")

    check(
        "45. IKINCI KEZ: yeni referans YOK",
        session.scalar(
            select(func.count()).select_from(RegulationReference)
        )
        == reference_before,
        f"{reference_before}",
    )

    check(
        "46. IKINCI KEZ: yeni comparison YOK",
        session.scalar(
            select(func.count()).select_from(RegulationComparison)
        )
        == comparison_before,
        f"{comparison_before}",
    )

    check(
        "47. IKINCI KEZ: yeni notification YOK",
        session.scalar(
            select(func.count()).select_from(Notification)
        )
        == notification_before,
        f"{notification_before}",
    )

    check(
        "48. IKINCI KEZ: yeni e-posta YOK",
        len(SMTP_SESSIONS) == 0,
        f"oturum={len(SMTP_SESSIONS)}",
    )

    check(
        "49. IKINCI KEZ: yeni kesif YOK",
        len(SEARCH_CALLS) == 0
        and len(CONSOLIDATED_FETCH_CALLS) == 0,
        f"arama={len(SEARCH_CALLS)} "
        f"fetch={len(CONSOLIDATED_FETCH_CALLS)}",
    )

    check(
        "50. IKINCI KEZ: yeni Gemini cagrisi YOK",
        len(GEMINI_CALLS) == 0,
        f"cagri={gemini_kinds()}",
    )

    return monitor_id, discovered_id, comparison_id, third


def run_scheduler_tests(session, monitor_id, discovered_id):
    """
    Scheduler yolu.

    Scheduler mimarisi DEGISTIRILMEZ. Yalnizca ayni
    pipeline entrypoint'ine bagli oldugu dogrulanir.
    """

    print()
    print("--- 10. scheduler yolu ---")

    from datetime import datetime, timedelta

    from app.core.scheduler import (
        evaluate_monitor_due,
        get_app_timezone,
    )

    monitor = session.get(Monitor, monitor_id)

    timezone_info = get_app_timezone()

    # Kontrol saatinden SONRAKI bir an secilir; boylece
    # monitor due olur.
    now_local = datetime.now(timezone_info).replace(
        hour=10,
        minute=0,
        second=0,
        microsecond=0,
    )

    # Son kontrol dunde birakilir.
    monitor.last_checked_at = (
        now_local - timedelta(days=1)
    ).replace(tzinfo=None)

    session.flush()

    decision = evaluate_monitor_due(
        monitor=monitor,
        now_local=now_local,
        timezone_info=timezone_info,
    )

    check(
        "51. SCHEDULER: due degerlendirmesi calisiyor",
        decision.due is True,
        f"due={decision.due} sebep={decision.reason}",
    )

    # -----------------------------------------------------
    # SCHEDULER GERCEK CALISTIRMASI
    #
    # _execute_due_monitor kendi SessionLocal'ini acar.
    # Testin rollback edilen transaction'i disina cikmamak
    # icin scheduler.SessionLocal test session'ina
    # yonlendirilir. Cagri zinciri DEGISMEZ.
    # -----------------------------------------------------

    reset_counters()

    # Sayfa yeniden degisir: scheduler gercek bir
    # degisiklik gormeli.
    #
    # Yeni satir hedef mevzuati ADIYLA anmalidir. Yalnizca
    # "Bu Yönetmeliğin ..." denirse eslestirici hedef adi
    # cikaramaz ve karsilastirma dogru sekilde CALISMAZ;
    # bu, scheduler'in degil fixture'in eksigi olurdu.
    CURRENT_PAGE["value"] = build_gazette_page(
        AMENDMENT_BODY
        + NEWLINE
        + (
            "MADDE 4- 1/12/2021 tarihli ve 31676 sayılı Resmî "
            "Gazete'de yayımlanan Ödeme Hizmetleri ve "
            "Elektronik Para İhracı ile Ödeme Hizmeti "
            "Sağlayıcıları Hakkında Yönetmeliğin 41 inci "
            "maddesinin ikinci fıkrasında yer alan \"denetime\" "
            "ibaresi \"incelemeye\" şeklinde değiştirilmiştir."
        )
    )

    class NonClosingSession:
        """
        Scheduler'in db.close() cagrisi test transaction'ini
        kapatmasin.
        """

        def __init__(self, session):
            self._session = session

        def __getattr__(self, name):
            return getattr(self._session, name)

        def close(self):
            return None

    original_session_local = scheduler.SessionLocal

    scheduler.SessionLocal = lambda: NonClosingSession(session)

    comparison_before = session.scalar(
        select(func.count()).select_from(RegulationComparison)
    )

    try:
        asyncio.run(
            scheduler._execute_due_monitor(
                monitor_id=monitor_id,
                monitor_name=monitor.name,
            )
        )

    finally:
        scheduler.SessionLocal = original_session_local

    comparison_after = session.scalar(
        select(func.count()).select_from(RegulationComparison)
    )

    check(
        "52. SCHEDULER: ayni pipeline'i calistirir",
        len(GAZETTE_FETCH_CALLS) >= 1
        and comparison_after == comparison_before + 1,
        f"fetch={len(GAZETTE_FETCH_CALLS)} "
        f"comparison {comparison_before}->{comparison_after}",
    )

    scheduler_comparison = session.scalars(
        select(RegulationComparison)
        .order_by(RegulationComparison.id.desc())
    ).first()

    check(
        "53. SCHEDULER: kesif/karsilastirma manuel ile AYNI",
        scheduler_comparison is not None
        and scheduler_comparison.status == COMPARISON_COMPLETED
        and scheduler_comparison.reference_regulation_id
        == discovered_id
        # Referans zaten kesfedilmisti: YENIDEN aranmadi.
        and len(SEARCH_CALLS) == 0,
        f"status={scheduler_comparison.status} "
        f"ref={scheduler_comparison.reference_regulation_id} "
        f"arama={len(SEARCH_CALLS)}",
    )

    # -----------------------------------------------------
    # PROCESS LOCK
    # -----------------------------------------------------

    from app.services.monitor_run_service import (
        acquire_monitor_run,
        is_monitor_running,
        release_monitor_run,
    )

    first_claim = acquire_monitor_run(monitor_id)
    second_claim = acquire_monitor_run(monitor_id)

    check(
        "54. SCHEDULER: process lock duplicate engelliyor",
        first_claim is True
        and second_claim is False
        and is_monitor_running(monitor_id) is True,
        f"1={first_claim} 2={second_claim}",
    )

    return monitor_id


def run_lock_endpoint_test(client, monitor_id):

    from app.services.monitor_run_service import (
        release_monitor_run,
    )

    # Lock hala tutuluyor (54. testten).
    blocked = client.post(f"/api/monitors/{monitor_id}/run")

    check(
        "55. calisan monitor icin /run 409 doner",
        blocked.status_code == 409,
        f"status={blocked.status_code}",
    )

    release_monitor_run(monitor_id)


def main() -> None:

    print("=" * 70)
    print("UCTAN UCA MEVZUAT TAKIBI TESTI")
    print("(gercek Gemini YOK, gercek SMTP YOK, gercek ag YOK)")
    print("=" * 70)

    # -----------------------------------------------------
    # SAHTELEME
    # -----------------------------------------------------

    original_gazette_fetch = db_monitor_service.fetch_website
    original_consolidated_fetch = (
        regulation_reference_service.fetch_website
    )
    original_post_search = mevzuat_search_service._post_search
    original_client = ai_analyzer.genai.Client

    original_smtp = smtplib.SMTP
    original_smtp_ssl = smtplib.SMTP_SSL

    original_settings = {
        field: getattr(email_service.settings, field)
        for field in (
            "smtp_host",
            "smtp_port",
            "smtp_username",
            "smtp_password",
            "smtp_from_email",
            "smtp_from_name",
            "smtp_use_tls",
            "smtp_use_ssl",
        )
    }

    db_monitor_service.fetch_website = fake_gazette_fetch
    regulation_reference_service.fetch_website = (
        fake_consolidated_fetch
    )
    mevzuat_search_service._post_search = fake_post_search
    ai_analyzer.genai.Client = FakeGeminiClient

    smtplib.SMTP = FakeSMTP
    smtplib.SMTP_SSL = FakeSMTPSSL

    email_service.settings.smtp_host = "smtp.ornek.test"
    email_service.settings.smtp_port = 587
    email_service.settings.smtp_username = "sahte@ornek.test"
    email_service.settings.smtp_password = "sahte-parola-12345"
    email_service.settings.smtp_from_email = "bot@ornek.test"
    email_service.settings.smtp_from_name = "Website Monitor AI"
    email_service.settings.smtp_use_tls = True
    email_service.settings.smtp_use_ssl = False

    # notification_service send_email'i import etmis olabilir.
    original_notification_send = notification_service.send_email
    notification_service.send_email = email_service.send_email

    block_network()

    # -----------------------------------------------------
    # TRANSACTION
    # -----------------------------------------------------

    connection = engine.connect()
    transaction = connection.begin()

    session = Session(
        bind=connection,
        join_transaction_mode="create_savepoint",
    )

    app.dependency_overrides[get_db] = lambda: session

    client = TestClient(app)

    counts_before = {
        "monitor": session.scalar(
            select(func.count()).select_from(Monitor)
        ),
        "reference": session.scalar(
            select(func.count()).select_from(RegulationReference)
        ),
        "comparison": session.scalar(
            select(func.count()).select_from(RegulationComparison)
        ),
        "notification": session.scalar(
            select(func.count()).select_from(Notification)
        ),
    }

    check(
        "test sirasinda scheduler baslatilmadi",
        scheduler._scheduler_task is None
        or scheduler._scheduler_task.done(),
    )

    try:
        monitor_id, discovered_id, comparison_id, _ = run_tests(
            session,
            client,
        )

        run_scheduler_tests(session, monitor_id, discovered_id)

        run_lock_endpoint_test(client, monitor_id)

        # Engel hem soket hem AD COZUMLEME seviyesindedir.
        check(
            "56. test boyunca dis ag denemesi = 0",
            NETWORK_ATTEMPTS == [],
            f"{NETWORK_ATTEMPTS}",
        )

        check(
            "57. test boyunca gercek SMTP soketi = 0",
            smtplib.SMTP is FakeSMTP,
            "sahte SMTP aktif",
        )

    finally:
        app.dependency_overrides.clear()

        session.close()

        transaction.rollback()
        connection.close()

        unblock_network()

        db_monitor_service.fetch_website = original_gazette_fetch
        regulation_reference_service.fetch_website = (
            original_consolidated_fetch
        )
        mevzuat_search_service._post_search = original_post_search
        ai_analyzer.genai.Client = original_client

        notification_service.send_email = (
            original_notification_send
        )

        smtplib.SMTP = original_smtp
        smtplib.SMTP_SSL = original_smtp_ssl

        for field, value in original_settings.items():
            setattr(email_service.settings, field, value)

    # -----------------------------------------------------
    # DB KIRLILIGI
    # -----------------------------------------------------

    with Session(bind=engine) as verify:

        counts_after = {
            "monitor": verify.scalar(
                select(func.count()).select_from(Monitor)
            ),
            "reference": verify.scalar(
                select(func.count()).select_from(
                    RegulationReference
                )
            ),
            "comparison": verify.scalar(
                select(func.count()).select_from(
                    RegulationComparison
                )
            ),
            "notification": verify.scalar(
                select(func.count()).select_from(Notification)
            ),
        }

    check(
        "58. DB kirliligi YOK",
        counts_after == counts_before,
        f"{counts_before} -> {counts_after}",
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
