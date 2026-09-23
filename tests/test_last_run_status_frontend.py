"""
SON CALISTIRMA DURUMU ARAYUZ DOGRULAMASI

Kapsam:
    20. kart no_change sonucunu gosterebilir
    21. kart fetch_failed sonucunu gosterebilir
    22. kart comparison_completed sonucunu gosterebilir
    23. durum stilleri mevcut token'lari kullanir
    +   frontend backend durumlarindan MESAJ URETMEZ
    +   "Sonuç" alani "Son kontrol" ile ayni blokta
    +   siddet siniflarinin tamami tanimli
    +   deneme zamani ile basarili kontrol zamani ayri
    +   guvenlik: dangerouslySetInnerHTML kullanilmiyor

NOT: Projede bir JS test kosucusu (vitest/jest) kurulu
degil ve yeni paket kurulmuyor. Asagidakiler CALISMA
ZAMANI component testi degil, KAYNAK KODU uzerinde
yapilan statik dogrulamalardir. Bu, mevcut frontend
testleriyle ayni yaklasimdir.

Backend semasi da okunur: kartin ihtiyaci olan alanlarin
GERCEKTEN donuldugu dogrulanir.

Veritabani kullanilmaz, ag istegi yapilmaz.

Calistirma:
    python -m tests.test_last_run_status_frontend
"""

import io
import re

from pathlib import Path

from app.services.monitor_run_status import (
    RUN_STATUSES,
    STATUS_COMPARISON_COMPLETED,
    STATUS_FETCH_FAILED,
    STATUS_NO_CHANGE,
    describe,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]

APP_JSX = PROJECT_ROOT / "frontend" / "src" / "App.jsx"
INDEX_CSS = PROJECT_ROOT / "frontend" / "src" / "index.css"

MONITOR_SCHEMA = (
    PROJECT_ROOT / "app" / "schemas" / "monitor.py"
)


PASSED = []
FAILED = []


def check(label: str, condition: bool, detail: str = "") -> None:

    status = "PASS" if condition else "FAIL"

    print(f"[{status}] {label}" + (f" -> {detail}" if detail else ""))

    if condition:
        PASSED.append(label)
    else:
        FAILED.append(f"{label} -> {detail}")


def read(path: Path) -> str:
    return io.open(path, encoding="utf-8").read()


def section(source: str, start: str, end: str) -> str:
    """
    Iki isaret arasindaki bolumu dondurur.

    Bitis isareti baslangicin ICINDE de gecebilir
    ("class MonitorResponse" / "class "); bu yuzden arama
    baslangic isaretinin SONRASINDAN baslar.
    """

    begin = source.index(start)
    after = begin + len(start)

    return source[begin : source.index(end, after)]


