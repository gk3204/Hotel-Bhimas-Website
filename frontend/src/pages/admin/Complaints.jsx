// Guest complaints — SLA / escalation / compensation (prompt 18c, slice 11).
// A management surface over source='guest' tickets (the ones the WhatsApp webhook + review pipeline
// already create, plus the front desk). Built through the shared BackofficeUI kit.
import React, { useCallback, useEffect, useState } from "react";
import { FaBell, FaCheck, FaGift, FaReply, FaSearch } from "react-icons/fa";
import * as api from "../../api/complaints";
import { useConfirm } from "../../components/ConfirmDialog";
import {
  Card, Chip, DataTable, Field, GhostButton, Modal, PageShell, PrimaryButton, SelectField,
  Stat, Tabs, fmtDate, inputCls, money, useToast,
} from "../../components/admin/BackofficeUI";

// Complaints already resolved/verified/closed can't be bulk-resolved again.
const DONE_STATUSES = ["resolved", "verified", "closed"];
const isResolvable = (c) => !DONE_STATUSES.includes(c.status);

const TABS = [
  ["open", "Open complaints"],
  ["breached", "SLA breached"],
  ["all", "All"],
  ["settings", "SLA settings"],
];

const PRIORITY_TONE = { urgent: "danger", high: "warn", normal: "info", low: "neutral" };
const STATUS_TONE = {
  open: "warn", assigned: "info", in_progress: "info", awaiting_parts: "warn",
  resolved: "ok", verified: "ok", closed: "neutral",
};

function StatusChip({ c }) {
  return <Chip tone={STATUS_TONE[c.status] || "neutral"}>{c.status.replace(/_/g, " ")}</Chip>;
}
function SlaChip({ c }) {
  if (c.status === "resolved" || c.status === "verified" || c.status === "closed")
    return <Chip tone="ok">met</Chip>;
  if (c.resolve_breached) return <Chip tone="danger">resolve overdue</Chip>;
  if (c.response_breached) return <Chip tone="warn">response overdue</Chip>;
  return <Chip tone="ok">on track</Chip>;
}

export default function Complaints() {
  const [tab, setTab] = useState("open");
  const [toast, showToast] = useToast();
  return (
    <PageShell
      icon="📣"
      title="Guest Complaints"
      subtitle="Log, track and resolve guest complaints against response/resolution SLAs — with escalation and goodwill compensation."
      toast={toast}
    >
      <Tabs tabs={TABS} active={tab} onChange={setTab} />
      {tab === "settings"
        ? <SettingsTab showToast={showToast} />
        : <ListTab key={tab} mode={tab} showToast={showToast} />}
    </PageShell>
  );
}

/* ------------------------------------------------------------------ list */

