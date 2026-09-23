"""
Gemini primary -> fallback model testleri.

GERCEK GEMINI API CAGRISI YAPILMAZ.

Tum cagrilar sahte (fake) client ile karsilanir ve test suresince
socket baglantisi bloklanir; Google'a HTTP istegi gitmedigi
ayrica assert edilir.

DB testi dis transaction icinde calisir ve rollback edilir.

Calistirma:
    python -m tests.test_gemini_fallback
"""

import asyncio
import socket

from dataclasses import dataclass

from google.genai import errors as genai_errors
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database import engine
from app.models.ai_analysis import AIAnalysis
from app.models.change import Change
from app.models.monitor import Monitor
from app.models.snapshot import Snapshot
from app.repositories.ai_analysis_repository import create_ai_analysis
from app.services import ai_analyzer
from app.services.ai_analyzer import (
    AIAnalyzerError,
    analyze_change_with_ai,
    is_rate_limit_error,
)
from app.services.ai_analyzer import (
    AIAnalysisResult as AIAnalysisResultRef,
)


PASSED = []
FAILED = []


PRIMARY_MODEL = "gemini-3.6-flash"
FALLBACK_MODEL = "gemini-3.7-flash"


ARC_LINE = "ARC-AGI-1 benchmark reaches 44 percent at 67 cents"

ADDED_LINES = [
    "AnkiDroid: Google Play no longer allowing Open Collective link",
    ARC_LINE,
]

REMOVED_LINES = ["Eski model fiyati 100 dolar"]


VALID_JSON = (
    '{"relevant": true, "importance": "medium", '
    '"category": "model_update", '
    '"summary": "ARC-AGI-1 testinde yeni sonuc.", '
    '"notification_recommended": true, '
    f'"evidence_text": "{ARC_LINE}", '
    '"evidence_type": "added"}'
)


def check(label: str, condition: bool, detail: str = "") -> None:

    status = "PASS" if condition else "FAIL"

    print(f"[{status}] {label}" + (f" -> {detail}" if detail else ""))

    if condition:
        PASSED.append(label)
    else:
        FAILED.append(f"{label} -> {detail}")


# ---------------------------------------------------------
# AG ERISIMI ENGELI
# ---------------------------------------------------------

NETWORK_ATTEMPTS = []

_real_connect = socket.socket.connect
_real_create_connection = socket.create_connection


LOOPBACK_HOSTS = {
    "127.0.0.1",
    "::1",
    "localhost",
    "",
}


def _is_loopback(address) -> bool:
    """
    asyncio'nun kendi self-pipe soketi ve yerel PostgreSQL
    baglantisi loopback uzerinden gider; bunlar engellenmez.

    Engellenen sey DISARIYA (Google API) cikan baglantidir.
    """

    if not isinstance(address, tuple) or not address:
        return True

    host = address[0]

    if not isinstance(host, str):
        return True

    return host in LOOPBACK_HOSTS


def _blocked_connect(self, address, *args, **kwargs):
    if _is_loopback(address):
        return _real_connect(self, address, *args, **kwargs)

    NETWORK_ATTEMPTS.append(address)

    raise AssertionError(
        f"Test sirasinda dis ag baglantisi denendi: {address}"
    )


def _blocked_create_connection(address, *args, **kwargs):
    if _is_loopback(address):
        return _real_create_connection(address, *args, **kwargs)

    NETWORK_ATTEMPTS.append(address)

    raise AssertionError(
        f"Test sirasinda dis ag baglantisi denendi: {address}"
    )


def block_network():
    socket.socket.connect = _blocked_connect
    socket.create_connection = _blocked_create_connection


def unblock_network():
    socket.socket.connect = _real_connect
    socket.create_connection = _real_create_connection


# ---------------------------------------------------------
# SAHTE GEMINI CLIENT
# ---------------------------------------------------------

@dataclass
class FakeResponse:
    text: str


class FakeModels:
    def __init__(self, behaviours, calls):
        self._behaviours = behaviours
        self._calls = calls

    async def generate_content(self, model, contents, config):
        self._calls.append(
            {
                "model": model,
                "prompt": contents,
                "config": config,
            }
        )

        behaviour = self._behaviours.get(model)

        if behaviour is None:
            raise AssertionError(
                f"Beklenmeyen model cagrisi: {model}"
            )

        if isinstance(behaviour, BaseException):
            raise behaviour

        return FakeResponse(text=behaviour)


