"""
MANUEL BETIK - GERCEK GEMINI API CAGRISI YAPAR

SINIF: live external acceptance

Bu dosya OTOMATIK TEST DEGILDIR. Assertion icermez;
ciktisi gozle okunur.

  GERCEK GEMINI : EVET (kota harcar)
  GERCEK SMTP   : hayir
  GERCEK AG     : EVET
  DB YAZAR      : hayir

run_automated_suite.py bu dosyayi TOPLAMAZ.

Calistirma:
    python -m tests.manual_gemini
"""

from google import genai

from app.core.config import settings


def main():

    print("=" * 60)
    print("GEMINI API TEST")
    print("=" * 60)

    print(f"Model : {settings.gemini_model}")

    try:

        client = genai.Client(
            api_key=settings.gemini_api_key,
        )

        response = client.models.generate_content(
            model=settings.gemini_model,
            contents=(
                "Sadece şu cümleyi yaz: "
                "Gemini API bağlantısı başarılı."
            ),
        )

        print("\nGEMINI RESPONSE")
        print("-" * 60)

        print(response.text)

        print("-" * 60)
        print("\nTEST BAŞARILI")

    except Exception as exc:

        print("\nTEST BAŞARISIZ")
        print("-" * 60)

        print(
            f"{type(exc).__name__}: {exc}"
        )

        raise


if __name__ == "__main__":
    main()