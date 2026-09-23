"""
DEGISIKLIK TESPITI TESTLERI

SINIF: automated (deterministic, CI-safe)

Kapsam:

     1. ayni metin -> degisiklik YOK
     2. ayni metin -> similarity 1.0
     3. bosluk farki degisiklik SAYILMAZ
     4. satir eklendi -> added_lines
     5. satir silindi -> removed_lines
     6. satir degisti -> hem removed hem added
     7. degismeyen satirlar RAPORLANMAZ
     8. similarity_ratio 0..1 araliginda
     9. buyuk degisiklik -> dusuk similarity
    10. kucuk degisiklik -> yuksek similarity
    11. similarity 4 haneye yuvarlanir
    12. bos -> dolu gecisi
    13. dolu -> bos gecisi
    14. old_text None -> ValueError
    15. new_text None -> ValueError
    16. satir SIRASI korunur
    17. tekrar eden satirlar kaybolmaz
    18. Turkce karakterler bozulmaz
    19. bastaki/sondaki bosluk kirpilir
    20. degismeyen sonuc ek liste URETMEZ

GERCEK GEMINI YOK. GERCEK SMTP YOK. GERCEK AG YOK.
VERITABANI KULLANILMAZ.

Bu dosya eskiden yalnizca ciktiyi ekrana basan bir
gosterim betigiydi; hicbir seyi dogrulamiyordu.
detect_changes() projenin baska HICBIR otomatik testinde
kapsanmadigi icin gercek assertion'lara cevrildi.

Calistirma:
    python -m tests.test_change_detector
"""

from app.services.change_detector import detect_changes


PASSED = []
FAILED = []


def check(label: str, condition: bool, detail: str = "") -> None:

    status = "PASS" if condition else "FAIL"

    print(f"[{status}] {label}" + (f" -> {detail}" if detail else ""))

    if condition:
        PASSED.append(label)
    else:
        FAILED.append(f"{label} -> {detail}")


def raises_value_error(old_text, new_text) -> bool:

    try:
        detect_changes(old_text=old_text, new_text=new_text)

    except ValueError:
        return True

    except Exception:
        return False

    return False


BASE = """Website Monitor AI
Model X supports 128K context window.
API price is 10 USD per million tokens.
Available in Europe."""


