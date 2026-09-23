"""
TURK MEVZUAT KAYNAGI INGESTION REGRESYONU

Bu test dosyasi GERCEK Resmî Gazete sayfalarinda bulunan
IKI SOMUT HATAYI kalici olarak kilitler.

1. KARAKTER KODLAMASI
   Resmî Gazete belge sayfalari (/eskiler/.../*.htm)
   windows-1254 ile kodlanmis Word ciktisidir ve kodlamayi
   YALNIZCA HTML icindeki meta etiketiyle bildirir; HTTP
   Content-Type basliginda charset YOKTUR.

   httpx bu durumda UTF-8 varsayiyordu ve Turkce harfler
   U+FFFD oluyordu (tek sayfada 21.603 karakter kaybi).
   "GEÇİCİ" kelimesi bozuldugu icin madde basliklari
   yanlis okunuyordu.

2. IKI SATIRA BOLUNMUS MADDE BASLIGI
   Gercek sayfalarda baslik su sekilde bolunebiliyor:

       GEÇİCİ
       MADDE 1 –

   Birlestirilmezse gecici madde, yonetmeligin GERCEK
   Madde 1'i gibi gorunuyordu.

Ayrica sartnamede istenen Turkce mevzuat kaliplari
regresyon olarak dogrulanir.

AG ISTEGI YAPILMAZ. Gercek sayfalardan alinmis kucuk
fixture'lar kullanilir; boylece test ag durumundan
BAGIMSIZDIR ve her zaman ayni sonucu verir.

Gemini CAGRILMAZ. Veritabani KULLANILMAZ.

Calistirma:
    python -m tests.test_turkish_source_ingestion
"""

import codecs

