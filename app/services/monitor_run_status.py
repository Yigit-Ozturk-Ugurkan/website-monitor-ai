"""
SON CALISTIRMA DURUMU

Bir monitor calistirmasinin sonucunu TEK bir deterministik
duruma ve kullaniciya gosterilecek TEK bir cumleye cevirir.

NEDEN TEK YER

Manuel calistirma (POST /api/monitors/{id}/run) ve
scheduler ayni pipeline'i kullanir. Durum esleme mantigi
burada TEK KOPYA tutulur; iki yol da bu modulu cagirir ve
ayni girdi icin ayni durumu uretir.

Frontend bu alanlari OLDUGU GIBI gosterir. Backend
durumlarindan kendi basina mesaj uretmez.

DURUM != SON BASARILI KONTROL

  last_checked_at : son BASARILI kontrolun zamani
  last_run_at     : son DENEMENIN zamani

Siteye erisilemediginde last_checked_at DEGISMEZ (scheduler
retry davranisi buna baglidir), ama last_run_at ve
last_run_status guncellenir. Boylece kart "en son ne oldu"
sorusunu dogru cevaplar.
"""

from dataclasses import dataclass


# ---------------------------------------------------------
# SIDDET
#
# Frontend renk secimini bu degere gore yapar; durum
# adlarina gore kendi tablosunu KURMAZ.
# ---------------------------------------------------------

SEVERITY_SUCCESS = "success"
SEVERITY_INFO = "info"
SEVERITY_WARNING = "warning"
SEVERITY_ERROR = "error"


# ---------------------------------------------------------
# DURUMLAR
# ---------------------------------------------------------

# Ilk snapshot alindi; karsilastirilacak onceki icerik yok.
STATUS_FIRST_RUN = "first_run"

# Kontrol basarili, icerik degismemis.
STATUS_NO_CHANGE = "no_change"

# Degisiklik var ve takip konusuyla ilgili.
STATUS_CHANGE_DETECTED = "change_detected"

# Degisiklik var ama AI konuyla ilgisiz buldu.
STATUS_CHANGE_IRRELEVANT = "change_irrelevant"

# Mevzuat karsilastirmasi tamamlandi ve dogrulanmis
# madde(ler) uretti.
STATUS_COMPARISON_COMPLETED = "comparison_completed"

# Karsilastirma calisti ama dogrulanmis madde cikmadi.
STATUS_COMPARISON_NO_MATCH = "comparison_no_match"

# Karsilastirma teknik olarak basarisiz oldu.
STATUS_COMPARISON_FAILED = "comparison_failed"

# Guvenilir referans mevzuat belirlenemedi.
STATUS_REFERENCE_NOT_FOUND = "reference_not_found"

# Birden fazla aday ayirt edilemedi; manuel dogrulama
# gerekiyor.
STATUS_REFERENCE_AMBIGUOUS = "reference_ambiguous"

# Resmi kaynakta arama/metin alma basarisiz oldu.
STATUS_DISCOVERY_FAILED = "discovery_failed"

# Referans bulundu ama guncel metni dogrulanamadi.
STATUS_REFERENCE_UNAVAILABLE = "reference_unavailable"

# Degisiklik kaydedildi ama AI analizi alinamadi.
STATUS_AI_FAILED = "ai_failed"

# Siteye erisilemedi.
STATUS_FETCH_FAILED = "fetch_failed"

# Izlenecek bolum belirlenemedi.
STATUS_SECTION_FAILED = "section_failed"

# Beklenmeyen hata.
STATUS_RUN_FAILED = "run_failed"


@dataclass(frozen=True)
class RunStatusInfo:
    status: str
    message: str
    severity: str

    # Calistirma pipeline'i TAMAMLANDI mi?
    #
    # SIDDET ILE KARISTIRILMAMALIDIR: ai_failed bir
    # "warning"dir ama pipeline tamamlanmistir ve
    # last_checked_at ilerlemistir.
    succeeded: bool = True


# ---------------------------------------------------------
# PIPELINE TAMAMLANMAYAN DURUMLAR
#
# Bunlar YALNIZCA resolve_failure_status() tarafindan,
# yani istisna yolundan uretilir. Pipeline snapshot
# asamasina ULASAMAMIS demektir; bu yuzden:
#
#   last_checked_at ILERLEMEZ
#   last_run_at     ilerler
#
# Diger butun durumlarda calistirma tamamlanmistir ve iki
# zaman damgasi da ayni calistirmaya aittir.
#
# Bu kume elle bakimi yapilan bir liste DEGILDIR:
# resolve_failure_status'un donebilecegi tum degerler
# budur ve test bunu dogrular.
# ---------------------------------------------------------

