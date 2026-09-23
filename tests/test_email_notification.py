"""
E-mail bildirimi testleri.

GERCEK MAIL GONDERILMEZ.
GERCEK SMTP SUNUCUSUNA BAGLANILMAZ.
GERCEK GEMINI API CAGRISI YAPILMAZ.
GERCEK HTTP ISTEGI YAPILMAZ.

- smtplib sahte bir sunucu sinifiyla degistirilir.
- Gemini sahte client ile karsilanir.
- fetch_website sahte HTML dondurur.
- Test suresince dis ag baglantisi bloklanir ve hic
  denenmedigi ayrica assert edilir.
- Tum DB islemleri dis bir transaction icinde calisir ve
  sonunda ROLLBACK edilir.

Calistirma:
    python -m tests.test_email_notification
"""

import asyncio
import io
import re
import smtplib
import socket
import warnings

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database import SessionLocal, engine
from app.models.ai_analysis import AIAnalysis
from app.models.change import Change
from app.models.monitor import Monitor
from app.models.notification import Notification
from app.models.snapshot import Snapshot

from app.repositories.monitor_repository import create_monitor
from app.repositories.notification_repository import (
    STATUS_FAILED,
    STATUS_SENT,
    count_unread_notifications,
    get_notification_by_analysis_and_type,
    get_notifications,
)
from app.repositories.snapshot_repository import get_latest_snapshot

from app.services import ai_analyzer
from app.services import db_monitor_service
from app.services import email_service
from app.services import notification_service
from app.services.email_content import build_change_email
from app.services.email_service import (
    EmailServiceError,
    is_email_configured,
    missing_email_settings,
    sanitize_error_message,
    send_email,
)
from app.services.monitor_run_service import execute_monitor_run

warnings.filterwarnings("ignore")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


PASSED = []
FAILED = []


PROJECT_ROOT = Path(__file__).resolve().parents[1]

APP_JSX = PROJECT_ROOT / "frontend" / "src" / "App.jsx"
API_JS = PROJECT_ROOT / "frontend" / "src" / "services" / "api.js"


PRIMARY_MODEL = "gemini-3.6-flash"

RECIPIENT = "bildirim@ornek-sirket.test"

FAKE_PASSWORD = "sahte-smtp-parolasi-12345"
FAKE_USERNAME = "sahte-kullanici@ornek.test"


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
# SAHTE SMTP SUNUCUSU
#
# smtplib.SMTP / SMTP_SSL yerine gecer. Hicbir soket
# acilmaz; yapilan cagrilar kaydedilir.
# ---------------------------------------------------------

SMTP_SESSIONS = []


class FakeSMTP:
    """
    smtplib.SMTP yerine gecen sahte sunucu.

    behaviour: hangi adimda hata firlatilacagini belirler.
    """

    behaviour = {}

    def __init__(self, host=None, port=None, timeout=None, context=None):
        self.session = {
            "class": type(self).__name__,
            "host": host,
            "port": port,
            "timeout": timeout,
            "ssl_context": context is not None,
            "ehlo": 0,
            "starttls": 0,
            "login": None,
            "messages": [],
            "closed": False,
        }

        SMTP_SESSIONS.append(self.session)

        error = type(self).behaviour.get("connect")

        if error is not None:
            raise error

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.session["closed"] = True
        return False

    def ehlo(self, *args, **kwargs):
        self.session["ehlo"] += 1

    def starttls(self, *args, **kwargs):
        self.session["starttls"] += 1

        error = type(self).behaviour.get("starttls")

        if error is not None:
            raise error

    def login(self, username, password):
        # Parola KAYDEDILMEZ; testte bile sir tutulmaz.
        self.session["login"] = username

        error = type(self).behaviour.get("login")

        if error is not None:
            raise error

    def send_message(self, message):
        error = type(self).behaviour.get("send")

        if error is not None:
            raise error

        self.session["messages"].append(message)

    def quit(self):
        self.session["closed"] = True


class FakeSMTPSSL(FakeSMTP):
    pass


def reset_smtp(behaviour=None):
    SMTP_SESSIONS.clear()
    FakeSMTP.behaviour = behaviour or {}


# ---------------------------------------------------------
# GEMINI TUZAGI
#
# E-mail uretimi icin YENI bir AI cagrisi olmamalidir.
# Degisiklik analizi cagrilari ayri sayilir.
# ---------------------------------------------------------

ANALYSIS_CALLS = []

ANALYSIS_RESPONSE = {"value": ""}


def analysis_json(
    relevant=True,
    notification_recommended=True,
    evidence=True,
):
    evidence_text = (
        '"Başlangıç paketi aylık 150 TL olarak sunulmaktadır."'
        if evidence
        else "null"
    )

    evidence_type = '"added"' if evidence else "null"

    return (
        f'{{"relevant": {str(relevant).lower()}, '
        '"importance": "high", '
        '"category": "pricing", '
        '"summary": "Başlangıç paketi fiyatı 150 TL oldu.", '
        '"notification_recommended": '
        f'{str(notification_recommended).lower()}, '
        f'"evidence_text": {evidence_text}, '
        f'"evidence_type": {evidence_type}}}'
    )


@dataclass
class FakeResponse:
    text: str


