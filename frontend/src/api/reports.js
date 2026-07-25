// Reports & Dashboard API (prompt 16). Same authHeaders/handle idiom as fraud.js / cashShift.js.
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

function rangeQuery({ from, to, ...rest } = {}) {
  const params = new URLSearchParams();
  if (from) params.set("from", from);
  if (to) params.set("to", to);
  Object.entries(rest).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== "") params.set(k, v);
  });
  const q = params.toString();
  return q ? `?${q}` : "";
}

async function getReport(path, params) {
  const res = await fetch(`${BASE_URL}/reports/${path}${rangeQuery(params)}`, {
    headers: authHeaders(),
  });
  return handle(res);
}

// ---- report data (JSON) ----
export const getOccupancy = (p) => getReport("occupancy", p);
export const getDailySales = (p) => getReport("sales/daily", p);
export const getArrivalsDepartures = (p) => getReport("arrivals-departures", p);
export const getInHouse = (p) => getReport("in-house", p);
export const getTravelAgents = (p) => getReport("travel-agents", p);
export const getOta = (p) => getReport("ota", p);
export const getGst = (p) => getReport("gst", p);
export const getCashShift = (p) => getReport("cash-shift", p);
export const getCardAudit = (p) => getReport("card-audit", p);
export const getFraudSummary = (p) => getReport("fraud-summary", p);

// ---- dashboard + trends ----
export const getDashboard = () => getReport("dashboard");
export const getWeeklyTrends = () => getReport("trends/weekly");

// ---- day-close / night audit ----
export const getDayClose = (date) => getReport("day-close", date ? { date } : {});
export const getDayCloseHistory = (limit = 30) => getReport("day-close/history", { limit });
export async function runDayClose(date) {
  const res = await fetch(
    `${BASE_URL}/reports/day-close/run${date ? `?date=${date}` : ""}`,
    { method: "POST", headers: authHeaders() }
  );
  return handle(res);
}

// ---- CSV / PDF export (blob download; bypasses `handle` since the body is binary) ----
export async function exportReport(path, params, format) {
  const res = await fetch(
    `${BASE_URL}/reports/${path}${rangeQuery({ ...params, format })}`,
    { headers: authHeaders() }
  );
  if (!res.ok) {
    let msg = `Export failed (HTTP ${res.status})`;
    try {
      const data = await res.json();
      if (data?.detail) msg = String(data.detail);
    } catch {
      /* binary or empty body */
    }
    throw new Error(msg);
  }
  const blob = await res.blob();
  // Derive a filename from the Content-Disposition header, else fall back.
  const cd = res.headers.get("Content-Disposition") || "";
  const match = cd.match(/filename="?([^"]+)"?/);
  const filename = match ? match[1] : `${path.replace(/\//g, "_")}.${format}`;
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}
