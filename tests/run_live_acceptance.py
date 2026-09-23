"""
CANLI KABUL TESTLERI - AYRI GIRIS NOKTASI

    python -m tests.run_live_acceptance

Bu komut GERCEK dis kaynaklara giden kabul testlerini
calistirir. Normal gelistirme dongusunun parcasi DEGILDIR.

--------------------------------------------------------
NORMAL SUITE'TEN FARKI
--------------------------------------------------------

Otomatik suite (python -m tests.run_automated_suite):

    dis ag           : YOK
    disari baglanma  : engellenir ve assert edilir
    tekrarlanabilir  : EVET

Canli kabul (bu komut):

    resmigazete.gov.tr  : GERCEK istek
    mevzuat.gov.tr      : GERCEK istek
    tekrarlanabilir     : HAYIR

Bu testlerin basarisiz olmasi uygulama kodunun bozuldugu
anlamina GELMEZ. Dis kaynak erisilemez olabilir veya yayin
duzenini degistirmis olabilir. Bu yuzden CI'da zorunlu
degildir ve otomatik suite tarafindan toplanmaz.

--------------------------------------------------------
DEGISMEYEN GARANTILER
--------------------------------------------------------

Burada da:

    GERCEK GEMINI CAGRISI : 0 (varsayilan)
        Toplanan dosyalarda genai.Client sahte nesneyle
        degistirilir ve her cagri sayilir.

        Gercek model kullanan kabul testleri VARSAYILAN
        OLARAK ATLANIR; --with-gemini ile acilir.

    GERCEK SMTP           : 0

    KALICI DB KAYDI       : 0
        Her sey tek transaction icinde yapilip geri
        alinir.

--------------------------------------------------------
CIKIS KODU
--------------------------------------------------------

  0  -> butun dosyalar basarili
  1  -> en az bir dosya basarisiz

Secenekler:

  --list          toplanan dosyalari yazar, calistirmaz
  --only NAME     yalnizca adinda NAME gecen dosyalari
                  calistirir
  --with-gemini   gercek Gemini kotasi harcayan kabul
                  testlerini de calistirir (varsayilan: HAYIR)
  --timeout N     dosya basina saniye siniri (varsayilan 900)
"""

import sys

from pathlib import Path

from tests.run_automated_suite import (
    DEFAULT_TIMEOUT,
    describe,
    run_module,
)


TESTS_DIR = Path(__file__).resolve().parent


LIVE_GLOB = "acceptance_*.py"


# GERCEK GEMINI KOTASI HARCAYAN KABUL TESTLERI
#
# Varsayilan olarak CALISTIRILMAZ. Bu komutun sozu
# "gercek dis kaynak EVET, gercek Gemini HAYIR"dir:
# kabul testi calistirmak kota harcamamali ve Google
# tarafindaki gecici bir 503 yuzunden kirmiziya
# donmemelidir.
#
# Calistirmak icin:
#     python -m tests.run_live_acceptance --with-gemini
REAL_GEMINI_MODULES = {
    # Bolum secimini (section discovery) GERCEK modelle
    # bir kez dogrular. Kendi basligi bunu acikca soyler.
    "acceptance_auto_section": (
        "1 gercek Gemini bolum secimi cagrisi yapar"
    ),
}


def collect(include_gemini: bool = False) -> list[str]:

    names = [
        path.stem
        for path in sorted(TESTS_DIR.glob(LIVE_GLOB))
    ]

    if include_gemini:
        return names

    return [
        name
        for name in names
        if name not in REAL_GEMINI_MODULES
    ]


def main() -> None:

    argv = sys.argv[1:]

    timeout = DEFAULT_TIMEOUT

    if "--timeout" in argv:
        timeout = int(argv[argv.index("--timeout") + 1])

    only = None

    if "--only" in argv:
        only = argv[argv.index("--only") + 1]

    include_gemini = "--with-gemini" in argv

    names = collect(include_gemini=include_gemini)

    if only:
        names = [name for name in names if only in name]

    if "--list" in argv:

        print(f"Toplanan canli kabul dosyasi: {len(names)}")

        for name in names:
            print(f"  tests/{name}.py")

        return

    print("=" * 70)
    print("CANLI KABUL TESTLERI")
    print("=" * 70)

    print(f"Toplanan dosya : {len(names)}")
    print("Gercek dis ag  : EVET")
    print(
        "Gercek Gemini  : "
        + ("ACIK (--with-gemini)" if include_gemini else "0")
    )
    print("Gercek SMTP    : 0")
    print("Kalici DB      : 0")

    if not include_gemini and not only:

        print("-" * 70)
        print("Atlanan (gercek Gemini kotasi harcar):")

        for name, reason in sorted(REAL_GEMINI_MODULES.items()):
            print(f"  tests/{name}.py  ({reason})")

        print("  --with-gemini ile calistirilabilir.")

    print("-" * 70)

    results = []

    for index, name in enumerate(names, start=1):

        print(
            f"[{index:>2}/{len(names)}] {name} ... ",
            end="",
            flush=True,
        )

        result = run_module(name, timeout)
        results.append(result)

        status = describe(result)

        counts = (
            f"{result['passed']} pass"
            if result["passed"] is not None
            else "ozet yok"
        )

        if result["failed"]:
            counts += f", {result['failed']} FAIL"

        print(
            f"{status} ({counts}, {result['duration']:.1f}s)"
        )

    total_passed = sum(
        result["passed"] or 0 for result in results
    )

    total_failed = sum(
        result["failed"] or 0 for result in results
    )

    broken = [
        result
        for result in results
        if describe(result) != "OK"
    ]

    print("-" * 70)

    print(f"Dosya          : {len(results)}")
    print(f"Toplam PASS    : {total_passed}")
    print(f"Toplam FAIL    : {total_failed}")
    print(f"Sorunlu dosya  : {len(broken)}")

    if broken:

        print("=" * 70)
        print("BASARISIZ DOSYALAR")
        print("=" * 70)

        for result in broken:

            print()
            print(
                f"--- {result['name']} "
                f"({describe(result)}, "
                f"rc={result['returncode']}) ---"
            )

            for line in result["output"].strip().splitlines()[-40:]:
                print(f"  {line}")

        print("=" * 70)
        print(
            "NOT: canli kabul basarisizligi dis kaynak "
            "kaynakli olabilir."
        )
        print("=" * 70)
        raise SystemExit(1)

    print("=" * 70)
    print("TUM CANLI KABUL TESTLERI BASARILI")
    print("=" * 70)


if __name__ == "__main__":
    main()
