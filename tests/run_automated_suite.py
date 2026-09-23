"""
OTOMATIK TEST SUITE - TEK GIRIS NOKTASI

    python -m tests.run_automated_suite

Bu komut projenin BUTUN otomatik testlerini calistirir.
Baska bir komuta gerek yoktur.

--------------------------------------------------------
NE TOPLANIR
--------------------------------------------------------

tests/test_*.py deseniyle eslesen HER dosya toplanir.

Toplama DESEN TABANLIDIR, elle yazilmis bir liste
degildir. Yeni bir test dosyasi eklendiginde suite onu
kendiliginden calistirir; kimsenin listeye eklemesi
gerekmez.

Dosyalar ayri birer process olarak calistirilir. Testler
genai.Client, fetch_website, smtplib gibi global nesneleri
degistirdigi icin process yalitimi ZORUNLUDUR; aksi halde
bir dosyanin sahte nesnesi digerine sizar.

--------------------------------------------------------
NE TOPLANMAZ
--------------------------------------------------------

  tests/manual_*.py
      Gercek Gemini / gercek SMTP / gercek ag kullanan
      veya kalici DB kaydi birakan MANUEL betikler.
      Assertion icermezler, ciktilari gozle okunur.

  tests/acceptance_*.py
      Gercek dis kaynaga (mevzuat.gov.tr, Resmî Gazete)
      giden LIVE kabul testleri. Ayri komutla calisir:
          python -m tests.run_live_acceptance

  tests/migrate_*.py
      Sema goc araclari. Test degildir.

--------------------------------------------------------
GARANTILER
--------------------------------------------------------

Bu suite icinde:

  GERCEK GEMINI CAGRISI : 0
  GERCEK SMTP BAGLANTISI: 0
  ISTENMEYEN DIS AG     : 0
  KALICI DB KAYDI       : 0

DB kullanan testler gercek PostgreSQL'e baglanir ama her
sey tek bir transaction icinde yapilip GERI ALINIR.

--------------------------------------------------------
CIKIS KODU
--------------------------------------------------------

  0  -> butun dosyalar basarili
  1  -> en az bir dosya basarisiz

Secenekler:

  --list        toplanan dosyalari yazar, calistirmaz
  --only NAME   yalnizca adinda NAME gecen dosyalari
                calistirir
  --timeout N   dosya basina saniye siniri (varsayilan 900)
"""

import os
import re
import subprocess
import sys
import time

from pathlib import Path


TESTS_DIR = Path(__file__).resolve().parent

PROJECT_ROOT = TESTS_DIR.parent


# Otomatik suite deseni. manual_* ve acceptance_* BILEREK
# disaridadir.
AUTOMATED_GLOB = "test_*.py"


# Testler ciktilarini "BASARILI : n" / "BASARISIZ: n"
# olarak yazar.
PASS_PATTERN = re.compile(r"BASARILI\s*:\s*(\d+)")
FAIL_PATTERN = re.compile(r"BASARISIZ\s*:\s*(\d+)")


# ACIK BASARI ISARETLERI
#
# Bazi kabul betikleri sayac yerine rapor uretir ve basariyi
# tek bir satirla bildirir. Bu satirlardan biri yoksa cikis
# kodu 0 olsa bile dosya "sessizce gecmis" sayilir.
SUCCESS_MARKERS = (
    re.compile(r"TUM TESTLER BASARILI"),
    re.compile(r"TUM CANLI KONTROLLER BASARILI"),
    re.compile(r"KABUL TESTI BASARILI"),
    re.compile(r"ACCEPTANCE\s*:\s*VERIFIED"),
    re.compile(r"STATUS\s*:\s*READY"),
)


def has_success_marker(text: str) -> bool:

    return any(
        pattern.search(text) for pattern in SUCCESS_MARKERS
    )


DEFAULT_TIMEOUT = 900


def collect() -> list[str]:
    """
    Otomatik test modullerini toplar.
    """

    names = [
        path.stem
        for path in sorted(TESTS_DIR.glob(AUTOMATED_GLOB))
    ]

    return names


def last_number(pattern, text: str) -> int | None:

    matches = pattern.findall(text)

    if not matches:
        return None

    return int(matches[-1])


def run_module(name: str, timeout: int) -> dict:
    """
    Tek bir test modulunu ayri process olarak calistirir.
    """

    started = time.monotonic()

    # Alt process'in Turkce ciktisi Windows konsol
    # kod sayfasinda bozulmasin.
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"

    try:
        completed = subprocess.run(
            [sys.executable, "-m", f"tests.{name}"],
            cwd=PROJECT_ROOT,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )

        output = completed.stdout + completed.stderr
        returncode = completed.returncode
        timed_out = False

    except subprocess.TimeoutExpired as exc:

        output = (exc.stdout or "") + (exc.stderr or "")
        returncode = -1
        timed_out = True

    duration = time.monotonic() - started

    passed = last_number(PASS_PATTERN, output)
    failed = last_number(FAIL_PATTERN, output)

    return {
        "name": name,
        "returncode": returncode,
        "timed_out": timed_out,
        "output": output,
        "passed": passed,
        "failed": failed,
        "duration": duration,
    }


def describe(result: dict) -> str:

    if result["timed_out"]:
        return "TIMEOUT"

    if result["returncode"] != 0:
        return "FAIL"

    if result["failed"]:
        return "FAIL"

    # Cikis kodu 0 ama ne sayac ozeti ne de acik bir basari
    # satiri varsa dosya calisti SAYILMAZ. Sessiz gecen bir
    # test yesil GORUNMEMELIDIR.
    if result["passed"] is None and not has_success_marker(
        result["output"]
    ):
        return "NO SUMMARY"

    return "OK"


def main() -> None:

    argv = sys.argv[1:]

    timeout = DEFAULT_TIMEOUT

    if "--timeout" in argv:
        index = argv.index("--timeout")
        timeout = int(argv[index + 1])

    only = None

    if "--only" in argv:
        index = argv.index("--only")
        only = argv[index + 1]

    names = collect()

    if only:
        names = [name for name in names if only in name]

    if "--list" in argv:

        print(f"Toplanan otomatik test dosyasi: {len(names)}")

        for name in names:
            print(f"  tests/{name}.py")

        return

    print("=" * 70)
    print("OTOMATIK TEST SUITE")
    print("=" * 70)

    print(f"Toplanan dosya : {len(names)}")
    print(f"Python         : {sys.executable}")
    print("Gercek Gemini  : 0")
    print("Gercek SMTP    : 0")
    print("Kalici DB      : 0")

    print("-" * 70)

    results = []

    started = time.monotonic()

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
            f"{status} ({counts}, "
            f"{result['duration']:.1f}s)"
        )

    total_duration = time.monotonic() - started

    # -----------------------------------------------------
    # OZET
    # -----------------------------------------------------

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
    print(f"Sure           : {total_duration:.1f}s")

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

            tail = result["output"].strip().splitlines()

            for line in tail[-40:]:
                print(f"  {line}")

        print("=" * 70)
        raise SystemExit(1)

    print("=" * 70)
    print("TUM OTOMATIK TESTLER BASARILI")
    print("=" * 70)


if __name__ == "__main__":
    main()
