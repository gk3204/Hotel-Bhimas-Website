// Back-office API — corporate billing, vendors/AMC, roster & attendance (prompt 18 slices 7/13/12/9).
// One module for the three screens: they share the auth/handle/blob idiom from compliance.js + reports.js.
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
      /* ignore */
    }
    throw new Error(msg);
  }
  return res.json();
}

function query(params = {}) {
  const search = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== "") search.set(k, v);
  });
  const q = search.toString();
  return q ? `?${q}` : "";
}

const req = (path, { method = "GET", body, params } = {}) =>
  fetch(`${BASE_URL}${path}${query(params)}`, {
    method,
    headers: authHeaders(),
    ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
  }).then(handle);

// Shared blob download (CSV / PDF), same as compliance.exportCompliance.
export async function download(path, params = {}) {
  const res = await fetch(`${BASE_URL}${path}${query(params)}`, { headers: authHeaders() });
  if (!res.ok) {
    let msg = `Export failed (HTTP ${res.status})`;
    try {
      const data = await res.json();
      if (data?.detail) msg = String(data.detail);
    } catch {
      /* binary/empty body */
    }
    throw new Error(msg);
  }
  const blob = await res.blob();
  const cd = res.headers.get("Content-Disposition") || "";
  const match = cd.match(/filename="?([^"]+)"?/);
  const filename = match ? match[1] : `${path.replace(/\//g, "_").slice(1)}.${params.format || "csv"}`;
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

/* ------------------------------------------------------------------ companies (slice 7) */
export const listCompanies = (params) => req("/companies/", { params });
export const getCompany = (id) => req(`/companies/${id}`);
export const createCompany = (body) => req("/companies/", { method: "POST", body });
export const updateCompany = (id, body) => req(`/companies/${id}`, { method: "PUT", body });
export const deactivateCompany = (id) => req(`/companies/${id}`, { method: "DELETE" });

export const getCompanyLedger = (id, params) =>
  req(`/companies/${id}/ledger`, { params: { ...params, format: "json" } });
export const exportCompanyLedger = (id, params, format) =>
  download(`/companies/${id}/ledger`, { ...params, format });

export const recordCompanyPayment = (id, body) =>
  req(`/companies/${id}/payment`, { method: "POST", body });
export const recordCompanyAdjustment = (id, body) =>
  req(`/companies/${id}/adjustment`, { method: "POST", body });

export const previewCompanyInvoice = (id, params) =>
  req(`/companies/${id}/invoice/preview`, { params });
export const createCompanyInvoice = (id, body) =>
  req(`/companies/${id}/invoice`, { method: "POST", body });
export const listCompanyInvoices = (id) => req(`/companies/${id}/invoices`);
export const downloadCompanyInvoice = (cinvoiceId) =>
  download(`/company-invoices/${cinvoiceId}/pdf`, { format: "pdf" });
export const emailCompanyInvoice = (cinvoiceId) =>
  req(`/company-invoices/${cinvoiceId}/email`, { method: "POST" });
export const cancelCompanyInvoice = (cinvoiceId, reason) =>
  req(`/company-invoices/${cinvoiceId}/cancel`, { method: "POST", params: { reason } });

/* ------------------------------------------------------------------ config (shared) */
export const getBackofficeConfig = () => req("/companies/config");
export const updateBackofficeConfig = (body) => req("/companies/config", { method: "PUT", body });

/* ------------------------------------------------------------------ vendors (slice 13) */
export const listVendors = (params) => req("/vendors/", { params });
export const getVendor = (id) => req(`/vendors/${id}`);
export const createVendor = (body) => req("/vendors/", { method: "POST", body });
export const updateVendor = (id, body) => req(`/vendors/${id}`, { method: "PUT", body });
export const deactivateVendor = (id) => req(`/vendors/${id}`, { method: "DELETE" });

export const listContracts = (vendorId) => req(`/vendors/${vendorId}/contracts`);
export const createContract = (vendorId, body) =>
  req(`/vendors/${vendorId}/contracts`, { method: "POST", body });
export const updateContract = (contractId, body) =>
  req(`/vendors/contracts/${contractId}`, { method: "PUT", body });
export const renewContract = (contractId, newEndDate, amount) =>
  req(`/vendors/contracts/${contractId}/renew`, {
    method: "POST",
    params: { new_end_date: newEndDate, ...(amount ? { amount } : {}) },
  });
export const cancelContract = (contractId, reason) =>
  req(`/vendors/contracts/${contractId}`, { method: "DELETE", params: { reason } });

export const getRenewals = (withinDays = 60) =>
  req("/vendors/renewals", { params: { within_days: withinDays } });
export const runRenewalReminders = () => req("/vendors/reminders/run", { method: "POST" });

/* ------------------------------------------------------------------ roster + attendance (slices 12/9) */
export const listStaff = () => req("/roster/staff");
export const getRoster = (weekStart) => req("/roster/", { params: { week_start: weekStart } });
export const createShift = (body) => req("/roster/shifts", { method: "POST", body });
export const updateShift = (id, body) => req(`/roster/shifts/${id}`, { method: "PUT", body });
export const deleteShift = (id) => req(`/roster/shifts/${id}`, { method: "DELETE" });
export const copyRosterWeek = (body) => req("/roster/bulk", { method: "POST", body });
export const getCoverage = (weekStart) => req("/roster/coverage", { params: { week_start: weekStart } });

export const listAttendance = (params) => req("/roster/attendance", { params });
export const getOnDuty = () => req("/roster/attendance/on-duty");
export const recordAttendance = (body) => req("/roster/attendance/manual", { method: "POST", body });
export const assignStaffCard = (userId, cardUid) =>
  req(`/roster/staff/${userId}/card`, { method: "PUT", body: { card_uid: cardUid || null } });

/* ------------------------------------------------------------------ staff performance (slice 9) */
export const getStaffPerformance = (params) =>
  req("/reports/staff-performance", { params: { ...params, format: "json" } });
export const exportStaffPerformance = (params, format) =>
  download("/reports/staff-performance", { ...params, format });
