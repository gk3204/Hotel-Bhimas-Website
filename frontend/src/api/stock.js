// Inventory / stock API (prompt 18c, slice 8). Same auth/handle/blob idiom as api/backoffice.js.
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
      /* ignore */
    }
    throw new Error(msg);
  }
  return res.json();
}

function query(params = {}) {
  const search = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== "") search.set(k, v);
  });
  const q = search.toString();
  return q ? `?${q}` : "";
}

const req = (path, { method = "GET", body, params } = {}) =>
  fetch(`${BASE_URL}${path}${query(params)}`, {
    method,
    headers: authHeaders(),
    ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
  }).then(handle);

export async function download(path, params = {}) {
  const res = await fetch(`${BASE_URL}${path}${query(params)}`, { headers: authHeaders() });
  if (!res.ok) {
    let msg = `Export failed (HTTP ${res.status})`;
    try {
      const data = await res.json();
      if (data?.detail) msg = String(data.detail);
    } catch {
      /* binary/empty body */
    }
    throw new Error(msg);
  }
  const blob = await res.blob();
  const cd = res.headers.get("Content-Disposition") || "";
  const match = cd.match(/filename="?([^"]+)"?/);
  const filename = match ? match[1] : `stock.${params.format || "csv"}`;
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

/* items */
export const listItems = (params) => req("/stock/items", { params });
export const getItem = (id) => req(`/stock/items/${id}`);
export const createItem = (body) => req("/stock/items", { method: "POST", body });
export const updateItem = (id, body) => req(`/stock/items/${id}`, { method: "PUT", body });
export const deactivateItem = (id) => req(`/stock/items/${id}`, { method: "DELETE" });

/* movements */
export const receiveStock = (id, body) => req(`/stock/items/${id}/receive`, { method: "POST", body });
export const adjustStock = (id, body) => req(`/stock/items/${id}/adjust`, { method: "POST", body });
export const listMovements = (params) => req("/stock/movements", { params });
export const getLowStock = () => req("/stock/low-stock");
export const exportStockReport = (format) => download("/stock/report", { format });

/* alerts + config */
export const runAlerts = () => req("/stock/alerts/run", { method: "POST" });
export const getStockConfig = () => req("/stock/config");
export const updateStockConfig = (body) => req("/stock/config", { method: "PUT", body });
