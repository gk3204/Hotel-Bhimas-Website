const BASE_URL = import.meta.env.VITE_API_URL || "http://127.0.0.1:8000";

export async function getRoomTypes() {
  const res = await fetch(`${BASE_URL}/room-types/`);
  if (!res.ok) throw new Error("Failed to fetch");
  return res.json();
}

export async function createRoomType(data) {
  const res = await fetch(
    `${BASE_URL}/room-types/`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: data.name,
        price_per_night: parseFloat(data.price),
        gst_percent: parseFloat(data.gst),
        max_occupancy: parseInt(data.occupancy),
        total_rooms: parseInt(data.total_rooms) || 1,
      }),
    }
  );

  if (!res.ok) throw new Error("Create failed");
  return res.json();
}

export async function toggleRoomType(id, is_active) {
  const res = await fetch(`${BASE_URL}/room-types/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ is_active }),
  });

  if (!res.ok) throw new Error("Toggle failed");
  return res.json();
}

export async function updateRoomType(id, data) {
  const res = await fetch(`${BASE_URL}/room-types/${id}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });

  if (!res.ok) throw new Error("Update failed");
  return res.json();
}
