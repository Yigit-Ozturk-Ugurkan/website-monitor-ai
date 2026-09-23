import re

from datetime import date, datetime

from pydantic import (
    BaseModel,
    ConfigDict,
    field_validator,
    model_validator,
)

from app.services.monitor_run_status import (
    describe as describe_run_status,
)
from app.services.regulation_monitor_service import (
    REFERENCE_MODE_AUTOMATIC,
    REFERENCE_MODE_MANUAL,
    REFERENCE_SELECTION_MODES,
    TRACKING_PLAN_REGULATION,
    TRACKING_PLAN_WEBSITE,
)


# Desteklenen degerler.
# Scheduler ve monitor pipeline bu degerlere gore calisiyor.

SCOPE_TYPES = (
    "full_page",
    "auto_section",
    "manual_section",
)

# Takip plani degerleri tek yerde tanimli.
# Sema, pipeline ve calistirma yaniti ayni sabitleri kullanir.
TRACKING_PLANS = (
    TRACKING_PLAN_WEBSITE,
    TRACKING_PLAN_REGULATION,
)

SCHEDULE_TYPES = (
    "daily",
    "weekdays",
    "weekly",
)


SCHEDULE_TIME_PATTERN = re.compile(
    r"^([01]\d|2[0-3]):([0-5]\d)$"
)


# Bildirim adresi icin sade bicim kontrolu.
#
# Amac gecerli bir adres oldugunu KANITLAMAK degil;
# acikca bozuk degerlerin (bosluk, @ yok, alan adi yok)
# veritabanina girip gonderim aninda hataya donusmesini
# engellemektir. Yeni bir bagimlilik eklenmez.
EMAIL_PATTERN = re.compile(
    r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$"
)


MAX_EMAIL_LENGTH = 255


# ---------------------------------------------------------
# ORTAK DOGRULAMALAR
#
# MonitorCreate ve MonitorUpdate ayni kurallari kullanir.
# ---------------------------------------------------------

def validate_notification_settings(
    notify_email: bool,
    notification_email: str | None,
) -> None:
    """
    E-mail bildirimi acikken gecerli bir adres zorunludur.

    notify_email kapaliyken adres bos olabilir; eski
    kayitlarda duran adresler bozulmaz.
    """

    if not notify_email:
        return

    cleaned = (notification_email or "").strip()

    if not cleaned:
        raise ValueError(
            "E-mail bildirimi açıksa e-mail adresi gereklidir."
        )

    if len(cleaned) > MAX_EMAIL_LENGTH:
        raise ValueError(
            "E-mail adresi çok uzun."
        )

    if not EMAIL_PATTERN.match(cleaned):
        raise ValueError(
            "Geçerli bir e-mail adresi girin."
        )


def validate_date_range(
    is_unlimited: bool,
    start_date: date | None,
    end_date: date | None,
) -> None:

    if (
        not is_unlimited
        and start_date
        and end_date
        and end_date < start_date
    ):
        raise ValueError(
            "Bitiş tarihi başlangıç tarihinden önce olamaz."
        )


def validate_manual_section_selection(
    scope_type: str | None,
    manual_section_reference: str | None,
    manual_section_label: str | None,
) -> None:
    """
    manual_section kapsaminda bolum secimi ZORUNLUDUR.

    Bolumu kullanici secer; sistem kendi basina bir bolum
    secmez. Bu yuzden secim olmadan manual_section monitoru
    kaydedilemez.

    Frontend zaten secim yapilmadan submit ettirmiyor;
    burasi sunucu tarafi guvencesidir.
    """

    if scope_type != "manual_section":
        return

    if not (manual_section_reference or "").strip():
        raise ValueError(
            "Manuel alan seçildiğinde izlenecek bölüm "
            "seçilmelidir."
        )

    if not (manual_section_label or "").strip():
        raise ValueError(
            "Seçilen bölümün adı eksik. Bölümleri yeniden "
            "getirip tekrar seçin."
        )


def _require_reference_selection_mode(value: str) -> str:

    if value not in REFERENCE_SELECTION_MODES:
        raise ValueError(
            "Geçersiz referans seçim modu. "
            "Desteklenen değerler: "
            f"{', '.join(REFERENCE_SELECTION_MODES)}"
        )

    return value


