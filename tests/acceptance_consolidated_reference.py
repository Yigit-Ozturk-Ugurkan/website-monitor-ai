"""
KONSOLIDE MEVZUAT KAYNAGI KABUL TESTI

Bu betik GERCEK mevzuat.gov.tr adresine baglanir.

KULLANIM

    python -m tests.acceptance_consolidated_reference

    python -m tests.acceptance_consolidated_reference \\
        --url "https://www.mevzuat.gov.tr/mevzuat?MevzuatNo=39080&MevzuatTur=7&MevzuatTertip=5"

    # Ayni yonetmeligin Resmî Gazete arsiv surumuyle
    # karsilastirmali rapor:
    python -m tests.acceptance_consolidated_reference \\
        --url "<mevzuat-url>" \\
        --archive-url "<resmi-gazete-url>"

DOGRULANAN SEYLER

    1. gercek konsolide icerik alinabiliyor
    2. MADDE parse ediliyor
    3. content hash uretiliyor
    4. is_consolidated=true
    5. surum/tarih bilgisi cikariliyor
    6. karsilastirma servisi bu referansi kullanabiliyor

GUVENLIK / SINIRLAR

    - SIR YAZMAZ
    - VERITABANINA HICBIR SEY YAZMAZ (dis transaction
      sonunda rollback)
    - GEMINI CAGIRMAZ
    - SMTP CAGIRMAZ
    - sertifika dogrulamasi ACIK kalir
"""

import argparse
import asyncio
import sys

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database import engine
from app.models.regulation_reference import RegulationReference
from app.repositories.regulation_reference_repository import (
    calculate_content_hash,
    create_regulation_reference,
)
from app.services.regulation_candidate_matcher import (
    select_candidates,
)
from app.services.regulation_reference_service import (
    RegulationReferenceFetchError,
    fetch_regulation_content,
)
from app.services.regulation_source_registry import (
    SOURCE_TYPE_MEVZUAT_GOV_TR,
    resolve_reference_source,
)
from app.services.regulation_text_parser import (
    parse_regulation_segments,
)


# Varsayilan: Odeme Hizmetleri Yonetmeligi.
# Sprint 3'te gercek Resmî Gazete degisikligi bu
# yonetmeligi degistiriyordu.
DEFAULT_URL = (
    "https://www.mevzuat.gov.tr/mevzuat"
    "?MevzuatNo=39080&MevzuatTur=7&MevzuatTertip=5"
)


def line(char: str = "-") -> None:
    print(char * 72)


def heading(title: str) -> None:
    print()
    line("=")
    print(title)
    line("=")


async def load(url: str, label: str):
    """
    Uretimde kullanilan AYNI yolu kullanir.
    """

    print(f"\n{label}")
    print(f"  {url}")

    resolved = resolve_reference_source(url)

    print(f"  kaynak turu   : {resolved.source_type}")
    print(f"  konsolide mi  : {resolved.is_consolidated}")
    print(f"  adres cevrildi: {resolved.rewritten}")

    if resolved.rewritten:
        print(f"  metin adresi  : {resolved.fetch_url}")

    try:
        content = await fetch_regulation_content(url)

    except RegulationReferenceFetchError as exc:
        print(f"  SONUC         : BASARISIZ")
        print(f"  HATA          : {exc}")

        return None

    print(f"  SONUC         : BASARILI")
    print(f"  metin uzunlugu: {content.text_length} karakter")
    print(f"  isaret sayisi : {content.amendment_marker_count}")
    print(f"  surum tarihi  : {content.version_date}")

    return content


