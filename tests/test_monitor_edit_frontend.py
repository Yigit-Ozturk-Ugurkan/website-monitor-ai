"""
Aktif/Pasif ve Duzenle ekrani frontend dogrulamasi.

NOT: Projede bir JS test kosucusu (vitest/jest) kurulu degil ve
bu sprintte yeni paket kurulmuyor. Bu yuzden asagidakiler
CALISMA ZAMANI component testi degil, KAYNAK KODU uzerinde
yapilan statik dogrulamalardir.

Calisma zamani dogrulamasi icin `npm run build` ve tarayici
kullanilir.

Calistirma:
    python -m tests.test_monitor_edit_frontend
"""

import io
import re

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]

APP_JSX = PROJECT_ROOT / "frontend" / "src" / "App.jsx"
API_JS = PROJECT_ROOT / "frontend" / "src" / "services" / "api.js"
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


def main() -> None:

    print("=" * 70)
    print("AKTIF/PASIF + DUZENLE FRONTEND DOGRULAMASI")
    print("(statik kaynak kodu kontrolu)")
    print("=" * 70)

    app_source = read(APP_JSX)
    api_source = read(API_JS)
    css_source = read(INDEX_CSS)

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

    new_page = section(
        app_source,
        "function NewMonitorPage()",
        "function MonitorEditPage()",
    )

    edit_page = section(
        app_source,
        "function MonitorEditPage()",
        "function NotificationsPage(",
    )

    # -----------------------------------------------------
    # API CLIENT
    # -----------------------------------------------------

    print("\n--- api client ---")

    check(
        "updateMonitor tanimli",
        "export async function updateMonitor(monitorId, payload)"
        in api_source,
    )

    check(
        "updateMonitor PATCH kullaniyor",
        re.search(
            r"updateMonitor[\s\S]{0,300}method:\s*\"PATCH\"",
            api_source,
        )
        is not None,
    )

    check(
        "updateMonitor dogru adrese gidiyor",
        "`${API_BASE_URL}/api/monitors/${monitorId}`" in api_source,
    )

    for existing in [
        "getMonitors",
        "getMonitor",
        "createMonitor",
        "runMonitor",
        "getNotifications",
    ]:
        check(
            f"mevcut {existing} bozulmadi",
            f"export async function {existing}(" in api_source,
        )

    # -----------------------------------------------------
    # AKTIF / PASIF
    # -----------------------------------------------------

    print("\n--- aktif / pasif ---")

    check(
        "DashboardPage updateMonitor cagiriyor",
        "await updateMonitor(" in dashboard,
    )

    check(
        "toggle sadece is_active gonderiyor (partial PATCH)",
        re.search(
            r"await updateMonitor\(\s*monitor\.id,\s*\{\s*is_active:\s*!monitor\.is_active,\s*\}",
            dashboard,
        )
        is not None,
    )

    check(
        "aktif monitorde 'Pasife Al' gosteriliyor",
        '"Pasife Al"' in dashboard,
    )

    check(
        "pasif monitorde 'Aktifleştir' gosteriliyor",
        '"Aktifleştir"' in dashboard,
    )

    check(
        "buton etiketi is_active degerine gore secilir",
        re.search(
            r"monitor\.is_active\s*\n?\s*\?\s*\"Pasife Al\"\s*\n?\s*:\s*\"Aktifleştir\"",
            dashboard,
        )
        is not None,
    )

    check(
        "toggle loading state var",
        "togglingMonitorId" in dashboard
        and '"Güncelleniyor..."' in dashboard,
    )

    check(
        "toggle sirasinda tekrar tetikleme engelleniyor",
        "if (togglingMonitorId !== null) {" in dashboard
        and "disabled={\n                        togglingMonitorId !== null\n                      }"
        in dashboard,
    )

    check(
        "basarili toggle sonrasi liste yeniden cekiliyor",
        re.search(
            r"await updateMonitor\([\s\S]{0,200}await refreshMonitors\(\)",
            dashboard,
        )
        is not None,
    )

    check(
        "refreshMonitors backend'den GET yapiyor",
        "await getMonitors()" in dashboard,
    )

    check(
        "toggle hatasi kullaniciya gosteriliyor",
        "toggleErrors" in dashboard
        and "{toggleErrors[monitor.id]}" in dashboard,
    )

    check(
        "pasif monitorde 'Şimdi Kontrol Et' disabled",
        "!monitor.is_active ||" in dashboard,
    )

    check(
        "pasif kart gorsel olarak ayirt ediliyor",
        "monitor-passive" in dashboard
        and ".monitor-passive" in css_source,
    )

    check(
        "kartta mevcut Durum alani korunmus",
        '"Aktif"' in dashboard and '"Pasif"' in dashboard,
    )

    # -----------------------------------------------------
    # DUZENLE
    # -----------------------------------------------------

    print("\n--- duzenle ---")

    check(
        "kartta Düzenle aksiyonu var",
        '"Düzenle"' in dashboard or ">\n                      Düzenle" in dashboard,
    )

    check(
        "Düzenle dogru route'a gidiyor",
        "`/monitors/${monitor.id}/edit`" in dashboard,
    )

    check(
        "edit route tanimli",
        'path="/monitors/:monitorId/edit"' in app_source,
    )

    check(
        "MonitorEditPage route'a bagli",
        "<MonitorEditPage />" in app_source,
    )

    check(
        "useParams ile monitorId aliniyor",
        "const { monitorId } = useParams();" in edit_page,
    )

    check(
        "edit ekrani GET /api/monitors/{id} cagiriyor",
        "await getMonitor(" in edit_page,
    )

    check(
        "edit ekrani loading state gosteriyor",
        "İzleme yükleniyor..." in edit_page,
    )

    check(
        "edit ekrani yukleme hatasini gosteriyor",
        "İzleme yüklenemedi" in edit_page,
    )

    check(
        "kayit yuklenmeden form render edilmiyor",
        "!loading && !loadError && monitor && (" in edit_page,
    )

    check(
        "edit ekrani kaydederken PATCH cagiriyor",
        "await updateMonitor(" in edit_page,
    )

    check(
        "basari sonrasi /monitors sayfasina donuyor",
        re.search(
            r"await updateMonitor\([\s\S]{0,200}navigate\(\s*\"/monitors\"",
            edit_page,
        )
        is not None,
    )

    check(
        "PATCH hatasi kullaniciya gosteriliyor",
        "setSubmitError(" in edit_page
        and 'errorTitle="İzleme güncellenemedi"' in edit_page,
    )

    check(
        "kaydederken buton disabled (submitting)",
        "submitting={submitting}" in edit_page,
    )

    # -----------------------------------------------------
    # FORM YENIDEN KULLANIMI
    # -----------------------------------------------------

    print("\n--- form yeniden kullanimi ---")

    check(
        "ortak MonitorForm component'i var",
        "function MonitorForm({" in app_source,
    )

    check(
        "yeni izleme ekrani ayni formu kullaniyor",
        "<MonitorForm" in new_page,
    )

    check(
        "duzenleme ekrani ayni formu kullaniyor",
        "<MonitorForm" in edit_page,
    )

    check(
        "mevcut degerler forma monitorToFormValues ile aktariliyor",
        "function monitorToFormValues(monitor)" in app_source
        and "monitorToFormValues(" in edit_page,
    )

    for field in [
        "name",
        "url",
        "topic",
        "scope_type",
        "schedule_type",
        "schedule_time",
        "start_date",
        "end_date",
        "is_unlimited",
        "notify_in_app",
        "notify_email",
        "notification_email",
        "manual_section_reference",
        "manual_section_label",
    ]:
        check(
            f"form alani mevcut degerle doluyor: {field}",
            f"initialValues.{field}" in monitor_form,
        )

    check(
        "yeni izleme varsayilanlari MVP'de calisan secenekler",
        "const NEW_MONITOR_DEFAULTS" in app_source
        and 'scope_type: "full_page"' in app_source
        and "notify_email: false" in app_source
        and 'schedule_type: "daily"' in app_source
        and 'schedule_time: "09:00"' in app_source,
    )

    # 7 isimli input + kontrollu state.
    # notification_email input'u YALNIZCA e-mail bildirimi
    # acikken render edilir.
    #
    # manual_candidate, manuel bolum secimi radio grubudur;
    # degeri FormData'dan degil secim state'inden okunur.
    named_inputs = set(
        re.findall(r'name="([a-z_]+)"', monitor_form)
    )

    expected_inputs = {
        "name",
        "url",
        "topic",
        "schedule_type",
        "schedule_time",
        "start_date",
        "end_date",
        "manual_candidate",
        # E-mail bildirimi acikken render edilir.
        # Degeri FormData'dan degil kontrollu state'ten okunur.
        "notification_email",
        # Referans secim modu radio grubu.
        # Degeri FormData'dan degil kontrollu state'ten okunur.
        "reference_selection_mode",
    }

    check(
        "formda yeni input alani eklenmemis",
        named_inputs == expected_inputs,
        f"fazla={named_inputs - expected_inputs} "
        f"eksik={expected_inputs - named_inputs}",
    )

    payload_fields = set(
        re.findall(
            r"^\s{6}([a-z_]+)\s*[,:]",
            monitor_form[
                monitor_form.index("const payload = {"):
                monitor_form.index("onSubmit(payload);")
            ],
            re.MULTILINE,
        )
    )

    check(
        "PATCH/POST govdesi tam olarak 17 alan iceriyor",
        payload_fields
        == {
            "name",
            "url",
            "topic",
            # Takip plani ve referans yonetmelik
            # (regulation intelligence sprint 1).
            "tracking_plan",
            "reference_regulation_id",
            # Referans secim modu (automatic | manual).
            "reference_selection_mode",
            "scope_type",
            "schedule_type",
            "schedule_time",
            "start_date",
            "end_date",
            "is_unlimited",
            "notify_in_app",
            "notify_email",
            "notification_email",
            "manual_section_reference",
            "manual_section_label",
        },
        f"alanlar={sorted(payload_fields)}",
    )

    check(
        "manuel kapsam disinda secim null gonderiliyor",
        "isManualScope" in monitor_form
        and ": null," in monitor_form,
    )

    check(
        "govde is_active gondermiyor (toggle ayri)",
        "is_active" not in payload_fields,
    )

    check(
        "kontrol saati alani required (create + edit ortak formu)",
        re.search(
            r'name="schedule_time"[\s\S]{0,200}?required',
            monitor_form,
        )
        is not None,
    )

    check(
        "bos saat null yerine bos string olarak gonderiliyor",
        re.search(
            r"schedule_time:[\s\S]{0,20}?String\(",
            monitor_form,
        )
        is not None,
    )

    check(
        "scope secimi mevcut ScopeOption ile yapiliyor",
        "<ScopeOption" in monitor_form,
    )

    check(
        "mevcut button class'lari kullaniliyor",
        "secondary-button" in dashboard
        and "primary-button" in monitor_form,
    )

    # -----------------------------------------------------
    # KAPSAM DISI KONTROLU
    # -----------------------------------------------------

    print("\n--- kapsam disi kontrolu ---")

    check(
        "silme aksiyonu eklenmemis",
        "deleteMonitor" not in app_source
        and "deleteMonitor" not in api_source,
    )

    check(
        "DELETE cagrisi yok",
        '"DELETE"' not in api_source,
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
