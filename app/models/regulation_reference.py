from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class RegulationReference(Base):
    """
    Bir monitorden BAGIMSIZ yasayan referans yonetmelik.

    Ayri tablo olmasinin nedeni paylasimdir: ayni yonetmelik
    (ornegin "Elektrik Piyasasi Lisans Yonetmeligi") birden
    fazla monitor tarafindan kullanilabilir. Metin monitor
    kaydina gomulseydi her monitor icin tekrar kopyalanir,
    guncelleme de her kopyada ayri ayri yapilmasi gerekirdi.
    """

    __tablename__ = "regulation_references"

    __table_args__ = (
        # Ayni kaynak icin ikinci bir referans olusturmak
        # neredeyse her zaman istenmeyen bir tekrardir.
        # API bunu 409 ile, veritabani da bu kisit ile
        # engeller.
        UniqueConstraint(
            "source_url",
            name="uq_regulation_reference_source_url",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True,
    )

    title: Mapped[str] = mapped_column(
        String(300),
        nullable=False,
    )

    source_url: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    # Kaynaktan cikarilmis DUZ METIN.
    #
    # HTML DEGILDIR. content_extractor script/style/iframe
    # gibi etiketleri kaldirdiktan sonra yalnizca metin
    # birakir; bu deger frontend'e de metin olarak gider.
    content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    # Icerigin sha256 ozeti.
    # Refresh sirasinda gercek bir degisiklik olup olmadigi
    # bu deger ile anlasilir.
    content_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
    )

    # -----------------------------------------------------
    # SURUM BILGISI
    #
    # Amac tek bir soruyu cevaplayabilmek:
    # bu metin GUNCEL KONSOLIDE mevzuat mi, yoksa
    # yayimlandigi andaki TARIHSEL metin mi?
    #
    # Tam bir surum gecmisi sistemi DEGILDIR; her referans
    # tek bir metin tasir.
    # -----------------------------------------------------

    # mevzuat_gov_tr | resmi_gazete_archive | generic
    #
    # NOT NULL + DEFAULT 'generic' olarak eklendigi icin
    # mevcut kayitlar otomatik olarak generic olur.
    source_type: Mapped[str] = mapped_column(
        String(40),
        default="generic",
        server_default="generic",
        nullable=False,
    )

    # Metin uzerine sonraki degisiklikler islenmis mi?
    #
    # Yalnizca KANITLANABILDIGINDE true olur. Konsolide
    # oldugu bilinmeyen bir metin konsolide gibi
    # isaretlenmez.
    is_consolidated: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default="false",
        nullable=False,
    )

    # Metne islenmis EN SON degisikligin Resmî Gazete
    # tarihi. Konsolide metinlerdeki
    # "(Değişik:RG-4/9/2026-33360)" isaretlerinden gelir.
    version_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )

    # Kaynak en son ne zaman BASARIYLA getirildi?
    #
    # updated_at yalnizca icerik DEGISTIGINDE oynar;
    # bu alan ise degisiklik olmasa bile her basarili
    # dogrulamada guncellenir.
    last_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )

    # Icerik GERCEKTEN degistiginde guncellenir.
    # Icerigi degismeyen bir refresh bu alani OYNATMAZ.
    #
    # onupdate KULLANILMAZ: satirda yalnizca
    # last_verified_at gibi surum alanlari degistiginde de
    # tetiklenir ve kullaniciya olmayan bir icerik
    # guncellemesi gosterirdi. Bu yuzden deger repository
    # katmaninda ACIKCA yazilir.
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )

    @property
    def content_length(self) -> int:
        """
        Metnin karakter uzunlugu.

        API yanitlari tam metin yerine bu degeri tasir;
        boylece payload gereksiz buyumez ama kullanici
        referansin dolu oldugunu gorebilir.
        """

        return len(self.content or "")
