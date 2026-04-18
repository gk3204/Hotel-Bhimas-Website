//import { BASE_URL } from "./config";
const BASE_URL = import.meta.env.VITE_API_URL || "http://127.0.0.1:8000";

// ADMIN LOGIN using fetch
export async function adminLogin(username, password) {
  try {
    const response = await fetch(`${BASE_URL}/admin/login`, {
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
// ADMIN CANCEL BOOKING with refund
export async function adminCancelBooking(bookingId, reason, refundAmount, adminNotes = null) {
  try {
    const token = localStorage.getItem("adminToken");
    
    if (!token) {
      throw new Error("Admin not authenticated");
    }

    const requestBody = {
      reason,
      refund_amount: refundAmount,
      admin_notes: adminNotes || null
    };

    console.log("📤 Sending admin cancel request:", requestBody);

    const response = await fetch(`${BASE_URL}/bookings/${bookingId}/admin-cancel`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Authorization": `Bearer ${token}`
      },
      body: JSON.stringify(requestBody)
    });

    if (!response.ok) {
      let errorMsg = `HTTP ${response.status}`;
      try {
        const errorData = await response.json();
        console.log("Backend error response:", errorData);
        if (errorData?.detail) {
          errorMsg = String(errorData.detail);
        }
      } catch (parseErr) {
        console.log("Could not parse error response");
        errorMsg = `HTTP ${response.status}: ${response.statusText}`;
      }
      console.error("API Error:", errorMsg);
      const error = new Error(errorMsg);
      throw error;
    }

    return await response.json();
  } catch (err) {
    const msg = (err && err.message) ? String(err.message) : String(err);
    console.error("Admin cancel booking error:", msg);
    throw new Error(msg);
  }
}