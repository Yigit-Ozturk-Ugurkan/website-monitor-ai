"""
OTOMATIK REFERANS KESFI

Kayitli RegulationReference kutuphanesinde guvenilir bir
referans BULUNAMADIGINDA, degisikligin hedefledigi mevzuati
RESMI kaynakta arar ve dogrulanmis bir referans olusturur.

AKIS

    base mevzuat adi
      -> mevzuat.gov.tr resmi aramasi
      -> aday dogrulama (yalnizca rakam kimlikler)
      -> ad + yayim bilgisi ile puanlama
      -> guvenilir TEK aday var mi?
           hayir -> no_match / ambiguous, KAYIT YOK
           evet  -> kanonik adres
                    -> konsolide metin GERCEKTEN alinir
                    -> ancak ondan sonra RegulationReference

TEMEL KURALLAR

  1. YALNIZCA RESMI KAYNAK.
     Referans SADECE mevzuat.gov.tr'den olusturulur. Genel
     arama motoru, blog, hukuk sitesi veya ozel mevzuat
     portali otomatik guvenilir referans OLARAK
     KULLANILMAZ.

  2. DOGRULAMADAN ONCE KAYIT YOK.
     Bir arama sonucunun yalnizca BASLIGINI gorup satir
     acilmaz. Once kanonik adresten gercek konsolide metin
     basariyla alinmali, anlamli uzunlukta olmali ve ozeti
     cikarilabilmelidir.

  3. SAHTE KESINLIK YOK.
     Birebir resmi ad veya cok guclu tek eslesme yoksa
     referans OLUSTURULMAZ. Iki guclu aday birbirine
     yakinsa sonuc ambiguous'tur.

  4. GEMINI EN SON.
     Once tamamen deterministik cozulur. Model YALNIZCA
     gercekten ayirt edilemeyen resmi adaylar icin ve en
     fazla BIR mantiksal cagri ile kullanilir. Modele TAM
     MEVZUAT METNI GONDERILMEZ; yalnizca aday kimligi,
     adi ve yayim bilgisi gider.

  5. KANONIK ADRES.
     Ayni belge farkli parametre sirasiyla gelse bile
     source_url her zaman ayni kanonik metindir; boylece
     duplicate satir olusmaz.
"""

import logging

from dataclasses import dataclass, field

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.regulation_reference import RegulationReference
from app.repositories.regulation_reference_repository import (
    create_regulation_reference,
    get_regulation_reference_by_url,
)
from app.services.ai_analyzer import (
    build_generation_config,
    run_with_quota_fallback,
)
from app.services.mevzuat_search_service import (
    MevzuatCandidate,
    MevzuatSearchError,
    SEARCH_PROFILES,
    search_official_regulations,
)
from app.services.regulation_reference_matcher import (
    score_name,
)
from app.services.regulation_reference_service import (
    MIN_CONTENT_LENGTH,
    RegulationReferenceFetchError,
    fetch_regulation_content,
)
from app.services.regulation_source_registry import (
    SOURCE_TYPE_MEVZUAT_GOV_TR,
    canonical_mevzuat_url,
)
from app.services.regulation_text_parser import (
    parse_regulation_segments,
)
from app.services.regulation_title_parser import (
    BaseRegulationName,
    normalize_regulation_name,
)


logger = logging.getLogger(__name__)


# ---------------------------------------------------------
# KESIF DURUMLARI
#
# Mevcut match/comparison durumlariyla CAKISMAZ; ayri bir
# alanda tasinir.
# ---------------------------------------------------------

# Kesif denenmedi: yerel eslestirme zaten bir referans
# verdi, ya da kesfin calismasi dogru degildi
# (yerel belirsizlik, cikarilamayan mevzuat adi).
DISCOVERY_NOT_NEEDED = "not_needed"

# Manuel mod / website takibi: kesif hic soz konusu degil.
DISCOVERY_NOT_APPLICABLE = "not_applicable"

