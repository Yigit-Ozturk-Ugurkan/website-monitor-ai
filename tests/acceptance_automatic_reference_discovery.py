"""
GERCEK KABUL TESTI - OTOMATIK REFERANS KESFI

Bu test SAHTE VERI KULLANMAZ:

  - mevzuat.gov.tr arama endpoint'ine GERCEK istek atar
  - konsolide mevzuat metnini GERCEKTEN indirir
  - gercek PostgreSQL uzerinde calisir

SENARYO

  Resmî Gazete 4 Eylül 2026 degisikligi:

    "ÖDEME HİZMETLERİ VE ELEKTRONİK PARA İHRACI İLE ÖDEME
     HİZMETİ SAĞLAYICILARI HAKKINDA YÖNETMELİKTE DEĞİŞİKLİK
     YAPILMASINA DAİR YÖNETMELİK"

  BASLANGIC: bu mevzuat icin veritabaninda HIC referans
  yok.

  BEKLENEN:

    1. yerel eslestirme -> referans YOK
    2. resmi kesif calisir
    3. mevzuat.gov.tr'de dogru mevzuat bulunur
    4. MevzuatNo / Tur / Tertip dogrulanir
    5. konsolide metin GERCEKTEN indirilir
    6. RegulationReference olusturulur
    7. is_consolidated = true
    8. karsilastirma pipeline'i devam eder
    9. Madde 41 adayi bulunur
   10. kullanici tiklamasi YOK
   11. ikinci ayni kesif duplicate URETMEZ

MALIYET

  Gercek Gemini KESIF cagrisi : 0
      Birebir resmi ad eslesmesi DETERMINISTIKTIR; kesif
      icin model kullanilmaz.

  Gercek Gemini KARSILASTIRMA cagrisi : 0
      Karsilastirma cevabi sahte verilir. Ancak cevap
      SABIT DEGILDIR: gercek prompt'taki aday listesinden
      41 inci maddenin kimligi ve GERCEK metni okunur ve
      kanit olarak geri verilir. Boylece sunucu tarafi
      exact evidence dogrulamasi GERCEK konsolide metin
      uzerinde calisir.

  Gercek SMTP : 0

VERITABANI

  Her sey TEK bir transaction icinde yapilir ve sonunda
  GERI ALINIR. Kalici kayit birakilmaz.

Calistirma:
    python -m tests.acceptance_automatic_reference_discovery
"""

import asyncio
import json
import warnings

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database import engine
from app.models.regulation_reference import RegulationReference
from app.repositories.ai_analysis_repository import (
    create_ai_analysis,
)
from app.repositories.change_repository import create_change
from app.repositories.monitor_repository import create_monitor
from app.repositories.snapshot_repository import create_snapshot
from app.services.regulation_discovery_service import (
    DISCOVERY_DISCOVERED,
    DISCOVERY_FOUND_EXISTING,
)
from app.services.regulation_monitor_service import (
    COMPARISON_COMPLETED,
    REFERENCE_MODE_AUTOMATIC,
    run_regulation_comparison,
)
from app.services.regulation_reference_matcher import (
    score_name,
)
from app.services.regulation_source_registry import (
    SOURCE_TYPE_MEVZUAT_GOV_TR,
)
from app.services.regulation_title_parser import (
    extract_base_regulation_names,
)


warnings.filterwarnings("ignore")


NEWLINE = chr(10)

# regulation_comparator.build_comparison_prompt aday
# bloklarini bu ayirici ile birlestiriyor.
CANDIDATE_SEPARATOR = (
    NEWLINE + NEWLINE + "---" + NEWLINE + NEWLINE
)


PASSED = []
FAILED = []


def check(label: str, condition: bool, detail: str = "") -> None:

    status = "PASS" if condition else "FAIL"

    print(f"[{status}] {label}" + (f" -> {detail}" if detail else ""))

    if condition:
        PASSED.append(label)
    else:
        FAILED.append(f"{label} -> {detail}")


def run(coroutine):
    return asyncio.run(coroutine)


