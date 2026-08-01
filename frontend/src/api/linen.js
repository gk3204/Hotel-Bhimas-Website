// Linen / laundry API (FE-9). Same authHeaders/handle idiom as audit.js / settings.js.
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
        msg = Array.isArray(data.detail) ? data.detail.map((e) => e.msg || e.type).join(". ") : String(data.detail);
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

export const getBoard = () => get("/linen/board");
export const listItems = () => get("/linen/items");
export const createItem = (data) => send("POST", "/linen/items", data);
export const updateItem = (id, data) => send("PUT", `/linen/items/${id}`, data);
export const deactivateItem = (id) => send("DELETE", `/linen/items/${id}`);

export const sendToLaundry = (id, qty, reason) => send("POST", `/linen/items/${id}/send-laundry`, { qty, reason });
export const receiveLaundry = (id, qty, reason) => send("POST", `/linen/items/${id}/receive`, { qty, reason });
export const replenish = (id, qty, reason) => send("POST", `/linen/items/${id}/replenish`, { qty, reason });
export const issue = (id, qty, reason) => send("POST", `/linen/items/${id}/issue`, { qty, reason });

export const getSets = () => get("/linen/sets");
export const updateSet = (roomTypeId, items) => send("PUT", `/linen/sets/${roomTypeId}`, { items });

export const getMovements = (itemId) =>
  get(`/linen/movements${itemId ? `?item_id=${itemId}` : ""}`);
