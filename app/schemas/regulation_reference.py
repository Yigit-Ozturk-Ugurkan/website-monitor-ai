from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, field_validator


MAX_TITLE_LENGTH = 300


# Detay yanitindaki onizleme siniri.
#
# Tam metin (yuz binlerce karakter olabilir) API yanitina
# KONULMAZ; frontend'in ihtiyaci referansin dogru belge
# oldugunu teyit etmektir.
MAX_PREVIEW_CHARS = 1500


def _require_non_empty(value: str, field_label: str) -> str:

    cleaned = value.strip()

    if not cleaned:
        raise ValueError(
            f"{field_label} boş olamaz."
        )

    return cleaned


def _require_http_url(value: str) -> str:
    """
    Kaynak adres bicim kontrolu.

    Asil dogrulama ve guvenlik davranisi web_fetcher'da;
    burasi kullaniciya erken ve anlasilir geri bildirim
    icindir. Yeni bir fetch yolu acilmaz.
    """

    cleaned = _require_non_empty(value, "Kaynak URL")

    if not cleaned.startswith(("http://", "https://")):
        raise ValueError(
            "Kaynak URL 'http://' veya 'https://' ile "
            "başlamalıdır."
        )

    return cleaned


def build_preview(text: str) -> str:
    """
    DUZ METIN onizleme uretir.

    Donen deger HTML DEGILDIR ve frontend tarafinda metin
    olarak render edilir (dangerouslySetInnerHTML yok).
    """

    if len(text) <= MAX_PREVIEW_CHARS:
        return text

    return text[:MAX_PREVIEW_CHARS].rstrip() + "..."


class RegulationReferenceCreate(BaseModel):
    title: str
    source_url: str

    @field_validator("title")
    @classmethod
    def check_title(cls, value):

        cleaned = _require_non_empty(
            value,
            "Yönetmelik adı",
        )

        if len(cleaned) > MAX_TITLE_LENGTH:
            raise ValueError(
                "Yönetmelik adı çok uzun."
            )

        return cleaned

    @field_validator("source_url")
    @classmethod
    def check_source_url(cls, value):
        return _require_http_url(value)


class RegulationReferenceSummary(BaseModel):
    """
    Liste ve olusturma yaniti.

    Tam metin BILEREK yoktur; monitor formundaki secim
    listesi icin bu alanlar yeterlidir.
    """

    id: int

    title: str
    source_url: str

    content_length: int
    content_hash: str

    # ---------------------------------------------------
    # SURUM BILGISI
    #
    # Kullanicinin en cok ihtiyac duydugu bilgi sudur:
    # bu metin GUNCEL KONSOLIDE mevzuat mi, yoksa
    # yayimlandigi andaki TARIHSEL metin mi?
    # ---------------------------------------------------

    # mevzuat_gov_tr | resmi_gazete_archive | generic
    source_type: str = "generic"

    is_consolidated: bool = False

    # Metne islenmis EN SON degisikligin RG tarihi.
    version_date: date | None = None

    # Kaynak en son ne zaman basariyla dogrulandi.
    last_verified_at: datetime | None = None

    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(
        from_attributes=True,
    )


class RegulationReferenceDetail(RegulationReferenceSummary):
    """
    Detay yaniti.

    Tam metin yerine kisaltilmis DUZ METIN onizleme doner.
    """

    preview: str

    # Onizleme metnin tamamini kapsiyor mu?
    # Frontend "..." yerine dogru bilgiyi gosterebilsin.
    preview_truncated: bool


class RegulationReferenceRefreshResponse(
    RegulationReferenceSummary
):
    """
    Yenileme yaniti.

    changed=False ise icerik gercekten degismemistir;
    kayitta hicbir alan guncellenmemistir.
    """

    changed: bool

    previous_content_hash: str


# ---------------------------------------------------------
# OTOMATIK REFERANS ESLESTIRME
#
# Kullanici yeni bir mevzuat izlemesi kurarken, degisiklik
# metnini verip "hangi referansi secmeliyim?" diye
# sorabilir.
#
# Bu uc nokta bir ONERI uretir; hicbir sey KAYDETMEZ ve
# kullanicinin secimini DEGISTIRMEZ.
# ---------------------------------------------------------

class RegulationReferenceMatchRequest(BaseModel):
    # Degisiklik metni (Resmî Gazete sayfasindan).
    source_text: str

    # Varsa acikca verilen baslik.
    title: str | None = None

    # Belirsizlik durumunda yapay zeka kullanilsin mi?
    #
    # False iken HICBIR kosulda Gemini cagrilmaz.
    allow_ai: bool = True

    @field_validator("source_text")
    @classmethod
    def check_source_text(cls, value):
        return _require_non_empty(value, "Değişiklik metni")


class RegulationReferenceMatchCandidate(BaseModel):
    reference_id: int
    title: str

    score: float

    is_consolidated: bool
    source_type: str


class RegulationReferenceMatchResponse(BaseModel):
    """
    Otomatik eslestirme onerisi.

    status:
      matched       -> guvenilir tek eslesme
      ambiguous     -> ayirt edilemedi
      no_match      -> uyan referans yok
      no_base_name  -> metinden mevzuat adi cikarilamadi
      no_references -> sistemde kayitli referans yok
    """

    status: str

    # Onerilen referans. status != matched ise null.
    reference_id: int | None = None
    reference_title: str | None = None

    # Metinden cikarilan hedef mevzuat adi.
    base_regulation_name: str | None = None

    # Hedef mevzuatin yayim bilgisi (bulunabildiyse).
    base_published_on: date | None = None
    base_gazette_issue: str | None = None

    score: float = 0.0

    # Belirsizlik icin yapay zeka calistirildi mi?
    ai_used: bool = False

    # Kullaniciya gosterilecek kisa aciklama.
    detail: str = ""

    # Skoru gorulen adaylar (en fazla birkac tane).
    candidates: list[RegulationReferenceMatchCandidate] = []
