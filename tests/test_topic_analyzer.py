"""
KONU ILGILILIK TESTLERI

SINIF: automated (deterministic, CI-safe)

Kapsam:

     1. konuyla ilgili degisiklik -> relevant
     2. konuyla ilgisiz degisiklik -> relevant DEGIL
     3. eslesen anahtar kelimeler raporlanir
     4. eslesmeyen kelime raporlanmaz
     5. relevance_score = eslesen / toplam
     6. tam eslesme -> score 1.0
     7. hic eslesme yok -> score 0.0
     8. score 4 haneye yuvarlanir
     9. buyuk/kucuk harf duyarsiz
    10. Turkce karakterler korunur
    11. noktalama anahtar kelimeyi bozmaz
    12. stop word anahtar kelime SAYILMAZ
    13. 3 harften kisa kelime elenir
    14. tekrar eden konu kelimesi bir kez sayilir
    15. removed_lines de taranir
    16. removed_lines varsayilan olarak bos
    17. bos konu -> ValueError
    18. yalnizca bosluk iceren konu -> ValueError
    19. yalnizca stop word iceren konu -> relevant DEGIL
    20. bos degisiklik listesi -> relevant DEGIL
    21. anahtar kelime kismi kelime icinde de eslesir

GERCEK GEMINI YOK. GERCEK SMTP YOK. GERCEK AG YOK.
VERITABANI KULLANILMAZ.

Bu dosya eskiden yalnizca ciktiyi ekrana basan bir
gosterim betigiydi; hicbir seyi dogrulamiyordu.
analyze_topic_relevance() projenin baska HICBIR otomatik
testinde kapsanmadigi icin gercek assertion'lara
cevrildi.

Calistirma:
    python -m tests.test_topic_analyzer
"""

from app.services.topic_analyzer import analyze_topic_relevance


PASSED = []
FAILED = []


def check(label: str, condition: bool, detail: str = "") -> None:

    status = "PASS" if condition else "FAIL"

    print(f"[{status}] {label}" + (f" -> {detail}" if detail else ""))

    if condition:
        PASSED.append(label)
    else:
        FAILED.append(f"{label} -> {detail}")


def raises_value_error(topic) -> bool:

    try:
        analyze_topic_relevance(
            topic=topic,
            added_lines=["herhangi bir satir"],
        )

    except ValueError:
        return True

    except Exception:
        return False

    return False


TOPIC = "Yapay zeka modeli ve API güncellemeleri"


