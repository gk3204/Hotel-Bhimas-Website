import React, { useEffect, useMemo, useState } from "react";
import { FaSearch, FaFileCsv, FaFilePdf, FaFileCode, FaPrint, FaSync, FaSave } from "react-icons/fa";
import * as api from "../../api/compliance";
import { PageShell } from "../../components/admin/BackofficeUI";
import ConfigPanel from "../../components/admin/ConfigPanel";
import { groupByKey } from "../../components/admin/settingsGroups";

const today = () => new Date().toISOString().slice(0, 10);
const daysAgo = (n) => new Date(Date.now() - n * 86400000).toISOString().slice(0, 10);

const inputCls =
  "px-4 py-2 bg-slate-900/50 border border-slate-600 rounded-lg text-white focus:outline-none focus:border-[#E5C07B] focus:ring-2 focus:ring-[#E5C07B]/20 transition";

const Field = ({ label, ...p }) => (
  <div className="flex flex-col">
    <label className="mb-2 text-sm font-semibold text-slate-300">{label}</label>
    <input {...p} className={inputCls} />
  </div>
);

// Tab config: each register maps a JSON response to {columns, rows} for the table + the export path.
const TABS = {
  tally: { label: "Tally / Accounting", range: true, export: "tally/export" },
  police: {
    label: "Police Register", range: true, export: "police-register",
    fetch: api.getPoliceRegister,
    columns: ["Check-in", "Check-out", "Room", "Guest", "Phone", "Address", "ID Type", "ID No", "Nationality"],
    row: (r) => [r.check_in, r.check_out, r.room, r.guest_name, r.phone, r.address, r.id_type, r.id_number, r.nationality],
  },
  formc: {
    label: "Form C (Foreign)", range: true, export: "form-c",
    fetch: api.getFormC,
    columns: ["Guest", "Nationality", "Passport", "Place of Issue", "Passport Exp", "Visa", "Visa Type", "Visa Exp", "Arrived From", "Next Dest", "Room", "Check-in", "Check-out"],
    row: (r) => [r.guest_name, r.nationality, r.passport_number, r.passport_place_of_issue, r.passport_expiry, r.visa_number, r.visa_type, r.visa_expiry, r.arrived_from, r.next_destination, r.room, r.check_in, r.check_out],
  },
  evacuation: {
    label: "Evacuation (Live)", live: true, export: "evacuation",
    fetch: api.getEvacuation,
    columns: ["Room", "Guest", "Phone", "Check-in", "Check-out"],
    row: (r) => [r.rooms, r.guest_name, r.phone, r.check_in, r.check_out],
  },
  idaudit: {
    label: "ID-Access Audit", range: true, export: "id-access-audit",
    fetch: api.getIdAccessAudit,
    columns: ["When (UTC)", "Viewed by", "Action", "Guest ID", "Guest", "IP", "Client"],
    row: (r) => [r.when, r.actor, r.action, r.guest_id, r.guest_name, r.ip, r.client],
  },
  settings: { label: "Settings" },
};
const TAB_ORDER = Object.keys(TABS);

