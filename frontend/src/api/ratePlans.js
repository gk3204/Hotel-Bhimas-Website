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

export async function getRatePlans(roomTypeId) {
  const res = await fetch(`${BASE_URL}/room-types/${roomTypeId}/rate-plans`, {
    headers: authHeaders(),
  });
  return handle(res);
}

export async function createRatePlan(roomTypeId, data) {
  const res = await fetch(`${BASE_URL}/room-types/${roomTypeId}/rate-plans`, {
    method: "POST",
    headers: authHeaders(),
    body: JSON.stringify(data),
  });
  return handle(res);
}

export async function updateRatePlan(planId, data) {
  const res = await fetch(`${BASE_URL}/room-types/rate-plans/${planId}`, {
    method: "PUT",
    headers: authHeaders(),
    body: JSON.stringify(data),
  });
  return handle(res);
}

export async function deleteRatePlan(planId) {
  const res = await fetch(`${BASE_URL}/room-types/rate-plans/${planId}`, {
    method: "DELETE",
    headers: authHeaders(),
  });
  return handle(res);
}

// Resolved-price preview (same resolver the desk booking uses)
export async function getQuote(roomTypeId, { check_in, check_out, channel, agent_id, quantity }) {
  const params = new URLSearchParams({ check_in, check_out, channel });
  if (agent_id) params.set("agent_id", agent_id);
  if (quantity) params.set("quantity", quantity);
  const res = await fetch(`${BASE_URL}/room-types/${roomTypeId}/quote?${params}`, {
    headers: authHeaders(),
  });
  return handle(res);
}
