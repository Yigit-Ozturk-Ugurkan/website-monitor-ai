"""
MEVZUAT BILGI SISTEMI RESMI ARAMASI

Bir mevzuat ADINDAN mevzuat.gov.tr'deki RESMI kaydi bulur.

NEDEN GEREKLI

Otomatik referans eslestirme yalnizca KAYITLI
RegulationReference kutuphanesine bakiyordu. Kullanici bir
mevzuati onceden elle tanimlamadiysa pipeline

    no_match / no_references

ile duruyordu. Bu modul o bosluğu resmi kaynagin KENDI
arama altyapisiyla kapatir.

TESPIT EDILEN GERCEK ENDPOINT

Kullanici mevzuat.gov.tr ana sayfasindaki sekmelerden birine
(orn. "Yönetmelikler") ad yazip aradiginda tarayici su
istegi yapar:

    POST https://www.mevzuat.gov.tr/Anasayfa/MevzuatDatatable
    Content-Type: application/json; charset=utf-8

Govde bir DataTables server-side istegidir; form alanlari
"parameters" nesnesi icinde tasinir:

    {
      "draw": 1, "columns": [], "order": [],
      "start": 0, "length": 10,
      "search": {"value": "", "regex": false},
      "parameters": {
        "MevzuatTur": "TumYonetmelik",
        "YonetmelikMevzuatTur": "TumYonetmelik",
        "AranacakIfade": "<aranan ad>",
        "AranacakYer": "2",          # 2 = Baslik
        "TamCumle": "false"
      }
    }

Bu, javascript.js icindeki

    "ajax": { url: window.av.searchFormDataTableUrl,
              type: "POST",
              contentType: "application/json; charset=utf-8",
              data: ... d.parameters =
                    $("#<form>_form").objectifyForm() ... }

kurulumundan okunmus ve GERCEK cevapla dogrulanmistir.

OLCULEN DAVRANIS

  - antiforgery token GEREKMIYOR
  - cerez / oturum GEREKMIYOR
  - cevap application/json, UTF-8
  - AranacakYer gonderilmezse TUM mevzuat doner (9031 kayit)
  - AranacakIfade bos ise TUM mevzuat doner
  - gecersiz MevzuatTur -> HTTP 600 "FormValidate"
  - kismi eslesmede mevAdi icinde
    <span style='background-color:yellow'> vurgusu gelir
  - mevAdi icinde \\r\\n bulunabilir

BU MODUL

  - Gemini CAGIRMAZ
  - veritabanina DOKUNMAZ
  - genel arama motoru (Google/Bing) KULLANMAZ
  - yalnizca resmi mevzuat.gov.tr'ye gider

Ham HTML/JSON disariya verilmez; yalnizca dogrulanmis
structured aday listesi doner.
"""

import html
import json
import logging
import re

from dataclasses import dataclass
from datetime import date

import httpx

from app.core.ssl_context import get_verification_context
from app.services.regulation_source_registry import (
    NUMERIC_PATTERN,
    SOURCE_TYPE_MEVZUAT_GOV_TR,
    build_mevzuat_shell_url,
)
from app.services.regulation_title_parser import (
    REGULATION_KIND_KANUN,
    REGULATION_KIND_KARARNAME,
    REGULATION_KIND_TEBLIG,
    REGULATION_KIND_YONETMELIK,
)
from app.services.web_fetcher import (
    DEFAULT_HEADERS,
    build_timeout,
)


logger = logging.getLogger(__name__)


class MevzuatSearchError(Exception):
    """
    Resmi arama tamamlanamadi.

    Pipeline'i DUSURMEZ; ust katman discovery_failed olarak
    devam eder ve UYDURMA referans olusturmaz.
    """


# ---------------------------------------------------------
# ENDPOINT
# ---------------------------------------------------------

MEVZUAT_SEARCH_URL = (
    "https://www.mevzuat.gov.tr/Anasayfa/MevzuatDatatable"
)


# AranacakYer degerleri (formdaki <select> secenekleri).
#
# Ad aramasi yaptigimiz icin DAIMA "Baslik" kullanilir;
# icerik aramasi konusu gecen her mevzuati dondurur ve
# eslestirme icin gurultudur.
SEARCH_SCOPE_TITLE = "2"
SEARCH_SCOPE_CONTENT = "3"
SEARCH_SCOPE_ALL = "1"


