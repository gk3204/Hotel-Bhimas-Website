//import { BASE_URL } from "./config";

// ADMIN LOGIN using fetch
export async function adminLogin(username, password) {
  try {
    const response = await fetch(`http://127.0.0.1:8000/admin/login`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        username,
        password,
      }),
    });

    if (!response.ok) {
      throw new Error("Invalid credentials");
    }

    return await response.json(); // { access_token }
  } catch (err) {
    console.error("Admin login failed:", err);
    throw err;
  }
}
