"""
Referans yonetmelik icerigini kaynaktan alma.

Bu modul YENI bir indirme/cikarma yolu acmaz. Mevcut
web_fetcher ve content_extractor altyapisini AYNEN yeniden
kullanir; boylece URL dogrulama, zaman asimi, HTTP hata
yonetimi ve HTML temizleme davranisi website monitor ile
birebir ayni kalir.

Bu sprintte PDF/OCR YOKTUR. Kaynak HTML bir web sayfasi
olmalidir; fetch_website zaten text/html disindaki
content-type degerlerini reddeder.

Gemini CAGRILMAZ. Referans olusturmak ve listelemek
hicbir AI istegi uretmez.
"""

from dataclasses import dataclass
from datetime import date

from app.services.content_extractor import extract_content
from app.services.regulation_source_registry import (
    ResolvedSource,
    resolve_reference_source,
)
from app.services.regulation_text_parser import (
    extract_consolidation_info,
)
from app.services.web_fetcher import (
    WebsiteFetchError,
    fetch_website,
)


class RegulationReferenceFetchError(Exception):
    """
    Referans yonetmelik metni alinamadiginda uretilir.

    Mesaji dogrudan kullaniciya gosterilir; bu yuzden
    anlasilir Turkce olmali ve sir icermemelidir.
    """


# Anlamli bir yonetmelik metninin altinda kalamayacagi
# uzunluk. Bunun altindaki cikti genellikle JavaScript ile
# doldurulan bos bir kabuk sayfadir.
MIN_CONTENT_LENGTH = 200


# Tek bir referans icin kabul edilen ust sinir.
# Amac veritabanina sinirsiz buyuklukte metin yazilmasini
# engellemektir.
MAX_CONTENT_LENGTH = 2_000_000


@dataclass
class RegulationContent:
    # Kaynak sayfanin <title> etigi.
    # Kullanicinin girdigi baslik ile karistirilmaz.
    page_title: str

    # Yonlendirmeler sonrasi ulasilan gercek adres.
    final_url: str

    # DUZ METIN. HTML degildir.
    text: str

    text_length: int

    # ---------------------------------------------------
    # SURUM BILGISI
    # ---------------------------------------------------

    # mevzuat_gov_tr | resmi_gazete_archive | generic
    source_type: str = "generic"

    # Metin uzerine sonraki degisiklikler islenmis mi?
    is_consolidated: bool = False

    # Metne islenmis EN SON degisikligin RG tarihi.
    version_date: date | None = None

    # Metinde bulunan degisiklik isareti sayisi.
    # Teshis icin; kullaniciya "gercekten konsolide mi"
    # sorusunun kaniti olarak sunulabilir.
    amendment_marker_count: int = 0

    # Kabuk sayfa metin adresine cevrildi mi?
    fetched_url: str = ""


async def fetch_regulation_content(
    source_url: str,
) -> RegulationContent:
    """
    Kaynak adresten referans yonetmelik metnini cikarir.

    Basarisiz her durum RegulationReferenceFetchError'a
    donusturulur; boylece API katmani tek bir hata turu
    ile calisir.
    """

    # -----------------------------------------------------
    # KAYNAK COZUMLEME
    #
    # mevzuat.gov.tr'de kullanicinin gordugu adres bir
    # KABUK SAYFADIR ve metin icermez; metin ayri bir
    # iframe adresinde durur. Burada adres, ayni belgenin
    # metin adresine cevrilir.
    #
    # Otomatik mevzuat esleme YAPILMAZ; yalnizca
    # kullanicinin verdigi belge baska bir adresle
    # gosterilir.
    # -----------------------------------------------------

    resolved = resolve_reference_source(source_url)

    try:
        fetch_result = await fetch_website(resolved.fetch_url)

    except WebsiteFetchError as exc:
        raise RegulationReferenceFetchError(
            f"Yönetmelik sayfası getirilemedi: {exc}"
        ) from exc

    except ValueError as exc:
        # URL bicimi hatali (bos, sema yok vb.)
        raise RegulationReferenceFetchError(
            str(exc)
        ) from exc

    try:
        extracted = extract_content(fetch_result.html)

    except ValueError as exc:
        raise RegulationReferenceFetchError(
            f"Yönetmelik içeriği okunamadı: {exc}"
        ) from exc

    text = extracted.text.strip()

    if len(text) < MIN_CONTENT_LENGTH:
        raise RegulationReferenceFetchError(
            "Bu adresten anlamlı bir yönetmelik metni "
            "çıkarılamadı. Sayfa içeriği çok kısa veya "
            "metin tarayıcıda sonradan yükleniyor olabilir. "
            "HTML olarak yayımlanan bir mevzuat adresi "
            "deneyin."
        )

    if len(text) > MAX_CONTENT_LENGTH:
        raise RegulationReferenceFetchError(
            "Yönetmelik metni bu sürümde desteklenen "
            "boyutun üzerinde. Daha dar kapsamlı bir "
            "kaynak adresi kullanın."
        )

    # -----------------------------------------------------
    # SURUM BILGISI
    #
    # is_consolidated iki kosula birden baglidir:
    #
    #   1. kaynak konsolide mevzuat yayimliyor olmali
    #   2. metin gercekten konsolide metin gibi gorunmeli
    #
    # Ikinci kosul, kaynagin bize hata/kabuk sayfa
    # dondurdugu durumlarda yanlis "konsolide" etiketi
    # uretilmesini engeller.
    #
    # ISTISNA: hic degistirilmemis yeni bir yonetmelikte
    # isaret bulunmaz. Bu yuzden isaret yoklugu tek basina
    # konsolideligi DUSURMEZ; yalnizca metin anlamli
    # uzunlukta MADDE icermiyorsa suphe edilir.
    # -----------------------------------------------------

    consolidation = extract_consolidation_info(text)

    return RegulationContent(
        page_title=extracted.title,
        final_url=fetch_result.final_url,
        text=text,
        text_length=len(text),
        source_type=resolved.source_type,
        is_consolidated=resolved.is_consolidated,
        version_date=consolidation.latest_amendment_date,
        amendment_marker_count=consolidation.marker_count,
        fetched_url=resolved.fetch_url,
    )
