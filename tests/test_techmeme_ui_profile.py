"""
TECHMEME UI GURULTU PROFILI TESTLERI

SINIF: automated (deterministic, CI-safe)

Onceki sprintte genel tasima/yeniden siralama iptali
eklendi. Gercek Techmeme kabulunde diff ciddi sekilde
temizlendi ama iki grup satir HALA kaliyordu:

  1. UI ac/kapa kontrolleri

         Expand More For Next 4
         Unexpand More For Next 3

  2. "Find" + goreli zaman bloklari

         Find
         2½ hours ago

Bunlar haber icerigi degil, Techmeme'in kendi arayuz
davranisidir.

BU KURALLARIN TAMAMI KAYNAK PROFILINE BAGLIDIR.
Genel bir "N hours ago gorursen sil" kurali YOKTUR:
baska bir sitede "Updated 3 hours ago" gercek icerik
olabilir.

Kapsam:

     1. Techmeme: "Expand More For Next" elenir
     2. Techmeme: "Expand More For Next 4" elenir
     3. Techmeme: "Unexpand More For Next 3" elenir
     4. genel site: ayni metinler KORUNUR
     5. Techmeme: Find + "3 hours ago" -> ikisi de elenir
     6. Techmeme: Find + "2½ hours ago" -> ikisi de elenir
     7. Techmeme: Find + "45 minutes ago" -> ikisi de elenir
     8. genel site: Find + "3 hours ago" KORUNUR
     9. makale metni "... 3 hours ago" KORUNUR
    10. yapinin disindaki tek "Find" KORUNUR
    11. "September 7, 2026" KORUNUR (saat kurali disinda)
    12. gercek haber basligi KORUNUR
    13. gercek yayinci adi KORUNUR
    14. "Expand OpenAI coverage" KORUNUR
    15. tekil bicimler (1 hour ago / 1 minute ago)
    16. snapshot saati kurali BOZULMADI
    17. tasima iptali davranisi BOZULMADI
    18. multiset davranisi BOZULMADI
    19. profil yalnizca tam satir esler
    20. ucucu kume normalize edilmis satir doner

GERCEK GEMINI YOK. GERCEK SMTP YOK. GERCEK AG YOK.
VERITABANI KULLANILMAZ.

Calistirma:
    python -m tests.test_techmeme_ui_profile
"""

from app.services.change_detector import detect_changes
from app.services.diff_noise_profiles import (
    TECHMEME_MARKER,
    collect_volatile_lines,
)
from app.services.diff_noise_reducer import (
    normalize_line,
    reduce_diff_noise,
)


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


# ---------------------------------------------------------
# SAYFA KURUCULAR
#
# techmeme_page : profili ETKINLESTIREN isaret satirini
#                 tasir
# generic_page  : ayni icerik, isaret YOK
# ---------------------------------------------------------

def techmeme_page(body: list[str]) -> str:

    header = [
        "Open Links In New Tab",
        "September 7, 2026, 4:00 AM",
        TECHMEME_MARKER,
        "Cancel",
    ]

    return NEWLINE.join(header + body)


def generic_page(body: list[str]) -> str:

    header = [
        "Ana Sayfa",
        "September 7, 2026, 4:00 AM",
        "Haberler",
    ]

    return NEWLINE.join(header + body)


def volatile_for(builder, old_body, new_body):
    """
    Iki sayfa icin ucucu satir kumesini uretir.
    """

    return collect_volatile_lines(
        old_content=builder(old_body),
        new_content=builder(new_body),
    )


def reduced(builder, old_body, new_body):
    """
    Ham diff + azaltma.
    """

    old_page = builder(old_body)
    new_page = builder(new_body)

    raw = detect_changes(old_text=old_page, new_text=new_page)

    reduction = reduce_diff_noise(
        added_lines=raw.added_lines,
        removed_lines=raw.removed_lines,
        volatile_lines=collect_volatile_lines(
            old_content=old_page,
            new_content=new_page,
        ),
    )

    return raw, reduction


def surviving(reduction) -> list[str]:
    return reduction.added_lines + reduction.removed_lines


# ---------------------------------------------------------
# GERCEK ICERIK
# ---------------------------------------------------------

STORY_A = (
    "OpenAI's rebel agent swarm died young, but its "
    "chilling logs live on"
)

STORY_B = (
    "OpenAI Agents Used a Public Wiki to Coordinate: "
    "What We Know"
)

STORY_C = (
    "Sources: UBS requires AI skills when hiring junior "
    "investment bankers, making it one of the first major "
    "financial institutions to explicitly require AI literacy"
)

