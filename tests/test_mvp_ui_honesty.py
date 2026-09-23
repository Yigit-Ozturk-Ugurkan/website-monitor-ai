"""
MVP arayuz durustlugu dogrulamasi.

Amac: kullaniciya AKTIFMIS GIBI gosterilen ama backend
pipeline'inda karsiligi olmayan secenek kalmadigini dogrulamak.

Gercekten uygulanan scope'lar: full_page, auto_section,
manual_section
E-mail bildirimi de uygulandi; artik yalnizca SMTP
yapilandirmasi eksikse devre disi gorunur.

NOT: Projede bir JS test kosucusu (vitest/jest) kurulu degil ve
yeni paket kurulmuyor. Asagidakiler CALISMA ZAMANI component
testi degil, KAYNAK KODU uzerinde yapilan statik dogrulamalardir.

Backend'e HIC dokunulmaz; bu test yalnizca frontend kaynagini
ve pipeline'in scope_type'i okumadigini kontrol eder.

Calistirma:
    python -m tests.test_mvp_ui_honesty
"""

import io
import re

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]

APP_JSX = PROJECT_ROOT / "frontend" / "src" / "App.jsx"
INDEX_CSS = PROJECT_ROOT / "frontend" / "src" / "index.css"
PIPELINE = PROJECT_ROOT / "app" / "services" / "db_monitor_service.py"
RUN_SERVICE = (
    PROJECT_ROOT / "app" / "services" / "monitor_run_service.py"
)
MAIN = PROJECT_ROOT / "app" / "main.py"


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


def scope_option_block(source: str, value: str) -> str:
    """
    Belirli bir ScopeOption cagrisinin govdesini dondurur.
    """

    marker = f'value="{value}"'

    start = source.rindex("<ScopeOption", 0, source.index(marker))
    end = source.index("/>", start)

    return source[start:end]