# Resmi kayit bulundu ve zaten kutuphanede vardi.
# YENI SATIR ACILMADI.
DISCOVERY_FOUND_EXISTING = "found_existing"

# Resmi kayit bulundu, dogrulandi ve YENI referans olustu.
DISCOVERY_DISCOVERED = "discovered"

# Resmi aramada guvenilir aday cikmadi.
DISCOVERY_NO_MATCH = "no_match"

# Birden fazla resmi aday ayirt edilemedi.
DISCOVERY_AMBIGUOUS = "ambiguous"

# Arama veya konsolide metin alma teknik olarak basarisiz.
DISCOVERY_FAILED = "discovery_failed"


DISCOVERY_STATUSES = (
    DISCOVERY_NOT_NEEDED,
    DISCOVERY_NOT_APPLICABLE,
    DISCOVERY_FOUND_EXISTING,
    DISCOVERY_DISCOVERED,
    DISCOVERY_NO_MATCH,
    DISCOVERY_AMBIGUOUS,
    DISCOVERY_FAILED,
)


# ---------------------------------------------------------
# ESLESTIRME YONTEMI
#
# Kesifle bulunmus bir referansin comparison satirinda
# NASIL belirlendigi. Kullanicinin izini surebilmesi icin
# yerel eslestirmeden AYRI adlar kullanilir.
# ---------------------------------------------------------

# Resmi ad, base ad ile birebir ayni.
MATCH_METHOD_DISCOVERED_EXACT = "automatic_discovered_exact"

# Birebir degil ama tek ve net guclu ad eslesmesi.
MATCH_METHOD_DISCOVERED_TITLE = "automatic_discovered_title"

# Ad esitti; yayim tarihi / RG sayisi ayirdi.
MATCH_METHOD_DISCOVERED_META = "automatic_discovered_meta"

# Belirsizlik yapay zeka ile cozuldu.
MATCH_METHOD_DISCOVERED_AI = "automatic_discovered_ai"


DISCOVERY_MATCH_METHODS = (
    MATCH_METHOD_DISCOVERED_EXACT,
    MATCH_METHOD_DISCOVERED_TITLE,
    MATCH_METHOD_DISCOVERED_META,
    MATCH_METHOD_DISCOVERED_AI,
)


# ---------------------------------------------------------
# ESIKLER
#
# Resmi kaynak adlari KANONIKTIR: kullanicilarin serbestce
# adlandirdigi kayitli referanslardan farkli olarak burada
# birebir eslesme BEKLENIR. Bu yuzden esik yerel
# eslestirmeden (0.60) daha yuksektir.
# ---------------------------------------------------------

MIN_DISCOVERY_SCORE = 0.75

# Kazananin ikinciden en az bu kadar onde olmasi gerekir.
DISCOVERY_DECISIVE_MARGIN = 0.15

# Modele gonderilecek en fazla aday.
MAX_AI_CANDIDATES = 6

# Modelin secimi bu guvenin altindaysa KABUL EDILMEZ.
MIN_AI_CONFIDENCE = 0.80

# RegulationReference.title alani String(300).
MAX_TITLE_LENGTH = 300


@dataclass
class ScoredCandidate:
    """
    Puanlanmis resmi arama adayi.

    Veritabani kaydi GEREKTIRMEZ; sahte bir satir
    olusturulmaz.
    """

    candidate: MevzuatCandidate
    score: float

    matched_tokens: int = 0
    total_tokens: int = 0

    # Resmi ad, base ad ile birebir ayni mi?
    exact_title: bool = False

    # Yayim tarihi tuttu mu?
    date_match: bool = False

    # Resmî Gazete sayisi tuttu mu?
    issue_match: bool = False

    @property
    def publication_match(self) -> bool:
        """
        Yayim bilgisi GERCEKTEN dogrulandi mi?

        Yalnizca hem kaynakta hem adayda bilgi VARSA ve
        tutuyorsa True olur. Eksik bilgi "uyuyor" sayilmaz.
        """

        return self.date_match or self.issue_match