function ListTab({ mode, showToast }) {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [priority, setPriority] = useState("");
  const [dateFrom, setDateFrom] = useState("");   // ALT-10: client-side "logged" date range
  const [dateTo, setDateTo] = useState("");
  const [detail, setDetail] = useState(null);
  const [busy, setBusy] = useState(false);
  const [selected, setSelected] = useState(() => new Set());
  const [bulkBusy, setBulkBusy] = useState(false);
  const { confirm } = useConfirm();

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const params = { ...(priority ? { priority } : {}) };
      if (mode === "open") params.open_only = true;
      if (mode === "breached") params.breached = true;
      const res = await api.listComplaints(params);
      setRows(res.data || []);
      setSelected(new Set()); // clear selection on any reload
    } catch (e) {
      setError(e.message || "Failed to load complaints");
    } finally {
      setLoading(false);
    }
  }, [mode, priority]);

  const toggle = (id) =>
    setSelected((s) => {
      const next = new Set(s);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });

  const visibleRows = rows.filter((c) => {
    const d = (c.created_at || "").slice(0, 10);
    if (dateFrom && (!d || d < dateFrom)) return false;
    if (dateTo && (!d || d > dateTo)) return false;
    return true;
  });

  const selectableIds = visibleRows.filter(isResolvable).map((c) => c.id);
  const allSelected = selectableIds.length > 0 && selectableIds.every((id) => selected.has(id));
  const toggleAll = () =>
    setSelected(() => (allSelected ? new Set() : new Set(selectableIds)));

  const bulkResolve = async () => {
    const ids = [...selected];
    if (ids.length === 0) return;
    if (!(await confirm({
      title: `Resolve ${ids.length} complaint(s)?`,
      message: "Marks them resolved and stops SLA escalation. Already-closed ones are skipped.",
      confirmText: "Resolve",
    }))) return;
    setBulkBusy(true);
    try {
      const res = await api.bulkResolveComplaints({ ids });
      showToast(`${res.resolved ?? 0} resolved${res.skipped ? `, ${res.skipped} skipped` : ""}`);
      load();
    } catch (e) {
      showToast(e.message || "Bulk resolve failed");
    } finally {
      setBulkBusy(false);
    }
  };

  useEffect(() => { load(); /* eslint-disable-next-line */ }, [priority]);

  const runSweep = async () => {
    setBusy(true);
    try {
      const res = await api.runEscalations();
      showToast(`Sweep done — ${res.escalated ?? 0} complaint(s) escalated`);
      load();
    } catch (e) {
      showToast(e.message || "Sweep failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <Card className="mb-6">
        <div className="p-5 grid grid-cols-1 md:grid-cols-5 gap-4 items-end">
          <SelectField label="Priority" value={priority} onChange={(e) => setPriority(e.target.value)}
            options={[{ value: "", label: "All priorities" },
              ...["urgent", "high", "normal", "low"].map((p) => ({ value: p, label: p }))]} />
          <Field label="Logged from"><input className={inputCls} type="date" value={dateFrom}
            onChange={(e) => setDateFrom(e.target.value)} /></Field>
          <Field label="Logged to"><input className={inputCls} type="date" value={dateTo}
            onChange={(e) => setDateTo(e.target.value)} /></Field>
          <PrimaryButton onClick={load}><FaSearch size={14} /> Refresh</PrimaryButton>
          <GhostButton onClick={runSweep} disabled={busy}>
            <FaBell size={14} /> {busy ? "Running…" : "Run SLA escalation now"}
          </GhostButton>
        </div>
      </Card>

      <Card title="Complaints" right={
        <span className="text-slate-400 text-sm">{selected.size > 0 ? `${selected.size} selected` : rows.length}</span>
      }>
        {selected.size > 0 && (
          <div className="flex items-center gap-3 px-5 py-3 bg-[#E5C07B]/10 border-b border-[#E5C07B]/30">
            <span className="text-[#FCD34D] text-sm font-semibold">{selected.size} selected</span>
            <PrimaryButton onClick={bulkResolve} disabled={bulkBusy}>
              <FaCheck size={13} /> {bulkBusy ? "Resolving…" : "Resolve selected"}
            </PrimaryButton>
            <button onClick={() => setSelected(new Set())} className="text-slate-400 hover:text-white text-sm">Clear</button>
          </div>
        )}
        <DataTable
          columns={[
            { label: <input type="checkbox" checked={allSelected} onChange={toggleAll} aria-label="Select all resolvable" /> },
            { label: "#", sort: (c) => c.id },
            { label: "Guest / room", sort: (c) => c.guest_name || "" },
            "Issue",
            { label: "Priority", sort: (c) => c.priority },
            "Status",
            "SLA",
            { label: "Escalation", sort: (c) => c.escalation_level || 0 },
            { label: "Logged", sort: (c) => c.created_at },
            "",
          ]}
          rows={visibleRows} loading={loading} error={error} onRetry={load}
          empty={mode === "breached" ? "No SLA breaches. Everything is on track." : "No complaints here."}
          renderRow={(c) => [
            isResolvable(c)
              ? <input key="cb" type="checkbox" checked={selected.has(c.id)} onChange={() => toggle(c.id)} aria-label={`Select complaint ${c.id}`} />
              : <span key="cb" className="text-slate-600">—</span>,
            `#${c.id}`,
            <div key="g">
              <span className="text-slate-100">{c.guest_name || "—"}</span>
              {c.room_number && <span className="text-slate-500 text-xs ml-1">rm {c.room_number}</span>}
            </div>,
            <span key="i" className="text-slate-300 max-w-xs inline-block truncate" title={c.issue}>{c.issue}</span>,
            <Chip key="p" tone={PRIORITY_TONE[c.priority] || "neutral"}>{c.priority}</Chip>,
            <StatusChip key="s" c={c} />,
            <SlaChip key="sla" c={c} />,
            c.escalation_level > 0 ? <Chip key="e" tone="danger">L{c.escalation_level}</Chip> : "—",
            fmtDate(c.created_at),
            <button key="a" onClick={() => setDetail(c)} className="text-sky-300 hover:underline text-xs">
              Manage
            </button>,
          ]}
        />
      </Card>

      {detail && (
        <ComplaintDetail id={detail.id} onClose={() => setDetail(null)} showToast={showToast}
          onChanged={() => load()} />
      )}
    </>
  );
}

/* ------------------------------------------------------------------ detail / actions */

function ComplaintDetail({ id, onClose, showToast, onChanged }) {
  const [c, setC] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [comp, setComp] = useState({ amount: "", reason: "" });
  const [showComp, setShowComp] = useState(false);

  const load = useCallback(async () => {
    try {
      setC(await api.getComplaint(id));
    } catch (e) {
      setError(e.message || "Failed to load");
    }
  }, [id]);

  useEffect(() => { load(); /* eslint-disable-next-line */ }, [id]);

  const act = async (fn, msg) => {
    setBusy(true);
    try {
      await fn();
      showToast(msg);
      await load();
      onChanged();
    } catch (e) {
      showToast(e.message || "Action failed");
    } finally {
      setBusy(false);
    }
  };

  const compensate = async () => {
    const amt = Number(comp.amount);
    if (!amt || amt <= 0) { showToast("Enter an amount"); return; }
    if (!comp.reason || comp.reason.trim().length < 2) { showToast("A reason is required"); return; }
    await act(() => api.compensateComplaint(id, { amount: amt, reason: comp.reason.trim() }),
      "Goodwill credit posted to the folio");
    setShowComp(false);
    setComp({ amount: "", reason: "" });
  };

  const isOpen = c && !["resolved", "verified", "closed"].includes(c.status);

  return (
    <Modal title={c ? `Complaint #${c.id}` : "Complaint"} onClose={onClose} wide>
      {error && <div className="text-red-300 mb-4">{error}</div>}
      {!c ? (
        <div className="text-slate-400 p-6 text-center">Loading…</div>
      ) : (
        <>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-5">
            <Stat label="Priority" value={c.priority} />
            <Stat label="Status" value={c.status.replace(/_/g, " ")} />
            <Stat label="Escalation" value={`L${c.escalation_level}`}
              tone={c.escalation_level ? "text-red-300" : ""} />
            <Stat label="Compensation" value={c.compensation_amount ? money(c.compensation_amount) : "—"} />
          </div>

          <div className="bg-slate-900/50 border border-slate-700 rounded-xl p-4 mb-5">
            <div className="text-slate-400 text-xs mb-1">Complaint</div>
            <div className="text-slate-100">{c.issue}</div>
            <div className="text-slate-500 text-xs mt-2">
              {c.guest_name || "Walk-in"}{c.room_number ? ` · room ${c.room_number}` : ""} · logged {fmtDate(c.created_at)}
              {c.source ? ` · via ${c.source}` : ""}
            </div>
          </div>

          <div className="grid grid-cols-2 md:grid-cols-3 gap-3 mb-5 text-sm">
            <div><span className="text-slate-500">Respond by:</span> <span className="text-slate-200">{fmtDate(c.sla_response_due_at)}</span></div>
            <div><span className="text-slate-500">Resolve by:</span> <span className="text-slate-200">{fmtDate(c.sla_resolve_due_at)}</span></div>
            <div><span className="text-slate-500">First response:</span> <span className="text-slate-200">{c.first_responded_at ? fmtDate(c.first_responded_at) : "—"}</span></div>
          </div>

          {c.escalations && c.escalations.length > 0 && (
            <div className="mb-5">
              <div className="text-slate-400 text-xs mb-2">Escalation trail</div>
              <div className="space-y-1">
                {c.escalations.map((e) => (
                  <div key={e.id} className="text-xs text-slate-300 flex gap-2">
                    <Chip tone="danger">L{e.level}</Chip>
                    <span>{e.reason}</span>
                    <span className="text-slate-500">· {fmtDate(e.created_at)}{e.notified ? " · owner alerted" : ""}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {c.resolution_notes && (
            <div className="bg-slate-900/50 border border-slate-700 rounded-xl p-4 mb-5">
              <div className="text-slate-400 text-xs mb-1">Notes</div>
              <div className="text-slate-200 text-sm whitespace-pre-line">{c.resolution_notes}</div>
            </div>
          )}

          {isOpen && (
            <div className="flex flex-wrap gap-3">
              {!c.first_responded_at && (
                <GhostButton onClick={() => act(() => api.respondComplaint(id, {}), "Marked as responded")} disabled={busy}>
                  <FaReply size={13} /> Mark responded
                </GhostButton>
              )}
              <GhostButton onClick={() => act(() => api.escalateComplaint(id, { reason: "Manually escalated" }), "Escalated + owner alerted")} disabled={busy}>
                <FaBell size={13} /> Escalate
              </GhostButton>
              <PrimaryButton onClick={() => act(() => api.resolveComplaint(id, {}), "Resolved")} disabled={busy}>
                <FaCheck size={13} /> Resolve
              </PrimaryButton>
              {c.booking_id && (
                <GhostButton onClick={() => setShowComp((v) => !v)} disabled={busy}>
                  <FaGift size={13} /> Compensate
                </GhostButton>
              )}
            </div>
          )}

          {showComp && (
            <div className="mt-5 bg-slate-900/50 border border-[#E5C07B]/40 rounded-xl p-4">
              <div className="text-[#E5C07B] font-semibold mb-3 text-sm">Goodwill credit to the guest folio</div>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <Field label="Amount ₹ *" type="number" value={comp.amount}
                  onChange={(e) => setComp({ ...comp, amount: e.target.value })} />
                <Field label="Reason *" value={comp.reason}
                  onChange={(e) => setComp({ ...comp, reason: e.target.value })} />
              </div>
              <p className="text-slate-500 text-xs mt-2">
                Posts a discount line on the guest's open folio. Admin-only, reason recorded and audited.
              </p>
              <div className="mt-4">
                <PrimaryButton onClick={compensate} disabled={busy}>
                  <FaGift size={13} /> Post credit
                </PrimaryButton>
              </div>
            </div>
          )}
        </>
      )}
    </Modal>
  );
}

/* ------------------------------------------------------------------ settings */

const PRIORITIES = ["urgent", "high", "normal", "low"];

function SettingsTab({ showToast }) {
  const [cfg, setCfg] = useState(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    api.getComplaintsConfig().then(setCfg).catch((e) => setError(e.message || "Failed to load settings"));
  }, []);

  const save = async () => {
    setSaving(true);
    try {
      const body = { complaint_escalation_enabled: cfg.escalation_enabled };
      PRIORITIES.forEach((p) => {
        body[`complaint_sla_response_hours_${p}`] = Number(cfg.sla_response_hours[p]) || 1;
        body[`complaint_sla_resolve_hours_${p}`] = Number(cfg.sla_resolve_hours[p]) || 1;
      });
      await api.updateComplaintsConfig(body);
      showToast("SLA settings saved");
    } catch (e) {
      showToast(e.message || "Save failed");
    } finally {
      setSaving(false);
    }
  };

  if (error) return <Card><div className="p-8 text-center text-red-300">{error}</div></Card>;
  if (!cfg) return <Card><div className="p-12 text-center text-slate-400">Loading…</div></Card>;

  return (
    <Card title="Complaint SLA settings">
      <div className="p-6">
        <label className="flex items-center gap-3 text-slate-200 mb-6">
          <input type="checkbox" checked={cfg.escalation_enabled}
            onChange={(e) => setCfg({ ...cfg, escalation_enabled: e.target.checked })} />
          Auto-escalate complaints when an SLA is breached (owner gets a WhatsApp alert)
        </label>

        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm mb-2">
            <thead className="text-slate-400">
              <tr>
                <th className="py-2">Priority</th>
                <th className="py-2">Respond within (hours)</th>
                <th className="py-2">Resolve within (hours)</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-700">
              {PRIORITIES.map((p) => (
                <tr key={p}>
                  <td className="py-2"><Chip tone={PRIORITY_TONE[p]}>{p}</Chip></td>
                  <td className="py-2 pr-4">
                    <input type="number" className={inputCls} value={cfg.sla_response_hours[p]}
                      onChange={(e) => setCfg({ ...cfg, sla_response_hours: { ...cfg.sla_response_hours, [p]: e.target.value } })} />
                  </td>
                  <td className="py-2">
                    <input type="number" className={inputCls} value={cfg.sla_resolve_hours[p]}
                      onChange={(e) => setCfg({ ...cfg, sla_resolve_hours: { ...cfg.sla_resolve_hours, [p]: e.target.value } })} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div className="mt-6">
          <PrimaryButton onClick={save} disabled={saving}>
            {saving ? "Saving…" : "Save SLA settings"}
          </PrimaryButton>
        </div>
      </div>
    </Card>
  );
}