def validate_regulation_reference_selection(
    tracking_plan: str | None,
    reference_regulation_id: int | None,
    reference_selection_mode: str | None = None,
) -> None:
    """
    Takip plani, secim modu ve referans tutarliligi.

    website_monitor
        referans olmamalidir.

    regulation_monitor + manual
        referans ZORUNLU. Kullanici kendi secimini yapar
        ve bu secim otomatik eslestirmeyle degistirilmez.

    regulation_monitor + automatic
        referans OPSIYONEL. Her calistirmada degisiklik
        metninden belirlenir; kullanilan referans
        comparison satirinda saklanir.

    Frontend zaten bu kurallara gore davraniyor; burasi
    sunucu tarafi guvencesidir. Referansin GERCEKTEN var
    olup olmadigi veritabani gerektirdigi icin burada
    degil, API katmaninda kontrol edilir.
    """

    plan = (tracking_plan or "").strip() or TRACKING_PLAN_WEBSITE

    if plan == TRACKING_PLAN_REGULATION:

        mode = (
            (reference_selection_mode or "").strip()
            or REFERENCE_MODE_MANUAL
        )

        if (
            mode == REFERENCE_MODE_MANUAL
            and reference_regulation_id is None
        ):
            raise ValueError(
                "Manuel referans seçiminde referans "
                "yönetmelik seçilmelidir."
            )

        return

    # website_monitor (ve normalize edilmis eski kayitlar)
    if reference_regulation_id is not None:
        raise ValueError(
            "Standart web sitesi takibinde referans "
            "yönetmelik seçilemez."
        )


def validate_monitor_settings(
    notify_email: bool,
    notification_email: str | None,
    is_unlimited: bool,
    start_date: date | None,
    end_date: date | None,
    scope_type: str | None = None,
    manual_section_reference: str | None = None,
    manual_section_label: str | None = None,
    tracking_plan: str | None = None,
    reference_regulation_id: int | None = None,
    reference_selection_mode: str | None = None,
) -> None:
    """
    Alanlar arasi (cross-field) dogrulamalar.

    Partial update sonrasi olusan BIRLESIK deger kumesi
    icin de ayni kurallar calistirilir.
    """

    validate_notification_settings(
        notify_email=notify_email,
        notification_email=notification_email,
    )

    validate_date_range(
        is_unlimited=is_unlimited,
        start_date=start_date,
        end_date=end_date,
    )

    validate_manual_section_selection(
        scope_type=scope_type,
        manual_section_reference=manual_section_reference,
        manual_section_label=manual_section_label,
    )

    validate_regulation_reference_selection(
        tracking_plan=tracking_plan,
        reference_regulation_id=reference_regulation_id,
        reference_selection_mode=reference_selection_mode,
    )


def _require_non_empty(
    value: str,
    field_label: str,
) -> str:

    cleaned = value.strip()

    if not cleaned:
        raise ValueError(
            f"{field_label} boş olamaz."
        )

    return cleaned


def _require_scope_type(value: str) -> str:

    if value not in SCOPE_TYPES:
        raise ValueError(
            "Geçersiz izleme kapsamı. "
            f"Desteklenen değerler: {', '.join(SCOPE_TYPES)}"
        )

    return value


def _require_tracking_plan(value: str) -> str:

    if value not in TRACKING_PLANS:
        raise ValueError(
            "Geçersiz takip planı. "
            f"Desteklenen değerler: {', '.join(TRACKING_PLANS)}"
        )

    return value


def _require_schedule_type(value: str) -> str:

    if value not in SCHEDULE_TYPES:
        raise ValueError(
            "Geçersiz zamanlama tipi. "
            f"Desteklenen değerler: {', '.join(SCHEDULE_TYPES)}"
        )

    return value


def _require_schedule_time(value) -> str:
    """
    Kontrol saati zorunludur.

    None, bos string veya HH:MM disindaki degerler reddedilir.
    Bos birakilan bir saat scheduler tarafindan
    "schedule_time gecersiz" olarak degerlendirilip monitorun
    hic calismamasina yol acardi.
    """

    if value is None:
        raise ValueError(
            "Kontrol saati zorunludur."
        )

    cleaned = str(value).strip()

    if not cleaned:
        raise ValueError(
            "Kontrol saati zorunludur."
        )

    if not SCHEDULE_TIME_PATTERN.match(cleaned):
        raise ValueError(
            "Kontrol saati HH:MM formatında olmalıdır. "
            "Geçerli aralık: 00:00 - 23:59"
        )

    return cleaned