class FakeGeminiClient:
    def __init__(self):
        self.aio = self
        self.models = self

    async def generate_content(self, model, contents, config):
        ANALYSIS_CALLS.append(
            {"model": model, "prompt": contents}
        )

        return FakeResponse(
            text=ANALYSIS_RESPONSE["value"]
        )

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
<head><title>Örnek Ürün Sitesi</title></head>
<body>
  <main>
    <section id="fiyatlandirma">
      <h2>Fiyatlandırma</h2>
      <p>Başlangıç paketi aylık {price} olarak sunulmaktadır.</p>
      <p>Kurumsal paket aylık 500 TL seviyesindedir ve sınırsızdır.</p>
    </section>

    <section id="blog">
      <h2>Blog</h2>
      <p>Yeni sürümde performans iyileştirmeleri yapıldı burada.</p>
      <p>Ekibimize katılan geliştiricilerle yol haritası genişledi.</p>
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


# ---------------------------------------------------------
# GONDERIM SAYACI
#
# Pipeline testlerinde send_email cagrisi sayilir; boylece
# "tam olarak bir kez gonderildi" dogrulanabilir.
# ---------------------------------------------------------

SEND_CALLS = []

SEND_BEHAVIOUR = {"error": None}


def counting_send_email(
    recipient,
    subject,
    message,
    html_message=None,
):
    SEND_CALLS.append(
        {
            "recipient": recipient,
            "subject": subject,
            "message": message,
            "html_message": html_message,
        }
    )

    error = SEND_BEHAVIOUR["error"]

    if error is not None:
        raise error

    return email_service.EmailSendResult(
        success=True,
        recipient=recipient,
        subject=subject,
    )


def reset_pipeline_counters():
    SEND_CALLS.clear()
    ANALYSIS_CALLS.clear()
    SEND_BEHAVIOUR["error"] = None


# ---------------------------------------------------------
# YARDIMCILAR
# ---------------------------------------------------------

def make_monitor(
    session,
    name,
    notify_in_app=True,
    notify_email=True,
    notification_email=RECIPIENT,
):
    return create_monitor(
        db=session,
        name=name,
        url="https://ornek-site.test/urun",
        topic="fiyat değişiklikleri",
        scope_type="full_page",
        schedule_type="daily",
        schedule_time="09:00",
        notify_in_app=notify_in_app,
        notify_email=notify_email,
        notification_email=notification_email,
    )


def run_monitor(session, monitor, html):
    CURRENT_HTML["value"] = html

    return asyncio.run(
        execute_monitor_run(
            db=session,
            monitor=monitor,
        )
    )


def prepare_change(session, monitor):
    """
    Monitor icin ilk snapshot'i olusturur.
    Sonraki calistirma gercek bir degisiklik uretir.
    """

    run_monitor(session, monitor, PAGE_V1)


def email_row(session, ai_analysis_id):
    return get_notification_by_analysis_and_type(
        db=session,
        ai_analysis_id=ai_analysis_id,
        notification_type="email",
    )


def in_app_row(session, ai_analysis_id):
    return get_notification_by_analysis_and_type(
        db=session,
        ai_analysis_id=ai_analysis_id,
        notification_type="in_app",
    )


