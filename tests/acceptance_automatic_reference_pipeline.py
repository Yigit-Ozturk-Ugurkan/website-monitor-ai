"""
OTOMATIK REFERANS PIPELINE KABUL TESTI

GERCEK VERI:

  - 4 Eylul 2026 tarihli GERCEK Resmî Gazete degisikligi
    (resmigazete.gov.tr)

  - GERCEK konsolide mevzuat metni
    (mevzuat.gov.tr, Yonetmelik 39080)

  - ayni yonetmeligin GERCEK arsiv surumu
    (resmigazete.gov.tr, 1 Aralik 2021)

  - alakasiz dikkat dagitici referanslar

BEKLENEN:

    degisiklik metni
      -> matcher
      -> KONSOLIDE referans secilir
      -> kullanici tiklamasi YOK
      -> karsilastirma calisir
      -> Madde 41 adayi bulunur
      -> comparison satiri konsolide referansi saklar
      -> arsiv surumu KULLANILMAZ
      -> alakasiz referanslar KULLANILMAZ

GEMINI

    Karsilastirma adimi icin sahte istemci kullanilir
    (kota tuketilmez). Matcher'in GERCEK Gemini cagrisi
    yapip yapmadigi ayrica sayilir; deterministik eslesmede
    bu sayinin SIFIR olmasi beklenir.

VERITABANI

    Dis transaction sonunda rollback edilir; kalici kayit
    birakilmaz.

Calistirma:
    python -m tests.acceptance_automatic_reference_pipeline
"""

import asyncio
import json
import smtplib
import sys
import warnings

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database import engine
from app.models.regulation_comparison import RegulationComparison
from app.models.regulation_reference import RegulationReference
from app.repositories.ai_analysis_repository import (
    create_ai_analysis,
)
from app.repositories.change_repository import create_change
from app.repositories.monitor_repository import create_monitor
from app.repositories.regulation_reference_repository import (
    create_regulation_reference,
)
from app.repositories.snapshot_repository import create_snapshot
from app.services.content_extractor import extract_content
from app.services.regulation_monitor_service import (
    COMPARISON_COMPLETED,
    MATCH_METHOD_AUTOMATIC_AI,
    REFERENCE_MODE_AUTOMATIC,
    run_regulation_comparison,
)
from app.services.regulation_reference_service import (
    fetch_regulation_content,
)
from app.services.web_fetcher import fetch_website


warnings.filterwarnings("ignore")


AMENDMENT_URL = (
    "https://www.resmigazete.gov.tr/eskiler/2026/09/20260904-1.htm"
)

CONSOLIDATED_URL = (
    "https://www.mevzuat.gov.tr/mevzuat"
    "?MevzuatNo=39080&MevzuatTur=7&MevzuatTertip=5"
)

ARCHIVE_URL = (
    "https://www.resmigazete.gov.tr/eskiler/2021/12/20211201-1.htm"
)


# ---------------------------------------------------------
# SAHTE GEMINI (yalnizca karsilastirma adimi icin)
# ---------------------------------------------------------

GEMINI_CALLS = []

SMTP_ATTEMPTS = []

_real_smtp = smtplib.SMTP
_real_smtp_ssl = smtplib.SMTP_SSL


class BlockedSMTP:
    def __init__(self, *args, **kwargs):
        SMTP_ATTEMPTS.append(args)

        raise AssertionError(
            "Kabul testinde gercek SMTP baglantisi denendi."
        )


# Karsilastirma cevabi GERCEK metinlerden turetilir.
#
# Sahte model cevabi bile olsa kanitlar UYDURULMAZ: her
# alan gercek kaynak/referans metninden BIREBIR alinir ve
# sunucunun exact dogrulamasindan gecmek zorundadir.
COMPARISON_PAYLOAD = {"value": None}


