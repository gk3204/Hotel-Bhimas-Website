// Editable operational settings API (F-A backbone): category option-lists + reg-slip rules.
// Same authHeaders/handle idiom as audit.js / reports.js.
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

// { families: {expense:[...], ...}, defaults: {...} }
export const getCategories = () =>
  fetch(`${BASE_URL}/settings/categories`, { headers: authHeaders() }).then(handle);

// items: string[] -> { family, items }
export const setCategoryList = (family, items) =>
  fetch(`${BASE_URL}/settings/categories/${encodeURIComponent(family)}`, {
    method: "PUT",
    headers: authHeaders(),
    body: JSON.stringify({ items }),
  }).then(handle);

// { text }
export const getRegistrationRules = () =>
  fetch(`${BASE_URL}/settings/registration-rules`, { headers: authHeaders() }).then(handle);

export const setRegistrationRules = (text) =>
  fetch(`${BASE_URL}/settings/registration-rules`, {
    method: "PUT",
    headers: authHeaders(),
    body: JSON.stringify({ text }),
  }).then(handle);

// v5m - arrival & departure rules: { rules, defaults }
export const getArrivalRules = () =>
  fetch(`${BASE_URL}/settings/arrival-rules`, { headers: authHeaders() }).then(handle);

export const setArrivalRules = (rules) =>
  fetch(`${BASE_URL}/settings/arrival-rules`, {
    method: "PUT",
    headers: authHeaders(),
    body: JSON.stringify({ rules }),
  }).then(handle);

// v5n — front-desk policy: how much KYC the desk must capture, and the no-show go-live cutoff
export const getFrontdeskConfig = () =>
  fetch(`${BASE_URL}/settings/frontdesk-config`, { headers: authHeaders() }).then(handle);

export const updateFrontdeskConfig = (patch) =>
  fetch(`${BASE_URL}/settings/frontdesk-config`, {
    method: "PUT",
    headers: authHeaders(),
    body: JSON.stringify(patch),
  }).then(handle);
