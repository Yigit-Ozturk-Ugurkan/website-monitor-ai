from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

# Iliskinin string ile cozulebilmesi icin modelin
# kaydolmus olmasi gerekir.
from app.models.regulation_reference import (  # noqa: F401
    RegulationReference,
)


class RegulationComparison(Base):
    """
    Bir Change icin yapilan mevzuat karsilastirmasinin
    BASLIGI.

    Tek bir degisiklik birden fazla yonetmelik maddesini
    degistirebilir; bu yuzden madde bazli sonuclar burada
    degil, RegulationComparisonItem satirlarinda tutulur.

    UNIQUE(change_id): ayni degisiklik icin ikinci bir
    karsilastirma (ve ikinci bir Gemini cagrisi) olusmaz.
    """

    __tablename__ = "regulation_comparisons"

    __table_args__ = (
        UniqueConstraint(
            "change_id",
            name="uq_regulation_comparison_change",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True,
    )

    monitor_id: Mapped[int] = mapped_column(
        ForeignKey(
            "monitors.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    change_id: Mapped[int] = mapped_column(
        ForeignKey(
            "changes.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    # Karsilastirmayi tetikleyen AI analizi.
    # Analiz olmadan karsilastirma calismaz.
    ai_analysis_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "ai_analyses.id",
            ondelete="CASCADE",
        ),
        nullable=True,
        index=True,
    )

    # Hangi referans yonetmelikle karsilastirildi.
    reference_regulation_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "regulation_references.id",
            ondelete="RESTRICT",
        ),
        nullable=True,
        index=True,
    )

    # Karsilastirma anindaki referans icerigin ozeti.
    #
    # Referans sonradan refresh edilirse bu karsilastirmanin
    # HANGI SURUME dayandigi kaybolmaz. Tam metin kopyasi
    # SAKLANMAZ; hash yeterlidir.
    reference_content_hash: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )

    # Karsilastirma KONSOLIDE bir metne mi dayaniyordu?
    #
    # Referansin bugunku durumu degil, KARSILASTIRMA
    # ANINDAKI durumu saklanir. Referans sonradan konsolide
    # bir kaynakla degistirilse bile eski karsilastirmanin
    # dayanagi oldugu gibi kalir.
    #
    # False ise sonuc, sonraki degisiklikleri gormemis bir
    # metne dayanmaktadir ve arayuzde UYARI gosterilir.
    #
    # Eski kayitlarda null olabilir: o kayitlarin hangi
    # metne dayandigi bilinmiyor demektir.
    reference_is_consolidated: Mapped[bool | None] = mapped_column(
        Boolean,
        nullable=True,
    )

    # OTOMATIK REFERANS ESLESTIRME SONUCU
    #
    # Karsilastirma DAIMA monitorun manuel referansiyla
    # yapilir; bu alan yalnizca otomatik eslestirmenin ne
    # dedigini kaydeder:
    #
    #   matched       -> otomatik secim manuel secimle ayni
    #   needs_review  -> otomatik secim FARKLI bir referans
    #                    oneriyor (kullanici gozden gecirmeli)
    #   no_match      -> uyan referans bulunamadi
    #   ambiguous     -> birden fazla aday ayirt edilemedi
    #   no_base_name  -> metinden hedef mevzuat adi cikmadi
    #
    # Eski kayitlarda null.
    reference_match_status: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
    )

    # REFERANS NASIL SECILDI
    #
    # automatic_exact   : otomatik, birebir ad eslesmesi
    # automatic_base    : otomatik, base ad skoru
    # automatic_ai      : otomatik, belirsizlik AI ile
    #                     cozuldu
    # manual            : kullanici sectI
    #
    # KESIFLE BULUNAN REFERANSLAR
    #
    # automatic_discovered_exact : resmi ad birebir ayni
    # automatic_discovered_title : tek ve net ad eslesmesi
    # automatic_discovered_meta  : ayrimi yayim bilgisi yapti
    # automatic_discovered_ai    : belirsizligi AI cozdu
    #
    # Eski kayitlarda null.
    reference_match_method: Mapped[str | None] = mapped_column(
        String(40),
        nullable=True,
    )

    # OTOMATIK REFERANS KESFI
    #
    # Kayitli kutuphanede referans BULUNAMADIGINDA resmi
    # kaynakta (mevzuat.gov.tr) yapilan aramanin sonucu:
    #
    #   not_needed        yerel eslestirme zaten referans
    #                     verdi, ya da kesfin calismasi
    #                     dogru degildi (yerel belirsizlik,
    #                     cikarilamayan mevzuat adi)
    #   not_applicable    manuel mod
    #   found_existing    resmi kayit kutuphanede vardi
    #   discovered        yeni referans olusturuldu
    #   no_match          guvenilir resmi aday yok
    #   ambiguous         resmi adaylar ayirt edilemedi
    #   discovery_failed  arama / metin alma basarisiz
    #
    # null: kesif hic calistirilmadi (eski kayitlar).
    reference_discovery_status: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
    )

    # Otomatik eslestirmenin guven skoru (0.0 - 1.0).
    # Manuel secimde null.
    reference_match_confidence: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )

    # Karsilastirma anindaki referans tazeligi.
    #
    # fresh             : yakinda dogrulanmis
    # refreshed         : bu calistirmada yenilendi
    # refreshed_changed : yenilendi ve icerik DEGISTI
    # stale_unverified  : yenilenemedi, onbellekteki metin
    #                     kullanildi (UI uyari gosterir)
    # not_applicable    : konsolide olmayan kaynak
    reference_freshness_status: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
    )

    # needs_review durumunda otomatik eslestirmenin onerdigi
    # referans.
    #
    # MANUAL modda: karsilastirmada KULLANILMAZ, yalnizca
    # kullaniciya gosterilir.
    #
    # AUTOMATIC modda bu alan kullanilmaz; otomatik secilen
    # referans dogrudan reference_regulation_id'dir.
    suggested_reference_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "regulation_references.id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )

    # completed | no_match | failed
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        index=True,
    )

    # Tum degisikligin kisa ozeti.
    # Bu bir OZETTIR; kanit degildir ve exact source
    # dogrulamasina tabi tutulmaz.
    overall_summary: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    importance: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
    )

    # Cevabi gercekten ureten model.
    # Kota fallback devreye girdiyse yedek modelin adi.
    model_name: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    # status=failed durumunda TEMIZLENMIS hata mesaji.
    # Stack trace veya sir icermez.
    error_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )

    items: Mapped[list["RegulationComparisonItem"]] = relationship(
        "RegulationComparisonItem",
        back_populates="comparison",
        cascade="all, delete-orphan",
        order_by="RegulationComparisonItem.id",
        lazy="selectin",
    )

    reference_regulation: Mapped[
        "RegulationReference | None"
    ] = relationship(
        "RegulationReference",
        foreign_keys=[reference_regulation_id],
        lazy="selectin",
    )

    suggested_reference: Mapped[
        "RegulationReference | None"
    ] = relationship(
        "RegulationReference",
        foreign_keys=[suggested_reference_id],
        lazy="selectin",
    )

    @property
    def suggested_reference_title(self) -> str | None:
        """
        needs_review durumunda onerilen referansin adi.
        """

        if self.suggested_reference is None:
            return None

        return self.suggested_reference.title

    @property
    def reference_regulation_title(self) -> str | None:
        """
        Detay ekraninda gosterilen referans adi.

        Yanit semasi from_attributes ile bu ozelligi okur;
        referansin TAM METNI hicbir yanit govdesine girmez.
        """

        if self.reference_regulation is None:
            return None

        return self.reference_regulation.title


