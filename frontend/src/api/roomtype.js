// GET all room types
// After deployment --> use environment variable
const BASE_URL = import.meta.env.VITE_API_URL || "http://127.0.0.1:8000";

export async function getRoomTypes() {
  try {
    const response = await fetch(`${BASE_URL}/room-types`, {
      method: "GET",
    });

    if (!response.ok) {
      throw new Error("Failed to fetch room types");
    }

    return await response.json();
  } catch (err) {
    console.error("Error fetching room types:", err);
    throw err;
  }
}



