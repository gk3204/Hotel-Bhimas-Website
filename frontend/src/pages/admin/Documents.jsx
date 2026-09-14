// Documents (v5j) — one place to search, preview and download every PDF the system generates:
// GST invoices, payment receipts, refund vouchers, registration slips, booking confirmations,
// company invoices and cash-shift reports. Read-only index over GET /documents; each row's PDF is
// regenerated on demand by the endpoint that owns it. Layout mirrors the Audit Log page.
import React, { useCallback, useEffect, useRef, useState } from "react";
import { FaSearch, FaFileInvoice, FaEye, FaDownload } from "react-icons/fa";
import { listDocuments, documentObjectUrl, downloadDocument } from "../../api/documents";
import {
  Card, Chip, DataTable, Field, GhostButton, Modal, PageShell, PrimaryButton, SelectField, money, useToast,
} from "../../components/admin/BackofficeUI";

const PAGE = 50;
const today = () => new Date().toISOString().slice(0, 10);
const daysAgo = (n) => new Date(Date.now() - n * 86400000).toISOString().slice(0, 10);
const when = (iso) => (iso ? iso.replace("T", " ").slice(0, 16) : "—");

const TYPES = [
  { value: "all", label: "All documents" },
  { value: "invoice", label: "GST invoices" },
  { value: "receipt", label: "Payment receipts" },
  { value: "refund", label: "Refund vouchers" },
  { value: "slip", label: "Registration slips" },
  { value: "confirmation", label: "Booking confirmations" },
  { value: "company_invoice", label: "Company invoices" },
  { value: "shift_report", label: "Shift reports" },
];
const TYPE_LABEL = Object.fromEntries(TYPES.map((t) => [t.value, t.label.replace(/s$/, "")]));
const TYPE_TONE = {
  invoice: "gold", receipt: "info", refund: "warn", confirmation: "ok",
  slip: "neutral", company_invoice: "gold", shift_report: "neutral",
};

