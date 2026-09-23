"""
GERCEK TURK MEVZUAT KAYNAGI KABUL TESTI

Bu betik GERCEK bir adrese baglanir. Claude ortaminda degil,
KULLANICININ KENDI MAKINESINDE calistirilmak uzere yazildi.

KULLANIM

    python -m tests.acceptance_turkish_regulation_source

    python -m tests.acceptance_turkish_regulation_source \\
        --url "https://www.resmigazete.gov.tr/eskiler/2026/09/20260904-1.htm"

    # Referans yonetmelikle karsilastirma (yerel, AI'siz):
    python -m tests.acceptance_turkish_regulation_source \\
        --url "<degisiklik-url>" \\
        --reference-url "<referans-yonetmelik-url>"

    # GERCEK Gemini karsilastirmasi (EN FAZLA 1 cagri):
    python -m tests.acceptance_turkish_regulation_source \\
        --url "<degisiklik-url>" \\
        --reference-url "<referans-url>" \\
        --compare

NE YAPAR

    1. gercek URL fetch
    2. content-type raporu
    3. cikarilan metin uzunlugu
    4. MADDE segment sayisi
    5. ilk 5 madde etiketi
    6. degisiklik kaynak metni cikarimi
    7. aday referans madde eslesmesi
    8. (opsiyonel) gercek Gemini karsilastirmasi

GUVENLIK

    - SIR YAZMAZ. API anahtari, SMTP bilgisi, parola
      hicbir ciktiya girmez.
    - Sertifika dogrulamasi ACIK kalir.
    - VERITABANINA HICBIR SEY YAZMAZ.
    - --compare verilmedikce Gemini CAGRILMAZ.
    - SMTP hicbir durumda cagrilmaz.
"""

import argparse
import asyncio
import ssl
import sys

from dataclasses import dataclass

from app.services.content_extractor import extract_content
from app.services.regulation_candidate_matcher import (
    MAX_CANDIDATES,
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
    WebsiteFetchError,
    fetch_website,
)


# Varsayilan hedef: Resmî Gazete gunluk yayin listesi.
# Bu sayfa monitorun izledigi yuzeydir.
DEFAULT_URL = "https://www.resmigazete.gov.tr/"


def line(char: str = "-") -> None:
    print(char * 72)


def heading(title: str) -> None:
    print()
    line("=")
    print(title)
    line("=")


@dataclass
class FetchReport:
    url: str
    ok: bool
    detail: str = ""

    content_type: str = ""
    final_url: str = ""
    title: str = ""
    text: str = ""

    @property
    def text_length(self) -> int:
        return len(self.text)


async def fetch_and_extract(url: str, label: str) -> FetchReport:
    """
    Uretimde kullanilan AYNI fetch/extraction yolunu kullanir.

    Yeni bir indirme yolu acilmaz; boylece bu betigin
    sonucu uygulamanin gercek davranisini temsil eder.
    """

    print(f"\n{label}")
    print(f"  {url}")

    try:
        result = await fetch_website(url)

    except WebsiteFetchError as exc:
        print(f"  SONUC       : BASARISIZ")
        print(f"  HATA        : {exc}")

        return FetchReport(url=url, ok=False, detail=str(exc))

    except ValueError as exc:
        print(f"  SONUC       : GECERSIZ URL")
        print(f"  HATA        : {exc}")

        return FetchReport(url=url, ok=False, detail=str(exc))

    try:
        extracted = extract_content(result.html)

    except ValueError as exc:
        print(f"  SONUC       : ICERIK OKUNAMADI")
        print(f"  HATA        : {exc}")

        return FetchReport(url=url, ok=False, detail=str(exc))

    print(f"  SONUC       : BASARILI")
    print(f"  status      : {result.status_code}")
    print(f"  content-type: {result.content_type}")
    print(f"  final url   : {result.final_url}")
    print(f"  sayfa basligi: {extracted.title[:70] or '(yok)'}")
    print(f"  metin uzunlugu: {extracted.text_length} karakter")

    return FetchReport(
        url=url,
        ok=True,
        content_type=result.content_type,
        final_url=result.final_url,
        title=extracted.title,
        text=extracted.text,
    )