async def run(args) -> int:

    problems: list[str] = []

    heading("1-5. KONSOLIDE KAYNAK")

    content = await load(args.url, "KONSOLIDE REFERANS")

    if content is None:
        problems.append("konsolide kaynak alinamadi")

        heading("SONUC")
        print("CONSOLIDATED_SOURCE_ACCEPTANCE: NOT VERIFIED")

        for problem in problems:
            print(f"  - {problem}")

        return 1

    # -----------------------------------------------------
    # 2. SEGMENTASYON
    # -----------------------------------------------------

    document = parse_regulation_segments(content.text)

    print(f"\n  SEGMENTASYON")
    print(f"    fallback  : {document.used_fallback}")
    print(f"    MADDE     : {document.article_count}")

    for segment in document.segments[:5]:
        print(
            f"      - {str(segment.article_label):22s} "
            f"{segment.text_length:6d} karakter"
        )

    if document.used_fallback:
        problems.append(
            "MADDE basliklari taninmadi (fallback)"
        )

    if document.article_count < 5:
        problems.append(
            "cok az MADDE bulundu: "
            f"{document.article_count}"
        )

    # -----------------------------------------------------
    # 3-5. HASH VE SURUM BILGISI
    # -----------------------------------------------------

    content_hash = calculate_content_hash(content.text)

    print(f"\n  content_hash  : {content_hash}")

    if len(content_hash) != 64:
        problems.append("hash sha256 uzunlugunda degil")

    if content.source_type != SOURCE_TYPE_MEVZUAT_GOV_TR:
        problems.append(
            "kaynak turu mevzuat_gov_tr degil: "
            f"{content.source_type}"
        )

    if not content.is_consolidated:
        problems.append("is_consolidated true degil")

    if content.amendment_marker_count == 0:
        print(
            "\n  NOT: metinde degisiklik isareti yok. "
            "Hic degistirilmemis bir yonetmelik olabilir."
        )

    if content.version_date is None and content.amendment_marker_count:
        problems.append(
            "isaret var ama surum tarihi cikarilamadi"
        )

    # -----------------------------------------------------
    # ARSIV SURUMU ILE KARSILASTIRMA
    # -----------------------------------------------------

    archive = None

    if args.archive_url:

        heading("KARSILASTIRMA: KONSOLIDE vs ARSIV")

        archive = await load(
            args.archive_url,
            "ARSIV (TARIHSEL) REFERANS",
        )

        if archive is None:
            problems.append("arsiv surumu alinamadi")

        else:
            archive_document = parse_regulation_segments(
                archive.text
            )

            print()
            print(
                f"  {'':22s} {'KONSOLIDE':>12s} "
                f"{'ARSIV':>12s}"
            )
            print(
                f"  {'metin uzunlugu':22s} "
                f"{content.text_length:12d} "
                f"{archive.text_length:12d}"
            )
            print(
                f"  {'MADDE sayisi':22s} "
                f"{document.article_count:12d} "
                f"{archive_document.article_count:12d}"
            )
            print(
                f"  {'degisiklik isareti':22s} "
                f"{content.amendment_marker_count:12d} "
                f"{archive.amendment_marker_count:12d}"
            )
            print(
                f"  {'is_consolidated':22s} "
                f"{str(content.is_consolidated):>12s} "
                f"{str(archive.is_consolidated):>12s}"
            )

            if archive.is_consolidated:
                problems.append(
                    "arsiv surumu yanlislikla konsolide "
                    "isaretlendi"
                )

            if content.text_length <= archive.text_length:
                print(
                    "\n  NOT: konsolide metin arsivden "
                    "uzun DEGIL. Yonetmelik hic "
                    "degistirilmemis olabilir."
                )

    # -----------------------------------------------------
    # 6. KARSILASTIRMA SERVISI BU REFERANSI KULLANABILIYOR MU
    #
    # GEMINI CAGRILMAZ. Yalnizca yerel aday secimi
    # calistirilir; karsilastirma motorunun bu referansi
    # tuketebildigi boylece dogrulanir.
    # -----------------------------------------------------

    heading("6. KARSILASTIRMA SERVISI UYUMU")

    sample_amendment = (
        "MADDE 1- Aynı Yönetmeliğin 41 inci maddesinde yer "
        "alan ibare değiştirilmiştir."
    )

    candidates = select_candidates(
        source_text=sample_amendment,
        segments=document.segments,
    )

    print(f"  ornek degisiklik metni ile aday secimi")
    print(f"  secilen aday: {len(candidates)}")

    for candidate in candidates[:5]:
        print(
            f"    {candidate.candidate_id:10s} "
            f"{str(candidate.article_label):22s} "
            f"{candidate.reason:20s} "
            f"skor={candidate.score:.3f}"
        )

    if not candidates:
        problems.append(
            "karsilastirma servisi bu referanstan aday "
            "secemedi"
        )

    elif candidates[0].article_label != "Madde 41":
        print(
            "\n  NOT: ilk aday Madde 41 degil "
            f"({candidates[0].article_label}). Bu yonetmelikte "
            "41 inci madde bulunmuyor olabilir."
        )

    # -----------------------------------------------------
    # VERITABANI KAYDI (rollback edilir)
    # -----------------------------------------------------

    heading("VERITABANI KAYDI (rollback edilecek)")

    connection = engine.connect()
    transaction = connection.begin()

    session = Session(
        bind=connection,
        join_transaction_mode="create_savepoint",
    )

    count_before = session.scalar(
        select(func.count()).select_from(RegulationReference)
    )

    try:
        reference = create_regulation_reference(
            db=session,
            title=f"[KABUL TESTİ] {content.page_title[:60]}",
            source_url=args.url,
            content=content.text,
            source_type=content.source_type,
            is_consolidated=content.is_consolidated,
            version_date=content.version_date,
        )

        print(f"  kayit id         : {reference.id}")
        print(f"  source_type      : {reference.source_type}")
        print(f"  is_consolidated  : {reference.is_consolidated}")
        print(f"  version_date     : {reference.version_date}")
        print(f"  last_verified_at : {reference.last_verified_at}")
        print(f"  content_hash     : {reference.content_hash[:32]}...")

        if reference.content_hash != content_hash:
            problems.append("saklanan hash beklenenden farkli")

        if not reference.is_consolidated:
            problems.append(
                "kayitta is_consolidated true degil"
            )

        if reference.last_verified_at is None:
            problems.append("last_verified_at yazilmadi")

    finally:
        session.close()
        transaction.rollback()
        connection.close()

    from app.core.database import SessionLocal

    verify = SessionLocal()

    try:
        count_after = verify.scalar(
            select(func.count()).select_from(RegulationReference)
        )

    finally:
        verify.close()

    print(f"\n  referans sayisi once : {count_before}")
    print(f"  referans sayisi sonra: {count_after}")

    if count_after != count_before:
        problems.append(
            "veritabaninda kalici kayit birakildi"
        )

    # -----------------------------------------------------
    # SONUC
    # -----------------------------------------------------

    heading("SONUC")

    print(f"konsolide icerik alindi : EVET")
    print(f"MADDE parse edildi      : {document.article_count}")
    print(f"content hash uretildi   : EVET")
    print(f"is_consolidated         : {content.is_consolidated}")
    print(f"surum tarihi            : {content.version_date}")
    print(f"karsilastirma adayi     : {len(candidates)}")
    print(f"gercek Gemini cagrisi   : HAYIR")
    print(f"gercek SMTP cagrisi     : HAYIR")
    print(f"kalici DB kaydi         : HAYIR")

    print()

    if problems:
        print("SORUNLAR:")

        for problem in problems:
            print(f"  - {problem}")

        print()
        print("CONSOLIDATED_SOURCE_ACCEPTANCE: NOT VERIFIED")

        return 1

    print("CONSOLIDATED_SOURCE_ACCEPTANCE: VERIFIED")

    return 0


def main() -> None:

    parser = argparse.ArgumentParser(
        description=(
            "Gercek mevzuat.gov.tr konsolide metni uzerinde "
            "erisim, surum bilgisi ve karsilastirma uyumu "
            "dogrulamasi."
        )
    )

    parser.add_argument(
        "--url",
        default=DEFAULT_URL,
        help=f"Konsolide mevzuat adresi. Varsayilan: {DEFAULT_URL}",
    )

    parser.add_argument(
        "--archive-url",
        default=None,
        help=(
            "Ayni yonetmeligin Resmî Gazete arsiv adresi. "
            "Verilirse konsolide/arsiv karsilastirmasi "
            "gosterilir."
        ),
    )

    args = parser.parse_args()

    print("=" * 72)
    print("KONSOLIDE MEVZUAT KAYNAGI KABUL TESTI")
    print("=" * 72)
    print(f"hedef    : {args.url}")
    print(f"arsiv    : {args.archive_url or '(verilmedi)'}")
    print("Gemini   : KAPALI")
    print("SMTP     : KAPALI")

    sys.exit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
