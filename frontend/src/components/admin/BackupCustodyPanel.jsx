// Where the backups actually ARE — for both halves of what the hotel holds.
//
// The card above this one answers "did the server produce a dump?". This one answers the
// question that matters at restore time: "does a verified copy exist, on which machine, in
// which folder?". Those are different facts. A download that dies mid-transfer, fails its
// checksum and is discarded still leaves the server's own timestamp looking healthy, which is
// why health here follows the CLI's report and the server's view is shown as a subordinate line.
import React from "react";
import { Card, Chip, DataTable, Spinner, fmtBytes, fmtWhen } from "./BackofficeUI";

/** One label/value line. Values that are missing read as "—", never as a plausible zero. */
function Row({ label, children }) {
  return (
    <p className="text-slate-400 text-sm">
      {label}: <b className="text-slate-200">{children ?? "—"}</b>
    </p>
  );
}

function Mono({ children }) {
  return <span className="font-mono text-xs break-all opacity-80">{children}</span>;
}

/**
 * The custody block, identical in shape for the database dump and the scan archive.
 *
 * Three banner states, not two. "Never reported" is genuinely different from "stale": it means
 * this backend has never heard from the CLI at all — an old BhimasBackup, or one that was never
 * set up — and painting that red would teach the owner to ignore a red banner.
 */
function Custody({ title, offsite, failureAt, failureReason, extra }) {
  const reported = offsite?.reported;
  const stale = offsite?.stale;

  let tone = "bg-slate-800/60 border-slate-600 text-slate-300";
  let headline = "No backup has ever been confirmed from a machine.";
  let sub = "Update BhimasBackup on the admin PC so it can confirm a copy actually landed.";

  if (reported && !stale) {
    tone = "bg-green-500/10 border-green-500/30 text-green-200";
    headline = `✅ A verified copy is on ${offsite.machine || "the admin PC"}.`;
    sub = `Confirmed ${fmtWhen(offsite.last_at)}${
      offsite.age_hours != null ? ` (${offsite.age_hours} h ago)` : ""
    }.`;
  } else if (reported && stale) {
    tone = "bg-red-500/10 border-red-500/40 text-red-200";
    headline = "⚠️ No verified copy confirmed recently — the job may have stopped.";
    sub = `Last confirmed ${fmtWhen(offsite.last_at)}${
      offsite.age_hours != null ? ` (${offsite.age_hours} h ago)` : ""
    }. Anything over ${offsite.stale_hours} h counts as stale for this artifact.`;
  }

  return (
    <div className="mb-6">
      <h3 className="text-slate-300 font-semibold text-sm mb-2">{title}</h3>

      <div className={`p-4 rounded-xl border mb-3 ${tone}`}>
        <div className="font-semibold">{headline}</div>
        <div className="text-sm mt-1">{sub}</div>
      </div>

      {reported && (
        <div className="space-y-1 mb-3">
          <Row label="Machine">{offsite.machine}</Row>
          <Row label="Folder">
            <Mono>{offsite.folder}</Mono>
          </Row>
          <Row label="File">
            <Mono>{offsite.filename}</Mono>
          </Row>
          <Row label="Size">{fmtBytes(offsite.bytes)}</Row>
          {offsite.count != null && <Row label="Scans in the archive">{offsite.count}</Row>}
          {offsite.sha256 && (
            <p className="text-slate-400 text-sm">
              sha256: <Mono>{offsite.sha256}</Mono>
            </p>
          )}
          {/* Informational, never an alarm: the server legitimately produces a newer dump than
              the one on the admin PC the moment anyone uses the Download button above. */}
          {offsite.sha_matches_server === true && (
            <Chip tone="ok">matches the server&apos;s last dump</Chip>
          )}
          {offsite.sha_matches_server === false && (
            <Chip tone="info">the server has produced a newer dump since</Chip>
          )}
        </div>
      )}

      {failureAt && (
        <div className="p-3 rounded-lg bg-red-500/10 border border-red-500/30 text-red-300 text-sm mb-3">
          Last attempt failed {fmtWhen(failureAt)}
          {failureReason ? <> — {failureReason}</> : null}
        </div>
      )}

      {extra}
    </div>
  );
}

