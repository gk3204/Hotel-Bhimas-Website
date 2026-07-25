// WhatsApp & automated alerts admin API (prompt 15). Same authHeaders/handle idiom as cashShift.js/fraud.js.
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
    } catch { /* ignore parse errors */ }
    throw new Error(msg);
  }
  return res.json();
}

export async function getWhatsappConfig() {
  const res = await fetch(`${BASE_URL}/whatsapp/config`, { headers: authHeaders() });
  return handle(res);
}

export async function updateWhatsappConfig(data) {
  const res = await fetch(`${BASE_URL}/whatsapp/config`, {
    method: "PUT", headers: authHeaders(), body: JSON.stringify(data),
  });
  return handle(res);
}

export async function getTemplates() {
  const res = await fetch(`${BASE_URL}/whatsapp/templates`, { headers: authHeaders() });
  return handle(res);
}

export async function getMessages({ status, direction, limit = 100 } = {}) {
  const params = new URLSearchParams();
  if (status) params.set("status", status);
  if (direction) params.set("direction", direction);
  if (limit) params.set("limit", limit);
  const q = params.toString();
  const res = await fetch(`${BASE_URL}/whatsapp/messages${q ? `?${q}` : ""}`, { headers: authHeaders() });
  return handle(res);
}

export async function getOptOuts() {
  const res = await fetch(`${BASE_URL}/whatsapp/opt-outs`, { headers: authHeaders() });
  return handle(res);
}

export async function addOptOut(phone, reason) {
  const res = await fetch(`${BASE_URL}/whatsapp/opt-outs`, {
    method: "POST", headers: authHeaders(), body: JSON.stringify({ phone, reason }),
  });
  return handle(res);
}

export async function removeOptOut(phone) {
  const res = await fetch(`${BASE_URL}/whatsapp/opt-outs/${encodeURIComponent(phone)}`, {
    method: "DELETE", headers: authHeaders(),
  });
  return handle(res);
}

export async function runJobs() {
  const res = await fetch(`${BASE_URL}/whatsapp/jobs/run`, { method: "POST", headers: authHeaders() });
  return handle(res);
}

export async function sendTest(data) {
  const res = await fetch(`${BASE_URL}/whatsapp/send-test`, {
    method: "POST", headers: authHeaders(), body: JSON.stringify(data),
  });
  return handle(res);
}