# Tek sayfada istenen en fazla kayit.
#
# Ad aramasi genelde tek haneli sonuc verir; sayfalama
# yapmiyoruz cunku 20'den fazla adayi olan bir ad zaten
# guvenle secilemez ve ambiguous'a duser.
SEARCH_PAGE_SIZE = 20


# Aday sayisi bunu asarsa ad yeterince ayirt edici degildir.
MAX_CANDIDATES = 20


# ---------------------------------------------------------
# ARAMA PROFILLERI
#
# Her mevzuat turu ana sayfada AYRI bir form ile aranir ve
# formun gizli MevzuatTur degeri farklidir. Degerler
# home page HTML'inden okunmustur; tahmin YOKTUR.
#
# Gecersiz bir MevzuatTur endpoint'ten HTTP 600
# "FormValidate" dondurur, bu yuzden yalnizca gozlemlenmis
# degerler kullanilir.
# ---------------------------------------------------------

@dataclass(frozen=True)
class SearchProfile:
    """
    Bir mevzuat turu icin form parametreleri.

    mevzuat_turler: bu profilin dondurmesi BEKLENEN sayisal
                    MevzuatTur degerleri. Cevapta baska bir
                    tur gelirse aday ELENIR; boylece bir
                    "yönetmelik" aramasi tebliğ dondurup
                    yanlis referans uretemez.
    """

    mevzuat_tur: str
    yonetmelik_mevzuat_tur: str
    mevzuat_turler: frozenset[int]


# Sayisal MevzuatTur degerleri (javascript.js av.enums).
TUR_KANUN = 1
TUR_TUZUK = 2
TUR_BKK_YONETMELIK = 3
TUR_KHK = 4
TUR_KURUM_YONETMELIK = 7
TUR_UNIVERSITE_YONETMELIK = 8
TUR_TEBLIG = 9
TUR_BKK_YONETMELIGI = 10
TUR_CB_KARARNAME = 19
TUR_CB_KARARI = 20
TUR_CB_YONETMELIK = 21


SEARCH_PROFILES: dict[str, SearchProfile] = {
    REGULATION_KIND_YONETMELIK: SearchProfile(
        mevzuat_tur="TumYonetmelik",
        yonetmelik_mevzuat_tur="TumYonetmelik",
        mevzuat_turler=frozenset(
            {
                TUR_BKK_YONETMELIK,
                TUR_KURUM_YONETMELIK,
                TUR_UNIVERSITE_YONETMELIK,
                TUR_BKK_YONETMELIGI,
                TUR_CB_YONETMELIK,
            }
        ),
    ),
    REGULATION_KIND_TEBLIG: SearchProfile(
        mevzuat_tur="Teblig",
        yonetmelik_mevzuat_tur="OsmanliKanunu",
        mevzuat_turler=frozenset({TUR_TEBLIG}),
    ),
    REGULATION_KIND_KANUN: SearchProfile(
        mevzuat_tur="Kanun",
        yonetmelik_mevzuat_tur="OsmanliKanunu",
        mevzuat_turler=frozenset({TUR_KANUN}),
    ),
    REGULATION_KIND_KARARNAME: SearchProfile(
        mevzuat_tur="CumhurbaskaniKararnameleri",
        yonetmelik_mevzuat_tur="OsmanliKanunu",
        mevzuat_turler=frozenset({TUR_CB_KARARNAME}),
    ),
}


# ---------------------------------------------------------
# ADAY
# ---------------------------------------------------------

# Arama sonucundaki vurgu isaretlemesi.
# Kismi eslesmede mevAdi HTML tasir; ad karsilastirmasindan
# ONCE temizlenmelidir.
TAG_PATTERN = re.compile(r"<[^>]+>")

# "31676" veya "25983 (Mükerrer)"
ISSUE_PATTERN = re.compile(r"^(\d{3,6})")

# "01.12.2021"
GAZETTE_DATE_PATTERN = re.compile(
    r"^(\d{1,2})\.(\d{1,2})\.(\d{4})$"
)


