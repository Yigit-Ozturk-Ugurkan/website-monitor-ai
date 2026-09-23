"""
TURKCE MEVZUAT PARSER VE ADAY SECIMI TESTLERI

Kapsam:
    7.  article parser MADDE 5
    8.  article parser Madde 12/A
    9.  Gecici Madde parsing
    10. acik madde atifiyla aday secimi
    +   fallback segmentasyon (MADDE basligi yok)
    +   Turkce buyuk I normalizasyonu

Bu test TAMAMEN saf fonksiyon testidir:

  - veritabani KULLANMAZ
  - ag istegi YAPMAZ
  - Gemini CAGIRMAZ

Calistirma:
    python -m tests.test_regulation_parser
"""

from app.services.regulation_candidate_matcher import (
    MAX_CANDIDATES,
    REASON_EXPLICIT_REFERENCE,
    REASON_LEXICAL_OVERLAP,
    select_candidates,
    tokenize,
)
from app.services.regulation_text_parser import (
    build_article_label,
    extract_article_references,
    normalize_article_number,
    parse_regulation_segments,
    turkish_lower,
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


REFERENCE_TEXT = """BİRİNCİ BÖLÜM
Amaç, Kapsam ve Tanımlar
MADDE 1 - Bu Yönetmeliğin amacı elektrik piyasasında faaliyet
gösterecek tüzel kişilerin lisans işlemlerine ilişkin usul ve
esasları düzenlemektir.
MADDE 5 - Başvuru sahipleri gerekli belgeleri Kuruma sunar ve
başvuru dosyası incelenir.
İKİNCİ BÖLÜM
Lisans Başvuruları
MADDE 12 - Lisans başvuruları otuz gün içerisinde değerlendirilir.
MADDE 12/A - Kurum ek belge talep edebilir ve süreyi uzatabilir.
MADDE 20 - Lisans bedelleri her yıl yeniden belirlenir.
GEÇİCİ MADDE 1 - Bu Yönetmeliğin yayımından önceki başvurular
eski hükümlere tabidir.
Geçici Madde 3 - Devam eden işlemler için ek süre tanınabilir.
EK MADDE 2 - İlave hükümler bu madde kapsamında uygulanır.
"""


def segment_by_label(document, label):

    for segment in document.segments:
        if segment.article_label == label:
            return segment

    return None


def main() -> None:

    print("=" * 70)
    print("MEVZUAT PARSER VE ADAY SECIMI TESTLERI")
    print("=" * 70)

    document = parse_regulation_segments(REFERENCE_TEXT)

    labels = [
        segment.article_label
        for segment in document.segments
    ]

    print(f"Bulunan madde sayisi: {document.article_count}")
    print(f"Etiketler           : {labels}")
    print("-" * 70)

    # -----------------------------------------------------
    # TURKCE BUYUK I NORMALIZASYONU
    #
    # Python'un lower() metodu "İ" harfini "i" + birlesik
    # nokta yapar. Bu tuzak mevzuat basliklarinda gercek
    # hataya yol acar.
    # -----------------------------------------------------

    print("\n--- Turkce buyuk harf normalizasyonu ---")

    check(
        "turkish_lower 'GEÇİCİ' -> 'geçici'",
        turkish_lower("GEÇİCİ") == "geçici",
        f"sonuc={turkish_lower('GEÇİCİ')!r}",
    )

    check(
        "python lower() bu isi DOGRU yapamiyor "
        "(neden ozel yardimci gerektigi)",
        "GEÇİCİ".lower() != "geçici",
    )

    check(
        "turkish_lower 'IŞIK' -> 'ışık'",
        turkish_lower("IŞIK") == "ışık",
        f"sonuc={turkish_lower('IŞIK')!r}",
    )

    # -----------------------------------------------------
    # 7. MADDE 5
    # -----------------------------------------------------

    print("\n--- 7. MADDE 5 ---")

    madde_5 = segment_by_label(document, "Madde 5")

    check(
        "7. MADDE 5 segmenti bulundu",
        madde_5 is not None,
    )

    check(
        "7. madde numarasi dogru",
        madde_5 is not None and madde_5.article_number == "5",
        f"numara={madde_5.article_number if madde_5 else None}",
    )

    check(
        "7. segment metni maddenin govdesini iceriyor",
        madde_5 is not None
        and "Başvuru sahipleri" in madde_5.text,
    )

    check(
        "7. segment SONRAKI maddeye tasmiyor",
        madde_5 is not None
        and "otuz gün" not in madde_5.text,
    )

    check(
        "7. bolum basligi tasiniyor",
        madde_5 is not None
        and madde_5.heading == "BİRİNCİ BÖLÜM",
        f"heading={madde_5.heading if madde_5 else None}",
    )

    check(
        "7. atif cozumleme: '5 inci maddesi'",
        [
            (r.article_number, r.is_provisional)
            for r in extract_article_references(
                "Aynı Yönetmeliğin 5 inci maddesi"
            )
        ] == [("5", False)],
        f"{extract_article_references('Aynı Yönetmeliğin 5 inci maddesi')}",
    )

    check(
        "7. atif cozumleme: kesme isaretli '5'inci maddesi'",
        any(
            r.article_number == "5"
            for r in extract_article_references(
                "5'inci maddesi"
            )
        ),
    )

    check(
        "7. atif cozumleme: '12 nci maddesinin'",
        any(
            r.article_number == "12"
            for r in extract_article_references(
                "12 nci maddesinin birinci fıkrası"
            )
        ),
    )

    check(
        "7. atif cozumleme: nokta ile '12. maddesi'",
        any(
            r.article_number == "12"
            for r in extract_article_references(
                "12. maddesi"
            )
        ),
    )

    # -----------------------------------------------------
    # 8. MADDE 12/A
    # -----------------------------------------------------

    print("\n--- 8. Madde 12/A ---")

    madde_12a = segment_by_label(document, "Madde 12/A")

    check(
        "8. Madde 12/A segmenti bulundu",
        madde_12a is not None,
    )

    check(
        "8. bolunmus numara dogru normalize edildi",
        madde_12a is not None
        and madde_12a.article_number == "12/A",
        f"numara={madde_12a.article_number if madde_12a else None}",
    )

    check(
        "8. Madde 12 ile Madde 12/A KARISTIRILMIYOR",
        segment_by_label(document, "Madde 12") is not None
        and madde_12a is not None
        and segment_by_label(
            document, "Madde 12"
        ).segment_id != madde_12a.segment_id,
    )

    check(
        "8. atif cozumleme: '12/A maddesi'",
        any(
            r.article_number == "12/A"
            for r in extract_article_references(
                "12/A maddesi"
            )
        ),
    )

    check(
        "8. numara normalizasyonu bosluklari temizliyor",
        normalize_article_number(" 12 / a ") == "12/A",
        normalize_article_number(" 12 / a "),
    )

    # -----------------------------------------------------
    # 9. GECICI MADDE
    # -----------------------------------------------------

    print("\n--- 9. Gecici Madde ---")

    gecici_1 = segment_by_label(document, "Geçici Madde 1")
    gecici_3 = segment_by_label(document, "Geçici Madde 3")

    check(
        "9. BUYUK harfli 'GEÇİCİ MADDE 1' taniniyor",
        gecici_1 is not None,
    )

    check(
        "9. kucuk harfli 'Geçici Madde 3' taniniyor",
        gecici_3 is not None,
    )

    check(
        "9. gecici madde isaretlenmis",
        gecici_1 is not None and gecici_1.is_provisional is True,
    )

    check(
        "9. Gecici Madde 1 ile normal Madde 1 AYRI",
        gecici_1 is not None
        and segment_by_label(document, "Madde 1") is not None
        and gecici_1.segment_id
        != segment_by_label(document, "Madde 1").segment_id,
    )

    check(
        "9. gecici atif cozumleniyor",
        any(
            r.article_number == "3" and r.is_provisional
            for r in extract_article_references(
                "geçici 3 üncü maddesi"
            )
        ),
        f"{extract_article_references('geçici 3 üncü maddesi')}",
    )

    check(
        "9. gecici atif NORMAL maddeyle eslesmiyor",
        gecici_1 is not None
        and not any(
            r.matches(segment_by_label(document, "Madde 1"))
            for r in extract_article_references(
                "geçici 1 inci maddesi"
            )
        ),
    )

    check(
        "9. EK MADDE de taniniyor",
        segment_by_label(document, "Ek Madde 2") is not None,
    )

    check(
        "9. etiket uretimi tutarli",
        build_article_label("12") == "Madde 12"
        and build_article_label("1", is_provisional=True)
        == "Geçici Madde 1"
        and build_article_label("2", is_additional=True)
        == "Ek Madde 2",
    )

    # -----------------------------------------------------
    # 10. ACIK ATIFLA ADAY SECIMI
    # -----------------------------------------------------

    print("\n--- 10. acik atifla aday secimi ---")

    amendment = (
        "Elektrik Piyasası Lisans Yönetmeliğinde Değişiklik "
        "Yapılmasına Dair Yönetmelik\n"
        "MADDE 1- Aynı Yönetmeliğin 12 nci maddesinde yer alan "
        '"otuz gün" ibaresi "on beş gün" şeklinde '
        "değiştirilmiştir.\n"
    )

    candidates = select_candidates(
        source_text=amendment,
        segments=document.segments,
    )

    check(
        "10. en az bir aday secildi",
        len(candidates) > 0,
    )

    check(
        "10. ILK aday atif yapilan madde (Madde 12)",
        candidates
        and candidates[0].article_label == "Madde 12",
        f"ilk={candidates[0].article_label if candidates else None}",
    )

    check(
        "10. ilk aday acik atif gerekcesiyle secildi",
        candidates
        and candidates[0].reason == REASON_EXPLICIT_REFERENCE,
        f"gerekce={candidates[0].reason if candidates else None}",
    )

    # Degisiklik yonetmeliginin KENDI "MADDE 1-" numarasi
    # hedef yonetmelige yapilan bir atif degildir.
    madde_1_candidates = [
        candidate for candidate in candidates
        if candidate.article_label == "Madde 1"
    ]

    check(
        "10. degisikligin KENDI 'MADDE 1-' numarasi "
        "hedef maddenin onune GECMIYOR",
        not madde_1_candidates
        or candidates.index(madde_1_candidates[0]) > 0,
        f"sira={[c.article_label for c in candidates]}",
    )

    check(
        "10. aday sayisi sinira uyuyor",
        len(candidates) <= MAX_CANDIDATES,
        f"adet={len(candidates)}",
    )

    check(
        "10. secilen adaylar TAM segment metnini tasiyor "
        "(exact dogrulama icin)",
        candidates
        and "otuz gün" in candidates[0].text,
    )

    gecici_amendment = (
        "MADDE 1- Aynı Yönetmeliğin geçici 3 üncü maddesi "
        "yürürlükten kaldırılmıştır."
    )

    gecici_candidates = select_candidates(
        source_text=gecici_amendment,
        segments=document.segments,
    )

    check(
        "10. gecici madde atifi dogru segmenti secti",
        gecici_candidates
        and gecici_candidates[0].article_label
        == "Geçici Madde 3",
        f"ilk={gecici_candidates[0].article_label if gecici_candidates else None}",
    )

    # Atif olmayan metin lexical ortusmeye duser.
    lexical_source = (
        "Lisans bedelleri konusunda yeni bir düzenleme "
        "yapılmıştır."
    )

    lexical_candidates = select_candidates(
        source_text=lexical_source,
        segments=document.segments,
    )

    check(
        "10. acik atif yoksa lexical ortusme calisiyor",
        lexical_candidates
        and lexical_candidates[0].reason
        == REASON_LEXICAL_OVERLAP,
        f"gerekce={lexical_candidates[0].reason if lexical_candidates else None}",
    )

    check(
        "10. lexical ortusme dogru maddeyi buldu (Madde 20)",
        lexical_candidates
        and lexical_candidates[0].article_label == "Madde 20",
        f"ilk={lexical_candidates[0].article_label if lexical_candidates else None}",
    )

    check(
        "10. bos segment listesi bos aday listesi veriyor",
        select_candidates(amendment, []) == [],
    )

    check(
        "10. tokenize durak kelimeleri ayikliyor",
        "madde" not in tokenize("Madde lisans başvuru")
        and "lisans" in tokenize("Madde lisans başvuru"),
        f"{sorted(tokenize('Madde lisans başvuru'))}",
    )

    # -----------------------------------------------------
    # FALLBACK SEGMENTASYON
    # -----------------------------------------------------

    print("\n--- fallback segmentasyon ---")

    plain_text = (
        "Bu belge madde başlığı içermeyen düz bir metindir. "
        "Yine de takip edilebilmelidir. " * 40
    )

    fallback_document = parse_regulation_segments(plain_text)

    check(
        "MADDE basligi yoksa fallback kullaniliyor",
        fallback_document.used_fallback is True,
    )

    check(
        "fallback segment uretti (sistem cokmedi)",
        len(fallback_document.segments) > 0,
        f"adet={len(fallback_document.segments)}",
    )

    check(
        "fallback segmentleri MADDE olarak SUNULMUYOR",
        all(
            segment.is_article is False
            and segment.article_label is None
            and segment.article_number is None
            for segment in fallback_document.segments
        ),
    )

    check(
        "fallback segmentleri de aday olabiliyor",
        len(
            select_candidates(
                source_text="düz bir metindir",
                segments=fallback_document.segments,
            )
        ) > 0,
    )

    check(
        "bos icerik cokme uretmiyor",
        parse_regulation_segments("").segments == []
        and parse_regulation_segments("   ").segments == [],
    )

    check(
        "yalnizca baslik olan metin fallback'e dusuyor "
        "(cok kisa madde govdesi)",
        parse_regulation_segments(
            "MADDE 1\nMADDE 2\n"
        ).used_fallback is True,
    )

    print("\n" + "=" * 70)
    print(f"BASARILI : {len(PASSED)}")
    print(f"BASARISIZ: {len(FAILED)}")

    if FAILED:
        print("-" * 70)
        for line in FAILED:
            print(line)

        raise SystemExit(1)

    print("TUM TESTLER BASARILI")
    print("=" * 70)


if __name__ == "__main__":
    main()
