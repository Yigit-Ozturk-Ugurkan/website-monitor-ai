import {
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";

import {
  ArrowLeft,
  Bell,
  BookText,
  CalendarDays,
  Check,
  Clock3,
  ExternalLink,
  Globe2,
  LayoutDashboard,
  Mail,
  Minus,
  Monitor,
  Pencil,
  Plus,
  Power,
  PowerOff,
  Radar,
  RefreshCw,
  Scale,
  ScanSearch,
  Sparkles,
  SquareMousePointer,
} from "lucide-react";

import {
  NavLink,
  Navigate,
  Route,
  Routes,
  useLocation,
  useNavigate,
  useParams,
} from "react-router-dom";

import {
  createMonitor,
  createRegulationReference,
  getCapabilities,
  getMonitor,
  getMonitors,
  getNotificationDetail,
  getNotifications,
  getRegulationReferences,
  getSectionCandidates,
  matchRegulationReference,
  getUnreadNotificationCount,
  markNotificationAsRead,
  runMonitor,
  updateMonitor,
} from "./services/api";


// ---------------------------------------------------------
// TAKIP PLANI
//
// website_monitor    : mevcut davranis, degismedi.
// regulation_monitor : ayrica bir referans yonetmelik tasir.
//
// Mevzuat KARSILASTIRMASI bu surumde YOK. Bu yuzden
// arayuzde hicbir yerde "karsilastirma hazir" izlenimi
// verilmez; calistirma sonucu backend'in bildirdigi
// regulation_comparison_status degerine gore gosterilir.
// ---------------------------------------------------------

const TRACKING_PLAN_WEBSITE = "website_monitor";
const TRACKING_PLAN_REGULATION = "regulation_monitor";


function formatTrackingPlan(trackingPlan) {
  const labels = {
    [TRACKING_PLAN_WEBSITE]: "Standart web sitesi",
    [TRACKING_PLAN_REGULATION]: "Mevzuat / Yönetmelik",
  };

  // Takip plani bilinmiyorsa (cok eski kayit) mevcut
  // davranis olan standart takip gosterilir.
  return (
    labels[trackingPlan] ??
    labels[TRACKING_PLAN_WEBSITE]
  );
}


// Sistem metinsel degisikligi gosterir; kesin hukuki
// tavsiye veya baglayici hukuki yorum URETMEZ.
// ---------------------------------------------------------
// REFERANS SURUM DURUMU
//
// Kullanicinin bilmesi gereken tek soru:
// bu referans GUNCEL KONSOLIDE mevzuat mi, yoksa
// yayimlandigi andaki TARIHSEL metin mi?
//
// Konsolide olmayan bir metinle yapilan karsilastirma
// aradaki degisiklikleri gormemis olabilir; bu yuzden
// durum gizlenmez.
// ---------------------------------------------------------

function formatReferenceStatus(reference) {
  if (!reference) {
    return null;
  }

  return reference.is_consolidated
    ? "Güncel konsolide metin"
    : "Orijinal / tarihsel metin";
}


function formatVersionDate(value) {
  if (!value) {
    return "";
  }

  const parsed = parseServerDate(value);

  if (!parsed || Number.isNaN(parsed.getTime())) {
    return "";
  }

  return parsed.toLocaleDateString("tr-TR", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
  });
}


// Karsilastirma konsolide OLMAYAN metne dayandiginda
// gosterilen uyari. Sahte kesinlik verilmez.
const NON_CONSOLIDATED_WARNING =
  "Karşılaştırma konsolide olmayan referans metin " +
  "üzerinden yapılmıştır. Referans, yayımlandığı " +
  "tarihteki hâlini gösteriyor olabilir ve sonraki " +
  "değişiklikleri içermeyebilir.";


// Otomatik eslestirme, kullanicinin sectiginden FARKLI bir
// referans onerdiginde gosterilir.
//
// Karsilastirma YINE kullanicinin secimiyle yapilir;
// bu yalnizca gozden gecirme cagrisidir.
const NEEDS_REVIEW_WARNING =
  "Otomatik eşleştirme, bu değişikliğin farklı bir " +
  "referans yönetmeliği hedeflediğini düşünüyor. " +
  "Karşılaştırma sizin seçtiğiniz referansla yapıldı; " +
  "referans seçimini gözden geçirin.";


// Eski kayitlarda referansin turu bilinmiyor olabilir.
const UNKNOWN_CONSOLIDATION_WARNING =
  "Bu karşılaştırmanın hangi referans metin sürümüne " +
  "dayandığı kayıtlı değil.";


const COMPARISON_DISCLAIMER =
  "AI tarafından oluşturulan karşılaştırmadır. " +
  "Resmî metin esas alınmalıdır.";


// ---------------------------------------------------------
// REFERANS SECIM MODU
//
// automatic : referans, Resmî Gazete degisikliginden
//             OTOMATIK belirlenir. Kullanicidan onay
//             beklenmez.
// manual    : referansi kullanici secer ve bu secim
//             otomatik eslestirmeyle degistirilmez.
// ---------------------------------------------------------

const REFERENCE_MODE_AUTOMATIC = "automatic";
const REFERENCE_MODE_MANUAL = "manual";


// Eslestirmenin nasil yapildigi.
const MATCH_METHOD_LABELS = {
  manual: "Manuel seçim",
  automatic_exact: "Otomatik — birebir başlık",
  automatic_base: "Otomatik — başlık eşleşmesi",
  automatic_ai: "Otomatik — yapay zeka ayrımı",

  // Kayitli mevzuatlar arasinda bulunamayip Mevzuat Bilgi
  // Sistemi'nde kesfedilen referanslar.
  automatic_discovered_exact:
    "Mevzuat Bilgi Sistemi — birebir başlık",
  automatic_discovered_title:
    "Mevzuat Bilgi Sistemi — başlık eşleşmesi",
  automatic_discovered_meta:
    "Mevzuat Bilgi Sistemi — yayım bilgisi ayrımı",
  automatic_discovered_ai:
    "Mevzuat Bilgi Sistemi — yapay zeka ayrımı",
};


function formatMatchMethod(method) {
  return MATCH_METHOD_LABELS[method] ?? null;
}


function formatConfidencePercent(confidence) {
  if (
    confidence === null ||
    confidence === undefined
  ) {
    return null;
  }

  return `%${Math.round(confidence * 100)}`;
}


// Otomatik eslestirme guvenilir sonuc veremediginde
// karsilastirma CALISTIRILMAZ. Kullanici nedenini gorur.
const MATCH_STATUS_MESSAGES = {
  no_match:
    "İlgili mevzuat değişikliği tespit edildi ancak " +
    "kayıtlı referans mevzuat otomatik olarak bulunamadı.",

  ambiguous:
    "Birden fazla olası referans bulundu. Manuel " +
    "doğrulama gerekiyor.",

  needs_review:
    "Birden fazla olası referans bulundu. Manuel " +
    "doğrulama gerekiyor.",

  no_base_name:
    "Değişiklik metninden hedef mevzuat adı " +
    "çıkarılamadı; otomatik referans belirlenemedi.",

  no_references:
    "Sistemde kayıtlı referans mevzuat bulunmadığı için " +
    "otomatik eşleştirme yapılamadı.",
};


function formatMatchStatus(status) {
  return MATCH_STATUS_MESSAGES[status] ?? "";
}


// ---------------------------------------------------------
// OTOMATIK REFERANS KESFI
//
// Kayitli mevzuatlar arasinda guvenilir referans
// bulunamadiginda, mevzuat RESMI kaynakta
// (Mevzuat Bilgi Sistemi) aranir.
//
// Kesif basarisiz olsa bile degisiklik bildirimi URETILIR;
// yalnizca referans metnin neden alinamadigi soylenir.
// Sahte referans veya sahte karsilastirma gosterilmez.
// ---------------------------------------------------------

const DISCOVERY_STATUS_MESSAGES = {
  no_match:
    "İlgili mevzuat değişikliği tespit edildi ancak " +
    "Mevzuat Bilgi Sistemi'nde güvenilir bir referans " +
    "metin otomatik olarak bulunamadı.",

  ambiguous:
    "Birden fazla olası resmî mevzuat kaydı bulundu. " +
    "Manuel doğrulama gerekiyor.",

  discovery_failed:
    "İlgili mevzuat tespit edildi ancak resmî referans " +
    "metin otomatik olarak alınamadı.",
};


function formatDiscoveryStatus(status) {
  // Tanimadigimiz veya kesfin calismadigi durumlar icin
  // uydurma aciklama uretilmez.
  return DISCOVERY_STATUS_MESSAGES[status] ?? "";
}


// Referans metni dogrulanamadi.
const STALE_REFERENCE_WARNING =
  "Referans metin bu çalıştırmada doğrulanamadı; " +
  "önbellekteki sürüm kullanıldı. Referans, kaynaktaki " +
  "en güncel hâli olmayabilir.";


const COMPARISON_STATUS_COMPLETED = "completed";
const COMPARISON_STATUS_NO_MATCH = "no_match";
const COMPARISON_STATUS_FAILED = "failed";


// Backend'in bildirdigi mevzuat karsilastirma durumlari.
//
// Ilk uc deger veritabanina yazilan GERCEK sonuclardir.
// skipped_* degerleri karsilastirmanin NEDEN hic
// calismadigini soyler ve saklanmaz.
const COMPARISON_STATUS_MESSAGES = {
  completed:
    "Mevzuat karşılaştırması tamamlandı. Ayrıntı için " +
    "bildirim detayına bakın.",

  no_match:
    "İlgili mevzuat değişikliği tespit edildi ancak " +
    "referans yönetmelikte güvenilir bir madde eşleşmesi " +
    "bulunamadı.",

  failed:
    "Mevzuat karşılaştırması tamamlanamadı.",

  skipped_first_run:
    "İlk kontrol yapıldı. Karşılaştırma için önceki bir " +
    "kayıt bulunmadığından mevzuat karşılaştırması " +
    "çalıştırılmadı.",

  skipped_no_change:
    "Sayfada değişiklik bulunmadığı için mevzuat " +
    "karşılaştırması çalıştırılmadı.",

  skipped_irrelevant:
    "Bulunan değişiklik takip konusuyla ilgili " +
    "görülmediği için mevzuat karşılaştırması " +
    "çalıştırılmadı.",

  skipped_no_reference:
    "Bu izlemeye bağlı bir referans yönetmelik " +
    "bulunmadığı için karşılaştırma çalıştırılmadı.",
};


function formatComparisonStatus(status) {
  // Tanimadigimiz bir durum icin uydurma aciklama
  // uretilmez.
  return COMPARISON_STATUS_MESSAGES[status] ?? "";
}


// Uc kapsam da pipeline'da gercekten uygulaniyor.
// Hicbir secenek "kayitli ama calismiyor" durumunda degil.
function formatScope(scopeType) {
  const labels = {
    full_page: "Tüm sayfa",
    auto_section: "Otomatik ilgili alan",
    manual_section: "Manuel alan",
  };

  return labels[scopeType] ?? scopeType;
}


// Bir monitorde izlenen bolumun adi.
// full_page'de bolum kavrami yoktur, null doner.
function watchedSectionLabel(monitor) {
  if (monitor.scope_type === "auto_section") {
    return (
      monitor.auto_section_label ||
      "İlk kontrolde belirlenecek"
    );
  }

  if (monitor.scope_type === "manual_section") {
    return monitor.manual_section_label || "—";
  }

  return null;
}


function formatCharacterCount(value) {
  return `${Number(value ?? 0).toLocaleString("tr-TR")} karakter`;
}


function formatSchedule(scheduleType) {
  const labels = {
    daily: "Her gün",
    weekdays: "Hafta içi",
    weekly: "Haftada bir",
  };

  return labels[scheduleType] ?? scheduleType;
}


function parseServerDate(value) {
  if (!value) {
    return null;
  }

  // Backend timestamp'leri datetime.utcnow() ile
  // saat dilimi bilgisi olmadan yaziliyor.
  // Tarayicinin bunlari yerel saat sanmamasi icin
  // UTC oldugu acikca belirtilir.
  const hasTimezone =
    /[Zz]$|[+-]\d{2}:\d{2}$/.test(value);

  const parsed = new Date(
    hasTimezone ? value : `${value}Z`
  );

  return Number.isNaN(parsed.getTime())
    ? null
    : parsed;
}


function formatDateTime(value) {
  const parsed = parseServerDate(value);

  return parsed
    ? parsed.toLocaleString("tr-TR")
    : "-";
}


function formatImportance(importance) {
  const labels = {
    low: "Düşük",
    medium: "Orta",
    high: "Yüksek",
  };

  return labels[importance] ?? importance;
}