PUBLISHER = "Simon Foy / Financial Times:"


def main() -> None:

    print("=" * 70)
    print("TECHMEME UI GURULTU PROFILI TESTLERI")
    print("=" * 70)

    # =====================================================
    # 1-4. AC/KAPA KONTROLLERI
    # =====================================================

    print()
    print("--- ac/kapa kontrolleri ---")

    controls = [
        "Expand More For Next",
        "Expand More For Next 4",
        "Unexpand More For Next 3",
    ]

    volatile = volatile_for(
        techmeme_page,
        [STORY_A],
        controls + [STORY_A],
    )

    for number, control in (
        ("1", "Expand More For Next"),
        ("2", "Expand More For Next 4"),
        ("3", "Unexpand More For Next 3"),
    ):
        check(
            f"{number}. Techmeme: {control!r} elenir",
            normalize_line(control) in volatile,
        )

    raw, reduction = reduced(
        techmeme_page,
        [STORY_A],
        controls + [STORY_A],
    )

    check(
        "1-3. kontrol satirlari diff'te GORUNMEZ",
        not any(
            "xpand More For Next" in line
            for line in surviving(reduction)
        ),
        f"{surviving(reduction)}",
    )

    # ----- GENEL SITE: ayni metinler korunmali -----

    generic_volatile = volatile_for(
        generic_page,
        [STORY_A],
        controls + [STORY_A],
    )

    check(
        "4. genel site: kontrol satirlari ucucu SAYILMAZ",
        generic_volatile == frozenset(),
        f"{generic_volatile}",
    )

    raw, reduction = reduced(
        generic_page,
        [STORY_A],
        controls + [STORY_A],
    )

    check(
        "4. genel site: kontrol satirlari KORUNUR",
        all(
            control in reduction.added_lines
            for control in controls
        ),
        f"+{reduction.added_lines}",
    )

    # =====================================================
    # 5-8. FIND + GORELI ZAMAN
    # =====================================================

    print()
    print("--- Find + goreli zaman ---")

    for number, moment in (
        ("5", "3 hours ago"),
        ("6", "2½ hours ago"),
        ("7", "45 minutes ago"),
    ):
        volatile = volatile_for(
            techmeme_page,
            [STORY_A],
            [STORY_A, "Find", moment],
        )

        check(
            f"{number}. Techmeme: Find + {moment!r} "
            "-> ikisi de elenir",
            normalize_line("Find") in volatile
            and normalize_line(moment) in volatile,
            f"{sorted(volatile)}",
        )

    raw, reduction = reduced(
        techmeme_page,
        [STORY_A],
        [STORY_A, "Find", "3 hours ago"],
    )

    check(
        "5. blok diff'te GORUNMEZ",
        "Find" not in surviving(reduction)
        and "3 hours ago" not in surviving(reduction),
        f"{surviving(reduction)}",
    )

    # ----- GENEL SITE -----

    generic_volatile = volatile_for(
        generic_page,
        [STORY_A],
        [STORY_A, "Find", "3 hours ago"],
    )

    check(
        "8. genel site: Find + goreli zaman ucucu SAYILMAZ",
        generic_volatile == frozenset(),
        f"{generic_volatile}",
    )

    raw, reduction = reduced(
        generic_page,
        [STORY_A],
        [STORY_A, "Find", "3 hours ago"],
    )

    check(
        "8. genel site: blok KORUNUR",
        "Find" in reduction.added_lines
        and "3 hours ago" in reduction.added_lines,
        f"+{reduction.added_lines}",
    )

    # =====================================================
    # 9-11, 14, 19. GERCEK METIN KORUNUR
    # =====================================================

    print()
    print("--- gercek metin korunur ---")

    article = "OpenAI announced this 3 hours ago"

    raw, reduction = reduced(
        techmeme_page,
        [STORY_A],
        [STORY_A, article],
    )

    check(
        "9. makale metni '... 3 hours ago' KORUNUR",
        article in reduction.added_lines,
        "tam satir eslesmedigi icin dokunulmaz",
    )

    # Yapinin disinda tek basina "Find":
    # ardindan goreli zaman GELMIYOR.
    volatile = volatile_for(
        techmeme_page,
        [STORY_A],
        [STORY_A, "Find", "Arama sonuclari"],
    )

    check(
        "10. yapinin disindaki tek 'Find' KORUNUR",
        normalize_line("Find") not in volatile,
        f"{sorted(volatile)}",
    )

    # Ayni sayfada hem yapisal hem yapisal OLMAYAN "Find"
    # varsa metin KORUNMALI (suphede sil-me kurali).
    volatile = volatile_for(
        techmeme_page,
        [STORY_A],
        [
            STORY_A,
            "Find",
            "3 hours ago",
            "Find",
            "Arama sonuclari",
        ],
    )

    check(
        "10. kismi yapida 'Find' KORUNUR",
        normalize_line("Find") not in volatile
        and normalize_line("3 hours ago") in volatile,
        "butun gecisleri yapisal degilse silinmez",
    )

    raw, reduction = reduced(
        techmeme_page,
        [STORY_A],
        [STORY_A, "September 7, 2026"],
    )

    check(
        "11. 'September 7, 2026' KORUNUR",
        "September 7, 2026" in reduction.added_lines,
        "snapshot saati kurali disindaki tarihler dokunulmaz",
    )

    raw, reduction = reduced(
        techmeme_page,
        [STORY_A],
        [STORY_A, STORY_B, PUBLISHER],
    )

    check(
        "12. gercek haber basligi KORUNUR",
        STORY_B in reduction.added_lines,
    )

    check(
        "13. gercek yayinci adi KORUNUR",
        PUBLISHER in reduction.added_lines,
    )

    raw, reduction = reduced(
        techmeme_page,
        [STORY_A],
        [STORY_A, "Expand OpenAI coverage"],
    )

    check(
        "14. 'Expand OpenAI coverage' KORUNUR",
        "Expand OpenAI coverage" in reduction.added_lines,
        "desen TAM SATIR esler",
    )

    check(
        "19. profil yalnizca tam satir esler",
        normalize_line("Find") not in volatile_for(
            techmeme_page,
            [STORY_A],
            [STORY_A, "Findlay / Reuters:", "3 hours ago"],
        ),
        "'Findlay / Reuters:' bir Find satiri DEGILDIR",
    )

    # =====================================================
    # 15. TEKIL BICIMLER
    # =====================================================

    print()
    print("--- tekil bicimler ---")

    for moment in ("1 hour ago", "1 minute ago"):

        volatile = volatile_for(
            techmeme_page,
            [STORY_A],
            [STORY_A, "Find", moment],
        )

        check(
            f"15. Find + {moment!r} elenir",
            normalize_line(moment) in volatile,
            f"{sorted(volatile)}",
        )

    # Gun/hafta gibi bicimler YAKALANMAZ.
    volatile = volatile_for(
        techmeme_page,
        [STORY_A],
        [STORY_A, "Find", "3 days ago"],
    )

    check(
        "15. '3 days ago' YAKALANMAZ",
        normalize_line("3 days ago") not in volatile,
        "yalnizca saat/dakika bicimleri taninir",
    )

    # =====================================================
    # 16-18, 20. MEVCUT DAVRANIS BOZULMADI
    # =====================================================

    print()
    print("--- mevcut davranis regresyonu ---")

    clock_volatile = collect_volatile_lines(
        old_content=techmeme_page([STORY_A]),
        new_content=NEWLINE.join(
            [
                "Open Links In New Tab",
                "September 7, 2026, 5:30 AM",
                TECHMEME_MARKER,
                "Cancel",
                STORY_A,
            ]
        ),
    )

    check(
        "16. snapshot saati kurali BOZULMADI",
        normalize_line("September 7, 2026, 4:00 AM")
        in clock_volatile
        and normalize_line("September 7, 2026, 5:30 AM")
        in clock_volatile,
        f"{sorted(clock_volatile)}",
    )

    raw, reduction = reduced(
        techmeme_page,
        [STORY_A, STORY_B, PUBLISHER],
        [PUBLISHER, STORY_B, STORY_A],
    )

    check(
        "17. tasima iptali davranisi BOZULMADI",
        reduction.is_empty
        and reduction.moved_lines_cancelled > 0,
        f"iptal={reduction.moved_lines_cancelled} "
        f"{surviving(reduction)}",
    )

    reduction = reduce_diff_noise(
        added_lines=["OpenAI", "OpenAI"],
        removed_lines=["OpenAI", "OpenAI", "OpenAI"],
    )

    check(
        "18. multiset davranisi BOZULMADI",
        reduction.removed_lines == ["OpenAI"]
        and reduction.added_lines == [],
        f"-{reduction.removed_lines}",
    )

    volatile = volatile_for(
        techmeme_page,
        [STORY_A],
        [STORY_A, "  Expand   More   For   Next   4  "],
    )

    check(
        "20. ucucu kume NORMALIZE edilmis satir doner",
        "Expand More For Next 4" in volatile,
        f"{sorted(volatile)}",
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