@dataclass
class DiscoveryResult:
    """
    Bir kesif denemesinin sonucu.

    reference None ise HICBIR satir olusturulmamistir.
    """

    status: str

    reference: RegulationReference | None = None

    # Secilen resmi aday (secim yapildiysa).
    candidate: MevzuatCandidate | None = None

    # Kesifle bulunan referansin nasil secildigi.
    match_method: str | None = None

    score: float = 0.0

    # Puanlanmis tum adaylar (yuksekten dusuge).
    candidates: list[ScoredCandidate] = field(
        default_factory=list
    )

    # Belirsizlik icin Gemini cagrildi mi?
    ai_used: bool = False

    ai_model_name: str | None = None

    # Insan tarafindan okunabilir kisa aciklama.
    detail: str = ""

    @property
    def created(self) -> bool:
        return self.status == DISCOVERY_DISCOVERED

    @property
    def usable(self) -> bool:
        """
        Karsilastirma bu sonucla devam edebilir mi?
        """

        return (
            self.reference is not None
            and self.status
            in (
                DISCOVERY_DISCOVERED,
                DISCOVERY_FOUND_EXISTING,
            )
        )


def _sanitize_detail(message: str) -> str:
    """
    Kullaniciya gosterilecek kisa aciklama.

    Stack trace, dosya yolu veya anahtar benzeri uzun
    degerler disari sizmaz.
    """

    cleaned = " ".join((message or "").split())

    if not cleaned:
        return "Bilinmeyen hata."

    parts = [
        "***" if len(word) > 40 else word
        for word in cleaned.split(" ")
    ]

    return " ".join(parts)[:300]


# ---------------------------------------------------------
# PUANLAMA
# ---------------------------------------------------------

def score_candidates(
    base_name: BaseRegulationName,
    candidates: list[MevzuatCandidate],
) -> list[ScoredCandidate]:
    """
    Resmi adaylari base ad ve yayim bilgisiyle puanlar.

    Ad puanlamasi mevcut score_name() yardimcisidir; yerel
    eslestirme ile AYNI olcut kullanilir.

    Yayim bilgisi (tarih / RG sayisi) SADECE ayirt edici
    sinyaldir; tek basina secim yaptirmaz.
    """

    normalized_base = base_name.normalized

    scored: list[ScoredCandidate] = []

    for candidate in candidates:

        score, matched, total = score_name(
            base_name=base_name,
            title=candidate.title,
        )

        date_match = bool(
            base_name.published_on is not None
            and candidate.published_on is not None
            and base_name.published_on == candidate.published_on
        )

        issue_match = bool(
            base_name.official_gazette_issue
            and candidate.official_gazette_issue
            and base_name.official_gazette_issue
            == candidate.official_gazette_issue
        )

        scored.append(
            ScoredCandidate(
                candidate=candidate,
                score=score,
                matched_tokens=matched,
                total_tokens=total,
                exact_title=(
                    normalize_regulation_name(candidate.title)
                    == normalized_base
                ),
                date_match=date_match,
                issue_match=issue_match,
            )
        )

    scored.sort(
        key=lambda item: (
            -item.score,
            not item.exact_title,
            not item.publication_match,
            item.candidate.candidate_id,
        )
    )

    return scored


# ---------------------------------------------------------
# BELIRSIZLIK ICIN GEMINI
#
# Modele TAM MEVZUAT METNI GONDERILMEZ. Yalnizca aday
# kimligi, adi ve yayim bilgisi gider.
# ---------------------------------------------------------

class DiscoverySelection(BaseModel):
    candidate_id: str = Field(
        description=(
            "Değişikliğin hedeflediği resmî mevzuat "
            "kaydının candidate_id değeri. YALNIZCA verilen "
            "listeden bir değer olabilir."
        )
    )

    confidence: float = Field(
        description=(
            "0.0 - 1.0 arası güven. Emin değilsen düşük "
            "yaz; yanlış eşleşme yerine seçim yapılmaması "
            "tercih edilir."
        )
    )

    reason: str = Field(
        description="Kısa Türkçe gerekçe."
    )


