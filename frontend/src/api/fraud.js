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

// Fraud alerts (optionally run the reconciliation sweep first)
export async function getAlerts({ status, type, severity, reconcile } = {}) {
  const params = new URLSearchParams();
  if (status) params.set("status", status);
  if (type) params.set("type", type);
  if (severity) params.set("severity", severity);
  if (reconcile) params.set("reconcile", "true");
  const q = params.toString();
  const res = await fetch(`${BASE_URL}/fraud/alerts${q ? `?${q}` : ""}`, {
    headers: authHeaders(),
  });
  return handle(res);
}

export async function reviewAlert(alertId, data) {
  const res = await fetch(`${BASE_URL}/fraud/alerts/${alertId}/review`, {
    method: "POST",
    headers: authHeaders(),
    body: JSON.stringify(data),
  });
  return handle(res);
}

// Run the detection sweep now
export async function runReconcile() {
  const res = await fetch(`${BASE_URL}/fraud/reconcile`, {
    method: "POST",
    headers: authHeaders(),
  });
  return handle(res);
}

// Card-vs-booking-vs-payment report
export async function getReconciliationReport() {
  const res = await fetch(`${BASE_URL}/fraud/reconciliation-report`, {
    headers: authHeaders(),
  });
  return handle(res);
}

// Owner daily digest
export async function getDigest(day) {
  const q = day ? `?day=${day}` : "";
  const res = await fetch(`${BASE_URL}/fraud/digest${q}`, { headers: authHeaders() });
  return handle(res);
}

// Approvals inbox (pending OTP requests reveal the code = interim on-screen delivery)
export async function getOtps(status = "pending") {
  const res = await fetch(`${BASE_URL}/fraud/otp?status=${status}`, {
    headers: authHeaders(),
  });
  return handle(res);
}

/**
 * Ask for an owner-approval code (v4b0). The code is NEVER returned here — the owner reads
 * it from the Approvals inbox (or receives it) and reads it out to the staff member.
 * `body` = { action, booking_id?, room_ids?, folio_id?, amount?, context? }
 */
export async function requestOtp(body) {
  const res = await fetch(`${BASE_URL}/fraud/otp/request`, {
    method: "POST",
    headers: { ...authHeaders(), "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return handle(res);
}

// Re-deliver a still-live approval code to the owner's WhatsApp (same code, not a new one)
export async function resendOtp(otpId) {
  const res = await fetch(`${BASE_URL}/fraud/otp/${otpId}/resend`, {
    method: "POST",
    headers: authHeaders(),
  });
  return handle(res);
}

// Current detector thresholds / OTP toggles
export async function getConfig() {
  const res = await fetch(`${BASE_URL}/fraud/config`, { headers: authHeaders() });
  return handle(res);
}

// Edit the thresholds + approval gates (backlog v2 FE-12 — these used to be env-only)
export async function updateFraudConfig(body) {
  const res = await fetch(`${BASE_URL}/fraud/config`, {
    method: "PUT",
    headers: authHeaders(),
    body: JSON.stringify(body),
  });
  return handle(res);
}
