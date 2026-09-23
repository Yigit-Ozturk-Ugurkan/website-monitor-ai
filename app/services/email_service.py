"""
SMTP taşıma katmanı.

Bu modül YALNIZCA e-mail göndermekten sorumludur:

  - içerik üretimi        -> app/services/email_content.py
  - bildirim kaydı/durumu -> app/services/notification_service.py

Sırlar (kullanıcı adı, parola) hiçbir zaman hata mesajına,
loga veya API cevabına yazılmaz.
"""

import smtplib
import ssl

from dataclasses import dataclass
from email.headerregistry import Address
from email.message import EmailMessage

from app.core.config import settings


# SMTP islemleri icin ust sinir.
# Baglanti kurulamayan bir sunucu pipeline'i kilitlemesin.
SMTP_TIMEOUT_SECONDS = 30


# E-mail bildirimi icin ZORUNLU ayarlar.
# Biri bile eksikse ozellik "yapilandirilmamis" sayilir.
REQUIRED_EMAIL_SETTINGS = (
    ("SMTP_HOST", "smtp_host"),
    ("SMTP_USERNAME", "smtp_username"),
    ("SMTP_PASSWORD", "smtp_password"),
    ("SMTP_FROM_EMAIL", "smtp_from_email"),
)


class EmailServiceError(Exception):
    """E-mail gönderimi başarısız olduğunda oluşturulan hata."""


@dataclass
class EmailSendResult:
    success: bool
    recipient: str
    subject: str


# ---------------------------------------------------------
# YAPILANDIRMA DURUMU
#
# Frontend'e "bu ozellik kullanilabilir mi" bilgisi bu
# fonksiyonlardan turetilir. Deger DONDURULMEZ, yalnizca
# eksik ayarlarin ADI bilinir; parola gibi degerler
# hicbir yere sizmaz.
# ---------------------------------------------------------

def missing_email_settings() -> list[str]:
    """
    Eksik olan zorunlu SMTP ayarlarinin adlarini dondurur.

    Yalnizca AYAR ADI doner; deger asla donmez.
    """

    missing = []

    for env_name, field_name in REQUIRED_EMAIL_SETTINGS:

        value = getattr(settings, field_name, None)

        if not value or not str(value).strip():
            missing.append(env_name)

    return missing


def is_email_configured() -> bool:
    """
    E-mail bildirimi gonderilebilir durumda mi?
    """

    return not missing_email_settings()


def _validate_email_settings() -> None:

    missing_settings = missing_email_settings()

    if missing_settings:
        raise EmailServiceError(
            "Eksik SMTP ayarları: "
            + ", ".join(missing_settings)
        )


# ---------------------------------------------------------
# HATA TEMIZLIGI
#
# SMTP sunuculari bazen cevaplarinda kullanici adini veya
# kimlik dogrulama ayrintilarini yankilar. Hata mesaji
# veritabanina ve kullaniciya gittigi icin once temizlenir.
# ---------------------------------------------------------

REDACTED = "***"

MAX_ERROR_MESSAGE_LENGTH = 500


def sanitize_error_message(message: str) -> str:
    """
    Hata metninden sirlari temizler ve boyutunu sinirlar.
    """

    cleaned = " ".join((message or "").split())

    for field_name in ("smtp_password", "smtp_username"):

        secret = getattr(settings, field_name, None)

        if secret and str(secret).strip():
            cleaned = cleaned.replace(
                str(secret),
                REDACTED,
            )

    if len(cleaned) > MAX_ERROR_MESSAGE_LENGTH:
        cleaned = (
            cleaned[:MAX_ERROR_MESSAGE_LENGTH].rstrip() + "..."
        )

    return cleaned


def _build_sender() -> str:
    """
    Gonderen basligi. Ad tanimliysa "Ad <adres>" bicimi kullanilir.
    """

    from_email = (settings.smtp_from_email or "").strip()

    from_name = (settings.smtp_from_name or "").strip()

    if not from_name:
        return from_email

    local_part, _, domain = from_email.partition("@")

    if not domain:
        return from_email

    return str(
        Address(
            display_name=from_name,
            username=local_part,
            domain=domain,
        )
    )


def _open_connection():
    """
    Yapilandirmaya gore SMTP baglantisi acar.

    smtp_use_ssl:  bastan SSL (genellikle 465)
    smtp_use_tls:  STARTTLS  (genellikle 587)

    SSL acikken STARTTLS uygulanmaz; ikisi ayni anda
    kullanilamaz.
    """

    if settings.smtp_use_ssl:

        return smtplib.SMTP_SSL(
            settings.smtp_host,
            settings.smtp_port,
            timeout=SMTP_TIMEOUT_SECONDS,
            context=ssl.create_default_context(),
        )

    return smtplib.SMTP(
        settings.smtp_host,
        settings.smtp_port,
        timeout=SMTP_TIMEOUT_SECONDS,
    )


def send_email(
    recipient: str,
    subject: str,
    message: str,
    html_message: str | None = None,
) -> EmailSendResult:
    """
    SMTP üzerinden e-mail gönderir.

    message düz metin gövdedir ve ZORUNLUDUR.
    html_message verilirse mail multipart/alternative olur;
    düz metin her zaman alternatif olarak kalır.

    Bu fonksiyon bloklar. Async akış içinden çağrılırken
    thread'e devredilmelidir (bkz. notification_service).
    """

    if not recipient.strip():
        raise ValueError(
            "E-mail alıcısı boş olamaz."
        )

    if not subject.strip():
        raise ValueError(
            "E-mail konusu boş olamaz."
        )

    if not message.strip():
        raise ValueError(
            "E-mail içeriği boş olamaz."
        )

    _validate_email_settings()

    email = EmailMessage()

    email["From"] = _build_sender()
    email["To"] = recipient
    email["Subject"] = subject

    email.set_content(message)

    if html_message and html_message.strip():
        email.add_alternative(
            html_message,
            subtype="html",
        )

    try:

        # "with" blogu hata durumunda da baglantiyi kapatir.
        with _open_connection() as smtp:

            smtp.ehlo()

            # SSL zaten sifreli; STARTTLS yalnizca duz
            # baglantida uygulanir.
            if settings.smtp_use_tls and not settings.smtp_use_ssl:

                smtp.starttls(
                    context=ssl.create_default_context()
                )

                smtp.ehlo()

            smtp.login(
                settings.smtp_username,
                settings.smtp_password,
            )

            smtp.send_message(
                email
            )

    except smtplib.SMTPAuthenticationError as exc:
        # Sunucu cevabi kullanici adini yankilayabilir.
        raise EmailServiceError(
            "SMTP kimlik doğrulaması başarısız."
        ) from exc

    except smtplib.SMTPRecipientsRefused as exc:
        raise EmailServiceError(
            sanitize_error_message(
                f"Alıcı adresi reddedildi: {recipient}"
            )
        ) from exc

    except smtplib.SMTPException as exc:
        raise EmailServiceError(
            sanitize_error_message(
                f"SMTP hatası: {exc}"
            )
        ) from exc

    except ssl.SSLError as exc:
        raise EmailServiceError(
            sanitize_error_message(
                f"SMTP TLS hatası: {exc}"
            )
        ) from exc

    except OSError as exc:
        # DNS, baglanti reddi, zaman asimi.
        raise EmailServiceError(
            sanitize_error_message(
                f"SMTP sunucusuna bağlanılamadı: {exc}"
            )
        ) from exc

    return EmailSendResult(
        success=True,
        recipient=recipient,
        subject=subject,
    )
