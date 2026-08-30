// Room service — take an order, print the KOT, deliver it (backlog v2 TBC-4).
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

const get = (path) => fetch(`${BASE_URL}${path}`, { headers: authHeaders() }).then(handle);
const send = (method, path, body) =>
  fetch(`${BASE_URL}${path}`, {
    method,
    headers: authHeaders(),
    ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
  }).then(handle);

export const getMenu = () => get("/room-service/menu");
export const getRooms = () => get("/room-service/rooms");
export const getOrders = (openOnly = true) =>
  get(`/room-service/orders?open_only=${openOnly ? "true" : "false"}`);
export const placeOrder = (body) => send("POST", "/room-service/orders", body);
// mark=false previews the docket without stamping it as printed.
export const getKot = (id, mark = true) =>
  get(`/room-service/orders/${id}/kot?mark=${mark ? "true" : "false"}`);
export const deliverOrder = (id) => send("POST", `/room-service/orders/${id}/deliver`);
export const getBill = (id, mark = false) =>
  get(`/room-service/orders/${id}/bill?mark=${mark ? "true" : "false"}`);
/**
 * Cancel an undelivered order (v4b5). A reason is mandatory, and when
 * `rs_cancel_otp_required` is armed the owner's code is too.
 * `body` = { reason, owner_otp_id?, owner_otp_code? }
 */
export const cancelOrder = (id, body) =>
  send("POST", `/room-service/orders/${id}/cancel`, body);
