const BASE_URL = import.meta.env.VITE_API_URL || "http://127.0.0.1:8000";

function getAuthHeader() {
  const token = localStorage.getItem("adminToken");
  return {
    "Content-Type": "application/json",
    Authorization: `Bearer ${token}`,
  };
}

export async function getRooms() {
  const res = await fetch(`${BASE_URL}/rooms/`, { headers: getAuthHeader() });
  if (!res.ok) throw new Error("Failed to fetch rooms");
  return res.json();
}

export async function createRoom(data) {
  const res = await fetch(`${BASE_URL}/rooms/`, {
    method: "POST",
    headers: getAuthHeader(),
    body: JSON.stringify({
      room_number: String(data.room_number).trim(),
      room_type_id: parseInt(data.room_type_id),
      building: parseInt(data.building) || 1,
      floor: parseInt(data.floor) || 1,
      max_cards: parseInt(data.max_cards) || 4,
      lock_no: data.lock_no ? String(data.lock_no).trim() : null,
      is_active: data.is_active !== undefined ? data.is_active : true,
      status: data.status || "vacant",
    }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || "Create failed");
  }
  return res.json();
}

export async function updateRoom(id, data) {
  const res = await fetch(`${BASE_URL}/rooms/${id}`, {
    method: "PUT",
    headers: getAuthHeader(),
    body: JSON.stringify(data),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || "Update failed");
  }
  return res.json();
}

export async function setRoomStatus(id, status) {
  const res = await fetch(`${BASE_URL}/rooms/${id}/status`, {
    method: "PATCH",
    headers: getAuthHeader(),
    body: JSON.stringify({ status }),
  });
  if (!res.ok) throw new Error("Status update failed");
  return res.json();
}

export async function toggleRoomActive(id, is_active) {
  const res = await fetch(`${BASE_URL}/rooms/${id}/active`, {
    method: "PATCH",
    headers: getAuthHeader(),
    body: JSON.stringify({ is_active }),
  });
  if (!res.ok) throw new Error("Toggle failed");
  return res.json();
}

export async function deleteRoom(id) {
  const res = await fetch(`${BASE_URL}/rooms/${id}`, {
    method: "DELETE",
    headers: getAuthHeader(),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || "Delete failed");
  }
  return res.json();
}
