// Backups card for the Settings hub (v4b10, R11).
//
// The failure mode this exists to prevent is the only one that matters with backups: the job
// quietly stopped running weeks ago and nobody finds out until a restore is needed. So the
// primary content here is not the download button — it is the age of the last successful run,
// stated loudly when it is overdue.
import React, { useCallback, useEffect, useState } from "react";
import { FaDownload, FaSyncAlt } from "react-icons/fa";
import { Card, GhostButton, PrimaryButton, Spinner, fmtBytes, fmtWhen } from "./BackofficeUI";
import {
  downloadBackupNow, getBackupManifest, getBackupRuns, getBackupStatus, getScansStatus,
} from "../../api/backup";
import BackupCustodyPanel from "./BackupCustodyPanel";
import usePoll from "../../utils/usePoll";

export default function BackupsPanel({ showToast }) {
  const [status, setStatus] = useState(null);
  const [manifest, setManifest] = useState(null);
  const [scans, setScans] = useState(null);
  const [runs, setRuns] = useState(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  // One load for both cards. Everything except the DB status is best-effort: an R2 outage or a
  // backend too old to know about /runs must not blank out the "is the nightly job alive?" card,
  // which is the one thing this screen exists to answer.
  const load = useCallback(async (silent = false) => {
    if (!silent) setLoading(true);
    setError("");
    try {
      const [s, m, sc, rr] = await Promise.all([
        getBackupStatus(),
        getBackupManifest().catch(() => null),   // manifest is nice-to-have, status is not
        getScansStatus().catch(() => null),
        getBackupRuns(10).catch(() => null),
      ]);
      setStatus(s);
      setManifest(m);
      setScans(sc);
      setRuns(rr);
    } catch (e) {
      setError(e.message || "Could not read the backup status.");
    } finally {
      if (!silent) setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  // The one moment this screen is actually watched is while a scheduled run is in flight, and
  // seeing it flip to green without pressing Refresh is the point. usePoll already skips hidden
  // tabs and overlapping calls; `silent` keeps the spinner from flashing every minute.
  usePoll(useCallback(() => load(true), [load]), 60000, { enabled: !busy });

  const download = async () => {
    setBusy(true);
    try {
      const r = await downloadBackupNow();
      showToast(`Downloaded ${r.name} (${fmtBytes(r.bytes)}).`, "success");
      await load();
    } catch (e) {
      showToast(e.message || "The backup download failed.", "error");
    } finally {
      setBusy(false);
    }
  };

  const stale = status?.stale;

  return (
    <>
      <Card title="Database backups" className="mb-6">
        <div className="p-5">
          <p className="text-slate-400 text-sm mb-5">
            A complete copy of the database, taken on the server with <code>pg_dump</code> and
            restorable with <code>pg_restore</code>. The nightly copy is pulled to the admin PC by
            the BhimasBackup scheduled task; this button is for taking one by hand before
            something risky. Keep Railway&apos;s own snapshots switched on as well — they cover
            different disasters.
          </p>

          {loading ? (
            <div className="flex items-center gap-3 text-slate-400 text-sm py-3"><Spinner /> Loading…</div>
          ) : error ? (
            <div className="p-4 rounded-lg bg-red-500/10 border border-red-500/30 text-red-300 text-sm">
              {error}
              <button onClick={load} className="ml-3 underline hover:text-red-200">Retry</button>
            </div>
          ) : (
            <>
              <div
                className={`p-4 rounded-xl border mb-5 ${
                  stale
                    ? "bg-red-500/10 border-red-500/40 text-red-200"
                    : "bg-green-500/10 border-green-500/30 text-green-200"
                }`}
              >
                <div className="font-semibold">
                  {stale
                    ? "⚠️ No recent backup — the nightly job may have stopped."
                    : "✅ The nightly backup is running."}
                </div>
                <div className="text-sm mt-1">
                  Last successful backup: <b>{fmtWhen(status?.last_success_at)}</b>
                  {status?.age_hours != null && <> ({status.age_hours} h ago)</>}
                  {status?.last_success_bytes ? <> · {fmtBytes(status.last_success_bytes)}</> : null}
                  {status?.last_success_by ? <> · by {status.last_success_by}</> : null}
                </div>
                {status?.last_success_sha256 && (
                  <div className="text-xs mt-1 font-mono break-all opacity-80">
                    sha256 {status.last_success_sha256}
                  </div>
                )}
                {!status?.pg_dump_available && (
                  <div className="text-sm mt-2">
                    ⚠️ <b>pg_dump is not installed in the backend container</b>, so no backup can
                    be taken at all until the image includes postgresql-client.
                  </div>
                )}
              </div>

              {manifest && (
                <div className="mb-5">
                  <div className="text-slate-300 font-semibold text-sm mb-2">
                    What a backup taken now would contain
                  </div>
                  <div className="flex flex-wrap gap-2">
                    {Object.entries(manifest.row_counts || {}).map(([k, v]) => (
                      <span
                        key={k}
                        className="px-3 py-1 rounded-full text-xs border border-slate-600 bg-slate-800/60 text-slate-300"
                      >
                        {k.replace(/_/g, " ")}: <b className="text-slate-100">{v ?? "—"}</b>
                      </span>
                    ))}
                  </div>
                  <p className="text-slate-500 text-xs mt-2">
                    PostgreSQL {manifest.server_version || "—"} · restore with{" "}
                    <code>{manifest.restore_with}</code>
                  </p>
                </div>
              )}

              <div className="flex flex-wrap gap-2">
                <PrimaryButton onClick={download} disabled={busy}>
                  {busy ? <Spinner /> : <FaDownload size={13} />}
                  {busy ? "Preparing…" : "Download backup now"}
                </PrimaryButton>
                <GhostButton onClick={load} disabled={busy}>
                  <FaSyncAlt size={12} /> Refresh
                </GhostButton>
              </div>
              <p className="text-slate-500 text-xs mt-3">
                One export per hour is allowed, and every one is recorded in the audit log with
                who asked and from where — this hands over the entire database.
              </p>
            </>
          )}
        </div>
      </Card>

      {/* The fragment above was always here for a sibling: where the backups actually live. */}
      <BackupCustodyPanel status={status} scans={scans} runs={runs} loading={loading} />
    </>
  );
}
