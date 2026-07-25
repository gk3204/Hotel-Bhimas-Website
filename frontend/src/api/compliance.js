// India compliance / Tally / Form-C API (prompt 18). Same authHeaders/handle/blob idiom as reports.js.
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

function query({ from, to, ...rest } = {}) {
  const params = new URLSearchParams();
  if (from) params.set("from", from);
  if (to) params.set("to", to);
  Object.entries(rest).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== "") params.set(k, v);
  });
  const q = params.toString();
  return q ? `?${q}` : "";
}

async function getJson(path, params) {
  const res = await fetch(`${BASE_URL}/compliance/${path}${query(params)}`, {
    headers: authHeaders(),
  });
  return handle(res);
}

// ---- JSON getters ----
export const getPoliceRegister = (p) => getJson("police-register", { ...p, format: "json" });
export const getFormC = (p) => getJson("form-c", { ...p, format: "json" });
export const getEvacuation = () => getJson("evacuation", { format: "json" });
export const getIdAccessAudit = (p) => getJson("id-access-audit", { ...p, format: "json" });
export const getTallyJson = (p) => getJson("tally/export", { ...p, format: "json" });
export const getComplianceConfig = () => getJson("config");

export async function updateComplianceConfig(payload) {
  const res = await fetch(`${BASE_URL}/compliance/config`, {
    method: "PUT",
    headers: authHeaders(),
    body: JSON.stringify(payload),
  });
  return handle(res);
}

// ---- blob download (CSV / PDF / Tally XML) ----
export async function exportCompliance(path, params, format) {
  const res = await fetch(`${BASE_URL}/compliance/${path}${query({ ...params, format })}`, {
    headers: authHeaders(),
  });
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