class MonitorCreate(BaseModel):
    name: str
    url: str
    topic: str

    # Takip plani gonderilmezse mevcut davranis korunur.
    # Eski istemciler bu alani hic bilmeden calismaya
    # devam eder.
    tracking_plan: str = TRACKING_PLAN_WEBSITE

    # REFERANS SECIM MODU
    #
    # Yeni mevzuat izlemelerinde varsayilan OTOMATIK'tir:
    # referans her calistirmada degisiklik metninden
    # belirlenir.
    reference_selection_mode: str = REFERENCE_MODE_AUTOMATIC

    # manual modda ZORUNLU.
    # automatic modda opsiyonel.
    # website_monitor icin gonderilmez veya null gonderilir.
    reference_regulation_id: int | None = None

    scope_type: str = "auto_section"

    schedule_type: str = "daily"

    # Kontrol saati zorunludur.
    # Varsayilan degeri yok; null gonderilirse validator reddeder.
    schedule_time: str | None

    start_date: date | None = None
    end_date: date | None = None
    is_unlimited: bool = True

    notify_in_app: bool = True
    notify_email: bool = False
    notification_email: str | None = None

    # manual_section kapsaminda ZORUNLU; diger kapsamlarda
    # gonderilmez veya null gonderilir.
    manual_section_reference: str | None = None
    manual_section_label: str | None = None

    # Alan bazli kurallar MonitorUpdate ile ayni
    # ortak yardimcilardan gelir.

    @field_validator("name")
    @classmethod
    def check_name(cls, value):
        return _require_non_empty(value, "İzleme adı")

    @field_validator("url")
    @classmethod
    def check_url(cls, value):
        return _require_non_empty(value, "Website URL")

    @field_validator("topic")
    @classmethod
    def check_topic(cls, value):
        return _require_non_empty(value, "Takip konusu")

    @field_validator("tracking_plan")
    @classmethod
    def check_tracking_plan(cls, value):
        return _require_tracking_plan(value)

    @field_validator("reference_selection_mode")
    @classmethod
    def check_reference_selection_mode(cls, value):
        return _require_reference_selection_mode(value)

    @field_validator("scope_type")
    @classmethod
    def check_scope_type(cls, value):
        return _require_scope_type(value)

    @field_validator("schedule_type")
    @classmethod
    def check_schedule_type(cls, value):
        return _require_schedule_type(value)

    @field_validator("schedule_time")
    @classmethod
    def check_schedule_time(cls, value):
        return _require_schedule_time(value)

    @model_validator(mode="after")
    def validate_monitor(self):

        validate_monitor_settings(
            notify_email=self.notify_email,
            notification_email=self.notification_email,
            is_unlimited=self.is_unlimited,
            start_date=self.start_date,
            end_date=self.end_date,
            scope_type=self.scope_type,
            manual_section_reference=(
                self.manual_section_reference
            ),
            manual_section_label=self.manual_section_label,
            tracking_plan=self.tracking_plan,
            reference_regulation_id=(
                self.reference_regulation_id
            ),
            reference_selection_mode=(
                self.reference_selection_mode
            ),
        )

        return self


class MonitorUpdate(BaseModel):
    """
    Partial update.

    Yalnizca gonderilen alanlar guncellenir.
    created_at ve last_checked_at burada tanimli olmadigi ve
    extra="forbid" oldugu icin PATCH ile degistirilemez.
    """

    model_config = ConfigDict(
        extra="forbid",
    )

    name: str | None = None
    url: str | None = None
    topic: str | None = None

    # Takip plani degistirilebilir.
    # Plan degisince referans secimi repository katmaninda
    # temizlenir; ayrintisi orada aciklandi.
    tracking_plan: str | None = None

    # Referans secim modu degistirilebilir.
    reference_selection_mode: str | None = None

    # None burada "gonderilmedi" anlamina gelir.
    # Referansi acikca kaldirmak icin plan website_monitor
    # olarak gonderilir; birlesik dogrulama bunu zaten
    # zorunlu kiliyor.
    reference_regulation_id: int | None = None

    scope_type: str | None = None

    schedule_type: str | None = None

    # None burada "gonderilmedi" anlamina gelir.
    # Acikca null gonderilirse validator reddeder.
    schedule_time: str | None = None

    start_date: date | None = None
    end_date: date | None = None
    is_unlimited: bool | None = None

    notify_in_app: bool | None = None
    notify_email: bool | None = None
    notification_email: str | None = None

    # manual_section secimi kullanici tarafindan degistirilebilir.
    # auto_section secimi ise pipeline'a aittir ve buradan
    # bilerek DEGISTIRILEMEZ (extra="forbid").
    manual_section_reference: str | None = None
    manual_section_label: str | None = None

    is_active: bool | None = None

    @field_validator("name")
    @classmethod
    def check_name(cls, value):

        if value is None:
            return None

        return _require_non_empty(value, "İzleme adı")

    @field_validator("url")
    @classmethod
    def check_url(cls, value):

        if value is None:
            return None

        return _require_non_empty(value, "Website URL")

    @field_validator("topic")
    @classmethod
    def check_topic(cls, value):

        if value is None:
            return None

        return _require_non_empty(value, "Takip konusu")

    @field_validator("tracking_plan")
    @classmethod
    def check_tracking_plan(cls, value):

        if value is None:
            return None

        return _require_tracking_plan(value)

    @field_validator("reference_selection_mode")
    @classmethod
    def check_reference_selection_mode(cls, value):

        if value is None:
            return None

        return _require_reference_selection_mode(value)

    @field_validator("scope_type")
    @classmethod
    def check_scope_type(cls, value):

        if value is None:
            return None

        return _require_scope_type(value)

    @field_validator("schedule_type")
    @classmethod
    def check_schedule_type(cls, value):

        if value is None:
            return None

        return _require_schedule_type(value)

    @field_validator("schedule_time")
    @classmethod
    def check_schedule_time(cls, value):

        return _require_schedule_time(value)