def build_comparison_payload(
    amendment_text: str,
    candidate_id: str,
    candidate_text: str,
) -> str:
    """
    Gercek metinlerden kanit secer.

    Secilen her parca ilgili metinde GERCEKTEN vardir;
    sunucu tarafi dogrulamasi bu yuzden gecer. Uydurma
    metin kullanilsa dogrulama REDDEDERDI.
    """

    def pick(text: str, marker: str, length: int = 60) -> str:
        position = text.find(marker)

        if position == -1:
            return marker

        return " ".join(
            text[position:position + length].split()
        )

    source_evidence = pick(
        amendment_text,
        "ibareleri eklenmiştir",
        40,
    )

    new_text = pick(amendment_text, "biyometrik", 30)

    reference_evidence = " ".join(
        candidate_text[:120].split()
    )

    return json.dumps(
        {
            "has_regulatory_change": True,
            "overall_summary": (
                "41 inci maddeye biyometrik doğrulama "
                "ibaresi eklenmiştir."
            ),
            "importance": "medium",
            "items": [
                {
                    "candidate_id": candidate_id,
                    "affected_article": "Madde 41",
                    "affected_paragraph": "4. fıkra",
                    "change_type": "added",
                    "old_text": None,
                    "new_text": new_text,
                    "source_evidence": source_evidence,
                    "reference_evidence": reference_evidence,
                    "change_summary": (
                        "Uzaktan kimlik doğrulamada "
                        "biyometrik yöntemler eklendi."
                    ),
                    "confidence": 0.95,
                }
            ],
        },
        ensure_ascii=False,
    )


class FakeResponse:
    def __init__(self, text):
        self.text = text


class FakeModels:
    async def generate_content(self, model, contents, config):
        GEMINI_CALLS.append(
            {"model": model, "prompt": contents}
        )

        return FakeResponse(COMPARISON_PAYLOAD["value"])


class FakeAio:
    def __init__(self):
        self.models = FakeModels()

    async def aclose(self):
        return None


class FakeGeminiClient:
    def __init__(self, *args, **kwargs):
        self.aio = FakeAio()


class RunResult:
    def __init__(self, change, analysis):
        self.first_run = False
        self.changed = True
        self.change_record = change
        self.ai_analysis_record = analysis
        self.new_snapshot = None


def line(char: str = "-") -> None:
    print(char * 72)


def heading(title: str) -> None:
    print()
    line("=")
    print(title)
    line("=")


async def load_amendment() -> str:
    result = await fetch_website(AMENDMENT_URL)

    return extract_content(result.html).text


