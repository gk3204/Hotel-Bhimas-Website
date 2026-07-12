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

// Public: active promotions for the booking page to preview discounts
export async function getActivePromotions() {
  const res = await fetch(`${BASE_URL}/promotions/active`);
  return handle(res);
}

// Admin
export async function getPromotions() {
  const res = await fetch(`${BASE_URL}/promotions/`, { headers: authHeaders() });
  return handle(res);
}

export async function createPromotion(data) {
  const res = await fetch(`${BASE_URL}/promotions/`, {
    method: "POST",
    headers: authHeaders(),
    body: JSON.stringify(data),
  });
  return handle(res);
}

export async function updatePromotion(id, data) {
  const res = await fetch(`${BASE_URL}/promotions/${id}`, {
    method: "PUT",
    headers: authHeaders(),
    body: JSON.stringify(data),
  });
  return handle(res);
}

export async function togglePromotion(id, is_active) {
  const res = await fetch(`${BASE_URL}/promotions/${id}/toggle`, {
    method: "PATCH",
    headers: authHeaders(),
    body: JSON.stringify({ is_active }),
  });
  return handle(res);
}

export async function deletePromotion(id) {
  const res = await fetch(`${BASE_URL}/promotions/${id}`, {
    method: "DELETE",
    headers: authHeaders(),
  });
  return handle(res);
}
