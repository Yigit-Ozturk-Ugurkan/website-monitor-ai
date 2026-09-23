import codecs
import re
import ssl

from dataclasses import dataclass

import httpx

from app.core.ssl_context import get_verification_context


# Bazi kamu siteleri yavas cevap veriyor. Baglanti kurmak
# hizli olmali ama govde okumasina daha genis sure taniyoruz;
# aksi halde erisilebilir bir kaynak "zaman asimi" gibi
# gorunuyor.
DEFAULT_CONNECT_TIMEOUT_SECONDS = 15.0

DEFAULT_READ_TIMEOUT_SECONDS = 45.0

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/151.0.0.0 Safari/537.36"
    ),

    # Bazi kamu siteleri Accept/Accept-Language basligi
    # olmayan istekleri farkli veya eksik icerikle
    # cevapliyor.
    "Accept": (
        "text/html,application/xhtml+xml,"
        "application/xml;q=0.9,*/*;q=0.8"
    ),
    "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.8",
}


def build_timeout() -> httpx.Timeout:
    return httpx.Timeout(
        DEFAULT_READ_TIMEOUT_SECONDS,
        connect=DEFAULT_CONNECT_TIMEOUT_SECONDS,
    )


class WebsiteFetchError(Exception):
    """Website içeriği alınamadığında oluşturulan hata."""


# ---------------------------------------------------------
# KARAKTER KODLAMASI
#
# NEDEN GEREKLI
#
# Resmî Gazete belge sayfalari (/eskiler/.../*.htm) Word'den
# disa aktarilmis HTML'dir ve windows-1254 (Turkce) ile
# kodlanmistir. Kodlamayi yalnizca HTML icindeki
#
#   <meta http-equiv=content-type
#         content="text/html; charset=windows-1254">
#
# etiketiyle bildirirler; HTTP Content-Type basliginda
# charset YOKTUR.
#
# httpx bu durumda UTF-8 varsayar ve Turkce harfler
# U+FFFD (replacement character) olur. Olculen kayip:
# tek bir sayfada 21.603 karakter.
#
# Bu, "GEÇİCİ" kelimesini "GE��C�" haline
# getirdigi icin madde basliklari ve kanit dogrulamasi
# GERCEK VERIDE bozuluyordu.
#
# COZUM
#
# Tarayicilarin yaptigi sirayla kodlama belirlenir:
#
#   1. BOM
#   2. HTTP Content-Type basligindaki charset
#   3. HTML icindeki <meta charset> / http-equiv
#   4. kati UTF-8 denemesi (sorunsuz cozuluyorsa UTF-8'dir)
#   5. son care: kayipsiz olmayan cozum yerine acik uyari
#
# Bu Turkiye'ye ozel bir yama DEGILDIR; eski kodlamayla
# yayimlanan her sayfa icin dogru davranistir.
# ---------------------------------------------------------

# Kodlama etiketi HTML'in bas kismindan okunur.
# HTML5 ilk 1024 bayti tarar; Word ciktisi HTML'lerde bas
# kisim uzun oldugu icin biraz daha genis bakiyoruz.
META_CHARSET_SCAN_BYTES = 8192

META_CHARSET_PATTERN = re.compile(
    rb"""charset\s*=\s*["']?\s*([a-zA-Z0-9_\-:.]+)""",
    re.IGNORECASE,
)


BOM_ENCODINGS = (
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF32_LE, "utf-32-le"),
    (codecs.BOM_UTF32_BE, "utf-32-be"),
    (codecs.BOM_UTF16_LE, "utf-16-le"),
    (codecs.BOM_UTF16_BE, "utf-16-be"),
)


def _normalize_encoding(name: str | None) -> str | None:
    """
    Kodlama adini Python'un tanidigi bir codec'e cevirir.

    Taninmayan ad None doner; uydurma bir codec ile
    cozumleme denenmez.
    """

    cleaned = (name or "").strip().strip("\"'").lower()

    if not cleaned:
        return None

    try:
        return codecs.lookup(cleaned).name

    except LookupError:
        return None


