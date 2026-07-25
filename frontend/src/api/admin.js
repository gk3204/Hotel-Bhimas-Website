//import { BASE_URL } from "./config";
const BASE_URL = import.meta.env.VITE_API_URL || "http://127.0.0.1:8000";

function authHeader() {
  const token = localStorage.getItem("adminToken");
  return { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) };
}

async function detailOrThrow(response, fallback) {
  if (!response.ok) {
    let msg = fallback || `HTTP ${response.status}`;
    try {
      const data = await response.json();
      if (data?.detail) msg = String(data.detail);
    } catch {
      /* ignore */
    }
    throw new Error(msg);
  }
  return response.json();
}

// ADMIN LOGIN using fetch. Returns { access_token } OR { twofa_required, challenge } when the
// account has TOTP 2FA enabled (prompt 18) — surfaces the backend detail (e.g. deactivated).
export async function adminLogin(username, password) {
  const response = await fetch(`${BASE_URL}/admin/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  return detailOrThrow(response, "Invalid credentials");
}

// Second factor: exchange the password-stage challenge + TOTP code for an access token.
export async function verifyLogin2fa(challenge, code) {
  const response = await fetch(`${BASE_URL}/admin/login/2fa`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ challenge, code }),
  });
  return detailOrThrow(response, "Invalid authentication code");
}

// --- 2FA management (authenticated admin) ---
export async function twofaStatus() {
  return detailOrThrow(await fetch(`${BASE_URL}/admin/2fa/status`, { headers: authHeader() }));
}
export async function twofaEnroll() {
  return detailOrThrow(await fetch(`${BASE_URL}/admin/2fa/enroll`, { method: "POST", headers: authHeader() }));
}
export async function twofaConfirm(code) {
  return detailOrThrow(await fetch(`${BASE_URL}/admin/2fa/confirm`, {
    method: "POST", headers: authHeader(), body: JSON.stringify({ code }) }));
}
export async function twofaDisable(code) {
  return detailOrThrow(await fetch(`${BASE_URL}/admin/2fa/disable`, {
    method: "POST", headers: authHeader(), body: JSON.stringify({ code }) }));
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