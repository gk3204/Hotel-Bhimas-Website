// Guest complaints API (prompt 18c, slice 11). Same auth/handle idiom as api/backoffice.js.
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

export const listComplaints = (params) => req("/complaints/", { params });
export const getComplaint = (id) => req(`/complaints/${id}`);
export const createComplaint = (body) => req("/complaints/", { method: "POST", body });
export const respondComplaint = (id, body) => req(`/complaints/${id}/respond`, { method: "POST", body });
export const escalateComplaint = (id, body) => req(`/complaints/${id}/escalate`, { method: "POST", body });
export const resolveComplaint = (id, body) => req(`/complaints/${id}/resolve`, { method: "POST", body });
export const compensateComplaint = (id, body) => req(`/complaints/${id}/compensate`, { method: "POST", body });

export const runEscalations = () => req("/complaints/escalations/run", { method: "POST" });
export const getComplaintsConfig = () => req("/complaints/config");
export const updateComplaintsConfig = (body) => req("/complaints/config", { method: "PUT", body });
