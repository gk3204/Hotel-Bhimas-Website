const BASE_URL = import.meta.env.VITE_API_URL || "http://127.0.0.1:8000";

// The WRITES need an admin token. `/room-types/` POST/PUT/PATCH were unauthenticated until the
// 2026-09-25 security fix guarded them with require_admin; this module was never updated, so
// every add / edit / toggle on the admin page came back 403 (FastAPI's HTTPBearer answers a
// MISSING Authorization header with 403, not 401 - which is why it reads as a permission problem
// rather than a login one). Same shape as every other admin api module.
function authHeaders() {
  const token = localStorage.getItem("adminToken");
  return {
    "Content-Type": "application/json",
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  };
}

// Admin/desk call this with no args to get every type; the public site passes
// { websiteOnly: true } so only active, website-visible types come back.
export async function getRoomTypes({ websiteOnly = false } = {}) {
  const qs = websiteOnly ? "?website_only=true" : "";
  const res = await fetch(`${BASE_URL}/room-types/${qs}`);
  if (!res.ok) throw new Error("Failed to fetch");
  return res.json();
}

export async function createRoomType(data) {
  const res = await fetch(
    `${BASE_URL}/room-types/`,
    {
      method: "POST",
      headers: authHeaders(),
      body: JSON.stringify({
        name: data.name,
        price_per_night: parseFloat(data.price),
        gst_percent: parseFloat(data.gst),
        max_occupancy: parseInt(data.occupancy),
        total_rooms: parseInt(data.total_rooms) || 1,
        is_ac: !!data.is_ac,
        show_on_website: data.show_on_website !== false,
      }),
    }
  );

  if (!res.ok) throw new Error("Create failed");
  return res.json();
}

export async function toggleRoomType(id, is_active) {
  const res = await fetch(`${BASE_URL}/room-types/${id}`, {
    method: "PATCH",
    headers: authHeaders(),
    body: JSON.stringify({ is_active }),
  });

  if (!res.ok) throw new Error("Toggle failed");
  return res.json();
}

export async function updateRoomType(id, data) {
  const res = await fetch(`${BASE_URL}/room-types/${id}`, {
    method: "PUT",
    headers: authHeaders(),
    body: JSON.stringify(data),
  });

  if (!res.ok) throw new Error("Update failed");
  return res.json();
}