def build_discovery_prompt(
    base_name: BaseRegulationName,
    candidates: list[ScoredCandidate],
) -> str:

    blocks = []

    for item in candidates:
        candidate = item.candidate

        lines = [
            f"candidate_id: {candidate.candidate_id}",
            f"ad: {candidate.title}",
            f"mevzuat_no: {candidate.mevzuat_no}",
            f"mevzuat_tur: {candidate.mevzuat_tur}",
            f"mevzuat_tertip: {candidate.mevzuat_tertip}",
        ]

        if candidate.kind_label:
            lines.append(f"tür: {candidate.kind_label}")

        if candidate.published_on:
            lines.append(
                f"yayım_tarihi: "
                f"{candidate.published_on.isoformat()}"
            )

        if candidate.official_gazette_issue:
            lines.append(
                f"resmî_gazete_sayısı: "
                f"{candidate.official_gazette_issue}"
            )

        blocks.append("\n".join(lines))

    candidate_text = "\n\n---\n\n".join(blocks)

    allowed = ", ".join(
        item.candidate.candidate_id
        for item in candidates
    )

    source_lines = [f"ad: {base_name.name}"]

    if base_name.published_on:
        source_lines.append(
            f"yayım_tarihi: "
            f"{base_name.published_on.isoformat()}"
        )

    if base_name.official_gazette_issue:
        source_lines.append(
            f"resmî_gazete_sayısı: "
            f"{base_name.official_gazette_issue}"
        )

    source_text = "\n".join(source_lines)

    return f"""
Sen bir resmî mevzuat eşleştirme motorusun.

Resmî Gazete'de yayımlanan bir değişiklik aşağıdaki
mevzuatı hedefliyor:

{source_text}

Mevzuat Bilgi Sistemi araması şu kayıtları döndürdü:

{candidate_text}

GÖREVİN:

1. Değişikliğin hedeflediği mevzuata karşılık gelen kaydı
   seç.

2. candidate_id alanı YALNIZCA şu değerlerden biri
   olabilir:

{allowed}

   Bu listede olmayan bir değer UYDURMA.

3. Yayım tarihi ve Resmî Gazete sayısı biliniyorsa güçlü
   ayırt edici sinyaldir; birlikte değerlendir.

4. Hiçbiri açıkça aynı mevzuat değilse confidence değerini
   düşük yaz. Yanlış eşleştirme yapmaktansa seçim
   yapılmaması tercih edilir.

5. reason alanını Türkçe ve kısa yaz.

Çıktı şemaya uygun olmalıdır.
""".strip()


class DiscoverySelectionError(Exception):
    """
    Kesif sirasinda AI hatasi.

    Pipeline'i DUSURMEZ; sonuc ambiguous olur.
    """


