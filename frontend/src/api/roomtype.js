// GET all room types
// Aftr deploytment --> http://localhost:8000/room-types
export async function getRoomTypes() {
  try {
    const response = await fetch("http://127.0.0.1:8000/room-types", {
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



