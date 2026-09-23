"""
DIFF GURULTU AZALTMA TESTLERI

SINIF: automated (deterministic, CI-safe)

GERCEK KULLANICI KABULUNDE GORULEN SORUN

  Techmeme monitorunde sayfa icerigi buyuk olcude YENIDEN
  SIRALANDIGI icin difflib devasa bir diff uretti:

      439 eklenen + 700 silinen satir

  Bu satirlarin cogu ne eklenmis ne silinmisti; yalnizca
  KONUM degistirmisti. Sonuc: sisen Change kaydi, gereksiz
  Gemini token maliyeti, gurultulu bildirim detayi.

Kapsam:

     1. ayni snapshot -> degisiklik yok
     2. basit ekleme korunur
     3. basit silme korunur
     4. ayni satirlar farkli sirada -> iptal
     5. yeniden siralama + gercek yeni satir
     6. yeniden siralama + gercek silinen satir
     7. multiset / tekrar davranisi
     8. bosluk normalizasyonu guvenli iptal
     9. benzer ama esit olmayan metin IPTAL EDILMEZ
    10. sayi degisikligi korunur
    11. model adi degisikligi korunur
    12. yalnizca noktalama satirlari elenir
    13. bos satir regresyonu
    14. Techmeme yeniden siralama fixture'i
    15. Techmeme gercek OpenAI kaniti KORUNUR
    16. Techmeme snapshot saati elenir
    17. anlamli tarih degisikligi elenmez
    18. ucucu profil isaret yoksa CALISMAZ
    19. buyuk/kucuk harf farki iptal EDILMEZ
    20. tanilama sayaclari dogru
    21. ham listeler DEGISTIRILMEZ
    22. performans: dogrusal, ikili karsilastirma yok
    23. mevzuat plani azaltmadan MUAF
    24. website plani azaltmaya TABI

GERCEK GEMINI YOK. GERCEK SMTP YOK. GERCEK AG YOK.
VERITABANI KULLANILMAZ.

Calistirma:
    python -m tests.test_diff_noise_reduction
"""

import time

from app.services.change_detector import detect_changes
from app.services.regulation_monitor_service import is_regulation_monitor
from app.services.diff_noise_profiles import (
    PROFILES,
    collect_volatile_lines,
)
from app.services.diff_noise_reducer import (
    is_trivial_line,
    normalize_line,
    reduce_diff_noise,
)


class _FakePlan:
    """
    is_regulation_monitor yalnizca tracking_plan alanini
    okur; tam Monitor nesnesi gerekmez.
    """

    def __init__(self, tracking_plan):
        self.tracking_plan = tracking_plan


PASSED = []
FAILED = []


NEWLINE = chr(10)


def check(label: str, condition: bool, detail: str = "") -> None:

    status = "PASS" if condition else "FAIL"

    print(f"[{status}] {label}" + (f" -> {detail}" if detail else ""))

    if condition:
        PASSED.append(label)
    else:
        FAILED.append(f"{label} -> {detail}")


def diff(old: str, new: str, volatile=frozenset()):
    """
    Ham diff uretir ve azaltmayi uygular.
    """

    raw = detect_changes(old_text=old, new_text=new)

    reduction = reduce_diff_noise(
        added_lines=raw.added_lines,
        removed_lines=raw.removed_lines,
        volatile_lines=volatile,
    )

    return raw, reduction


# ---------------------------------------------------------
# TECHMEME FIXTURE
#
# Gercek sayfanin yapisini taklit eder: ust menu, snapshot
# saati, isaret satiri ve yeniden siralanan kaynak adlari.
# ---------------------------------------------------------

def techmeme_page(clock: str, stories: list[str]) -> str:

    header = [
        "Open Links In New Tab",
        "Mobile",
        "Archives",
        clock,
        "Enter Techmeme snapshot date and time:",
        "Cancel",
    ]

    return NEWLINE.join(header + stories)


TECHMEME_OLD_STORIES = [
    "Business Insider",
    "The Information",
    "Security Affairs",
    "The Indian Express",
    "Simon Willison's Weblog",
    "Digit",
    "TechCrunch",
]

TECHMEME_NEW_STORIES = [
    "Business Insider",
    "Security Affairs",
    "The Information",
    "The Indian Express",
    "Digit",
    "Simon Willison's Weblog",
    "TechCrunch",
]