async def select_with_ai(
    base_name: BaseRegulationName,
    candidates: list[ScoredCandidate],
) -> tuple[ScoredCandidate | None, str, str]:
    """
    Ayirt edilemeyen resmi adaylar arasindan secim yaptirir.

    Doner: (secilen aday veya None, model adi, gerekce)

    Modelin dondurdugu candidate_id SUNUCU TARAFINDA
    allow-list ile dogrulanir; listede olmayan bir deger
    KABUL EDILMEZ.
    """

    if not candidates:
        return None, "", "aday yok"

    limited = candidates[:MAX_AI_CANDIDATES]

    prompt = build_discovery_prompt(
        base_name=base_name,
        candidates=limited,
    )

    allow_list = {
        item.candidate.candidate_id: item
        for item in limited
    }

    async def generate(client, model_name):

        response = await client.aio.models.generate_content(
            model=model_name,
            contents=prompt,
            config=build_generation_config(
                model_name,
                response_schema=DiscoverySelection,
            ),
        )

        if not response.text:
            raise DiscoverySelectionError(
                "Gemini resmî mevzuat seçimi için boş cevap "
                "döndürdü."
            )

        try:
            return DiscoverySelection.model_validate_json(
                response.text
            )

        except ValidationError as exc:
            raise DiscoverySelectionError(
                "Gemini resmî mevzuat seçimi cevabı beklenen "
                "şemaya uygun değil."
            ) from exc

    outcome = await run_with_quota_fallback(
        generate=generate,
        error_class=DiscoverySelectionError,
        error_prefix="Gemini resmî mevzuat seçimi hatası",
    )

    selection: DiscoverySelection = outcome.result

    chosen = allow_list.get(
        (selection.candidate_id or "").strip()
    )

    if chosen is None:
        # UYDURMA KIMLIK
        #
        # Model aday listesinde olmayan bir deger dondurdu.
        # Bu deger adres kurmakta ASLA kullanilmaz.
        logger.warning(
            "Kesif: uydurma candidate_id reddedildi. id=%r",
            selection.candidate_id,
        )

        return (
            None,
            outcome.model_name,
            "model aday listesinde olmayan bir kimlik döndürdü",
        )

    if selection.confidence < MIN_AI_CONFIDENCE:
        return (
            None,
            outcome.model_name,
            selection.reason or "model yeterince emin değil",
        )

    return chosen, outcome.model_name, selection.reason


# ---------------------------------------------------------
# KARAR
# ---------------------------------------------------------

def resolve_local(
    scored: list[ScoredCandidate],
) -> tuple[str, ScoredCandidate | None, str | None]:
    """
    DETERMINISTIK karar. Gemini CAGRILMAZ.

    Doner: (durum, secilen aday, eslestirme yontemi)

    Durum:
      matched   -> guvenle secilebilir tek aday
      ambiguous -> ayirt edilemedi
      no_match  -> yeterli aday yok
    """

    if not scored:
        return DISCOVERY_NO_MATCH, None, None

    # -----------------------------------------------------
    # 1. BIREBIR RESMI AD
    # -----------------------------------------------------

    exact = [
        item
        for item in scored
        if item.exact_title
    ]

    if len(exact) == 1:
        return (
            "matched",
            exact[0],
            MATCH_METHOD_DISCOVERED_EXACT,
        )

    if len(exact) > 1:
        # Ayni ada sahip birden fazla resmi kayit.
        # Yayim bilgisi ayirabilir mi?
        by_publication = [
            item
            for item in exact
            if item.publication_match
        ]

        if len(by_publication) == 1:
            return (
                "matched",
                by_publication[0],
                MATCH_METHOD_DISCOVERED_META,
            )

        return DISCOVERY_AMBIGUOUS, None, None

    # -----------------------------------------------------
    # 2. GUCLU AD ESLESMESI
    # -----------------------------------------------------

    qualified = [
        item
        for item in scored
        if item.score >= MIN_DISCOVERY_SCORE
    ]

    if not qualified:
        return DISCOVERY_NO_MATCH, None, None

    best = qualified[0]

    if len(qualified) == 1:
        return (
            "matched",
            best,
            MATCH_METHOD_DISCOVERED_TITLE,
        )

    runner_up = qualified[1]

    if (
        best.score - runner_up.score
        >= DISCOVERY_DECISIVE_MARGIN
    ):
        return (
            "matched",
            best,
            MATCH_METHOD_DISCOVERED_TITLE,
        )

    # -----------------------------------------------------
    # 3. YAYIM BILGISI AYRIMI
    #
    # Ad ayirt edemedi. Degisiklik govdesinden cikan
    # yayim tarihi / RG sayisi TEK bir adayi gosteriyorsa
    # karar belirsiz DEGILDIR.
    # -----------------------------------------------------

    by_publication = [
        item
        for item in qualified
        if item.publication_match
    ]

    if len(by_publication) == 1:
        return (
            "matched",
            by_publication[0],
            MATCH_METHOD_DISCOVERED_META,
        )

    return DISCOVERY_AMBIGUOUS, None, None


