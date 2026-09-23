"""
REFERANS YONETMELIK GERCEK KABUL TESTI

Bu betik TEK BIR gercek HTTP fetch yapar: halka acik,
HTML olarak yayimlanan bir mevzuat sayfasi indirilir ve
metin cikarimi dogrulanir.

YAPILMAYANLAR:
    - Gercek Gemini cagrisi YOK
    - Gercek SMTP cagrisi YOK
    - Veritabanina KALICI yazma YOK

Kaynak erisilemezse betik durur ve FIXTURE moduna dusup
bunu ACIKCA raporlar. Erisilemeyen bir kaynak "basarili"
gibi gosterilmez.

Calistirma:
    python -m tests.acceptance_regulation_reference
"""

import asyncio
import sys

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database import engine
from app.models.regulation_reference import RegulationReference
from app.repositories.regulation_reference_repository import (
    calculate_content_hash,
    create_regulation_reference,
    get_regulation_reference_by_url,
    update_regulation_reference_content,
)
from app.services.content_extractor import extract_content
from app.services.regulation_reference_service import (
    MIN_CONTENT_LENGTH,
    RegulationReferenceFetchError,
    fetch_regulation_content,
)


# Halka acik, HTML olarak yayimlanan mevzuat sayfalari.
#
# Turkce kaynaklar once denenir. Bu calisma ortamindan
# mevzuat.gov.tr ve resmigazete.gov.tr adreslerine
# baglanilamadigi icin listede uluslararasi HTML mevzuat
# kaynaklari da bulunuyor; boylece kabul testi gercek bir
# mevzuat sayfasi uzerinde calisabiliyor.
CANDIDATE_URLS = [
    (
        "Elektrik Piyasasi Lisans Yonetmeligi (mevzuat.gov.tr)",
        "https://www.mevzuat.gov.tr/mevzuat"
        "?MevzuatNo=17614&MevzuatTur=7&MevzuatTertip=5",
    ),
    (
        "Resmi Gazete (resmigazete.gov.tr)",
        "https://www.resmigazete.gov.tr/",
    ),
    (
        "UK SI 2016/1024 (legislation.gov.uk)",
        "https://www.legislation.gov.uk/uksi/2016/1024/made",
    ),
]


# Kaynaklara erisilemezse kullanilan yerel fixture.
FIXTURE_HTML = (
    "<html><head>"
    "<title>Ornek Yonetmelik - Fixture</title>"
    "<style>.a{color:#000}</style>"
    "</head><body>"
    "<script>var t='SCRIPT';</script>"
    "<h1>ORNEK YONETMELIK</h1>"
    "<p>MADDE 1 - Bu Yonetmeligin amaci ornek bir mevzuat "
    "metni uzerinden referans cikarimini dogrulamaktir. "
    "Metin, gercek bir mevzuat sayfasinin yapisini taklit "
    "eder ve yeterli uzunluktadir. " * 8 + "</p>"
    "</body></html>"
)


def line(char: str = "-") -> None:
    print(char * 70)


async def try_real_sources():
    """
    Adaylari sirayla dener.

    Ilk basarili kaynakta durur. Hicbiri erisilemezse
    (None, hatalar) doner.
    """

    errors = []

    for label, url in CANDIDATE_URLS:
        print(f"Deneniyor: {label}")
        print(f"  {url}")

        try:
            content = await fetch_regulation_content(url)

            print("  -> BASARILI")

            return (label, url, content), errors

        except RegulationReferenceFetchError as exc:
            print(f"  -> BASARISIZ: {exc}")
            errors.append((label, str(exc)))

        except Exception as exc:  # noqa: BLE001
            print(f"  -> BASARISIZ: {type(exc).__name__}: {exc}")
            errors.append((label, f"{type(exc).__name__}: {exc}"))

    return None, errors


