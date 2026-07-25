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

// Cash-shift config (cycle + variance-alert threshold) — read + admin edit.
export async function getCashConfig() {
  const res = await fetch(`${BASE_URL}/cash-shift/config`, { headers: authHeaders() });
  return handle(res);
}

export async function updateCashConfig(data) {
  const res = await fetch(`${BASE_URL}/cash-shift/config`, {
    method: "PUT",
    headers: authHeaders(),
    body: JSON.stringify(data),
  });
  return handle(res);
}

// Recent shifts (for the owner review table)
export async function getShifts(limit = 50) {
  const res = await fetch(`${BASE_URL}/cash-shift/?limit=${limit}`, { headers: authHeaders() });
  return handle(res);
}
