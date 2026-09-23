"""
MANUEL BETIK - YEREL TEST SUNUCUSU GEREKTIRIR

SINIF: manual tool

Bu dosya OTOMATIK TEST DEGILDIR. http://localhost:8000
adresinde test_site/ dizininin servis ediliyor olmasini
bekler ve snapshot dosyalarini data/snapshots/ altina
KALICI olarak yazar.

Hazirlik:
    python -m http.server 8000 --directory test_site

  GERCEK GEMINI : hayir
  GERCEK SMTP   : hayir
  GERCEK AG     : EVET (localhost)
  DB YAZAR      : hayir (diske snapshot yazar)

run_automated_suite.py bu dosyayi TOPLAMAZ.

Calistirma:
    python -m tests.manual_monitor
"""

import asyncio

from app.services.monitor_service import run_monitor


async def main():
    monitor_key = "local-ai-news"
    url = "http://localhost:8000"

    print("=" * 60)
    print("WEBSITE MONITOR TEST")
    print("=" * 60)

    print(f"Monitor : {monitor_key}")
    print(f"URL     : {url}")

    result = await run_monitor(
        monitor_key=monitor_key,
        url=url,
    )

    print(f"\nTitle       : {result.content.title}")
    print(f"Status Code : {result.fetch_result.status_code}")
    print(f"Text Length : {result.content.text_length}")

    if result.first_run:
        print("\nİLK KONTROL")
        print("-" * 60)
        print("Önceki snapshot bulunamadı.")
        print("İlk snapshot oluşturuldu.")

        return

    print("\nDEĞİŞİKLİK KONTROLÜ")
    print("-" * 60)

    change = result.change_result

    print(f"Changed    : {change.changed}")
    print(f"Similarity : {change.similarity_ratio}")

    if not change.changed:
        print("\nDeğişiklik bulunamadı.")
        return

    print("\nSİLİNENLER")
    print("-" * 60)

    if change.removed_lines:
        for line in change.removed_lines:
            print(f"- {line}")
    else:
        print("Yok")

    print("\nEKLENENLER")
    print("-" * 60)

    if change.added_lines:
        for line in change.added_lines:
            print(f"+ {line}")
    else:
        print("Yok")


if __name__ == "__main__":
    asyncio.run(main())