def detect_meta_charset(raw: bytes) -> str | None:
    """
    HTML'in bas kismindaki charset bildirimini okur.

    Hem <meta charset="..."> hem de
    <meta http-equiv=content-type content="...charset=...">
    bicimlerini yakalar.
    """

    head = raw[:META_CHARSET_SCAN_BYTES]

    match = META_CHARSET_PATTERN.search(head)

    if match is None:
        return None

    return _normalize_encoding(
        match.group(1).decode("ascii", "ignore")
    )


def detect_bom_encoding(raw: bytes) -> str | None:

    for bom, encoding in BOM_ENCODINGS:
        if raw.startswith(bom):
            return encoding

    return None


def decode_html(
    raw: bytes,
    header_charset: str | None = None,
) -> tuple[str, str]:
    """
    HTML baytlarini DOGRU kodlamayla metne cevirir.

    Doner: (metin, kullanilan kodlama)

    Hicbir durumda hata firlatmaz; en kotu ihtimalde
    kayipli cozumleme yapilir ama fetch akisi kirilmaz.
    """

    if not raw:
        return "", "utf-8"

    # 1. BOM her seyin onunde gelir.
    bom_encoding = detect_bom_encoding(raw)

    if bom_encoding:
        try:
            return raw.decode(bom_encoding), bom_encoding
        except (UnicodeDecodeError, LookupError):
            pass

    candidates: list[str] = []

    # 2. HTTP basligi
    declared = _normalize_encoding(header_charset)

    if declared:
        candidates.append(declared)

    # 3. HTML icindeki meta etiketi
    meta = detect_meta_charset(raw)

    if meta and meta not in candidates:
        candidates.append(meta)

    # 4. UTF-8 kati deneme
    #
    # Kati cozumleme basariliysa icerik gercekten UTF-8'dir;
    # bu, etiket yanlis oldugunda bile dogru sonuc verir.
    if "utf-8" not in candidates:
        candidates.append("utf-8")

    for encoding in candidates:
        try:
            return raw.decode(encoding), encoding

        except (UnicodeDecodeError, LookupError):
            continue

    # 5. Hicbiri kati cozumlenemedi.
    #
    # Bildirilen kodlamayi kayipli da olsa uygulamak,
    # rastgele bir codec secmekten daha dogrudur.
    fallback = candidates[0] if candidates else "utf-8"

    try:
        return raw.decode(fallback, errors="replace"), fallback

    except LookupError:
        return raw.decode("utf-8", errors="replace"), "utf-8"


def _find_ssl_cause(exc: BaseException) -> ssl.SSLError | None:
    """
    Hata zincirinde bir SSL hatasi var mi?

    httpx SSL hatalarini ConnectError icine sardigi icin
    gercek sebep ancak __cause__ zinciri izlenerek
    gorulebilir.
    """

    seen = set()
    current: BaseException | None = exc

    while current is not None and id(current) not in seen:

        seen.add(id(current))

        if isinstance(current, ssl.SSLError):
            return current

        current = current.__cause__ or current.__context__

    return None


def _is_certificate_error(exc: BaseException) -> bool:
    return _find_ssl_cause(exc) is not None


def _certificate_error_detail(exc: BaseException) -> str:
    """
    Kullaniciya gosterilecek KISA sertifika hatasi.

    Sir icermez; yalnizca OpenSSL'in dogrulama gerekcesi.
    """

    cause = _find_ssl_cause(exc)

    if cause is None:
        return "bilinmeyen sertifika hatası"

    reason = getattr(cause, "verify_message", None) or str(cause)

    detail = " ".join(str(reason).split())[:200]

    # Eksik ARA SERTIFIKA en sik karsilasilan durum ve
    # cozumu farkli oldugu icin ayrica isaretlenir.
    if "unable to get local issuer certificate" in detail:
        detail += (
            " (sunucu sertifika zincirini eksik gönderiyor; "
            "eksik ara sertifika EXTRA_CA_BUNDLE ile "
            "tanıtılabilir)"
        )

    return detail


