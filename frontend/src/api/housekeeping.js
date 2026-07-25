// Housekeeping API (prompt 13) — shared by the staff PWA (housekeeper) and the admin board.
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

// Rooms + current housekeeping status + open cleaning task. `mine` = my/unassigned only.
export async function getRooms(mine = false) {
  const q = mine ? "?mine=true" : "";
  const res = await fetch(`${BASE_URL}/housekeeping/rooms${q}`, { headers: authHeaders() });
  return handle(res);
}

export async function getTasks({ status, mine } = {}) {
  const params = new URLSearchParams();
  if (status) params.set("status", status);
  if (mine) params.set("mine", "true");
  const qs = params.toString();
  const res = await fetch(`${BASE_URL}/housekeeping/tasks${qs ? `?${qs}` : ""}`, { headers: authHeaders() });
  return handle(res);
}

export async function startTask(taskId) {
  const res = await fetch(`${BASE_URL}/housekeeping/tasks/${taskId}/start`, {
    method: "POST",
    headers: authHeaders(),
  });
  return handle(res);
}

export async function completeTask(taskId, data = {}) {
  const res = await fetch(`${BASE_URL}/housekeeping/tasks/${taskId}/complete`, {
    method: "POST",
    headers: authHeaders(),
    body: JSON.stringify(data),
  });
  return handle(res);
}

export async function setRoomStatus(roomId, data) {
  const res = await fetch(`${BASE_URL}/housekeeping/rooms/${roomId}/status`, {
    method: "POST",
    headers: authHeaders(),
    body: JSON.stringify(data),
  });
  return handle(res);
}

// Admin/supervisor only — the inspected gate (room -> re-sellable).
export async function inspectRoom(roomId, data = {}) {
  const res = await fetch(`${BASE_URL}/housekeeping/rooms/${roomId}/inspect`, {
    method: "POST",
    headers: authHeaders(),
    body: JSON.stringify(data),
  });
  return handle(res);
}

export async function minibarRestock(data) {
  const res = await fetch(`${BASE_URL}/housekeeping/minibar-restock`, {
    method: "POST",
    headers: authHeaders(),
    body: JSON.stringify(data),
  });
  return handle(res);
}

export async function getConfig() {
  const res = await fetch(`${BASE_URL}/housekeeping/config`, { headers: authHeaders() });
  return handle(res);
}

export async function updateConfig(data) {
  const res = await fetch(`${BASE_URL}/housekeeping/config`, {
    method: "PUT",
    headers: authHeaders(),
    body: JSON.stringify(data),
  });
  return handle(res);
}
