// Documents API (v5j) — the admin index of every generated PDF (invoices, receipts, refund
// vouchers, registration slips, booking confirmations, company invoices, shift reports).
// Same auth/handle/blob idiom as backoffice.js; `download` is reused from there.
import { download } from "./backoffice";

const BASE_URL = import.meta.env.VITE_API_URL || "http://127.0.0.1:8000";

function authHeaders() {
  const token = localStorage.getItem("adminToken");
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function handle(res) {
  if (!res.ok) {
    let msg = `HTTP ${res.status}`;
    try {
      const data = await res.json();
      if (data?.detail) msg = Array.isArray(data.detail) ? data.detail.map((e) => e.msg || e.type).join(". ") : String(data.detail);
    } catch { /* ignore */ }
    throw new Error(msg);
  }
  return res.json();
}

function query(params = {}) {
  const s = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => { if (v !== undefined && v !== null && v !== "") s.set(k, v); });
  const q = s.toString();
  return q ? `?${q}` : "";
}

/** { total, data: [{type, number, date, booking_id, guest, phone, room, amount, status, pdf_url}], types } */
export const listDocuments = (params) =>
  fetch(`${BASE_URL}/documents${query(params)}`, { headers: authHeaders() }).then(handle);

/** Fetch a document's PDF with the admin token and return an object URL for an <iframe> preview.
 *  Caller revokes it (URL.revokeObjectURL) when the preview closes. */
export async function documentObjectUrl(pdfUrl) {
  const res = await fetch(`${BASE_URL}${pdfUrl}`, { headers: authHeaders() });
  if (!res.ok) {
    let msg = `Could not open this document (HTTP ${res.status})`;
    try {
      const data = await res.json();
      if (data?.detail) msg = String(data.detail);
    } catch { /* binary / empty */ }
    throw new Error(msg);
  }
  const blob = await res.blob();
  return URL.createObjectURL(blob);
}

/** Save the PDF — the server's Content-Disposition names the file (receipt-12.pdf, shift_report_3.pdf …). */
export const downloadDocument = (pdfUrl) => {
  // `download` appends `?…` itself; split a pre-built query so both parts survive.
  const [path, qs] = pdfUrl.split("?");
  const params = Object.fromEntries(new URLSearchParams(qs || ""));
  return download(path, { ...params, format: "pdf" });
};