@dataclass
class FetchResult:
    url: str
    final_url: str
    status_code: int
    content_type: str
    html: str

    # Metni cozumlemek icin GERCEKTEN kullanilan kodlama.
    # Teshis ve kabul testi icin; mevcut cagiranlar bu
    # alani okumak zorunda degil.
    encoding: str = "utf-8"


async def fetch_website(url: str) -> FetchResult:
    """
    Verilen web adresine HTTP GET isteği gönderir
    ve HTML içeriğini döndürür.
    """

    if not url:
        raise ValueError("URL boş olamaz.")

    if not url.startswith(("http://", "https://")):
        raise ValueError(
            "URL 'http://' veya 'https://' ile başlamalıdır."
        )

    try:
        async with httpx.AsyncClient(
            timeout=build_timeout(),
            follow_redirects=True,
            headers=DEFAULT_HEADERS,

            # Sertifika DOGRULAMASI ACIK. Baglam yalnizca
            # bazi sunucularin gondermedigi ARA sertifikayi
            # ek olarak tanir; kok deposu genisletilmez ve
            # dogrulama gevsetilmez.
            verify=get_verification_context(),
        ) as client:
            response = await client.get(url)

        response.raise_for_status()

        content_type = response.headers.get(
            "content-type",
            "",
        ).lower()

        if "text/html" not in content_type:

            if "pdf" in content_type:
                # Resmî Gazete bazi belgeleri (kurul
                # kararlari, yargi kararlari) yalnizca PDF
                # olarak yayimliyor. Yonetmelik metinleri
                # HTML'dir. PDF destegi bu surumde YOKTUR
                # ve sessizce bos icerik uretilmez.
                raise WebsiteFetchError(
                    "Bu adres PDF belge döndürüyor. Bu "
                    "sürümde yalnızca HTML sayfalar "
                    "desteklenir. Yönetmelik metinleri "
                    "genellikle .htm adresinde yayımlanır; "
                    "PDF yerine HTML bağlantısını kullanın."
                )

            raise WebsiteFetchError(
                f"Beklenen HTML içerik alınamadı. "
                f"Content-Type: {content_type}"
            )

        # KODLAMA
        #
        # response.text KULLANILMAZ: httpx, charset
        # bildirilmediginde UTF-8 varsayar ve Turkce
        # sayfalarda harfleri U+FFFD yapar. Kodlama
        # baytlardan belirlenir.
        html, encoding = decode_html(
            raw=response.content,
            header_charset=response.charset_encoding,
        )

        return FetchResult(
            url=url,
            final_url=str(response.url),
            status_code=response.status_code,
            content_type=content_type,
            html=html,
            encoding=encoding,
        )

    except httpx.TimeoutException as exc:
        raise WebsiteFetchError(
            f"Site zaman aşımına uğradı: {url}"
        ) from exc

    except httpx.HTTPStatusError as exc:
        raise WebsiteFetchError(
            f"HTTP hatası: "
            f"{exc.response.status_code} - {url}"
        ) from exc

    except httpx.ConnectError as exc:

        # TLS hatalarini "bağlanılamadı" icine gomme.
        #
        # Ara sertifikasini gondermeyen sunucularda hata
        # tam olarak burada olusuyordu ve genel bir
        # baglanti hatasi gibi gorunup YANLIS TESHISE yol
        # aciyordu.
        if _is_certificate_error(exc):
            raise WebsiteFetchError(
                "Sitenin güvenlik sertifikası doğrulanamadı: "
                f"{_certificate_error_detail(exc)}"
            ) from exc

        raise WebsiteFetchError(
            f"Siteye bağlanılamadı: {url}"
        ) from exc

    except httpx.RequestError as exc:
        raise WebsiteFetchError(
            f"Siteye bağlanılamadı: {url}"
        ) from exc