# ---------------------------------------------------------
# KANONIK ADRESLE MEVCUT KAYIT ARAMA
# ---------------------------------------------------------

def find_existing_by_canonical_url(
    db: Session,
    canonical_url: str,
) -> RegulationReference | None:
    """
    Ayni resmi belgeyi gosteren bir referans zaten var mi?

    Once dogrudan adres esitligi denenir. Bulunamazsa
    kayitli mevzuat.gov.tr adresleri KANONIKLESTIRILEREK
    karsilastirilir; boylece kullanicinin farkli parametre
    sirasiyla (veya iframe adresiyle) eklemis oldugu bir
    kayit ikinci kez olusturulmaz.
    """

    existing = get_regulation_reference_by_url(
        db=db,
        source_url=canonical_url,
    )

    if existing is not None:
        return existing

    statement = select(RegulationReference).where(
        RegulationReference.source_url.ilike(
            "%mevzuat.gov.tr%"
        )
    )

    for reference in db.scalars(statement).all():

        if (
            canonical_mevzuat_url(reference.source_url)
            == canonical_url
        ):
            return reference

    return None


# ---------------------------------------------------------
# DOGRULANMIS REFERANS OLUSTURMA
# ---------------------------------------------------------

async def create_reference_from_candidate(
    db: Session,
    candidate: MevzuatCandidate,
) -> tuple[RegulationReference, bool]:
    """
    Adaydan DOGRULANMIS bir RegulationReference uretir.

    Doner: (referans, yeni_mi)

    CREATE BEFORE VERIFY YOKTUR:

      1. kanonik adresten gercek metin alinir
         (mevcut source resolver kabuk -> iframe cevirir)
      2. HTTP basarisi, anlamli uzunluk ve icerik cikarimi
         mevcut fetch_regulation_content tarafindan
         dogrulanir
      3. kaynak turu ve konsolidelik KANITLANMIS olmali
      4. ancak bundan SONRA satir acilir

    Kalicilik mevcut create_regulation_reference'tir; ayri
    bir yazma yolu yazilmaz. content_hash, source_type,
    is_consolidated, version_date ve last_verified_at
    oradan gelir.
    """

    existing = find_existing_by_canonical_url(
        db=db,
        canonical_url=candidate.canonical_url,
    )

    if existing is not None:
        return existing, False

    content = await fetch_regulation_content(
        candidate.canonical_url
    )

    # -----------------------------------------------------
    # KAYNAK DOGRULAMASI
    #
    # Otomatik olusturulan referans YALNIZCA resmi konsolide
    # metin olabilir. Kaynak beklenmedik bicimde baska bir
    # sey dondurduyse satir ACILMAZ.
    # -----------------------------------------------------

    if content.source_type != SOURCE_TYPE_MEVZUAT_GOV_TR:
        raise RegulationReferenceFetchError(
            "Otomatik referans yalnızca Mevzuat Bilgi "
            "Sistemi kaynağından oluşturulabilir."
        )

    if not content.is_consolidated:
        raise RegulationReferenceFetchError(
            "Alınan metin konsolide mevzuat metni olarak "
            "doğrulanamadı."
        )

    if content.text_length < MIN_CONTENT_LENGTH:
        raise RegulationReferenceFetchError(
            "Alınan mevzuat metni anlamlı bir referans "
            "oluşturmak için çok kısa."
        )

    # Ayristirici hic segment bulamazsa karsilastirma zaten
    # aday uretemez. Bu bir ENGEL degildir (metin gecerli
    # olabilir) ama iz birakilir.
    try:
        document = parse_regulation_segments(content.text)

        segment_count = len(document.segments)

    except Exception:  # noqa: BLE001
        segment_count = 0

    if segment_count == 0:
        logger.warning(
            "Kesfedilen referans metninde MADDE bulunamadi. "
            "url=%s uzunluk=%s",
            candidate.canonical_url,
            content.text_length,
        )

    # -----------------------------------------------------
    # RESMI AD KANONIKTIR
    #
    # Kullanicidan cikarilmis base ad DEGIL, resmi arama
    # sonucunun kendi adi saklanir.
    # -----------------------------------------------------

    title = candidate.title[:MAX_TITLE_LENGTH]

    try:
        reference = create_regulation_reference(
            db=db,
            title=title,
            source_url=candidate.canonical_url,
            content=content.text,
            source_type=content.source_type,
            is_consolidated=content.is_consolidated,
            version_date=content.version_date,
        )

        return reference, True

    except IntegrityError:

        # -------------------------------------------------
        # YARIS DURUMU
        #
        # Baska bir calistirma ayni referansi once
        # olusturdu. unique(source_url) kisiti bunu
        # yakaladi; COKMEK yerine mevcut satir kullanilir.
        # -------------------------------------------------

        db.rollback()

        existing = find_existing_by_canonical_url(
            db=db,
            canonical_url=candidate.canonical_url,
        )

        if existing is None:
            raise

        logger.info(
            "Kesfedilen referans baska bir calistirmada "
            "olusmus. reference_id=%s",
            existing.id,
        )

        return existing, False