class MonitorResponse(BaseModel):
    id: int

    name: str
    url: str
    topic: str

    # Eski kayitlarda da bu alan dolu gelir; migration
    # tracking_plan'i website_monitor olarak doldurdu.
    tracking_plan: str

    # automatic | manual
    # website_monitor kayitlarinda anlamsizdir.
    reference_selection_mode: str = REFERENCE_MODE_MANUAL

    # manual modda dolu, automatic modda bos olabilir.
    reference_regulation_id: int | None = None

    # Monitor kartinda gosterilen referans adi.
    # Referansin TAM METNI bu yanitta yer almaz.
    reference_regulation_title: str | None = None

    scope_type: str

    # auto_section kapsaminda izlenen bolumun adi.
    # Diger kapsamlarda ve bolum henuz secilmemisken null.
    # Bolum referansinin kendisi dahili bilgidir, donulmez.
    auto_section_label: str | None = None

    # manual_section kapsaminda kullanicinin sectigi bolum.
    # Referans, duzenleme ekraninda secimin korunabilmesi
    # icin donulur.
    manual_section_reference: str | None = None
    manual_section_label: str | None = None

    schedule_type: str
    schedule_time: str | None

    start_date: date | None
    end_date: date | None
    is_unlimited: bool

    notify_in_app: bool
    notify_email: bool
    notification_email: str | None

    is_active: bool

    created_at: datetime

    # Son BASARILI kontrolun zamani.
    last_checked_at: datetime | None

    # ---------------------------------------------------
    # SON CALISTIRMA OZETI
    #
    # Kart "Sonuç" alanini bu alanlardan cizer. Frontend
    # backend durumlarindan kendi mesajini URETMEZ:
    # metin last_run_message, renk last_run_severity.
    #
    # Hic calistirilmamis monitorlerde ucu de None'dir.
    # ---------------------------------------------------

    last_run_status: str | None = None
    last_run_message: str | None = None

    # Son DENEMENIN zamani. Siteye erisilemeyen bir
    # calistirmada last_checked_at DEGISMEZ ama bu alan
    # guncellenir; ikisi ayni sey DEGILDIR.
    last_run_at: datetime | None = None

    # success | info | warning | error
    #
    # Durumdan TUREVDIR, veritabaninda saklanmaz.
    last_run_severity: str | None = None

    # Son DENEME pipeline'i tamamladi mi?
    #
    # False ise last_checked_at ile last_run_at FARKLI
    # calistirmalara aittir: son basarili kontrol eskide
    # kalmis, daha yeni bir deneme basarisiz olmustur.
    #
    # SIDDETTEN BAGIMSIZDIR: ai_failed "warning"dir ama
    # pipeline tamamlandigi icin succeeded=True'dur.
    #
    # Durumdan TUREVDIR, veritabaninda saklanmaz.
    last_run_succeeded: bool | None = None

    model_config = ConfigDict(
        from_attributes=True
    )

    @model_validator(mode="after")
    def fill_run_status_fields(self):
        """
        Siddeti ve basari bilgisini durum adindan tureter.

        Boylece hem renk hem "son deneme gosterilsin mi"
        karari backend'de TEK yerde kalir; frontend durum
        adlarindan kendi listesini KURMAZ.
        """

        info = describe_run_status(
            self.last_run_status
        )

        if info is None:
            return self

        if self.last_run_severity is None:
            self.last_run_severity = info.severity

        if self.last_run_succeeded is None:
            self.last_run_succeeded = info.succeeded

        return self


