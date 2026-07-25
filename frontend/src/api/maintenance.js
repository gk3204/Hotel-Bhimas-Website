// Maintenance-ticket API (prompt 13) — shared by the staff PWA (maintenance) and admin board.
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

export async function getTickets({ status, category, assignee, overdue } = {}) {
  const params = new URLSearchParams();
  if (status) params.set("status", status);
  if (category) params.set("category", category);
  if (assignee != null) params.set("assignee", assignee);
  if (overdue) params.set("overdue", "true");
  const qs = params.toString();
  const res = await fetch(`${BASE_URL}/maintenance/tickets${qs ? `?${qs}` : ""}`, { headers: authHeaders() });
  return handle(res);
}

export async function getTicket(id) {
  const res = await fetch(`${BASE_URL}/maintenance/tickets/${id}`, { headers: authHeaders() });
  return handle(res);
}

export async function createTicket(data) {
  const res = await fetch(`${BASE_URL}/maintenance/tickets`, {
    method: "POST",
    headers: authHeaders(),
    body: JSON.stringify(data),
  });
  return handle(res);
}

export async function assignTicket(id, data) {
  const res = await fetch(`${BASE_URL}/maintenance/tickets/${id}/assign`, {
    method: "POST",
    headers: authHeaders(),
    body: JSON.stringify(data),
  });
  return handle(res);
}

export async function updateTicketStatus(id, data) {
  const res = await fetch(`${BASE_URL}/maintenance/tickets/${id}/status`, {
    method: "POST",
    headers: authHeaders(),
    body: JSON.stringify(data),
  });
  return handle(res);
}

export async function verifyTicket(id, data = {}) {
  const res = await fetch(`${BASE_URL}/maintenance/tickets/${id}/verify`, {
    method: "POST",
    headers: authHeaders(),
    body: JSON.stringify(data),
  });
  return handle(res);
}

export async function addTicketItem(id, data) {
  const res = await fetch(`${BASE_URL}/maintenance/tickets/${id}/items`, {
    method: "POST",
    headers: authHeaders(),
    body: JSON.stringify(data),
  });
  return handle(res);
}

export async function approveItem(itemId) {
  const res = await fetch(`${BASE_URL}/maintenance/items/${itemId}/approve`, {
    method: "POST",
    headers: authHeaders(),
  });
  return handle(res);
}

export async function purchaseItem(itemId, data) {
  const res = await fetch(`${BASE_URL}/maintenance/items/${itemId}/purchase`, {
    method: "POST",
    headers: authHeaders(),
    body: JSON.stringify(data),
  });
  return handle(res);
}

// Assignable maintenance staff (admin board dropdown). Uses the admin users endpoint.
export async function getMaintenanceStaff() {
  const res = await fetch(`${BASE_URL}/users/`, { headers: authHeaders() });
  const users = await handle(res);
  return users.filter((u) => u.role === "maintenance" && u.is_active !== false);
}