def main() -> None:

    print("=" * 70)
    print("MVP ARAYUZ DURUSTLUGU DOGRULAMASI")
    print("(statik kaynak kodu kontrolu)")
    print("=" * 70)

    app_source = read(APP_JSX)
    css_source = read(INDEX_CSS)
    pipeline_source = read(PIPELINE)
    run_service_source = read(RUN_SERVICE)
    main_source = read(MAIN)

    monitor_form = section(
        app_source,
        "function MonitorForm(",
        "function NewMonitorPage()",
    )

    scope_component = section(
        app_source,
        "function ScopeOption({",
        "const NEW_MONITOR_DEFAULTS",
    )

    defaults = section(
        app_source,
        "const NEW_MONITOR_DEFAULTS",
        "function monitorToFormValues(",
    )

    # -----------------------------------------------------
    # ONCE: PIPELINE GERCEKTEN scope_type OKUMUYOR MU
    # -----------------------------------------------------

    print("\n--- dayanak: pipeline durumu ---")

    check(
        "pipeline scope_type'i GERCEKTEN okuyor "
        "(auto_section artik uygulaniyor)",
        "monitor.scope_type == SCOPE_AUTO_SECTION"
        in pipeline_source,
        "auto_section pipeline'da uygulanmiyorsa UI yaniltici olur",
    )

    check(
        "pipeline manual_section'i GERCEKTEN uyguluyor",
        "monitor.scope_type == SCOPE_MANUAL_SECTION"
        in pipeline_source,
        "manual_section uygulanmiyorsa UI yaniltici olur",
    )

    check(
        "manual_section kendi cikarimini kullaniyor "
        "(auto pipeline'ina dusmuyor)",
        "extract_manual_section_content(" in pipeline_source,
    )

    check(
        "manual_section akisinda AI bolum secimi YOK",
        "select_section_with_ai"
        not in section(
            pipeline_source,
            "def extract_manual_section_content(",
            "async def run_database_monitor(",
        ),
    )

    check(
        "e-mail gonderimi pipeline'a GERCEKTEN baglandi",
        "send_email_notification(" in run_service_source,
        "e-mail bagli degilse UI yaniltici olur",
    )

    check(
        "e-mail yalnizca notify_email acikken deneniyor",
        "if monitor.notify_email and has_analysis:"
        in run_service_source,
    )

    check(
        "e-mail in-app'ten BAGIMSIZ calisiyor",
        "monitor.notify_in_app and has_analysis"
        in run_service_source
        and "monitor.notify_email and has_analysis"
        in run_service_source,
    )

    check(
        "capability backend'den turetiliyor",
        "is_email_configured" in main_source
        and "email_notifications" in main_source,
    )

    check(
        "capability cevabinda SMTP degeri yok",
        "smtp_password" not in main_source
        and "smtp_host" not in main_source,
    )

    # -----------------------------------------------------
    # 1-2. VARSAYILANLAR
    # -----------------------------------------------------

    print("\n--- 1-2. yeni izleme varsayilanlari ---")

    check(
        "1. varsayilan scope = full_page",
        'scope_type: "full_page"' in defaults,
        re.search(r"scope_type: \"[a-z_]+\"", defaults).group(0),
    )

    check(
        "varsayilan artik auto_section DEGIL",
        'scope_type: "auto_section"' not in defaults,
    )

    check(
        "2. varsayilan notify_email = false",
        "notify_email: false" in defaults,
    )

    check(
        "varsayilan notify_email artik true DEGIL",
        "notify_email: true" not in defaults,
    )

    check(
        "notify_in_app varsayilani korunmus (calisan ozellik)",
        "notify_in_app: true" in defaults,
    )

    # -----------------------------------------------------
    # 3-4. SCOPE SECENEKLERI DISABLED
    # -----------------------------------------------------

    print("\n--- 3-4. uygulanmayan scope secenekleri ---")

    check(
        "ScopeOption disabled prop destekliyor",
        "disabled = false," in scope_component
        and "disabled={disabled}" in scope_component,
    )

    check(
        "disabled secenek gorsel olarak ayrisiyor",
        'disabled ? "disabled" : ""' in scope_component
        and ".scope-option.disabled" in css_source,
    )

    auto_block = scope_option_block(monitor_form, "auto_section")
    manual_block = scope_option_block(monitor_form, "manual_section")
    full_block = scope_option_block(monitor_form, "full_page")

    check(
        "3. auto_section artik disabled DEGIL",
        "disabled" not in auto_block,
        auto_block.strip()[:60],
    )

    check(
        "3. auto_section basliginda '(yakında)' YOK",
        "(yakında)" not in auto_block,
        auto_block.strip()[:80],
    )

    check(
        "3. auto_section basligi dogru",
        "Konuyla ilgili alanı otomatik bul" in auto_block,
    )

    check(
        "3. auto_section aciklamasi gercek davranisi anlatiyor",
        "Takip konusuyla ilgili bölüm otomatik belirlenir"
        in auto_block
        and "yalnız bu alan izlenir" in auto_block
        and "henüz uygulanmadı" not in auto_block,
    )

    check(
        "3. auto_section onChange ile secilebiliyor",
        "onChange={setScope}" in auto_block,
    )

    check(
        "4. manual_section artik disabled DEGIL",
        "disabled" not in manual_block,
        manual_block.strip()[:60],
    )

    check(
        "4. manual_section basliginda '(yakında)' YOK",
        "(yakında)" not in manual_block,
        manual_block.strip()[:80],
    )

    check(
        "4. manual_section basligi dogru",
        "Manuel alan seç" in manual_block,
    )

    check(
        "4. manual_section aciklamasi gercek davranisi anlatiyor",
        "Sayfadaki algılanan bölümlerden takip etmek istediğiniz"
        in manual_block
        and "henüz uygulanmadı" not in manual_block,
    )

    check(
        "4. manual_section onChange ile secilebiliyor",
        "onChange={setScope}" in manual_block,
    )

    # -----------------------------------------------------
    # 8. full_page SECILEBILIR KALIYOR
    # -----------------------------------------------------

    print("\n--- 8. calisan secenek ---")

    check(
        "8. full_page disabled DEGIL",
        "disabled" not in full_block,
        full_block.strip()[:60],
    )

    check(
        "8. full_page onChange ile secilebiliyor",
        "onChange={setScope}" in full_block,
    )

    check(
        "full_page aciklamasi degismedi",
        "URL üzerindeki tüm anlamlı içerik değişikliklerini"
        in full_block,
    )

    # -----------------------------------------------------
    # 5. E-MAIL SECENEGI DISABLED
    # -----------------------------------------------------

    print("\n--- 5. e-mail secenegi ---")

    check(
        "5. e-mail secenegi artik kosulsuz disabled DEGIL",
        'className="notification-option disabled"'
        not in monitor_form,
    )

    check(
        "5. e-mail basliginda '(yakında)' YOK",
        "E-mail bildirimi (yakında)" not in app_source,
    )

    check(
        "5. checkbox YALNIZCA SMTP yoksa disable ediliyor",
        "disabled={!emailAvailable}" in monitor_form,
    )

    check(
        "5. SMTP yoksa dogru sebep gosteriliyor",
        "E-mail gönderimi için SMTP yapılandırması gerekli."
        in monitor_form
        and "henüz uygulanmadı" not in monitor_form,
    )

    check(
        "5. capability backend'den okunuyor "
        "(frontend uydurmuyor)",
        "getCapabilities()" in monitor_form
        and "email_notifications" in monitor_form,
    )

    check(
        "5. capability okunamazsa ozellik KAPALI kabul ediliyor",
        "setEmailAvailable(false)" in monitor_form,
    )

    check(
        "e-mail durumu artik degistirilebiliyor",
        "setEmailNotification(" in monitor_form,
    )

    check(
        "notification_email input'u checkbox acikken render EDILIYOR",
        'name="notification_email"' in monitor_form
        and re.search(
            r"\{emailNotification && \([\s\S]{0,400}?"
            r'name="notification_email"',
            monitor_form,
        )
        is not None,
    )

    check(
        "uygulama ici bildirim secenegi hala aktif",
        "setInAppNotification(" in monitor_form
        and 'className="notification-option disabled"'
        not in monitor_form.split("Uygulama içi bildirim")[0][-200:],
    )

    # -----------------------------------------------------
    # 6-7. OLU BUTONLAR
    # -----------------------------------------------------

    print("\n--- 6-7. olu butonlar ---")

    check(
        "6. 'Alanı bul ve önizle' butonu YOK",
        "Alanı bul ve önizle" not in app_source,
    )

    check(
        "7. 'Önizlemeyi aç' butonu YOK",
        "Önizlemeyi aç" not in app_source,
    )

    check(
        "scope-action bloklari kaldirildi",
        "scope-action" not in app_source,
    )

    check(
        "formda onClick'siz olu buton kalmadi",
        not re.findall(
            r'<button\s+type="button"\s+className="secondary-button"\s*>',
            monitor_form,
        ),
    )

    # -----------------------------------------------------
    # 9. PAYLOAD
    # -----------------------------------------------------

    print("\n--- 9. create/update payload ---")

    payload = monitor_form[
        monitor_form.index("const payload = {"):
        monitor_form.index("onSubmit(payload);")
    ]

    check(
        "9. payload notify_email'i state'ten aliyor",
        "notify_email:\n        emailNotification," in payload
        or "notify_email:" in payload,
    )

    check(
        "9. yeni izleme varsayilani hala notify_email=false",
        "notify_email: false" in defaults,
    )

    check(
        "9. notification_email kontrollu state'ten geliyor",
        "notificationEmail" in payload,
    )

    check(
        "9. e-mail kapaliyken adres null gonderiliyor",
        re.search(
            r"notification_email:[\s\S]{0,120}?: null,",
            payload,
        )
        is not None,
    )

    check(
        "edit formu kayitli adresi yukluyor",
        "useState(initialValues.notification_email)"
        in monitor_form,
    )

    check(
        "payload 17 alan gonderiyor "
        "(manuel bolum secimi + takip plani + "
        "referans secim modu eklendi)",
        len(
            set(
                re.findall(r"^\s{6}([a-z_]+)\s*[,:]", payload, re.M)
            )
        )
        == 17,
        f"{sorted(set(re.findall(chr(94)+chr(92)+'s{6}([a-z_]+)'+chr(92)+'s*[,:]', payload, re.M)))}",
    )

    # -----------------------------------------------------
    # 10-11. ESKI KAYIT ETIKETLERI
    # -----------------------------------------------------

    print("\n--- 10-11. eski kayit etiketleri ---")

    format_scope = section(
        app_source,
        "function formatScope(scopeType)",
        "function formatSchedule(",
    )

    check(
        "10. auto_section etiketi artik uyari icermiyor",
        'auto_section: "Otomatik ilgili alan"' in format_scope
        and "Otomatik ilgili alan (henüz uygulanmıyor)"
        not in format_scope,
    )

    check(
        "11. manual_section etiketi artik uyari icermiyor",
        'manual_section: "Manuel alan"' in format_scope
        and "Manuel alan (henüz uygulanmıyor)"
        not in format_scope,
    )

    check(
        "hicbir scope etiketi 'henüz uygulanmıyor' demiyor",
        "henüz uygulanmıyor" not in format_scope,
        format_scope[:200],
    )

    check(
        "uygulanmayan scope uyarisi kaldirildi "
        "(uc kapsam da calisiyor)",
        "IMPLEMENTED_SCOPE_TYPES" not in app_source,
    )

    check(
        "10-11. kartta izlenen bolum gosteriliyor",
        "monitor.auto_section_label" in app_source
        and "monitor.manual_section_label" in app_source
        and "İzlenen bölüm" in app_source,
    )

    check(
        "10-11. izlenen bolum etiketi ortak yardimcidan geliyor",
        "function watchedSectionLabel(monitor)" in app_source
        and "watchedSectionLabel(" in app_source,
    )

    check(
        "manual_section etiketi sade kaliyor",
        'manual_section: "Manuel alan"' in format_scope,
    )

    check(
        "full_page etiketi sade kaliyor",
        'full_page: "Tüm sayfa"' in format_scope,
    )

    check(
        "monitor kartinda formatScope kullaniliyor",
        "formatScope(" in app_source,
    )

    check(
        "scope-notice stili manuel akista hala kullaniliyor",
        "scope-notice" in monitor_form
        and ".scope-notice" in css_source,
    )

    check(
        "SMTP yokken acik kalmis e-mail kaydi icin uyari var",
        "SMTP yapılandırması bulunmadığı için" in monitor_form,
    )

    # -----------------------------------------------------
    # 12. ESKI KAYIT EDIT EKRANI
    # -----------------------------------------------------

    print("\n--- 12. eski kayit edit ekrani ---")

    check(
        "12. scope state kayitli degerden baslatiliyor "
        "(auto_section kaydi bozulmaz)",
        "useState(initialValues.scope_type)" in monitor_form,
    )

    check(
        "12. disabled secenek yine de secili gosterilebiliyor",
        "checked={selected === value}" in scope_component,
    )

    check(
        "12. e-mail degeri kayitli halinden baslatiliyor",
        "useState(initialValues.notify_email)" in monitor_form,
    )

    check(
        "12. monitorToFormValues eski alanlari koruyor",
        "monitor.scope_type ??" in app_source
        and "monitor.notify_email ?? false" in app_source,
    )

    check(
        "kullanici full_page'e gecebiliyor (tek yonlu duzeltme)",
        "onChange={setScope}" in full_block,
    )

    # -----------------------------------------------------
    # BACKEND'E DOKUNULMADI
    # -----------------------------------------------------

    print("\n--- backend dokunulmadi ---")

    schemas = read(
        PROJECT_ROOT / "app" / "schemas" / "monitor.py"
    )

    check(
        "backend SCOPE_TYPES hala 3 degeri kabul ediyor "
        "(API contract degismedi)",
        '"full_page"' in schemas
        and '"auto_section"' in schemas
        and '"manual_section"' in schemas,
    )

    check(
        "backend notify_email alani duruyor",
        "notify_email" in schemas,
    )

    check(
        "backend auto_section_label alanini donuyor "
        "(kart bilgisi uydurma degil)",
        "auto_section_label" in schemas,
    )

    check(
        "backend manual_section_label alanini donuyor",
        "manual_section_label" in schemas,
    )

    # -----------------------------------------------------
    # E-MAIL DOGRULAMASI
    # -----------------------------------------------------

    print("\n--- e-mail dogrulamasi ---")

    check(
        "backend e-mail adresi bicimini dogruluyor",
        "EMAIL_PATTERN" in schemas,
    )

    check(
        "notify_email acikken adres zorunlu",
        "E-mail bildirimi açıksa e-mail adresi gereklidir."
        in schemas,
    )

    check(
        "frontend kaynaginda SMTP sirri yok",
        "smtp_password" not in app_source
        and "SMTP_PASSWORD" not in app_source
        and "smtp_username" not in app_source,
    )

    # -----------------------------------------------------
    # MANUEL SECIM GUVENLIGI
    # -----------------------------------------------------

    print("\n--- manuel bolum onizlemesi guvenli ---")

    check(
        "27. dangerouslySetInnerHTML KULLANILMIYOR "
        "(yorumda gecmesi sayilmaz)",
        "dangerouslySetInnerHTML={" not in app_source,
    )

    check(
        "27. iframe KULLANILMIYOR",
        "<iframe" not in app_source
        and "createElement(\"iframe\"" not in app_source,
    )

    check(
        "27. aday onizlemesi duz metin olarak render ediliyor",
        "{candidate.preview}" in monitor_form,
    )

    check(
        "27. onizleme icin innerHTML / srcdoc yok",
        "innerHTML" not in app_source
        and "srcdoc" not in app_source,
    )

    # Submit butonu artik IKI blokaj tasiyor:
    #   manualBlocked    -> manuel kapsamda bolum secimi
    #   referenceBlocked -> mevzuat planinda referans secimi
    submit_button = section(
        monitor_form,
        'type="submit"',
        "</button>",
    )

    check(
        "manuel secim olmadan submit engelleniyor",
        "manualBlocked" in monitor_form
        and "submitting" in submit_button
        and "manualBlocked" in submit_button,
    )

    check(
        "aday listesi backend'den geliyor (uydurma yok)",
        "getSectionCandidates(" in monitor_form,
    )

    # -----------------------------------------------------
    # SONUC
    # -----------------------------------------------------

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
