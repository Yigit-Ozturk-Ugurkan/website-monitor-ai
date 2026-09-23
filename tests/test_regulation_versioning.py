"""
REGULATION VERSIONING TESTLERI

Kapsam:
     1. mevcut referanslar geriye donuk uyumlu
     2. Resmî Gazete arsivi -> is_consolidated=false
     3. konsolide kaynak    -> is_consolidated=true
     4. surum bilgisi kaliciligi
     5. refresh surum bilgisini dogru koruyor
     6. konsolide referansla karsilastirma degismedi
     7. konsolide OLMAYAN karsilastirma uyarisi
     8. bildirim detayinda uyari
     9. arayuzde referans durumu
    10. mevcut karsilastirma regresyonu

AG ISTEGI YAPILMAZ. fetch_website fixture ile degistirilir.
Gemini CAGRILMAZ. SMTP CAGRILMAZ.

Gercek PostgreSQL uzerinde calisir ama KALICI KAYIT
BIRAKMAZ; dis transaction sonunda rollback edilir.

Calistirma:
    python -m tests.test_regulation_versioning
"""

import io
import warnings

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database import engine, get_db
from app.models.regulation_comparison import RegulationComparison
from app.models.regulation_reference import RegulationReference
from app.repositories.ai_analysis_repository import (
    create_ai_analysis,
)
from app.repositories.change_repository import create_change
from app.repositories.monitor_repository import create_monitor
from app.repositories.notification_repository import (
    create_notification,
)
from app.repositories.regulation_comparison_repository import (
    STATUS_COMPLETED,
    create_comparison,
)
from app.repositories.regulation_reference_repository import (
    create_regulation_reference,
    update_regulation_reference_content,
)
from app.repositories.snapshot_repository import create_snapshot
from app.services import regulation_reference_service
from app.services.regulation_source_registry import (
    SOURCE_TYPE_GENERIC,
    SOURCE_TYPE_MEVZUAT_GOV_TR,
    SOURCE_TYPE_RESMI_GAZETE,
    build_mevzuat_text_url,
    resolve_reference_source,
)
from app.services.regulation_text_parser import (
    extract_consolidation_info,
)


warnings.filterwarnings("ignore")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


PROJECT_ROOT = Path(__file__).resolve().parents[1]

APP_JSX = PROJECT_ROOT / "frontend" / "src" / "App.jsx"
INDEX_CSS = PROJECT_ROOT / "frontend" / "src" / "index.css"


PASSED = []
FAILED = []


def check(label: str, condition: bool, detail: str = "") -> None:

    status = "PASS" if condition else "FAIL"

    print(f"[{status}] {label}" + (f" -> {detail}" if detail else ""))

    if condition:
        PASSED.append(label)
    else:
        FAILED.append(f"{label} -> {detail}")


def read(path: Path) -> str:
    return io.open(path, encoding="utf-8").read()


def section(source: str, start_marker: str, end_marker: str) -> str:
    start = source.index(start_marker)
    end = source.index(end_marker, start)

    return source[start:end]


# ---------------------------------------------------------
# SAHTE KAYNAKLAR
#
# Gercek mevzuat.gov.tr / Resmî Gazete yapisini taklit eder.
# ---------------------------------------------------------

MEVZUAT_SHELL_URL = (
    "https://www.mevzuat.gov.tr/mevzuat"
    "?MevzuatNo=39080&MevzuatTur=7&MevzuatTertip=5"
)

MEVZUAT_TEXT_URL = build_mevzuat_text_url(
    tur="7",
    no="39080",
    tertip="5",
)

ARCHIVE_URL = (
    "https://www.resmigazete.gov.tr/eskiler/2021/12/20211201-1.htm"
)


# Konsolide metin: uzerine islenmis degisiklik isaretleri var.
CONSOLIDATED_BODY = """ÖDEME HİZMETLERİ HAKKINDA YÖNETMELİK
MADDE 1 –
(1) Bu Yönetmeliğin amacı ödeme kuruluşlarının
yetkilendirilmesine ilişkin usul ve esasları düzenlemektir.
MADDE 41 –
(Ek ibare:RG-4/9/2026-33360)
(1) Uzaktan iletişim aracı ile müşterinin kimliğinin
doğrulanması biyometrik yöntemlerle yapılabilir.
MADDE 42 –
(Değişik:RG-7/10/2023-32332)
(1) Ödeme kuruluşları asgari özkaynak yükümlülüğünü sağlar.
"""