def report_segments(text: str, label: str):
    """
    Metni MADDE segmentlerine ayirir ve ozetler.
    """

    document = parse_regulation_segments(text)

    print(f"\n  {label}")
    print(f"    fallback kullanildi : {document.used_fallback}")
    print(f"    MADDE segment sayisi: {document.article_count}")
    print(f"    toplam segment      : {len(document.segments)}")

    if document.used_fallback:
        print(
            "    NOT: MADDE basligi bulunamadi. Segmentler "
            "madde olarak SUNULMAZ."
        )

    print(f"    ilk 5 madde etiketi:")

    for segment in document.segments[:5]:
        label_text = segment.article_label or "(madde degil)"
        print(
            f"      - {label_text:22s} "
            f"{segment.text_length:6d} karakter"
        )

    return document


class SyntheticChange:
    """
    Change kaydinin kaynak kurucu icin gereken alanlari.

    VERITABANI KULLANILMAZ; bu yalnizca gercek metin
    uzerinde kaynak cikarimini gostermek icindir.
    """

    def __init__(self, added_content, removed_content=None):
        self.added_content = added_content
        self.removed_content = removed_content


async def run(args) -> int:

    problems: list[str] = []

    heading("1-5. KAYNAK ERISIMI VE METIN CIKARIMI")

    print(f"OpenSSL       : {ssl.OPENSSL_VERSION}")

    from app.core.ssl_context import collect_extra_ca_sources

    extra = collect_extra_ca_sources()

    print(f"ek CA dosyasi : {len(extra)}")

    for path in extra:
        print(f"  - {path.name}")

    print(
        "sertifika dogrulamasi: ACIK "
        "(kapatma secenegi yok)"
    )

    source_report = await fetch_and_extract(
        args.url,
        "DEGISIKLIK / IZLENEN KAYNAK",
    )

    if not source_report.ok:
        problems.append(
            f"kaynak erisilemedi: {source_report.detail}"
        )

        heading("SONUC")
        print("TURKISH_SOURCE_ACCEPTANCE: NOT VERIFIED")
        print()

        for problem in problems:
            print(f"  - {problem}")

        return 1

    source_document = report_segments(
        source_report.text,
        "KAYNAK METIN SEGMENTASYONU",
    )

    if source_report.text_length < 200:
        problems.append(
            "cikarilan metin cok kisa "
            f"({source_report.text_length} karakter)"
        )

    # -----------------------------------------------------
    # 6. KARSILASTIRMA KAYNAK METNI
    # -----------------------------------------------------

    heading("6. KARSILASTIRMA KAYNAK METNI")

    # Gercek bir Change yok; sayfanin tamami "eklenen
    # icerik" gibi ele alinir. Bu, monitorun ilk kez bu
    # yayini gordugu duruma karsilik gelir.
    change = SyntheticChange(
        added_content=source_report.text,
    )

    comparison_source = build_comparison_source(change=change)

    print(f"  kaynak uzunlugu : {comparison_source.length}")
    print(f"  kullanilan bolum: {comparison_source.sections}")
    print(f"  kesildi mi      : {comparison_source.truncated}")

    references = extract_article_references(
        comparison_source.text
    )

    print(f"\n  BULUNAN MADDE ATIFLARI: {len(references)}")

    for reference in references[:12]:
        kind = (
            "kendi numarasi"
            if reference.is_own_numbering
            else reference.kind
        )

        provisional = " (gecici)" if reference.is_provisional else ""

        print(
            f"    - Madde {reference.article_number}"
            f"{provisional:10s} [{kind}]"
        )

    cross_references = [
        reference
        for reference in references
        if not reference.is_own_numbering
    ]

    print(
        f"\n  hedef yonetmelige atif sayisi: "
        f"{len(cross_references)}"
    )

    if references and not cross_references:
        print(
            "  NOT: yalnizca degisikligin KENDI madde "
            "numaralari bulundu. Bu sayfa bir degisiklik "
            "metni olmayabilir (ornegin gunluk yayin listesi)."
        )

    # -----------------------------------------------------
    # 7. REFERANS ADAY ESLESMESI
    # -----------------------------------------------------

    reference_report = None
    candidates = []

    if args.reference_url:

        heading("7. REFERANS YONETMELIK VE ADAY ESLESMESI")

        reference_report = await fetch_and_extract(
            args.reference_url,
            "REFERANS YONETMELIK",
        )

        if not reference_report.ok:
            problems.append(
                "referans yonetmelik erisilemedi: "
                f"{reference_report.detail}"
            )

        else:
            reference_document = report_segments(
                reference_report.text,
                "REFERANS SEGMENTASYONU",
            )

            candidates = select_candidates(
                source_text=comparison_source.text,
                segments=reference_document.segments,
                max_candidates=MAX_CANDIDATES,
            )

            print(f"\n  SECILEN ADAYLAR: {len(candidates)}")

            for candidate in candidates:
                print(
                    f"    {candidate.candidate_id:10s} "
                    f"{str(candidate.article_label):22s} "
                    f"{candidate.reason:20s} "
                    f"skor={candidate.score:.3f}"
                )

            if not candidates:
                print(
                    "\n  Aday bulunamadi. Gercek calistirmada "
                    "bu durum no_match olarak raporlanir; "
                    "uydurma eslesme URETILMEZ."
                )

    else:
        heading("7. REFERANS ADAY ESLESMESI")
        print(
            "  --reference-url verilmedi, bu adim atlandi.\n"
            "  Aday eslesmesini gormek icin referans "
            "yonetmeligin HTML adresini verin."
        )

    # -----------------------------------------------------
    # 8. OPSIYONEL GERCEK GEMINI KARSILASTIRMASI
    # -----------------------------------------------------

    heading("8. MEVZUAT KARSILASTIRMASI")

    if not args.compare:
        print(
            "  --compare verilmedi.\n"
            "  GERCEK GEMINI CAGRISI YAPILMADI."
        )

    elif not candidates:
        print(
            "  Aday madde bulunamadigi icin karsilastirma "
            "calistirilmadi.\n"
            "  GERCEK GEMINI CAGRISI YAPILMADI."
        )

    else:
        print(
            "  UYARI: bu adim GERCEK bir Gemini cagrisi "
            "yapar (en fazla 1 mantiksal cagri).\n"
        )

        from app.services.regulation_comparator import (
            RegulationComparisonError,
            compare_regulation_change,
        )

        try:
            outcome = await compare_regulation_change(
                topic=args.topic,
                regulation_title=(
                    reference_report.title
                    or "Referans Yönetmelik"
                ),
                source_text=comparison_source.text,
                candidates=candidates,
            )

        except (RegulationComparisonError, ValueError) as exc:
            print(f"  KARSILASTIRMA BASARISIZ: {exc}")
            problems.append(f"karsilastirma hatasi: {exc}")

        else:
            print(f"  model            : {outcome.model_name}")
            print(
                f"  yedek model mi   : {outcome.used_fallback}"
            )
            print(
                f"  degisiklik var mi: "
                f"{outcome.has_regulatory_change}"
            )
            print(f"  ozet             : {outcome.overall_summary}")
            print(f"  onem             : {outcome.importance}")
            print(
                f"  dogrulanan item  : {len(outcome.items)}"
            )
            print(
                f"  reddedilen item  : "
                f"{outcome.rejected_item_count}"
            )

            if not outcome.items:
                print(
                    "\n  Dogrulanmis madde yok. Gercek "
                    "calistirmada bu no_match olarak "
                    "kaydedilir."
                )

            for item in outcome.items:
                line()
                print(f"  madde     : {item.affected_article}")
                print(f"  fikra     : {item.affected_paragraph}")
                print(f"  tur       : {item.change_type}")
                print(f"  eski metin: {item.old_text!r}")
                print(f"  yeni metin: {item.new_text!r}")
                print(f"  ozet      : {item.change_summary}")
                print(f"  guven     : {item.confidence}")

                # BAGIMSIZ exact dogrulama.
                # Servis zaten dogruluyor; burada
                # kullaniciya KANITI gosteriyoruz.
                checks = [
                    (
                        "source_evidence kaynakta birebir",
                        contains_exact(
                            comparison_source.text,
                            item.source_evidence,
                        ),
                    ),
                    (
                        "new_text kaynakta birebir",
                        item.new_text is None
                        or contains_exact(
                            comparison_source.text,
                            item.new_text,
                        ),
                    ),
                    (
                        "old_text referansta birebir",
                        item.old_text is None
                        or contains_exact(
                            item.candidate.text,
                            item.old_text,
                        ),
                    ),
                    (
                        "reference_evidence referansta birebir",
                        item.reference_evidence is None
                        or contains_exact(
                            item.candidate.text,
                            item.reference_evidence,
                        ),
                    ),
                ]

                for label, ok in checks:
                    print(
                        f"    [{'OK ' if ok else 'HATA'}] {label}"
                    )

                    if not ok:
                        problems.append(
                            f"exact dogrulama basarisiz: {label}"
                        )

    # -----------------------------------------------------
    # SONUC
    # -----------------------------------------------------

    heading("SONUC")

    print(f"kaynak erisimi        : {'EVET' if source_report.ok else 'HAYIR'}")
    print(f"metin cikarimi        : {source_report.text_length} karakter")
    print(
        f"MADDE parse edildi    : "
        f"{source_document.article_count}"
    )
    print(
        f"fallback'e dusuldu mu : "
        f"{source_document.used_fallback}"
    )

    if reference_report is not None:
        print(
            f"referans erisimi      : "
            f"{'EVET' if reference_report.ok else 'HAYIR'}"
        )
        print(f"aday eslesme sayisi   : {len(candidates)}")

    print(f"gercek Gemini cagrisi : {'EVET' if args.compare else 'HAYIR'}")
    print("gercek SMTP cagrisi   : HAYIR")
    print("veritabani yazimi     : HAYIR")

    print()

    if problems:
        print("SORUNLAR:")

        for problem in problems:
            print(f"  - {problem}")

        print()
        print("TURKISH_SOURCE_ACCEPTANCE: NOT VERIFIED")

        return 1

    print("TURKISH_SOURCE_ACCEPTANCE: VERIFIED")

    return 0


