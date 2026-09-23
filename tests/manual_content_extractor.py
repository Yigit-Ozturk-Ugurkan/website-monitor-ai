"""
MANUEL BETIK - GERCEK AG ISTEGI YAPAR

SINIF: manual tool

Bu dosya OTOMATIK TEST DEGILDIR. https://example.com
adresini indirip cikarilan metni ekrana basar. Hatalari
YUTAR; basarisizlikta bile 0 ile cikar.

  GERCEK GEMINI : hayir
  GERCEK SMTP   : hayir
  GERCEK AG     : EVET
  DB YAZAR      : hayir

run_automated_suite.py bu dosyayi TOPLAMAZ.

Calistirma:
    python -m tests.manual_content_extractor
"""

import asyncio

from app.services.content_extractor import extract_content
from app.services.web_fetcher import (
    WebsiteFetchError,
    fetch_website,
)


async def main():
    url = "https://example.com"

    print("=" * 60)
    print("CONTENT EXTRACTOR TEST")
    print("=" * 60)

    try:
        print(f"URL: {url}")
        print("\nSite indiriliyor...")

        fetch_result = await fetch_website(url)

        print("Site başarıyla indirildi.")

        content = extract_content(fetch_result.html)

        print("\nSONUÇ")
        print("-" * 60)

        print(f"Title        : {content.title}")
        print(f"HTML Length  : {len(fetch_result.html)}")
        print(f"Text Length  : {content.text_length}")

        print("\nTemiz Metin:")
        print("-" * 60)

        print(content.text[:1000])

        print("-" * 60)
        print("\nTEST BAŞARILI")

    except WebsiteFetchError as exc:
        print("\nWEBSITE FETCH HATASI")
        print(exc)

    except Exception as exc:
        print("\nBEKLENMEYEN HATA")
        print(f"{type(exc).__name__}: {exc}")


if __name__ == "__main__":
    asyncio.run(main())