@dataclass(frozen=True)
class MevzuatCandidate:
    """
    Resmi arama sonucundan gelen DOGRULANMIS bir kayit.

    Ham JSON disariya cikmaz; yalnizca bu alanlar tasinir.

    mevzuat_no / mevzuat_tur / mevzuat_tertip degerleri
    yalnizca RAKAM icerir; adres kurulmadan once
    dogrulanmislardir.
    """

    mevzuat_no: str
    mevzuat_tur: str
    mevzuat_tertip: str

    # Resmi kaynagin kendi verdigi ad.
    # KANONIK isimlendirme kaynagi budur.
    title: str

    # Kanonik mevzuat.gov.tr kabuk adresi.
    canonical_url: str

    source_type: str = SOURCE_TYPE_MEVZUAT_GOV_TR

    # Yayim bilgisi (cevapta varsa).
    published_on: date | None = None
    official_gazette_issue: str | None = None

    # "Kurum ve Kuruluş Yönetmelikleri" gibi tur etiketi.
    kind_label: str | None = None

    @property
    def candidate_id(self) -> str:
        """
        Aday icin kararli, tahmin edilemez olmayan kimlik.

        AI ayrimi bu degeri dondurur ve sunucu tarafinda
        allow-list ile dogrulanir.
        """

        return (
            f"{self.mevzuat_tur}-"
            f"{self.mevzuat_no}-"
            f"{self.mevzuat_tertip}"
        )


def clean_official_title(raw: str | None) -> str:
    """
    Arama sonucundaki adi temizler.

    Yapilanlar:
      - vurgu <span> etiketleri kaldirilir
      - HTML varliklari cozulur
      - \\r\\n ve fazla bosluk tek bosluga indirilir

    Bu temizlik ad karsilastirmasi icin ZORUNLUDUR;
    aksi halde kismi eslesmede gelen HTML kelimelerin
    icine karisir.
    """

    text = TAG_PATTERN.sub(" ", raw or "")

    text = html.unescape(text)

    return " ".join(text.split())


def _parse_gazette_date(value: str | None) -> date | None:
    """
    "01.12.2021" -> date(2021, 12, 1)

    Taninmayan bicim None doner; uydurma tarih URETILMEZ.
    """

    match = GAZETTE_DATE_PATTERN.match((value or "").strip())

    if match is None:
        return None

    try:
        return date(
            int(match.group(3)),
            int(match.group(2)),
            int(match.group(1)),
        )

    except ValueError:
        return None


def _parse_issue(value: str | None) -> str | None:
    """
    Resmî Gazete sayisini cikarir.

    "25983 (Mükerrer)" -> "25983"
    """

    match = ISSUE_PATTERN.match((value or "").strip())

    if match is None:
        return None

    return match.group(1)


def _as_numeric_text(value) -> str | None:
    """
    Cevaptan gelen bir degeri SAYISAL METNE cevirir.

    Cevap alanlari bazen metin ("39080"), bazen sayi
    (7) olarak geliyor. Her iki durumda da sonuc yalnizca
    rakamlardan olusmalidir.

    Rakam disi her deger None doner ve aday ELENIR.
    Kullanicidan/uzaktan gelen deger adres icine OLDUGU
    GIBI tasinmaz.
    """

    if value is None:
        return None

    if isinstance(value, bool):
        return None

    if isinstance(value, int):
        text = str(value)

    elif isinstance(value, str):
        text = value.strip()

    else:
        return None

    if not NUMERIC_PATTERN.match(text):
        return None

    return text


