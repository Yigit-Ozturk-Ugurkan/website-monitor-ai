import logging

from dataclasses import dataclass
from typing import Literal

from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from pydantic import BaseModel, Field, ValidationError

from app.core.config import settings


logger = logging.getLogger(__name__)


class AIAnalyzerError(Exception):
    """Gemini değişiklik analizi başarısız olduğunda oluşturulan hata."""


class AIAnalysisResult(BaseModel):
    relevant: bool = Field(
        description=(
            "Değişikliğin kullanıcının takip ettiği konu ile "
            "gerçekten ilgili olup olmadığı."
        )
    )

    importance: Literal[
        "low",
        "medium",
        "high",
    ] = Field(
        description="Değişikliğin önem seviyesi."
    )

    category: str = Field(
        description=(
            "Değişiklik kategorisi. Örnek: pricing, model_update, "
            "feature, policy, availability, documentation, other."
        )
    )

    summary: str = Field(
        description=(
            "Kullanıcıya gösterilecek kısa ve anlaşılır Türkçe özet."
        )
    )

    notification_recommended: bool = Field(
        description=(
            "Bu değişiklik için kullanıcıya bildirim gönderilmesinin "
            "önerilip önerilmediği."
        )
    )

    evidence_text: str | None = Field(
        default=None,
        description=(
            "Bu değerlendirmenin dayandığı kaynak metin. "
            "EKLENEN veya KALDIRILAN içerikten BİREBİR kopyalanmalıdır. "
            "Yeniden yazılmış, özetlenmiş veya çevrilmiş metin olamaz. "
            "Uygun bir kaynak satır yoksa null."
        ),
    )

    evidence_type: Literal[
        "added",
        "removed",
    ] | None = Field(
        default=None,
        description=(
            "evidence_text hangi kaynaktan alındı: "
            "eklenen içerikten ise 'added', "
            "kaldırılan içerikten ise 'removed'."
        ),
    )


def _format_lines(
    lines: list[str],
    prefix: str,
) -> str:

    if not lines:
        return "Yok"

    return "\n".join(
        f"{prefix} {line}"
        for line in lines
    )


def _build_prompt(
    topic: str,
    added_lines: list[str],
    removed_lines: list[str],
    keyword_relevant: bool | None = None,
    keyword_score: float | None = None,
) -> str:

    added_content = _format_lines(
        added_lines,
        "+",
    )

    removed_content = _format_lines(
        removed_lines,
        "-",
    )

    keyword_information = ""

    if keyword_relevant is not None:
        keyword_information = f"""
Ön analiz sonucu:
- Keyword filtresi ilgili dedi mi: {keyword_relevant}
- Keyword relevance score: {keyword_score}

Bu keyword sonucu yalnızca yardımcı bir sinyaldir.
Nihai kararı sen anlamsal olarak ver.
Keyword sonucu false olsa bile değişiklik konu ile ilişkili olabilir.
"""

    return f"""
Sen bir web sitesi değişiklik izleme sisteminin analiz motorusun.

Kullanıcının takip ettiği konu:

{topic}

Web sitesinde EKLENEN içerikler:

{added_content}

Web sitesinden KALDIRILAN içerikler:

{removed_content}

{keyword_information}

Görevin:

1. Değişikliğin takip edilen konuyla gerçekten ilgili olup olmadığını
   anlamsal olarak değerlendir.

2. Sadece kelime eşleşmesine güvenme.
   Eş anlamlı kelimeleri, ürün isimlerini, model isimlerini,
   fiyat değişikliklerini ve bağlamı dikkate al.

3. Önem seviyesini belirle:

   low:
   Küçük veya kullanıcı açısından sınırlı değişiklik.

   medium:
   Kullanıcının takip ettiği konuda dikkate değer güncelleme.

   high:
   Fiyat, ürün/model lansmanı, kritik özellik, erişilebilirlik,
   politika veya önemli teknik değişiklik.

4. Kısa bir kategori oluştur.
   Tercihen aşağıdaki kategorilerden birini kullan:

   pricing
   model_update
   feature
   availability
   policy
   documentation
   security
   other

5. summary alanını Türkçe yaz.
   Kullanıcının ne değiştiğini hızlıca anlayabileceği
   1-3 cümlelik kısa bir açıklama hazırla.

6. notification_recommended yalnızca kullanıcı açısından
   bildirmeye değer bir değişiklik varsa true olmalıdır.

7. evidence_text ve evidence_type ile kararının KAYNAK KANITINI ver.

   evidence_text:
   Yukarıdaki EKLENEN veya KALDIRILAN listelerden BİREBİR
   kopyalanmış TEK BİR SATIR olmalıdır.

   Satır başındaki "+" veya "-" işaretini alma,
   yalnızca satırın kendi metnini kopyala.

   Kendi cümleni kurma, özetleme, kısaltma, çevirme.
   Birden fazla satırı birleştirme.

   Değerlendirmene en çok sebep olan satırı seç.
   Listedeki ilk satırı değil, KONUYLA EN İLGİLİ satırı seç.

   evidence_type:
   Seçtiğin satır EKLENEN listesindeyse "added",
   KALDIRILAN listesindeyse "removed".

   relevant=false ise evidence_text ve evidence_type null olabilir.
   relevant=true ise mümkün olduğunca evidence üret.
   Uygun tek bir kaynak satır bulamıyorsan ikisini de null bırak.

Önemli:
- Sitenin genel olarak değişmiş olması yeterli değildir.
- Değişiklik takip edilen konu ile ilgili değilse relevant=false.
- İlgisiz değişikliklerde notification_recommended=false.
- evidence_text uydurulmuş metin olamaz; kaynakta birebir bulunmalıdır.
- Çıktı şemaya uygun olmalıdır.
""".strip()