def main() -> None:

    print("=" * 70)
    print("REFERANS YONETMELIK GERCEK KABUL TESTI")
    print("=" * 70)
    print("Gercek Gemini cagrisi : YOK")
    print("Gercek SMTP cagrisi   : YOK")
    print("Kalici DB kaydi       : YOK (rollback)")
    line()

    outcome, errors = asyncio.run(try_real_sources())

    line()

    used_fixture = outcome is None

    if used_fixture:
        print("SONUC: Hicbir gercek kaynaga ERISILEMEDI.")
        print("Fixture ile devam ediliyor.")
        line()

        for label, message in errors:
            print(f"  {label}: {message}")

        line()

        extracted = extract_content(FIXTURE_HTML)

        source_label = "YEREL FIXTURE"
        source_url = "fixture://ornek-yonetmelik"
        page_title = extracted.title
        text = extracted.text.strip()

    else:
        source_label, source_url, content = outcome

        page_title = content.page_title
        text = content.text

        print(f"SONUC: GERCEK KAYNAK kullanildi.")
        line()

    print(f"Kaynak       : {source_label}")
    print(f"Adres        : {source_url}")
    print(f"Sayfa basligi: {page_title[:80]}")
    print(f"Metin uzunlugu: {len(text)} karakter")
    line()

    print("Metin ornegi (ilk 300 karakter):")
    print(text[:300].replace("\n", " "))
    line()

    problems = []

    if len(text) < MIN_CONTENT_LENGTH:
        problems.append(
            f"metin cok kisa ({len(text)} < {MIN_CONTENT_LENGTH})"
        )

    if "<script" in text or "<div" in text:
        problems.append("metinde HTML etiketi kalmis")

    if "SCRIPT" in text and used_fixture:
        problems.append("script icerigi metne sizmis")

    content_hash = calculate_content_hash(text)

    if len(content_hash) != 64:
        problems.append("hash sha256 uzunlugunda degil")

    print(f"content_hash : {content_hash}")
    line()

    # -----------------------------------------------------
    # DB YAZMA - DIS TRANSACTION ICINDE, GERI ALINIR
    # -----------------------------------------------------

    print("Veritabani kaydi dogrulaniyor (rollback edilecek)...")

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
        existing = get_regulation_reference_by_url(
            db=session,
            source_url=source_url,
        )

        if existing is not None:
            problems.append(
                "bu kaynak icin zaten bir referans var; "
                "kabul testi kayit olusturmadi"
            )

        else:
            reference = create_regulation_reference(
                db=session,
                title=f"[KABUL TESTI] {source_label}",
                source_url=source_url,
                content=text,
            )

            print(f"  kayit id      : {reference.id}")
            print(f"  saklanan hash : {reference.content_hash}")

            if reference.content_hash != content_hash:
                problems.append("saklanan hash beklenenden farkli")

            if reference.content_length != len(text):
                problems.append("content_length tutarsiz")

            # Ayni icerikle refresh -> degisiklik YOK
            changed_same = update_regulation_reference_content(
                db=session,
                reference=reference,
                content=text,
            )

            if changed_same:
                problems.append(
                    "ayni icerik 'degisti' olarak raporlandi"
                )

            # Degisen icerikle refresh -> degisiklik VAR
            changed_new = update_regulation_reference_content(
                db=session,
                reference=reference,
                content=text + "\nEK MADDE - kabul testi.",
            )

            if not changed_new:
                problems.append(
                    "degisen icerik 'degismedi' olarak raporlandi"
                )

            print(f"  ayni icerik  -> changed={changed_same}")
            print(f"  degisen icerik -> changed={changed_new}")

    finally:
        session.close()
        transaction.rollback()
        connection.close()

    # -----------------------------------------------------
    # KALICI KAYIT BIRAKILMADI MI
    # -----------------------------------------------------

    from app.core.database import SessionLocal

    verify = SessionLocal()

    try:
        count_after = verify.scalar(
            select(func.count()).select_from(RegulationReference)
        )

        leftovers = verify.scalars(
            select(RegulationReference).where(
                RegulationReference.title.like("[KABUL TESTI]%")
            )
        ).all()

    finally:
        verify.close()

    line()
    print(f"Referans sayisi once : {count_before}")
    print(f"Referans sayisi sonra: {count_after}")
    print(f"Artik kayit          : {[r.id for r in leftovers]}")

    if count_after != count_before or leftovers:
        problems.append("veritabaninda kalici kayit birakildi")

    line("=")

    if used_fixture:
        print("KABUL TESTI MODU : FIXTURE")
        print(
            "Gercek kaynaga erisilemedigi icin dogrulama "
            "yerel fixture ile yapildi."
        )
    else:
        print("KABUL TESTI MODU : GERCEK KAYNAK (1 fetch)")

    if problems:
        print("\nSORUNLAR:")
        for problem in problems:
            print(f"  - {problem}")

        print("\nKABUL TESTI BASARISIZ")
        line("=")

        sys.exit(1)

    print("\nKABUL TESTI BASARILI")
    line("=")


if __name__ == "__main__":
    main()
