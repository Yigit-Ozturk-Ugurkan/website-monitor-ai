"""
E-mail bildirimi KABUL TESTI.

DIKKAT: Bu dosya otomatik test paketinin parcasi DEGILDIR.
Adi bilerek "test_" ile baslamaz.

GERCEK MAIL GONDERILMEZ.
Elimizde dogrulanmis bir SMTP kimlik bilgisi olmadigi icin
tasima katmani SAHTE bir SMTP sunucusuyla degistirilir.
Kisisel bir e-mail hesabi kullanilmaz.

GERCEK GEMINI CAGRISI: 0
GERCEK HTTP ISTEGI: 0

Dogrulanan akis:

    fetch -> degisiklik -> AI analizi -> in-app bildirim
          -> e-mail bildirimi -> SMTP gonderimi

Veritabani:
Tum islemler dis bir transaction icinde yapilir ve sonunda
ROLLBACK edilir. Cop monitor birakilmaz.

Calistirma:
    python -m tests.acceptance_email_notification
"""

import asyncio
import smtplib
import socket

from dataclasses import dataclass
from html import escape

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database import SessionLocal, engine
from app.models.monitor import Monitor
from app.models.notification import Notification

from app.repositories.monitor_repository import create_monitor
from app.repositories.notification_repository import (
    STATUS_SENT,
    get_notification_by_analysis_and_type,
)

from app.services import ai_analyzer
from app.services import db_monitor_service
from app.services import email_service
from app.services.monitor_run_service import execute_monitor_run


PASSED = []
FAILED = []


RECIPIENT = "kabul-testi@ornek-sirket.test"

MONITOR_NAME = "Techmeme AI Takibi"
MONITOR_URL = "https://www.techmeme.com/"
MONITOR_TOPIC = "yapay zeka modelleri ve fiyat değişiklikleri"


SUMMARY = (
    "Başlangıç paketinin aylık fiyatı 100 TL'den "
    "150 TL'ye yükseldi."
)

EVIDENCE = (
    "Başlangıç paketi aylık 150 TL olarak sunulmaktadır."
)


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


LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost", ""}


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
        f"Kabul testinde dis ag baglantisi denendi: {address}"
    )


def _blocked_create_connection(address, *args, **kwargs):
    if _is_loopback(address):
        return _real_create_connection(address, *args, **kwargs)

    NETWORK_ATTEMPTS.append(address)

    raise AssertionError(
        f"Kabul testinde dis ag baglantisi denendi: {address}"
    )


# ---------------------------------------------------------
# SAHTE SMTP SUNUCUSU
#
# Hicbir soket acilmaz. Gonderilen mesaj burada tutulur ve
# icerigi dogrulanir.
# ---------------------------------------------------------

SMTP_SESSIONS = []

SENT_MESSAGES = []


class FakeSMTP:
    def __init__(self, host=None, port=None, timeout=None, context=None):
        self.session = {
            "host": host,
            "port": port,
            "timeout": timeout,
            "starttls": 0,
            "login": None,
            "closed": False,
        }

        SMTP_SESSIONS.append(self.session)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.session["closed"] = True
        return False

    def ehlo(self, *args, **kwargs):
        return None

    def starttls(self, *args, **kwargs):
        self.session["starttls"] += 1

    def login(self, username, password):
        # Parola KAYDEDILMEZ.
        self.session["login"] = username

    def send_message(self, message):
        SENT_MESSAGES.append(message)

    def quit(self):
        self.session["closed"] = True


class FakeSMTPSSL(FakeSMTP):
    pass


# ---------------------------------------------------------
# SAHTE GEMINI
# ---------------------------------------------------------

ANALYSIS_CALLS = []


ANALYSIS_JSON = (
    '{"relevant": true, "importance": "high", '
    '"category": "pricing", '
    f'"summary": "{SUMMARY}", '
    '"notification_recommended": true, '
    f'"evidence_text": "{EVIDENCE}", '
    '"evidence_type": "added"}'
)


@dataclass
class FakeResponse:
    text: str


class FakeGeminiClient:
    def __init__(self):
        self.aio = self
        self.models = self

    async def generate_content(self, model, contents, config):
        ANALYSIS_CALLS.append(model)

        return FakeResponse(text=ANALYSIS_JSON)

    async def aclose(self):
        return None


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


