// Append-only audit-log viewer (admin). Read-only surface over GET /audit-logs with filters,
// server-side pagination, and a before/after diff drawer. On the shared BackofficeUI kit.
import React, { useCallback, useEffect, useState } from "react";
import { FaSearch, FaHistory } from "react-icons/fa";
import { getAuditLogs } from "../../api/audit";
import {
  Card, Chip, DataTable, Field, Modal, PageShell, PrimaryButton, SelectField, inputCls, useToast,
} from "../../components/admin/BackofficeUI";

const PAGE = 50;
const today = () => new Date().toISOString().slice(0, 10);
const daysAgo = (n) => new Date(Date.now() - n * 86400000).toISOString().slice(0, 10);
const when = (iso) => (iso ? iso.replace("T", " ").slice(0, 19) : "—");

// Colour the action chip by its top-level namespace.
const actionTone = (a = "") => {
  const ns = a.split(".")[0];
  if (["card"].includes(ns)) return "gold";
  if (["payment", "refund", "folio"].includes(ns)) return "info";
  if (["shift", "cash"].includes(ns)) return "warn";
  if (["booking"].includes(ns)) return "ok";
  if (["id", "user", "auth", "twofa"].includes(ns)) return "danger";
  return "neutral";
};

const CLIENTS = [
  { value: "", label: "All sources" },
  { value: "web", label: "Web admin" },
  { value: "desktop", label: "Reception desktop" },
  { value: "housekeeper", label: "Housekeeper" },
  { value: "system", label: "System" },
];

export default function AuditLog() {
  const [toast, showToast] = useToast();
  const [filters, setFilters] = useState({
    from: daysAgo(6), to: today(), action: "", entity_type: "", client: "",
  });
  const [rows, setRows] = useState([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [detail, setDetail] = useState(null);

  const load = useCallback(async (pg = page) => {
    setLoading(true);
    setError("");
    try {
      const data = await getAuditLogs({ ...filters, limit: PAGE, offset: pg * PAGE });
      setRows(data.data || []);
      setTotal(data.total || 0);
    } catch (e) {
      setError(e.message || "Failed to load the audit log");
      setRows([]);
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filters, page]);

  useEffect(() => { load(page); /* eslint-disable-next-line */ }, [page]);

  const apply = () => {
    if (page === 0) load(0);
    else setPage(0); // resets → effect reloads
  };

  const pageCount = Math.max(1, Math.ceil(total / PAGE));
  const from = total === 0 ? 0 : page * PAGE + 1;
  const to = Math.min(total, (page + 1) * PAGE);

  return (
    <PageShell
      icon="📜"
      title="Audit Log"
      subtitle="Append-only trail of who did what, when and from where. Read-only."
      toast={toast}
    >
      <Card className="mb-6">
        <div className="p-5 grid grid-cols-1 md:grid-cols-6 gap-4 items-end">
          <Field label="From" type="date" value={filters.from}
            onChange={(e) => setFilters({ ...filters, from: e.target.value })} />
          <Field label="To" type="date" value={filters.to}
            onChange={(e) => setFilters({ ...filters, to: e.target.value })} />
          <Field label="Action starts with" value={filters.action} placeholder="e.g. card. / booking.cancel"
            onChange={(e) => setFilters({ ...filters, action: e.target.value })} />
          <Field label="Entity type" value={filters.entity_type} placeholder="booking / card / folio…"
            onChange={(e) => setFilters({ ...filters, entity_type: e.target.value })} />
          <SelectField label="Source" value={filters.client} options={CLIENTS}
            onChange={(e) => setFilters({ ...filters, client: e.target.value })} />
          <PrimaryButton onClick={apply}><FaSearch size={14} /> Apply</PrimaryButton>
        </div>
      </Card>

      <Card
        title="Entries"
        right={<span className="text-slate-400 text-sm flex items-center gap-2"><FaHistory /> {total} total</span>}
      >
        <DataTable
          columns={["When", "Actor", "Action", "Entity", "Source", "IP", ""]}
          rows={rows} loading={loading} error={error} pageSize={0}
          empty="No audit entries for these filters."
          renderRow={(r) => [
            <span key="w" className="text-slate-300 whitespace-nowrap font-mono text-xs">{when(r.when)}</span>,
            r.actor,
            <Chip key="a" tone={actionTone(r.action)}>{r.action}</Chip>,
            r.entity_type ? `${r.entity_type}${r.entity_id ? ` #${r.entity_id}` : ""}` : "—",
            r.client || "—",
            <span key="ip" className="text-slate-400 text-xs">{r.ip || "—"}</span>,
            (r.before || r.after)
              ? <button key="d" onClick={() => setDetail(r)} className="text-sky-300 hover:underline text-xs">View change</button>
              : <span key="d" className="text-slate-600 text-xs">—</span>,
          ]}
        />

        {/* Server-side pagination */}
        {total > PAGE && (
          <div className="flex items-center justify-between gap-3 px-4 py-3 border-t border-slate-700 text-sm text-slate-400">
            <span>Showing <b className="text-slate-200">{from}–{to}</b> of <b className="text-slate-200">{total}</b></span>
            <div className="flex items-center gap-2">
              <button onClick={() => setPage((p) => Math.max(0, p - 1))} disabled={page === 0 || loading}
                className="px-3 py-1.5 rounded-lg bg-slate-700 hover:bg-slate-600 text-white disabled:opacity-40 transition">Prev</button>
              <span className="text-slate-300">Page {page + 1} / {pageCount}</span>
              <button onClick={() => setPage((p) => Math.min(pageCount - 1, p + 1))} disabled={page >= pageCount - 1 || loading}
                className="px-3 py-1.5 rounded-lg bg-slate-700 hover:bg-slate-600 text-white disabled:opacity-40 transition">Next</button>
            </div>
          </div>
        )}
      </Card>

      {detail && (
        <Modal title={`#${detail.id} · ${detail.action}`} onClose={() => setDetail(null)} wide>
          <div className="text-slate-400 text-sm mb-4">
            {when(detail.when)} · {detail.actor} · {detail.client || "—"}{detail.ip ? ` · ${detail.ip}` : ""}
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <JsonPanel label="Before" value={detail.before} />
            <JsonPanel label="After" value={detail.after} />
          </div>
        </Modal>
      )}
    </PageShell>
  );
}

function JsonPanel({ label, value }) {
  let pretty = value;
  try { pretty = value ? JSON.stringify(JSON.parse(value), null, 2) : "—"; } catch { /* keep raw */ }
  return (
    <div>
      <div className="text-xs font-semibold text-slate-400 mb-1">{label}</div>
      <pre className={`${inputCls} whitespace-pre-wrap break-words text-xs max-h-80 overflow-y-auto`}>{pretty || "—"}</pre>
    </div>
  );
}