export default function Compliance() {
  const [tab, setTab] = useState("tally");
  const [range, setRange] = useState({ from: daysAgo(29), to: today() });
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [toast, setToast] = useState("");

  const cfg = TABS[tab];
  const showToast = (m) => { setToast(m); setTimeout(() => setToast(""), 3500); };
  const params = useMemo(() => (cfg.range ? { from: range.from, to: range.to } : {}), [cfg, range]);

  const load = async () => {
    if (!cfg.fetch) { setData(null); return; }
    setLoading(true); setError("");
    try {
      setData(await cfg.fetch(params));
    } catch (e) {
      setError(e.message || "Failed to load"); setData(null);
    } finally { setLoading(false); }
  };

  useEffect(() => { load(); /* eslint-disable-next-line */ }, [tab]);

  const doExport = async (format) => {
    try {
      await api.exportCompliance(cfg.export, params, format);
    } catch (e) { showToast(e.message || "Export failed"); }
  };

  const rows = data?.rows || [];

  return (
    <PageShell
      icon="🛡️"
      title="Compliance & Tally"
      subtitle="GST accounting export, police / FRRO registers, live evacuation list and ID-access audit."
      toast={toast}
    >

        {/* Tabs */}
        <div className="flex flex-wrap gap-2 mb-6">
          {TAB_ORDER.map((k) => (
            <button key={k} onClick={() => { setTab(k); setData(null); setError(""); }}
              className={`px-4 py-2 rounded-lg font-semibold transition text-sm ${
                tab === k
                  ? "bg-gradient-to-r from-[#E5C07B] to-[#D4AF37] text-slate-900"
                  : "bg-slate-800/60 border border-slate-700 text-slate-300 hover:bg-slate-700/60"
              }`}>
              {TABS[k].label}
            </button>
          ))}
        </div>

        {tab === "settings" ? (
          <SettingsPanel showToast={showToast} />
        ) : (
          <>
            {/* Controls card */}
            <div className="bg-gradient-to-r from-slate-800/50 to-slate-700/50 border border-slate-700 p-5 rounded-2xl shadow-xl mb-6 backdrop-blur">
              <div className="grid grid-cols-1 md:grid-cols-5 gap-4 items-end">
                {cfg.range && (
                  <>
                    <Field label="From" type="date" value={range.from}
                      onChange={(e) => setRange({ ...range, from: e.target.value })} />
                    <Field label="To" type="date" value={range.to}
                      onChange={(e) => setRange({ ...range, to: e.target.value })} />
                    <button onClick={load}
                      className="bg-gradient-to-r from-[#E5C07B] to-[#D4AF37] text-slate-900 font-bold px-6 py-2.5 rounded-lg transition-all hover:scale-105 flex items-center justify-center gap-2">
                      <FaSearch size={14} /> Apply
                    </button>
                  </>
                )}
                {cfg.live && (
                  <>
                    <button onClick={load}
                      className="bg-gradient-to-r from-[#E5C07B] to-[#D4AF37] text-slate-900 font-bold px-6 py-2.5 rounded-lg transition-all hover:scale-105 flex items-center justify-center gap-2">
                      <FaSync size={14} /> Refresh
                    </button>
                    <button onClick={() => window.print()}
                      className="bg-slate-700 hover:bg-slate-600 text-white font-semibold px-4 py-2.5 rounded-lg transition flex items-center justify-center gap-2">
                      <FaPrint size={14} /> Print
                    </button>
                  </>
                )}
                {/* Export buttons */}
                <div className="flex gap-2 md:col-start-4 md:col-span-2">
                  {tab === "tally" ? (
                    <>
                      <button onClick={() => doExport("xml")}
                        className="flex-1 bg-slate-700 hover:bg-slate-600 text-white font-semibold px-4 py-2.5 rounded-lg transition flex items-center justify-center gap-2">
                        <FaFileCode size={14} /> Tally XML
                      </button>
                      <button onClick={() => doExport("csv")}
                        className="flex-1 bg-slate-700 hover:bg-slate-600 text-white font-semibold px-4 py-2.5 rounded-lg transition flex items-center justify-center gap-2">
                        <FaFileCsv size={14} /> CSV
                      </button>
                    </>
                  ) : (
                    <>
                      <button onClick={() => doExport("csv")}
                        className="flex-1 bg-slate-700 hover:bg-slate-600 text-white font-semibold px-4 py-2.5 rounded-lg transition flex items-center justify-center gap-2">
                        <FaFileCsv size={14} /> CSV
                      </button>
                      <button onClick={() => doExport("pdf")}
                        className="flex-1 bg-slate-700 hover:bg-slate-600 text-white font-semibold px-4 py-2.5 rounded-lg transition flex items-center justify-center gap-2">
                        <FaFilePdf size={14} /> PDF
                      </button>
                    </>
                  )}
                </div>
              </div>
              {tab === "tally" && (
                <p className="text-slate-500 text-xs mt-3">ⓘ Sales + collection vouchers for the range. Totals reconcile with the Reports → GST / Sales screens. Import the XML into Tally Prime/ERP-9.</p>
              )}
              {tab === "formc" && (
                <p className="text-slate-500 text-xs mt-3">ⓘ Only guests marked as foreign nationals appear here (set on the guest profile). Passport/visa numbers are masked.</p>
              )}
            </div>

            {/* Result */}
            {tab === "tally"
              ? <TallyResult data={data} loading={loading} error={error} />
              : <TableResult columns={cfg.columns} rows={rows} rowFn={cfg.row}
                  loading={loading} error={error} count={data?.count} />}
          </>
        )}
    </PageShell>
  );
}