# Ayni yonetmeligin YAYIMLANDIGI ANDAKI hali:
# degisiklik isareti YOK, 41 inci madde eski haliyle.
ARCHIVE_BODY = """ÖDEME HİZMETLERİ HAKKINDA YÖNETMELİK
MADDE 1 –
(1) Bu Yönetmeliğin amacı ödeme kuruluşlarının
yetkilendirilmesine ilişkin usul ve esasları düzenlemektir.
MADDE 41 –
(1) Uzaktan iletişim aracı ile müşterinin kimliğinin
doğrulanması yazılı şekilde yapılır.
MADDE 42 –
(1) Ödeme kuruluşları asgari özkaynak yükümlülüğünü sağlar.
"""

# Refresh sirasinda kaynak guncellenirse.
CONSOLIDATED_BODY_UPDATED = CONSOLIDATED_BODY + """MADDE 43 –
(Ek:RG-15/12/2026-33450)
(1) Bu madde sonradan eklenmiştir ve yeterince uzundur.
"""


def build_html(body: str) -> str:
    return (
        "<html><head><title>Mevzuat Bilgi Sistemi</title>"
        "</head><body>"
        + body.replace("\n", "<br>\n")
        + "</body></html>"
    )


@dataclass
class FakeFetchResult:
    url: str
    final_url: str
    status_code: int
    content_type: str
    html: str
    encoding: str = "utf-8"


FETCH_CALLS = []

# Testin ortasinda degistirilerek kaynak guncellemesi
# taklit edilir.
CURRENT_BODIES = {
    MEVZUAT_TEXT_URL: CONSOLIDATED_BODY,
    ARCHIVE_URL: ARCHIVE_BODY,
}


async def fake_fetch_website(url: str):

    FETCH_CALLS.append(url)

    if not url.startswith(("http://", "https://")):
        raise ValueError(
            "URL 'http://' veya 'https://' ile baslamalidir."
        )

    body = CURRENT_BODIES.get(url)

    if body is None:
        # Kabuk sayfa: metin YOK.
        # Gercek mevzuat.gov.tr davranisini taklit eder.
        body = "MEVZUAT BİLGİ SİSTEMİ menü içeriği " * 12

    return FakeFetchResult(
        url=url,
        final_url=url,
        status_code=200,
        content_type="text/html; charset=utf-8",
        html=build_html(body),
    )