// ---------------------------------------------------------
// TEXT FRAGMENT
//
// Tarayicinin native "scroll to text" ozelligi icin
// https://site/sayfa#:~:text=... bicimi uretilir.
// Eslesme tarayici ve site tarafindan garanti edilmez;
// eslesmezse link normal sayfayi acar.
// ---------------------------------------------------------

const FRAGMENT_MIN_LENGTH = 15;
const FRAGMENT_MAX_LENGTH = 140;


function pickFragmentText(addedContent) {
  if (!addedContent) {
    return "";
  }

  // Ayrac ve cok kisa satirlar ("|", "(", ")") anlamli
  // bir eslesme uretmez, atlanir.
  const candidate = addedContent
    .split(/\r?\n/)
    .map((line) => line.trim())
    .find(
      (line) =>
        line.length >= FRAGMENT_MIN_LENGTH &&
        /[\p{L}]{3}/u.test(line)
    );

  if (!candidate) {
    return "";
  }

  if (candidate.length <= FRAGMENT_MAX_LENGTH) {
    return candidate;
  }

  // Cok uzun bir diff'in tamamini URL'ye koyma.
  // Kelime ortasindan kesmemek icin son bosluktan boluyoruz.
  const truncated = candidate.slice(
    0,
    FRAGMENT_MAX_LENGTH
  );

  const lastSpace = truncated.lastIndexOf(" ");

  return lastSpace > FRAGMENT_MIN_LENGTH
    ? truncated.slice(0, lastSpace)
    : truncated;
}


function encodeFragmentText(text) {
  // Text Fragment sozdiziminde "-" aralik ayiricisidir ve
  // encodeURIComponent bunu kodlamaz.
  return encodeURIComponent(text).replace(
    /-/g,
    "%2D"
  );
}


function resolveFragmentText(
  addedContent,
  evidenceText,
  evidenceType
) {
  // 1. Oncelik: Gemini'nin dogruladigi kaynak kanit.
  //    Bildirime sebep olan degisiklik budur.
  if (evidenceType === "removed") {
    // Silinen metin canli sayfada bulunmaz,
    // fragment uretmenin anlami yok.
    return "";
  }

  if (evidenceType === "added" && evidenceText) {
    const cleaned = evidenceText.trim();

    // Bosluktan ibaret evidence gecerli sayilmaz,
    // asagidaki heuristic'e dusulur.
    if (cleaned) {
      return cleaned;
    }
  }

  // 2. Fallback: evidence yoksa (eski kayitlar)
  //    mevcut added_content sezgisi kullanilir.
  return pickFragmentText(addedContent);
}


function buildLivePageUrl(
  url,
  addedContent,
  evidenceText,
  evidenceType
) {
  if (!url) {
    return "";
  }

  const fragmentText = resolveFragmentText(
    addedContent,
    evidenceText,
    evidenceType
  );

  if (!fragmentText) {
    return url;
  }

  const hashIndex = url.indexOf("#");

  const base =
    hashIndex === -1
      ? url
      : url.slice(0, hashIndex);

  const existingHash =
    hashIndex === -1
      ? ""
      : url.slice(hashIndex + 1);

  // Adres zaten bir text directive iceriyorsa dokunma.
  if (existingHash.includes(":~:")) {
    return url;
  }

  return `${base}#${existingHash}:~:text=${encodeFragmentText(
    fragmentText
  )}`;
}


function formatRunMessage(result) {
  if (result.first_run) {
    return "İlk snapshot oluşturuldu.";
  }

  if (!result.changed) {
    return "Yeni değişiklik bulunamadı.";
  }

  if (result.ai_relevant === false) {
    return (
      "Değişiklik bulundu ancak takip konusuyla ilgili değil."
    );
  }

  if (result.ai_summary) {
    return result.ai_summary;
  }

  return (
    "Değişiklik bulundu. Yapay zeka analizi tamamlanamadı."
  );
}


function DashboardPage({ onRunCompleted }) {
  const [monitors, setMonitors] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const [
    runningMonitorId,
    setRunningMonitorId,
  ] = useState(null);

  const [runResults, setRunResults] = useState({});
  const [runErrors, setRunErrors] = useState({});

  const [
    togglingMonitorId,
    setTogglingMonitorId,
  ] = useState(null);

  const [toggleErrors, setToggleErrors] = useState({});


  async function refreshMonitors() {
    // Backend verisi source of truth kabul edilir.
    const data = await getMonitors();

    setMonitors(data);
  }


  async function handleRunMonitor(monitorId) {
    if (runningMonitorId !== null) {
      return;
    }

    try {
      setRunningMonitorId(monitorId);

      setRunErrors((current) => ({
        ...current,
        [monitorId]: "",
      }));

      const result = await runMonitor(
        monitorId
      );

      setRunResults((current) => ({
        ...current,
        [monitorId]: result,
      }));

      await refreshMonitors();

      // BILDIRIM HIZLI YOLU
      //
      // Yanit bildirim olusup olusmadigini zaten tasiyor;
      // rozet yoklama araligini beklemeden guncellenir.
      //
      // Sayac tazelemesi calistirma sonucunu ETKILEMEZ:
      // burada olusan bir hata yukaridaki run sonucunu
      // veya listeyi bozmamalidir.
      if (onRunCompleted) {
        try {
          onRunCompleted(result);
        } catch {
          // Rozet guncellenemezse calistirma yine de
          // basarili sayilir.
        }
      }
    } catch (requestError) {
      setRunErrors((current) => ({
        ...current,
        [monitorId]:
          requestError.message ||
          "Kontrol sırasında bir hata oluştu.",
      }));
    } finally {
      setRunningMonitorId(null);
    }
  }


  async function handleToggleActive(monitor) {
    if (togglingMonitorId !== null) {
      return;
    }

    try {
      setTogglingMonitorId(monitor.id);

      setToggleErrors((current) => ({
        ...current,
        [monitor.id]: "",
      }));

      await updateMonitor(
        monitor.id,
        {
          is_active: !monitor.is_active,
        }
      );

      // Backend verisi source of truth kabul edilir.
      await refreshMonitors();
    } catch (requestError) {
      setToggleErrors((current) => ({
        ...current,
        [monitor.id]:
          requestError.message ||
          "Durum güncellenirken bir hata oluştu.",
      }));
    } finally {
      setTogglingMonitorId(null);
    }
  }


  useEffect(() => {
    let active = true;

    async function loadMonitors() {
      try {
        setLoading(true);
        setError("");

        const data = await getMonitors();

        if (active) {
          setMonitors(data);
        }
      } catch (requestError) {
        if (active) {
          setError(
            requestError.message ||
              "İzlemeler yüklenirken bir hata oluştu."
          );
        }
      } finally {
        if (active) {
          setLoading(false);
        }
      }
    }

    loadMonitors();

    return () => {
      active = false;
    };
  }, []);


  return (
    <section>
      <div className="page-header">
        <div>
          <p className="eyebrow">
            GENEL BAKIŞ
          </p>

          <h1>
            İzlemeler
          </h1>

          <p className="page-description">
            Takip ettiğiniz web sitelerini ve son kontrolleri
            buradan yönetebilirsiniz.
          </p>
        </div>

        <NavLink
          to="/new-monitor"
          className="primary-button"
        >
          <Plus size={18} />
          Yeni İzleme
        </NavLink>
      </div>


      {loading && (
        <div className="placeholder-panel">
          İzlemeler yükleniyor...
        </div>
      )}


      {!loading && error && (
        <div className="placeholder-panel">
          <strong>
            İzlemeler yüklenemedi
          </strong>

          <p>
            {error}
          </p>
        </div>
      )}


      {!loading &&
        !error &&
        monitors.length === 0 && (
          <div className="empty-state">
            <div className="empty-icon">
              <Radar size={26} />
            </div>

            <h2>
              Henüz bir izleme oluşturulmadı
            </h2>

            <p>
              Bir web sitesi ve takip konusu belirleyerek
              ilk monitörünüzü oluşturun.
            </p>

            <NavLink
              to="/new-monitor"
              className="secondary-button"
            >
              İlk izlemeyi oluştur
            </NavLink>
          </div>
        )}


      {!loading &&
        !error &&
        monitors.length > 0 && (
          <div className="monitor-form">
            {monitors.map((monitor) => (
              <article
                key={monitor.id}
                className={`form-panel ${
                  monitor.is_active
                    ? ""
                    : "monitor-passive"
                }`}
              >
                <div className="form-panel-heading">
                  <div className="section-number">
                    <Radar size={17} />
                  </div>

                  <div>
                    <h2>
                      {monitor.name}
                    </h2>

                    <p>
                      {monitor.url}
                    </p>
                  </div>
                </div>


                <div className="form-grid three-columns">
                  <div className="form-field">
                    <label>
                      Durum
                    </label>

                    <strong>
                      {monitor.is_active
                        ? "Aktif"
                        : "Pasif"}
                    </strong>
                  </div>


                  <div className="form-field">
                    <label>
                      Takip planı
                    </label>

                    <strong>
                      {formatTrackingPlan(
                        monitor.tracking_plan
                      )}
                    </strong>
                  </div>


                  {/* Referans YALNIZCA mevzuat
                      takibinde gosterilir; standart
                      kartlarin gorunumu degismez. */}
                  {monitor.tracking_plan ===
                    TRACKING_PLAN_REGULATION &&
                    monitor.reference_regulation_title && (
                      <div className="form-field">
                        <label>
                          Referans yönetmelik
                        </label>

                        <strong>
                          {
                            monitor.reference_regulation_title
                          }
                        </strong>
                      </div>
                    )}


                  <div className="form-field">
                    <label>
                      İzleme kapsamı
                    </label>

                    <strong>
                      {formatScope(
                        monitor.scope_type
                      )}
                    </strong>
                  </div>


                  {watchedSectionLabel(
                    monitor
                  ) !== null && (
                    <div className="form-field">
                      <label>
                        İzlenen bölüm
                      </label>

                      <strong>
                        {watchedSectionLabel(
                          monitor
                        )}
                      </strong>
                    </div>
                  )}


                  <div className="form-field">
                    <label>
                      Zamanlama
                    </label>

                    <strong>
                      {formatSchedule(
                        monitor.schedule_type
                      )}

                      {monitor.schedule_time
                        ? ` · ${monitor.schedule_time}`
                        : ""}
                    </strong>
                  </div>


                  <div className="form-field full-width">
                    <label>
                      Takip konusu
                    </label>

                    <p>
                      {monitor.topic}
                    </p>
                  </div>


                  <div className="form-field">
                    <label>
                      Uygulama içi bildirim
                    </label>

                    <strong>
                      {monitor.notify_in_app
                        ? "Açık"
                        : "Kapalı"}
                    </strong>
                  </div>


                  <div className="form-field">
                    <label>
                      E-mail bildirimi
                    </label>

                    <strong>
                      {monitor.notify_email
                        ? monitor.notification_email ||
                          "Açık"
                        : "Kapalı"}
                    </strong>
                  </div>


                  <div className="form-field">
                    <label>
                      Son kontrol
                    </label>

                    <strong>
                      {monitor.last_checked_at
                        ? formatDateTime(
                            monitor.last_checked_at
                          )
                        : "Henüz kontrol edilmedi"}
                    </strong>
                  </div>


                  {/* SONUC
                      Metin ve renk backend'den gelir.
                      Burada durum adindan mesaj URETILMEZ. */}
                  {monitor.last_run_message && (
                    <div className="form-field">
                      <label>
                        Sonuç
                      </label>

                      <span
                        className={
                          "run-status " +
                          (monitor.last_run_severity ||
                            "info")
                        }
                      >
                        {monitor.last_run_message}
                      </span>

                      {/* SON DENEME
                          Yalnizca son deneme BASARISIZ
                          olduysa gosterilir: o zaman
                          "Son kontrol" eski bir
                          calistirmaya aittir.

                          Karar backend'den gelir
                          (last_run_succeeded). Burada
                          durum adi listesi TUTULMAZ ve
                          zaman damgalari
                          KARSILASTIRILMAZ: basarili bir
                          calistirmada iki damga da ayni
                          run'a aittir ama milisaniye
                          farkla yazilir. */}
                      {monitor.last_run_succeeded === false &&
                        monitor.last_run_at && (
                          <small className="run-status-time">
                            Son deneme:{" "}
                            {formatDateTime(
                              monitor.last_run_at
                            )}
                          </small>
                        )}
                    </div>
                  )}
                </div>


                <div className="monitor-run">
                  <div className="monitor-actions">
                    <button
                      type="button"
                      className="secondary-button"
                      onClick={() =>
                        handleRunMonitor(
                          monitor.id
                        )
                      }
                      disabled={
                        !monitor.is_active ||
                        runningMonitorId !== null
                      }
                      title={
                        monitor.is_active
                          ? undefined
                          : "Pasif izleme kontrol edilemez."
                      }
                    >
                      <RefreshCw size={16} />

                      {runningMonitorId === monitor.id
                        ? "Kontrol ediliyor..."
                        : "Şimdi Kontrol Et"}
                    </button>


                    <NavLink
                      to={`/monitors/${monitor.id}/edit`}
                      className="secondary-button"
                    >
                      <Pencil size={16} />
                      Düzenle
                    </NavLink>


                    <button
                      type="button"
                      className="secondary-button"
                      onClick={() =>
                        handleToggleActive(
                          monitor
                        )
                      }
                      disabled={
                        togglingMonitorId !== null
                      }
                    >
                      {monitor.is_active ? (
                        <PowerOff size={16} />
                      ) : (
                        <Power size={16} />
                      )}

                      {togglingMonitorId === monitor.id
                        ? "Güncelleniyor..."
                        : monitor.is_active
                          ? "Pasife Al"
                          : "Aktifleştir"}
                    </button>
                  </div>


                  {toggleErrors[monitor.id] && (
                    <p className="monitor-run-error">
                      {toggleErrors[monitor.id]}
                    </p>
                  )}


                  {runErrors[monitor.id] && (
                    <p className="monitor-run-error">
                      {runErrors[monitor.id]}
                    </p>
                  )}


                  {!runErrors[monitor.id] &&
                    runResults[monitor.id] && (
                      <div className="monitor-run-result">
                        <p>
                          {formatRunMessage(
                            runResults[monitor.id]
                          )}
                        </p>

                        {runResults[monitor.id]
                          .ai_error && (
                          <p className="monitor-run-warning">
                            Yapay zeka analizi tamamlanamadı:
                            {" "}
                            {
                              runResults[monitor.id]
                                .ai_error
                            }
                          </p>
                        )}

                        {runResults[monitor.id]
                          .notification_created && (
                          <p className="monitor-run-note">
                            Uygulama içi bildirim oluşturuldu.
                          </p>
                        )}

                        {/* Mevzuat karsilastirmasinin
                            GERCEK durumu. Calismadiysa
                            nedeni acikca yazilir; var
                            olmayan bir sonuc gosterilmez. */}
                        {formatComparisonStatus(
                          runResults[monitor.id]
                            .regulation_comparison_status
                        ) && (
                          <p
                            className={
                              runResults[monitor.id]
                                .regulation_comparison_status ===
                              COMPARISON_STATUS_COMPLETED
                                ? "monitor-run-note"
                                : "monitor-run-warning"
                            }
                          >
                            {formatComparisonStatus(
                              runResults[monitor.id]
                                .regulation_comparison_status
                            )}

                            {runResults[monitor.id]
                              .regulation_changes_count
                              ? ` Etkilenen madde sayısı: ${
                                  runResults[monitor.id]
                                    .regulation_changes_count
                                }.`
                              : ""}
                          </p>
                        )}
                      </div>
                    )}
                </div>
              </article>
            ))}
          </div>
        )}
    </section>
  );
}