# ---------------------------------------------------------
# GERCEK RESMI GAZETE DEGISIKLIK METNI
# ---------------------------------------------------------

AMENDMENT = """4 Eylül 2026 CUMA
Resmî Gazete
Sayı : 33360
YÖNETMELİK
Türkiye Cumhuriyet Merkez Bankasından:
ÖDEME HİZMETLERİ VE ELEKTRONİK PARA İHRACI İLE ÖDEME HİZMETİ
SAĞLAYICILARI HAKKINDA YÖNETMELİKTE DEĞİŞİKLİK YAPILMASINA DAİR YÖNETMELİK
MADDE 1- 1/12/2021 tarihli ve 31676 sayılı Resmî Gazete'de yayımlanan Ödeme
Hizmetleri ve Elektronik Para İhracı ile Ödeme Hizmeti Sağlayıcıları Hakkında
Yönetmeliğin 41 inci maddesinin birinci fıkrasında yer alan "yazılı" ibaresi
"biyometrik" şeklinde değiştirilmiştir.
MADDE 2- Bu Yönetmelik yayımı tarihinde yürürlüğe girer.
MADDE 3- Bu Yönetmelik hükümlerini Türkiye Cumhuriyet Merkez Bankası Başkanı
yürütür."""


# Kaynak degisiklik metninde BIREBIR bulunan ifadeler.
# Sunucu dogrulamasi bunlari kaynak metinde arar.
SOURCE_EVIDENCE = '"biyometrik" şeklinde değiştirilmiştir'

NEW_TEXT = "biyometrik"


EXPECTED_MEVZUAT_NO = "39080"
EXPECTED_MEVZUAT_TUR = "7"
EXPECTED_MEVZUAT_TERTIP = "5"

EXPECTED_CANONICAL_URL = (
    "https://www.mevzuat.gov.tr/mevzuat"
    f"?MevzuatNo={EXPECTED_MEVZUAT_NO}"
    f"&MevzuatTur={EXPECTED_MEVZUAT_TUR}"
    f"&MevzuatTertip={EXPECTED_MEVZUAT_TERTIP}"
)


# ---------------------------------------------------------
# KARSILASTIRMA CEVABI GERCEK PROMPT'TAN URETILIR
#
# Sabit bir cevap donmek, kanit dogrulamasini GERCEK
# konsolide metin uzerinde sinamaz. Burada aday
# listesinden 41 inci maddenin candidate_id'si ve gercek
# metninden bir satir okunur ve kanit olarak geri verilir.
#
# Aday bulunamazsa BOS item listesi doner; uydurma kanit
# URETILMEZ ve test bunu FAIL olarak gorur.
# ---------------------------------------------------------

def parse_candidate_blocks(prompt: str) -> list[dict]:
    """
    Karsilastirma promptundaki aday bloklarini ayristirir.

    Blok bicimi:

        candidate_id: <id>
        madde: <etiket>
        bölüm: <baslik>
        metin:
        <metin>
    """

    blocks = []

    for raw in prompt.split(CANDIDATE_SEPARATOR):

        if "candidate_id:" not in raw:
            continue

        block = {
            "candidate_id": "",
            "madde": "",
            "metin": "",
        }

        text_lines = []
        in_text = False

        for line in raw.splitlines():

            if line.startswith("candidate_id:"):
                block["candidate_id"] = line.split(":", 1)[
                    1
                ].strip()

            elif line.startswith("madde:"):
                block["madde"] = line.split(":", 1)[1].strip()

            elif line.strip() == "metin:":
                in_text = True

            elif in_text:
                text_lines.append(line)

        block["metin"] = NEWLINE.join(text_lines).strip()

        if block["candidate_id"]:
            blocks.append(block)

    return blocks


def find_article_41(prompt: str) -> dict | None:
    """
    Aday listesinde 41 inci maddeyi bulur.
    """

    for block in parse_candidate_blocks(prompt):

        label = (
            block["madde"]
            .upper()
            .replace("MADDE", "")
            .replace("-", " ")
            .strip()
        )

        if label == "41":
            return block

    return None