from app.services.content_extractor import extract_content
from app.services.regulation_candidate_matcher import (
    REASON_EXPLICIT_REFERENCE,
    select_candidates,
)
from app.services.regulation_comparator import contains_exact
from app.services.regulation_source_builder import (
    build_comparison_source,
)
from app.services.regulation_text_parser import (
    extract_article_references,
    parse_regulation_segments,
)
from app.services.web_fetcher import (
    decode_html,
    detect_bom_encoding,
    detect_meta_charset,
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


# ---------------------------------------------------------
# FIXTURE
#
# Gercek Resmî Gazete belge sayfasinin yapisi:
#   - Word ciktisi HTML
#   - meta http-equiv ile windows-1254 bildirimi
#   - baslikta charset YOK  (asagida ayrica test edilir)
#   - GEÇİCİ / MADDE basligi iki satira bolunmus
# ---------------------------------------------------------

REAL_PAGE_HTML = """<html xmlns:o="urn:schemas-microsoft-com:office:office">
<head>
<meta http-equiv=content-type content="text/html; charset=windows-1254">
<meta name=progid content=word.document>
<title></title>
</head>
<body>
<p>4 Eylül 2026 CUMA</p>
<p>Resmî Gazete</p>
<p>YÖNETMELİK</p>
<p>Türkiye Cumhuriyet Merkez Bankasından:</p>
<p>ÖDEME HİZMETLERİ HAKKINDA YÖNETMELİKTE DEĞİŞİKLİK
YAPILMASINA DAİR YÖNETMELİK</p>
<p>MADDE 1-</p>
<p>Aynı Yönetmeliğin 41 inci
maddesinin dördüncü fıkrasının ikinci cümlesinde yer alan
&#8220;Uzaktan iletişim aracı ile müşterinin&#8221; ibaresinden sonra
gelmek üzere &#8220;biyometrik yöntemlerle&#8221; ibareleri eklenmiştir.</p>
<p>MADDE 2-</p>
<p>Bu Yönetmelik yayımı tarihinde yürürlüğe girer.</p>
</body>
</html>
"""


# Referans yonetmelik: gercek sayfadaki gibi GEÇİCİ basligi
# iki satira bolunmus.
REFERENCE_HTML = """<html>
<head>
<meta http-equiv=content-type content="text/html; charset=windows-1254">
</head>
<body>
<p>MADDE 40</p>
<p>(1) Ödeme hizmeti sağlayıcıları kayıtlarını saklar ve
gerektiğinde Bankaya sunar.</p>
<p>MADDE 41</p>
<p>(1) Uzaktan iletişim aracı ile müşterinin kimliğinin
doğrulanması Bankaca belirlenen usullere göre yapılır.</p>
<p>MADDE 42</p>
<p>(1) Ödeme kuruluşları asgari özkaynak yükümlülüğünü
sürekli olarak sağlamakla yükümlüdür.</p>
<p>Geçiş</p>
<p>hükümleri</p>
<p>GEÇİCİ</p>
<p>MADDE 1 &#8211;</p>
<p>(1) Bu Yönetmeliğin yürürlüğe girdiği tarih itibarıyla
faaliyette bulunan kuruluşlar bir yıl içerisinde uyum
sağlamakla yükümlüdür.</p>
<p>MADDE 43</p>
<p>(1) Bu Yönetmelik yayımı tarihinde yürürlüğe girer.</p>
</body>
</html>
"""


def as_windows_1254(html: str) -> bytes:
    """
    Fixture'i GERCEK sayfalardaki gibi windows-1254 ile
    kodlar.
    """

    return html.encode("cp1254")


def segment_by_label(document, label):

    for segment in document.segments:
        if segment.article_label == label:
            return segment

    return None


def main() -> None:

    print("=" * 70)
    print("TURK MEVZUAT KAYNAGI INGESTION REGRESYONU")
    print("=" * 70)

    raw = as_windows_1254(REAL_PAGE_HTML)

    # -----------------------------------------------------
    # 1. KODLAMA TESPITI
    # -----------------------------------------------------

    print("\n--- 1. kodlama tespiti ---")

    check(
        "1. meta etiketindeki charset okunuyor",
        detect_meta_charset(raw) == "cp1254",
        f"bulunan={detect_meta_charset(raw)}",
    )

    check(
        "1. <meta charset=\"utf-8\"> bicimi de okunuyor",
        detect_meta_charset(
            b'<html><head><meta charset="utf-8"></head>'
        ) == "utf-8",
    )

    check(
        "1. charset bildirimi yoksa None doner",
        detect_meta_charset(
            b"<html><head><title>x</title></head>"
        ) is None,
    )

    check(
        "1. uydurma codec adi kabul edilmiyor",
        detect_meta_charset(
            b'<meta charset="uydurma-kodlama-9">'
        ) is None,
    )

    check(
        "1. BOM tespit ediliyor",
        detect_bom_encoding(
            codecs.BOM_UTF8 + b"<html>"
        ) == "utf-8-sig"
        and detect_bom_encoding(b"<html>") is None,
    )

    # -----------------------------------------------------
    # 2. KODLAMA DUZELTMESI
    #
    # ASIL REGRESYON: baslikta charset YOKKEN meta
    # etiketine bakilmali.
    # -----------------------------------------------------

    print("\n--- 2. kodlama duzeltmesi ---")

    text, encoding = decode_html(raw, header_charset=None)

    check(
        "2. baslikta charset yokken meta kullaniliyor",
        encoding == "cp1254",
        f"kullanilan={encoding}",
    )

    check(
        "2. cozumlemede U+FFFD URETILMIYOR",
        text.count("�") == 0,
        f"U+FFFD sayisi={text.count(chr(0xFFFD))}",
    )

    check(
        "2. Turkce harfler korunuyor",
        "DEĞİŞİKLİK" in text
        and "eklenmiştir" in text
        and "müşterinin" in text,
    )

    # Duzeltmeden ONCEKI hatali davranis.
    broken = raw.decode("utf-8", errors="replace")

    check(
        "2. eski davranis (UTF-8 varsayimi) GERCEKTEN "
        "bozuyordu",
        broken.count("�") > 0
        and "DEĞİŞİKLİK" not in broken,
        f"eski U+FFFD sayisi={broken.count(chr(0xFFFD))}",
    )

    # -----------------------------------------------------
    # 3. KODLAMA ONCELIK SIRASI
    # -----------------------------------------------------

    print("\n--- 3. kodlama oncelik sirasi ---")

    utf8_page = (
        '<html><head><meta charset="utf-8"></head>'
        "<body>GEÇİCİ MADDE 1</body></html>"
    ).encode("utf-8")

    check(
        "3. HTTP basligi bildirimi onceliklidir",
        decode_html(
            utf8_page,
            header_charset="utf-8",
        )[1] == "utf-8",
    )

    check(
        "3. UTF-8 sayfa dogru cozumleniyor",
        "GEÇİCİ MADDE 1"
        in decode_html(utf8_page, header_charset="utf-8")[0],
    )

    check(
        "3. BOM her seyin onunde gelir",
        decode_html(
            codecs.BOM_UTF8 + "MADDE 1".encode("utf-8"),
            header_charset="cp1254",
        )[1] == "utf-8-sig",
    )

    # Baslik YANLIS bildirirse kati cozumleme kurtarir.
    check(
        "3. yanlis baslik bildirimi cozumlemeyi "
        "COKERTMIYOR",
        decode_html(
            "MADDE 1 – Türkçe içerik".encode("utf-8"),
            header_charset="utf-16",
        )[0] is not None,
    )

    check(
        "3. bos govde cokme uretmiyor",
        decode_html(b"", header_charset=None) == ("", "utf-8"),
    )

    # -----------------------------------------------------
    # 4. IKI SATIRA BOLUNMUS GECICI MADDE BASLIGI
    # -----------------------------------------------------

    print("\n--- 4. bolunmus GECICI MADDE basligi ---")

    reference_text, _ = decode_html(
        as_windows_1254(REFERENCE_HTML),
        header_charset=None,
    )

    reference_extracted = extract_content(reference_text)

    document = parse_regulation_segments(
        reference_extracted.text
    )

    labels = [
        segment.article_label
        for segment in document.segments
    ]

    print(f"    bulunan etiketler: {labels}")

    check(
        "4. fallback'e dusulmedi",
        document.used_fallback is False,
    )

    gecici = segment_by_label(document, "Geçici Madde 1")

    check(
        "4. iki satira bolunmus GEÇİCİ MADDE 1 taniniyor",
        gecici is not None,
        f"etiketler={labels}",
    )

    check(
        "4. gecici olarak isaretlendi",
        gecici is not None and gecici.is_provisional is True,
    )

    check(
        "4. gecici madde NORMAL 'Madde 1' olarak "
        "ETIKETLENMIYOR",
        "Madde 1" not in labels,
        f"etiketler={labels}",
    )

    check(
        "4. gecici madde govdesi dogru segmentte",
        gecici is not None
        and "faaliyette bulunan kuruluşlar" in gecici.text,
    )

    check(
        "4. onceki madde gecici govdesini YUTMADI",
        segment_by_label(document, "Madde 42") is not None
        and "faaliyette bulunan kuruluşlar"
        not in segment_by_label(document, "Madde 42").text,
    )

    check(
        "4. normal maddeler bozulmadi",
        all(
            segment_by_label(document, label) is not None
            for label in (
                "Madde 40",
                "Madde 41",
                "Madde 42",
                "Madde 43",
            )
        ),
        f"etiketler={labels}",
    )

    # -----------------------------------------------------
    # 5. TURKCE MEVZUAT KALIPLARI (sartname listesi)
    # -----------------------------------------------------

    print("\n--- 5. Turkce mevzuat kaliplari ---")

    patterns = [
        (
            "Aynı Yönetmeliğin 12 nci maddesi",
            "12",
            False,
        ),
        (
            "12 nci maddesinin üçüncü fıkrası",
            "12",
            False,
        ),
        (
            "5 inci maddesinde yer alan “otuz gün” ibaresi "
            "“on beş gün” olarak değiştirilmiştir.",
            "5",
            False,
        ),
        (
            "13 üncü maddesine aşağıdaki fıkra eklenmiştir.",
            "13",
            False,
        ),
        (
            "Geçici 3 üncü maddesi yürürlükten kaldırılmıştır.",
            "3",
            True,
        ),
        (
            "41 inci maddesinin dördüncü fıkrasının ikinci "
            "cümlesinde yer alan ibare",
            "41",
            False,
        ),
    ]

    for text_pattern, expected_number, expected_provisional in patterns:

        references = extract_article_references(text_pattern)

        matched = [
            reference
            for reference in references
            if reference.article_number == expected_number
            and reference.is_provisional == expected_provisional
            and not reference.is_own_numbering
        ]

        check(
            f"5. kalip cozumlendi: {text_pattern[:44]}...",
            bool(matched),
            f"bulunan={[(r.article_number, r.is_provisional, r.kind) for r in references]}",
        )

    # -----------------------------------------------------
    # 6. KENDI NUMARASI vs HEDEF MADDE
    # -----------------------------------------------------

    print("\n--- 6. kendi numarasi ayrimi ---")

    source_text, _ = decode_html(raw, header_charset=None)

    source_extracted = extract_content(source_text)

    comparison_source = build_comparison_source(
        change=type(
            "Change",
            (),
            {
                "added_content": source_extracted.text,
                "removed_content": None,
            },
        )()
    )

    references = extract_article_references(
        comparison_source.text
    )

    own = [r for r in references if r.is_own_numbering]
    cross = [r for r in references if not r.is_own_numbering]

    print(
        f"    kendi numarasi: "
        f"{[r.article_number for r in own]}"
    )
    print(
        f"    hedef atif    : "
        f"{[r.article_number for r in cross]}"
    )

    check(
        "6. degisikligin KENDI 'MADDE 1-' numarasi "
        "kendi numarasi olarak isaretlendi",
        any(
            r.article_number == "1" and r.is_own_numbering
            for r in references
        ),
    )

    check(
        "6. hedef yonetmelige atif (41) cekimli olarak "
        "taniniyor",
        any(
            r.article_number == "41"
            and not r.is_own_numbering
            and r.kind == "inline"
            for r in references
        ),
    )

    candidates = select_candidates(
        source_text=comparison_source.text,
        segments=document.segments,
    )

    print(
        f"    adaylar: "
        f"{[(c.article_label, c.reason) for c in candidates]}"
    )

    check(
        "6. ILK aday atif yapilan madde (Madde 41)",
        candidates
        and candidates[0].article_label == "Madde 41",
        f"ilk={candidates[0].article_label if candidates else None}",
    )

    check(
        "6. ilk aday acik atifla secildi",
        candidates
        and candidates[0].reason == REASON_EXPLICIT_REFERENCE,
    )

    check(
        "6. gecici madde acik atif gibi ONE CIKARILMADI",
        all(
            candidate.article_label != "Geçici Madde 1"
            or candidate.reason != REASON_EXPLICIT_REFERENCE
            for candidate in candidates
        ),
    )

    # -----------------------------------------------------
    # 7. EXACT EVIDENCE GUVENLIGI KORUNUYOR
    # -----------------------------------------------------

    print("\n--- 7. exact evidence guvenligi ---")

    check(
        "7. kaynaktan BIREBIR alinti dogrulaniyor",
        contains_exact(
            comparison_source.text,
            "ibareleri eklenmiştir",
        ),
    )

    check(
        "7. Turkce karakterli alinti dogrulaniyor",
        contains_exact(
            comparison_source.text,
            "biyometrik yöntemlerle",
        ),
    )

    check(
        "7. satira bolunmus alinti dogrulaniyor "
        "(bosluk normalizasyonu)",
        contains_exact(
            comparison_source.text,
            "41 inci maddesinin dördüncü fıkrasının",
        ),
    )

    check(
        "7. UYDURMA alinti REDDEDILIYOR",
        not contains_exact(
            comparison_source.text,
            "kırk beş gün içerisinde değerlendirilir",
        ),
    )

    check(
        "7. ANLAMCA benzer ama farkli metin REDDEDILIYOR",
        not contains_exact(
            comparison_source.text,
            "biyometrik yontemlerle",
        ),
        "Turkce harf farki exact eslesmeyi gecersiz kilmali",
    )

    check(
        "7. referans alintisi REFERANS metninde "
        "dogrulaniyor",
        gecici is not None
        and contains_exact(
            gecici.text,
            "faaliyette bulunan kuruluşlar",
        ),
    )

    check(
        "7. referans alintisi KAYNAK metninde "
        "bulunmuyor (kaynaklar ayri)",
        not contains_exact(
            comparison_source.text,
            "faaliyette bulunan kuruluşlar",
        ),
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