function ScopeOption({
  value,
  selected,
  onChange,
  icon: Icon,
  title,
  description,
  disabled = false,
}) {
  return (
    <label
      className={[
        "scope-option",
        selected === value ? "selected" : "",
        disabled ? "disabled" : "",
      ]
        .filter(Boolean)
        .join(" ")}
    >
      <input
        type="radio"
        name="scope"
        value={value}
        checked={selected === value}
        disabled={disabled}
        onChange={() =>
          onChange(value)
        }
      />

      <div className="scope-icon">
        <Icon size={20} />
      </div>

      <div>
        <strong>
          {title}
        </strong>

        <p>
          {description}
        </p>
      </div>
    </label>
  );
}


// Takip plani secenegi.
//
// ScopeOption ile ayni gorsel dili kullanir ama AYRI bir
// radio grubudur (name="tracking_plan"); iki secim
// birbirini etkilemez.
function TrackingPlanOption({
  value,
  selected,
  onChange,
  icon: Icon,
  title,
  description,

  // Radio grubu adi.
  //
  // Ayni formda birden fazla secim grubu var (takip plani
  // ve referans secim modu); ayni ad kullanilirsa
  // tarayici bunlari TEK grup sayar ve secimler birbirini
  // bozar.
  name = "tracking_plan",
}) {
  return (
    <label
      className={[
        "scope-option",
        selected === value ? "selected" : "",
      ]
        .filter(Boolean)
        .join(" ")}
    >
      <input
        type="radio"
        name={name}
        value={value}
        checked={selected === value}
        onChange={() =>
          onChange(value)
        }
      />

      <div className="scope-icon">
        <Icon size={20} />
      </div>

      <div>
        <strong>
          {title}
        </strong>

        <p>
          {description}
        </p>
      </div>
    </label>
  );
}


// Yeni referans yonetmelik formu.
//
// Ayri bir sayfa veya modal kutuphanesi EKLENMEZ; form
// mevcut panel bileseninin icinde acilir. Boylece
// frontend mimarisine en az mudahale edilir.
function NewRegulationReferenceForm({
  onCreated,
  onCancel,
}) {
  const [title, setTitle] = useState("");
  const [sourceUrl, setSourceUrl] = useState("");

  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");


  async function handleCreate() {
    const cleanTitle = title.trim();
    const cleanUrl = sourceUrl.trim();

    if (!cleanTitle) {
      setError("Yönetmelik adı gereklidir.");
      return;
    }

    if (!cleanUrl) {
      setError("Kaynak URL gereklidir.");
      return;
    }

    try {
      setSaving(true);
      setError("");

      const reference = await createRegulationReference({
        title: cleanTitle,
        source_url: cleanUrl,
      });

      onCreated(reference);
    } catch (requestError) {
      // Backend'in fetch/extraction mesaji oldugu gibi
      // gosterilir; kullanici neyin basarisiz oldugunu
      // gorur.
      setError(
        requestError.message ||
          "Referans yönetmelik kaydedilemedi."
      );
    } finally {
      setSaving(false);
    }
  }


  return (
    <div className="reference-form">
      <div className="form-grid">
        <div className="form-field">
          <label htmlFor="reference-title">
            Yönetmelik adı
          </label>

          <input
            id="reference-title"
            type="text"
            placeholder="Örn. Elektrik Piyasası Lisans Yönetmeliği"
            value={title}
            onChange={(event) =>
              setTitle(event.target.value)
            }
          />
        </div>


        <div className="form-field">
          <label htmlFor="reference-url">
            Kaynak URL
          </label>

          <div className="input-with-icon">
            <Globe2 size={18} />

            <input
              id="reference-url"
              type="url"
              placeholder="https://mevzuat.gov.tr/..."
              value={sourceUrl}
              onChange={(event) =>
                setSourceUrl(event.target.value)
              }
            />
          </div>

          <span className="field-help">
            Yönetmeliğin HTML olarak yayımlandığı adres.
            Bu sürümde PDF desteklenmiyor.
          </span>
        </div>
      </div>


      <div className="reference-form-actions">
        <button
          type="button"
          className="primary-button"
          onClick={handleCreate}
          disabled={saving}
        >
          <BookText size={16} />

          {saving
            ? "Yönetmelik içeriği alınıyor..."
            : "Referansı Getir ve Kaydet"}
        </button>

        <button
          type="button"
          className="secondary-button"
          onClick={onCancel}
          disabled={saving}
        >
          Vazgeç
        </button>
      </div>


      {error && (
        <p className="scope-notice">
          {error}
        </p>
      )}
    </div>
  );
}


const NEW_MONITOR_DEFAULTS = {
  name: "",
  url: "",
  topic: "",

  // Varsayilan takip plani mevcut davranistir.
  tracking_plan: TRACKING_PLAN_WEBSITE,

  // Yeni mevzuat izlemelerinde varsayilan OTOMATIK.
  reference_selection_mode: REFERENCE_MODE_AUTOMATIC,

  reference_regulation_id: null,

  scope_type: "full_page",

  schedule_type: "daily",
  schedule_time: "09:00",

  start_date: "",
  end_date: "",
  is_unlimited: true,

  notify_in_app: true,
  notify_email: false,
  notification_email: "",

  manual_section_reference: "",
  manual_section_label: "",
};


function monitorToFormValues(monitor) {
  return {
    name: monitor.name ?? "",
    url: monitor.url ?? "",
    topic: monitor.topic ?? "",

    // Takip plani tasimayan eski kayitlar standart
    // web sitesi takibi olarak acilir.
    tracking_plan:
      monitor.tracking_plan ??
      NEW_MONITOR_DEFAULTS.tracking_plan,

    // Mevcut kayitlar SESSIZCE otomatige gecirilmez:
    // referansini kullanici secmisti.
    reference_selection_mode:
      monitor.reference_selection_mode ??
      REFERENCE_MODE_MANUAL,

    reference_regulation_id:
      monitor.reference_regulation_id ?? null,

    scope_type:
      monitor.scope_type ??
      NEW_MONITOR_DEFAULTS.scope_type,

    schedule_type:
      monitor.schedule_type ??
      NEW_MONITOR_DEFAULTS.schedule_type,

    schedule_time: monitor.schedule_time ?? "",

    start_date: monitor.start_date ?? "",
    end_date: monitor.end_date ?? "",
    is_unlimited: monitor.is_unlimited ?? true,

    notify_in_app: monitor.notify_in_app ?? true,
    notify_email: monitor.notify_email ?? false,
    notification_email: monitor.notification_email ?? "",

    manual_section_reference:
      monitor.manual_section_reference ?? "",
    manual_section_label:
      monitor.manual_section_label ?? "",
  };
}