def build_candidate(
    row: dict,
    allowed_turler: frozenset[int] | None = None,
) -> MevzuatCandidate | None:
    """
    Ham arama satirini DOGRULANMIS adaya cevirir.

    Dogrulanamayan her satir None doner ve atilir:

      - MevzuatNo sayisal degilse
      - MevzuatTur sayisal degilse
      - MevzuatTertip sayisal degilse
      - ad bos ise
      - tur, aranan profilin disindaysa

    Bu, mevcut konsolide adres cozumleyicisinin guvenlik
    kuralinin (yalnizca rakam) arama tarafinda da
    uygulanmasidir.
    """

    if not isinstance(row, dict):
        return None

    no = _as_numeric_text(
        row.get("mevzuatNo", row.get("MevzuatNo"))
    )
    tur = _as_numeric_text(
        row.get("mevzuatTur", row.get("MevzuatTur"))
    )
    tertip = _as_numeric_text(
        row.get("mevzuatTertip", row.get("MevzuatTertip"))
    )

    if no is None or tur is None or tertip is None:
        logger.debug(
            "Resmi arama sonucu reddedildi: sayisal olmayan "
            "kimlik. no=%r tur=%r tertip=%r",
            row.get("mevzuatNo"),
            row.get("mevzuatTur"),
            row.get("mevzuatTertip"),
        )

        return None

    if (
        allowed_turler is not None
        and int(tur) not in allowed_turler
    ):
        # Aranan turden BASKA bir mevzuat turu geldi.
        # Bir yonetmelik aramasi tebliğ dondurup yanlis
        # referans uretemez.
        return None

    title = clean_official_title(
        row.get("mevAdi", row.get("MevAdi"))
    )

    if not title:
        return None

    try:
        canonical_url = build_mevzuat_shell_url(
            tur=tur,
            no=no,
            tertip=tertip,
        )

    except ValueError:
        # build_mevzuat_shell_url ikinci savunma katmanidir.
        return None

    return MevzuatCandidate(
        mevzuat_no=no,
        mevzuat_tur=tur,
        mevzuat_tertip=tertip,
        title=title,
        canonical_url=canonical_url,
        published_on=_parse_gazette_date(
            row.get(
                "resmiGazeteTarihi",
                row.get("ResmiGazeteTarihi"),
            )
        ),
        official_gazette_issue=_parse_issue(
            row.get(
                "resmiGazeteSayisi",
                row.get("ResmiGazeteSayisi"),
            )
        ),
        kind_label=clean_official_title(
            row.get(
                "mevzuatTurEnumString",
                row.get("MevzuatTurEnumString"),
            )
        )
        or None,
    )


def build_search_payload(
    query: str,
    profile: SearchProfile,
    length: int = SEARCH_PAGE_SIZE,
) -> dict:
    """
    DataTables server-side istek govdesini kurar.

    AranacakYer DAIMA gonderilir: gonderilmezse endpoint
    tum mevzuati donduruyor.
    """

    return {
        "draw": 1,
        "columns": [],
        "order": [],
        "start": 0,
        "length": length,
        "search": {"value": "", "regex": False},
        "parameters": {
            "MevzuatTur": profile.mevzuat_tur,
            "YonetmelikMevzuatTur": (
                profile.yonetmelik_mevzuat_tur
            ),
            "AranacakIfade": query,
            "AranacakYer": SEARCH_SCOPE_TITLE,
            "TamCumle": "false",
        },
    }


def _search_headers() -> dict:
    """
    Istek basliklari.

    Mevcut web_fetcher basliklari yeniden kullanilir;
    yalnizca JSON icin gerekli olanlar eklenir.
    """

    headers = dict(DEFAULT_HEADERS)

    headers["Content-Type"] = "application/json; charset=utf-8"
    headers["Accept"] = (
        "application/json, text/javascript, */*; q=0.01"
    )
    headers["X-Requested-With"] = "XMLHttpRequest"
    headers["Referer"] = "https://www.mevzuat.gov.tr/"

    return headers


