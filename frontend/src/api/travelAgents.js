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

// ---- Agents ----
export async function getAgents(active) {
  const q = active === undefined ? "" : `?active=${active}`;
  const res = await fetch(`${BASE_URL}/agents/${q}`, { headers: authHeaders() });
  return handle(res);
}

export async function createAgent(data) {
  const res = await fetch(`${BASE_URL}/agents/`, {
    method: "POST",
    headers: authHeaders(),
    body: JSON.stringify(data),
  });
  return handle(res);
}

export async function updateAgent(id, data) {
  const res = await fetch(`${BASE_URL}/agents/${id}`, {
    method: "PUT",
    headers: authHeaders(),
    body: JSON.stringify(data),
  });
  return handle(res);
}

export async function toggleAgent(id, is_active) {
  const res = await fetch(`${BASE_URL}/agents/${id}/toggle`, {
    method: "PATCH",
    headers: authHeaders(),
    body: JSON.stringify({ is_active }),
  });
  return handle(res);
}

export async function deleteAgent(id) {
  const res = await fetch(`${BASE_URL}/agents/${id}`, {
    method: "DELETE",
    headers: authHeaders(),
  });
  return handle(res);
}

// ---- Negotiated rate cards ----
export async function getAgentRates(agentId) {
  const res = await fetch(`${BASE_URL}/agents/${agentId}/rates`, { headers: authHeaders() });
  return handle(res);
}

export async function createAgentRate(agentId, data) {
  const res = await fetch(`${BASE_URL}/agents/${agentId}/rates`, {
    method: "POST",
    headers: authHeaders(),
    body: JSON.stringify(data),
  });
  return handle(res);
}

export async function updateAgentRate(rateId, data) {
  const res = await fetch(`${BASE_URL}/agents/rates/${rateId}`, {
    method: "PUT",
    headers: authHeaders(),
    body: JSON.stringify(data),
  });
  return handle(res);
}

export async function deleteAgentRate(rateId) {
  const res = await fetch(`${BASE_URL}/agents/rates/${rateId}`, {
    method: "DELETE",
    headers: authHeaders(),
  });
  return handle(res);
}