FAILURE_STATUSES = frozenset(
    {
        "fetch_failed",
        "section_failed",
        "run_failed",
    }
)


def attempt_succeeded(status: str | None) -> bool:
    """
    Bu durum, tamamlanmis bir calistirmayi mi anlatiyor?

    Taninmayan veya bos durum icin True doner: elimizde
    basarisizlik KANITI yoktur, uydurulmaz.
    """

    if not status:
        return True

    return status not in FAILURE_STATUSES


# Durum -> (mesaj, siddet)
#
# Mesajlar KULLANICI icindir: teknik terim, durum adi veya
# istisna metni tasimazlar.
STATUS_TABLE: dict[str, tuple[str, str]] = {
    STATUS_FIRST_RUN: (
        "İlk snapshot oluşturuldu",
        SEVERITY_INFO,
    ),
    STATUS_NO_CHANGE: (
        "Kontrol başarılı — değişiklik tespit edilmedi",
        SEVERITY_SUCCESS,
    ),
    STATUS_CHANGE_DETECTED: (
        "Değişiklik tespit edildi",
        SEVERITY_SUCCESS,
    ),
    STATUS_CHANGE_IRRELEVANT: (
        "Değişiklik tespit edildi ancak takip konusuyla "
        "ilgili bulunmadı",
        SEVERITY_INFO,
    ),
    STATUS_COMPARISON_COMPLETED: (
        "Mevzuat değişikliği bulundu — karşılaştırma "
        "tamamlandı",
        SEVERITY_SUCCESS,
    ),
    STATUS_COMPARISON_NO_MATCH: (
        "Değişiklik bulundu ancak referans mevzuatta "
        "karşılığı belirlenemedi",
        SEVERITY_WARNING,
    ),
    STATUS_COMPARISON_FAILED: (
        "Değişiklik bulundu ancak karşılaştırma "
        "tamamlanamadı",
        SEVERITY_WARNING,
    ),
    STATUS_REFERENCE_NOT_FOUND: (
        "Değişiklik bulundu ancak referans mevzuat "
        "bulunamadı",
        SEVERITY_WARNING,
    ),
    STATUS_REFERENCE_AMBIGUOUS: (
        "Değişiklik bulundu — referans mevzuat için manuel "
        "doğrulama gerekiyor",
        SEVERITY_WARNING,
    ),
    STATUS_DISCOVERY_FAILED: (
        "Değişiklik bulundu ancak resmî referans metin "
        "alınamadı",
        SEVERITY_WARNING,
    ),
    STATUS_REFERENCE_UNAVAILABLE: (
        "Değişiklik bulundu ancak referans mevzuatın güncel "
        "metni doğrulanamadı",
        SEVERITY_WARNING,
    ),
    STATUS_AI_FAILED: (
        "Değişiklik kaydedildi ancak yapay zeka analizi "
        "alınamadı",
        SEVERITY_WARNING,
    ),
    STATUS_FETCH_FAILED: (
        "Son kontrol başarısız — siteye erişilemedi",
        SEVERITY_ERROR,
    ),
    STATUS_SECTION_FAILED: (
        "Son kontrol başarısız — izlenecek bölüm "
        "belirlenemedi",
        SEVERITY_ERROR,
    ),
    STATUS_RUN_FAILED: (
        "Son kontrol başarısız — beklenmeyen bir hata oluştu",
        SEVERITY_ERROR,
    ),
}


RUN_STATUSES = tuple(STATUS_TABLE)


def describe(status: str | None) -> RunStatusInfo | None:
    """
    Durum adini mesaj ve siddete cevirir.

    Taninmayan durum icin None doner; uydurma bir mesaj
    URETILMEZ.
    """

    if not status:
        return None

    entry = STATUS_TABLE.get(status)

    if entry is None:
        return None

    message, severity = entry

    return RunStatusInfo(
        status=status,
        message=message,
        severity=severity,
        succeeded=attempt_succeeded(status),
    )