# ---------------------------------------------------------
# SUNUCU TARAFI EVIDENCE DOGRULAMASI
#
# Gemini'nin evidence_text alanina korukorune guvenilmez.
# Metin gercekten kaynak icerikte var mi diye kontrol edilir.
# ---------------------------------------------------------

def find_evidence_in_lines(
    evidence_text: str | None,
    lines: list[str],
) -> str | None:
    """
    evidence_text verilen satirlarin icinde birebir var mi?

    Kabul kosullari:
    - bosluklardan arindirilmis hali bir satira esit, VEYA
    - bir satirin icinde gecen bir alt dizge

    Alt dizge TEK BIR SATIR icinde bulunmalidir. Boylece
    evidence icinde satir sonu olmaz ve text fragment
    uretimi bozulmaz.

    Bulunursa kaynaktaki temizlenmis hali dondurulur.
    """

    if not evidence_text:
        return None

    cleaned = evidence_text.strip()

    if not cleaned:
        return None

    for line in lines:

        stripped_line = line.strip()

        if not stripped_line:
            continue

        if cleaned == stripped_line:
            return stripped_line

        if cleaned in stripped_line:
            return cleaned

    return None


def validate_evidence(
    result: AIAnalysisResult,
    added_lines: list[str],
    removed_lines: list[str],
) -> AIAnalysisResult:
    """
    Dogrulanamayan evidence temizlenir.

    Evidence gecersiz olsa bile AI analizinin geri kalani
    (relevant, importance, category, summary) korunur.
    """

    evidence_text = result.evidence_text
    evidence_type = result.evidence_type

    def without_evidence() -> AIAnalysisResult:
        return result.model_copy(
            update={
                "evidence_text": None,
                "evidence_type": None,
            }
        )

    # Ikisi birlikte olmali.
    if not evidence_text or evidence_type not in ("added", "removed"):
        return without_evidence()

    source_lines = (
        added_lines
        if evidence_type == "added"
        else removed_lines
    )

    matched = find_evidence_in_lines(
        evidence_text=evidence_text,
        lines=source_lines,
    )

    if matched is None:
        # Uydurulmus metin veya yanlis evidence_type.
        return without_evidence()

    return result.model_copy(
        update={
            "evidence_text": matched,
            "evidence_type": evidence_type,
        }
    )


# ---------------------------------------------------------
# RATE LIMIT TESPITI
#
# Model degistirmek YALNIZCA gercek kota/rate limit
# durumunda yapilir. Kimlik dogrulama, yetki, gecersiz
# istek veya sema hatalari model degistirilerek gizlenmez.
# ---------------------------------------------------------

RATE_LIMIT_HTTP_CODE = 429

RATE_LIMIT_STATUS = "RESOURCE_EXHAUSTED"

# Yalnizca SDK disindan gelen (yapisal bilgi tasimayan)
# hatalar icin ikincil savunma katmani.
RATE_LIMIT_MARKERS = (
    "resource_exhausted",
    "too many requests",
    "quota exceeded",
    "rate limit",
)