class FakeAio:
    def __init__(self, behaviours, calls, closed):
        self.models = FakeModels(behaviours, calls)
        self._closed = closed

    async def aclose(self):
        self._closed.append(True)


class FakeClient:
    def __init__(self, behaviours, calls, closed):
        self.aio = FakeAio(behaviours, calls, closed)


def install_fake_client(behaviours):
    """
    genai.Client yerine sahte client kurar.

    Donen (calls, closed) listeleri cagri sayisini ve
    client'in kapatilip kapatilmadigini izler.
    """

    calls = []
    closed = []

    def factory(*args, **kwargs):
        return FakeClient(behaviours, calls, closed)

    ai_analyzer.genai.Client = factory

    return calls, closed


def api_error(code: int, status: str, message: str = "hata"):
    return genai_errors.APIError(
        code,
        {
            "error": {
                "code": code,
                "status": status,
                "message": message,
            }
        },
    )


def run(behaviours):
    calls, closed = install_fake_client(behaviours)

    try:
        outcome = asyncio.run(
            analyze_change_with_ai(
                topic="yapay zeka modelleri",
                added_lines=ADDED_LINES,
                removed_lines=REMOVED_LINES,
            )
        )

        return outcome, calls, closed, None

    except Exception as exc:
        return None, calls, closed, exc