def main() -> None:

    parser = argparse.ArgumentParser(
        description=(
            "Gercek Turk mevzuat kaynagi uzerinde "
            "erisim, metin cikarimi ve madde eslesmesi "
            "dogrulamasi."
        )
    )

    parser.add_argument(
        "--url",
        default=DEFAULT_URL,
        help=(
            "Izlenen/degisiklik sayfasinin adresi. "
            f"Varsayilan: {DEFAULT_URL}"
        ),
    )

    parser.add_argument(
        "--reference-url",
        default=None,
        help=(
            "Referans yonetmeligin HTML adresi. "
            "Verilirse aday madde eslesmesi gosterilir."
        ),
    )

    parser.add_argument(
        "--topic",
        default=(
            "Enerji, elektrik piyasası ve lisans "
            "yönetmelikleri"
        ),
        help="Takip konusu (karsilastirma promptunda kullanilir).",
    )

    parser.add_argument(
        "--compare",
        action="store_true",
        help=(
            "GERCEK Gemini karsilastirmasi yapar "
            "(en fazla 1 mantiksal cagri). "
            "Verilmezse hicbir AI cagrisi yapilmaz."
        ),
    )

    args = parser.parse_args()

    print("=" * 72)
    print("GERCEK TURK MEVZUAT KAYNAGI KABUL TESTI")
    print("=" * 72)
    print(f"hedef       : {args.url}")
    print(f"referans    : {args.reference_url or '(verilmedi)'}")
    print(
        f"Gemini      : "
        f"{'GERCEK CAGRI YAPILACAK' if args.compare else 'KAPALI'}"
    )

    sys.exit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