export default function Documents() {
  const [toast, showToast] = useToast();
  const [filters, setFilters] = useState({ q: "", type: "all", from: daysAgo(30), to: today() });
  const [rows, setRows] = useState([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [preview, setPreview] = useState(null);     // { row, url } while the viewer is open
  const [busyRow, setBusyRow] = useState(null);     // row number being fetched
  const previewUrl = useRef(null);

  const load = useCallback(async (pg) => {
    setLoading(true);
    setError("");
    try {
      const data = await listDocuments({ ...filters, limit: PAGE, offset: pg * PAGE });
      setRows(data.data || []);
      setTotal(data.total || 0);
    } catch (e) {
      setError(e.message || "Failed to load documents");
      setRows([]);
    } finally {
      setLoading(false);
    }
  }, [filters]);

  // Reload on page change only — typing in the filters must not fire a request per keystroke;
  // the Search button (or Enter) applies them.
  useEffect(() => {
    load(page);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page]);

  const apply = () => {
    if (page === 0) load(0);
    else setPage(0);
  };

  const closePreview = () => {
    if (previewUrl.current) URL.revokeObjectURL(previewUrl.current);
    previewUrl.current = null;
    setPreview(null);
  };
  // Revoke the object URL if the page unmounts with the viewer open.
  useEffect(() => () => { if (previewUrl.current) URL.revokeObjectURL(previewUrl.current); }, []);

  const onPreview = async (r) => {
    setBusyRow(r.number);
    try {
      const url = await documentObjectUrl(r.pdf_url);
      if (previewUrl.current) URL.revokeObjectURL(previewUrl.current);
      previewUrl.current = url;
      setPreview({ row: r, url });
    } catch (e) {
      showToast(e.message || "Could not open the document", "error");
    } finally {
      setBusyRow(null);
    }
  };

  const onDownload = async (r) => {
    setBusyRow(r.number);
    try {
      await downloadDocument(r.pdf_url);
    } catch (e) {
      showToast(e.message || "Download failed", "error");
    } finally {
      setBusyRow(null);
    }
  };

  const pageCount = Math.max(1, Math.ceil(total / PAGE));
  const from = total === 0 ? 0 : page * PAGE + 1;
  const to = Math.min(total, (page + 1) * PAGE);

  return (
    <PageShell
      icon="🗂️"
      title="Documents"
      subtitle="Every invoice, receipt, refund voucher, registration slip, booking confirmation, company invoice and shift report — search, preview, download."
      toast={toast}
    >
      <Card className="mb-6">
        <div className="p-5 grid grid-cols-1 md:grid-cols-6 gap-4 items-end">
          <div className="md:col-span-2">
            <Field label="Search" value={filters.q} placeholder="Invoice / receipt no · guest · phone · booking # · room"
              onChange={(e) => setFilters({ ...filters, q: e.target.value })}
              onKeyDown={(e) => { if (e.key === "Enter") apply(); }} />
          </div>
          <SelectField label="Type" value={filters.type} options={TYPES}
            onChange={(e) => setFilters({ ...filters, type: e.target.value })} />
          <Field label="From" type="date" value={filters.from}
            onChange={(e) => setFilters({ ...filters, from: e.target.value })} />
          <Field label="To" type="date" value={filters.to}
            onChange={(e) => setFilters({ ...filters, to: e.target.value })} />
          <PrimaryButton onClick={apply}><FaSearch size={14} /> Search</PrimaryButton>
        </div>
      </Card>

      <Card
        title="Documents"
        right={<span className="text-slate-400 text-sm flex items-center gap-2"><FaFileInvoice /> {total} found</span>}
      >
        <DataTable
          columns={["Type", "Number", "Date", "Booking", "Guest / party", "Room", "Amount", "Status", ""]}
          rows={rows} loading={loading} error={error} pageSize={0}
          empty="No documents match these filters."
          renderRow={(r) => [
            <Chip key="t" tone={TYPE_TONE[r.type] || "neutral"}>{TYPE_LABEL[r.type] || r.type}</Chip>,
            <span key="n" className="font-mono text-xs text-slate-200 whitespace-nowrap">{r.number}</span>,
            <span key="d" className="text-slate-300 whitespace-nowrap text-xs">{when(r.date)}</span>,
            r.booking_id ? `#${r.booking_id}` : "—",
            <span key="g">
              {r.guest || "—"}
              {r.phone && <span className="text-slate-500 text-xs"> · {r.phone}</span>}
            </span>,
            r.room || "—",
            <span key="a" className="block text-right tabular-nums">{r.amount != null ? money(r.amount) : "—"}</span>,
            <span key="s" className="text-slate-400 text-xs">{r.status || "—"}</span>,
            <span key="x" className="flex gap-2 justify-end whitespace-nowrap">
              <GhostButton onClick={() => onPreview(r)} disabled={busyRow === r.number}>
                <FaEye size={12} /> Preview
              </GhostButton>
              <GhostButton onClick={() => onDownload(r)} disabled={busyRow === r.number}>
                <FaDownload size={12} /> Download
              </GhostButton>
            </span>,
          ]}
        />

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

      {preview && (
        <Modal title={`${TYPE_LABEL[preview.row.type] || preview.row.type} · ${preview.row.number}`} onClose={closePreview} wide>
          <div className="flex items-center justify-between gap-3 mb-3 text-sm text-slate-400">
            <span>
              {when(preview.row.date)}
              {preview.row.guest ? ` · ${preview.row.guest}` : ""}
              {preview.row.booking_id ? ` · booking #${preview.row.booking_id}` : ""}
              {preview.row.room ? ` · room ${preview.row.room}` : ""}
            </span>
            <PrimaryButton onClick={() => onDownload(preview.row)}><FaDownload size={12} /> Download</PrimaryButton>
          </div>
          <iframe title={preview.row.number} src={preview.url} className="w-full h-[70vh] rounded-lg bg-white" />
        </Modal>
      )}
    </PageShell>
  );
}