def is_rate_limit_error(exc: BaseException) -> bool:
    """
    Hata gercek bir kota/rate limit hatasi mi?

    google-genai APIError nesnesi HTTP kodunu (code) ve
    durum adini (status) tasidigi icin SDK hatalarinda
    KARAR TAMAMEN YAPISAL bilgiye dayanir. 401/403/400 gibi
    hatalar burada False doner.

    String eslesmesi yalnizca APIError olmayan, yapisal
    bilgi tasimayan sarmalayici hatalar icin kullanilir.
    """

    if isinstance(exc, genai_errors.APIError):

        if exc.code == RATE_LIMIT_HTTP_CODE:
            return True

        status = exc.status

        if (
            isinstance(status, str)
            and status.strip().upper() == RATE_LIMIT_STATUS
        ):
            return True

        # APIError olup 429/RESOURCE_EXHAUSTED degilse
        # kesinlikle rate limit degildir.
        return False

    message = str(exc).lower()

    return any(
        marker in message
        for marker in RATE_LIMIT_MARKERS
    )


@dataclass
class AIAnalysisOutcome:
    """
    Analiz sonucu ve cevabi gercekten ureten model.

    model_name AIAnalysis kaydina yazilir; boylece DB
    hangi modelin cevap verdigini dogru gosterir.
    """

    result: AIAnalysisResult
    model_name: str
    used_fallback: bool


@dataclass
class ModelOutcome:
    """
    run_with_quota_fallback sonucu.

    result cagiranin verdigi generate fonksiyonunun
    dondurdugu degerdir; bu modul icin AIAnalysisResult,
    bolum secimi icin SectionSelection olur.
    """

    result: object
    model_name: str
    used_fallback: bool


# ---------------------------------------------------------
# PRIMARY -> FALLBACK CALISTIRICI
#
# Kota/rate limit disinda MODEL DEGISTIRILMEZ.
# Bir mantiksal analiz icin en fazla IKI model cagrisi
# yapilir; retry dongusu veya ucuncu model yoktur.
#
# Hem degisiklik analizi hem de auto_section bolum secimi
# bu ayni davranisi kullanir.
# ---------------------------------------------------------

async def run_with_quota_fallback(
    generate,
    error_class=AIAnalyzerError,
    error_prefix: str = "Gemini analiz hatası",
) -> ModelOutcome:
    """
    generate(client, model_name) cagrisini primary model ile
    calistirir; yalnizca kota/rate limit hatasinda yedek
    modeli BIR KEZ dener.

    generate icinden firlatilan error_class hatalari
    (bos cevap, sema hatasi) model degistirilerek gizlenmez.
    """

    primary_model = settings.gemini_model

    fallback_model = (
        settings.gemini_fallback_model or ""
    ).strip()

    client = genai.Client(
        api_key=settings.gemini_api_key,
    )

    try:
        # -------------------------------------------------
        # 1. PRIMARY MODEL
        # -------------------------------------------------

        try:
            result = await generate(
                client,
                primary_model,
            )

            return ModelOutcome(
                result=result,
                model_name=primary_model,
                used_fallback=False,
            )

        except error_class:
            # Bos cevap veya sema hatasi.
            # Model degistirmek bunu cozmez.
            raise

        except Exception as primary_error:

            if not is_rate_limit_error(primary_error):
                # Kimlik dogrulama, yetki, gecersiz istek vb.
                # Model degistirerek gizlenmez.
                raise error_class(
                    f"{error_prefix}: "
                    f"{type(primary_error).__name__}: {primary_error}"
                ) from primary_error

            if not fallback_model or fallback_model == primary_model:
                raise error_class(
                    f"Gemini kota sınırına takıldı ({primary_model}) "
                    f"ve kullanılabilir yedek model yok: "
                    f"{primary_error}"
                ) from primary_error

            logger.warning(
                "Gemini kota siniri. primary=%s fallback=%s hata=%s",
                primary_model,
                fallback_model,
                primary_error,
            )

        # -------------------------------------------------
        # 2. FALLBACK MODEL (yalnizca bir kez)
        # -------------------------------------------------

        try:
            result = await generate(
                client,
                fallback_model,
            )

            logger.info(
                "Gemini yedek model ile tamamlandi. model=%s",
                fallback_model,
            )

            return ModelOutcome(
                result=result,
                model_name=fallback_model,
                used_fallback=True,
            )

        except error_class:
            raise

        except Exception as fallback_error:
            raise error_class(
                f"Gemini kota sınırına takıldı ({primary_model}) ve "
                f"yedek model de başarısız oldu ({fallback_model}): "
                f"{type(fallback_error).__name__}: {fallback_error}"
            ) from fallback_error

    finally:
        await client.aio.aclose()