def main() -> None:

    print("=" * 70)
    print("DEGISIKLIK TESPITI TESTLERI")
    print("=" * 70)

    # -----------------------------------------------------
    # AYNI METIN
    # -----------------------------------------------------

    same = detect_changes(old_text=BASE, new_text=BASE)

    check(
        "1. ayni metin -> degisiklik YOK",
        same.changed is False,
        f"changed={same.changed}",
    )

    check(
        "2. ayni metin -> similarity 1.0",
        same.similarity_ratio == 1.0,
        f"{same.similarity_ratio}",
    )

    check(
        "20. degismeyen sonuc ek liste URETMEZ",
        same.added_lines == [] and same.removed_lines == [],
        f"+{same.added_lines} -{same.removed_lines}",
    )

    # Bastaki/sondaki bosluk kirpildigi icin degisiklik
    # sayilmaz.
    padded = detect_changes(
        old_text=BASE,
        new_text=f"\n\n  {BASE}  \n\n",
    )

    check(
        "3. bosluk farki degisiklik SAYILMAZ",
        padded.changed is False,
        f"changed={padded.changed}",
    )

    check(
        "19. bastaki/sondaki bosluk kirpilir",
        padded.similarity_ratio == 1.0,
        f"{padded.similarity_ratio}",
    )

    # -----------------------------------------------------
    # EKLEME
    # -----------------------------------------------------

    added = detect_changes(
        old_text=BASE,
        new_text=BASE + "\nNew image support has been added.",
    )

    check(
        "4. satir eklendi -> added_lines",
        added.changed is True
        and added.added_lines
        == ["New image support has been added."],
        f"{added.added_lines}",
    )

    check(
        "7. degismeyen satirlar RAPORLANMAZ (ekleme)",
        added.removed_lines == [],
        f"{added.removed_lines}",
    )

    # -----------------------------------------------------
    # SILME
    # -----------------------------------------------------

    removed = detect_changes(
        old_text=BASE,
        new_text="\n".join(BASE.splitlines()[:-1]),
    )

    check(
        "5. satir silindi -> removed_lines",
        removed.changed is True
        and removed.removed_lines == ["Available in Europe."],
        f"{removed.removed_lines}",
    )

    check(
        "7. degismeyen satirlar RAPORLANMAZ (silme)",
        removed.added_lines == [],
        f"{removed.added_lines}",
    )

    # -----------------------------------------------------
    # DEGISTIRME
    # -----------------------------------------------------

    replaced = detect_changes(
        old_text=BASE,
        new_text=BASE.replace(
            "API price is 10 USD per million tokens.",
            "API price is 8 USD per million tokens.",
        ),
    )

    check(
        "6. satir degisti -> hem removed hem added",
        "API price is 10 USD per million tokens."
        in replaced.removed_lines
        and "API price is 8 USD per million tokens."
        in replaced.added_lines,
        f"-{replaced.removed_lines} +{replaced.added_lines}",
    )

    check(
        "7. degismeyen satirlar RAPORLANMAZ (degistirme)",
        "Website Monitor AI" not in replaced.added_lines
        and "Website Monitor AI"
        not in replaced.removed_lines,
        f"+{replaced.added_lines}",
    )

    # -----------------------------------------------------
    # SIMILARITY
    # -----------------------------------------------------

    check(
        "8. similarity_ratio 0..1 araliginda",
        0.0 <= replaced.similarity_ratio <= 1.0,
        f"{replaced.similarity_ratio}",
    )

    big_change = detect_changes(
        old_text=BASE,
        new_text=(
            "Tamamen farkli bir icerik.\n"
            "Ikinci satir da farkli.\n"
            "Ucuncu satir da farkli.\n"
            "Dorduncu satir da farkli."
        ),
    )

    check(
        "9. buyuk degisiklik -> dusuk similarity",
        big_change.similarity_ratio
        < replaced.similarity_ratio,
        f"{big_change.similarity_ratio} < "
        f"{replaced.similarity_ratio}",
    )

    check(
        "10. kucuk degisiklik -> yuksek similarity",
        replaced.similarity_ratio >= 0.7,
        f"{replaced.similarity_ratio}",
    )

    ratio_text = str(replaced.similarity_ratio)

    decimals = (
        len(ratio_text.split(".")[1])
        if "." in ratio_text
        else 0
    )

    check(
        "11. similarity 4 haneye yuvarlanir",
        decimals <= 4,
        f"{ratio_text}",
    )

    # -----------------------------------------------------
    # SINIR DURUMLARI
    # -----------------------------------------------------

    from_empty = detect_changes(
        old_text="",
        new_text="Ilk icerik geldi.",
    )

    check(
        "12. bos -> dolu gecisi",
        from_empty.changed is True
        and from_empty.added_lines == ["Ilk icerik geldi."],
        f"{from_empty.added_lines}",
    )

    to_empty = detect_changes(
        old_text="Icerik vardi.",
        new_text="",
    )

    check(
        "13. dolu -> bos gecisi",
        to_empty.changed is True
        and to_empty.removed_lines == ["Icerik vardi."],
        f"{to_empty.removed_lines}",
    )

    check(
        "14. old_text None -> ValueError",
        raises_value_error(None, "x"),
    )

    check(
        "15. new_text None -> ValueError",
        raises_value_error("x", None),
    )

    # -----------------------------------------------------
    # SIRA VE TEKRAR
    # -----------------------------------------------------

    ordered = detect_changes(
        old_text="a\nb\nc",
        new_text="a\nb\nc\nX\nY\nZ",
    )

    check(
        "16. satir SIRASI korunur",
        ordered.added_lines == ["X", "Y", "Z"],
        f"{ordered.added_lines}",
    )

    repeated = detect_changes(
        old_text="satir\nsatir",
        new_text="satir\nsatir\nsatir\nsatir",
    )

    check(
        "17. tekrar eden satirlar kaybolmaz",
        len(repeated.added_lines) == 2,
        f"{repeated.added_lines}",
    )

    turkish = detect_changes(
        old_text="Ödeme şartı yazılı olarak bildirilir.",
        new_text="Ödeme şartı biyometrik olarak bildirilir.",
    )

    check(
        "18. Turkce karakterler bozulmaz",
        turkish.added_lines
        == ["Ödeme şartı biyometrik olarak bildirilir."],
        f"{turkish.added_lines}",
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