def build_comparison_payload(prompt: str) -> str:

    block = find_article_41(prompt)

    items = []

    if block is not None:

        # Adayin GERCEK metninden birebir bir satir.
        candidate_lines = [
            line.strip()
            for line in block["metin"].splitlines()
            if len(line.strip()) > 30
        ]

        reference_evidence = (
            candidate_lines[0]
            if candidate_lines
            else block["metin"][:120]
        )

        items.append(
            {
                "candidate_id": block["candidate_id"],
                "affected_article": block["madde"],
                "affected_paragraph": None,
                "change_type": "amended",
                "old_text": None,
                "new_text": NEW_TEXT,
                "source_evidence": SOURCE_EVIDENCE,
                "reference_evidence": reference_evidence,
                "change_summary": (
                    "Kimlik doğrulama yöntemi değişti."
                ),
                "confidence": 0.95,
            }
        )

    return json.dumps(
        {
            "has_regulatory_change": True,
            "overall_summary": (
                "41 inci maddede kimlik doğrulama yöntemi "
                "değiştirildi."
            ),
            "importance": "high",
            "items": items,
        },
        ensure_ascii=False,
    )


# ---------------------------------------------------------
# SAHTE GEMINI
#
# YALNIZCA karsilastirma cevabi icin. Kesif tarafinda bu
# istemcinin HIC cagrilmamasi BEKLENIR ve dogrulanir.
# ---------------------------------------------------------

GEMINI_CALLS = []


class FakeResponse:
    def __init__(self, text):
        self.text = text


class FakeModels:
    async def generate_content(self, model, contents, config):
        GEMINI_CALLS.append(
            {"model": model, "prompt": contents}
        )

        return FakeResponse(
            build_comparison_payload(contents)
        )


class FakeAio:
    def __init__(self):
        self.models = FakeModels()

    async def aclose(self):
        return None


class FakeGeminiClient:
    def __init__(self, *args, **kwargs):
        self.aio = FakeAio()


class FakeRunResult:
    def __init__(self, change, analysis):
        self.first_run = False
        self.changed = True
        self.change_record = change
        self.ai_analysis_record = analysis
        self.new_snapshot = None


def build_change(session, monitor):

    old_snapshot = create_snapshot(
        db=session,
        monitor_id=monitor.id,
        content="Önceki Resmî Gazete içeriği.",
        title="Resmî Gazete",
    )

    new_snapshot = create_snapshot(
        db=session,
        monitor_id=monitor.id,
        content=AMENDMENT,
        title="Resmî Gazete",
    )

    change = create_change(
        db=session,
        monitor_id=monitor.id,
        old_snapshot_id=old_snapshot.id,
        new_snapshot_id=new_snapshot.id,
        relevant=True,
        relevance_score=0.95,
        added_lines=AMENDMENT.splitlines(),
        removed_lines=[],
    )

    analysis = create_ai_analysis(
        db=session,
        change_id=change.id,
        model_name="acceptance-fake",
        keyword_relevant=True,
        keyword_score=0.95,
        relevant=True,
        importance="high",
        category="mevzuat",
        summary=(
            "Ödeme hizmetleri yönetmeliğinin 41 inci "
            "maddesi değiştirildi."
        ),
        notification_recommended=True,
    )

    return change, analysis


def build_acceptance_monitor(session, name):

    return create_monitor(
        db=session,
        name=name,
        url="https://www.resmigazete.gov.tr/",
        topic="Ödeme hizmetleri ve elektronik para mevzuatı",
        scope_type="full_page",
        schedule_type="daily",
        schedule_time="09:00",
        is_unlimited=True,
        notify_in_app=True,
        notify_email=False,
        tracking_plan="regulation_monitor",
        reference_selection_mode=REFERENCE_MODE_AUTOMATIC,
        reference_regulation_id=None,
    )


