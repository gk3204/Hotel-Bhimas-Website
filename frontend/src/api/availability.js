const BASE_URL = import.meta.env.VITE_API_URL || "http://127.0.0.1:8000";

function getAuthHeader() {
  const token = localStorage.getItem("adminToken");

  return {
    "Content-Type": "application/json",
    Authorization: `Bearer ${token}`,
  };
}


// Get blocked dates
export async function getBlockedDates(roomTypeId) {
  const res = await fetch(
    `${BASE_URL}/room-type-availability/${roomTypeId}`,
    {
      headers: getAuthHeader(),
    }
  );

  if (!res.ok) throw new Error("Failed to fetch blocked dates");

  return res.json();
}


// Block date
export async function blockDate(data) {
  const res = await fetch(`${BASE_URL}/room-type-availability/block`, {
    method: "POST",
    headers: getAuthHeader(),
    body: JSON.stringify(data),
  });

  if (!res.ok) throw new Error("Block failed");

  return res.json();
}


export async function unblockDate(room_type_id, date) {
  const res = await fetch(
    `${BASE_URL}/room-type-availability/unblock?room_type_id=${room_type_id}&date=${date}`,
    {
      method: "DELETE",
      headers: getAuthHeader(),
    }
  );

  if (!res.ok) throw new Error("Unblock failed");

  return res.json();
}