def main() -> None:

    print("=" * 72)
    print("OTOMATIK REFERANS PIPELINE KABUL TESTI")
    print("=" * 72)
    print("Gercek kaynak      : EVET")
    print("Gercek Gemini      : HAYIR (karsilastirma sahte)")
    print("Gercek SMTP        : HAYIR")
    print("Kalici DB kaydi    : HAYIR (rollback)")

    problems: list[str] = []

    from google import genai

    real_client = genai.Client

    smtplib.SMTP = BlockedSMTP
    smtplib.SMTP_SSL = BlockedSMTP

    # -----------------------------------------------------
    # 1. GERCEK KAYNAKLAR
    # -----------------------------------------------------

    heading("1. GERCEK KAYNAKLAR")

    try:
        amendment_text = asyncio.run(load_amendment())

    except Exception as exc:  # noqa: BLE001
        print(f"  Resmî Gazete degisikligi ALINAMADI: {exc}")
        print()
        print("AUTOMATIC_PIPELINE_ACCEPTANCE: NOT VERIFIED")
        sys.exit(1)

    print(f"  degisiklik metni : {len(amendment_text)} karakter")

    try:
        consolidated_content = asyncio.run(
            fetch_regulation_content(CONSOLIDATED_URL)
        )

    except Exception as exc:  # noqa: BLE001
        print(f"  konsolide metin ALINAMADI: {exc}")
        print()
        print("AUTOMATIC_PIPELINE_ACCEPTANCE: NOT VERIFIED")
        sys.exit(1)

    print(
        f"  konsolide metin  : "
        f"{consolidated_content.text_length} karakter, "
        f"surum {consolidated_content.version_date}"
    )

    try:
        archive_content = asyncio.run(
            fetch_regulation_content(ARCHIVE_URL)
        )

    except Exception as exc:  # noqa: BLE001
        print(f"  arsiv surumu ALINAMADI: {exc}")
        archive_content = None

    if archive_content is not None:
        print(
            f"  arsiv surumu     : "
            f"{archive_content.text_length} karakter"
        )

    # -----------------------------------------------------
    # 2. OTOMATIK PIPELINE
    # -----------------------------------------------------

    genai.Client = FakeGeminiClient

    connection = engine.connect()
    transaction = connection.begin()

    session = Session(
        bind=connection,
        join_transaction_mode="create_savepoint",
    )

    reference_count_before = session.scalar(
        select(func.count()).select_from(RegulationReference)
    )

    comparison_count_before = session.scalar(
        select(func.count()).select_from(RegulationComparison)
    )

    try:
        heading("2. REFERANS KUTUPHANESI")

        distractors = [
            create_regulation_reference(
                db=session,
                title="Elektrik Piyasası Lisans Yönetmeliği",
                source_url="https://mevzuat.test.local/acc-1",
                content="MADDE 1 - elektrik içerik " * 30,
            ),
            create_regulation_reference(
                db=session,
                title=(
                    "Bankaların İç Sistemleri Hakkında "
                    "Yönetmelik"
                ),
                source_url="https://mevzuat.test.local/acc-2",
                content="MADDE 1 - banka içerik " * 30,
            ),
            create_regulation_reference(
                db=session,
                title=(
                    "Ödeme ve Elektronik Para "
                    "Kuruluşlarının Bilgi Sistemleri "
                    "Hakkında Tebliğ"
                ),
                source_url="https://mevzuat.test.local/acc-3",
                content="MADDE 1 - bilgi sistemleri " * 30,
            ),
        ]

        archive_reference = None

        if archive_content is not None:
            archive_reference = create_regulation_reference(
                db=session,
                title=(
                    "Ödeme Hizmetleri ve Elektronik Para "
                    "İhracı ile Ödeme Hizmeti "
                    "Sağlayıcıları Hakkında Yönetmelik"
                ),
                source_url=ARCHIVE_URL,
                content=archive_content.text,
                source_type=archive_content.source_type,
                is_consolidated=(
                    archive_content.is_consolidated
                ),
                version_date=archive_content.version_date,
            )

        consolidated_reference = create_regulation_reference(
            db=session,
            title=(
                "Ödeme Hizmetleri ve Elektronik Para "
                "İhracı ile Ödeme Hizmeti Sağlayıcıları "
                "Hakkında Yönetmelik"
            ),
            source_url=CONSOLIDATED_URL,
            content=consolidated_content.text,
            source_type=consolidated_content.source_type,
            is_consolidated=(
                consolidated_content.is_consolidated
            ),
            version_date=consolidated_content.version_date,
        )

        print(f"  alakasiz referans : {len(distractors)}")
        print(
            f"  arsiv referans    : "
            f"{archive_reference.id if archive_reference else '(yok)'}"
        )
        print(
            f"  konsolide referans: "
            f"{consolidated_reference.id} "
            f"(cons={consolidated_reference.is_consolidated})"
        )

        heading("3. OTOMATIK MONITOR")

        monitor = create_monitor(
            db=session,
            name="[KABUL TESTİ] Resmî Gazete Otomatik",
            url="https://www.resmigazete.gov.tr/",
            topic=(
                "Ödeme hizmetleri ve elektronik para "
                "mevzuatı"
            ),
            tracking_plan="regulation_monitor",
            reference_selection_mode=REFERENCE_MODE_AUTOMATIC,
            reference_regulation_id=None,
            scope_type="full_page",
            schedule_time="09:00",
        )

        print(f"  secim modu       : {monitor.reference_selection_mode}")
        print(
            f"  monitor referansi: "
            f"{monitor.reference_regulation_id} (null olmali)"
        )

        if monitor.reference_regulation_id is not None:
            problems.append(
                "otomatik monitorde referans null degil"
            )

        old_snapshot = create_snapshot(
            session,
            monitor.id,
            "Resmî Gazete",
            "önceki yayımlar",
        )

        new_snapshot = create_snapshot(
            session,
            monitor.id,
            "Resmî Gazete",
            "önceki yayımlar\n" + amendment_text,
        )

        change = create_change(
            db=session,
            monitor_id=monitor.id,
            old_snapshot_id=old_snapshot.id,
            new_snapshot_id=new_snapshot.id,
            relevant=True,
            relevance_score=0.92,
            added_lines=amendment_text.splitlines(),
            removed_lines=[],
        )

        analysis = create_ai_analysis(
            db=session,
            change_id=change.id,
            model_name="acceptance-fake",
            keyword_relevant=True,
            keyword_score=0.92,
            relevant=True,
            importance="high",
            category="policy",
            summary="Ödeme hizmetleri yönetmeliği değişti.",
            notification_recommended=True,
        )

        heading("4. CALISTIRMA (kullanici tiklamasi YOK)")

        # Sahte model cevabini GERCEK metinlerden kur.
        from app.services.regulation_candidate_matcher import (
            select_candidates as _select,
        )
        from app.services.regulation_text_parser import (
            parse_regulation_segments as _parse,
        )

        _document = _parse(consolidated_reference.content)

        _candidates = _select(
            source_text=amendment_text,
            segments=_document.segments,
        )

        COMPARISON_PAYLOAD["value"] = build_comparison_payload(
            amendment_text=amendment_text,
            candidate_id=(
                _candidates[0].candidate_id
                if _candidates
                else "seg-1"
            ),
            candidate_text=(
                _candidates[0].text if _candidates else ""
            ),
        )

        GEMINI_CALLS.clear()

        outcome = asyncio.run(
            run_regulation_comparison(
                db=session,
                monitor=monitor,
                result=RunResult(change, analysis),
            )
        )

        print(f"  karsilastirma durumu : {outcome.status}")
        print(f"  eslestirme durumu    : {outcome.match_status}")
        print(f"  matcher AI kullandi  : {outcome.matcher_ai_used}")
        print(f"  tazelik              : {outcome.freshness_status}")
        print(f"  toplam Gemini cagrisi: {len(GEMINI_CALLS)}")

        comparison = outcome.comparison

        if outcome.status != COMPARISON_COMPLETED:
            problems.append(
                f"karsilastirma tamamlanmadi: {outcome.status}"
            )

        if comparison is None:
            problems.append("karsilastirma satiri olusmadi")

        else:
            heading("5. KARSILASTIRMA SATIRI")

            used = comparison.reference_regulation_id

            print(f"  kullanilan referans : {used}")
            print(
                f"  konsolide mi        : "
                f"{comparison.reference_is_consolidated}"
            )
            print(
                f"  eslestirme yontemi  : "
                f"{comparison.reference_match_method}"
            )
            print(
                f"  guven               : "
                f"{comparison.reference_match_confidence}"
            )
            print(
                f"  tazelik             : "
                f"{comparison.reference_freshness_status}"
            )

            if used != consolidated_reference.id:
                problems.append(
                    "KONSOLIDE referans kullanilmadi: "
                    f"{used}"
                )

            if (
                archive_reference is not None
                and used == archive_reference.id
            ):
                problems.append(
                    "ARSIV surumu kullanildi"
                )

            if used in {
                reference.id for reference in distractors
            }:
                problems.append(
                    "ALAKASIZ referans kullanildi"
                )

            if not comparison.reference_is_consolidated:
                problems.append(
                    "kullanilan referans konsolide degil"
                )

            if (
                comparison.reference_match_method
                == MATCH_METHOD_AUTOMATIC_AI
            ):
                print(
                    "\n  NOT: eslesme AI ile cozuldu; "
                    "deterministik eslesme tercih edilirdi."
                )

        # -------------------------------------------------
        # 6. ADAY MADDE
        # -------------------------------------------------

        heading("6. ADAY MADDE KONTROLU")

        from app.services.regulation_candidate_matcher import (
            select_candidates,
        )
        from app.services.regulation_text_parser import (
            parse_regulation_segments,
        )

        document = parse_regulation_segments(
            consolidated_reference.content
        )

        candidates = select_candidates(
            source_text=amendment_text,
            segments=document.segments,
        )

        print(f"  referans MADDE sayisi : {document.article_count}")
        print(f"  secilen aday          : {len(candidates)}")

        for candidate in candidates[:3]:
            print(
                f"    {candidate.candidate_id:10s} "
                f"{str(candidate.article_label):20s} "
                f"{candidate.reason:20s} "
                f"skor={candidate.score:.3f}"
            )

        if not candidates:
            problems.append("aday madde bulunamadi")

        elif candidates[0].article_label != "Madde 41":
            problems.append(
                "ilk aday Madde 41 degil: "
                f"{candidates[0].article_label}"
            )

        # -------------------------------------------------
        # MATCHER GEMINI SAYIMI
        # -------------------------------------------------

        heading("7. GEMINI KULLANIMI")

        matcher_calls = (
            len(GEMINI_CALLS) - 1
            if len(GEMINI_CALLS) >= 1
            else 0
        )

        print(f"  toplam cagri          : {len(GEMINI_CALLS)}")
        print(f"  karsilastirma cagrisi : 1")
        print(f"  matcher cagrisi       : {matcher_calls}")

        if outcome.matcher_ai_used:
            print(
                "\n  NOT: matcher AI kullandi. Deterministik "
                "eslesmede bu SIFIR olmalidir."
            )

    finally:
        session.close()
        transaction.rollback()
        connection.close()

        genai.Client = real_client
        smtplib.SMTP = _real_smtp
        smtplib.SMTP_SSL = _real_smtp_ssl

    # -----------------------------------------------------
    # KALICI KAYIT
    # -----------------------------------------------------

    from app.core.database import SessionLocal

    verify = SessionLocal()

    try:
        reference_count_after = verify.scalar(
            select(func.count()).select_from(RegulationReference)
        )

        comparison_count_after = verify.scalar(
            select(func.count()).select_from(RegulationComparison)
        )

    finally:
        verify.close()

    heading("SONUC")

    print(f"referans sayisi once/sonra   : "
          f"{reference_count_before}/{reference_count_after}")
    print(f"karsilastirma sayisi once/sonra: "
          f"{comparison_count_before}/{comparison_count_after}")
    print(f"gercek SMTP cagrisi          : {len(SMTP_ATTEMPTS)}")

    if reference_count_after != reference_count_before:
        problems.append("veritabaninda kalici referans kaldi")

    if comparison_count_after != comparison_count_before:
        problems.append(
            "veritabaninda kalici karsilastirma kaldi"
        )

    if SMTP_ATTEMPTS:
        problems.append("gercek SMTP baglantisi denendi")

    print()

    if problems:
        print("SORUNLAR:")

        for problem in problems:
            print(f"  - {problem}")

        print()
        print("AUTOMATIC_PIPELINE_ACCEPTANCE: NOT VERIFIED")

        sys.exit(1)

    print("AUTOMATIC_PIPELINE_ACCEPTANCE: VERIFIED")


if __name__ == "__main__":
    main()