# ---------------------------------------------------------
# ANA GIRIS NOKTASI
# ---------------------------------------------------------

async def discover_reference(
    db: Session,
    base_name: BaseRegulationName,
    allow_ai: bool = True,
) -> DiscoveryResult:
    """
    Kayitli kutuphanede bulunamayan mevzuati RESMI kaynakta
    arar ve dogrulanmis bir referans olusturur.

    Bu fonksiyon HICBIR ZAMAN hata firlatmaz. Basarisizlik
    discovery_failed olarak doner; scheduler cokmez,
    snapshot / change / AIAnalysis korunur ve UYDURMA
    referans olusmaz.
    """

    if base_name is None:
        return DiscoveryResult(
            status=DISCOVERY_NOT_NEEDED,
            detail=(
                "Değişiklik metninden hedef mevzuat adı "
                "çıkarılamadığı için resmî arama yapılmadı."
            ),
        )

    if base_name.kind not in SEARCH_PROFILES:
        return DiscoveryResult(
            status=DISCOVERY_NOT_NEEDED,
            detail=(
                f"'{base_name.kind}' türü için resmî arama "
                "profili tanımlı değil."
            ),
        )

    # -----------------------------------------------------
    # 1. RESMI ARAMA
    # -----------------------------------------------------

    try:
        search = await search_official_regulations(
            query=base_name.name,
            kind=base_name.kind,
        )

    except MevzuatSearchError as exc:
        logger.warning(
            "Resmi mevzuat aramasi basarisiz: %s",
            exc,
        )

        return DiscoveryResult(
            status=DISCOVERY_FAILED,
            detail=_sanitize_detail(str(exc)),
        )

    except Exception as exc:  # noqa: BLE001
        # Beklenmeyen bir hata da pipeline'i dusurmez.
        logger.exception(
            "Resmi mevzuat aramasinda beklenmeyen hata."
        )

        return DiscoveryResult(
            status=DISCOVERY_FAILED,
            detail=_sanitize_detail(
                f"{type(exc).__name__}: {exc}"
            ),
        )

    scored = score_candidates(
        base_name=base_name,
        candidates=search.candidates,
    )

    if not scored:
        return DiscoveryResult(
            status=DISCOVERY_NO_MATCH,
            candidates=scored,
            detail=(
                f"'{base_name.name}' adına karşılık gelen "
                "resmî mevzuat kaydı bulunamadı."
            ),
        )

    # -----------------------------------------------------
    # 2. DETERMINISTIK KARAR
    # -----------------------------------------------------

    status, chosen, method = resolve_local(scored)

    ai_used = False
    ai_model_name = None
    ai_reason = ""

    # -----------------------------------------------------
    # 3. YALNIZCA BELIRSIZLIKTE GEMINI
    #
    # Birebir resmi ad eslesmesinde BU BLOK CALISMAZ ve
    # kesif icin 0 model cagrisi yapilir.
    # -----------------------------------------------------

    if status == DISCOVERY_AMBIGUOUS and allow_ai:

        qualified = [
            item
            for item in scored
            if item.score >= MIN_DISCOVERY_SCORE
            or item.exact_title
        ]

        try:
            selected, ai_model_name, ai_reason = (
                await select_with_ai(
                    base_name=base_name,
                    candidates=qualified,
                )
            )

            ai_used = True

            if selected is not None:
                status = "matched"
                chosen = selected
                method = MATCH_METHOD_DISCOVERED_AI

        except DiscoverySelectionError as exc:
            logger.warning(
                "Kesif AI ayrimi basarisiz: %s",
                exc,
            )

            ai_used = True

        except Exception:  # noqa: BLE001
            logger.exception(
                "Kesif AI ayriminda beklenmeyen hata."
            )

            ai_used = True

    if status == DISCOVERY_AMBIGUOUS or chosen is None:

        if status == DISCOVERY_NO_MATCH:
            detail = (
                f"'{base_name.name}' adına yeterince uyan "
                "resmî mevzuat kaydı bulunamadı."
            )

        else:
            status = DISCOVERY_AMBIGUOUS

            detail = (
                "Birden fazla olası resmî mevzuat kaydı "
                "bulundu; otomatik seçim yapılmadı."
            )

            if ai_reason:
                detail += f" ({_sanitize_detail(ai_reason)})"

        return DiscoveryResult(
            status=status,
            candidates=scored,
            ai_used=ai_used,
            ai_model_name=ai_model_name,
            detail=detail,
        )

    # -----------------------------------------------------
    # 4. DOGRULA, SONRA OLUSTUR
    # -----------------------------------------------------

    candidate = chosen.candidate

    try:
        reference, created = (
            await create_reference_from_candidate(
                db=db,
                candidate=candidate,
            )
        )

    except RegulationReferenceFetchError as exc:
        logger.warning(
            "Kesfedilen mevzuatin konsolide metni "
            "alinamadi. url=%s hata=%s",
            candidate.canonical_url,
            exc,
        )

        return DiscoveryResult(
            status=DISCOVERY_FAILED,
            candidate=candidate,
            candidates=scored,
            score=chosen.score,
            ai_used=ai_used,
            ai_model_name=ai_model_name,
            detail=_sanitize_detail(str(exc)),
        )

    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "Kesfedilen referans olusturulamadi. url=%s",
            candidate.canonical_url,
        )

        try:
            db.rollback()

        except Exception:  # noqa: BLE001
            pass

        return DiscoveryResult(
            status=DISCOVERY_FAILED,
            candidate=candidate,
            candidates=scored,
            score=chosen.score,
            ai_used=ai_used,
            ai_model_name=ai_model_name,
            detail=_sanitize_detail(
                f"{type(exc).__name__}: {exc}"
            ),
        )

    logger.info(
        "Resmi referans %s. url=%s reference_id=%s "
        "yontem=%s skor=%.3f ai=%s",
        "olusturuldu" if created else "zaten vardi",
        candidate.canonical_url,
        reference.id,
        method,
        chosen.score,
        ai_used,
    )

    return DiscoveryResult(
        status=(
            DISCOVERY_DISCOVERED
            if created
            else DISCOVERY_FOUND_EXISTING
        ),
        reference=reference,
        candidate=candidate,
        match_method=method,
        score=chosen.score,
        candidates=scored,
        ai_used=ai_used,
        ai_model_name=ai_model_name,
        detail=(
            f"'{reference.title}' Mevzuat Bilgi Sistemi'nde "
            "bulundu ve konsolide metni alındı."
        ),
    )