def main() -> None:

    print("=" * 70)
    print("GERCEK KABUL TESTI - OTOMATIK REFERANS KESFI")
    print("=" * 70)
    print()
    print("mevzuat.gov.tr'ye GERCEK istek yapilacak.")
    print("Veritabani degisiklikleri GERI ALINACAK.")
    print()

    from google import genai

    real_client = genai.Client
    genai.Client = FakeGeminiClient

    connection = engine.connect()
    transaction = connection.begin()

    session = Session(
        bind=connection,
        join_transaction_mode="create_savepoint",
    )

    references_before = session.scalar(
        select(func.count()).select_from(RegulationReference)
    )

    try:
        run_acceptance(session)

    finally:
        session.close()

        transaction.rollback()
        connection.close()

        genai.Client = real_client

    with Session(bind=engine) as verify:
        references_after = verify.scalar(
            select(func.count()).select_from(
                RegulationReference
            )
        )

    check(
        "DB GARBAGE = 0 (kalici referans kaydi yok)",
        references_after == references_before,
        f"{references_before} -> {references_after}",
    )

    print()
    print("=" * 70)
    print(f"BASARILI : {len(PASSED)}")
    print(f"BASARISIZ: {len(FAILED)}")

    if FAILED:
        print("-" * 70)

        for item in FAILED:
            print(f"  {item}")

        print("=" * 70)
        print("OFFICIAL_SEARCH_DISCOVERY: NOT VERIFIED")
        raise SystemExit(1)

    print("TUM KABUL KRITERLERI KARSILANDI")
    print("=" * 70)
    print("OFFICIAL_SEARCH_DISCOVERY: VERIFIED")