def main() -> None:

    print("=" * 70)
    print("GEMINI FALLBACK TESTLERI")
    print("(gercek API cagrisi YOK, ag bloklu)")
    print("=" * 70)

    original_client = ai_analyzer.genai.Client
    original_primary = ai_analyzer.settings.gemini_model
    original_fallback = ai_analyzer.settings.gemini_fallback_model

    ai_analyzer.settings.gemini_model = PRIMARY_MODEL
    ai_analyzer.settings.gemini_fallback_model = FALLBACK_MODEL

    block_network()

    try:
        # -------------------------------------------------
        # 1. PRIMARY BASARILI
        # -------------------------------------------------

        print("\n--- 1. primary basarili ---")

        outcome, calls, closed, error = run(
            {PRIMARY_MODEL: VALID_JSON}
        )

        check(
            "primary basarili -> hata yok",
            error is None,
            repr(error),
        )

        check(
            "toplam model cagrisi = 1",
            len(calls) == 1,
            f"cagri={len(calls)}",
        )

        check(
            "fallback CAGRILMADI",
            all(call["model"] == PRIMARY_MODEL for call in calls),
            f"{[c['model'] for c in calls]}",
        )

        check(
            "used model = primary",
            outcome.model_name == PRIMARY_MODEL
            and outcome.used_fallback is False,
            f"{outcome.model_name}",
        )

        check(
            "analiz sonucu dondu",
            outcome.result.relevant is True
            and outcome.result.category == "model_update",
        )

        check(
            "client kapatildi",
            closed == [True],
        )

        primary_config = calls[0]["config"]
        primary_prompt = calls[0]["prompt"]

        # -------------------------------------------------
        # 2. PRIMARY QUOTA -> FALLBACK BASARILI
        # -------------------------------------------------

        print("\n--- 2. primary quota -> fallback ---")

        outcome, calls, closed, error = run(
            {
                PRIMARY_MODEL: api_error(
                    429, "RESOURCE_EXHAUSTED", "Quota exceeded"
                ),
                FALLBACK_MODEL: VALID_JSON,
            }
        )

        check(
            "fallback sonrasi hata yok",
            error is None,
            repr(error),
        )

        check(
            "toplam model cagrisi = 2",
            len(calls) == 2,
            f"cagri={len(calls)}",
        )

        check(
            "sira: once primary sonra fallback",
            [call["model"] for call in calls]
            == [PRIMARY_MODEL, FALLBACK_MODEL],
            f"{[c['model'] for c in calls]}",
        )

        check(
            "used model = fallback",
            outcome.model_name == FALLBACK_MODEL
            and outcome.used_fallback is True,
            f"{outcome.model_name}",
        )

        # -------------------------------------------------
        # AYNI AI DAVRANIŞI
        # -------------------------------------------------

        print("\n--- fallback ayni ayarlarla cagriliyor ---")

        fallback_config = calls[1]["config"]
        fallback_prompt = calls[1]["prompt"]

        check(
            "prompt birebir ayni",
            fallback_prompt == calls[0]["prompt"] == primary_prompt,
        )

        # --- 1-3. SAMPLING PARAMETRELERI ---
        #
        # Gemini 3.7 sampling parametresi istemiyor.
        # Istek govdesine gercekten ne gittigini gormek icin
        # config serilestirilerek kontrol edilir.

        fallback_payload = fallback_config.model_dump(
            exclude_none=True
        )

        primary_payload = primary_config.model_dump(
            exclude_none=True
        )

        check(
            "3.7 config icinde temperature YOK",
            "temperature" not in fallback_payload
            and fallback_config.temperature is None,
            f"{fallback_payload.get('temperature')!r}",
        )

        check(
            "3.7 config icinde top_p YOK",
            "top_p" not in fallback_payload
            and fallback_config.top_p is None,
        )

        check(
            "3.7 config icinde top_k YOK",
            "top_k" not in fallback_payload
            and fallback_config.top_k is None,
        )

        check(
            "3.7 isteginde hicbir sampling alani yok",
            not {"temperature", "top_p", "top_k"}
            & set(fallback_payload),
            f"{sorted(fallback_payload)}",
        )

        check(
            "primary 3.6 davranisi degismedi (temperature=0.0)",
            primary_payload.get("temperature") == 0.0,
            f"{primary_payload.get('temperature')!r}",
        )

        check(
            "response_mime_type ayni",
            fallback_config.response_mime_type
            == primary_config.response_mime_type
            == "application/json",
        )

        check(
            "response_schema ayni",
            fallback_config.response_schema
            is primary_config.response_schema,
        )

        check(
            "automatic function calling ayari ayni",
            fallback_config.automatic_function_calling.disable
            is primary_config.automatic_function_calling.disable
            is True,
        )

        # -------------------------------------------------
        # 429 DISINDA RESOURCE_EXHAUSTED
        # -------------------------------------------------

        print("\n--- status bazli tespit ---")

        outcome, calls, closed, error = run(
            {
                PRIMARY_MODEL: api_error(
                    0, "RESOURCE_EXHAUSTED", "quota"
                ),
                FALLBACK_MODEL: VALID_JSON,
            }
        )

        check(
            "RESOURCE_EXHAUSTED status'u fallback tetikliyor",
            error is None and len(calls) == 2,
            f"cagri={len(calls)} hata={error!r}",
        )

        # -------------------------------------------------
        # 3. IKI MODEL DE QUOTA
        # -------------------------------------------------

        print("\n--- 3. iki model de quota ---")

        outcome, calls, closed, error = run(
            {
                PRIMARY_MODEL: api_error(
                    429, "RESOURCE_EXHAUSTED", "Quota exceeded"
                ),
                FALLBACK_MODEL: api_error(
                    429, "RESOURCE_EXHAUSTED", "Quota exceeded"
                ),
            }
        )

        check(
            "AIAnalyzerError firlatiliyor",
            isinstance(error, AIAnalyzerError),
            f"{type(error).__name__}",
        )

        check(
            "hata mesajinda iki model de geciyor",
            PRIMARY_MODEL in str(error)
            and FALLBACK_MODEL in str(error),
            str(error)[:90],
        )

        check(
            "toplam model cagrisi = 2 (sonsuz retry yok)",
            len(calls) == 2,
            f"cagri={len(calls)}",
        )

        check(
            "hata durumunda da client kapatildi",
            closed == [True],
        )

        # -------------------------------------------------
        # 4. PRIMARY QUOTA -> FALLBACK NORMAL HATA
        # -------------------------------------------------

        print("\n--- 4. fallback normal API hatasi ---")

        outcome, calls, closed, error = run(
            {
                PRIMARY_MODEL: api_error(
                    429, "RESOURCE_EXHAUSTED", "Quota exceeded"
                ),
                FALLBACK_MODEL: api_error(
                    500, "INTERNAL", "Sunucu hatasi"
                ),
            }
        )

        check(
            "AIAnalyzerError firlatiliyor",
            isinstance(error, AIAnalyzerError),
            f"{type(error).__name__}",
        )

        check(
            "ucuncu model denenmedi",
            len(calls) == 2,
            f"cagri={len(calls)}",
        )

        # -------------------------------------------------
        # 5-7. FALLBACK YAPILMAMASI GEREKEN DURUMLAR
        # -------------------------------------------------

        print("\n--- 5-7. fallback yapilmamasi gereken hatalar ---")

        no_fallback_cases = [
            ("authentication (401)", api_error(401, "UNAUTHENTICATED", "API key invalid")),
            ("permission (403)", api_error(403, "PERMISSION_DENIED", "izin yok")),
            ("invalid request (400)", api_error(400, "INVALID_ARGUMENT", "malformed request")),
            ("not found (404)", api_error(404, "NOT_FOUND", "model yok")),
            ("server error (500)", api_error(500, "INTERNAL", "sunucu hatasi")),
            ("programlama hatasi", TypeError("beklenmeyen tip")),
        ]

        for label, behaviour in no_fallback_cases:
            outcome, calls, closed, error = run(
                {
                    PRIMARY_MODEL: behaviour,
                    FALLBACK_MODEL: VALID_JSON,
                }
            )

            check(
                f"{label}: fallback CAGRILMADI",
                len(calls) == 1
                and calls[0]["model"] == PRIMARY_MODEL,
                f"cagri={[c['model'] for c in calls]}",
            )

            check(
                f"{label}: AIAnalyzerError firlatiliyor",
                isinstance(error, AIAnalyzerError),
                f"{type(error).__name__}",
            )

        # Sema / bos cevap hatalari
        parse_cases = [
            ("sema hatasi", '{"relevant": "evet"}'),
            ("gecersiz json", "bu json degil"),
            ("bos cevap", ""),
        ]

        for label, behaviour in parse_cases:
            outcome, calls, closed, error = run(
                {
                    PRIMARY_MODEL: behaviour,
                    FALLBACK_MODEL: VALID_JSON,
                }
            )

            check(
                f"{label}: fallback CAGRILMADI",
                len(calls) == 1,
                f"cagri={[c['model'] for c in calls]}",
            )

            check(
                f"{label}: AIAnalyzerError firlatiliyor",
                isinstance(error, AIAnalyzerError),
                f"{type(error).__name__}: {error}",
            )

        # -------------------------------------------------
        # 8. FALLBACK EVIDENCE VALIDATION
        # -------------------------------------------------

        print("\n--- 8. fallback evidence dogrulamasi ---")

        outcome, calls, closed, error = run(
            {
                PRIMARY_MODEL: api_error(429, "RESOURCE_EXHAUSTED"),
                FALLBACK_MODEL: VALID_JSON,
            }
        )

        check(
            "fallback evidence dogrulamadan gecti ve kabul edildi",
            outcome.result.evidence_text == ARC_LINE
            and outcome.result.evidence_type == "added",
            f"{outcome.result.evidence_text!r}",
        )

        removed_evidence_json = VALID_JSON.replace(
            f'"evidence_text": "{ARC_LINE}"',
            '"evidence_text": "Eski model fiyati 100 dolar"',
        ).replace('"evidence_type": "added"', '"evidence_type": "removed"')

        outcome, calls, closed, error = run(
            {
                PRIMARY_MODEL: api_error(429, "RESOURCE_EXHAUSTED"),
                FALLBACK_MODEL: removed_evidence_json,
            }
        )

        check(
            "fallback removed evidence de dogrulaniyor",
            outcome.result.evidence_text
            == "Eski model fiyati 100 dolar"
            and outcome.result.evidence_type == "removed",
        )

        # -------------------------------------------------
        # 9. FALLBACK HALLUCINATED EVIDENCE
        # -------------------------------------------------

        print("\n--- 9. fallback uydurulmus evidence ---")

        hallucinated_json = VALID_JSON.replace(
            ARC_LINE,
            "Kaynakta olmayan uydurma bir cumle",
        )

        outcome, calls, closed, error = run(
            {
                PRIMARY_MODEL: api_error(429, "RESOURCE_EXHAUSTED"),
                FALLBACK_MODEL: hallucinated_json,
            }
        )

        check(
            "fallback uydurulmus evidence temizlendi",
            outcome.result.evidence_text is None
            and outcome.result.evidence_type is None,
            f"{outcome.result.evidence_text!r}",
        )

        check(
            "evidence temizlense de analiz alanlari korundu",
            outcome.result.relevant is True
            and outcome.result.importance == "medium"
            and outcome.result.category == "model_update"
            and outcome.result.notification_recommended is True,
        )

        check(
            "evidence temizlense de used model fallback",
            outcome.model_name == FALLBACK_MODEL,
        )

        # -------------------------------------------------
        # FALLBACK YAPILANDIRILMAMISSA
        # -------------------------------------------------

        print("\n--- fallback yapilandirilmamis ---")

        for value in (None, "", "   ", PRIMARY_MODEL):
            ai_analyzer.settings.gemini_fallback_model = value

            outcome, calls, closed, error = run(
                {
                    PRIMARY_MODEL: api_error(
                        429, "RESOURCE_EXHAUSTED"
                    ),
                }
            )

            check(
                f"fallback={value!r}: ikinci cagri yapilmadi",
                len(calls) == 1,
                f"cagri={len(calls)}",
            )

            check(
                f"fallback={value!r}: AIAnalyzerError",
                isinstance(error, AIAnalyzerError),
            )

        ai_analyzer.settings.gemini_fallback_model = FALLBACK_MODEL

        # -------------------------------------------------
        # 13. MAKSIMUM IKI INVOCATION
        # -------------------------------------------------

        print("\n--- 13. maksimum iki cagri ---")

        max_calls = 0

        scenarios = [
            {PRIMARY_MODEL: VALID_JSON},
            {
                PRIMARY_MODEL: api_error(429, "RESOURCE_EXHAUSTED"),
                FALLBACK_MODEL: VALID_JSON,
            },
            {
                PRIMARY_MODEL: api_error(429, "RESOURCE_EXHAUSTED"),
                FALLBACK_MODEL: api_error(429, "RESOURCE_EXHAUSTED"),
            },
            {
                PRIMARY_MODEL: api_error(429, "RESOURCE_EXHAUSTED"),
                FALLBACK_MODEL: TypeError("bozuk"),
            },
        ]

        for behaviours in scenarios:
            _, calls, _, _ = run(behaviours)
            max_calls = max(max_calls, len(calls))

        check(
            "hicbir senaryoda 2'den fazla model cagrisi yok",
            max_calls == 2,
            f"maksimum={max_calls}",
        )

        # -------------------------------------------------
        # GENERATION CONFIG (birim)
        # -------------------------------------------------

        print()
        print("--- generation config (birim) ---")

        from app.services.ai_analyzer import (
            build_generation_config,
            uses_sampling_parameters,
        )

        no_sampling_models = [
            "gemini-3.7-flash",
            "gemini-3.7-pro",
            "GEMINI-3.7-FLASH",
            "  gemini-3.7-flash  ",
        ]

        for model in no_sampling_models:
            config = build_generation_config(model)
            payload = config.model_dump(exclude_none=True)

            check(
                f"{model.strip()!r}: sampling alani gonderilmiyor",
                not {"temperature", "top_p", "top_k"}
                & set(payload),
                f"{sorted(payload)}",
            )

            check(
                f"{model.strip()!r}: structured output korunuyor",
                payload.get("response_mime_type")
                == "application/json"
                and config.response_schema is AIAnalysisResultRef
                and config.automatic_function_calling.disable
                is True,
            )

        for model in ["gemini-3.6-flash", "gemini-2.5-flash"]:
            config = build_generation_config(model)
            payload = config.model_dump(exclude_none=True)

            check(
                f"{model!r}: temperature=0.0 korunuyor",
                payload.get("temperature") == 0.0,
                f"{payload.get('temperature')!r}",
            )

            check(
                f"{model!r}: top_p/top_k zaten gonderilmiyor",
                "top_p" not in payload and "top_k" not in payload,
            )

        check(
            "uses_sampling_parameters dogru ayirt ediyor",
            uses_sampling_parameters("gemini-3.6-flash") is True
            and uses_sampling_parameters("gemini-3.7-flash") is False,
        )

        check(
            "bos/None model adi guvenli sekilde ele aliniyor",
            uses_sampling_parameters("") is True
            and uses_sampling_parameters(None) is True,
        )

        # -------------------------------------------------
        # RATE LIMIT TESPITI (birim)
        # -------------------------------------------------

        print("\n--- rate limit tespiti ---")

        check(
            "429 rate limit sayiliyor",
            is_rate_limit_error(api_error(429, "RESOURCE_EXHAUSTED")),
        )

        check(
            "429 farkli status ile de rate limit",
            is_rate_limit_error(api_error(429, "SOMETHING_ELSE")),
        )

        check(
            "RESOURCE_EXHAUSTED status'u rate limit",
            is_rate_limit_error(api_error(0, "resource_exhausted")),
        )

        for code, status in [
            (401, "UNAUTHENTICATED"),
            (403, "PERMISSION_DENIED"),
            (400, "INVALID_ARGUMENT"),
            (404, "NOT_FOUND"),
            (500, "INTERNAL"),
            (503, "UNAVAILABLE"),
        ]:
            check(
                f"APIError {code}/{status} rate limit DEGIL",
                not is_rate_limit_error(api_error(code, status)),
            )

        check(
            "APIError icindeki 'quota' metni yapisal karari bozmuyor",
            not is_rate_limit_error(
                api_error(400, "INVALID_ARGUMENT", "quota exceeded")
            ),
        )

        check(
            "SDK disi hatada dar string kontrolu calisiyor",
            is_rate_limit_error(
                RuntimeError("429 Too Many Requests")
            )
            and is_rate_limit_error(
                RuntimeError("Quota exceeded for model")
            ),
        )

        check(
            "SDK disi alakasiz hata rate limit degil",
            not is_rate_limit_error(ValueError("gecersiz deger"))
            and not is_rate_limit_error(
                RuntimeError("invalid api key")
            ),
        )

        # -------------------------------------------------
        # 10-11. DB MODEL_NAME
        # -------------------------------------------------

        print("\n--- 10-11. DB model_name ---")

        from app.core.database import SessionLocal

        baseline = SessionLocal()

        try:
            persistent_count = baseline.scalar(
                select(func.count()).select_from(AIAnalysis)
            )
        finally:
            baseline.close()

        connection = engine.connect()
        transaction = connection.begin()

        session = Session(
            bind=connection,
            join_transaction_mode="create_savepoint",
        )

        try:
            count_before = session.scalar(
                select(func.count()).select_from(AIAnalysis)
            )

            monitor = session.scalars(
                select(Monitor).limit(1)
            ).first()

            old_snapshot = Snapshot(
                monitor_id=monitor.id,
                title="t",
                content="a",
                content_hash="h1",
            )

            new_snapshot = Snapshot(
                monitor_id=monitor.id,
                title="t",
                content="b",
                content_hash="h2",
            )

            session.add_all([old_snapshot, new_snapshot])
            session.flush()

            def make_change():
                change = Change(
                    monitor_id=monitor.id,
                    old_snapshot_id=old_snapshot.id,
                    new_snapshot_id=new_snapshot.id,
                    relevant=True,
                    relevance_score=1.0,
                    added_content="\n".join(ADDED_LINES),
                    removed_content="\n".join(REMOVED_LINES),
                )

                session.add(change)
                session.flush()

                return change

            # Primary basarili senaryosu
            outcome, _, _, _ = run({PRIMARY_MODEL: VALID_JSON})

            primary_record = create_ai_analysis(
                db=session,
                change_id=make_change().id,
                model_name=outcome.model_name,
                keyword_relevant=True,
                keyword_score=1.0,
                relevant=outcome.result.relevant,
                importance=outcome.result.importance,
                category=outcome.result.category,
                summary=outcome.result.summary,
                notification_recommended=(
                    outcome.result.notification_recommended
                ),
                evidence_text=outcome.result.evidence_text,
                evidence_type=outcome.result.evidence_type,
            )

            check(
                "primary basarida model_name = gemini-3.6-flash",
                primary_record.model_name == PRIMARY_MODEL,
                primary_record.model_name,
            )

            # Fallback senaryosu
            outcome, _, _, _ = run(
                {
                    PRIMARY_MODEL: api_error(
                        429, "RESOURCE_EXHAUSTED"
                    ),
                    FALLBACK_MODEL: VALID_JSON,
                }
            )

            fallback_record = create_ai_analysis(
                db=session,
                change_id=make_change().id,
                model_name=outcome.model_name,
                keyword_relevant=True,
                keyword_score=1.0,
                relevant=outcome.result.relevant,
                importance=outcome.result.importance,
                category=outcome.result.category,
                summary=outcome.result.summary,
                notification_recommended=(
                    outcome.result.notification_recommended
                ),
                evidence_text=outcome.result.evidence_text,
                evidence_type=outcome.result.evidence_type,
            )

            check(
                "fallback basarida model_name = gemini-3.7-flash",
                fallback_record.model_name == FALLBACK_MODEL,
                fallback_record.model_name,
            )

            check(
                "iki kayit farkli model adiyla saklandi",
                primary_record.model_name
                != fallback_record.model_name,
            )

            session.flush()

            check(
                "test icinde gecici kayit olustu",
                session.scalar(
                    select(func.count()).select_from(AIAnalysis)
                )
                > count_before,
            )

        finally:
            session.close()
            transaction.rollback()
            connection.close()

        verify = SessionLocal()

        try:
            final_count = verify.scalar(
                select(func.count()).select_from(AIAnalysis)
            )

            check(
                "rollback sonrasi kalici kayit yok",
                final_count == persistent_count,
                f"once={persistent_count} sonra={final_count}",
            )

        finally:
            verify.close()

        # -------------------------------------------------
        # 12. IKI MODEL BASARISIZ -> ORCHESTRATION
        # -------------------------------------------------

        print("\n--- 12. iki model basarisiz: pipeline davranisi ---")

        outcome, calls, closed, error = run(
            {
                PRIMARY_MODEL: api_error(429, "RESOURCE_EXHAUSTED"),
                FALLBACK_MODEL: api_error(429, "RESOURCE_EXHAUSTED"),
            }
        )

        check(
            "AIAnalyzerError, db_monitor_service tarafindan "
            "yakalanabilir tiptedir",
            isinstance(error, AIAnalyzerError),
        )

        import inspect

        from app.services import db_monitor_service

        source = inspect.getsource(
            db_monitor_service.run_database_monitor
        )

        analysis_start = source.index("10. GEMINI AI ANALYSIS")

        check(
            "snapshot ve change AI cagrisindan ONCE olusturuluyor",
            source.index("create_snapshot(") < analysis_start
            and source.index("create_change(") < analysis_start,
        )

        check(
            "AIAnalyzerError yakalaniyor ve ai_error'a yaziliyor",
            "except AIAnalyzerError as exc:" in source
            and "ai_error = str(exc)" in source,
        )

        check(
            "hata durumunda ai_analysis_record None kalir "
            "(notification olusmaz)",
            "ai_analysis_record = None" in source,
        )

        from app.services import monitor_run_service

        run_source = inspect.getsource(
            monitor_run_service.execute_monitor_run
        )

        check(
            "notification yalnizca ai_analysis_record varken olusur",
            "result.ai_analysis_record is not None" in run_source,
        )

        check(
            "last_checked_at guncellemesi AI hatasindan sonra da "
            "calisiyor (snapshot/change kaybolmaz)",
            source.rindex("update_last_checked(") > analysis_start,
        )

        # -------------------------------------------------
        # AG ERISIMI
        # -------------------------------------------------

        print("\n--- canli API cagrisi ---")

        check(
            "test boyunca hicbir ag baglantisi denenmedi",
            not NETWORK_ATTEMPTS,
            f"{NETWORK_ATTEMPTS}",
        )

    finally:
        unblock_network()

        ai_analyzer.genai.Client = original_client
        ai_analyzer.settings.gemini_model = original_primary
        ai_analyzer.settings.gemini_fallback_model = original_fallback

    print("\n" + "=" * 70)
    print(f"BASARILI : {len(PASSED)}")
    print(f"BASARISIZ: {len(FAILED)}")

    if FAILED:
        print("-" * 70)
        for line in FAILED:
            print(line)

        raise SystemExit(1)

    print("TUM TESTLER BASARILI")
    print("=" * 70)


if __name__ == "__main__":
    main()