# ---------------------------------------------------------
# MANUEL BOLUM SECIMI ICIN ADAY LISTESI
#
# Bu semalar YALNIZCA onizleme icindir:
# DB'ye hicbir sey yazilmaz, Gemini cagrilmaz.
# ---------------------------------------------------------

class SectionCandidateRequest(BaseModel):
    url: str

    @field_validator("url")
    @classmethod
    def check_url(cls, value):
        return _require_non_empty(value, "Website URL")


class SectionCandidatePreview(BaseModel):
    candidate_id: str
    label: str
    reference: str
    tag: str

    # Bolumun TAM metin uzunlugu.
    # preview kisaltilmis olsa da bu deger gercek boyutu verir.
    text_length: int

    # Duz metin onizleme.
    # HTML DEGILDIR; frontend bunu metin olarak render eder.
    preview: str


class SectionCandidatesResponse(BaseModel):
    page_title: str
    candidates: list[SectionCandidatePreview]


class MonitorRunResponse(BaseModel):
    monitor_id: int
    monitor_name: str

    tracking_plan: str | None = None

    # MEVZUAT KARSILASTIRMA DURUMU
    #
    # website_monitor    -> None (bu plan icin gecerli degil)
    # regulation_monitor -> gercek durum:
    #
    #   completed            karsilastirma yapildi,
    #                        dogrulanmis madde var
    #   no_match             degisiklik var ama referansta
    #                        guvenilir eslesme yok
    #   failed               teknik / AI hatasi
    #
    #   skipped_first_run    ilk snapshot, karsilastirilacak
    #                        onceki icerik yok
    #   skipped_no_change    icerik degismedi
    #   skipped_irrelevant   degisiklik konuyla ilgisiz
    #   skipped_no_reference referans yonetmelik bagli degil
    #
    # skipped_* degerleri veritabanina YAZILMAZ; bir
    # karsilastirma yapilmadigi icin saklanacak sonuc yoktur.
    regulation_comparison_status: str | None = None

    # Karsilastirma kaydi olustuysa kimligi.
    # Detay ayrica bildirim detay yanitinda da gelir.
    regulation_comparison_id: int | None = None

    # Dogrulanmis madde sayisi.
    # Karsilastirma yapilmadiysa None.
    regulation_changes_count: int | None = None

    # -----------------------------------------------------
    # REFERANS SECIMI
    #
    # website_monitor icin hepsi None.
    # -----------------------------------------------------

    # automatic | manual
    reference_selection_mode: str | None = None

    # matched | ambiguous | no_match | needs_review |
    # no_base_name | no_references
    reference_match_status: str | None = None

    # Karsilastirmada GERCEKTEN kullanilan referans.
    # Otomatik modda matcher'in sectigi kayittir.
    matched_reference_id: int | None = None
    matched_reference_title: str | None = None

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

    # -----------------------------------------------------
    # OTOMATIK REFERANS KESFI
    #
    # Kayitli kutuphanede guvenilir referans BULUNAMADIGINDA
    # mevzuat resmi kaynakta (mevzuat.gov.tr) aranir.
    #
    # website_monitor          -> None
    # manuel mod               -> not_applicable
    # yerel eslestirme tuttu   -> not_needed
    #
    # found_existing   resmi kayit kutuphanede zaten vardi
    # discovered       yeni referans olusturuldu
    # no_match         guvenilir resmi aday yok
    # ambiguous        resmi adaylar ayirt edilemedi
    # discovery_failed arama / metin alma basarisiz
    # -----------------------------------------------------

    reference_discovery_status: str | None = None

    # Kesifle bulunan/olusturulan referans.
    # Kesif bir referans uretmediyse None.
    discovered_reference_id: int | None = None
    discovered_reference_title: str | None = None

    scope_type: str | None = None

    # auto_section kapsaminda bu calistirmada izlenen bolum.
    auto_section_label: str | None = None

    # manual_section kapsaminda izlenen bolum.
    manual_section_label: str | None = None

    first_run: bool
    changed: bool

    similarity_ratio: float | None = None

    topic_relevant: bool | None = None

    ai_relevant: bool | None = None
    ai_importance: str | None = None
    ai_category: str | None = None
    ai_summary: str | None = None

    notification_created: bool = False

    # E-mail bildirimi durumu: sent | failed | None
    # None = bu calistirmada e-mail gerekmedi/gonderilmedi.
    email_status: str | None = None

    # Gonderim basarisizsa TEMIZLENMIS hata mesaji.
    # SMTP kullanici adi/parolasi buraya asla girmez.
    email_error: str | None = None

    ai_error: str | None = None

    last_checked_at: datetime | None = None