// Yeni izleme ve izleme düzenleme ekranları
// aynı formu paylaşır.
function MonitorForm({
  initialValues,
  submitting,
  submitError,
  errorTitle,
  submitLabel,
  onSubmit,
}) {
  const [scope, setScope] =
    useState(initialValues.scope_type);

  // Takip plani.
  const [trackingPlan, setTrackingPlan] =
    useState(initialValues.tracking_plan);

  // Referans secim modu.
  const [
    referenceSelectionMode,
    setReferenceSelectionMode,
  ] = useState(
    initialValues.reference_selection_mode
  );

  // Secili referans yonetmelik.
  // Kontrollu state'te tutulur; FormData'dan okunmaz.
  const [
    referenceRegulationId,
    setReferenceRegulationId,
  ] = useState(
    initialValues.reference_regulation_id
  );

  const [references, setReferences] = useState([]);

  const [
    referencesLoading,
    setReferencesLoading,
  ] = useState(false);

  const [
    referencesError,
    setReferencesError,
  ] = useState("");

  // Yeni referans formu acik mi?
  const [
    referenceFormOpen,
    setReferenceFormOpen,
  ] = useState(false);

  // Kayit sonrasi tek seferlik basari mesaji.
  const [
    referenceCreatedMessage,
    setReferenceCreatedMessage,
  ] = useState("");

  const [referenceError, setReferenceError] =
    useState("");

  // OTOMATIK REFERANS ONERISI
  //
  // Kullanici degisiklik metnini yapistirip "hangi
  // referansi secmeliyim?" diye sorabilir. Oneri
  // SECIMI KENDILIGINDEN DEGISTIRMEZ; kullanici
  // uygulamak isterse butona basar.
  const [suggestOpen, setSuggestOpen] = useState(false);

  const [suggestText, setSuggestText] = useState("");

  const [suggestLoading, setSuggestLoading] =
    useState(false);

  const [suggestResult, setSuggestResult] =
    useState(null);

  const [suggestError, setSuggestError] = useState("");


  async function handleSuggestReference() {
    const cleaned = suggestText.trim();

    if (!cleaned) {
      setSuggestError(
        "Öneri için değişiklik metnini yapıştırın."
      );
      return;
    }

    try {
      setSuggestLoading(true);
      setSuggestError("");
      setSuggestResult(null);

      const data = await matchRegulationReference({
        source_text: cleaned,
        allow_ai: true,
      });

      setSuggestResult(data);
    } catch (requestError) {
      setSuggestError(
        requestError.message ||
          "Referans önerisi alınamadı."
      );
    } finally {
      setSuggestLoading(false);
    }
  }

  // Website URL kontrollu tutulur.
  // Manuel bolum secimi BU URL'ye baglidir; URL degisince
  // eski secim gecersiz sayilmalidir.
  const [url, setUrl] =
    useState(initialValues.url);

  // Kaydedilmis manuel secim ve ait oldugu URL.
  const [manualSelection, setManualSelection] =
    useState({
      reference: initialValues.manual_section_reference,
      label: initialValues.manual_section_label,
      url: initialValues.url,
    });

  // Getirilen aday bolumler ve ait olduklari URL.
  const [candidates, setCandidates] =
    useState({
      url: "",
      pageTitle: "",
      items: [],
      loaded: false,
    });

  const [
    candidatesLoading,
    setCandidatesLoading,
  ] = useState(false);

  const [
    candidatesError,
    setCandidatesError,
  ] = useState("");

  const [selectionError, setSelectionError] =
    useState("");

  const [unlimited, setUnlimited] =
    useState(initialValues.is_unlimited);

  const [
    inAppNotification,
    setInAppNotification,
  ] = useState(initialValues.notify_in_app);

  const [
    emailNotification,
    setEmailNotification,
  ] = useState(initialValues.notify_email);

  const [notificationEmail, setNotificationEmail] =
    useState(initialValues.notification_email);

  // E-mail gonderimi deployment'ta SMTP yapilandirmasina
  // baglidir. Ozellik uygulanmis olsa bile SMTP yoksa
  // secenek durustce devre disi gosterilir.
  const [
    emailAvailable,
    setEmailAvailable,
  ] = useState(false);

  const [
    capabilitiesLoaded,
    setCapabilitiesLoaded,
  ] = useState(false);


  useEffect(() => {
    let active = true;

    async function loadCapabilities() {
      try {
        const data = await getCapabilities();

        if (active) {
          setEmailAvailable(
            Boolean(data.email_notifications)
          );
        }
      } catch {
        // Capability okunamazsa ozellik KAPALI kabul edilir.
        // Calismayan bir secenegi acik gostermek yaniltici olur.
        if (active) {
          setEmailAvailable(false);
        }
      } finally {
        if (active) {
          setCapabilitiesLoaded(true);
        }
      }
    }

    loadCapabilities();

    return () => {
      active = false;
    };
  }, []);


  const isRegulationPlan =
    trackingPlan === TRACKING_PLAN_REGULATION;


  // Referans listesi YALNIZCA mevzuat planinda cekilir.
  // Standart izleme olusturan kullanici bu istegi hic
  // yapmaz.
  //
  // Bu istek Gemini CALISTIRMAZ.
  useEffect(() => {
    if (!isRegulationPlan) {
      return undefined;
    }

    let active = true;

    async function loadReferences() {
      try {
        setReferencesLoading(true);
        setReferencesError("");

        const data = await getRegulationReferences();

        if (active) {
          setReferences(data);
        }
      } catch (requestError) {
        if (active) {
          setReferences([]);

          setReferencesError(
            requestError.message ||
              "Referans yönetmelikler yüklenirken bir hata oluştu."
          );
        }
      } finally {
        if (active) {
          setReferencesLoading(false);
        }
      }
    }

    loadReferences();

    return () => {
      active = false;
    };
  }, [isRegulationPlan]);


  function handleReferenceModeChange(value) {
    setReferenceSelectionMode(value);
    setReferenceError("");

    // Otomatige gecince eski secim asili kalmaz;
    // backend de bu kaydi temizliyor.
    if (value === REFERENCE_MODE_AUTOMATIC) {
      setReferenceRegulationId(null);
      setReferenceFormOpen(false);
      setSuggestOpen(false);
    }
  }


  function handleTrackingPlanChange(value) {
    setTrackingPlan(value);
    setReferenceError("");

    // Standart takibe donuldugunde referans secimi
    // birakilmaz; backend de bu kaydi temizliyor.
    if (value !== TRACKING_PLAN_REGULATION) {
      setReferenceRegulationId(null);
      setReferenceFormOpen(false);
      setReferenceCreatedMessage("");
    }
  }


  function handleReferenceCreated(reference) {
    // Yeni kayit listeye eklenir ve dogrudan secilir.
    setReferences((current) => {
      const others = current.filter(
        (item) => item.id !== reference.id
      );

      return [...others, reference].sort((a, b) =>
        a.title.localeCompare(b.title, "tr")
      );
    });

    setReferenceRegulationId(reference.id);
    setReferenceFormOpen(false);
    setReferenceError("");

    setReferenceCreatedMessage(
      "Referans yönetmelik kaydedildi."
    );
  }


  const isAutomaticReference =
    referenceSelectionMode === REFERENCE_MODE_AUTOMATIC;

  const referenceSelected =
    referenceRegulationId !== null &&
    referenceRegulationId !== undefined;

  // Secili referansin kaydi. Surum durumunu gostermek
  // icin kullanilir.
  const selectedReference =
    references.find(
      (reference) =>
        reference.id === referenceRegulationId
    ) ?? null;

  // Referans YALNIZCA manuel modda zorunludur.
  // Otomatik modda referans her calistirmada belirlenir.
  const referenceBlocked =
    isRegulationPlan &&
    !isAutomaticReference &&
    !referenceSelected;


  const currentUrl = url.trim();

  const isManualScope = scope === "manual_section";

  // Secim yalnizca AIT OLDUGU URL icin gecerlidir.
  // Kullanici URL'yi degistirirse secim otomatik olarak
  // gecersiz hale gelir ve yeniden secim istenir.
  const manualSelectionValid =
    Boolean(manualSelection.reference) &&
    manualSelection.url === currentUrl;

  // Eski secim duruyor ama URL degismis.
  const manualSelectionStale =
    Boolean(manualSelection.reference) &&
    manualSelection.url !== currentUrl;

  // Adaylar da yalnizca getirildikleri URL icin gecerlidir.
  const candidatesValid =
    candidates.loaded && candidates.url === currentUrl;

  const manualBlocked =
    isManualScope && !manualSelectionValid;


  function handleUrlChange(event) {
    setUrl(event.target.value);
    setSelectionError("");
  }


  function handleSelectCandidate(candidate) {
    setManualSelection({
      reference: candidate.reference,
      label: candidate.label,
      url: currentUrl,
    });

    setSelectionError("");
  }


  async function handleFetchCandidates() {
    if (!currentUrl) {
      setCandidatesError(
        "Bölümleri görmek için önce Website URL girin."
      );
      return;
    }

    try {
      setCandidatesLoading(true);
      setCandidatesError("");

      const data = await getSectionCandidates(
        currentUrl
      );

      setCandidates({
        url: currentUrl,
        pageTitle: data.page_title ?? "",
        items: data.candidates ?? [],
        loaded: true,
      });
    } catch (requestError) {
      setCandidates({
        url: "",
        pageTitle: "",
        items: [],
        loaded: false,
      });

      setCandidatesError(
        requestError.message ||
          "Bölümler getirilirken bir hata oluştu."
      );
    } finally {
      setCandidatesLoading(false);
    }
  }


  function handleSubmit(event) {
    event.preventDefault();

    const formData =
      new FormData(
        event.currentTarget
      );

    const name =
      String(
        formData.get("name") ?? ""
      ).trim();

    const topic =
      String(
        formData.get("topic") ?? ""
      ).trim();

    // Manuel kapsamda secim olmadan kayit yapilamaz.
    // Backend de ayni kurali uyguluyor; bu yalnizca
    // kullaniciya erken ve anlasilir geri bildirim.
    if (isManualScope && !manualSelectionValid) {
      setSelectionError(
        manualSelectionStale
          ? "Website URL değişti. Bölümleri yeniden getirip bir alan seçin."
          : "Devam etmek için izlemek istediğiniz bölümü seçin."
      );
      return;
    }

    // Referans YALNIZCA manuel modda zorunludur.
    // Backend de ayni kurali uyguluyor; bu yalnizca
    // kullaniciya erken ve anlasilir geri bildirim.
    if (
      isRegulationPlan &&
      !isAutomaticReference &&
      !referenceSelected
    ) {
      setReferenceError(
        "Devam etmek için bir referans yönetmelik seçin."
      );
      return;
    }

    const payload = {
      name,
      url: currentUrl,
      topic,

      tracking_plan: trackingPlan,

      reference_selection_mode: referenceSelectionMode,

      // Standart takipte referans temizlenir; baska bir
      // planda eski secim sessizce durmaz.
      //
      // Otomatik modda referans GONDERILMEZ: her
      // calistirmada degisiklik metninden belirlenir.
      reference_regulation_id:
        isRegulationPlan && !isAutomaticReference
          ? referenceRegulationId
          : null,

      scope_type: scope,

      // Manuel kapsam disinda secim temizlenir; baska bir
      // kapsamda eski referans sessizce durmaz.
      manual_section_reference:
        isManualScope
          ? manualSelection.reference
          : null,

      manual_section_label:
        isManualScope
          ? manualSelection.label
          : null,

      schedule_type:
        formData.get(
          "schedule_type"
        ) || "daily",

      schedule_time:
        String(
          formData.get(
            "schedule_time"
          ) ?? ""
        ).trim(),

      start_date:
        formData.get(
          "start_date"
        ) || null,

      end_date:
        unlimited
          ? null
          : formData.get(
              "end_date"
            ) || null,

      is_unlimited:
        unlimited,

      notify_in_app:
        inAppNotification,

      notify_email:
        emailNotification,

      // E-mail kapaliyken adres temizlenir.
      notification_email:
        emailNotification
          ? notificationEmail.trim() || null
          : null,
    };


    onSubmit(payload);
  }


  return (
      <form
        className="monitor-form"
        onSubmit={handleSubmit}
      >
        {/* TAKIP PLANI */}

        <div className="form-panel">
          <div className="form-panel-heading">
            <div className="section-number">
              1
            </div>

            <div>
              <h2>
                Takip planı
              </h2>

              <p>
                Bu izlemenin ne tür bir takip yapacağını
                seçin.
              </p>
            </div>
          </div>


          <div className="scope-grid">
            <TrackingPlanOption
              value={TRACKING_PLAN_WEBSITE}
              selected={trackingPlan}
              onChange={handleTrackingPlanChange}
              icon={Globe2}
              title="Standart Web Sitesi Takibi"
              description="Web sayfasındaki değişiklikleri takip eder."
            />

            <TrackingPlanOption
              value={TRACKING_PLAN_REGULATION}
              selected={trackingPlan}
              onChange={handleTrackingPlanChange}
              icon={Scale}
              title="Mevzuat / Yönetmelik Takibi"
              description="Yeni yayımlanan mevzuatı mevcut referans yönetmelikle karşılaştırmak için kullanılır."
            />
          </div>


          {/* REFERANS YONETMELIK
              YALNIZCA mevzuat planinda gorunur. */}

          {isRegulationPlan && (
            <div className="reference-section">
              <div className="reference-heading">
                <BookText size={18} />

                <strong>
                  Referans yönetmelik
                </strong>
              </div>


              {/* Karsilastirma GERCEKTEN calisiyor, ama
                  yalnizca konuyla ilgili bir degisiklik
                  bulundugunda. Kullanici bunu bastan
                  bilmeli. */}
              <p className="scope-notice">
                Konuyla ilgili bir değişiklik bulunduğunda,
                değişen maddeler referans yönetmelikle
                karşılaştırılır ve eski/yeni metin bildirim
                detayında gösterilir.
                {" "}
                {COMPARISON_DISCLAIMER}
              </p>


              {/* REFERANS SECIM MODU */}

              <div className="reference-mode">
                <span className="reference-mode-label">
                  Referans seçimi
                </span>

                <div className="scope-grid">
                  <TrackingPlanOption
                    value={REFERENCE_MODE_AUTOMATIC}
                    selected={referenceSelectionMode}
                    onChange={handleReferenceModeChange}
                    name="reference_selection_mode"
                    icon={ScanSearch}
                    title="Otomatik belirle"
                    description="Referans önce kayıtlı mevzuatlarda aranır; bulunamazsa Mevzuat Bilgi Sistemi'nden otomatik oluşturulur."
                  />

                  <TrackingPlanOption
                    value={REFERENCE_MODE_MANUAL}
                    selected={referenceSelectionMode}
                    onChange={handleReferenceModeChange}
                    name="reference_selection_mode"
                    icon={BookText}
                    title="Manuel seç"
                    description="Karşılaştırmada kullanılacak referans mevzuatı siz seçersiniz."
                  />
                </div>
              </div>


              {/* OTOMATIK MODDA SECICI GIZLI

                  Referans her calistirmada degisiklik
                  metninden belirlenir; kullanicidan secim
                  veya onay BEKLENMEZ. */}

              {isAutomaticReference && (
                <p className="scope-notice">
                  İlgili referans önce kayıtlı mevzuatlar
                  arasında aranır. Bulunamazsa Mevzuat
                  Bilgi Sistemi üzerinden güvenilir
                  eşleşme aranarak konsolide referans
                  otomatik oluşturulur. Güvenilir bir
                  eşleşme bulunamazsa karşılaştırma
                  çalıştırılmaz ve nedeni bildirim
                  detayında gösterilir.
                </p>
              )}


              {!isAutomaticReference && referencesLoading && (
                <p className="field-help">
                  Referans yönetmelikler yükleniyor...
                </p>
              )}


              {!isAutomaticReference && referencesError && (
                <p className="scope-notice">
                  {referencesError}
                </p>
              )}


              {!isAutomaticReference &&
                !referencesLoading &&
                !referencesError &&
                references.length === 0 &&
                !referenceFormOpen && (
                  <p className="scope-notice">
                    Henüz kayıtlı referans yönetmelik yok.
                    Devam etmek için yeni bir referans
                    ekleyin.
                  </p>
                )}


              {!isAutomaticReference &&
                !referencesLoading &&
                references.length > 0 && (
                  <div className="form-field">
                    <label htmlFor="reference-select">
                      Kayıtlı referanslar
                    </label>

                    {/* Deger kontrollu state'ten okunur;
                        bu yuzden name attribute'u yok. */}
                    <select
                      id="reference-select"
                      value={
                        referenceRegulationId ?? ""
                      }
                      onChange={(event) => {
                        const value = event.target.value;

                        setReferenceRegulationId(
                          value === ""
                            ? null
                            : Number(value)
                        );

                        setReferenceError("");
                        setReferenceCreatedMessage("");
                      }}
                    >
                      <option value="">
                        Referans yönetmelik seçin
                      </option>

                      {references.map((reference) => (
                        <option
                          key={reference.id}
                          value={reference.id}
                        >
                          {reference.title}
                          {" — "}
                          {formatReferenceStatus(reference)}
                        </option>
                      ))}
                    </select>
                  </div>
                )}


              {/* SECILI REFERANSIN SURUM DURUMU

                  Konsolide olmayan bir metinle yapilacak
                  karsilastirma aradaki degisiklikleri
                  gormemis olabilir; kullanici bunu SECIM
                  ANINDA bilmeli. */}

              {!isAutomaticReference && selectedReference && (
                <div className="reference-status">
                  <span
                    className={[
                      "reference-badge",
                      selectedReference.is_consolidated
                        ? "consolidated"
                        : "historical",
                    ].join(" ")}
                  >
                    {formatReferenceStatus(
                      selectedReference
                    )}
                  </span>

                  {selectedReference.version_date && (
                    <span className="reference-meta">
                      Son işlenen değişiklik:{" "}
                      {formatVersionDate(
                        selectedReference.version_date
                      )}
                    </span>
                  )}

                  {!selectedReference.is_consolidated && (
                    <p className="scope-notice">
                      Bu referans, yayımlandığı tarihteki
                      hâlini gösteriyor olabilir. Daha
                      doğru karşılaştırma için Mevzuat
                      Bilgi Sistemi&apos;ndeki güncel
                      metni kullanabilirsiniz.
                    </p>
                  )}
                </div>
              )}


              {!isAutomaticReference && referenceCreatedMessage && (
                <p className="manual-selection-summary">
                  <Check size={16} />

                  <span>
                    {referenceCreatedMessage}
                  </span>
                </p>
              )}


              {!isAutomaticReference && (
              <div className="reference-actions">
                {!referenceFormOpen && (
                  <button
                    type="button"
                    className="secondary-button"
                    onClick={() => {
                      setReferenceFormOpen(true);
                      setReferenceCreatedMessage("");
                    }}
                  >
                    <Plus size={16} />
                    Yeni Referans Yönetmelik
                  </button>
                )}

                {references.length > 0 && (
                  <button
                    type="button"
                    className="secondary-button"
                    onClick={() =>
                      setSuggestOpen(
                        (current) => !current
                      )
                    }
                  >
                    <ScanSearch size={16} />
                    {suggestOpen
                      ? "Öneriyi Kapat"
                      : "Referansı Otomatik Bul"}
                  </button>
                )}
              </div>
              )}


              {/* OTOMATIK REFERANS ONERISI

                  Oneri SECIMI KENDILIGINDEN DEGISTIRMEZ.
                  Kullanici uygulamak isterse butona
                  basar; manuel secim daima ustundur. */}

              {!isAutomaticReference && suggestOpen && (
                <div className="reference-form">
                  <div className="form-field">
                    <label htmlFor="suggest-text">
                      Değişiklik metni
                    </label>

                    <textarea
                      id="suggest-text"
                      rows={5}
                      placeholder="Resmî Gazete'deki değişiklik yönetmeliğinin metnini yapıştırın."
                      value={suggestText}
                      onChange={(event) =>
                        setSuggestText(event.target.value)
                      }
                    />

                    <span className="field-help">
                      Metinden hedef mevzuat adı çıkarılır
                      ve kayıtlı referanslarla eşleştirilir.
                      Güncel konsolide metin tercih edilir.
                    </span>
                  </div>

                  <div className="reference-form-actions">
                    <button
                      type="button"
                      className="secondary-button"
                      onClick={handleSuggestReference}
                      disabled={suggestLoading}
                    >
                      <ScanSearch size={16} />
                      {suggestLoading
                        ? "Referans aranıyor..."
                        : "Referans Öner"}
                    </button>
                  </div>

                  {suggestError && (
                    <p className="scope-notice">
                      {suggestError}
                    </p>
                  )}

                  {suggestResult && (
                    <div className="suggest-result">
                      {suggestResult.base_regulation_name && (
                        <p className="field-help">
                          Hedef mevzuat:{" "}
                          <strong>
                            {
                              suggestResult.base_regulation_name
                            }
                          </strong>
                        </p>
                      )}

                      <p className="scope-notice">
                        {suggestResult.detail}
                      </p>

                      {suggestResult.status === "matched" &&
                        suggestResult.reference_id !== null && (
                          <button
                            type="button"
                            className="primary-button"
                            onClick={() => {
                              setReferenceRegulationId(
                                suggestResult.reference_id
                              );
                              setReferenceError("");
                              setSuggestOpen(false);
                            }}
                          >
                            <Check size={16} />
                            Bu referansı seç:{" "}
                            {suggestResult.reference_title}
                          </button>
                        )}
                    </div>
                  )}
                </div>
              )}


              {!isAutomaticReference && referenceFormOpen && (
                <NewRegulationReferenceForm
                  onCreated={handleReferenceCreated}
                  onCancel={() =>
                    setReferenceFormOpen(false)
                  }
                />
              )}


              {!isAutomaticReference && referenceError && (
                <p className="scope-notice">
                  {referenceError}
                </p>
              )}
            </div>
          )}
        </div>


        {/* KAYNAK */}

        <div className="form-panel">
          <div className="form-panel-heading">
            <div className="section-number">
              2
            </div>

            <div>
              <h2>
                Kaynak ve takip konusu
              </h2>

              <p>
                İzlemek istediğiniz sayfayı
                ve Gemini'nin takip edeceği
                konuyu belirtin.
              </p>
            </div>
          </div>


          <div className="form-grid">
            <div className="form-field">
              <label htmlFor="monitor-name">
                İzleme adı
              </label>

              <input
                id="monitor-name"
                name="name"
                type="text"
                placeholder="Örn. AI Teknoloji Haberleri"
                defaultValue={initialValues.name}
                required
              />
            </div>


            <div className="form-field">
              <label htmlFor="website-url">
                Website URL
              </label>

              <div className="input-with-icon">
                <Globe2 size={18} />

                <input
                  id="website-url"
                  name="url"
                  type="url"
                  placeholder="https://example.com"
                  value={url}
                  onChange={handleUrlChange}
                  required
                />
              </div>
            </div>


            <div className="form-field full-width">
              <label htmlFor="topic">
                Takip etmek istediğiniz konu
              </label>

              <textarea
                id="topic"
                name="topic"
                rows={4}
                placeholder="Örn. Yeni yapay zeka modelleri, API güncellemeleri ve fiyat değişiklikleri"
                defaultValue={initialValues.topic}
                required
              />

              <span className="field-help">
                Konuyu doğal dille
                yazabilirsiniz. Gemini
                değişiklikleri bu açıklamaya
                göre değerlendirecek.
              </span>
            </div>
          </div>
        </div>


        {/* ALAN */}

        <div className="form-panel">
          <div className="form-panel-heading">
            <div className="section-number">
              3
            </div>

            <div>
              <h2>
                İzlenecek alan
              </h2>

              <p>
                Sayfanın hangi kapsamda takip
                edileceğini seçin.
              </p>
            </div>
          </div>


          <div className="scope-grid">
            <ScopeOption
              value="full_page"
              selected={scope}
              onChange={setScope}
              icon={Monitor}
              title="Tüm sayfayı izle"
              description="URL üzerindeki tüm anlamlı içerik değişikliklerini takip eder."
            />

            <ScopeOption
              value="auto_section"
              selected={scope}
              onChange={setScope}
              icon={ScanSearch}
              title="Konuyla ilgili alanı otomatik bul"
              description="Takip konusuyla ilgili bölüm otomatik belirlenir ve yalnız bu alan izlenir."
            />

            <ScopeOption
              value="manual_section"
              selected={scope}
              onChange={setScope}
              icon={SquareMousePointer}
              title="Manuel alan seç"
              description="Sayfadaki algılanan bölümlerden takip etmek istediğiniz alanı seçin."
            />
          </div>


          {/* MANUEL BOLUM SECIMI */}

          {isManualScope && (
            <div className="manual-section">
              {!currentUrl && (
                <p className="scope-notice">
                  Bölümleri görmek için önce Website URL
                  girin.
                </p>
              )}


              {currentUrl && (
                <div className="manual-section-actions">
                  <button
                    type="button"
                    className="secondary-button"
                    onClick={handleFetchCandidates}
                    disabled={candidatesLoading}
                  >
                    <ScanSearch size={16} />

                    {candidatesLoading
                      ? "Bölümler yükleniyor..."
                      : candidatesValid
                        ? "Bölümleri Yeniden Getir"
                        : "Bölümleri Getir"}
                  </button>

                  {candidates.pageTitle &&
                    candidatesValid && (
                      <span className="field-help">
                        {candidates.pageTitle}
                      </span>
                    )}
                </div>
              )}


              {manualSelectionValid && (
                <p className="manual-selection-summary">
                  <Check size={16} />

                  <span>
                    Seçili bölüm:{" "}
                    <strong>
                      {manualSelection.label}
                    </strong>
                  </span>
                </p>
              )}


              {manualSelectionStale && (
                <p className="scope-notice">
                  Website URL değiştiği için önceki bölüm
                  seçimi (&quot;{manualSelection.label}&quot;)
                  artık kullanılamaz. Bölümleri yeniden
                  getirip yeni bir alan seçin.
                </p>
              )}


              {candidatesError && (
                <p className="scope-notice">
                  {candidatesError}
                </p>
              )}


              {candidatesValid &&
                candidates.items.length === 0 && (
                  <p className="scope-notice">
                    Bu sayfada seçilebilir anlamlı bir bölüm
                    bulunamadı.
                  </p>
                )}


              {candidatesValid &&
                candidates.items.length > 0 && (
                  <div className="candidate-list">
                    {candidates.items.map(
                      (candidate) => (
                        <label
                          key={candidate.candidate_id}
                          className={[
                            "candidate-option",
                            manualSelection.reference ===
                            candidate.reference
                              ? "selected"
                              : "",
                          ]
                            .filter(Boolean)
                            .join(" ")}
                        >
                          <input
                            type="radio"
                            name="manual_candidate"
                            value={candidate.candidate_id}
                            checked={
                              manualSelection.reference ===
                              candidate.reference
                            }
                            onChange={() =>
                              handleSelectCandidate(
                                candidate
                              )
                            }
                          />

                          <div className="candidate-body">
                            <div className="candidate-heading">
                              <strong>
                                {candidate.label}
                              </strong>

                              <span className="candidate-meta">
                                {candidate.tag} ·{" "}
                                {formatCharacterCount(
                                  candidate.text_length
                                )}
                              </span>
                            </div>

                            {/* Onizleme DUZ METIN olarak
                                render edilir. HTML olarak
                                yorumlanmaz. */}
                            <p className="candidate-preview">
                              {candidate.preview}
                            </p>
                          </div>
                        </label>
                      )
                    )}
                  </div>
                )}


              {selectionError && (
                <p className="scope-notice">
                  {selectionError}
                </p>
              )}
            </div>
          )}
        </div>


        {/* ZAMANLAMA */}

        <div className="form-panel">
          <div className="form-panel-heading">
            <div className="section-number">
              4
            </div>

            <div>
              <h2>
                Zamanlama
              </h2>

              <p>
                Web sitesinin ne zaman
                otomatik kontrol edileceğini
                belirleyin.
              </p>
            </div>
          </div>


          <div className="form-grid three-columns">
            <div className="form-field">
              <label htmlFor="schedule">
                Tekrar
              </label>

              <select
                id="schedule"
                name="schedule_type"
                defaultValue={initialValues.schedule_type}
              >
                <option value="daily">
                  Her gün
                </option>

                <option value="weekdays">
                  Hafta içi
                </option>

                <option value="weekly">
                  Haftada bir
                </option>
              </select>
            </div>


            <div className="form-field">
              <label htmlFor="schedule-time">
                Kontrol saati
              </label>

              <div className="input-with-icon">
                <Clock3 size={18} />

                <input
                  id="schedule-time"
                  name="schedule_time"
                  type="time"
                  defaultValue={initialValues.schedule_time}
                  required
                />
              </div>
            </div>


            <div className="form-field">
              <label htmlFor="start-date">
                Başlangıç tarihi
              </label>

              <div className="input-with-icon">
                <CalendarDays
                  size={18}
                />

                <input
                  id="start-date"
                  name="start_date"
                  type="date"
                  defaultValue={initialValues.start_date}
                />
              </div>
            </div>
          </div>


          <div className="schedule-end">
            <label className="checkbox-control">
              <input
                type="checkbox"
                checked={unlimited}
                onChange={(event) =>
                  setUnlimited(
                    event.target.checked
                  )
                }
              />

              <span>
                Süresiz takip et
              </span>
            </label>


            {!unlimited && (
              <div className="form-field end-date-field">
                <label htmlFor="end-date">
                  Bitiş tarihi
                </label>

                <input
                  id="end-date"
                  name="end_date"
                  type="date"
                  defaultValue={initialValues.end_date}
                  required={!unlimited}
                />
              </div>
            )}
          </div>
        </div>


        {/* BİLDİRİMLER */}

        <div className="form-panel">
          <div className="form-panel-heading">
            <div className="section-number">
              5
            </div>

            <div>
              <h2>
                Bildirimler
              </h2>

              <p>
                Önemli değişiklik bulunduğunda
                nasıl haberdar olmak istediğinizi
                seçin.
              </p>
            </div>
          </div>


          <div className="notification-options">
            <label className="notification-option">
              <div className="notification-icon">
                <Bell size={19} />
              </div>

              <div className="notification-copy">
                <strong>
                  Uygulama içi bildirim
                </strong>

                <span>
                  Kontrol sonuçlarını ve
                  değişiklikleri uygulama
                  içerisinde göster.
                </span>
              </div>

              <input
                type="checkbox"
                checked={
                  inAppNotification
                }
                onChange={(event) =>
                  setInAppNotification(
                    event.target.checked
                  )
                }
              />
            </label>


            <label
              className={[
                "notification-option",
                emailAvailable ? "" : "disabled",
              ]
                .filter(Boolean)
                .join(" ")}
            >
              <div className="notification-icon">
                <Mail size={19} />
              </div>

              <div className="notification-copy">
                <strong>
                  E-mail bildirimi
                </strong>

                <span>
                  {emailAvailable
                    ? "Önemli bir değişiklik bulunduğunda belirttiğiniz adrese e-mail gönderilir."
                    : "E-mail gönderimi için SMTP yapılandırması gerekli."}
                </span>
              </div>

              <input
                type="checkbox"
                checked={emailNotification}
                disabled={!emailAvailable}
                onChange={(event) =>
                  setEmailNotification(
                    event.target.checked
                  )
                }
              />
            </label>
          </div>


          {emailNotification && (
            <div className="form-grid">
              <div className="form-field full-width">
                <label htmlFor="notification-email">
                  Bildirim e-mail adresi
                </label>

                <div className="input-with-icon">
                  <Mail size={18} />

                  <input
                    id="notification-email"
                    name="notification_email"
                    type="email"
                    placeholder="ornek@sirket.com"
                    value={notificationEmail}
                    onChange={(event) =>
                      setNotificationEmail(
                        event.target.value
                      )
                    }
                    required
                  />
                </div>

                <span className="field-help">
                  Bildirimler yalnızca Gemini değişikliği
                  konuyla ilgili ve bildirmeye değer
                  bulduğunda gönderilir.
                </span>
              </div>
            </div>
          )}


          {capabilitiesLoaded &&
            !emailAvailable &&
            emailNotification && (
              <p className="scope-notice">
                Bu izlemede e-mail bildirimi kayıtlı ancak
                sunucuda SMTP yapılandırması bulunmadığı için
                gönderim yapılamıyor. Kayıt olduğu gibi
                korunuyor.
              </p>
            )}
        </div>


        {submitError && (
          <div className="placeholder-panel">
            <strong>
              {errorTitle}
            </strong>

            <p>
              {submitError}
            </p>
          </div>
        )}


        {/* SUBMIT */}

        <div className="form-actions">
          <NavLink
            to="/monitors"
            className="secondary-button"
          >
            Vazgeç
          </NavLink>

          {/* Manuel kapsamda gecerli bir bolum secimi,
              mevzuat planinda da referans yonetmelik
              secimi olmadan kayit yapilamaz. */}
          <button
            type="submit"
            className="primary-button"
            disabled={
              submitting ||
              manualBlocked ||
              referenceBlocked
            }
          >
            <Radar size={18} />

            {submitting
              ? "Kaydediliyor..."
              : submitLabel}
          </button>
        </div>
      </form>
  );
}


