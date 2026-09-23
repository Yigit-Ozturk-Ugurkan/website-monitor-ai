"""
MANUEL BETIK - GERCEK AG ISTEGI YAPAR

SINIF: manual tool

Bu dosya OTOMATIK TEST DEGILDIR. https://example.com
adresini gercekten indirir ve ciktiyi ekrana basar.
Hatalari YUTAR; basarisizlikta bile 0 ile cikar.

  GERCEK GEMINI : hayir
  GERCEK SMTP   : hayir
  GERCEK AG     : EVET
  DB YAZAR      : hayir

run_automated_suite.py bu dosyayi TOPLAMAZ.

Calistirma:
    python -m tests.manual_fetcher
"""

import asyncio

from app.services.web_fetcher import (
    WebsiteFetchError,
    fetch_website,
)


async def main():
    url = "https://example.com"

    print("=" * 60)
    print("WEBSITE FETCH TEST")
    print("=" * 60)

    print(f"URL          : {url}")

    try:
        result = await fetch_website(url)

        print(f"Status Code  : {result.status_code}")
        print(f"Final URL    : {result.final_url}")
        print(f"Content Type : {result.content_type}")
        print(f"HTML Length  : {len(result.html)} karakter")

        print("\nHTML İlk 500 Karakter:")
        print("-" * 60)
        print(result.html[:500])
        print("-" * 60)

        print("\nTEST BAŞARILI")

    except WebsiteFetchError as exc:
        print("\nTEST BAŞARISIZ")
        print(f"Hata: {exc}")

    except Exception as exc:
        print("\nBEKLENMEYEN HATA")
        print(f"{type(exc).__name__}: {exc}")


if __name__ == "__main__":
    asyncio.run(main())