async def _post_search(payload: dict) -> dict:
    """
    Arama istegini gonderir ve JSON govdeyi cozer.

    Her basarisizlik MevzuatSearchError'a donusturulur;
    ust katman tek bir hata turuyle calisir ve scheduler
    HICBIR durumda cokmez.
    """

    try:
        async with httpx.AsyncClient(
            timeout=build_timeout(),
            follow_redirects=True,
            headers=_search_headers(),

            # Sertifika DOGRULAMASI ACIK.
            verify=get_verification_context(),
        ) as client:
            response = await client.post(
                MEVZUAT_SEARCH_URL,
                content=json.dumps(
                    payload,
                    ensure_ascii=False,
                ).encode("utf-8"),
            )

    except httpx.TimeoutException as exc:
        raise MevzuatSearchError(
            "Mevzuat Bilgi Sistemi araması zaman aşımına "
            "uğradı."
        ) from exc

    except httpx.RequestError as exc:
        raise MevzuatSearchError(
            "Mevzuat Bilgi Sistemi'ne bağlanılamadı."
        ) from exc

    # NOT: endpoint gecersiz parametrede standart olmayan
    # HTTP 600 "FormValidate" donduruyor. 2xx disindaki her
    # kod hata sayilir.
    if response.status_code >= 300:
        raise MevzuatSearchError(
            "Mevzuat Bilgi Sistemi araması beklenmeyen bir "
            f"yanıt döndürdü (HTTP {response.status_code})."
        )

    content_type = (
        response.headers.get("content-type") or ""
    ).lower()

    if "json" not in content_type:
        raise MevzuatSearchError(
            "Mevzuat Bilgi Sistemi araması JSON yerine "
            f"beklenmeyen bir içerik döndürdü "
            f"({content_type or 'bilinmiyor'})."
        )

    try:
        data = json.loads(
            response.content.decode("utf-8")
        )

    except (UnicodeDecodeError, ValueError) as exc:
        raise MevzuatSearchError(
            "Mevzuat Bilgi Sistemi araması okunamayan bir "
            "yanıt döndürdü."
        ) from exc

    if not isinstance(data, dict):
        raise MevzuatSearchError(
            "Mevzuat Bilgi Sistemi araması beklenen biçimde "
            "bir yanıt döndürmedi."
        )

    return data


@dataclass
class MevzuatSearchResult:
    """
    Bir arama denemesinin structured sonucu.

    candidates: DOGRULANMIS adaylar. Ham JSON tasinmaz.
    """

    query: str
    candidates: list[MevzuatCandidate]

    # Kaynagin bildirdigi toplam kayit sayisi.
    # Adaylardan fazla olabilir (sayfalama / eleme).
    total_records: int = 0

    # Aday sayisi tavani asti mi?
    # Bu durumda ad yeterince ayirt edici degildir.
    truncated: bool = False


async def search_official_regulations(
    query: str,
    kind: str,
) -> MevzuatSearchResult:
    """
    Mevzuat adiyla RESMI arama yapar.

    query : base mevzuat adi
            (regulation_title_parser ciktisi)

    kind  : yönetmelik | tebliğ | kararname | kanun

    Doner: dogrulanmis adaylar.

    Bos sorgu ile ARAMA YAPILMAZ: endpoint bos ifadede tum
    mevzuati donduruyor ve bu, rastgele bir kaydin referans
    olarak secilmesine yol acardi.
    """

    cleaned = " ".join((query or "").split())

    if not cleaned:
        raise MevzuatSearchError(
            "Arama için mevzuat adı gerekli."
        )

    profile = SEARCH_PROFILES.get(kind)

    if profile is None:
        raise MevzuatSearchError(
            f"'{kind}' türü için resmî arama profili yok."
        )

    data = await _post_search(
        build_search_payload(
            query=cleaned,
            profile=profile,
        )
    )

    rows = data.get("data")

    if rows is None:
        rows = data.get("Data")

    if rows is None:
        rows = []

    if not isinstance(rows, list):
        raise MevzuatSearchError(
            "Mevzuat Bilgi Sistemi araması beklenen liste "
            "biçiminde sonuç döndürmedi."
        )

    candidates: list[MevzuatCandidate] = []

    seen: set[str] = set()

    for row in rows:
        candidate = build_candidate(
            row=row,
            allowed_turler=profile.mevzuat_turler,
        )

        if candidate is None:
            continue

        if candidate.canonical_url in seen:
            continue

        seen.add(candidate.canonical_url)

        candidates.append(candidate)

    total = data.get("recordsTotal")

    if not isinstance(total, int):
        total = data.get("RecordsTotal")

    if not isinstance(total, int):
        total = len(candidates)

    logger.info(
        "Resmi mevzuat aramasi. tur=%s sorgu=%r "
        "toplam=%s aday=%s",
        kind,
        cleaned[:120],
        total,
        len(candidates),
    )

    return MevzuatSearchResult(
        query=cleaned,
        candidates=candidates[:MAX_CANDIDATES],
        total_records=total,
        truncated=len(candidates) > MAX_CANDIDATES,
    )