def build_page(price: str) -> str:
    return f"""<html>
<head><title>Techmeme</title></head>
<body>
  <main>
    <section id="fiyatlandirma">
      <h2>Fiyatlandırma</h2>
      <p>Başlangıç paketi aylık {price} olarak sunulmaktadır.</p>
      <p>Kurumsal paket aylık 500 TL seviyesindedir ve sınırsızdır.</p>
    </section>
  </main>
</body>
</html>"""


PAGE_V1 = build_page("100 TL")
PAGE_V2 = build_page("150 TL")


CURRENT_HTML = {"value": PAGE_V1}


async def fake_fetch_website(url: str) -> FakeFetchResult:
    return FakeFetchResult(
        url=url,
        final_url=url,
        status_code=200,
        content_type="text/html; charset=utf-8",
        html=CURRENT_HTML["value"],
    )


def message_text(message) -> str:
    """
    Mailin duz metin govdesini dondurur.
    """

    if not message.is_multipart():
        return message.get_content()

    for part in message.walk():
        if part.get_content_type() == "text/plain":
            return part.get_content()

    return ""


def message_html(message) -> str:

    for part in message.walk():
        if part.get_content_type() == "text/html":
            return part.get_content()

    return ""


def main() -> None:

    print("=" * 70)
    print("E-MAIL BILDIRIMI KABUL TESTI")
    print("SAHTE SMTP TASIMASI - GERCEK MAIL GONDERILMEZ")
    print("GERCEK GEMINI CAGRISI BEKLENMIYOR: 0")
    print("=" * 70)

    original_smtp = smtplib.SMTP
    original_smtp_ssl = smtplib.SMTP_SSL
    original_client = ai_analyzer.genai.Client
    original_fetch = db_monitor_service.fetch_website

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

    # Kabul testi icin SAHTE ayarlar.
    # Gercek .env degerleri kullanilmaz.
    email_service.settings.smtp_host = "smtp.kabul-testi.local"
    email_service.settings.smtp_port = 587
    email_service.settings.smtp_username = "kabul@ornek.test"
    email_service.settings.smtp_password = "sahte-parola"
    email_service.settings.smtp_from_email = "bot@ornek.test"
    email_service.settings.smtp_from_name = "Website Monitor AI"
    email_service.settings.smtp_use_tls = True
    email_service.settings.smtp_use_ssl = False

    email_service.smtplib.SMTP = FakeSMTP
    email_service.smtplib.SMTP_SSL = FakeSMTPSSL

    ai_analyzer.genai.Client = lambda *a, **k: FakeGeminiClient()
    db_monitor_service.fetch_website = fake_fetch_website

    socket.socket.connect = _blocked_connect
    socket.create_connection = _blocked_create_connection

    baseline = SessionLocal()

    try:
        monitors_before = baseline.scalar(
            select(func.count()).select_from(Monitor)
        )
        notifications_before = baseline.scalar(
            select(func.count()).select_from(Notification)
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
        # -------------------------------------------------
        # 1. CAPABILITY
        # -------------------------------------------------

        print("\n--- 1. capability ---")

        check(
            "SMTP yapilandirilmis gorunuyor",
            email_service.is_email_configured() is True,
        )

        # -------------------------------------------------
        # 2. MONITOR VE ILK SNAPSHOT
        # -------------------------------------------------

        print("\n--- 2. monitor ve ilk kontrol ---")

        monitor = create_monitor(
            db=session,
            name=MONITOR_NAME,
            url=MONITOR_URL,
            topic=MONITOR_TOPIC,
            scope_type="full_page",
            schedule_type="daily",
            schedule_time="09:00",
            notify_in_app=True,
            notify_email=True,
            notification_email=RECIPIENT,
        )

        CURRENT_HTML["value"] = PAGE_V1

        first = asyncio.run(
            execute_monitor_run(db=session, monitor=monitor)
        )

        check(
            "ilk kontrol: degisiklik yok, bildirim yok",
            first.result.first_run is True
            and first.notification is None
            and first.email_notification is None,
        )

        check(
            "ilk kontrolde mail gonderilmedi",
            SENT_MESSAGES == [],
            f"{len(SENT_MESSAGES)}",
        )

        # -------------------------------------------------
        # 3. GERCEK DEGISIKLIK -> BILDIRIM
        # -------------------------------------------------

        print("\n--- 3. degisiklik ve bildirimler ---")

        CURRENT_HTML["value"] = PAGE_V2

        outcome = asyncio.run(
            execute_monitor_run(db=session, monitor=monitor)
        )

        analysis = outcome.result.ai_analysis_record

        check(
            "pipeline gercek bir degisiklik uretti",
            outcome.result.changed is True
            and outcome.result.change_record is not None,
        )

        check(
            "AI analizi kaydedildi",
            analysis is not None
            and analysis.relevant is True
            and analysis.notification_recommended is True,
        )

        check(
            "in-app bildirim kaydi olustu",
            outcome.notification is not None
            and outcome.notification.notification_type
            == "in_app",
        )

        email_notification = outcome.email_notification

        check(
            "e-mail bildirim kaydi olustu",
            email_notification is not None
            and email_notification.notification_type
            == "email",
        )

        check(
            "e-mail kaydi dogru aliciya bagli",
            email_notification.recipient == RECIPIENT,
            f"{email_notification.recipient}",
        )

        check(
            "e-mail durumu 'sent'",
            email_notification.status == STATUS_SENT,
            email_notification.status,
        )

        check(
            "sent_at dolduruldu, hata yok",
            email_notification.sent_at is not None
            and email_notification.error_message is None,
        )

        # -------------------------------------------------
        # 4. SMTP TASIMASI
        # -------------------------------------------------

        print("\n--- 4. SMTP tasimasi ---")

        check(
            "SAHTE SMTP TAM OLARAK BIR KEZ cagrildi",
            len(SENT_MESSAGES) == 1,
            f"{len(SENT_MESSAGES)}",
        )

        check(
            "tek SMTP oturumu acildi ve kapatildi",
            len(SMTP_SESSIONS) == 1
            and SMTP_SESSIONS[0]["closed"] is True,
        )

        check(
            "STARTTLS uygulandi",
            SMTP_SESSIONS[0]["starttls"] == 1,
        )

        check(
            "timeout verildi",
            SMTP_SESSIONS[0]["timeout"]
            == email_service.SMTP_TIMEOUT_SECONDS,
            f"{SMTP_SESSIONS[0]['timeout']}",
        )

        message = SENT_MESSAGES[0]

        text_body = message_text(message)
        html_body = message_html(message)

        print()
        print(f"To      : {message['To']}")
        print(f"From    : {message['From']}")
        print(f"Subject : {message['Subject']}")
        print("-" * 70)
        print(text_body)
        print("-" * 70)

        check(
            "alici dogru",
            message["To"] == RECIPIENT,
        )

        check(
            "subject monitor adini iceriyor",
            MONITOR_NAME in message["Subject"],
            message["Subject"],
        )

        check(
            "subject onem seviyesini iceriyor",
            "Yüksek önem" in message["Subject"],
            message["Subject"],
        )

        check(
            "subject urun onekini iceriyor",
            "[Website Monitor AI]" in message["Subject"],
        )

        check(
            "govde monitor adini iceriyor",
            MONITOR_NAME in text_body,
        )

        check(
            "govde website adresini iceriyor",
            MONITOR_URL in text_body,
        )

        check(
            "govde takip konusunu iceriyor",
            MONITOR_TOPIC in text_body,
        )

        check(
            "govde AI ozetini iceriyor",
            SUMMARY in text_body,
            SUMMARY[:40],
        )

        check(
            "govde EVIDENCE metnini iceriyor",
            EVIDENCE in text_body,
            EVIDENCE[:40],
        )

        check(
            "govde kategoriyi iceriyor",
            analysis.category in text_body,
        )

        check(
            "govde degisiklik zamanini iceriyor",
            "Değişiklik zamanı" in text_body,
        )

        check(
            "govde uygulama detay baglantisini iceriyor",
            f"/notifications/{email_notification.id}"
            in text_body,
        )

        check(
            "duz metin ve HTML alternatif birlikte gonderildi",
            message.is_multipart()
            and bool(text_body)
            and bool(html_body),
        )

        # HTML govdede tum degerler escape edilir; sayfa
        # icerigi mail icinde HTML olarak yorumlanmaz.
        # Bu yuzden karsilastirma escape edilmis bicim uzerinden yapilir.
        check(
            "HTML govde de ozeti iceriyor (escape edilmis)",
            escape(SUMMARY) in html_body,
            escape(SUMMARY)[:60],
        )

        check(
            "HTML govde degerleri escape ediyor "
            "(mail icinde HTML calismaz)",
            "'" not in html_body.replace("&#x27;", "")
            or "&#x27;" in html_body,
        )

        check(
            "mail icinde SMTP parolasi yok",
            "sahte-parola" not in text_body
            and "sahte-parola" not in html_body,
        )

        # -------------------------------------------------
        # 5. DUPLICATE
        # -------------------------------------------------

        print("\n--- 5. duplicate korumasi ---")

        from app.services.notification_service import (
            send_email_notification,
        )

        duplicate = asyncio.run(
            send_email_notification(
                db=session,
                monitor=monitor,
                change=outcome.result.change_record,
                ai_analysis=analysis,
            )
        )

        check(
            "ikinci cagri YENI mail gondermedi",
            len(SENT_MESSAGES) == 1,
            f"{len(SENT_MESSAGES)}",
        )

        check(
            "mevcut kayit geri dondu",
            duplicate is not None
            and duplicate.id == email_notification.id,
        )

        check(
            "veritabaninda tek e-mail kaydi var",
            session.scalar(
                select(func.count())
                .select_from(Notification)
                .where(
                    Notification.ai_analysis_id
                    == analysis.id,
                    Notification.notification_type
                    == "email",
                )
            )
            == 1,
        )

        # Ayni sayfayla tekrar calistirma da mail uretmemeli
        repeat = asyncio.run(
            execute_monitor_run(db=session, monitor=monitor)
        )

        check(
            "degisiklik yokken tekrar mail gonderilmedi",
            len(SENT_MESSAGES) == 1
            and repeat.email_notification is None,
            f"{len(SENT_MESSAGES)}",
        )

        # -------------------------------------------------
        # 6. AI CAGRI SAYISI
        # -------------------------------------------------

        print("\n--- 6. AI cagri sayisi ---")

        check(
            "yalnizca 1 degisiklik analizi yapildi",
            len(ANALYSIS_CALLS) == 1,
            f"{ANALYSIS_CALLS}",
        )

        check(
            "e-mail uretimi icin EK AI cagrisi yapilmadi",
            len(ANALYSIS_CALLS) == 1,
        )

        session.flush()

    finally:
        session.close()
        transaction.rollback()
        connection.close()

        socket.socket.connect = _real_connect
        socket.create_connection = _real_create_connection

        email_service.smtplib.SMTP = original_smtp
        email_service.smtplib.SMTP_SSL = original_smtp_ssl
        ai_analyzer.genai.Client = original_client
        db_monitor_service.fetch_website = original_fetch

        for field, value in original_settings.items():
            setattr(email_service.settings, field, value)

    # -----------------------------------------------------
    # 7. TEMIZLIK
    # -----------------------------------------------------

    print("\n--- 7. cop kayit birakilmadi ---")

    verify = SessionLocal()

    try:
        monitors_after = verify.scalar(
            select(func.count()).select_from(Monitor)
        )
        notifications_after = verify.scalar(
            select(func.count()).select_from(Notification)
        )
    finally:
        verify.close()

    check(
        "kabul testi monitoru DB'de birakilmadi",
        monitors_after == monitors_before,
        f"{monitors_before} -> {monitors_after}",
    )

    check(
        "kabul testi bildirimleri DB'de birakilmadi",
        notifications_after == notifications_before,
        f"{notifications_before} -> {notifications_after}",
    )

    check(
        "dis ag baglantisi HIC denenmedi",
        NETWORK_ATTEMPTS == [],
        f"{NETWORK_ATTEMPTS}",
    )

    # -----------------------------------------------------
    # SONUC
    # -----------------------------------------------------

    print("\n" + "=" * 70)
    print(f"GERCEK MAIL GONDERIMI    : 0 (sahte SMTP)")
    print(f"GERCEK GEMINI CAGRISI    : 0 (sahte client)")
    print(f"SAHTE SMTP GONDERIMI     : {len(SENT_MESSAGES)}")
    print(f"BASARILI : {len(PASSED)}")
    print(f"BASARISIZ: {len(FAILED)}")

    if FAILED:
        print("-" * 70)
        for line in FAILED:
            print(line)

        raise SystemExit(1)

    print("KABUL TESTI BASARILI")
    print("=" * 70)


if __name__ == "__main__":
    main()
