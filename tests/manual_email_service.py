"""
MANUEL BETIK - GERCEK SMTP SUNUCUSUNA BAGLANIR

SINIF: live external acceptance

Bu dosya OTOMATIK TEST DEGILDIR. .env icindeki
SMTP_TEST_TO adresine GERCEK bir e-posta gonderir.

E-posta govdesi ve gonderim mantigi AUTOMATED olarak
sahte SMTP ile dogrulanir:
    tests/test_email_notification.py

  GERCEK GEMINI : hayir
  GERCEK SMTP   : EVET (gercek mail gonderir)
  GERCEK AG     : EVET
  DB YAZAR      : hayir

run_automated_suite.py bu dosyayi TOPLAMAZ.

Calistirma:
    python -m tests.manual_email_service
"""

from app.core.config import settings

from app.services.email_service import (
    EmailServiceError,
    send_email,
)


def main():

    print("=" * 60)
    print("EMAIL SERVICE TEST")
    print("=" * 60)

    recipient = settings.smtp_test_to

    if not recipient:

        print(
            "SMTP_TEST_TO .env içinde tanımlı değil."
        )

        return

    subject = (
        "Website Monitor AI - E-mail Testi"
    )

    message = """
Merhaba,

Website Monitor AI e-mail servisi başarıyla çalışıyor.

Bu mesaj geliştirme ortamından gönderilen bir test e-mailidir.

Website Monitor AI
""".strip()

    print(
        f"Recipient : {recipient}"
    )

    print(
        f"SMTP Host : {settings.smtp_host}"
    )

    print(
        f"SMTP Port : {settings.smtp_port}"
    )

    print("\nE-mail gönderiliyor...")

    try:

        result = send_email(
            recipient=recipient,
            subject=subject,
            message=message,
        )

        print("\nEMAIL SONUCU")
        print("-" * 60)

        print(
            f"Success   : {result.success}"
        )

        print(
            f"Recipient : {result.recipient}"
        )

        print(
            f"Subject   : {result.subject}"
        )

        print("\nTEST BAŞARILI")

    except EmailServiceError as exc:

        print("\nTEST BAŞARISIZ")
        print("-" * 60)

        print(exc)


if __name__ == "__main__":
    main()