def main() -> None:

    print("=" * 70)
    print("E-MAIL BILDIRIMI TESTLERI")
    print("(gercek mail YOK, gercek SMTP YOK, gercek Gemini YOK)")
    print("=" * 70)

    original_smtp = smtplib.SMTP
    original_smtp_ssl = smtplib.SMTP_SSL
    original_client = ai_analyzer.genai.Client
    original_fetch = db_monitor_service.fetch_website
    original_send = notification_service.send_email

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

    def set_smtp(**overrides):
        values = {
            "smtp_host": "smtp.ornek.test",
            "smtp_port": 587,
            "smtp_username": FAKE_USERNAME,
            "smtp_password": FAKE_PASSWORD,
            "smtp_from_email": "bot@ornek.test",
            "smtp_from_name": "Website Monitor AI",
            "smtp_use_tls": True,
            "smtp_use_ssl": False,
        }

        values.update(overrides)

        for field, value in values.items():
            setattr(email_service.settings, field, value)

    email_service.smtplib.SMTP = FakeSMTP
    email_service.smtplib.SMTP_SSL = FakeSMTPSSL

    ai_analyzer.genai.Client = lambda *a, **k: FakeGeminiClient()
    db_monitor_service.fetch_website = fake_fetch_website

    ANALYSIS_RESPONSE["value"] = analysis_json()

    block_network()

    client = TestClient(app)

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
        # =================================================
        # 1-3. CAPABILITY
        # =================================================

        print("\n--- 1-3. capability ---")

        set_smtp()

        check(
            "2. tum SMTP ayarlari varken capability true",
            is_email_configured() is True
            and missing_email_settings() == [],
        )

        available = client.get("/api/capabilities").json()

        check(
            "2. /api/capabilities email_notifications=true",
            available == {"email_notifications": True},
            f"{available}",
        )

        for field, env_name in [
            ("smtp_host", "SMTP_HOST"),
            ("smtp_username", "SMTP_USERNAME"),
            ("smtp_password", "SMTP_PASSWORD"),
            ("smtp_from_email", "SMTP_FROM_EMAIL"),
        ]:
            set_smtp(**{field: None})

            check(
                f"1. {env_name} eksikken capability false",
                is_email_configured() is False
                and missing_email_settings() == [env_name],
                f"{missing_email_settings()}",
            )

            set_smtp(**{field: "   "})

            check(
                f"1. {env_name} bosluk iken de capability false",
                is_email_configured() is False,
            )

            set_smtp()

        set_smtp(smtp_host=None)

        unavailable = client.get("/api/capabilities").json()

        check(
            "1. SMTP eksikken endpoint false donuyor",
            unavailable == {"email_notifications": False},
            f"{unavailable}",
        )

        set_smtp()

        # 3. SIR SIZINTISI
        capability_body = client.get(
            "/api/capabilities"
        ).text

        secrets = [
            FAKE_PASSWORD,
            FAKE_USERNAME,
            "smtp.ornek.test",
            "bot@ornek.test",
        ]

        check(
            "3. capability cevabi hicbir SMTP degeri icermiyor",
            not any(
                secret.lower() in capability_body.lower()
                for secret in secrets
            ),
            capability_body,
        )

        check(
            "3. capability cevabi yalnizca boolean tasiyor",
            set(
                client.get("/api/capabilities").json().keys()
            )
            == {"email_notifications"},
        )

        check(
            "3. missing_email_settings yalnizca AYAR ADI donuyor",
            all(
                secret not in " ".join(
                    missing_email_settings()
                )
                for secret in secrets
            ),
        )

        # =================================================
        # SMTP TASIMA KATMANI (birim)
        # =================================================

        print("\n--- birim: SMTP tasima katmani ---")

        set_smtp()
        reset_smtp()

        result = send_email(
            recipient=RECIPIENT,
            subject="Konu",
            message="Düz metin gövde",
            html_message="<p>HTML gövde</p>",
        )

        session_info = SMTP_SESSIONS[0]

        check(
            "tek SMTP oturumu acildi",
            len(SMTP_SESSIONS) == 1 and result.success is True,
            f"{len(SMTP_SESSIONS)}",
        )

        check(
            "STARTTLS uygulandi",
            session_info["starttls"] == 1
            and session_info["class"] == "FakeSMTP",
        )

        check(
            "timeout verildi",
            session_info["timeout"]
            == email_service.SMTP_TIMEOUT_SECONDS,
            f"{session_info['timeout']}",
        )

        check(
            "baglanti kapatildi",
            session_info["closed"] is True,
        )

        check(
            "login yapildi",
            session_info["login"] == FAKE_USERNAME,
        )

        message = session_info["messages"][0]

        check(
            "mesaj gonderildi ve alici dogru",
            message["To"] == RECIPIENT
            and message["Subject"] == "Konu",
        )

        check(
            "gonderen adi kullanildi",
            message["From"]
            == "Website Monitor AI <bot@ornek.test>",
            f"{message['From']}",
        )

        check(
            "mail multipart/alternative "
            "(duz metin + HTML birlikte)",
            message.is_multipart()
            and {
                part.get_content_type()
                for part in message.walk()
                if part.get_content_maintype() == "text"
            }
            == {"text/plain", "text/html"},
        )

        # SSL modu
        set_smtp(smtp_use_ssl=True, smtp_port=465)
        reset_smtp()

        send_email(
            recipient=RECIPIENT,
            subject="Konu",
            message="Gövde",
        )

        ssl_session = SMTP_SESSIONS[0]

        check(
            "SSL modunda SMTP_SSL kullanildi",
            ssl_session["class"] == "FakeSMTPSSL"
            and ssl_session["ssl_context"] is True,
            f"{ssl_session['class']}",
        )

        check(
            "SSL modunda STARTTLS uygulanmadi",
            ssl_session["starttls"] == 0,
        )

        set_smtp()

        # Hata esleme
        failure_cases = [
            (
                "auth hatasi",
                {"login": smtplib.SMTPAuthenticationError(
                    535, b"auth failed"
                )},
            ),
            (
                "alici reddi",
                {"send": smtplib.SMTPRecipientsRefused(
                    {RECIPIENT: (550, b"no such user")}
                )},
            ),
            (
                "sunucu hatasi",
                {"send": smtplib.SMTPException("451 try later")},
            ),
            (
                "baglanti hatasi",
                {"connect": OSError("dns failure")},
            ),
            (
                "zaman asimi",
                {"connect": TimeoutError("timed out")},
            ),
        ]

        for label, behaviour in failure_cases:
            reset_smtp(behaviour)

            error = None

            try:
                send_email(
                    recipient=RECIPIENT,
                    subject="Konu",
                    message="Gövde",
                )

            except Exception as exc:
                error = exc

            check(
                f"{label}: EmailServiceError firlatiliyor",
                isinstance(error, EmailServiceError),
                f"{type(error).__name__}",
            )

            check(
                f"{label}: hata mesajinda sir yok",
                FAKE_PASSWORD not in str(error)
                and FAKE_USERNAME not in str(error),
                str(error)[:70],
            )

        reset_smtp()

        check(
            "sanitize_error_message parolayi maskeliyor",
            FAKE_PASSWORD
            not in sanitize_error_message(
                f"SMTP said {FAKE_PASSWORD} rejected"
            )
            and "***"
            in sanitize_error_message(
                f"SMTP said {FAKE_PASSWORD} rejected"
            ),
        )

        check(
            "sanitize_error_message kullanici adini maskeliyor",
            FAKE_USERNAME
            not in sanitize_error_message(
                f"login {FAKE_USERNAME} failed"
            ),
        )

        check(
            "sanitize_error_message uzunlugu siniryor",
            len(sanitize_error_message("x" * 5000))
            <= email_service.MAX_ERROR_MESSAGE_LENGTH + 3,
        )

        check(
            "SMTP yapilandirilmamisken send_email kontrollu hata",
            isinstance(
                _capture(
                    lambda: (
                        setattr(
                            email_service.settings,
                            "smtp_host",
                            None,
                        ),
                        send_email(
                            recipient=RECIPIENT,
                            subject="K",
                            message="G",
                        ),
                    )
                ),
                EmailServiceError,
            ),
        )

        set_smtp()

        # =================================================
        # PIPELINE
        # =================================================

        notification_service.send_email = counting_send_email

        # -------------------------------------------------
        # 4. notify_email=false -> mail YOK
        # -------------------------------------------------

        print("\n--- 4. notify_email kapali ---")

        reset_pipeline_counters()

        no_email_monitor = make_monitor(
            session,
            "email kapali",
            notify_in_app=True,
            notify_email=False,
            notification_email=None,
        )

        prepare_change(session, no_email_monitor)

        outcome = run_monitor(
            session,
            no_email_monitor,
            PAGE_V2,
        )

        check(
            "4. notify_email=false -> send_email cagrilmadi",
            SEND_CALLS == [],
            f"{len(SEND_CALLS)}",
        )

        check(
            "4. e-mail notification kaydi olusmadi",
            email_row(
                session,
                outcome.result.ai_analysis_record.id,
            )
            is None,
        )

        check(
            "15. in_app acikken yalnizca in-app olusuyor",
            outcome.notification is not None
            and outcome.email_notification is None,
        )

        # -------------------------------------------------
        # 5. relevant=false -> mail YOK
        # -------------------------------------------------

        print("\n--- 5-6. AI kararlari ---")

        reset_pipeline_counters()

        ANALYSIS_RESPONSE["value"] = analysis_json(
            relevant=False,
        )

        irrelevant_monitor = make_monitor(
            session,
            "ilgisiz degisiklik",
        )

        prepare_change(session, irrelevant_monitor)

        outcome = run_monitor(
            session,
            irrelevant_monitor,
            PAGE_V2,
        )

        check(
            "5. relevant=false -> mail gonderilmedi",
            SEND_CALLS == [],
            f"{len(SEND_CALLS)}",
        )

        check(
            "5. relevant=false -> in-app da olusmadi",
            outcome.notification is None
            and outcome.email_notification is None,
        )

        check(
            "5. relevant=false -> change ve analiz KORUNDU",
            outcome.result.change_record is not None
            and outcome.result.ai_analysis_record is not None,
        )

        # 6. notification_recommended=false
        reset_pipeline_counters()

        ANALYSIS_RESPONSE["value"] = analysis_json(
            relevant=True,
            notification_recommended=False,
        )

        not_recommended_monitor = make_monitor(
            session,
            "bildirim onerilmedi",
        )

        prepare_change(session, not_recommended_monitor)

        outcome = run_monitor(
            session,
            not_recommended_monitor,
            PAGE_V2,
        )

        check(
            "6. notification_recommended=false -> mail YOK",
            SEND_CALLS == [],
            f"{len(SEND_CALLS)}",
        )

        check(
            "6. notification_recommended=false -> in-app da YOK",
            outcome.notification is None
            and outcome.email_notification is None,
        )

        # -------------------------------------------------
        # 7-9. BASARILI GONDERIM
        # -------------------------------------------------

        print("\n--- 7-9, 16, 25. basarili gonderim ---")

        reset_pipeline_counters()

        ANALYSIS_RESPONSE["value"] = analysis_json()

        both_monitor = make_monitor(
            session,
            "her iki bildirim",
            notify_in_app=True,
            notify_email=True,
        )

        prepare_change(session, both_monitor)

        analysis_calls_before = len(ANALYSIS_CALLS)

        outcome = run_monitor(session, both_monitor, PAGE_V2)

        analysis = outcome.result.ai_analysis_record

        check(
            "7. send_email TAM OLARAK BIR KEZ cagrildi",
            len(SEND_CALLS) == 1,
            f"{len(SEND_CALLS)}",
        )

        check(
            "7. dogru aliciya gonderildi",
            SEND_CALLS[0]["recipient"] == RECIPIENT,
            SEND_CALLS[0]["recipient"],
        )

        email_notification = outcome.email_notification

        check(
            "8. e-mail notification DB kaydi olustu",
            email_notification is not None
            and email_notification.notification_type == "email",
        )

        check(
            "8. kayit dogru analiz ve change'e bagli",
            email_notification.ai_analysis_id == analysis.id
            and email_notification.change_id
            == outcome.result.change_record.id
            and email_notification.monitor_id
            == both_monitor.id,
        )

        check(
            "8. recipient kayitta duruyor",
            email_notification.recipient == RECIPIENT,
        )

        check(
            "9. status=sent",
            email_notification.status == STATUS_SENT,
            email_notification.status,
        )

        check(
            "9. sent_at dolduruldu",
            email_notification.sent_at is not None,
        )

        check(
            "9. error_message bos",
            email_notification.error_message is None,
        )

        check(
            "16. iki bildirim tipi de olustu",
            outcome.notification is not None
            and outcome.notification.notification_type
            == "in_app"
            and email_notification.notification_type
            == "email",
        )

        check(
            "16. ayni analiz icin iki ayri kayit var",
            in_app_row(session, analysis.id) is not None
            and email_row(session, analysis.id) is not None,
        )

        # 25. GEMINI TEKRAR CAGRILMADI
        check(
            "25. e-mail icin EK Gemini cagrisi yapilmadi "
            "(yalnizca degisiklik analizi)",
            len(ANALYSIS_CALLS) - analysis_calls_before == 1,
            f"{len(ANALYSIS_CALLS) - analysis_calls_before}",
        )

        check(
            "25. mail govdesi AI ozetini kullaniyor",
            analysis.summary in SEND_CALLS[0]["message"],
            SEND_CALLS[0]["message"][:80],
        )

        check(
            "25. mail govdesi evidence'i kullaniyor",
            analysis.evidence_text
            in SEND_CALLS[0]["message"],
            f"{analysis.evidence_text!r}",
        )

        check(
            "subject monitor adini ve onemi tasiyor",
            both_monitor.name in SEND_CALLS[0]["subject"]
            and "Yüksek önem" in SEND_CALLS[0]["subject"],
            SEND_CALLS[0]["subject"],
        )

        check(
            "govde website ve kategori bilgisini tasiyor",
            both_monitor.url in SEND_CALLS[0]["message"]
            and analysis.category
            in SEND_CALLS[0]["message"],
        )

        check(
            "govde uygulama detay baglantisini tasiyor",
            f"/notifications/{email_notification.id}"
            in SEND_CALLS[0]["message"],
            SEND_CALLS[0]["message"][-120:],
        )

        check(
            "HTML alternatif de uretildi",
            bool(SEND_CALLS[0]["html_message"])
            and "<div" in SEND_CALLS[0]["html_message"],
        )

        # -------------------------------------------------
        # 13. DUPLICATE
        # -------------------------------------------------

        print("\n--- 13. duplicate korumasi ---")

        send_calls_before = len(SEND_CALLS)

        duplicate = asyncio.run(
            notification_service.send_email_notification(
                db=session,
                monitor=both_monitor,
                change=outcome.result.change_record,
                ai_analysis=analysis,
            )
        )

        check(
            "13. ayni analiz icin IKINCI mail gonderilmedi",
            len(SEND_CALLS) == send_calls_before,
            f"{len(SEND_CALLS)}",
        )

        check(
            "13. mevcut kayit geri donuyor",
            duplicate is not None
            and duplicate.id == email_notification.id,
        )

        check(
            "13. e-mail kaydi tek",
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

        # -------------------------------------------------
        # 10-12. GONDERIM HATASI
        # -------------------------------------------------

        print("\n--- 10-12. gonderim hatasi izolasyonu ---")

        reset_pipeline_counters()

        SEND_BEHAVIOUR["error"] = EmailServiceError(
            "SMTP sunucusuna bağlanılamadı: dns failure"
        )

        failing_monitor = make_monitor(
            session,
            "gonderim hatasi",
        )

        prepare_change(session, failing_monitor)

        snapshots_before_failure = session.scalar(
            select(func.count())
            .select_from(Snapshot)
            .where(
                Snapshot.monitor_id == failing_monitor.id
            )
        )

        outcome = run_monitor(
            session,
            failing_monitor,
            PAGE_V2,
        )

        failed_notification = outcome.email_notification

        check(
            "10. gonderim hatasinda status=failed",
            failed_notification is not None
            and failed_notification.status == STATUS_FAILED,
            f"{failed_notification.status if failed_notification else None}",
        )

        check(
            "10. error_message dolduruldu",
            failed_notification.error_message
            and "bağlanılamadı"
            in failed_notification.error_message,
            f"{failed_notification.error_message!r}",
        )

        check(
            "10. sent_at bos birakildi",
            failed_notification.sent_at is None,
        )

        check(
            "10. error_message icinde SIR yok",
            FAKE_PASSWORD
            not in failed_notification.error_message
            and FAKE_USERNAME
            not in failed_notification.error_message,
        )

        check(
            "11. gonderim hatasina ragmen in-app bildirim ayakta",
            outcome.notification is not None
            and outcome.notification.notification_type
            == "in_app",
        )

        check(
            "12. snapshot korundu",
            session.scalar(
                select(func.count())
                .select_from(Snapshot)
                .where(
                    Snapshot.monitor_id
                    == failing_monitor.id
                )
            )
            == snapshots_before_failure + 1,
        )

        check(
            "12. change kaydi korundu",
            outcome.result.change_record is not None,
        )

        check(
            "12. AI analiz kaydi korundu",
            outcome.result.ai_analysis_record is not None
            and outcome.result.ai_error is None,
        )

        check(
            "12. monitor run BASARISIZ sayilmadi "
            "(hata firlamadi)",
            outcome.result.changed is True,
        )

        check(
            "12. last_checked_at guncellendi",
            failing_monitor.last_checked_at is not None,
        )

        # Beklenmeyen hata da pipeline'i dusurmemeli
        reset_pipeline_counters()

        SEND_BEHAVIOUR["error"] = RuntimeError(
            "beklenmeyen hata"
        )

        unexpected_monitor = make_monitor(
            session,
            "beklenmeyen hata",
        )

        prepare_change(session, unexpected_monitor)

        outcome = run_monitor(
            session,
            unexpected_monitor,
            PAGE_V2,
        )

        check(
            "beklenmeyen hata da yakalandi, status=failed",
            outcome.email_notification is not None
            and outcome.email_notification.status
            == STATUS_FAILED,
        )

        check(
            "beklenmeyen hatada in-app yine ayakta",
            outcome.notification is not None,
        )

        SEND_BEHAVIOUR["error"] = None

        # -------------------------------------------------
        # 14. YALNIZCA E-MAIL
        # -------------------------------------------------

        print("\n--- 14. yalnizca e-mail ---")

        reset_pipeline_counters()

        email_only_monitor = make_monitor(
            session,
            "yalniz email",
            notify_in_app=False,
            notify_email=True,
        )

        prepare_change(session, email_only_monitor)

        outcome = run_monitor(
            session,
            email_only_monitor,
            PAGE_V2,
        )

        check(
            "14. in_app=false + email=true -> yalnizca e-mail",
            outcome.notification is None
            and outcome.email_notification is not None
            and len(SEND_CALLS) == 1,
            f"in_app={outcome.notification} "
            f"send={len(SEND_CALLS)}",
        )

        check(
            "14. in-app kaydi HIC olusmadi",
            in_app_row(
                session,
                outcome.result.ai_analysis_record.id,
            )
            is None,
        )

        # -------------------------------------------------
        # SMTP YAPILANDIRILMAMISKEN PIPELINE
        # -------------------------------------------------

        print("\n--- SMTP yoksa pipeline ---")

        reset_pipeline_counters()

        set_smtp(smtp_host=None)

        unconfigured_monitor = make_monitor(
            session,
            "smtp yok",
        )

        prepare_change(session, unconfigured_monitor)

        outcome = run_monitor(
            session,
            unconfigured_monitor,
            PAGE_V2,
        )

        check(
            "SMTP yokken gonderim denenmedi",
            SEND_CALLS == [],
            f"{len(SEND_CALLS)}",
        )

        check(
            "SMTP yokken sahte 'sent' kaydi olusturulmadi",
            outcome.email_notification is None
            and email_row(
                session,
                outcome.result.ai_analysis_record.id,
            )
            is None,
        )

        check(
            "SMTP yokken in-app bildirim yine calisiyor",
            outcome.notification is not None,
        )

        set_smtp()

        # =================================================
        # 30. OKUNMAMIS / LISTE REGRESYONU
        # =================================================

        print("\n--- 30. bildirim listesi ve okunmamis sayisi ---")

        in_app_rows = get_notifications(
            db=session,
            notification_type="in_app",
        )

        email_rows = get_notifications(
            db=session,
            notification_type="email",
        )

        check(
            "30. in_app filtresi e-mail kayitlarini getirmiyor",
            all(
                row[0].notification_type == "in_app"
                for row in in_app_rows
            )
            and len(email_rows) > 0,
            f"in_app={len(in_app_rows)} email={len(email_rows)}",
        )

        in_app_unread = count_unread_notifications(
            db=session,
            notification_type="in_app",
        )

        all_unread = count_unread_notifications(
            db=session,
        )

        check(
            "30. in_app okunmamis sayisi e-mail'den etkilenmiyor",
            in_app_unread < all_unread,
            f"in_app={in_app_unread} tum={all_unread}",
        )

        check(
            "30. frontend in_app filtresi kullaniyor",
            "notification_type=in_app" in read(API_JS),
        )

        api_source = read(API_JS)

        check(
            "30. hem liste hem okunmamis sayisi filtreli",
            api_source.count(
                "IN_APP_NOTIFICATION_QUERY"
            )
            >= 3,
            f"{api_source.count('IN_APP_NOTIFICATION_QUERY')}",
        )

        # =================================================
        # 26-28. KAPSAM REGRESYONU
        # =================================================

        print("\n--- 26-28. kapsam regresyonu ---")

        from app.services.content_extractor import extract_content
        from app.services.section_extractor import (
            extract_section_candidates,
        )

        pricing_candidate = next(
            candidate
            for candidate in extract_section_candidates(
                PAGE_V1
            ).candidates
            if candidate.reference == "id:fiyatlandirma"
        )

        # 26. FULL PAGE
        reset_pipeline_counters()

        full_monitor = make_monitor(
            session,
            "full page + email",
        )

        prepare_change(session, full_monitor)

        outcome = run_monitor(session, full_monitor, PAGE_V2)

        check(
            "26. full_page snapshot hala TUM SAYFA",
            get_latest_snapshot(
                db=session,
                monitor_id=full_monitor.id,
            ).content
            == extract_content(PAGE_V2).text,
        )

        check(
            "26. full_page + email birlikte calisiyor",
            len(SEND_CALLS) == 1
            and outcome.email_notification.status
            == STATUS_SENT,
        )

        # 28. MANUAL SECTION
        reset_pipeline_counters()

        manual_monitor = create_monitor(
            db=session,
            name="manual + email",
            url="https://ornek-site.test/urun",
            topic="fiyat değişiklikleri",
            scope_type="manual_section",
            schedule_type="daily",
            schedule_time="09:00",
            notify_in_app=True,
            notify_email=True,
            notification_email=RECIPIENT,
            manual_section_reference=pricing_candidate.reference,
            manual_section_label=pricing_candidate.label,
        )

        prepare_change(session, manual_monitor)

        outcome = run_monitor(
            session,
            manual_monitor,
            PAGE_V2,
        )

        manual_snapshot = get_latest_snapshot(
            db=session,
            monitor_id=manual_monitor.id,
        )

        check(
            "28. manual_section hala yalnizca bolumu izliyor",
            "Blog" not in manual_snapshot.content
            and "150 TL" in manual_snapshot.content,
        )

        check(
            "28. manual_section + email birlikte calisiyor",
            len(SEND_CALLS) == 1
            and outcome.email_notification.status
            == STATUS_SENT,
        )

        # 27. AUTO SECTION
        reset_pipeline_counters()

        auto_monitor = create_monitor(
            db=session,
            name="auto + email",
            url="https://ornek-site.test/urun",
            topic="fiyat değişiklikleri",
            scope_type="auto_section",
            schedule_type="daily",
            schedule_time="09:00",
            notify_in_app=True,
            notify_email=True,
            notification_email=RECIPIENT,
        )

        # Bolum secimi zaten yapilmis kabul edilir; bu test
        # e-mail entegrasyonu icindir, bolum kesfi degil.
        auto_monitor.auto_section_reference = (
            pricing_candidate.reference
        )
        auto_monitor.auto_section_label = (
            pricing_candidate.label
        )
        session.flush()

        prepare_change(session, auto_monitor)

        outcome = run_monitor(session, auto_monitor, PAGE_V2)

        check(
            "27. auto_section hala yalnizca bolumu izliyor",
            "Blog"
            not in get_latest_snapshot(
                db=session,
                monitor_id=auto_monitor.id,
            ).content,
        )

        check(
            "27. auto_section + email birlikte calisiyor",
            len(SEND_CALLS) == 1
            and outcome.email_notification.status
            == STATUS_SENT,
        )

        check(
            "27. auto_section referansi bozulmadi",
            auto_monitor.auto_section_reference
            == pricing_candidate.reference,
        )

        # =================================================
        # 29. SCHEDULER REGRESYONU
        # =================================================

        print("\n--- 29. scheduler regresyonu ---")

        from app.core import scheduler

        check(
            "29. scheduler ayni execute_monitor_run'i kullaniyor",
            scheduler.execute_monitor_run
            is __import__(
                "app.services.monitor_run_service",
                fromlist=["execute_monitor_run"],
            ).execute_monitor_run,
        )

        reset_pipeline_counters()

        SEND_BEHAVIOUR["error"] = EmailServiceError(
            "SMTP sunucusuna bağlanılamadı"
        )

        scheduler_monitor = make_monitor(
            session,
            "scheduler + email hatasi",
        )

        prepare_change(session, scheduler_monitor)

        scheduler_error = _capture(
            lambda: run_monitor(
                session,
                scheduler_monitor,
                PAGE_V2,
            )
        )

        check(
            "29. e-mail hatasi execute_monitor_run'dan "
            "DISARI SIZMIYOR (scheduler cokmez)",
            scheduler_error is None,
            f"{type(scheduler_error).__name__}",
        )

        SEND_BEHAVIOUR["error"] = None

        # =================================================
        # 17-19. VALIDATION
        # =================================================

        print("\n--- 17-19. dogrulama ---")

        base_body = {
            "name": "dogrulama testi",
            "url": "https://ornek-site.test/urun",
            "topic": "fiyat",
            "scope_type": "full_page",
            "schedule_type": "daily",
            "schedule_time": "09:00",
        }

        created_ids = []

        optional_response = client.post(
            "/api/monitors",
            json={**base_body, "notify_email": False},
        )

        check(
            "17. notify_email=false -> adres opsiyonel (201)",
            optional_response.status_code == 201,
            f"{optional_response.status_code}",
        )

        if optional_response.status_code == 201:
            created_ids.append(
                optional_response.json()["id"]
            )

            check(
                "17. adres null kaydedildi",
                optional_response.json()[
                    "notification_email"
                ]
                is None,
            )

        required_response = client.post(
            "/api/monitors",
            json={**base_body, "notify_email": True},
        )

        check(
            "18. notify_email=true -> adres zorunlu (422)",
            required_response.status_code == 422,
            f"{required_response.status_code}",
        )

        for bad_email in [
            "not-an-email",
            "a@b",
            "a b@c.com",
            "@ornek.com",
            "ornek.com",
            "   ",
        ]:
            invalid_response = client.post(
                "/api/monitors",
                json={
                    **base_body,
                    "notify_email": True,
                    "notification_email": bad_email,
                },
            )

            check(
                f"19. gecersiz adres reddediliyor: {bad_email!r}",
                invalid_response.status_code == 422,
                f"{invalid_response.status_code}",
            )

        valid_response = client.post(
            "/api/monitors",
            json={
                **base_body,
                "notify_email": True,
                "notification_email": RECIPIENT,
            },
        )

        check(
            "gecerli adres kabul ediliyor (201)",
            valid_response.status_code == 201,
            f"{valid_response.status_code}",
        )

        if valid_response.status_code == 201:
            created_ids.append(valid_response.json()["id"])

            monitor_id = valid_response.json()["id"]

            # PATCH: adres olmadan e-mail acilamaz
            check(
                "18. PATCH: adressiz e-mail acilamaz",
                client.patch(
                    f"/api/monitors/{monitor_id}",
                    json={
                        "notify_email": True,
                        "notification_email": None,
                    },
                ).status_code
                == 422,
            )

            # PATCH: e-mail kapatilinca adres temizlenebilir
            off_response = client.patch(
                f"/api/monitors/{monitor_id}",
                json={
                    "notify_email": False,
                    "notification_email": None,
                },
            )

            check(
                "e-mail kapatilinca adres temizlenebiliyor",
                off_response.status_code == 200
                and off_response.json()["notify_email"]
                is False
                and off_response.json()[
                    "notification_email"
                ]
                is None,
                f"{off_response.status_code}",
            )

            check(
                "tekrar acarken adres yeniden gerekiyor",
                client.patch(
                    f"/api/monitors/{monitor_id}",
                    json={"notify_email": True},
                ).status_code
                == 422,
            )

        # Gercek DB'ye yazilan dogrulama kayitlari temizlenir.
        if created_ids:
            cleanup = SessionLocal()

            try:
                for monitor_id in created_ids:
                    record = cleanup.get(Monitor, monitor_id)

                    if record is not None:
                        cleanup.delete(record)

                cleanup.commit()
            finally:
                cleanup.close()

        # =================================================
        # 24. API CEVAPLARINDA SIR YOK
        # =================================================

        print("\n--- 24. sir sizintisi ---")

        api_bodies = [
            client.get("/api/capabilities").text,
            client.get("/api/health").text,
            client.get("/api/monitors").text,
            client.get("/api/notifications").text,
        ]

        check(
            "24. API cevaplarinda SMTP parolasi yok",
            not any(
                FAKE_PASSWORD in body
                for body in api_bodies
            ),
        )

        check(
            "24. API cevaplarinda SMTP kullanici adi yok",
            not any(
                FAKE_USERNAME in body
                for body in api_bodies
            ),
        )

        check(
            "24. API cevaplarinda SMTP host yok",
            not any(
                "smtp.ornek.test" in body
                for body in api_bodies
            ),
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

        email_service.smtplib.SMTP = original_smtp
        email_service.smtplib.SMTP_SSL = original_smtp_ssl
        ai_analyzer.genai.Client = original_client
        db_monitor_service.fetch_website = original_fetch
        notification_service.send_email = original_send

        for field, value in original_settings.items():
            setattr(email_service.settings, field, value)

    # -----------------------------------------------------
    # 20-23. FRONTEND (statik kaynak kontrolu)
    # -----------------------------------------------------

    print("\n--- 20-23. frontend ---")

    app_source = read(APP_JSX)

    monitor_form = section(
        app_source,
        "function MonitorForm(",
        "function NewMonitorPage()",
    )

    check(
        "22. 'E-mail bildirimi (yakında)' etiketi KALDIRILDI",
        "E-mail bildirimi (yakında)" not in app_source,
    )

    check(
        "22. 'henüz uygulanmadı' e-mail metni kaldirildi",
        "E-mail gönderimi henüz uygulanmadı"
        not in app_source,
    )

    check(
        "20-21. checkbox capability'ye gore disable ediliyor",
        "disabled={!emailAvailable}" in monitor_form,
    )

    check(
        "20-21. capability backend'den okunuyor",
        "getCapabilities()" in monitor_form
        and "email_notifications" in monitor_form,
    )

    check(
        "20. SMTP yoksa dogru mesaj gosteriliyor",
        "E-mail gönderimi için SMTP yapılandırması gerekli."
        in monitor_form,
    )

    check(
        "20. capability okunamazsa ozellik KAPALI kabul ediliyor",
        "setEmailAvailable(false)" in monitor_form,
    )

    check(
        "21. checkbox artik degistirilebilir",
        "setEmailNotification(" in monitor_form,
    )

    check(
        "23. adres alani yalnizca checkbox acikken render ediliyor",
        re.search(
            r"\{emailNotification && \([\s\S]{0,400}?"
            r'name="notification_email"',
            monitor_form,
        )
        is not None,
    )

    check(
        "23. adres alani required",
        re.search(
            r'name="notification_email"[\s\S]{0,500}?required',
            monitor_form,
        )
        is not None,
    )

    check(
        "yeni izleme varsayilani notify_email=false",
        "notify_email: false"
        in section(
            app_source,
            "const NEW_MONITOR_DEFAULTS",
            "function monitorToFormValues(",
        ),
    )

    check(
        "edit formu kayitli adresi yukluyor",
        "useState(initialValues.notification_email)"
        in monitor_form,
    )

    check(
        "e-mail kapaliyken payload adresi null gonderiyor",
        re.search(
            r"notification_email:[\s\S]{0,120}?: null,",
            monitor_form,
        )
        is not None,
    )

    check(
        "24. frontend kaynaginda SMTP sirri yok",
        "smtp_password" not in app_source
        and "SMTP_PASSWORD" not in app_source
        and "smtp_username" not in app_source,
    )

    check(
        "monitor kartinda e-mail durumu gosteriliyor",
        "monitor.notify_email" in app_source,
    )

    # -----------------------------------------------------
    # ROLLBACK DOGRULAMASI
    # -----------------------------------------------------

    print("\n--- rollback dogrulamasi ---")

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
        "test monitorleri kalici olarak yazilmadi",
        monitors_after == monitors_before,
        f"{monitors_before} -> {monitors_after}",
    )

    check(
        "test bildirimleri kalici olarak yazilmadi",
        notifications_after == notifications_before,
        f"{notifications_before} -> {notifications_after}",
    )

    check(
        "hicbir gercek mail gonderilmedi "
        "(tum SMTP oturumlari sahte)",
        all(
            session_info["class"]
            in ("FakeSMTP", "FakeSMTPSSL")
            for session_info in SMTP_SESSIONS
        ),
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