def main() -> None:

    print("=" * 70)
    print("REGULATION VERSIONING TESTLERI")
    print("=" * 70)

    app_source = read(APP_JSX)
    css_source = read(INDEX_CSS)

    real_fetch = regulation_reference_service.fetch_website
    regulation_reference_service.fetch_website = fake_fetch_website

    connection = engine.connect()
    transaction = connection.begin()

    session = Session(
        bind=connection,
        join_transaction_mode="create_savepoint",
    )

    app.dependency_overrides[get_db] = lambda: session

    client = TestClient(app)

    reference_count_before = session.scalar(
        select(func.count()).select_from(RegulationReference)
    )

    try:

        # -------------------------------------------------
        # URL COZUMLEME
        # -------------------------------------------------

        print("\n--- kaynak cozumleme ---")

        shell = resolve_reference_source(MEVZUAT_SHELL_URL)

        check(
            "kabuk sayfa METIN adresine cevriliyor",
            shell.fetch_url == MEVZUAT_TEXT_URL
            and shell.rewritten is True,
            f"fetch_url={shell.fetch_url}",
        )

        check(
            "kullanicinin girdigi adres KORUNUYOR",
            shell.source_url == MEVZUAT_SHELL_URL,
        )

        check(
            "mevzuat.gov.tr konsolide kabul ediliyor",
            shell.source_type == SOURCE_TYPE_MEVZUAT_GOV_TR
            and shell.is_consolidated is True,
        )

        direct = resolve_reference_source(MEVZUAT_TEXT_URL)

        check(
            "metin adresi dogrudan verilirse tekrar "
            "cevrilmiyor",
            direct.fetch_url == MEVZUAT_TEXT_URL
            and direct.rewritten is False,
        )

        archive = resolve_reference_source(ARCHIVE_URL)

        check(
            "Resmî Gazete arsivi konsolide DEGIL",
            archive.source_type == SOURCE_TYPE_RESMI_GAZETE
            and archive.is_consolidated is False,
        )

        for url in (
            "https://example.com/",
            "https://www.mevzuat.gov.tr/hakkimizda",
            "https://www.mevzuat.gov.tr/mevzuat?MevzuatNo=abc",
            "",
        ):
            resolved = resolve_reference_source(url)

            check(
                f"taninmayan adres generic: {url[:40] or '(bos)'}",
                resolved.source_type == SOURCE_TYPE_GENERIC
                and resolved.is_consolidated is False,
                f"type={resolved.source_type}",
            )

        check(
            "adres parametreleri yalnizca RAKAM kabul "
            "ediyor (enjeksiyon yok)",
            resolve_reference_source(
                "https://www.mevzuat.gov.tr/mevzuat"
                "?MevzuatNo=1&MevzuatTur=7&MevzuatTertip=5x"
            ).source_type == SOURCE_TYPE_GENERIC,
        )

        # -------------------------------------------------
        # 1. GERIYE DONUK UYUMLULUK
        # -------------------------------------------------

        print("\n--- 1. geriye donuk uyumluluk ---")

        legacy = create_regulation_reference(
            db=session,
            title="Eski Cagri Referansi",
            source_url="https://mevzuat.test.local/eski-cagri",
            content="MADDE 1 - Eski cagri icerigi. " * 20,
        )

        check(
            "1. surum parametresi gondermeyen cagri "
            "calisiyor",
            legacy is not None,
        )

        check(
            "1. varsayilan source_type generic",
            legacy.source_type == SOURCE_TYPE_GENERIC,
            f"type={legacy.source_type}",
        )

        check(
            "1. varsayilan is_consolidated FALSE "
            "(guvenli taraf)",
            legacy.is_consolidated is False,
        )

        check(
            "1. version_date bos",
            legacy.version_date is None,
        )

        check(
            "1. olusturmada last_verified_at yaziliyor",
            legacy.last_verified_at is not None,
        )

        existing = session.scalars(
            select(RegulationReference).limit(50)
        ).all()

        check(
            "1. veritabanindaki tum kayitlarda source_type "
            "dolu",
            all(
                reference.source_type
                for reference in existing
            ),
        )

        # -------------------------------------------------
        # 2 + 3 + 4. KAYNAK TURUNE GORE OLUSTURMA
        # -------------------------------------------------

        print("\n--- 2/3/4. kaynak turune gore olusturma ---")

        FETCH_CALLS.clear()

        archive_response = client.post(
            "/api/regulation-references",
            json={
                "title": "Ödeme Hizmetleri Yönetmeliği (arşiv)",
                "source_url": ARCHIVE_URL,
            },
        )

        check(
            "2. arsiv referansi olusturuldu",
            archive_response.status_code == 201,
            f"status={archive_response.status_code} "
            f"body={archive_response.text[:160]}",
        )

        archive_body = archive_response.json()

        check(
            "2. arsiv referansi is_consolidated=false",
            archive_body["is_consolidated"] is False,
        )

        check(
            "2. arsiv referansi source_type dogru",
            archive_body["source_type"]
            == SOURCE_TYPE_RESMI_GAZETE,
            f"type={archive_body['source_type']}",
        )

        check(
            "2. arsiv metninde surum tarihi YOK",
            archive_body["version_date"] is None,
        )

        FETCH_CALLS.clear()

        consolidated_response = client.post(
            "/api/regulation-references",
            json={
                "title": "Ödeme Hizmetleri Yönetmeliği",
                "source_url": MEVZUAT_SHELL_URL,
            },
        )

        check(
            "3. konsolide referans olusturuldu",
            consolidated_response.status_code == 201,
            f"status={consolidated_response.status_code} "
            f"body={consolidated_response.text[:160]}",
        )

        consolidated_body = consolidated_response.json()

        check(
            "3. konsolide referans is_consolidated=true",
            consolidated_body["is_consolidated"] is True,
        )

        check(
            "3. source_type mevzuat_gov_tr",
            consolidated_body["source_type"]
            == SOURCE_TYPE_MEVZUAT_GOV_TR,
        )

        check(
            "3. KABUK sayfa degil METIN adresi indirildi",
            FETCH_CALLS == [MEVZUAT_TEXT_URL],
            f"cagrilar={FETCH_CALLS}",
        )

        check(
            "3. kayitta kullanicinin girdigi adres duruyor",
            consolidated_body["source_url"]
            == MEVZUAT_SHELL_URL,
        )

        check(
            "4. version_date isaretlerden cikarildi",
            consolidated_body["version_date"] == "2026-09-04",
            f"tarih={consolidated_body['version_date']}",
        )

        consolidated_id = consolidated_body["id"]

        stored = session.get(
            RegulationReference,
            consolidated_id,
        )

        check(
            "4. surum bilgisi VERITABANINA yazildi",
            stored.is_consolidated is True
            and stored.source_type == SOURCE_TYPE_MEVZUAT_GOV_TR
            and stored.version_date == date(2026, 9, 4),
            f"cons={stored.is_consolidated} "
            f"tarih={stored.version_date}",
        )

        check(
            "4. konsolide metin arsiv metninden FARKLI",
            stored.content
            != session.get(
                RegulationReference,
                archive_body["id"],
            ).content,
        )

        check(
            "4. liste yaniti surum bilgisini tasiyor",
            all(
                key in (
                    client.get("/api/regulation-references")
                    .json()[0]
                )
                for key in (
                    "source_type",
                    "is_consolidated",
                    "version_date",
                )
            ),
        )

        check(
            "4. detay yaniti surum bilgisini tasiyor",
            client.get(
                f"/api/regulation-references/{consolidated_id}"
            ).json()["is_consolidated"] is True,
        )

        # Isaret cikarimi birim dogrulamasi
        info = extract_consolidation_info(CONSOLIDATED_BODY)

        check(
            "4. isaret sayaci dogru",
            info.marker_count == 2,
            f"sayi={info.marker_count}",
        )

        check(
            "4. EN YENI tarih seciliyor",
            info.latest_amendment_date == date(2026, 9, 4),
        )

        check(
            "4. isaretsiz metinde tarih yok",
            extract_consolidation_info(
                ARCHIVE_BODY
            ).latest_amendment_date is None,
        )

        # -------------------------------------------------
        # 5. REFRESH SURUM BILGISINI KORUYOR
        # -------------------------------------------------

        print("\n--- 5. refresh davranisi ---")

        before = session.get(
            RegulationReference,
            consolidated_id,
        )

        updated_at_before = before.updated_at
        verified_before = before.last_verified_at

        refresh_same = client.post(
            f"/api/regulation-references/{consolidated_id}"
            f"/refresh"
        )

        check(
            "5. degismeyen refresh 200 donuyor",
            refresh_same.status_code == 200,
        )

        check(
            "5. degismeyen refresh changed=False",
            refresh_same.json()["changed"] is False,
        )

        session.expire_all()
        after_same = session.get(
            RegulationReference,
            consolidated_id,
        )

        check(
            "5. degismeyen refresh updated_at'i OYNATMIYOR "
            "(sprint 1 garantisi korundu)",
            after_same.updated_at == updated_at_before,
            f"once={updated_at_before} sonra={after_same.updated_at}",
        )

        check(
            "5. degismeyen refresh last_verified_at'i "
            "GUNCELLIYOR",
            after_same.last_verified_at is not None
            and after_same.last_verified_at != verified_before,
        )

        check(
            "5. degismeyen refresh surum bilgisini KORUYOR",
            after_same.is_consolidated is True
            and after_same.version_date == date(2026, 9, 4),
        )

        # Kaynak guncellenirse
        CURRENT_BODIES[MEVZUAT_TEXT_URL] = (
            CONSOLIDATED_BODY_UPDATED
        )

        refresh_changed = client.post(
            f"/api/regulation-references/{consolidated_id}"
            f"/refresh"
        )

        check(
            "5. degisen refresh changed=True",
            refresh_changed.json()["changed"] is True,
        )

        session.expire_all()
        after_changed = session.get(
            RegulationReference,
            consolidated_id,
        )

        check(
            "5. degisen refresh version_date'i ILERLETTI",
            after_changed.version_date == date(2026, 12, 15),
            f"tarih={after_changed.version_date}",
        )

        check(
            "5. degisen refresh updated_at'i guncelledi",
            after_changed.updated_at > updated_at_before,
        )

        check(
            "5. degisen refresh konsolide bayragini "
            "korudu",
            after_changed.is_consolidated is True,
        )

        # Repository seviyesinde: version_date bulunamazsa
        # eski deger SILINMEZ.
        preserved = update_regulation_reference_content(
            db=session,
            reference=after_changed,
            content=after_changed.content + "\nEk satir.",
            source_type=SOURCE_TYPE_MEVZUAT_GOV_TR,
            is_consolidated=True,
            version_date=None,
        )

        session.expire_all()

        check(
            "5. version_date bulunamayinca ESKI deger "
            "silinmiyor",
            preserved is True
            and session.get(
                RegulationReference,
                consolidated_id,
            ).version_date == date(2026, 12, 15),
        )

        CURRENT_BODIES[MEVZUAT_TEXT_URL] = CONSOLIDATED_BODY

        # -------------------------------------------------
        # 6 + 7. KARSILASTIRMA
        # -------------------------------------------------

        print("\n--- 6/7. karsilastirma ---")

        def build_monitor(name, reference_id):
            return create_monitor(
                db=session,
                name=name,
                url="https://example.com/rg",
                topic="Ödeme hizmetleri mevzuatı",
                tracking_plan="regulation_monitor",
                reference_regulation_id=reference_id,
                scope_type="full_page",
                schedule_time="09:00",
            )

        def build_change_and_analysis(monitor):

            old_snapshot = create_snapshot(
                session, monitor.id, "RG", "eski"
            )

            new_snapshot = create_snapshot(
                session, monitor.id, "RG", "eski\nyeni"
            )

            change = create_change(
                db=session,
                monitor_id=monitor.id,
                old_snapshot_id=old_snapshot.id,
                new_snapshot_id=new_snapshot.id,
                relevant=True,
                relevance_score=0.9,
                added_lines=["yeni içerik"],
                removed_lines=[],
            )

            analysis = create_ai_analysis(
                db=session,
                change_id=change.id,
                model_name="test-model",
                keyword_relevant=True,
                keyword_score=0.9,
                relevant=True,
                importance="high",
                category="policy",
                summary="Yönetmelikte değişiklik.",
                notification_recommended=True,
            )

            return change, analysis

        consolidated_reference = session.get(
            RegulationReference,
            consolidated_id,
        )

        archive_reference = session.get(
            RegulationReference,
            archive_body["id"],
        )

        # --- konsolide referansla
        consolidated_monitor = build_monitor(
            "Konsolide Monitor",
            consolidated_reference.id,
        )

        c_change, c_analysis = build_change_and_analysis(
            consolidated_monitor
        )

        consolidated_comparison = create_comparison(
            db=session,
            monitor_id=consolidated_monitor.id,
            change_id=c_change.id,
            ai_analysis_id=c_analysis.id,
            reference_regulation_id=consolidated_reference.id,
            reference_content_hash=(
                consolidated_reference.content_hash
            ),
            reference_is_consolidated=(
                consolidated_reference.is_consolidated
            ),
            status=STATUS_COMPLETED,
            overall_summary="Madde 41 değişti.",
            importance="high",
            model_name="test-model",
            items=[
                {
                    "affected_article": "Madde 41",
                    "affected_paragraph": None,
                    "change_type": "amended",
                    "old_text": "yazılı şekilde",
                    "new_text": "biyometrik yöntemlerle",
                    "source_evidence": "biyometrik",
                    "reference_evidence": "Madde 41",
                    "change_summary": "Yöntem değişti.",
                    "confidence": 0.95,
                }
            ],
        )

        check(
            "6. konsolide referansla karsilastirma "
            "OLUSTU",
            consolidated_comparison.status == STATUS_COMPLETED
            and len(consolidated_comparison.items) == 1,
        )

        check(
            "6. karsilastirma konsolide bayragini SAKLADI",
            consolidated_comparison.reference_is_consolidated
            is True,
        )

        check(
            "6. karsilastirma referans hash'ini saklamaya "
            "devam ediyor",
            consolidated_comparison.reference_content_hash
            == consolidated_reference.content_hash,
        )

        # --- konsolide OLMAYAN referansla
        archive_monitor = build_monitor(
            "Arsiv Monitor",
            archive_reference.id,
        )

        a_change, a_analysis = build_change_and_analysis(
            archive_monitor
        )

        archive_comparison = create_comparison(
            db=session,
            monitor_id=archive_monitor.id,
            change_id=a_change.id,
            ai_analysis_id=a_analysis.id,
            reference_regulation_id=archive_reference.id,
            reference_content_hash=(
                archive_reference.content_hash
            ),
            reference_is_consolidated=(
                archive_reference.is_consolidated
            ),
            status=STATUS_COMPLETED,
            overall_summary="Madde 41 değişti.",
            importance="high",
            model_name="test-model",
            items=[
                {
                    "affected_article": "Madde 41",
                    "affected_paragraph": None,
                    "change_type": "amended",
                    "old_text": "yazılı şekilde",
                    "new_text": "biyometrik yöntemlerle",
                    "source_evidence": "biyometrik",
                    "reference_evidence": "Madde 41",
                    "change_summary": "Yöntem değişti.",
                    "confidence": 0.95,
                }
            ],
        )

        check(
            "7. konsolide OLMAYAN karsilastirma yine "
            "CALISIYOR (engellenmez)",
            archive_comparison.status == STATUS_COMPLETED
            and len(archive_comparison.items) == 1,
        )

        check(
            "7. konsolide OLMAYAN karsilastirma "
            "isaretlendi",
            archive_comparison.reference_is_consolidated
            is False,
        )

        check(
            "7. dogrudan karsilastirma yaniti bayragi "
            "tasiyor",
            client.get(
                f"/api/regulation-comparisons/"
                f"{archive_comparison.id}"
            ).json()["reference_is_consolidated"] is False,
        )

        # -------------------------------------------------
        # 8. BILDIRIM DETAYINDA UYARI
        # -------------------------------------------------

        print("\n--- 8. bildirim detayi ---")

        def build_notification(monitor, change, analysis):
            return create_notification(
                db=session,
                monitor_id=monitor.id,
                change_id=change.id,
                ai_analysis_id=analysis.id,
                notification_type="in_app",
                title=f"{monitor.name} - Yeni Değişiklik",
                message=analysis.summary,
                importance=analysis.importance,
                category=analysis.category,
                recipient=None,
            )

        archive_notification = build_notification(
            archive_monitor,
            a_change,
            a_analysis,
        )

        archive_detail = client.get(
            f"/api/notifications/"
            f"{archive_notification.id}/detail"
        ).json()

        nested_archive = (
            archive_detail.get("regulation_comparison") or {}
        )

        check(
            "8. detay karsilastirmayi tasiyor",
            bool(nested_archive),
        )

        check(
            "8. detayda konsolide OLMAYAN bayragi false",
            nested_archive.get("reference_is_consolidated")
            is False,
        )

        consolidated_notification = build_notification(
            consolidated_monitor,
            c_change,
            c_analysis,
        )

        consolidated_detail = client.get(
            f"/api/notifications/"
            f"{consolidated_notification.id}/detail"
        ).json()

        nested_consolidated = (
            consolidated_detail.get("regulation_comparison")
            or {}
        )

        check(
            "8. konsolide detayda bayrak true",
            nested_consolidated.get(
                "reference_is_consolidated"
            ) is True,
        )

        check(
            "8. nested yanit hala TAM METIN tasimiyor",
            "content" not in nested_consolidated,
            f"alanlar={sorted(nested_consolidated.keys())}",
        )

        # -------------------------------------------------
        # 10. MEVCUT KARSILASTIRMA REGRESYONU
        # -------------------------------------------------

        print("\n--- 10. mevcut davranis regresyonu ---")

        check(
            "10. bayrak GONDERILMEYEN karsilastirma hala "
            "olusuyor (eski cagri)",
            create_comparison(
                db=session,
                monitor_id=consolidated_monitor.id,
                change_id=build_change_and_analysis(
                    consolidated_monitor
                )[0].id,
                status=STATUS_COMPLETED,
            ).reference_is_consolidated is None,
        )

        check(
            "10. website_monitor referanssiz calismaya "
            "devam ediyor",
            create_monitor(
                db=session,
                name="Surum Website Monitor",
                url="https://example.com/x",
                topic="konu",
                scope_type="full_page",
                schedule_time="09:00",
            ).reference_regulation_id is None,
        )

    finally:
        app.dependency_overrides.clear()

        session.close()
        transaction.rollback()
        connection.close()

        regulation_reference_service.fetch_website = real_fetch

        print("\n" + "-" * 70)
        print("Dis transaction geri alindi.")

    # -----------------------------------------------------
    # 9. ARAYUZ (statik kaynak dogrulamasi)
    # -----------------------------------------------------

    print("\n--- 9. arayuz referans durumu ---")

    monitor_form = section(
        app_source,
        "function MonitorForm(",
        "function NewMonitorPage()",
    )

    comparison_section = section(
        app_source,
        "function RegulationComparisonSection({",
        "function ChangeSection({",
    )

    check(
        "9. referans durumu yardimcisi tanimli",
        "function formatReferenceStatus(" in app_source,
    )

    check(
        "9. iki durum da adlandirilmis",
        "Güncel konsolide metin" in app_source
        and "Orijinal / tarihsel metin" in app_source,
    )

    check(
        "9. secim listesinde durum gosteriliyor",
        "formatReferenceStatus(reference)" in monitor_form,
    )

    check(
        "9. secili referansin durumu ayrica gosteriliyor",
        "selectedReference" in monitor_form
        and "reference-badge" in monitor_form,
    )

    check(
        "9. surum tarihi gosteriliyor",
        "Son işlenen değişiklik" in monitor_form
        and "formatVersionDate(" in app_source,
    )

    check(
        "9. konsolide olmayan secimde kullanici "
        "uyariliyor",
        "yayımlandığı tarihteki" in monitor_form,
    )

    check(
        "9. karsilastirma detayinda uyari var",
        "NON_CONSOLIDATED_WARNING" in comparison_section,
    )

    check(
        "9. uyari YALNIZCA konsolide olmayanda gosteriliyor",
        "reference_is_consolidated === false"
        in comparison_section,
    )

    check(
        "9. bilinmeyen dayanak icin ayri mesaj var",
        "UNKNOWN_CONSOLIDATION_WARNING"
        in comparison_section
        and "reference_is_consolidated === null"
        in comparison_section,
    )

    check(
        "9. uyari metni sahte kesinlik vermiyor",
        "konsolide olmayan referans metin" in app_source,
    )

    check(
        "9. durum rozetleri stillendirilmis",
        ".reference-badge.consolidated" in css_source
        and ".reference-badge.historical" in css_source
        and ".comparison-warning" in css_source,
    )

    check(
        "9. guvenli render korunuyor "
        "(dangerouslySetInnerHTML yok)",
        "dangerouslySetInnerHTML" not in comparison_section,
    )

    # -----------------------------------------------------
    # KALICI KAYIT BIRAKILMADI MI
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

        check(
            "veritabaninda referans sayisi degismedi",
            reference_count_after == reference_count_before,
            f"once={reference_count_before} "
            f"sonra={reference_count_after}",
        )

        check(
            "test karsilastirmasi kalmadi",
            comparison_count_after == 0,
            f"kalan={comparison_count_after}",
        )

    finally:
        verify.close()

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
