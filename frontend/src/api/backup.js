// Database backup (v4b10, R11). Admin only — every call here is audited server-side.
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
    let detail = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      detail = body.detail || detail;
    } catch { /* keep the status */ }
    throw new Error(detail);
  }
  return res.json();
}

/** Is the nightly job still running? `stale` is true once a night has been missed. */
export async function getBackupStatus() {
  return handle(await fetch(`${BASE_URL}/admin/backup/status`, { headers: authHeaders() }));
}

/** Row counts + server version as of now — what a dump taken right now would contain. */
export async function getBackupManifest() {
  return handle(await fetch(`${BASE_URL}/admin/backup/manifest`, { headers: authHeaders() }));
}

/**
 * Download a dump through the browser.
 *
 * Deliberately fetch-then-blob rather than pointing the browser at the URL: the endpoint needs
 * an Authorization header, which a plain link or window.open cannot send. The whole file lands
 * in memory here, which is fine for a hand-pulled copy — the NIGHTLY copy is the CLI's job, and
 * it streams to disk.
 */
export async function downloadBackupNow() {
  const res = await fetch(`${BASE_URL}/admin/backup/export`, { headers: authHeaders() });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      detail = body.detail || detail;
    } catch { /* keep the status */ }
    throw new Error(detail);
  }
  const sha = res.headers.get("X-Backup-Sha256");
  const blob = await res.blob();
  const name =
    (res.headers.get("Content-Disposition") || "").match(/filename="?([^";]+)"?/)?.[1] ||
    `bhimas-backup-${new Date().toISOString().slice(0, 10)}.dump`;

  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
  return { name, bytes: blob.size, sha256: sha };
}