# ---------------------------------------------------------
# GENERATION CONFIG
#
# Gemini 3.7 gecis rehberi sampling parametrelerinin
# (temperature / top_p / top_k) gonderilmemesini istiyor.
# Bu yuzden config model bazli uretilir.
#
# SDK bu alanlar verilmedigi zaman onlari None birakir ve
# istek govdesine hic eklemez (exclude_none), yani parametreyi
# atlamak icin baska bir sey yapmak gerekmiyor.
# ---------------------------------------------------------

# Sampling parametresi GONDERILMEYECEK model onekleri.
NO_SAMPLING_MODEL_PREFIXES = (
    "gemini-3.7",
)

# Sampling destekleyen modellerde kullanilan deger.
# Analiz deterministik olsun diye 0.0.
ANALYSIS_TEMPERATURE = 0.0


def uses_sampling_parameters(model_name: str) -> bool:
    """
    Bu modele temperature/top_p/top_k gonderilmeli mi?
    """

    normalized = (model_name or "").strip().lower()

    return not normalized.startswith(
        NO_SAMPLING_MODEL_PREFIXES
    )


def build_generation_config(
    model_name: str,
    response_schema=AIAnalysisResult,
) -> types.GenerateContentConfig:
    """
    Model icin generation config uretir.

    Sampling disindaki tum davranis (structured JSON output,
    response schema, automatic function calling) her modelde
    BIREBIR aynidir.

    response_schema yalnizca farkli bir yapisal cikti gereken
    cagrilar icin degistirilir (ornegin auto_section bolum
    secimi). Degisiklik analizi varsayilani kullanir.
    """

    common = {
        "response_mime_type": "application/json",
        "response_schema": response_schema,
        "automatic_function_calling": (
            types.AutomaticFunctionCallingConfig(
                disable=True
            )
        ),
    }

    if uses_sampling_parameters(model_name):
        return types.GenerateContentConfig(
            temperature=ANALYSIS_TEMPERATURE,
            **common,
        )

    # top_p ve top_k zaten hicbir zaman gonderilmiyordu;
    # burada temperature de atlanir.
    return types.GenerateContentConfig(
        **common
    )


async def _generate_analysis(
    client,
    model_name: str,
    prompt: str,
    added_lines: list[str],
    removed_lines: list[str],
) -> AIAnalysisResult:
    """
    Tek bir model cagrisi.

    Prompt, response_mime_type, response_schema, automatic
    function calling ayari ve evidence dogrulamasi primary ile
    fallback icin BIREBIR aynidir.

    Tek fark sampling parametreleridir: bunlar yalnizca
    destekleyen modellere gonderilir.
    """

    response = await client.aio.models.generate_content(
        model=model_name,
        contents=prompt,
        config=build_generation_config(model_name),
    )

    if not response.text:
        raise AIAnalyzerError(
            "Gemini boş cevap döndürdü."
        )

    try:
        result = AIAnalysisResult.model_validate_json(
            response.text
        )

    except ValidationError as exc:
        raise AIAnalyzerError(
            "Gemini cevabı beklenen şemaya uygun değil."
        ) from exc

    # Evidence dogrulamasi fallback cevabina da aynen uygulanir.
    return validate_evidence(
        result=result,
        added_lines=added_lines,
        removed_lines=removed_lines,
    )


async def analyze_change_with_ai(
    topic: str,
    added_lines: list[str],
    removed_lines: list[str] | None = None,
    keyword_relevant: bool | None = None,
    keyword_score: float | None = None,
) -> AIAnalysisOutcome:
    """
    Degisikligi Gemini ile analiz eder.

    Primary model kota/rate limit hatasi verirse yedek model
    YALNIZCA BIR KEZ denenir. Bir analiz icin en fazla
    iki model cagrisi yapilir; retry dongusu veya ucuncu
    model yoktur.
    """

    if not topic.strip():
        raise ValueError(
            "Takip edilen konu boş olamaz."
        )

    removed_lines = removed_lines or []

    if not added_lines and not removed_lines:
        raise ValueError(
            "Analiz edilecek değişiklik bulunamadı."
        )

    prompt = _build_prompt(
        topic=topic,
        added_lines=added_lines,
        removed_lines=removed_lines,
        keyword_relevant=keyword_relevant,
        keyword_score=keyword_score,
    )

    async def generate(client, model_name):
        return await _generate_analysis(
            client=client,
            model_name=model_name,
            prompt=prompt,
            added_lines=added_lines,
            removed_lines=removed_lines,
        )

    outcome = await run_with_quota_fallback(
        generate=generate,
        error_class=AIAnalyzerError,
        error_prefix="Gemini analiz hatası",
    )

    return AIAnalysisOutcome(
        result=outcome.result,
        model_name=outcome.model_name,
        used_fallback=outcome.used_fallback,
    )
