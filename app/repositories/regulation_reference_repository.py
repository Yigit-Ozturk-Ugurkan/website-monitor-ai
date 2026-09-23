import hashlib

from datetime import date, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.regulation_reference import RegulationReference


def calculate_content_hash(content: str) -> str:
    """
    Referans icerigin sha256 ozeti.

    Snapshot tarafiyla ayni algoritma kullanilir; boylece
    "gercekten degisti mi" sorusu her yerde ayni sekilde
    cevaplanir.
    """

    return hashlib.sha256(
        content.encode("utf-8")
    ).hexdigest()


def create_regulation_reference(
    db: Session,
    title: str,
    source_url: str,
    content: str,
    source_type: str = "generic",
    is_consolidated: bool = False,
    version_date: date | None = None,
) -> RegulationReference:
    """
    Referans yonetmeligi kaydeder.

    Surum alanlarinin varsayilanlari GUVENLI TARAFTADIR:
    bu parametreleri hic gondermeyen bir cagri, metni
    konsolide OLMAYAN genel bir kaynak olarak kaydeder.
    """

    reference = RegulationReference(
        title=title,
        source_url=source_url,
        content=content,
        content_hash=calculate_content_hash(content),
        source_type=source_type,
        is_consolidated=is_consolidated,
        version_date=version_date,

        # Icerik su anda basariyla alindi.
        last_verified_at=datetime.utcnow(),
    )

    db.add(reference)
    db.commit()
    db.refresh(reference)

    return reference


def get_regulation_reference(
    db: Session,
    reference_id: int,
) -> RegulationReference | None:

    return db.get(
        RegulationReference,
        reference_id,
    )


def get_regulation_reference_by_url(
    db: Session,
    source_url: str,
) -> RegulationReference | None:
    """
    Ayni kaynaktan zaten bir referans var mi?

    API bu bilgiyle kullaniciyi sessiz bir kopya yaratmak
    yerine mevcut kayda yonlendirir.
    """

    statement = (
        select(RegulationReference)
        .where(
            RegulationReference.source_url == source_url
        )
        .limit(1)
    )

    return db.scalar(statement)


def get_regulation_reference_by_title(
    db: Session,
    title: str,
) -> RegulationReference | None:
    """
    Ayni ada sahip referansi buyuk/kucuk harf duyarsiz arar.

    Ayni ad FARKLI kaynaklarda mesru olabilir (ornegin
    yonetmeligin baska bir yayin adresi), bu yuzden bu
    kontrol engelleyici degildir; API yalnizca uyari
    dondurmek icin kullanir.
    """

    statement = (
        select(RegulationReference)
        .where(
            func.lower(RegulationReference.title)
            == title.strip().lower()
        )
        .order_by(RegulationReference.id)
        .limit(1)
    )

    return db.scalar(statement)


def get_regulation_references(
    db: Session,
) -> list[RegulationReference]:
    """
    Monitor formundaki secim listesi icin kullanilir.

    Sirlama ada gore yapilir; kullanici uzun bir listede
    aradigini bulabilsin.
    """

    statement = (
        select(RegulationReference)
        .order_by(
            RegulationReference.title,
            RegulationReference.id,
        )
    )

    return list(
        db.scalars(statement).all()
    )


def update_regulation_reference_content(
    db: Session,
    reference: RegulationReference,
    content: str,
    source_type: str | None = None,
    is_consolidated: bool | None = None,
    version_date: date | None = None,
) -> bool:
    """
    Referans icerigini yeniler.

    Icerik GERCEKTEN degistiyse True doner.

    SURUM ALANLARI ICERIKTEN BAGIMSIZ GUNCELLENIR:

      - last_verified_at her BASARILI dogrulamada oynar
        (icerik degismese bile kaynagin hala erisilebilir
        oldugunu gosterir)

      - source_type / is_consolidated / version_date
        gonderildiyse guncellenir

    updated_at ise YALNIZCA icerik degistiginde oynar;
    kullaniciya olmayan bir icerik guncellemesi
    gosterilmez.
    """

    new_hash = calculate_content_hash(content)

    content_changed = new_hash != reference.content_hash

    if content_changed:
        reference.content = content
        reference.content_hash = new_hash

        # updated_at YALNIZCA burada oynar.
        reference.updated_at = datetime.utcnow()

    if source_type is not None:
        reference.source_type = source_type

    if is_consolidated is not None:
        reference.is_consolidated = is_consolidated

    # version_date yalnizca YENI bir deger bulunduysa
    # yazilir. Bulunamadiginda eski deger silinmez;
    # bir kez tespit edilmis surum bilgisi kaybolmaz.
    if version_date is not None:
        reference.version_date = version_date

    reference.last_verified_at = datetime.utcnow()

    db.commit()
    db.refresh(reference)

    return content_changed
