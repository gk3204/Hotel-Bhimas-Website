// OTA tracking API (prompt 17). Mirrors the cashShift/whatsapp authHeaders + handle idiom.
const BASE_URL = import.meta.env.VITE_API_URL || "http://127.0.0.1:8000";

function authHeaders() {
  const token = localStorage.getItem("adminToken");
  return {
    "Content-Type": "application/json",
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  };
}

async function handle(res) {
  if (!res.ok) {
    let msg = `HTTP ${res.status}`;
    try {
      const data = await res.json();
      if (data?.detail) {
        msg = Array.isArray(data.detail)
          ? data.detail.map((e) => e.msg || e.type).join(". ")
          : String(data.detail);
      }
    } catch {
      /* ignore parse errors */
    }
    throw new Error(msg);
  }
  return res.json();
}

function qs(params) {
  const p = new URLSearchParams();
  Object.entries(params || {}).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== "") p.append(k, v);
  });
  const s = p.toString();
  return s ? `?${s}` : "";
}

// --- Per-OTA config ---
export async function getChannels() {
  const res = await fetch(`${BASE_URL}/ota/channels`, { headers: authHeaders() });
  return handle(res);
}

export async function updateChannel(code, data) {
  const res = await fetch(`${BASE_URL}/ota/channels/${code}`, {
    method: "PUT",
    headers: authHeaders(),
    body: JSON.stringify(data),
  });
  return handle(res);
}

// --- OTA bookings list ---
export async function getOtaBookings(params) {
  const res = await fetch(`${BASE_URL}/ota/bookings${qs(params)}`, { headers: authHeaders() });
  return handle(res);
}

// --- Payout reconciliation + settlements ---
export async function getReconciliation(params) {
  const res = await fetch(`${BASE_URL}/ota/reconciliation${qs(params)}`, { headers: authHeaders() });
  return handle(res);
}

export async function getSettlements(params) {
  const res = await fetch(`${BASE_URL}/ota/settlements${qs(params)}`, { headers: authHeaders() });
  return handle(res);
}

export async function createSettlement(data) {
  const res = await fetch(`${BASE_URL}/ota/settlements`, {
    method: "POST",
    headers: authHeaders(),
    body: JSON.stringify(data),
  });
  return handle(res);
}

// --- Email draft inbox ---
export async function getDrafts(status, q) {
  // v6n.3: `q` searches the server (name / OTA id / phone), reaching drafts past the 200-row cap.
  const res = await fetch(`${BASE_URL}/ota/drafts${qs({ status, q })}`, { headers: authHeaders() });
  return handle(res);
}

export async function confirmDraft(id, data) {
  const res = await fetch(`${BASE_URL}/ota/drafts/${id}/confirm`, {
    method: "POST",
    headers: authHeaders(),
    body: JSON.stringify(data),
  });
  return handle(res);
}

export async function dismissDraft(id) {
  const res = await fetch(`${BASE_URL}/ota/drafts/${id}/dismiss`, {
    method: "POST",
    headers: authHeaders(),
  });
  return handle(res);
}

export async function pollMailbox() {
  const res = await fetch(`${BASE_URL}/ota/poll`, { method: "POST", headers: authHeaders() });
  return handle(res);
}

// One-time go-live backfill: import OTA mail on/after `since` (YYYY-MM-DD) without marking it read.
export async function importSince(since) {
  const res = await fetch(`${BASE_URL}/ota/import?since=${encodeURIComponent(since)}`, {
    method: "POST",
    headers: authHeaders(),
  });
  return handle(res);
}

// --- P1 Go-MMT payment tracking (admin) ---
const get = async (path) => handle(await fetch(`${BASE_URL}${path}`, { headers: authHeaders() }));
const post = async (path, body) =>
  handle(await fetch(`${BASE_URL}${path}`, {
    method: "POST",
    headers: authHeaders(),
    body: body === undefined ? undefined : JSON.stringify(body),
  }));

export const getPaymentSummary = () => get("/ota/payments/summary");
export const getPayments = (params) => get(`/ota/payments${qs(params)}`);
export const getPayment = (id) => get(`/ota/payments/${id}`);
export const getPaymentBookings = (params) => get(`/ota/payments/bookings${qs(params)}`);
export const getPaymentExceptions = () => get("/ota/payments/exceptions");
export const getBookingPaymentState = (bookingId) => get(`/ota/payments/booking/${bookingId}`);
export const getPaymentCandidates = (params) => get(`/ota/payments/candidates${qs(params)}`);
export const receivePayment = (id, data) => post(`/ota/payments/${id}/receive`, data);
export const undoPaymentReceipt = (id, reason) => post(`/ota/payments/${id}/undo`, { reason });
export const addPayment = (data) => post("/ota/payments", data);
export const linkPaymentLine = (lineId, bookingId) =>
  post(`/ota/payments/lines/${lineId}/link`, { booking_id: bookingId });
export const ignorePaymentLine = (lineId) => post(`/ota/payments/lines/${lineId}/ignore`);
export const importPayments = (since) => post(`/ota/payments/import${qs({ since })}`);