function NewMonitorPage() {
  const navigate = useNavigate();

  const [
    submitting,
    setSubmitting,
  ] = useState(false);

  const [
    submitError,
    setSubmitError,
  ] = useState("");


  async function handleCreate(payload) {
    try {
      setSubmitting(true);
      setSubmitError("");

      await createMonitor(
        payload
      );

      navigate(
        "/monitors"
      );
    } catch (requestError) {
      setSubmitError(
        requestError.message ||
          "İzleme oluşturulurken bir hata oluştu."
      );
    } finally {
      setSubmitting(false);
    }
  }


  return (
    <section>
      <div className="page-header">
        <div>
          <p className="eyebrow">
            YENİ MONİTÖR
          </p>

          <h1>
            Yeni İzleme Oluştur
          </h1>

          <p className="page-description">
            İzlemek istediğiniz web sitesini,
            konuyu ve kontrol zamanını
            belirleyin.
          </p>
        </div>
      </div>


      <MonitorForm
        initialValues={NEW_MONITOR_DEFAULTS}
        submitting={submitting}
        submitError={submitError}
        errorTitle="İzleme oluşturulamadı"
        submitLabel="Takibi Başlat"
        onSubmit={handleCreate}
      />
    </section>
  );
}


function MonitorEditPage() {
  const navigate = useNavigate();
  const { monitorId } = useParams();

  const [monitor, setMonitor] = useState(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");

  const [
    submitting,
    setSubmitting,
  ] = useState(false);

  const [
    submitError,
    setSubmitError,
  ] = useState("");


  useEffect(() => {
    let active = true;

    async function loadMonitor() {
      try {
        setLoading(true);
        setLoadError("");

        const data = await getMonitor(
          monitorId
        );

        if (active) {
          setMonitor(data);
        }
      } catch (requestError) {
        if (active) {
          setLoadError(
            requestError.message ||
              "İzleme yüklenirken bir hata oluştu."
          );
        }
      } finally {
        if (active) {
          setLoading(false);
        }
      }
    }

    loadMonitor();

    return () => {
      active = false;
    };
  }, [monitorId]);


  async function handleUpdate(payload) {
    try {
      setSubmitting(true);
      setSubmitError("");

      await updateMonitor(
        monitorId,
        payload
      );

      navigate(
        "/monitors"
      );
    } catch (requestError) {
      setSubmitError(
        requestError.message ||
          "İzleme güncellenirken bir hata oluştu."
      );
    } finally {
      setSubmitting(false);
    }
  }


  return (
    <section>
      <div className="page-header">
        <div>
          <p className="eyebrow">
            İZLEME DÜZENLE
          </p>

          <h1>
            {monitor
              ? monitor.name
              : "İzlemeyi Düzenle"}
          </h1>

          <p className="page-description">
            Mevcut izleme ayarlarını
            güncelleyebilirsiniz.
          </p>
        </div>
      </div>


      {loading && (
        <div className="placeholder-panel">
          İzleme yükleniyor...
        </div>
      )}


      {!loading && loadError && (
        <div className="placeholder-panel">
          <strong>
            İzleme yüklenemedi
          </strong>

          <p>
            {loadError}
          </p>

          <NavLink
            to="/monitors"
            className="secondary-button"
          >
            İzlemelere dön
          </NavLink>
        </div>
      )}


      {!loading && !loadError && monitor && (
        <MonitorForm
          initialValues={monitorToFormValues(
            monitor
          )}
          submitting={submitting}
          submitError={submitError}
          errorTitle="İzleme güncellenemedi"
          submitLabel="Değişiklikleri Kaydet"
          onSubmit={handleUpdate}
        />
      )}
    </section>
  );
}


function NotificationsPage({ refreshSignal = 0 }) {
  const [notifications, setNotifications] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");


  useEffect(() => {
    let active = true;

    async function loadNotifications() {
      try {
        setLoading(true);
        setError("");

        const data = await getNotifications();

        if (active) {
          setNotifications(data);
        }
      } catch (requestError) {
        if (active) {
          setError(
            requestError.message ||
              "Bildirimler yüklenirken bir hata oluştu."
          );
        }
      } finally {
        if (active) {
          setLoading(false);
        }
      }
    }

    loadNotifications();

    return () => {
      active = false;
    };
    // refreshSignal degistiginde liste yeniden cekilir.
    //
    // Sinyal iki durumda artar:
    //   1. manuel calistirma bildirim olusturdu
    //   2. yoklama okunmamis sayisinda degisiklik gordu
    //      (scheduler'in arka planda urettigi bildirim)
    //
    // Boylece ekran ACIKKEN yeni kayit gorunur; route
    // degistirmek gerekmez.
  }, [refreshSignal]);


  return (
    <section>
      <div className="page-header">
        <div>
          <p className="eyebrow">
            BİLDİRİMLER
          </p>

          <h1>
            Bildirimler
          </h1>

          <p className="page-description">
            Tespit edilen önemli değişiklikleri
            burada göreceksiniz.
          </p>
        </div>
      </div>


      {loading && (
        <div className="placeholder-panel">
          Bildirimler yükleniyor...
        </div>
      )}


      {!loading && error && (
        <div className="placeholder-panel">
          <strong>
            Bildirimler yüklenemedi
          </strong>

          <p>
            {error}
          </p>
        </div>
      )}


      {!loading &&
        !error &&
        notifications.length === 0 && (
          <div className="empty-state">
            <div className="empty-icon">
              <Bell size={26} />
            </div>

            <h2>
              Henüz bildirim yok
            </h2>

            <p>
              Takip ettiğiniz sayfalarda konuyla ilgili
              önemli bir değişiklik bulunduğunda
              bildirimler burada listelenir.
            </p>
          </div>
        )}


      {!loading &&
        !error &&
        notifications.length > 0 && (
          <div className="monitor-form">
            {notifications.map((notification) => (
              <NavLink
                key={notification.id}
                to={`/notifications/${notification.id}`}
                className={
                  notification.is_read
                    ? "form-panel notification-item"
                    : "form-panel notification-item unread"
                }
              >
                <div className="form-panel-heading">
                  <div className="section-number">
                    <Bell size={17} />
                  </div>

                  <div className="notification-heading-text">
                    <h2>
                      {notification.title}
                    </h2>

                    <p>
                      {notification.monitor_name}
                    </p>
                  </div>

                  <span
                    className={`notification-badge ${notification.importance}`}
                  >
                    {formatImportance(
                      notification.importance
                    )}
                  </span>
                </div>


                <p className="notification-message">
                  {notification.message}
                </p>


                <div className="notification-meta">
                  <span className="notification-tag">
                    {notification.category}
                  </span>

                  <span>
                    {formatDateTime(
                      notification.created_at
                    )}
                  </span>

                  {!notification.is_read && (
                    <span className="unread-tag">
                      Yeni
                    </span>
                  )}

                  <span className="notification-open">
                    Detayı gör
                  </span>
                </div>
              </NavLink>
            ))}
          </div>
        )}
    </section>
  );
}


function ContextBlockView({ block, variant }) {
  // Snapshot metni React text node olarak render edilir.
  // dangerouslySetInnerHTML KULLANILMAZ; içerikteki
  // HTML/script benzeri metin asla çalıştırılabilir olmaz.
  return (
    <div className="context-block">
      <div className="context-block-range">
        Satır {block.start_line}–{block.end_line}
      </div>

      <div className="context-lines">
        {block.lines.map((line) => (
          <div
            key={line.line_number}
            className={[
              "context-line",
              line.changed ? `changed ${variant}` : "",
              line.evidence ? "evidence" : "",
            ]
              .filter(Boolean)
              .join(" ")}
          >
            <span className="context-line-number">
              {line.line_number}
            </span>

            <span className="context-line-marker">
              {line.changed
                ? variant === "added"
                  ? "+"
                  : "−"
                : " "}
            </span>

            <span className="context-line-text">
              {line.text}
            </span>

            {line.evidence && (
              <span className="evidence-tag">
                Bildirime sebep olan
              </span>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}


// ---------------------------------------------------------
// MEVZUAT KARŞILAŞTIRMASI
//
// Tüm metinler DÜZ METİN olarak render edilir.
// dangerouslySetInnerHTML KULLANILMAZ.
//
// Ekranda gösterilen old_text / new_text / kanıt alanları
// sunucu tarafında ilgili kaynak içinde BİREBİR doğrulanmış
// metinlerdir. Doğrulanamayan alanlar backend'den null
// gelir ve burada hiç gösterilmez.
// ---------------------------------------------------------

const CHANGE_TYPE_LABELS = {
  amended: "Değiştirildi",
  added: "Eklendi",
  repealed: "Yürürlükten kaldırıldı",
  renamed: "Adı değiştirildi",
  unknown: "Belirlenemedi",
};


function formatChangeType(changeType) {
  return (
    CHANGE_TYPE_LABELS[changeType] ?? "Belirlenemedi"
  );
}


function formatConfidence(confidence) {
  if (
    confidence === null ||
    confidence === undefined
  ) {
    return null;
  }

  return `%${Math.round(confidence * 100)} güven`;
}


function ComparisonEvidence({ label, text }) {
  if (!text) {
    return null;
  }

  return (
    <div className="comparison-evidence">
      <span className="comparison-evidence-label">
        {label}
      </span>

      <p className="comparison-evidence-text">
        {text}
      </p>
    </div>
  );
}


function RegulationComparisonItem({ item }) {
  const confidenceLabel = formatConfidence(
    item.confidence
  );

  return (
    <div className="comparison-item">
      <div className="comparison-item-heading">
        <strong>
          {item.affected_article ??
            "Madde belirlenemedi"}
        </strong>

        {item.affected_paragraph && (
          <span className="comparison-paragraph">
            {item.affected_paragraph}
          </span>
        )}

        <span
          className={`change-badge ${item.change_type}`}
        >
          {formatChangeType(item.change_type)}
        </span>

        {confidenceLabel && (
          <span className="comparison-confidence">
            {confidenceLabel}
          </span>
        )}
      </div>


      {/* Eski ve yeni metin YALNIZCA doğrulanmışsa
          gösterilir. */}

      {item.old_text && (
        <div className="comparison-text removed">
          <span className="comparison-text-label">
            ESKİ METİN
          </span>

          <p className="comparison-text-body">
            {item.old_text}
          </p>
        </div>
      )}


      {item.new_text && (
        <div className="comparison-text added">
          <span className="comparison-text-label">
            YENİ METİN
          </span>

          <p className="comparison-text-body">
            {item.new_text}
          </p>
        </div>
      )}


      {item.change_summary && (
        <div className="comparison-summary">
          <span className="comparison-text-label">
            DEĞİŞİKLİK ÖZETİ
          </span>

          <p className="comparison-text-body">
            {item.change_summary}
          </p>
        </div>
      )}


      <ComparisonEvidence
        label="Kaynak kanıtı"
        text={item.source_evidence}
      />

      <ComparisonEvidence
        label="Referans kanıtı"
        text={item.reference_evidence}
      />
    </div>
  );
}


function RegulationComparisonSection({ comparison }) {
  if (!comparison) {
    return null;
  }

  const items = comparison.items ?? [];

  const isCompleted =
    comparison.status === COMPARISON_STATUS_COMPLETED &&
    items.length > 0;

  return (
    <div className="form-panel">
      <div className="form-panel-heading">
        <div className="section-number">
          <Scale size={17} />
        </div>

        <div className="notification-heading-text">
          <h2>
            Mevzuat Karşılaştırması
          </h2>

          <p>
            Yeni değişikliğin referans yönetmelik
            üzerindeki karşılığı.
          </p>
        </div>

        {comparison.importance && isCompleted && (
          <span
            className={`notification-badge ${comparison.importance}`}
          >
            {formatImportance(
              comparison.importance
            )}
          </span>
        )}
      </div>


      {comparison.reference_regulation_title && (
        <div className="form-grid">
          <div className="form-field full-width">
            <label>
              Referans
            </label>

            <strong>
              {comparison.reference_regulation_title}
            </strong>
          </div>
        </div>
      )}


      {/* REFERANS SURUM UYARISI

          Karsilastirmanin KONSOLIDE olmayan bir metne
          dayandigi durumlarda sonuc gizlenmez, ama
          dayanaginin sinirli oldugu ACIKCA soylenir.
          Sahte kesinlik verilmez.

          reference_is_consolidated:
            true  -> uyari yok
            false -> konsolide degil uyarisi
            null  -> eski kayit, dayanak bilinmiyor */}

      {comparison.reference_is_consolidated === false && (
        <p className="comparison-warning">
          {NON_CONSOLIDATED_WARNING}
        </p>
      )}

      {comparison.reference_is_consolidated === null &&
        comparison.reference_regulation_id !== null && (
          <p className="comparison-warning">
            {UNKNOWN_CONSOLIDATION_WARNING}
          </p>
        )}


      {/* REFERANS SECIMI BILGISI

          Otomatik secilen referansta kullanici NASIL
          secildigini ve guven duzeyini gorur. */}

      {comparison.reference_match_method && (
        <div className="form-grid three-columns">
          <div className="form-field">
            <label>
              Referans seçimi
            </label>

            <strong>
              {comparison.reference_match_method ===
              "manual"
                ? "Manuel"
                : "Otomatik"}
            </strong>
          </div>

          <div className="form-field">
            <label>
              Eşleşme
            </label>

            <strong>
              {formatMatchMethod(
                comparison.reference_match_method
              ) ?? "Bilinmiyor"}
            </strong>
          </div>

          {comparison.reference_match_confidence !==
            null &&
            comparison.reference_match_confidence !==
              undefined && (
              <div className="form-field">
                <label>
                  Güven
                </label>

                <strong>
                  {formatConfidencePercent(
                    comparison.reference_match_confidence
                  )}
                </strong>
              </div>
            )}
        </div>
      )}


      {/* OTOMATIK ESLESTIRME BASARISIZ

          Karsilastirma CALISTIRILMADI. Nedeni acikca
          soylenir; uydurma sonuc uretilmez. */}

      {comparison.reference_regulation_id === null &&
        formatMatchStatus(
          comparison.reference_match_status
        ) && (
          <p className="comparison-warning">
            {formatMatchStatus(
              comparison.reference_match_status
            )}
          </p>
        )}


      {/* RESMI KESIF SONUCU

          Kayitli mevzuatlar arasinda bulunamadi ve
          Mevzuat Bilgi Sistemi'nde de guvenilir bir
          kayit belirlenemedi. Nedeni acikca soylenir. */}

      {comparison.reference_regulation_id === null &&
        formatDiscoveryStatus(
          comparison.reference_discovery_status
        ) && (
          <p className="comparison-warning">
            {formatDiscoveryStatus(
              comparison.reference_discovery_status
            )}
          </p>
        )}


      {/* REFERANS DOGRULANAMADI */}

      {comparison.reference_freshness_status ===
        "stale_unverified" && (
        <p className="comparison-warning">
          {STALE_REFERENCE_WARNING}
        </p>
      )}


      {/* OTOMATIK ESLESTIRME UYARISI

          Otomatik eslestirme farkli bir referans
          oneriyorsa kullanici bilgilendirilir. Sonuc
          GIZLENMEZ; karsilastirma kullanicinin secimiyle
          yapilmistir ve oyle soylenir. */}

      {comparison.reference_match_status ===
        "needs_review" && (
        <p className="comparison-warning">
          {NEEDS_REVIEW_WARNING}

          {comparison.suggested_reference_title && (
            <>
              {" "}
              Önerilen referans:{" "}
              <strong>
                {comparison.suggested_reference_title}
              </strong>
            </>
          )}
        </p>
      )}


      {/* TAMAMLANDI */}

      {isCompleted && (
        <>
          {comparison.overall_summary && (
            <p className="notification-message">
              {comparison.overall_summary}
            </p>
          )}

          <div className="comparison-list">
            {items.map((item) => (
              <RegulationComparisonItem
                key={item.id}
                item={item}
              />
            ))}
          </div>

          <p className="context-note">
            {COMPARISON_DISCLAIMER}
          </p>
        </>
      )}


      {/* EŞLEŞME YOK
          AI'ın uydurma karşılaştırma üretmesi yerine
          durum açıkça bildirilir. */}

      {comparison.status ===
        COMPARISON_STATUS_NO_MATCH && (
        <p className="context-note">
          İlgili mevzuat değişikliği tespit edildi ancak
          referans yönetmelikte güvenilir bir madde
          eşleşmesi bulunamadı.
        </p>
      )}


      {/* TAMAMLANAMADI */}

      {comparison.status ===
        COMPARISON_STATUS_FAILED && (
        <>
          <p className="context-note">
            Mevzuat karşılaştırması tamamlanamadı.
          </p>

          {comparison.error_message && (
            <p className="monitor-run-warning">
              {comparison.error_message}
            </p>
          )}
        </>
      )}


      {/* completed ama doğrulanmış madde yok:
          sessizce boş bırakılmaz. */}

      {comparison.status ===
        COMPARISON_STATUS_COMPLETED &&
        items.length === 0 && (
          <p className="context-note">
            Karşılaştırma tamamlandı ancak doğrulanabilen
            bir madde değişikliği bulunamadı.
          </p>
        )}
    </div>
  );
}


function ChangeSection({
  title,
  badgeLabel,
  variant,
  blocks,
  fallbackContent,
  emptyLabel,
  icon: Icon,
  hasEvidence,
}) {
  const fallbackLines = (fallbackContent ?? "")
    .split(/\r?\n/)
    .filter((line) => line.trim().length > 0);

  const hasBlocks = blocks && blocks.length > 0;
  const hasFallback = fallbackLines.length > 0;

  return (
    <div className="form-panel">
      <div className="form-panel-heading">
        <div className="section-number">
          <Icon size={17} />
        </div>

        <div className="notification-heading-text">
          <h2>
            {title}
          </h2>

          <p>
            {hasBlocks
              ? "Değişen satırlar snapshot içindeki bağlamıyla gösteriliyor."
              : "Kayıtlı değişiklik içeriği."}
          </p>
        </div>

        {hasEvidence && (
          <span className="change-badge evidence">
            Bildirimle ilgili
          </span>
        )}

        <span className={`change-badge ${variant}`}>
          {badgeLabel}
        </span>
      </div>


      {hasBlocks &&
        blocks.map((block) => (
          <ContextBlockView
            key={`${block.start_line}-${block.end_line}`}
            block={block}
            variant={variant}
          />
        ))}


      {!hasBlocks && hasFallback && (
        <div className="context-block">
          <div className="context-lines">
            {fallbackLines.map((line, index) => (
              <div
                key={index}
                className={`context-line changed ${variant}`}
              >
                <span className="context-line-marker">
                  {variant === "added" ? "+" : "−"}
                </span>

                <span className="context-line-text">
                  {line}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}


      {!hasBlocks && !hasFallback && (
        <p className="context-empty">
          {emptyLabel}
        </p>
      )}
    </div>
  );
}


function NotificationDetailPage({ onNotificationRead }) {
  const { notificationId } = useParams();

  const [detail, setDetail] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [readError, setReadError] = useState("");


  useEffect(() => {
    let active = true;

    async function loadDetail() {
      try {
        setLoading(true);
        setError("");

        const data = await getNotificationDetail(
          notificationId
        );

        if (!active) {
          return;
        }

        setDetail(data);

        // Okundu isaretleme detayin gosterilmesini
        // ENGELLEMEZ. Basarisiz olursa detay yine acilir,
        // yalnizca kucuk bir uyari gosterilir.
        if (data.is_read === false) {
          try {
            await markNotificationAsRead(
              notificationId
            );

            if (active) {
              setDetail((current) =>
                current
                  ? { ...current, is_read: true }
                  : current
              );
            }

            // Bell sayaci backend'den yeniden cekilir.
            if (onNotificationRead) {
              onNotificationRead();
            }
          } catch (readError) {
            if (active) {
              setReadError(
                readError.message ||
                  "Bildirim okundu olarak işaretlenemedi."
              );
            }
          }
        }
      } catch (requestError) {
        if (active) {
          setError(
            requestError.message ||
              "Değişiklik detayı yüklenirken bir hata oluştu."
          );
        }
      } finally {
        if (active) {
          setLoading(false);
        }
      }
    }

    loadDetail();

    return () => {
      active = false;
    };
  }, [notificationId, onNotificationRead]);


  const livePageUrl = detail
    ? buildLivePageUrl(
        detail.url,
        detail.added_content,
        detail.evidence_text,
        detail.evidence_type
      )
    : "";


  return (
    <section>
      <div className="page-header">
        <div>
          <p className="eyebrow">
            DEĞİŞİKLİK DETAYI
          </p>

          <h1>
            {detail
              ? detail.title
              : "Değişiklik Detayı"}
          </h1>

          <p className="page-description">
            Bildirime konu olan değişikliğin kayıtlı
            snapshot üzerindeki karşılığı.
          </p>
        </div>

        <NavLink
          to="/notifications"
          className="secondary-button"
        >
          <ArrowLeft size={18} />
          Bildirimlere dön
        </NavLink>
      </div>


      {loading && (
        <div className="placeholder-panel">
          Değişiklik detayı yükleniyor...
        </div>
      )}


      {!loading && error && (
        <div className="placeholder-panel">
          <strong>
            Değişiklik detayı yüklenemedi
          </strong>

          <p>
            {error}
          </p>

          <NavLink
            to="/notifications"
            className="secondary-button"
          >
            Bildirimlere dön
          </NavLink>
        </div>
      )}


      {!loading && !error && detail && (
        <div className="monitor-form">

          {readError && (
            <p className="read-error">
              {readError} Değişiklik detayı normal şekilde
              görüntülenmeye devam ediyor.
            </p>
          )}


          {/* ÜST BİLGİ */}

          <div className="form-panel">
            <div className="form-panel-heading">
              <div className="section-number">
                <Radar size={17} />
              </div>

              <div className="notification-heading-text">
                <h2>
                  {detail.monitor_name}
                </h2>

                <p>
                  {detail.url}
                </p>
              </div>

              <span
                className={`notification-badge ${detail.importance}`}
              >
                {formatImportance(
                  detail.importance
                )}
              </span>
            </div>


            <div className="form-grid three-columns">
              <div className="form-field">
                <label>
                  Bildirim tarihi
                </label>

                <strong>
                  {formatDateTime(
                    detail.created_at
                  )}
                </strong>
              </div>


              <div className="form-field">
                <label>
                  Kategori
                </label>

                <strong>
                  {detail.category}
                </strong>
              </div>


              <div className="form-field">
                <label>
                  Önem
                </label>

                <strong>
                  {formatImportance(
                    detail.importance
                  )}
                </strong>
              </div>
            </div>


            <div className="monitor-run">
              <div className="monitor-actions">
                <a
                  className="primary-button"
                  href={livePageUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  <ExternalLink size={16} />
                  Canlı sayfada görüntüle
                </a>
              </div>

              <p className="context-note">
                Tarayıcı desteklerse sayfa açıldığında değişen
                metne kaydırılır ve işaretlenir. Metin
                bulunamazsa sayfa normal şekilde açılır.
              </p>
            </div>
          </div>


          {/* GEMINI ÖZETİ */}

          <div className="form-panel">
            <div className="form-panel-heading">
              <div className="section-number">
                <Sparkles size={17} />
              </div>

              <div className="notification-heading-text">
                <h2>
                  Gemini Özeti
                </h2>

                <p>
                  Değişikliğin takip konusuna göre
                  yapay zeka değerlendirmesi.
                </p>
              </div>
            </div>

            <p className="notification-message">
              {detail.ai_summary ??
                detail.message ??
                "Bu değişiklik için yapay zeka özeti bulunmuyor."}
            </p>


            {detail.evidence_text && (
              <div className="evidence-panel">
                <div className="evidence-panel-heading">
                  <span className="change-badge evidence">
                    Bildirimle ilgili değişiklik
                  </span>

                  <span className="evidence-source">
                    {detail.evidence_type === "added"
                      ? "Eklenen içerikten"
                      : "Silinen içerikten"}
                  </span>
                </div>

                <p className="evidence-text">
                  {detail.evidence_text}
                </p>
              </div>
            )}


            {!detail.evidence_text && (
              <p className="context-note">
                Bu kayıt için kaynak metin işaretlenmemiş.
                Aşağıdaki değişiklikler ve canlı sayfa bağlantısı
                tüm diff üzerinden gösteriliyor.
              </p>
            )}
          </div>


          {/* MEVZUAT KARŞILAŞTIRMASI

              Yalnızca regulation_monitor bildirimlerinde
              ve yalnızca bu değişiklik için bir
              karşılaştırma yapıldıysa görünür.
              website_monitor detay ekranı değişmez. */}

          <RegulationComparisonSection
            comparison={detail.regulation_comparison}
          />


          {/* EKLENEN */}

          <ChangeSection
            title="Eklenen İçerik"
            badgeLabel="Eklenen"
            variant="added"
            icon={Plus}
            blocks={detail.added_blocks}
            fallbackContent={detail.added_content}
            emptyLabel="Bu değişiklikte eklenen içerik bulunmuyor."
            hasEvidence={
              detail.evidence_type === "added"
            }
          />


          {/* SİLİNEN */}

          <ChangeSection
            title="Silinen İçerik"
            badgeLabel="Silinen"
            variant="removed"
            icon={Minus}
            blocks={detail.removed_blocks}
            fallbackContent={detail.removed_content}
            emptyLabel="Bu değişiklikte silinen içerik bulunmuyor."
            hasEvidence={
              detail.evidence_type === "removed"
            }
          />
        </div>
      )}
    </section>
  );
}


function SidebarLink({
  to,
  icon: Icon,
  children,
}) {
  return (
    <NavLink
      to={to}
      className={({ isActive }) =>
        `sidebar-link ${
          isActive
            ? "active"
            : ""
        }`
      }
    >
      <Icon
        size={19}
        strokeWidth={1.8}
      />

      <span>
        {children}
      </span>
    </NavLink>
  );
}


// ARKA PLAN YOKLAMA ARALIGI
//
// Yalnizca uygulama ici okunmamis bildirim sayaci icin
// kullanilir. E-mail icin yoklama YOKTUR.
//
// 25 saniye: kullanicinin fark edecegi kadar hizli, ama
// backend'e yuk bindirmeyecek kadar seyrek. Manuel
// calistirma zaten hizli yoldan aninda tazeler; yoklama
// sadece scheduler'in arka planda urettigi bildirimler
// icindir.
const UNREAD_POLL_INTERVAL_MS = 25000;


function App() {
  const location = useLocation();

  const [unreadCount, setUnreadCount] = useState(0);

  // Bildirim listesini tazelemek icin sayac.
  //
  // Deger her degistiginde Bildirimler ekrani listeyi
  // yeniden ceker. Boylece ekran ACIKKEN olusan yeni bir
  // bildirim icin route degistirmek gerekmez.
  const [
    notificationsVersion,
    setNotificationsVersion,
  ] = useState(0);

  // Ust uste binen istekleri engeller. Route efekti,
  // manuel calistirma ve polling ayni anda tetiklenirse
  // tek bir GET gider.
  const unreadRequestRef = useRef(false);

  // Son okunan sayi. Degisiklik tespit etmek icin
  // tutulur; state yerine ref kullanilir cunku bir
  // state guncelleyicisi icinden baska bir state
  // guncellenemez.
  const lastUnreadRef = useRef(null);


  const refreshUnreadCount = useCallback(async () => {
    if (unreadRequestRef.current) {
      return;
    }

    unreadRequestRef.current = true;

    try {
      const data = await getUnreadNotificationCount();

      const next = data.count ?? 0;

      // Sayi degistiyse liste de bayatlamis demektir.
      // Scheduler'in arka planda olusturdugu bildirimler
      // bu yolla yakalanir.
      if (
        lastUnreadRef.current !== null &&
        lastUnreadRef.current !== next
      ) {
        setNotificationsVersion(
          (current) => current + 1
        );
      }

      lastUnreadRef.current = next;

      setUnreadCount(next);
    } catch {
      // Sayac cekilemezse uygulama calismaya devam eder;
      // onceki rozet degeri korunur.
    } finally {
      unreadRequestRef.current = false;
    }
  }, []);


  // MANUEL CALISTIRMA HIZLI YOLU
  //
  // Calistirma yaniti bildirim olusup olusmadigini ZATEN
  // soyluyor. Polling araligini beklemeye gerek yok.
  //
  // Bildirim olusmadiysa (ornegin degisiklik yok) istek
  // ATILMAZ: sayinin degismedigi bilinmektedir.
  const handleRunCompleted = useCallback(
    (result) => {
      if (!result || !result.notification_created) {
        return;
      }

      setNotificationsVersion(
        (current) => current + 1
      );

      refreshUnreadCount();
    },
    [refreshUnreadCount]
  );


  // App ilk mount edildiginde ve her route degisiminde
  // (Bildirimler listesi, bildirim detayi dahil) sayac
  // backend'den yeniden okunur.
  useEffect(() => {
    // Efekt govdesinde DOGRUDAN cagrilmaz: senkron
    // setState zinciri olusmasin diye ic fonksiyona
    // sarilir.
    async function loadUnreadCount() {
      await refreshUnreadCount();
    }

    loadUnreadCount();
  }, [location.pathname, refreshUnreadCount]);


  // ARKA PLAN YOKLAMASI
  //
  // Scheduler bildirimi arka planda olusturur; frontend
  // bunu HTTP yaniti uzerinden ogrenemez. Bu sprintte
  // WebSocket/SSE YOK, bu yuzden hafif bir yoklama
  // kullanilir.
  //
  // Sekme gorunur degilken zamanlayici DURDURULUR ve
  // sekme geri gelince bir kez hemen okunur.
  useEffect(() => {
    let timerId = null;

    function stopPolling() {
      if (timerId !== null) {
        clearInterval(timerId);
        timerId = null;
      }
    }

    function startPolling() {
      // Ayni anda ikinci bir zamanlayici KURULMAZ.
      if (timerId !== null) {
        return;
      }

      timerId = setInterval(() => {
        refreshUnreadCount();
      }, UNREAD_POLL_INTERVAL_MS);
    }

    function handleVisibilityChange() {
      if (document.hidden) {
        stopPolling();
        return;
      }

      refreshUnreadCount();
      startPolling();
    }

    if (!document.hidden) {
      startPolling();
    }

    document.addEventListener(
      "visibilitychange",
      handleVisibilityChange
    );

    return () => {
      stopPolling();

      document.removeEventListener(
        "visibilitychange",
        handleVisibilityChange
      );
    };
  }, [refreshUnreadCount]);


  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">
            <Globe2 size={21} />
          </div>

          <div>
            <div className="brand-name">
              Website Monitor
            </div>

            <div className="brand-subtitle">
              AI
            </div>
          </div>
        </div>


        <nav
          className="sidebar-navigation"
          aria-label="Ana navigasyon"
        >
          <SidebarLink
            to="/monitors"
            icon={LayoutDashboard}
          >
            İzlemeler
          </SidebarLink>

          <SidebarLink
            to="/new-monitor"
            icon={Plus}
          >
            Yeni İzleme
          </SidebarLink>

          <SidebarLink
            to="/notifications"
            icon={Bell}
          >
            Bildirimler
          </SidebarLink>
        </nav>


        <div className="sidebar-footer">
          <div className="system-status">
            <span className="status-dot" />
            Sistem aktif
          </div>
        </div>
      </aside>


      <div className="workspace">
        <header className="topbar">
          <div className="topbar-title">
            Website Monitor AI
          </div>

          <NavLink
            to="/notifications"
            className="icon-button bell-button"
            aria-label={
              unreadCount > 0
                ? `Bildirimler, ${unreadCount} okunmamış`
                : "Bildirimler"
            }
          >
            <Bell size={19} />

            {unreadCount > 0 && (
              <span className="bell-badge">
                {unreadCount > 99 ? "99+" : unreadCount}
              </span>
            )}
          </NavLink>
        </header>


        <main className="main-content">
          <Routes>
            <Route
              path="/"
              element={
                <Navigate
                  to="/monitors"
                  replace
                />
              }
            />

            <Route
              path="/monitors"
              element={
                <DashboardPage
                  onRunCompleted={handleRunCompleted}
                />
              }
            />

            <Route
              path="/new-monitor"
              element={
                <NewMonitorPage />
              }
            />

            <Route
              path="/monitors/:monitorId/edit"
              element={
                <MonitorEditPage />
              }
            />

            <Route
              path="/notifications"
              element={
                <NotificationsPage
                  refreshSignal={notificationsVersion}
                />
              }
            />

            <Route
              path="/notifications/:notificationId"
              element={
                <NotificationDetailPage
                  onNotificationRead={refreshUnreadCount}
                />
              }
            />
          </Routes>
        </main>
      </div>
    </div>
  );
}


export default App;