class RegulationComparisonItem(Base):
    """
    Tek bir yonetmelik maddesindeki degisiklik.

    old_text / new_text / source_evidence / reference_evidence
    alanlarina YALNIZCA sunucu tarafinda exact olarak
    dogrulanmis metin yazilir. Dogrulanamayan deger null
    birakilir; uydurma hukuki metin saklanmaz.
    """

    __tablename__ = "regulation_comparison_items"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True,
    )

    comparison_id: Mapped[int] = mapped_column(
        ForeignKey(
            "regulation_comparisons.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    # "Madde 12" gibi kullaniciya gosterilen etiket.
    # Fallback segmentasyonda madde bilinmiyorsa null.
    affected_article: Mapped[str | None] = mapped_column(
        String(120),
        nullable=True,
    )

    affected_paragraph: Mapped[str | None] = mapped_column(
        String(120),
        nullable=True,
    )

    # amended | added | repealed | renamed | unknown
    change_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    # Referans yonetmelikten BIREBIR alinmis eski ifade.
    old_text: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # Kaynak degisiklik metninden BIREBIR alinmis yeni ifade.
    new_text: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # Kaynak degisiklik metninden BIREBIR kanit.
    source_evidence: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # Referans yonetmelikten BIREBIR kanit.
    reference_evidence: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # AI ozeti. Kanit DEGILDIR.
    change_summary: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    confidence: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )

    comparison: Mapped["RegulationComparison"] = relationship(
        "RegulationComparison",
        back_populates="items",
    )
