// Customer / Guest CRM admin API (prompt 14). Same adminToken-bearer idiom as rooms.js.
const BASE_URL = import.meta.env.VITE_API_URL || "http://127.0.0.1:8000";

function getAuthHeader() {
  const token = localStorage.getItem("adminToken");
  return {
    "Content-Type": "application/json",
    Authorization: `Bearer ${token}`,
  };
}

async function handle(res, fallback) {
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || fallback);
  }
  return res.json();
}

export async function listGuests({ q = "", vip = null, blacklist = null } = {}) {
  const params = new URLSearchParams();
  if (q) params.set("q", q);
  if (vip) params.set("vip", "true");
  if (blacklist) params.set("blacklist", "true");
  const res = await fetch(`${BASE_URL}/crm/guests?${params.toString()}`, { headers: getAuthHeader() });
  return handle(res, "Failed to load guests");
}

export async function getGuest(id) {
  const res = await fetch(`${BASE_URL}/crm/guests/${id}`, { headers: getAuthHeader() });
  return handle(res, "Failed to load guest");
}

export async function getGuestHistory(id) {
  const res = await fetch(`${BASE_URL}/crm/guests/${id}/history`, { headers: getAuthHeader() });
  return handle(res, "Failed to load history");
}

export async function getGuestLoyalty(id) {
  const res = await fetch(`${BASE_URL}/crm/guests/${id}/loyalty`, { headers: getAuthHeader() });
  return handle(res, "Failed to load loyalty");
}

export async function updateGuest(id, data) {
  const res = await fetch(`${BASE_URL}/crm/guests/${id}`, {
    method: "PUT",
    headers: getAuthHeader(),
    body: JSON.stringify(data),
  });
  return handle(res, "Failed to save guest");
}

export async function setVip(id, vip) {
  const res = await fetch(`${BASE_URL}/crm/guests/${id}/vip`, {
    method: "POST",
    headers: getAuthHeader(),
    body: JSON.stringify({ vip }),
  });
  return handle(res, "Failed to update VIP");
}

export async function setBlacklist(id, blacklist, reason) {
  const res = await fetch(`${BASE_URL}/crm/guests/${id}/blacklist`, {
    method: "POST",
    headers: getAuthHeader(),
    body: JSON.stringify({ blacklist, reason }),
  });
  return handle(res, "Failed to update blacklist");
}

export async function getCrmConfig() {
  const res = await fetch(`${BASE_URL}/crm/config`, { headers: getAuthHeader() });
  return handle(res, "Failed to load config");
}

// PUT /crm/config takes query params (loyalty_enabled, loyalty_points_per_rupee,
// loyalty_rupee_per_point, blacklist_enforcement). Only send the keys you want to change.
export async function updateCrmConfig(params = {}) {
  const qs = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => { if (v !== undefined && v !== null) qs.set(k, v); });
  const res = await fetch(`${BASE_URL}/crm/config?${qs.toString()}`, {
    method: "PUT", headers: getAuthHeader(),
  });
  return handle(res, "Failed to save config");
}
