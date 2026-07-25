// Google Review Auto-Reply API (prompt 20). Same authHeaders/handle idiom as reports.js.
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

function query(params = {}) {
  const p = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== "") p.set(k, v);
  });
  const q = p.toString();
  return q ? `?${q}` : "";
}

export async function listReviews(filters = {}) {
  const res = await fetch(`${BASE_URL}/reviews/${query(filters)}`, { headers: authHeaders() });
  return handle(res);
}

export async function getReview(id) {
  const res = await fetch(`${BASE_URL}/reviews/${id}`, { headers: authHeaders() });
  return handle(res);
}

export async function approveReview(id, replyText) {
  const res = await fetch(`${BASE_URL}/reviews/${id}/approve`, {
    method: "POST",
    headers: authHeaders(),
    body: JSON.stringify({ reply_text: replyText ?? null }),
  });
  return handle(res);
}

export async function skipReview(id, reason) {
  const res = await fetch(`${BASE_URL}/reviews/${id}/skip`, {
    method: "POST",
    headers: authHeaders(),
    body: JSON.stringify({ reason: reason ?? null }),
  });
  return handle(res);
}

export async function regenerateReply(id) {
  const res = await fetch(`${BASE_URL}/reviews/${id}/regenerate`, {
    method: "POST",
    headers: authHeaders(),
  });
  return handle(res);
}

export async function getReviewConfig() {
  const res = await fetch(`${BASE_URL}/reviews/config`, { headers: authHeaders() });
  return handle(res);
}

export async function updateReviewConfig(body) {
  const res = await fetch(`${BASE_URL}/reviews/config`, {
    method: "PUT",
    headers: authHeaders(),
    body: JSON.stringify(body),
  });
  return handle(res);
}

export async function pollReviews() {
  const res = await fetch(`${BASE_URL}/reviews/poll`, { method: "POST", headers: authHeaders() });
  return handle(res);
}

export async function injectTestReview(body) {
  const res = await fetch(`${BASE_URL}/reviews/_test/inject`, {
    method: "POST",
    headers: authHeaders(),
    body: JSON.stringify(body),
  });
  return handle(res);
}

export const getReviewTrends = async () => {
  const res = await fetch(`${BASE_URL}/reviews/trends/weekly`, { headers: authHeaders() });
  return handle(res);
};