export default function BackupCustodyPanel({ status, scans, runs, loading }) {
  if (loading) {
    return (
      <Card title="Where the backups are" className="mb-6">
        <div className="p-5 flex items-center gap-3 text-slate-400 text-sm">
          <Spinner /> Loading…
        </div>
      </Card>
    );
  }

  const dbOffsite = status?.offsite;
  const scOffsite = scans?.offsite;
  // ⚠️ When object storage cannot be listed, /scans/status returns error together with
  // online_count: 0 and oldest_scan_at: null. Rendering those as facts would say "no scans
  // stored", which is the opposite of the truth — so they become "unknown", never zero.
  const storageBroken = Boolean(scans?.error);

  return (
    <Card title="Where the backups are" className="mb-6">
      <div className="p-5">
        <p className="text-slate-400 text-sm mb-5">
          Confirmed by the machine that holds each file, after it re-checked the checksum on its
          own disk. Producing a dump and holding a copy of it are different things — only the
          second one survives losing this server.
        </p>

        <Custody
          title="Database backup (pg_dump)"
          offsite={dbOffsite}
          failureAt={status?.last_failure_at}
          failureReason={status?.last_failure_reason}
          extra={
            <p className="text-slate-500 text-xs">
              Server last produced a dump: {fmtWhen(status?.last_success_at)}
              {status?.last_success_by ? ` by ${status.last_success_by}` : ""}
              {status?.last_success_bytes ? ` · ${fmtBytes(status.last_success_bytes)}` : ""}.{" "}
              Producing a dump is not the same as a copy existing off the server.
            </p>
          }
        />

        <Custody
          title="Guest ID scan archive"
          offsite={scOffsite}
          failureAt={scans?.last_failure_at}
          failureReason={scans?.last_failure_reason}
          extra={
            scans ? (
              <div className="space-y-1">
                {storageBroken && (
                  <div className="p-3 rounded-lg bg-red-500/10 border border-red-500/30 text-red-300 text-sm mb-2">
                    {scans.error}
                  </div>
                )}
                <Row label="Storage">
                  <Chip tone={scans.storage_ready ? "ok" : "danger"}>
                    {scans.backend === "r2" ? "Cloudflare R2" : scans.backend}
                    {scans.storage_ready ? "" : " — not reachable"}
                  </Chip>
                </Row>
                {scans.bucket && (
                  <Row label="Bucket">
                    <Mono>
                      {scans.bucket}/{scans.prefix}
                    </Mono>
                  </Row>
                )}
                <Row label="Still in object storage">
                  {storageBroken
                    ? "unknown"
                    : `${scans.online_count} scan(s) · ${fmtBytes(scans.online_bytes)}`}
                </Row>
                <Row label="Oldest scan online">
                  {storageBroken ? "unknown" : fmtWhen(scans.oldest_scan_at)}
                </Row>
                <Row label="Last purge">{fmtWhen(scans.last_purge_at)}</Row>
                <Row label="Purge floor">
                  nothing younger than {scans.min_age_days} days can be deleted
                </Row>
                <Row label="Purging">
                  {/* warn, not ok: an armed irreversible delete should catch the eye. */}
                  <Chip tone={scans.purge_enabled ? "warn" : "neutral"}>
                    {scans.purge_enabled ? "ARMED" : "disabled"}
                  </Chip>
                </Row>
                <p className="text-slate-500 text-xs pt-1">
                  Server last built an archive: {fmtWhen(scans.last_export_at)}
                  {scans.last_export_count ? ` · ${scans.last_export_count} scan(s)` : ""}.
                </p>
              </div>
            ) : (
              <p className="text-slate-500 text-xs">ID scan storage status is unavailable.</p>
            )
          }
        />

        <h3 className="text-slate-300 font-semibold text-sm mb-2 mt-6">Recent runs</h3>
        {runs?.runs?.length ? (
          <DataTable
            columns={["When", "Kind", "Machine", "Where", "Size", "Result"]}
            rows={runs.runs}
            pageSize={10}
            renderRow={(r) => [
              fmtWhen(r.finished_at),
              <Chip key="k" tone={r.kind === "database" ? "gold" : "info"}>
                {r.kind}
              </Chip>,
              r.machine,
              <Mono key="w">{r.filename || r.folder}</Mono>,
              r.kind === "scans" && r.count != null
                ? `${fmtBytes(r.bytes)} · ${r.count} scan(s)`
                : fmtBytes(r.bytes),
              <Chip key="r" tone={r.outcome === "ok" ? "ok" : "danger"}>
                {r.outcome}
              </Chip>,
            ]}
          />
        ) : (
          <p className="text-slate-500 text-xs">
            No runs reported yet. BhimasBackup records one entry per artifact per run.
          </p>
        )}
      </div>
    </Card>
  );
}
