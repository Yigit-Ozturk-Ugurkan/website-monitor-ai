from datetime import datetime

from pydantic import BaseModel, ConfigDict


# Kullaniciya gosterilecek degisiklik turu etiketleri.
CHANGE_TYPE_LABELS = {
    "amended": "Değiştirildi",
    "added": "Eklendi",
    "repealed": "Yürürlükten kaldırıldı",
    "renamed": "Adı değiştirildi",
    "unknown": "Belirlenemedi",
}


# Frontend'de her zaman gosterilen sorumluluk notu.
# Sistem hukuki tavsiye veremez.
DISCLAIMER = (
    "AI tarafından oluşturulan karşılaştırmadır. "
    "Resmî metin esas alınmalıdır."
)


# Karsilastirma konsolide OLMAYAN bir metne dayandiginda
# gosterilen uyari.
# Otomatik eslestirme, kullanicinin sectiginden FARKLI bir
# referans onerdiginde gosterilen uyari.
# Referans metni dogrulanamadi; onbellekteki surum
# kullanildi.
STALE_REFERENCE_WARNING = (
    "Referans metin bu çalıştırmada doğrulanamadı; "
    "önbellekteki sürüm kullanıldı. Referans, kaynaktaki "
    "en güncel hâli olmayabilir."
)


# Eslestirme yontemi etiketleri.
MATCH_METHOD_LABELS = {
    "manual": "Manuel seçim",
    "automatic_exact": "Otomatik — birebir başlık",
    "automatic_base": "Otomatik — başlık eşleşmesi",
    "automatic_ai": "Otomatik — yapay zeka ayrımı",

    # Kayitli kutuphanede bulunamayip resmi kaynakta
    # kesfedilen referanslar.
    "automatic_discovered_exact": (
        "Mevzuat Bilgi Sistemi — birebir başlık"
    ),
    "automatic_discovered_title": (
        "Mevzuat Bilgi Sistemi — başlık eşleşmesi"
    ),
    "automatic_discovered_meta": (
        "Mevzuat Bilgi Sistemi — yayım bilgisi ayrımı"
    ),
    "automatic_discovered_ai": (
        "Mevzuat Bilgi Sistemi — yapay zeka ayrımı"
    ),
}


NEEDS_REVIEW_WARNING = (
    "Otomatik eşleştirme, bu değişikliğin farklı bir "
    "referans yönetmeliği hedeflediğini düşünüyor. "
    "Karşılaştırma sizin seçtiğiniz referansla yapıldı; "
    "referans seçimini gözden geçirin."
)


NON_CONSOLIDATED_WARNING = (
    "Karşılaştırma konsolide olmayan referans metin "
    "üzerinden yapılmıştır. Referans, yayımlandığı "
    "tarihteki hâlini gösteriyor olabilir ve sonraki "
    "değişiklikleri içermeyebilir."
)


class RegulationComparisonItemResponse(BaseModel):
    """
    Tek bir madde degisikligi.

    Buradaki old_text / new_text / source_evidence /
    reference_evidence degerleri SUNUCU TARAFINDA birebir
    dogrulanmis metinlerdir. Dogrulanamayanlar null gelir.

    change_summary bir OZETTIR; kanit degildir.
    """

    id: int

    affected_article: str | None = None
    affected_paragraph: str | None = None

    change_type: str

    old_text: str | None = None
    new_text: str | None = None

    source_evidence: str | None = None
    reference_evidence: str | None = None

    change_summary: str | None = None
    confidence: float | None = None

    model_config = ConfigDict(
        from_attributes=True,
    )


class RegulationComparisonResponse(BaseModel):
    """
    Bir degisiklik icin yapilan mevzuat karsilastirmasi.

    status:
        completed | no_match | failed
    """

    id: int

    monitor_id: int
    change_id: int

    reference_regulation_id: int | None = None

    # Karsilastirma anindaki referans surumunun ozeti.
    # Referans sonradan yenilense de bu deger degismez.
    reference_content_hash: str | None = None

    # Karsilastirma KONSOLIDE bir metne mi dayaniyordu?
    #
    # False  -> sonuc, sonraki degisiklikleri gormemis
    #           tarihsel bir metne dayaniyor
    # None   -> eski kayit; hangi metne dayandigi bilinmiyor
    #
    # Her iki durumda da arayuz UYARI gosterir; sahte
    # kesinlik verilmez.
    reference_is_consolidated: bool | None = None

    # OTOMATIK REFERANS ESLESTIRME
    #
    # Karsilastirma DAIMA kullanicinin sectigi referansla
    # yapilir. Bu alanlar yalnizca otomatik eslestirmenin
    # ne dedigini bildirir.
    #
    # needs_review -> otomatik eslestirme FARKLI bir
    #                 referans oneriyor; kullanici gozden
    #                 gecirmeli
    # None         -> eslestirme calistirilmadi (eski kayit)
    reference_match_status: str | None = None

    # manual | automatic_exact | automatic_base |
    # automatic_ai |
    # automatic_discovered_exact | automatic_discovered_title |
    # automatic_discovered_meta  | automatic_discovered_ai
    reference_match_method: str | None = None

    # Otomatik secimin guven skoru (0.0 - 1.0).
    reference_match_confidence: float | None = None

    # fresh | refreshed | refreshed_changed |
    # stale_unverified | not_applicable
    reference_freshness_status: str | None = None

    # OTOMATIK REFERANS KESFI
    #
    # Kayitli kutuphanede referans bulunamadiginda resmi
    # kaynakta (mevzuat.gov.tr) yapilan aramanin sonucu:
    #
    # not_needed | not_applicable | found_existing |
    # discovered | no_match | ambiguous | discovery_failed
    #
    # None -> kesif calistirilmadi (eski kayit).
    reference_discovery_status: str | None = None

    suggested_reference_id: int | None = None

    # Onerilen referansin adi. TAM METNI yer almaz.
    suggested_reference_title: str | None = None

    # Kart ve detay ekraninda gosterilen referans adi.
    # Referansin TAM METNI bu yanitta YER ALMAZ.
    reference_regulation_title: str | None = None

    status: str

    overall_summary: str | None = None
    importance: str | None = None

    model_name: str | None = None

    # status=failed durumunda TEMIZLENMIS mesaj.
    # Stack trace veya sir icermez.
    error_message: str | None = None

    created_at: datetime

    items: list[RegulationComparisonItemResponse] = []

    model_config = ConfigDict(
        from_attributes=True,
    )