def run_acceptance(session):

    # -----------------------------------------------------
    # 0. BASE MEVZUAT ADI
    # -----------------------------------------------------

    base_name = extract_base_regulation_names(
        source_text=AMENDMENT
    ).primary

    check(
        "0. degisiklik metninden base mevzuat adi cikarildi",
        base_name is not None
        and "ödeme hizmetleri" in (base_name.normalized or ""),
        base_name.name if base_name else "",
    )

    check(
        "0. base ad DEGISIKLIK basligi degil",
        base_name is not None
        and "değişiklik yapılmasına"
        not in base_name.normalized,
        base_name.normalized if base_name else "",
    )

    check(
        "0. yayim bilgisi govdeden cikarildi",
        base_name is not None
        and base_name.official_gazette_issue == "31676",
        str(
            base_name.official_gazette_issue
            if base_name
            else None
        ),
    )

    # -----------------------------------------------------
    # BASLANGIC: BU MEVZUAT ICIN HIC REFERANS YOK
    #
    # Gercek veritabaninda uyan bir kayit varsa transaction
    # icinde gecici olarak gizlenir; rollback ile geri gelir.
    # -----------------------------------------------------

    hidden = 0

    for reference in session.scalars(
        select(RegulationReference)
    ).all():

        score, _, _ = score_name(
            base_name=base_name,
            title=reference.title,
        )

        matches_url = EXPECTED_MEVZUAT_NO in (
            reference.source_url or ""
        )

        if score >= 0.60 or matches_url:
            reference.title = (
                f"Kabul Testi Gizli Kayıt {reference.id} "
                "Yönetmelik"
            )
            reference.source_url = (
                "https://kabul-testi.local/gizli/"
                f"{reference.id}"
            )
            hidden += 1

    session.commit()

    print(f"    (gecici olarak gizlenen kayit: {hidden})")

    existing_target = session.scalar(
        select(func.count())
        .select_from(RegulationReference)
        .where(
            RegulationReference.source_url
            == EXPECTED_CANONICAL_URL
        )
    )

    check(
        "1. BASLANGIC: bu mevzuat icin DB'de referans YOK",
        existing_target == 0,
        f"eslesen kayit={existing_target}",
    )

    # -----------------------------------------------------
    # CALISTIRMA
    # -----------------------------------------------------

    monitor = build_acceptance_monitor(
        session,
        "Kabul Testi - Otomatik Kesif",
    )

    check(
        "10. kullanici tiklamasi YOK "
        "(monitore bagli referans yok)",
        monitor.reference_regulation_id is None,
    )

    change, analysis = build_change(session, monitor)

    started = datetime.utcnow()

    result = run(
        run_regulation_comparison(
            db=session,
            monitor=monitor,
            result=FakeRunResult(change, analysis),
        )
    )

    elapsed = (datetime.utcnow() - started).total_seconds()

    print(f"    (calistirma suresi: {elapsed:.1f} sn)")

    # -----------------------------------------------------
    # 2-3. RESMI KESIF CALISTI VE DOGRU MEVZUATI BULDU
    # -----------------------------------------------------

    check(
        "2. resmi kesif CALISTI",
        result.discovery_status == DISCOVERY_DISCOVERED,
        f"{result.discovery_status} / {result.discovery_detail}",
    )

    discovered = session.scalar(
        select(RegulationReference).where(
            RegulationReference.source_url
            == EXPECTED_CANONICAL_URL
        )
    )

    check(
        "3. mevzuat.gov.tr'de DOGRU mevzuat bulundu",
        discovered is not None,
        result.discovery_detail,
    )

    if discovered is None:
        return

    # -----------------------------------------------------
    # 4. KIMLIK DOGRULANDI
    # -----------------------------------------------------

    check(
        "4. MevzuatNo / MevzuatTur / MevzuatTertip dogrulandi",
        discovered.source_url == EXPECTED_CANONICAL_URL,
        discovered.source_url,
    )

    check(
        "4. kanonik adres SADECE rakam parametre tasiyor",
        f"MevzuatNo={EXPECTED_MEVZUAT_NO}"
        in discovered.source_url
        and f"MevzuatTur={EXPECTED_MEVZUAT_TUR}"
        in discovered.source_url
        and f"MevzuatTertip={EXPECTED_MEVZUAT_TERTIP}"
        in discovered.source_url,
    )

    # -----------------------------------------------------
    # 5-7. KONSOLIDE METIN GERCEKTEN INDIRILDI
    # -----------------------------------------------------

    content = discovered.content or ""

    check(
        "5. konsolide metin GERCEKTEN indirildi",
        len(content) > 10_000,
        f"uzunluk={len(content)}",
    )

    check(
        "5. indirilen metin gercek MADDE'ler iceriyor",
        "MADDE" in content.upper(),
    )

    check(
        "5. indirilen metin 41 inci maddeyi iceriyor",
        "MADDE 41" in content.upper(),
    )

    check(
        "6. RegulationReference OLUSTURULDU",
        discovered.id is not None
        and result.discovered_reference_id == discovered.id,
        f"reference_id={discovered.id}",
    )

    check(
        "6. RESMI baslik saklandi "
        "(kullanicidan cikarilan base ad DEGIL)",
        "ÖDEME" in (discovered.title or "").upper()
        and "YÖNETMEL" in (discovered.title or "").upper(),
        discovered.title,
    )

    check(
        "7. is_consolidated = true",
        discovered.is_consolidated is True,
    )

    check(
        "7. source_type = mevzuat_gov_tr",
        discovered.source_type == SOURCE_TYPE_MEVZUAT_GOV_TR,
        discovered.source_type,
    )

    check(
        "7. content_hash ve last_verified_at yazildi",
        bool(discovered.content_hash)
        and discovered.last_verified_at is not None,
    )

    check(
        "7. version_date konsolide metinden cikarildi",
        discovered.version_date is not None,
        str(discovered.version_date),
    )

    # -----------------------------------------------------
    # 8-9. KARSILASTIRMA PIPELINE'I DEVAM ETTI
    # -----------------------------------------------------

    prompts = [call["prompt"] for call in GEMINI_CALLS]

    check(
        "9. karsilastirma cagrisi yapildi",
        len(prompts) == 1,
        f"cagri={len(prompts)}",
    )

    prompt = prompts[0] if prompts else ""

    article_block = find_article_41(prompt)

    check(
        "9. Madde 41 adayi GERCEK konsolide metinden bulundu",
        article_block is not None,
        (
            f"aday={article_block['candidate_id']}"
            if article_block
            else "aday listesinde 41 inci madde yok"
        ),
    )

    check(
        "9. referans yonetmeligin TAMAMI modele gonderilmedi",
        len(prompt) < len(content),
        f"prompt={len(prompt)} icerik={len(content)}",
    )

    check(
        "8. karsilastirma TAMAMLANDI",
        result.status == COMPARISON_COMPLETED,
        result.status,
    )

    check(
        "8. dogrulanmis madde uretildi",
        result.item_count == 1,
        f"item={result.item_count}",
    )

    item = (
        result.comparison.items[0]
        if result.comparison and result.comparison.items
        else None
    )

    check(
        "8. madde etiketi GERCEK konsolide metinden geldi",
        item is not None
        and "41" in (item.affected_article or ""),
        item.affected_article if item else "",
    )

    check(
        "8. referans kaniti GERCEK konsolide metinde birebir var",
        item is not None
        and bool(item.reference_evidence)
        and item.reference_evidence in content,
        (item.reference_evidence or "")[:60] if item else "",
    )

    check(
        "8. kaynak kaniti degisiklik metninde birebir var",
        item is not None
        and bool(item.source_evidence)
        and item.source_evidence in AMENDMENT,
        (item.source_evidence or "")[:60] if item else "",
    )

    check(
        "8. comparison satiri kesfedilen referansa bagli",
        result.comparison is not None
        and result.comparison.reference_regulation_id
        == discovered.id,
    )

    check(
        "8. comparison konsolide metne dayaniyor",
        result.comparison is not None
        and result.comparison.reference_is_consolidated
        is True,
    )

    check(
        "8. comparison dogru content hash'i saklar",
        result.comparison is not None
        and result.comparison.reference_content_hash
        == discovered.content_hash,
    )

    check(
        "8. comparison kesif durumunu saklar",
        result.comparison is not None
        and result.comparison.reference_discovery_status
        == DISCOVERY_DISCOVERED,
        str(
            result.comparison.reference_discovery_status
            if result.comparison
            else None
        ),
    )

    # -----------------------------------------------------
    # GERCEK GEMINI KESIF CAGRISI = 0
    # -----------------------------------------------------

    check(
        "KESIF Gemini cagrisi = 0 "
        "(birebir resmi ad deterministik cozuldu)",
        result.discovery_ai_used is False,
    )

    check(
        "kesif yontemi izlenebilir",
        result.comparison is not None
        and (
            result.comparison.reference_match_method or ""
        ).startswith("automatic_discovered"),
        str(
            result.comparison.reference_match_method
            if result.comparison
            else None
        ),
    )

    # -----------------------------------------------------
    # 11. IKINCI KESIF DUPLICATE URETMEZ
    #
    # Kesfedilen kayit artik RESMI adiyla kutuphanede ve
    # yerel eslestirmeyi kazanir. Kesif yolunun YENIDEN
    # calistigini gorebilmek icin adi gecici olarak
    # gizlenir; kanonik ADRES aynen kalir.
    # -----------------------------------------------------

    discovered.title = "Gizli Kayıt İkinci Tur Yönetmelik"
    session.commit()

    GEMINI_CALLS.clear()

    monitor_two = build_acceptance_monitor(
        session,
        "Kabul Testi - İkinci Kesif",
    )

    change_two, analysis_two = build_change(
        session, monitor_two
    )

    result_two = run(
        run_regulation_comparison(
            db=session,
            monitor=monitor_two,
            result=FakeRunResult(change_two, analysis_two),
        )
    )

    duplicate_count = session.scalar(
        select(func.count())
        .select_from(RegulationReference)
        .where(
            RegulationReference.source_url
            == EXPECTED_CANONICAL_URL
        )
    )

    check(
        "11. ikinci ayni kesif DUPLICATE URETMEZ",
        duplicate_count == 1,
        f"satir={duplicate_count}",
    )

    check(
        "11. ikinci kesif mevcut satiri kullanir",
        result_two.discovery_status
        == DISCOVERY_FOUND_EXISTING
        and result_two.discovered_reference_id
        == discovered.id,
        str(result_two.discovery_status),
    )

    check(
        "11. ikinci kesif icin de Gemini kesif cagrisi = 0",
        result_two.discovery_ai_used is False,
    )


if __name__ == "__main__":
    main()