def main() -> None:

    print("=" * 70)
    print("SON CALISTIRMA DURUMU ARAYUZ DOGRULAMASI")
    print("=" * 70)

    app_source = read(APP_JSX)
    css_source = read(INDEX_CSS)
    schema_source = read(MONITOR_SCHEMA)

    # -----------------------------------------------------
    # KART ALANI
    # -----------------------------------------------------

    print()
    print("--- kart alani ---")

    check(
        "'Sonuç' alani kartta var",
        "Sonuç" in app_source,
    )

    check(
        "kart last_run_message degerini basiyor",
        "monitor.last_run_message" in app_source,
    )

    check(
        "kart last_run_severity ile sinif seciyor",
        "monitor.last_run_severity" in app_source,
    )

    # "Sonuç" alani "Son kontrol" ile AYNI blokta olmali.
    card_block = section(
        app_source,
        "Son kontrol",
        "monitor-run",
    )

    check(
        "'Sonuç' alani 'Son kontrol' ile ayni blokta",
        "Sonuç" in card_block
        and "last_run_message" in card_block,
    )

    # -----------------------------------------------------
    # 20-22. DURUMLARIN GORUNURLUGU
    #
    # Mesaj metni backend'den geldigi icin arayuzde sabit
    # metin ARANMAZ. Aranan sey: kartin herhangi bir
    # durumu gosterebilecek yapiya sahip olmasi.
    # -----------------------------------------------------

    print()
    print("--- durumlarin gorunurlugu ---")

    renders_message = (
        "monitor.last_run_message" in card_block
    )

    for number, status in (
        ("20", STATUS_NO_CHANGE),
        ("21", STATUS_FETCH_FAILED),
        ("22", STATUS_COMPARISON_COMPLETED),
    ):
        info = describe(status)

        severity_class = f".run-status.{info.severity}"

        check(
            f"{number}. kart {status} sonucunu gosterebilir",
            renders_message
            and info is not None
            and severity_class in css_source,
            f"siddet={info.severity}",
        )

    # -----------------------------------------------------
    # 23. STIL
    # -----------------------------------------------------

    print()
    print("--- stil ---")

    check(
        "23. .run-status temel sinifi tanimli",
        ".run-status {" in css_source,
    )

    severities = sorted(
        {describe(status).severity for status in RUN_STATUSES}
    )

    missing = [
        severity
        for severity in severities
        if f".run-status.{severity}" not in css_source
    ]

    check(
        "23. kullanilan tum siddet siniflari tanimli",
        not missing,
        f"eksik={missing} kullanilan={severities}",
    )

    # Mevcut tasarim token'lari yeniden kullanilmali.
    style_block = section(
        css_source,
        "SON CALISTIRMA SONUCU",
        ".run-status-time",
    )

    check(
        "23. mevcut renk token'lari yeniden kullaniliyor",
        "var(--success)" in style_block
        and "var(--brand)" in style_block
        and "#b54708" in style_block
        and "#b42318" in style_block,
    )

    check(
        "23. yeni renk degiskeni eklenmedi",
        "--run-status" not in css_source,
    )

    # -----------------------------------------------------
    # FRONTEND MESAJ URETMEZ
    # -----------------------------------------------------

    print()
    print("--- sozlesme ---")

    # Durum adlarindan frontend'de mesaj tablosu
    # kurulmamali; metin backend'den gelir.
    invented = [
        status
        for status in RUN_STATUSES
        if f'"{status}"' in app_source
        or f"'{status}'" in app_source
    ]

    check(
        "frontend durum adlarindan mesaj URETMEZ",
        not invented,
        f"gomulu durum adlari={invented}",
    )

    for message_fragment in (
        "değişiklik tespit edilmedi",
        "siteye erişilemedi",
        "karşılaştırma tamamlandı",
    ):
        check(
            f"'{message_fragment}' metni frontend'de gomulu degil",
            message_fragment not in app_source,
        )

    # -----------------------------------------------------
    # ZAMAN AYRIMI
    # -----------------------------------------------------

    print()
    print("--- zaman ayrimi ---")

    check(
        "deneme zamani ayri gosteriliyor",
        "last_run_at" in app_source
        and "Son deneme" in app_source,
    )

    # -----------------------------------------------------
    # 9. SON DENEME GORUNURLUK KURALI
    #
    # BUG: basarili calistirmada da "Son deneme" satiri
    # goruluyordu, cunku frontend iki zaman damgasini
    # KARSILASTIRIYORDU. Ayni calistirmaya ait olsalar
    # bile milisaniye farkla yazildiklari icin kosul her
    # zaman dogruydu.
    #
    # Karar artik backend'den gelen tek bir boolean'dir.
    # -----------------------------------------------------

    check(
        "9. gorunurluk backend boolean'ina bagli",
        "monitor.last_run_succeeded === false" in app_source,
    )

    check(
        "9. zaman damgalari KARSILASTIRILMIYOR",
        "last_run_at !==" not in app_source
        and "last_run_at ===" not in app_source
        and "!== monitor.last_checked_at" not in app_source,
    )

    # Frontend hangi durumlarin basarisiz sayildigini
    # BILMEMELIDIR.
    failure_names = [
        status
        for status in RUN_STATUSES
        if not describe(status).succeeded
    ]

    leaked = [
        status
        for status in failure_names
        if status in app_source
    ]

    check(
        "9. basarisiz durum adlari frontend'de gomulu degil",
        not leaked,
        f"gomulu={leaked} kontrol_edilen={failure_names}",
    )

    check(
        "9. frontend severity'yi basari gostergesi olarak "
        "KULLANMIYOR",
        '"error"' not in app_source
        and "'error'" not in app_source,
        "siddet ile deneme basarisi ayri kavramlar",
    )

    # -----------------------------------------------------
    # BACKEND SOZLESMESI
    # -----------------------------------------------------

    print()
    print("--- backend sozlesmesi ---")

    response_block = section(
        schema_source,
        "class MonitorResponse",
        "class ",
    )

    for field in (
        "last_run_status",
        "last_run_message",
        "last_run_at",
        "last_run_severity",
        "last_run_succeeded",
    ):
        check(
            f"MonitorResponse {field} tasiyor",
            field in response_block,
        )

    check(
        "siddet ve basari backend'de tureiliyor",
        "fill_run_status_fields" in schema_source,
    )

    # -----------------------------------------------------
    # GUVENLIK
    # -----------------------------------------------------

    print()
    print("--- guvenlik ---")

    # Aciklama satirinda gecmesi serbest; ARANAN sey
    # gercek kullanimdir.
    check(
        "dangerouslySetInnerHTML KULLANIMI yok",
        re.search(
            r"dangerouslySetInnerHTML\s*[=:]",
            app_source,
        )
        is None,
        "yalnizca aciklama satirinda gecmesi serbest",
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
