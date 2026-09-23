"""
TAKIP PLANI ARAYUZ DOGRULAMASI

Kapsam:
    16. takip plani secici formda var
    17. referans yonetmelik secici YALNIZCA mevzuat modunda
    18. normal monitor arayuzu regresyonu
    +   guvenlik: dangerouslySetInnerHTML yok, onizleme duz metin
    +   durustluk: "karsilastirma hazir" izlenimi verilmiyor

NOT: Projede bir JS test kosucusu (vitest/jest) kurulu degil
ve yeni paket kurulmuyor. Asagidakiler CALISMA ZAMANI
component testi degil, KAYNAK KODU uzerinde yapilan statik
dogrulamalardir. Bu, mevcut frontend testleriyle ayni
yaklasimdir.

Backend'e HIC dokunulmaz; veritabani kullanilmaz, ag istegi
yapilmaz.

Calistirma:
    python -m tests.test_regulation_tracking_frontend
"""

import io
import re

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]

APP_JSX = PROJECT_ROOT / "frontend" / "src" / "App.jsx"
API_JS = PROJECT_ROOT / "frontend" / "src" / "services" / "api.js"
INDEX_CSS = PROJECT_ROOT / "frontend" / "src" / "index.css"

MONITOR_ROUTES = (
    PROJECT_ROOT / "app" / "api" / "monitor_routes.py"
)
REFERENCE_ROUTES = (
    PROJECT_ROOT
    / "app"
    / "api"
    / "regulation_reference_routes.py"
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


def read(path: Path) -> str:
    return io.open(path, encoding="utf-8").read()


def section(source: str, start_marker: str, end_marker: str) -> str:
    start = source.index(start_marker)
    end = source.index(end_marker, start)

    return source[start:end]


def main() -> None:

    print("=" * 70)
    print("TAKIP PLANI ARAYUZ DOGRULAMASI")
    print("(statik kaynak kodu kontrolu)")
    print("=" * 70)

    app_source = read(APP_JSX)
    api_source = read(API_JS)
    css_source = read(INDEX_CSS)

    routes_source = read(MONITOR_ROUTES)
    reference_routes_source = read(REFERENCE_ROUTES)

    dashboard = section(
        app_source,
        "function DashboardPage(",
        "function ScopeOption(",
    )

    monitor_form = section(
        app_source,
        "function MonitorForm(",
        "function NewMonitorPage()",
    )

    plan_component = section(
        app_source,
        "function TrackingPlanOption({",
        "function NewRegulationReferenceForm({",
    )

    reference_form = section(
        app_source,
        "function NewRegulationReferenceForm({",
        "const NEW_MONITOR_DEFAULTS",
    )

    defaults = section(
        app_source,
        "const NEW_MONITOR_DEFAULTS",
        "function monitorToFormValues(",
    )

    form_values = section(
        app_source,
        "function monitorToFormValues(monitor)",
        "// Yeni izleme ve izleme düzenleme ekranları",
    )

    # -----------------------------------------------------
    # DAYANAK: BACKEND GERCEKTEN UYGULUYOR MU
    #
    # Arayuzde bir secenegin gosterilmesi, backend'de
    # karsiliginin olmasina baglidir.
    # -----------------------------------------------------

    print("\n--- dayanak: backend durumu ---")

    check(
        "backend takip planini GERCEKTEN kaydediyor",
        "tracking_plan=payload.tracking_plan" in routes_source,
    )

    check(
        "backend referans varligini GERCEKTEN dogruluyor",
        "ensure_regulation_reference_exists(" in routes_source,
    )

    check(
        "backend referans uc noktalarini sunuyor",
        "/api/regulation-references"
        in reference_routes_source,
    )

    check(
        "referans olusturma GERCEKTEN fetch + extract "
        "yapiyor",
        "fetch_regulation_content("
        in reference_routes_source,
    )

    # -----------------------------------------------------
    # 16. TAKIP PLANI SECICI
    # -----------------------------------------------------

    print("\n--- 16. takip plani secici ---")

    check(
        "16. TrackingPlanOption bileseni tanimli",
        "function TrackingPlanOption({" in app_source,
    )

    check(
        "16. formda 'Takip planı' basligi var",
        "Takip planı" in monitor_form,
    )

    check(
        "16. standart secenek metni dogru",
        "Standart Web Sitesi Takibi" in monitor_form,
    )

    check(
        "16. standart secenek aciklamasi dogru",
        "Web sayfasındaki değişiklikleri takip eder."
        in monitor_form,
    )

    check(
        "16. mevzuat secenek metni dogru",
        "Mevzuat / Yönetmelik Takibi" in monitor_form,
    )

    check(
        "16. mevzuat secenek aciklamasi dogru",
        "Yeni yayımlanan mevzuatı mevcut referans "
        "yönetmelikle karşılaştırmak" in monitor_form,
    )

    # TrackingPlanOption bileseni artik IKI ayri secim
    # grubunda kullaniliyor:
    #   - takip plani (website / regulation)
    #   - referans secim modu (automatic / manual)
    check(
        "16. iki takip plani secenegi render ediliyor",
        monitor_form.count('value={TRACKING_PLAN_WEBSITE}') == 1
        and monitor_form.count(
            'value={TRACKING_PLAN_REGULATION}'
        ) == 1,
        f"website={monitor_form.count('value={TRACKING_PLAN_WEBSITE}')} "
        f"regulation={monitor_form.count('value={TRACKING_PLAN_REGULATION}')}",
    )

    check(
        "16. varsayilan plan website_monitor",
        "tracking_plan: TRACKING_PLAN_WEBSITE" in defaults,
        re.search(r"tracking_plan: [A-Z_]+", defaults).group(0),
    )

    check(
        "16. plan secimi AYRI bir radio grubu "
        "(scope ile karismiyor)",
        'name = "tracking_plan"' in plan_component
        and "name={name}" in plan_component
        and 'name="scope"' not in plan_component,
        "grup adi parametre; varsayilan tracking_plan",
    )

    check(
        "16. referans modu AYRI radio grubu kullaniyor "
        "(plan secimiyle karismiyor)",
        'name="reference_selection_mode"' in monitor_form,
    )

    check(
        "16. plan secimi kontrollu state'te tutuluyor",
        "useState(initialValues.tracking_plan)"
        in monitor_form,
    )

    check(
        "16. payload takip planini gonderiyor",
        "tracking_plan: trackingPlan" in monitor_form,
    )

    # -----------------------------------------------------
    # 17. REFERANS SECICI YALNIZCA MEVZUAT MODUNDA
    # -----------------------------------------------------

    print("\n--- 17. referans secici yalnizca mevzuat modunda ---")

    check(
        "17. isRegulationPlan turetilmis degeri var",
        "const isRegulationPlan" in monitor_form
        and "TRACKING_PLAN_REGULATION" in monitor_form,
    )

    check(
        "17. referans paneli kosullu render ediliyor",
        "{isRegulationPlan && (" in monitor_form,
    )

    reference_panel = section(
        monitor_form,
        "{isRegulationPlan && (",
        "{/* KAYNAK */}",
    )

    check(
        "17. panelde 'Referans yönetmelik' basligi var",
        "Referans yönetmelik" in reference_panel,
    )

    check(
        "17. kayitli referanslar icin select var",
        "<select" in reference_panel
        and "reference-select" in reference_panel,
    )

    check(
        "17. yeni referans ekleme aksiyonu var",
        "Yeni Referans Yönetmelik" in reference_panel,
    )

    check(
        "17. referans listesi YALNIZCA mevzuat planinda "
        "cekiliyor",
        re.search(
            r"if \(!isRegulationPlan\) \{\s*return undefined;",
            monitor_form,
        )
        is not None,
    )

    check(
        "17. liste efekti plana bagli",
        "}, [isRegulationPlan]);" in monitor_form,
    )

    check(
        "17. standart plana donunce secim temizleniyor",
        "setReferenceRegulationId(null);" in monitor_form,
    )

    check(
        "17. standart planda payload referansi null "
        "gonderiyor",
        re.search(
            r"reference_regulation_id:[\s\S]{0,120}?: null,",
            monitor_form,
        )
        is not None,
    )

    check(
        "17. referans secilmeden submit engelleniyor "
        "(YALNIZCA manuel modda)",
        "referenceBlocked" in monitor_form
        and "!isAutomaticReference" in monitor_form
        and "!referenceSelected" in monitor_form,
    )

    submit_button = section(
        monitor_form,
        'type="submit"',
        "</button>",
    )

    check(
        "17. submit butonu referans blokajini kullaniyor",
        "referenceBlocked" in submit_button,
    )

    check(
        "17. yeni referans formu ad ve URL istiyor",
        "Yönetmelik adı" in reference_form
        and "Kaynak URL" in reference_form,
    )

    check(
        "17. kaydet butonu dogru etikete sahip",
        "Referansı Getir ve Kaydet" in reference_form,
    )

    check(
        "17. yukleme durumu gosteriliyor",
        "Yönetmelik içeriği alınıyor..." in reference_form,
    )

    check(
        "17. basari mesaji gosteriliyor",
        "Referans yönetmelik kaydedildi." in monitor_form,
    )

    check(
        "17. fetch/extraction hatasi kullaniciya "
        "gosteriliyor",
        "setError(" in reference_form
        and "requestError.message" in reference_form,
    )

    check(
        "17. referans api istemcisi tanimli",
        "export async function getRegulationReferences("
        in api_source
        and "export async function createRegulationReference("
        in api_source,
    )

    check(
        "17. referans istekleri dogru adrese gidiyor",
        "/api/regulation-references" in api_source,
    )

    check(
        "17. referans stilleri mevcut token'larla eklendi",
        ".reference-section" in css_source
        and ".reference-form" in css_source,
    )

    # -----------------------------------------------------
    # 18. NORMAL MONITOR ARAYUZU REGRESYONU
    # -----------------------------------------------------

    print("\n--- 18. normal monitor arayuzu regresyonu ---")

    check(
        "18. uc kapsam secenegi duruyor",
        len(re.findall(r"<ScopeOption", monitor_form)) == 3,
        f"adet={len(re.findall(r'<ScopeOption', monitor_form))}",
    )

    for value in ("full_page", "auto_section", "manual_section"):
        check(
            f"18. kapsam secenegi duruyor: {value}",
            f'value="{value}"' in monitor_form,
        )

    check(
        "18. varsayilan kapsam hala full_page",
        'scope_type: "full_page"' in defaults,
    )

    check(
        "18. manuel bolum secimi akisi bozulmadi",
        "getSectionCandidates(" in monitor_form
        and "manualBlocked" in monitor_form,
    )

    check(
        "18. zamanlama alanlari duruyor",
        'name="schedule_type"' in monitor_form
        and 'name="schedule_time"' in monitor_form
        and 'name="start_date"' in monitor_form,
    )

    check(
        "18. bildirim secenekleri duruyor",
        "Uygulama içi bildirim" in monitor_form
        and "E-mail bildirimi" in monitor_form,
    )

    check(
        "18. SMTP capability kontrolu duruyor",
        "getCapabilities()" in monitor_form
        and "disabled={!emailAvailable}" in monitor_form,
    )

    check(
        "18. referans secici FormData'ya yeni isimli alan "
        "eklemedi",
        'name="reference_regulation_id"' not in monitor_form,
        "deger kontrollu state'ten okunuyor",
    )

    check(
        "18. kartta takip plani gosteriliyor",
        "Takip planı" in dashboard
        and "formatTrackingPlan(" in dashboard,
    )

    check(
        "18. kartta referans YALNIZCA mevzuat planinda "
        "gosteriliyor",
        "monitor.tracking_plan ===" in dashboard
        and "TRACKING_PLAN_REGULATION" in dashboard,
    )

    check(
        "18. kart referansin TAM METNINI degil ADINI "
        "gosteriyor",
        "reference_regulation_title" in dashboard
        and "reference_regulation_content" not in dashboard,
    )

    check(
        "18. kartta mevcut alanlar duruyor",
        "İzleme kapsamı" in dashboard
        and "Zamanlama" in dashboard
        and "Takip konusu" in dashboard
        and "Son kontrol" in dashboard
        and "E-mail bildirimi" in dashboard,
    )

    check(
        "18. kart aksiyonlari duruyor",
        "Şimdi Kontrol Et" in dashboard
        and "Düzenle" in dashboard
        and "Pasife Al" in dashboard,
    )

    check(
        "18. plan tasimayan eski kayit standart olarak "
        "aciliyor",
        "monitor.tracking_plan ??" in form_values,
    )

    check(
        "18. edit formu kayitli referansi yukluyor",
        "monitor.reference_regulation_id ?? null" in form_values,
    )

    check(
        "18. edit formu referansi state'e aktariyor",
        "initialValues.reference_regulation_id"
        in monitor_form,
    )

    # -----------------------------------------------------
    # DURUSTLUK: KARSILASTIRMA HENUZ YOK
    # -----------------------------------------------------

    print("\n--- durustluk: karsilastirma henuz yok ---")

    check(
        "karsilastirma durumu backend'den okunuyor",
        "regulation_comparison_status" in app_source,
    )

    # Sprint 2: karsilastirma GERCEKTEN calisiyor.
    # Artik "henuz yapilmiyor" metni gosterilmemeli.

    check(
        "artik 'henuz yapilmamaktadir' metni GOSTERILMIYOR",
        "henüz yapılmamaktadır" not in app_source
        and "bu sürümde çalıştırılmadı" not in app_source,
    )

    check(
        "kullaniciya karsilastirmanin ne zaman calistigi "
        "bastan soyleniyor",
        "karşılaştırılır" in monitor_form
        and "detayında gösterilir" in monitor_form,
    )

    check(
        "her calistirma durumu icin GERCEK bir aciklama var",
        all(
            status in app_source
            for status in (
                "completed",
                "no_match",
                "failed",
                "skipped_first_run",
                "skipped_no_change",
                "skipped_irrelevant",
                "skipped_no_reference",
            )
        ),
    )

    check(
        "bilinmeyen durum icin uydurma metin uretilmiyor",
        'return COMPARISON_STATUS_MESSAGES[status] ?? "";'
        in app_source,
    )

    check(
        "hukuki sorumluluk notu gosteriliyor",
        "Resmî metin esas alınmalıdır." in app_source,
    )

    check(
        "sorumluluk notu HEM formda HEM karsilastirmada",
        app_source.count("COMPARISON_DISCLAIMER") >= 3,
        f"kullanim={app_source.count('COMPARISON_DISCLAIMER')}",
    )

    # -----------------------------------------------------
    # GUVENLIK
    # -----------------------------------------------------

    print("\n--- guvenlik ---")

    check(
        "dangerouslySetInnerHTML KULLANIMI yok",
        re.search(
            r"dangerouslySetInnerHTML\s*[=:]",
            app_source,
        )
        is None,
        "yalnizca aciklama satirinda gecmesi serbest",
    )

    check(
        "innerHTML / srcdoc kullanilmiyor",
        "innerHTML" not in app_source
        and "srcdoc" not in app_source,
    )

    check(
        "referans onizlemesi backend'de DUZ METIN uretiliyor",
        "build_preview(" in reference_routes_source,
    )

    check(
        "liste yaniti tam metin tasimiyor",
        "RegulationReferenceSummary"
        in reference_routes_source
        and "content: str"
        not in read(
            PROJECT_ROOT
            / "app"
            / "schemas"
            / "regulation_reference.py"
        ),
    )

    check(
        "referans formu sir loglamiyor",
        "console.log" not in reference_form,
    )

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
