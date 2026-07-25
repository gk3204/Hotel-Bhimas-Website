// Guest portal admin/desk API (prompt 18d, slice 10). Same auth/handle idiom as api/stock.js.
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

/* requests (desk) */
export const listRequests = (params) => req("/portal/requests", { params });
export const ackRequest = (id) => req(`/portal/requests/${id}/ack`, { method: "POST" });
export const completeRequest = (id, body = {}) => req(`/portal/requests/${id}/complete`, { method: "POST", body });
export const dismissRequest = (id, body = {}) => req(`/portal/requests/${id}/dismiss`, { method: "POST", body });

/* sessions */
export const mintSession = (bookingId) => req("/portal/session", { method: "POST", body: { booking_id: bookingId } });
export const qrUrl = (bookingId) => `${BASE_URL}/portal/session/${bookingId}/qr.png`;

/* menu (admin) */
export const listMenu = (params) => req("/portal/menu-items", { params });
export const createMenuItem = (body) => req("/portal/menu-items", { method: "POST", body });
export const updateMenuItem = (id, body) => req(`/portal/menu-items/${id}`, { method: "PUT", body });
export const deactivateMenuItem = (id) => req(`/portal/menu-items/${id}`, { method: "DELETE" });

/* config */
export const getPortalConfig = () => req("/portal/config");
export const updatePortalConfig = (body) => req("/portal/config", { method: "PUT", body });