function TableResult({ columns, rows, rowFn, loading, error, count }) {
  return (
    <div className="bg-gradient-to-r from-slate-800/50 to-slate-700/50 border border-slate-700 rounded-2xl shadow-xl overflow-hidden backdrop-blur">
      <div className="p-5 border-b border-slate-700 flex items-center justify-between">
        <h2 className="text-lg font-bold text-[#E5C07B]">Records</h2>
        {count != null && <span className="text-slate-400 text-sm">{count} row(s)</span>}
      </div>
      {loading ? (
        <div className="p-12 flex items-center justify-center">
          <div className="animate-spin"><div className="h-10 w-10 border-4 border-[#E5C07B] border-t-[#D4AF37] rounded-full" /></div>
        </div>
      ) : error ? (
        <div className="p-8 text-center text-red-300">{error}</div>
      ) : rows.length === 0 ? (
        <div className="p-12 text-center text-slate-400"><div className="text-5xl mb-4">📭</div><p>No records for the selected period.</p></div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="bg-slate-900/80 border-b border-slate-700">
              <tr>{columns.map((c) => <th key={c} className="px-4 py-3 font-semibold whitespace-nowrap">{c}</th>)}</tr>
            </thead>
            <tbody className="divide-y divide-slate-700">
              {rows.map((r, i) => (
                <tr key={i} className="hover:bg-slate-700/30 transition">
                  {rowFn(r).map((c, j) => (
                    <td key={j} className="px-4 py-2.5 text-slate-200 whitespace-nowrap">{c == null || c === "" ? "—" : String(c)}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function TallyResult({ data, loading, error }) {
  if (loading) return <div className="p-12 flex justify-center"><div className="animate-spin"><div className="h-10 w-10 border-4 border-[#E5C07B] border-t-[#D4AF37] rounded-full" /></div></div>;
  if (error) return <div className="p-8 text-center text-red-300">{error}</div>;
  const t = data?.totals;
  return (
    <div className="bg-gradient-to-r from-slate-800/50 to-slate-700/50 border border-slate-700 rounded-2xl shadow-xl overflow-hidden backdrop-blur p-6">
      <h2 className="text-lg font-bold text-[#E5C07B] mb-4">Export preview — {data?.company}</h2>
      {!t ? (
        <p className="text-slate-400">Apply a date range to preview totals.</p>
      ) : (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          {[["Gross Sales", t.gross_sales], ["Taxable", t.taxable], ["CGST", t.cgst], ["SGST", t.sgst],
            ["Room Revenue", t.room_revenue], ["Other Revenue", t.other_revenue], ["Discount", t.discount]]
            .map(([label, val]) => (
              <div key={label} className="bg-slate-900/50 border border-slate-700 rounded-xl p-4">
                <div className="text-slate-400 text-xs mb-1">{label}</div>
                <div className="text-xl font-bold text-white">₹ {Number(val || 0).toLocaleString("en-IN", { minimumFractionDigits: 2 })}</div>
              </div>
            ))}
          <div className="bg-slate-900/50 border border-slate-700 rounded-xl p-4">
            <div className="text-slate-400 text-xs mb-1">Vouchers</div>
            <div className="text-xl font-bold text-[#FCD34D]">{data?.vouchers?.length || 0}</div>
          </div>
        </div>
      )}
      <p className="text-slate-500 text-xs mt-4">ⓘ Use the <b>Tally XML</b> button above to download the importable file; totals here match Reports → GST.</p>
    </div>
  );
}

// The same editor the Settings hub renders — one source, so the two can't drift (FE-12).
function SettingsPanel({ showToast }) {
  return <ConfigPanel group={groupByKey("compliance")} showToast={showToast} />;
}
