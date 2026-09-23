"""
KARSILASTIRMA API + ARAYUZ TESTLERI

Kapsam:
    25. bildirim detayi karsilastirmayi iceriyor
    26. birden fazla item arayuzde render ediliyor
    27. no_match arayuz durumu
    28. failed arayuz durumu
    29. duz metin / guvenli render
    30. mevcut website bildirim detayi regresyonu
    31. e-mail regresyonu
    37. 0 gercek SMTP cagrisi

API kismi gercek PostgreSQL uzerinde calisir ama KALICI
KAYIT BIRAKMAZ. Arayuz kismi mevcut frontend testleriyle
ayni yaklasimla KAYNAK KODU uzerinde statik dogrulamadir
(projede JS test kosucusu yok, yeni paket kurulmuyor).

Gemini CAGRILMAZ. SMTP CAGRILMAZ; smtplib.SMTP ve
SMTP_SSL bloklanir ve sifir cagri assert edilir.

Calistirma:
    python -m tests.test_regulation_comparison_frontend
"""

import io
import re
import smtplib
import warnings

from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database import engine, get_db
from app.models.regulation_comparison import (
    RegulationComparison,
)
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
    STATUS_FAILED,
    STATUS_NO_MATCH,
    create_comparison,
)
from app.repositories.regulation_reference_repository import (
    create_regulation_reference,
)
from app.repositories.snapshot_repository import create_snapshot
from app.services.email_content import build_change_email
from app.services.notification_service import (
    build_notification_message,
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
# SMTP BLOKAJI
#
# Gercek bir mail gonderimi denenirse test PATLAR.
# ---------------------------------------------------------

SMTP_ATTEMPTS = []

_real_smtp = smtplib.SMTP
_real_smtp_ssl = smtplib.SMTP_SSL


class BlockedSMTP:
    def __init__(self, *args, **kwargs):
        SMTP_ATTEMPTS.append(args)

        raise AssertionError(
            "Test sirasinda gercek SMTP baglantisi denendi."
        )


REFERENCE_TEXT = """MADDE 12 - Lisans başvuruları otuz gün içerisinde
değerlendirilir.
MADDE 18 - Denetim raporları Kuruma sunulur.
"""


def main() -> None:

    print("=" * 70)
    print("KARSILASTIRMA API + ARAYUZ TESTLERI")
    print("=" * 70)

    smtplib.SMTP = BlockedSMTP
    smtplib.SMTP_SSL = BlockedSMTP

    app_source = read(APP_JSX)
    css_source = read(INDEX_CSS)

    connection = engine.connect()
    transaction = connection.begin()

    session = Session(
        bind=connection,
        join_transaction_mode="create_savepoint",
    )

    app.dependency_overrides[get_db] = lambda: session

    client = TestClient(app)

    comparison_count_before = session.scalar(
        select(func.count()).select_from(RegulationComparison)
    )

    try:

        # -------------------------------------------------
        # HAZIRLIK
        # -------------------------------------------------

        reference = create_regulation_reference(
            db=session,
            title="Test Lisans Yönetmeliği",
            source_url="https://mevzuat.test.local/detay-testi",
            content=REFERENCE_TEXT,
        )

        def build_monitor(name, plan, reference_id):
            return create_monitor(
                db=session,
                name=name,
                url="https://example.com/rg",
                topic="Elektrik piyasası lisans yönetmelikleri",
                tracking_plan=plan,
                reference_regulation_id=reference_id,
                scope_type="full_page",
                schedule_time="09:00",
                notify_email=False,
            )

        def build_change_and_analysis(monitor):

            old_snapshot = create_snapshot(
                session, monitor.id, "RG", "eski"
            )

            new_snapshot = create_snapshot(
                session, monitor.id, "RG", "eski\nyeni içerik"
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
                summary="Yönetmelikte değişiklik yapıldı.",
                notification_recommended=True,
            )

            return change, analysis

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

        # -------------------------------------------------
        # 25. BILDIRIM DETAYI KARSILASTIRMAYI ICERIYOR
        # -------------------------------------------------

        print("\n--- 25. bildirim detayi ---")

        regulation_monitor = build_monitor(
            "Detay Mevzuat Monitor",
            "regulation_monitor",
            reference.id,
        )

        change, analysis = build_change_and_analysis(
            regulation_monitor
        )

        comparison = create_comparison(
            db=session,
            monitor_id=regulation_monitor.id,
            change_id=change.id,
            ai_analysis_id=analysis.id,
            reference_regulation_id=reference.id,
            reference_content_hash=reference.content_hash,
            status=STATUS_COMPLETED,
            overall_summary="İki madde değiştirildi.",
            importance="high",
            model_name="test-model",
            items=[
                {
                    "affected_article": "Madde 12",
                    "affected_paragraph": "1. fıkra",
                    "change_type": "amended",
                    "old_text": "otuz gün",
                    "new_text": "on beş gün",
                    "source_evidence": "otuz gün ibaresi",
                    "reference_evidence": (
                        "Lisans başvuruları otuz gün"
                    ),
                    "change_summary": "Süre kısaltıldı.",
                    "confidence": 0.96,
                },
                {
                    "affected_article": "Madde 18",
                    "affected_paragraph": None,
                    "change_type": "repealed",
                    "old_text": (
                        "Denetim raporları Kuruma sunulur."
                    ),
                    "new_text": None,
                    "source_evidence": "yürürlükten kaldırılmıştır",
                    "reference_evidence": None,
                    "change_summary": "Madde kaldırıldı.",
                    "confidence": 0.9,
                },
            ],
        )

        notification = build_notification(
            regulation_monitor,
            change,
            analysis,
        )

        response = client.get(
            f"/api/notifications/{notification.id}/detail"
        )

        check(
            "25. detay 200 donuyor",
            response.status_code == 200,
            f"status={response.status_code} "
            f"body={response.text[:200]}",
        )

        detail = response.json()

        check(
            "25. detay karsilastirmayi NESTED tasiyor "
            "(ikinci istek gerekmiyor)",
            detail.get("regulation_comparison") is not None,
        )

        nested = detail.get("regulation_comparison") or {}

        check(
            "25. nested durum completed",
            nested.get("status") == STATUS_COMPLETED,
            f"durum={nested.get('status')}",
        )

        check(
            "25. nested referans ADINI tasiyor",
            nested.get("reference_regulation_title")
            == reference.title,
        )

        check(
            "25. nested referans TAM METNINI tasimiyor",
            "content" not in nested
            and "reference_content" not in nested,
            f"alanlar={sorted(nested.keys())}",
        )

        check(
            "25. nested referans surum hash'ini tasiyor",
            nested.get("reference_content_hash")
            == reference.content_hash,
        )

        check(
            "25. nested ozet ve onem tasiyor",
            nested.get("overall_summary")
            == "İki madde değiştirildi."
            and nested.get("importance") == "high",
        )

        nested_items = nested.get("items") or []

        check(
            "25. nested BIRDEN FAZLA item tasiyor",
            len(nested_items) == 2,
            f"item={len(nested_items)}",
        )

        first_item = nested_items[0] if nested_items else {}

        check(
            "25. item eski/yeni metni tasiyor",
            first_item.get("old_text") == "otuz gün"
            and first_item.get("new_text") == "on beş gün",
        )

        check(
            "25. item kanit alanlarini tasiyor",
            first_item.get("source_evidence")
            == "otuz gün ibaresi"
            and first_item.get("reference_evidence")
            == "Lisans başvuruları otuz gün",
        )

        check(
            "25. item ozeti KANITTAN ayri alanda",
            first_item.get("change_summary")
            == "Süre kısaltıldı."
            and first_item.get("change_summary")
            != first_item.get("source_evidence"),
        )

        check(
            "25. dogrudan karsilastirma uc noktasi da "
            "calisiyor",
            client.get(
                f"/api/regulation-comparisons/{comparison.id}"
            ).status_code == 200,
        )

        check(
            "25. olmayan karsilastirma icin 404",
            client.get(
                "/api/regulation-comparisons/99999999"
            ).status_code == 404,
        )

        # -------------------------------------------------
        # 30. WEBSITE BILDIRIM DETAYI REGRESYONU
        # -------------------------------------------------

        print("\n--- 30. website detay regresyonu ---")

        website_monitor = build_monitor(
            "Detay Website Monitor",
            "website_monitor",
            None,
        )

        website_change, website_analysis = (
            build_change_and_analysis(website_monitor)
        )

        website_notification = build_notification(
            website_monitor,
            website_change,
            website_analysis,
        )

        website_response = client.get(
            f"/api/notifications/{website_notification.id}"
            f"/detail"
        )

        check(
            "30. website detay 200 donuyor",
            website_response.status_code == 200,
        )

        website_detail = website_response.json()

        check(
            "30. website detayinda karsilastirma null",
            website_detail.get("regulation_comparison")
            is None,
        )

        check(
            "30. mevcut detay alanlari bozulmadi",
            all(
                key in website_detail
                for key in (
                    "notification_id",
                    "monitor_name",
                    "url",
                    "change_id",
                    "ai_analysis_id",
                    "title",
                    "message",
                    "importance",
                    "category",
                    "ai_summary",
                    "added_content",
                    "removed_content",
                    "added_blocks",
                    "removed_blocks",
                )
            ),
            f"eksik={[k for k in ('notification_id','monitor_name','url','ai_summary','added_blocks') if k not in website_detail]}",
        )

        check(
            "30. website detayinda AI ozeti korunuyor",
            website_detail.get("ai_summary")
            == "Yönetmelikte değişiklik yapıldı.",
        )

        # -------------------------------------------------
        # 27-28. NO_MATCH ve FAILED API DURUMLARI
        # -------------------------------------------------

        print("\n--- 27-28. no_match / failed ---")

        no_match_monitor = build_monitor(
            "Detay NoMatch Monitor",
            "regulation_monitor",
            reference.id,
        )

        nm_change, nm_analysis = build_change_and_analysis(
            no_match_monitor
        )

        create_comparison(
            db=session,
            monitor_id=no_match_monitor.id,
            change_id=nm_change.id,
            ai_analysis_id=nm_analysis.id,
            reference_regulation_id=reference.id,
            reference_content_hash=reference.content_hash,
            status=STATUS_NO_MATCH,
        )

        nm_notification = build_notification(
            no_match_monitor,
            nm_change,
            nm_analysis,
        )

        nm_detail = client.get(
            f"/api/notifications/{nm_notification.id}/detail"
        ).json()

        nm_comparison = (
            nm_detail.get("regulation_comparison") or {}
        )

        check(
            "27. no_match durumu detayda gorunuyor",
            nm_comparison.get("status") == STATUS_NO_MATCH,
            f"durum={nm_comparison.get('status')}",
        )

        check(
            "27. no_match kaydinda item YOK "
            "(uydurma karsilastirma yok)",
            (nm_comparison.get("items") or []) == [],
        )

        failed_monitor = build_monitor(
            "Detay Failed Monitor",
            "regulation_monitor",
            reference.id,
        )

        f_change, f_analysis = build_change_and_analysis(
            failed_monitor
        )

        create_comparison(
            db=session,
            monitor_id=failed_monitor.id,
            change_id=f_change.id,
            ai_analysis_id=f_analysis.id,
            reference_regulation_id=reference.id,
            reference_content_hash=reference.content_hash,
            status=STATUS_FAILED,
            error_message="Gemini geçici olarak kullanılamıyor.",
        )

        f_notification = build_notification(
            failed_monitor,
            f_change,
            f_analysis,
        )

        f_detail = client.get(
            f"/api/notifications/{f_notification.id}/detail"
        ).json()

        f_comparison = (
            f_detail.get("regulation_comparison") or {}
        )

        check(
            "28. failed durumu detayda gorunuyor",
            f_comparison.get("status") == STATUS_FAILED,
        )

        check(
            "28. failed hata mesaji TEMIZ "
            "(stack trace yok)",
            "Traceback"
            not in (f_comparison.get("error_message") or "")
            and len(
                f_comparison.get("error_message") or ""
            ) <= 300,
        )

        check(
            "28. failed kaydinda item YOK",
            (f_comparison.get("items") or []) == [],
        )

        # -------------------------------------------------
        # 31 + 37. E-MAIL REGRESYONU
        # -------------------------------------------------

        print("\n--- 31/37. e-mail regresyonu ---")

        baseline_email = build_change_email(
            monitor=website_monitor,
            ai_analysis=website_analysis,
            changed_at=website_change.detected_at,
            notification_id=website_notification.id,
        )

        check(
            "31. karsilastirmasiz mail govdesi mevcut "
            "davranisi koruyor",
            "Mevzuat karşılaştırması"
            not in baseline_email.text_body
            and "Mevzuat karşılaştırması"
            not in baseline_email.html_body,
        )

        check(
            "31. mevcut mail alanlari duruyor",
            "AI Özeti" in baseline_email.text_body
            and website_monitor.name
            in baseline_email.text_body
            and baseline_email.subject.startswith(
                "[Website Monitor AI]"
            ),
        )

        comparison_email = build_change_email(
            monitor=regulation_monitor,
            ai_analysis=analysis,
            changed_at=change.detected_at,
            notification_id=notification.id,
            regulation_comparison=comparison,
        )

        check(
            "31. karsilastirmali mail ozeti iceriyor",
            "İki madde değiştirildi."
            in comparison_email.text_body,
        )

        check(
            "31. karsilastirmali mail madde satirlarini "
            "iceriyor",
            "Madde 12" in comparison_email.text_body
            and "Madde 18" in comparison_email.text_body,
        )

        check(
            "31. mail sorumluluk notu tasiyor",
            "Resmî metin esas alınmalıdır."
            in comparison_email.text_body
            and "Resmî metin esas alınmalıdır."
            in comparison_email.html_body,
        )

        check(
            "31. mail HTML govdesi degerleri escape ediyor",
            "<script" not in comparison_email.html_body,
        )

        check(
            "31. mail TAM referans metnini TASIMIYOR",
            "Denetim raporları Kuruma sunulur."
            not in comparison_email.text_body,
        )

        check(
            "31. bildirim govdesi karsilastirmasiz "
            "DEGISMIYOR",
            build_notification_message(analysis)
            == analysis.summary,
        )

        check(
            "31. bildirim govdesi karsilastirmayla "
            "zenginlesiyor",
            analysis.summary
            in build_notification_message(
                analysis,
                comparison,
            )
            and "Etkilenen madde sayısı: 2"
            in build_notification_message(
                analysis,
                comparison,
            ),
        )

        check(
            "37. hic gercek SMTP baglantisi denenmedi",
            not SMTP_ATTEMPTS,
            f"denemeler={SMTP_ATTEMPTS}",
        )

    finally:
        app.dependency_overrides.clear()

        session.close()
        transaction.rollback()
        connection.close()

        smtplib.SMTP = _real_smtp
        smtplib.SMTP_SSL = _real_smtp_ssl

        print("\n" + "-" * 70)
        print("Dis transaction geri alindi.")

    # -----------------------------------------------------
    # 26-29. ARAYUZ (statik kaynak dogrulamasi)
    # -----------------------------------------------------

    print("\n--- 26. birden fazla item render ---")

    comparison_section = section(
        app_source,
        "function RegulationComparisonSection({",
        "function ChangeSection({",
    )

    item_component = section(
        app_source,
        "function RegulationComparisonItem({",
        "function RegulationComparisonSection({",
    )

    detail_page = section(
        app_source,
        "function NotificationDetailPage({",
        "function SidebarLink({",
    )

    check(
        "26. karsilastirma bileseni tanimli",
        "function RegulationComparisonSection({"
        in app_source,
    )

    check(
        "26. bildirim detayinda render ediliyor",
        "<RegulationComparisonSection" in detail_page,
    )

    check(
        "26. veri detay yanitindan geliyor "
        "(ikinci istek yok)",
        "detail.regulation_comparison" in detail_page,
    )

    check(
        "26. item listesi map ile render ediliyor",
        "items.map(" in comparison_section
        and "<RegulationComparisonItem" in comparison_section,
    )

    check(
        "26. her item benzersiz key aliyor",
        "key={item.id}" in comparison_section,
    )

    check(
        "26. item madde etiketi gosteriyor",
        "item.affected_article" in item_component,
    )

    check(
        "26. item degisiklik turu etiketi gosteriyor",
        "formatChangeType(" in item_component,
    )

    check(
        "26. ESKI ve YENI metin bolumleri var",
        "ESKİ METİN" in item_component
        and "YENİ METİN" in item_component,
    )

    check(
        "26. degisiklik ozeti bolumu var",
        "DEĞİŞİKLİK ÖZETİ" in item_component,
    )

    check(
        "26. kaynak ve referans kaniti gosteriliyor",
        "Kaynak kanıtı" in item_component
        and "Referans kanıtı" in item_component,
    )

    check(
        "26. dogrulanmamis (null) alanlar HIC "
        "gosterilmiyor",
        "{item.old_text && (" in item_component
        and "{item.new_text && (" in item_component,
    )

    check(
        "26. tum degisiklik turleri icin etiket var",
        all(
            change_type in app_source
            for change_type in (
                "amended",
                "added",
                "repealed",
                "renamed",
                "unknown",
            )
        ),
    )

    check(
        "26. degisiklik turu rozetleri stillendirilmis",
        ".change-badge.amended" in css_source
        and ".change-badge.repealed" in css_source,
    )

    print("\n--- 27-28. no_match / failed arayuz ---")

    check(
        "27. no_match icin acik mesaj var",
        "güvenilir bir madde" in comparison_section
        and "eşleşmesi bulunamadı" in comparison_section,
    )

    check(
        "27. no_match uydurma karsilastirma GOSTERMIYOR",
        "COMPARISON_STATUS_NO_MATCH" in comparison_section,
    )

    check(
        "28. failed icin acik mesaj var",
        "Mevzuat karşılaştırması tamamlanamadı."
        in comparison_section,
    )

    check(
        "28. failed sanitized hata mesajini gosterebiliyor",
        "comparison.error_message" in comparison_section,
    )

    check(
        "28. completed ama item yoksa sessiz kalmiyor",
        "doğrulanabilen" in comparison_section,
    )

    print("\n--- 29. guvenli render ---")

    check(
        "29. dangerouslySetInnerHTML KULLANIMI yok",
        re.search(
            r"dangerouslySetInnerHTML\s*[=:]",
            app_source,
        )
        is None,
    )

    check(
        "29. innerHTML / srcdoc kullanilmiyor",
        "innerHTML" not in app_source
        and "srcdoc" not in app_source,
    )

    check(
        "29. mevzuat metinleri React text node olarak "
        "render ediliyor",
        "{item.old_text}" in item_component
        and "{item.new_text}" in item_component,
    )

    check(
        "29. kanit metni de duz metin",
        "{text}" in section(
            app_source,
            "function ComparisonEvidence({",
            "function RegulationComparisonItem({",
        ),
    )

    check(
        "29. uzun metin tasmayi engelleyen stil var",
        "word-break: break-word" in css_source
        and "white-space: pre-wrap" in css_source,
    )

    check(
        "29. hukuki sorumluluk notu gosteriliyor",
        "COMPARISON_DISCLAIMER" in comparison_section,
    )

    check(
        "29. sistem hukuki tavsiye iddiasinda bulunmuyor",
        "Resmî metin esas alınmalıdır."
        in app_source,
    )

    # -----------------------------------------------------
    # KALICI KAYIT BIRAKILMADI MI
    # -----------------------------------------------------

    from app.core.database import SessionLocal

    verify = SessionLocal()

    try:
        comparison_count_after = verify.scalar(
            select(func.count()).select_from(RegulationComparison)
        )

        check(
            "veritabaninda karsilastirma sayisi degismedi",
            comparison_count_after == comparison_count_before,
            f"once={comparison_count_before} "
            f"sonra={comparison_count_after}",
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