def resolve_failure_status(error: BaseException) -> str:
    """
    Pipeline'dan yukselen istisnayi duruma cevirir.

    Istisna METNI kullaniciya gosterilmez; yalnizca turu
    duruma eslenir.
    """

    # Gec import: modul dongusel bagimlilik kurmasin.
    from app.services.section_selector import (
        AutoSectionSelectionError,
    )
    from app.services.db_monitor_service import (
        ManualSectionResolutionError,
    )
    from app.services.web_fetcher import WebsiteFetchError

    if isinstance(error, WebsiteFetchError):
        return STATUS_FETCH_FAILED

    if isinstance(
        error,
        (AutoSectionSelectionError, ManualSectionResolutionError),
    ):
        return STATUS_SECTION_FAILED

    return STATUS_RUN_FAILED


def resolve_run_status(
    result,
    regulation_comparison=None,
) -> str:
    """
    Basariyla tamamlanmis bir calistirmayi duruma cevirir.

    result                : DatabaseMonitorResult
    regulation_comparison : RegulationComparisonRunResult | None

    Karar sirasi ONEMLIDIR: once calistirmanin kendisi
    (ilk snapshot / degisiklik yok), sonra mevzuat
    karsilastirmasinin sonucu.
    """

    if result.first_run:
        return STATUS_FIRST_RUN

    if not result.changed:
        return STATUS_NO_CHANGE

    # Buradan sonrasi GERCEK bir degisiklik.

    if result.ai_analysis_record is None:
        # AI cagrisi basarisiz oldu; snapshot ve change
        # kayitlari korundu.
        return STATUS_AI_FAILED

    if not result.ai_analysis_record.relevant:
        return STATUS_CHANGE_IRRELEVANT

    status = _comparison_status(regulation_comparison)

    if status is not None:
        return status

    return STATUS_CHANGE_DETECTED


def _comparison_status(regulation_comparison) -> str | None:
    """
    Mevzuat karsilastirmasinin sonucunu duruma cevirir.

    website_monitor icin (veya karsilastirma hic
    calismadiysa) None doner ve genel degisiklik durumu
    kullanilir.
    """

    if regulation_comparison is None:
        return None

    # Gec import: bu modul servis katmanindan once
    # yuklenebilmeli.
    from app.services.regulation_discovery_service import (
        DISCOVERY_AMBIGUOUS,
        DISCOVERY_FAILED,
    )
    from app.services.regulation_monitor_service import (
        COMPARISON_COMPLETED,
        COMPARISON_FAILED,
        COMPARISON_NO_MATCH,
        COMPARISON_SKIPPED_NO_AUTO_MATCH,
        COMPARISON_SKIPPED_NO_REFERENCE,
        COMPARISON_SKIPPED_STALE_REFERENCE,
    )
    from app.services.regulation_reference_matcher import (
        MATCH_STATUS_AMBIGUOUS,
        MATCH_STATUS_NEEDS_REVIEW,
    )

    status = regulation_comparison.status

    # website_monitor: karsilastirma soz konusu degil.
    if status is None:
        return None

    if status == COMPARISON_COMPLETED:
        return STATUS_COMPARISON_COMPLETED

    if status == COMPARISON_NO_MATCH:
        return STATUS_COMPARISON_NO_MATCH

    if status == COMPARISON_FAILED:
        return STATUS_COMPARISON_FAILED

    if status == COMPARISON_SKIPPED_STALE_REFERENCE:
        return STATUS_REFERENCE_UNAVAILABLE

    if status == COMPARISON_SKIPPED_NO_REFERENCE:
        return STATUS_REFERENCE_NOT_FOUND

    if status == COMPARISON_SKIPPED_NO_AUTO_MATCH:

        # NEDEN referans belirlenemedigi kullaniciya
        # farkli bir eylem onerir; bu yuzden ayrilir.
        discovery_status = getattr(
            regulation_comparison,
            "discovery_status",
            None,
        )

        if discovery_status == DISCOVERY_FAILED:
            return STATUS_DISCOVERY_FAILED

        if discovery_status == DISCOVERY_AMBIGUOUS:
            return STATUS_REFERENCE_AMBIGUOUS

        match_status = getattr(
            regulation_comparison,
            "match_status",
            None,
        )

        if match_status in (
            MATCH_STATUS_AMBIGUOUS,
            MATCH_STATUS_NEEDS_REVIEW,
        ):
            return STATUS_REFERENCE_AMBIGUOUS

        return STATUS_REFERENCE_NOT_FOUND

    # skipped_first_run / skipped_no_change /
    # skipped_irrelevant durumlarina bu noktada
    # ulasilmaz: ust katman onlari zaten ayirdi.
    return None
