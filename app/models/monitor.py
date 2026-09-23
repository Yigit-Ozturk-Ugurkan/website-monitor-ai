from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

# Iliskinin string ile cozulebilmesi icin modelin
# kaydolmus olmasi gerekir. Bu modul yalnizca Base'e
# baglidir; dairesel import olusmaz.
from app.models.regulation_reference import (  # noqa: F401
    RegulationReference,
)


class Monitor(Base):
    __tablename__ = "monitors"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True,
    )

    name: Mapped[str] = mapped_column(
        String(150),
        nullable=False,
    )

    url: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    topic: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    # Takip plani
    # website_monitor    | regulation_monitor
    #
    # website_monitor mevcut davranistir: sayfadaki
    # degisiklikler takip edilir.
    #
    # regulation_monitor ayrica bir REFERANS YONETMELIK
    # tasir; karsilastirma motoru sonraki sprintte gelecek.
    #
    # Bu kolon NOT NULL + DEFAULT 'website_monitor' olarak
    # eklendigi icin mevcut tum kayitlar otomatik olarak
    # website_monitor kabul edilir.
    tracking_plan: Mapped[str] = mapped_column(
        String(30),
        default="website_monitor",
        server_default="website_monitor",
        nullable=False,
    )

    # REFERANS SECIM MODU
    #
    # automatic : referans, degisiklik metninden OTOMATIK
    #             belirlenir. reference_regulation_id
    #             bos kalabilir; karsilastirmada
    #             kullanilan referans comparison
    #             satirinda saklanir.
    #
    # manual    : referansi KULLANICI secer.
    #             reference_regulation_id ZORUNLUDUR ve
    #             otomatik eslestirme onu ASLA
    #             degistirmez.
    #
    # website_monitor kayitlarinda anlamsizdir; migration
    # sonrasi mevcut regulation monitorler manual kabul
    # edilir (referanslarini kullanici secmisti).
    reference_selection_mode: Mapped[str] = mapped_column(
        String(20),
        default="automatic",
        server_default="manual",
        nullable=False,
    )

    # manual modda ZORUNLU referans yonetmelik.
    #
    # automatic modda BOS kalabilir: o modda referans her
    # calistirmada metinden belirlenir ve tarihsel gercek
    # comparison satirindadir.
    #
    # website_monitor kayitlarinda daima null kalir.
    #
    # RESTRICT: bir monitor tarafindan kullanilan referans
    # sessizce silinemez.
    reference_regulation_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "regulation_references.id",
            ondelete="RESTRICT",
        ),
        nullable=True,
        index=True,
    )

    reference_regulation: Mapped[
        "RegulationReference | None"
    ] = relationship(
        "RegulationReference",
        # Liste uc noktasinda N+1 sorgu olusmasin.
        lazy="selectin",
    )

    @property
    def reference_regulation_title(self) -> str | None:
        """
        Monitor kartinda gosterilen referans adi.

        Yanit semasi from_attributes ile bu ozelligi okur;
        boylece tam metin payload'a hic girmez.
        """

        if self.reference_regulation is None:
            return None

        return self.reference_regulation.title

    # Takip kapsamı
    # full_page | auto_section | manual_section
    scope_type: Mapped[str] = mapped_column(
        String(30),
        default="auto_section",
        nullable=False,
    )

    # auto_section kapsaminda secilmis bolumun kalici kimligi.
    #
    # Bu alan doluysa sonraki kontrollerde AI'a bolum secimi
    # icin YENIDEN SORULMAZ; bolum dogrudan bu referans ile
    # bulunur. Referans HTML'de bulunamazsa temizlenip
    # yeniden secim yapilir.
    #
    # full_page monitorlarda null kalir.
    # manual_section bu alanlari kullanmaz.
    auto_section_reference: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # Kullaniciya gosterilen bolum adi.
    auto_section_label: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # manual_section kapsaminda KULLANICININ sectigi bolum.
    #
    # auto_section alanlarindan bagimsizdir; iki kapsam
    # birbirinin secimini asla kullanmaz.
    #
    # Bu alan kullanici secimidir: bulunamazsa otomatik
    # yeni bolum SECILMEZ, kontrollu hata uretilir.
    #
    # full_page / auto_section monitorlarda null kalir.
    manual_section_reference: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # Kullaniciya gosterilen bolum adi.
    manual_section_label: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # Zamanlama
    schedule_type: Mapped[str] = mapped_column(
        String(50),
        default="daily",
        nullable=False,
    )

    schedule_time: Mapped[str | None] = mapped_column(
        String(5),
        nullable=True,
    )

    start_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )

    end_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )

    is_unlimited: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )

    # Bildirimler
    notify_in_app: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )

    notify_email: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )

    notification_email: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    # Eski alanları şimdilik koruyoruz
    notification_type: Mapped[str] = mapped_column(
        String(50),
        default="in_app",
        nullable=False,
    )

    notification_target: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )

    # SON BASARILI KONTROL
    #
    # Yalnizca calistirma snapshot asamasina ULASTIGINDA
    # guncellenir. Siteye erisilemezse DEGISMEZ; scheduler
    # retry davranisi buna baglidir.
    last_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )

    # ---------------------------------------------------
    # SON CALISTIRMA OZETI
    #
    # Kartta "Sonuç" alanini besler. Basarisiz denemeler de
    # buraya yazilir; bu yuzden last_checked_at'ten AYRIDIR.
    #
    # Yeni bir gecmis/audit tablosu YOKTUR: yalnizca EN SON
    # denemenin sonucu tutulur.
    # ---------------------------------------------------

    # app/services/monitor_run_status.py icindeki
    # deterministik durum adi. Eski kayitlarda NULL.
    last_run_status: Mapped[str | None] = mapped_column(
        String(30),
        nullable=True,
    )

    # Kullaniciya gosterilen cumle. Durum adiyla birlikte
    # yazilir; frontend kendi metnini uretmez.
    last_run_message: Mapped[str | None] = mapped_column(
        String(200),
        nullable=True,
    )

    # Son DENEMENIN zamani. Basarisiz denemede de guncellenir.
    last_run_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )