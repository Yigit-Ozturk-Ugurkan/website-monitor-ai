const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8001";


async function handleResponse(response) {
  if (response.ok) {
    return response.json();
  }

  let message = "İstek sırasında bir hata oluştu.";

  try {
    const errorData = await response.json();

    if (typeof errorData.detail === "string") {
      message = errorData.detail;
    } else if (Array.isArray(errorData.detail)) {
      message = errorData.detail
        .map((item) => item.msg)
        .join(", ");
    }
  } catch {
    // JSON olmayan hata cevabında varsayılan mesaj kullanılır.
  }

  throw new Error(message);
}


export async function getCapabilities() {
  // Deployment'ta hangi ozelliklerin gercekten
  // kullanilabilir oldugunu sorar.
  // Cevap yalnizca boolean tasir; sir icermez.
  const response = await fetch(
    `${API_BASE_URL}/api/capabilities`
  );

  return handleResponse(response);
}


export async function getMonitors() {
  const response = await fetch(
    `${API_BASE_URL}/api/monitors`
  );

  return handleResponse(response);
}


export async function getMonitor(monitorId) {
  const response = await fetch(
    `${API_BASE_URL}/api/monitors/${monitorId}`
  );

  return handleResponse(response);
}


export async function createMonitor(payload) {
  const response = await fetch(
    `${API_BASE_URL}/api/monitors`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(payload),
    }
  );

  return handleResponse(response);
}


export async function updateMonitor(monitorId, payload) {
  const response = await fetch(
    `${API_BASE_URL}/api/monitors/${monitorId}`,
    {
      method: "PATCH",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(payload),
    }
  );

  return handleResponse(response);
}


export async function getSectionCandidates(url) {
  // Manuel alan secimi icin sayfadaki bolumleri getirir.
  // Bu istek Gemini calistirmaz ve veritabanina yazmaz.
  const response = await fetch(
    `${API_BASE_URL}/api/monitors/section-candidates`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ url }),
    }
  );

  return handleResponse(response);
}


// ---------------------------------------------------------
// REFERANS YONETMELIK
//
// Bu istekler Gemini CALISTIRMAZ. Referans olusturmak ve
// listelemek hicbir AI istegi uretmez.
// ---------------------------------------------------------

export async function getRegulationReferences() {
  // Monitor formundaki secim listesi.
  // Yanit tam metin TASIMAZ; yalnizca ozet alanlar gelir.
  const response = await fetch(
    `${API_BASE_URL}/api/regulation-references`
  );

  return handleResponse(response);
}


export async function getRegulationReference(referenceId) {
  const response = await fetch(
    `${API_BASE_URL}/api/regulation-references/${referenceId}`
  );

  return handleResponse(response);
}


export async function createRegulationReference(payload) {
  // Backend kaynak adresi indirir, metni cikarir ve
  // hash'leyip kaydeder.
  const response = await fetch(
    `${API_BASE_URL}/api/regulation-references`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(payload),
    }
  );

  return handleResponse(response);
}


export async function matchRegulationReference(payload) {
  // Bir degisiklik metni icin uygun referansi ONERIR.
  //
  // Veritabanina hicbir sey yazmaz ve kullanicinin
  // secimini degistirmez. Yapay zeka YALNIZCA birden
  // fazla aday ayirt edilemediginde devreye girer.
  const response = await fetch(
    `${API_BASE_URL}/api/regulation-references/match`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(payload),
    }
  );

  return handleResponse(response);
}


export async function refreshRegulationReference(referenceId) {
  const response = await fetch(
    `${API_BASE_URL}/api/regulation-references/${referenceId}/refresh`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
    }
  );

  return handleResponse(response);
}


export async function runMonitor(monitorId) {
  const response = await fetch(
    `${API_BASE_URL}/api/monitors/${monitorId}/run`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
    }
  );

  return handleResponse(response);
}


// Uygulama ici bildirim akisi YALNIZCA in_app kayitlarini
// gosterir. E-mail bildirimleri ayri bir gonderim kaydidir;
// listeye ve okunmamis rozetine karismamalidir.
const IN_APP_NOTIFICATION_QUERY = "notification_type=in_app";


export async function getNotifications() {
  const response = await fetch(
    `${API_BASE_URL}/api/notifications`
    + `?${IN_APP_NOTIFICATION_QUERY}`
  );

  return handleResponse(response);
}


export async function getNotificationDetail(notificationId) {
  const response = await fetch(
    `${API_BASE_URL}/api/notifications/${notificationId}/detail`
  );

  return handleResponse(response);
}


export async function getUnreadNotificationCount() {
  const response = await fetch(
    `${API_BASE_URL}/api/notifications/unread-count`
    + `?${IN_APP_NOTIFICATION_QUERY}`
  );

  return handleResponse(response);
}


export async function markNotificationAsRead(notificationId) {
  const response = await fetch(
    `${API_BASE_URL}/api/notifications/${notificationId}/read`,
    {
      method: "PATCH",
      headers: {
        "Content-Type": "application/json",
      },
    }
  );

  return handleResponse(response);
}
