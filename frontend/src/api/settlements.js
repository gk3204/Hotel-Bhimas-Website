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

function range(from, to) {
  const params = new URLSearchParams();
  if (from) params.set("from", from);
  if (to) params.set("to", to);
  const q = params.toString();
  return q ? `?${q}` : "";
}

// Per-agent settlement summary (booked, commission, paid, outstanding)
export async function getSettlement(from, to) {
  const res = await fetch(`${BASE_URL}/agents/settlement${range(from, to)}`, {
    headers: authHeaders(),
  });
  return handle(res);
}

// One agent's detail (bookings + payouts)
export async function getAgentSettlement(agentId, from, to) {
  const res = await fetch(`${BASE_URL}/agents/${agentId}/settlement${range(from, to)}`, {
    headers: authHeaders(),
  });
  return handle(res);
}

export async function recordPayout(agentId, data) {
  const res = await fetch(`${BASE_URL}/agents/${agentId}/payments`, {
    method: "POST",
    headers: authHeaders(),
    body: JSON.stringify(data),
  });
  return handle(res);
}

// Settle several agents' outstanding balances at once. data = { items:[{agent_id, amount}], paid_on, mode }
export async function recordBulkPayouts(data) {
  const res = await fetch(`${BASE_URL}/agents/payments/bulk`, {
    method: "POST",
    headers: authHeaders(),
    body: JSON.stringify(data),
  });
  return handle(res);
}