def main() -> None:

    print("=" * 70)
    print("KONU ILGILILIK TESTLERI")
    print("=" * 70)

    # -----------------------------------------------------
    # TEMEL DAVRANIS
    # -----------------------------------------------------

    relevant = analyze_topic_relevance(
        topic=TOPIC,
        added_lines=[
            "Model X API fiyatı 8 dolardan 6 dolara düşürüldü.",
            "Yeni yapay zeka modeli kullanıma sunuldu.",
        ],
    )

    check(
        "1. konuyla ilgili degisiklik -> relevant",
        relevant.relevant is True,
        f"score={relevant.relevance_score}",
    )

    irrelevant = analyze_topic_relevance(
        topic=TOPIC,
        added_lines=[
            "Şirket iletişim telefonu değiştirildi.",
            "İstanbul ofisinin adresi güncellendi.",
        ],
    )

    check(
        "2. konuyla ilgisiz degisiklik -> relevant DEGIL",
        irrelevant.relevant is False,
        f"matched={irrelevant.matched_keywords}",
    )

    check(
        "3. eslesen anahtar kelimeler raporlanir",
        "yapay" in relevant.matched_keywords
        and "zeka" in relevant.matched_keywords
        and "modeli" in relevant.matched_keywords,
        f"{relevant.matched_keywords}",
    )

    check(
        "4. eslesmeyen kelime raporlanmaz",
        "güncellemeleri" not in relevant.matched_keywords,
        f"{relevant.matched_keywords}",
    )

    # -----------------------------------------------------
    # SKOR
    # -----------------------------------------------------

    expected_score = round(
        len(relevant.matched_keywords)
        / len(relevant.topic_keywords),
        4,
    )

    check(
        "5. relevance_score = eslesen / toplam",
        relevant.relevance_score == expected_score,
        f"{relevant.relevance_score} == {expected_score}",
    )

    full = analyze_topic_relevance(
        topic="mevzuat değişikliği",
        added_lines=["Mevzuat değişikliği yayımlandı."],
    )

    check(
        "6. tam eslesme -> score 1.0",
        full.relevance_score == 1.0,
        f"{full.relevance_score} {full.matched_keywords}",
    )

    check(
        "7. hic eslesme yok -> score 0.0",
        irrelevant.relevance_score == 0.0,
        f"{irrelevant.relevance_score}",
    )

    partial = analyze_topic_relevance(
        topic="alfa beta gama",
        added_lines=["Yalnizca alfa gecti."],
    )

    score_text = str(partial.relevance_score)

    decimals = (
        len(score_text.split(".")[1])
        if "." in score_text
        else 0
    )

    check(
        "8. score 4 haneye yuvarlanir",
        decimals <= 4 and partial.relevance_score == 0.3333,
        f"{score_text}",
    )

    # -----------------------------------------------------
    # NORMALIZASYON
    # -----------------------------------------------------

    upper = analyze_topic_relevance(
        topic="MEVZUAT DEĞİŞİKLİĞİ",
        added_lines=["mevzuat değişikliği yayımlandı"],
    )

    check(
        "9. buyuk/kucuk harf duyarsiz",
        upper.relevant is True
        and upper.relevance_score == 1.0,
        f"{upper.matched_keywords}",
    )

    check(
        "10. Turkce karakterler korunur",
        "değişikliği" in upper.topic_keywords,
        f"{upper.topic_keywords}",
    )

    punctuated = analyze_topic_relevance(
        topic="ödeme, hizmetleri; yönetmelik!",
        added_lines=["Ödeme hizmetleri yönetmelik değişti."],
    )

    check(
        "11. noktalama anahtar kelimeyi bozmaz",
        punctuated.topic_keywords
        == ["ödeme", "hizmetleri", "yönetmelik"]
        and punctuated.relevance_score == 1.0,
        f"{punctuated.topic_keywords}",
    )

    # -----------------------------------------------------
    # ANAHTAR KELIME CIKARIMI
    # -----------------------------------------------------

    check(
        "12. stop word anahtar kelime SAYILMAZ",
        "ve" not in relevant.topic_keywords,
        f"{relevant.topic_keywords}",
    )

    short = analyze_topic_relevance(
        topic="ab abc abcd",
        added_lines=["abc"],
    )

    check(
        "13. 3 harften kisa kelime elenir",
        short.topic_keywords == ["abc", "abcd"],
        f"{short.topic_keywords}",
    )

    duplicated = analyze_topic_relevance(
        topic="mevzuat mevzuat mevzuat",
        added_lines=["mevzuat"],
    )

    check(
        "14. tekrar eden konu kelimesi bir kez sayilir",
        duplicated.topic_keywords == ["mevzuat"],
        f"{duplicated.topic_keywords}",
    )

    # -----------------------------------------------------
    # SILINEN SATIRLAR
    # -----------------------------------------------------

    from_removed = analyze_topic_relevance(
        topic="mevzuat değişikliği",
        added_lines=[],
        removed_lines=["Eski mevzuat değişikliği kaldırıldı."],
    )

    check(
        "15. removed_lines de taranir",
        from_removed.relevant is True,
        f"{from_removed.matched_keywords}",
    )

    only_added = analyze_topic_relevance(
        topic="mevzuat değişikliği",
        added_lines=["Mevzuat değişikliği var."],
    )

    check(
        "16. removed_lines varsayilan olarak bos",
        only_added.relevant is True
        and only_added.relevance_score == 1.0,
        f"{only_added.matched_keywords}",
    )

    # -----------------------------------------------------
    # SINIR DURUMLARI
    # -----------------------------------------------------

    check(
        "17. bos konu -> ValueError",
        raises_value_error(""),
    )

    check(
        "18. yalnizca bosluk iceren konu -> ValueError",
        raises_value_error("   \n\t "),
    )

    stop_only = analyze_topic_relevance(
        topic="ve veya ile",
        added_lines=["ve veya ile"],
    )

    check(
        "19. yalnizca stop word iceren konu -> relevant DEGIL",
        stop_only.relevant is False
        and stop_only.topic_keywords == []
        and stop_only.relevance_score == 0.0,
        f"{stop_only.topic_keywords}",
    )

    empty_change = analyze_topic_relevance(
        topic=TOPIC,
        added_lines=[],
    )

    check(
        "20. bos degisiklik listesi -> relevant DEGIL",
        empty_change.relevant is False
        and empty_change.relevance_score == 0.0,
        f"{empty_change.matched_keywords}",
    )

    # Eslesme SUBSTRING uzerinden yapilir: "model" kelimesi
    # "modeller" icinde de yakalanir. Bu bilincli bir
    # gevsekliktir; kesin karari Gemini analizi verir.
    substring = analyze_topic_relevance(
        topic="model",
        added_lines=["Yeni modeller yayımlandı."],
    )

    check(
        "21. anahtar kelime kismi kelime icinde de eslesir",
        substring.relevant is True,
        f"{substring.matched_keywords}",
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