OPENAI_STORY = (
    "In response to the 'wiki incident', OpenAI says it is "
    "working on a framework for reporting misalignment "
    "incidents during training, evaluation, and deployment"
)


def main() -> None:

    print("=" * 70)
    print("DIFF GURULTU AZALTMA TESTLERI")
    print("=" * 70)

    # =====================================================
    # TEMEL DAVRANIS
    # =====================================================

    print()
    print("--- temel davranis ---")

    raw, reduction = diff("Alpha\nBeta", "Alpha\nBeta")

    check(
        "1. ayni snapshot -> degisiklik yok",
        raw.changed is False and reduction.is_empty,
        f"changed={raw.changed}",
    )

    raw, reduction = diff("Alpha\nBeta", "Alpha\nBeta\nGamma")

    check(
        "2. basit ekleme korunur",
        reduction.added_lines == ["Gamma"]
        and reduction.removed_lines == [],
        f"+{reduction.added_lines} -{reduction.removed_lines}",
    )

    raw, reduction = diff("Alpha\nBeta\nGamma", "Alpha\nBeta")

    check(
        "3. basit silme korunur",
        reduction.removed_lines == ["Gamma"]
        and reduction.added_lines == [],
        f"+{reduction.added_lines} -{reduction.removed_lines}",
    )

    # =====================================================
    # 4-6. TASIMA IPTALI
    # =====================================================

    print()
    print("--- tasima iptali ---")

    raw, reduction = diff(
        "Alpha\nBeta\nGamma\nDelta",
        "Gamma\nAlpha\nDelta\nBeta",
    )

    check(
        "4. ayni satirlar farkli sirada -> iptal",
        reduction.is_empty
        and reduction.moved_lines_cancelled > 0,
        f"iptal={reduction.moved_lines_cancelled} "
        f"+{reduction.added_lines} -{reduction.removed_lines}",
    )

    raw, reduction = diff(
        "Alpha\nBeta\nGamma",
        "Gamma\nAlpha\nNEW IMPORTANT STORY",
    )

    check(
        "5. yeniden siralama + gercek yeni satir",
        reduction.added_lines == ["NEW IMPORTANT STORY"]
        and reduction.removed_lines == ["Beta"],
        f"+{reduction.added_lines} -{reduction.removed_lines}",
    )

    raw, reduction = diff(
        "Alpha\nBeta\nGamma\nDelta",
        "Delta\nAlpha\nGamma",
    )

    check(
        "6. yeniden siralama + gercek silinen satir",
        reduction.removed_lines == ["Beta"]
        and reduction.added_lines == [],
        f"+{reduction.added_lines} -{reduction.removed_lines}",
    )

    # =====================================================
    # 7. MULTISET
    # =====================================================

    print()
    print("--- multiset ---")

    raw, reduction = diff(
        "OpenAI\nOpenAI\nGemini",
        "OpenAI\nGemini",
    )

    check(
        "7. tekrar eden satirin YALNIZCA fazlasi silinir",
        reduction.removed_lines == ["OpenAI"]
        and reduction.added_lines == [],
        f"+{reduction.added_lines} -{reduction.removed_lines}",
    )

    # Ters yon: bir kopya gercekten eklenmis.
    raw, reduction = diff(
        "OpenAI\nGemini",
        "OpenAI\nOpenAI\nGemini",
    )

    check(
        "7. gercekten eklenen kopya korunur",
        reduction.added_lines == ["OpenAI"]
        and reduction.removed_lines == [],
        f"+{reduction.added_lines} -{reduction.removed_lines}",
    )

    # Uc kopyadan biri kalmali.
    reduction = reduce_diff_noise(
        added_lines=["A", "A"],
        removed_lines=["A", "A", "A"],
    )

    check(
        "7. min(added, removed) kadar iptal edilir",
        reduction.added_lines == []
        and reduction.removed_lines == ["A"]
        and reduction.moved_lines_cancelled == 4,
        f"+{reduction.added_lines} -{reduction.removed_lines} "
        f"iptal={reduction.moved_lines_cancelled}",
    )

    # =====================================================
    # 8-11, 19. NORMALIZASYON SINIRLARI
    # =====================================================

    print()
    print("--- normalizasyon sinirlari ---")

    reduction = reduce_diff_noise(
        added_lines=["  Business   Insider  "],
        removed_lines=["Business Insider"],
    )

    check(
        "8. bosluk farki guvenli sekilde iptal edilir",
        reduction.is_empty,
        f"+{reduction.added_lines} -{reduction.removed_lines}",
    )

    raw, reduction = diff(
        "OpenAI announces GPT-6",
        "OpenAI announces GPT-7",
    )

    check(
        "9. benzer ama esit olmayan metin IPTAL EDILMEZ",
        reduction.added_lines == ["OpenAI announces GPT-7"]
        and reduction.removed_lines
        == ["OpenAI announces GPT-6"],
        f"+{reduction.added_lines}",
    )

    raw, reduction = diff("Price: $10", "Price: $11")

    check(
        "10. sayi degisikligi korunur",
        reduction.added_lines == ["Price: $11"]
        and reduction.removed_lines == ["Price: $10"],
        f"+{reduction.added_lines}",
    )

    check(
        "11. model adi degisikligi korunur",
        normalize_line("GPT-6") != normalize_line("GPT-7"),
    )

    check(
        "19. buyuk/kucuk harf farki iptal EDILMEZ",
        normalize_line("OpenAI") != normalize_line("openai"),
        "agresif lowercase YOK",
    )

    # =====================================================
    # 12-13. ANLAMSIZ SATIRLAR
    # =====================================================

    print()
    print("--- anlamsiz satirlar ---")

    check(
        "12. yalnizca noktalama satirlari anlamsiz",
        is_trivial_line(",")
        and is_trivial_line(":")
        and is_trivial_line("|")
        and is_trivial_line("---"),
    )

    check(
        "12. icinde harf/rakam olan satir KORUNUR",
        not is_trivial_line("3")
        and not is_trivial_line("$5")
        and not is_trivial_line("A."),
        "kisa ama gercek icerik silinmez",
    )

    reduction = reduce_diff_noise(
        added_lines=[",", "Gercek icerik", ":"],
        removed_lines=["|"],
    )

    check(
        "12. anlamsiz satirlar diff'ten cikar",
        reduction.added_lines == ["Gercek icerik"]
        and reduction.removed_lines == []
        and reduction.trivial_lines_removed == 3,
        f"+{reduction.added_lines} "
        f"anlamsiz={reduction.trivial_lines_removed}",
    )

    check(
        "13. bos satir anlamsiz sayilir",
        is_trivial_line("") and is_trivial_line("   "),
    )

    # =====================================================
    # 14-18. TECHMEME / UCUCU
    # =====================================================

    print()
    print("--- Techmeme fixture ---")

    old_page = techmeme_page(
        "September 7, 2026, 3:25 AM",
        TECHMEME_OLD_STORIES,
    )

    new_page = techmeme_page(
        "September 7, 2026, 4:00 AM",
        TECHMEME_NEW_STORIES + [OPENAI_STORY],
    )

    volatile = collect_volatile_lines(
        old_content=old_page,
        new_content=new_page,
    )

    raw, reduction = diff(old_page, new_page, volatile)

    check(
        "14. Techmeme yeniden siralamasi buyuk olcude iptal",
        reduction.moved_lines_cancelled > 0
        and len(reduction.added_lines)
        + len(reduction.removed_lines)
        < len(raw.added_lines) + len(raw.removed_lines),
        f"ham={len(raw.added_lines)}+{len(raw.removed_lines)} "
        f"final={len(reduction.added_lines)}+"
        f"{len(reduction.removed_lines)}",
    )

    check(
        "15. gercek OpenAI kaniti KORUNUR",
        OPENAI_STORY in reduction.added_lines,
        "wiki incident / framework metni",
    )

    clock_lines = [
        line
        for line in reduction.added_lines
        + reduction.removed_lines
        if "3:25 AM" in line or "4:00 AM" in line
    ]

    check(
        "16. Techmeme snapshot saati elenir",
        not clock_lines
        and reduction.volatile_lines_removed == 2,
        f"kalan={clock_lines} "
        f"ucucu={reduction.volatile_lines_removed}",
    )

    # 17. ISARET YOKSA ayni tarih satiri KORUNMALI.
    plain_old = NEWLINE.join(
        [
            "Yururluk tarihi",
            "September 7, 2026, 3:25 AM",
            "Diger icerik",
        ]
    )

    plain_new = NEWLINE.join(
        [
            "Yururluk tarihi",
            "September 7, 2026, 4:00 AM",
            "Diger icerik",
        ]
    )

    plain_volatile = collect_volatile_lines(
        old_content=plain_old,
        new_content=plain_new,
    )

    raw, reduction = diff(plain_old, plain_new, plain_volatile)

    check(
        "17. anlamli tarih degisikligi ELENMEZ",
        "September 7, 2026, 4:00 AM" in reduction.added_lines
        and "September 7, 2026, 3:25 AM"
        in reduction.removed_lines,
        f"+{reduction.added_lines} -{reduction.removed_lines}",
    )

    check(
        "18. ucucu profil isaret yoksa CALISMAZ",
        plain_volatile == frozenset(),
        f"{plain_volatile}",
    )

    check(
        "18. profil tablosu kucuk ve acik",
        len(PROFILES) <= 3
        and all(profile.marker for profile in PROFILES),
        f"profil={len(PROFILES)}",
    )

    # =====================================================
    # 20-21. TANILAMA VE YAN ETKI
    # =====================================================

    print()
    print("--- tanilama ve yan etki ---")

    original_added = ["A", "B", ","]
    original_removed = ["B", "A"]

    snapshot_added = list(original_added)
    snapshot_removed = list(original_removed)

    reduction = reduce_diff_noise(
        added_lines=original_added,
        removed_lines=original_removed,
    )

    check(
        "20. tanilama sayaclari dogru",
        reduction.moved_lines_cancelled == 4
        and reduction.trivial_lines_removed == 1
        and reduction.total_removed_noise == 5,
        f"iptal={reduction.moved_lines_cancelled} "
        f"anlamsiz={reduction.trivial_lines_removed}",
    )

    check(
        "21. ham listeler DEGISTIRILMEZ",
        original_added == snapshot_added
        and original_removed == snapshot_removed,
        "girdi listeleri mutasyona ugramamali",
    )

    # =====================================================
    # 22. PERFORMANS
    # =====================================================

    print()
    print("--- performans ---")

    big_added = [f"satir {i}" for i in range(20000)]
    big_removed = list(reversed(big_added))

    started = time.perf_counter()

    reduction = reduce_diff_noise(
        added_lines=big_added,
        removed_lines=big_removed,
    )

    elapsed = time.perf_counter() - started

    check(
        "22. 40.000 satir dogrusal surede islenir",
        reduction.is_empty and elapsed < 1.0,
        f"{elapsed * 1000:.1f} ms",
    )

    # =====================================================
    # 23-24. PLAN AYRIMI
    # =====================================================

    print()
    print("--- plan ayrimi ---")

    # Karar PIPELINE'DA DEGIL, cagirandadir.
    # run_database_monitor takip planini bilmez.
    import inspect

    from app.services import db_monitor_service
    from app.services import monitor_run_service

    pipeline_source = inspect.getsource(db_monitor_service)

    check(
        "23. pipeline takip planini BILMEZ",
        "tracking_plan" not in pipeline_source,
        "azaltma karari cagirandan gelir (reduce_noise)",
    )

    check(
        "23. pipeline reduce_noise bayragi kabul eder",
        "reduce_noise" in pipeline_source,
    )

    caller_source = inspect.getsource(monitor_run_service)

    check(
        "23. mevzuat izlemesi azaltmadan MUAF",
        "reduce_noise=not is_regulation_monitor(monitor)"
        in caller_source,
        "hukuki metinde tasima anlamli olabilir",
    )

    check(
        "24. website izlemesi azaltmaya TABI",
        is_regulation_monitor(
            _FakePlan("website_monitor")
        )
        is False
        and is_regulation_monitor(_FakePlan(None)) is False
        and is_regulation_monitor(_FakePlan("")) is False,
        "eski kayitlar website_monitor sayilir",
    )

    check(
        "24. mevzuat plani dogru taninir",
        is_regulation_monitor(
            _FakePlan("regulation_monitor")
        )
        is True,
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
