import React, { useEffect, useMemo, useState } from "react";
import { FaSearch, FaFileCsv, FaFilePdf, FaChartBar } from "react-icons/fa";
import * as api from "../../api/reports";

const fmt = (n) =>
  `₹${Number(n || 0).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

const today = () => new Date().toISOString().slice(0, 10);
const daysAgo = (n) => {
  const d = new Date();
  d.setDate(d.getDate() - n);
  return d.toISOString().slice(0, 10);
};

// Each report: how to fetch it, its API path (for export), and how to project its JSON into a
// (columns, rows, totals) table — matching the backend's CSV/PDF projection so all three agree.
const REPORTS = {
  occupancy: {
    label: "Occupancy",
    path: "occupancy",
    range: true,
    groupBy: true,
    fetch: (p) => api.getOccupancy(p),
    project: (d) =>
      d.group_by === "room_type"
        ? {
            columns: ["Room Type", "Capacity (room-nights)", "Sold", "Occupancy %"],
            rows: d.rows.map((r) => [r.room_type, r.capacity_room_nights, r.room_nights_sold, `${r.occupancy_pct}%`]),
          }
        : {
            columns: ["Date", "Occupied", "Available", "Total Rooms", "Occupancy %"],
            rows: d.rows.map((r) => [r.date, r.occupied, r.available, r.total_rooms, `${r.occupancy_pct}%`]),
          },
  },
  "sales/daily": {
    label: "Daily Sales",
    path: "sales/daily",
    range: true,
    fetch: (p) => api.getDailySales(p),
    project: (d) => ({
      columns: ["Date", "Room Revenue", "Other Revenue", "Discount", "Gross Sales", "Taxable", "CGST", "SGST"],
      rows: d.rows.map((r) => [r.date, fmt(r.room_revenue), fmt(r.other_revenue), fmt(r.discount),
        fmt(r.gross_sales), fmt(r.taxable), fmt(r.cgst), fmt(r.sgst)]),
      totals: ["TOTAL", fmt(d.totals.room_revenue), fmt(d.totals.other_revenue), fmt(d.totals.discount),
        fmt(d.totals.gross_sales), fmt(d.totals.taxable), fmt(d.totals.cgst), fmt(d.totals.sgst)],
    }),
  },
  "arrivals-departures": {
    label: "Arrivals / Departures",
    path: "arrivals-departures",
    range: true,
    fetch: (p) => api.getArrivalsDepartures(p),
    project: (d) => ({
      columns: ["Date", "Arrivals", "Departures"],
      rows: d.rows.map((r) => [r.date, r.arrivals, r.departures]),
      totals: ["TOTAL", d.totals.arrivals, d.totals.departures],
    }),
  },
  "in-house": {
    label: "In-House",
    path: "in-house",
    range: false,
    fetch: () => api.getInHouse(),
    project: (d) => ({
      columns: ["Booking", "Guest", "Phone", "Rooms", "Check-in", "Check-out", "Nights", "Balance"],
      rows: d.rows.map((r) => [r.booking_id, r.guest_name, r.phone, r.rooms, r.check_in, r.check_out,
        r.nights, r.balance == null ? "—" : fmt(r.balance)]),
    }),
  },
  "travel-agents": {
    label: "Travel Agents",
    path: "travel-agents",
    range: true,
    fetch: (p) => api.getTravelAgents(p),
    project: (d) => ({
      columns: ["Agent", "Comm %", "Bookings", "Room Revenue", "Accrued", "Paid", "Outstanding"],
      rows: d.rows.map((r) => [r.agent_name, `${r.commission_percent}%`, r.bookings, fmt(r.room_revenue),
        fmt(r.commission_accrued), fmt(r.commission_paid), fmt(r.outstanding)]),
      totals: ["TOTAL", "", d.totals.bookings, fmt(d.totals.room_revenue), fmt(d.totals.commission_accrued),
        fmt(d.totals.commission_paid), fmt(d.totals.outstanding)],
    }),
  },
  ota: {
    label: "OTA Revenue",
    path: "ota",
    range: true,
    fetch: (p) => api.getOta(p),
    note: "OTA commission/payout reconciliation lands with the channel manager (prompt 17). Commission shown is an estimate.",
    project: (d) => ({
      columns: ["OTA", "Bookings", "Room Revenue", "Commission (est.)"],
      rows: d.rows.map((r) => [r.ota, r.bookings, fmt(r.room_revenue), fmt(r.commission_est)]),
      totals: ["TOTAL", d.totals.bookings, fmt(d.totals.room_revenue), fmt(d.totals.commission_est)],
    }),
  },
  gst: {
    label: "GST",
    path: "gst",
    range: true,
    fetch: (p) => api.getGst(p),
    project: (d) => ({
      columns: ["GST Slab %", "Taxable Value", "CGST", "SGST", "Gross"],
      rows: d.slabs.map((r) => [`${r.gst_percent}%`, fmt(r.taxable), fmt(r.cgst), fmt(r.sgst), fmt(r.gross)]),
      totals: ["TOTAL", fmt(d.taxable_total), fmt(d.cgst_total), fmt(d.sgst_total), fmt(d.gross_total)],
    }),
  },
  "cash-shift": {
    label: "Cash / Shift",
    path: "cash-shift",
    range: true,
    fetch: (p) => api.getCashShift(p),
    project: (d) => ({
      columns: ["Shift", "Station", "Status", "Staff", "Opening", "Collections", "Expenses", "Payouts",
        "Expected", "Counted", "Variance"],
      rows: d.rows.map((r) => [r.shift_id, r.station_id, r.status, r.staff_name, fmt(r.opening_balance),
        fmt(r.collections_cash), fmt(r.expenses_total), fmt(r.payouts_total), fmt(r.expected_cash),
        r.counted_cash == null ? "—" : fmt(r.counted_cash), r.variance == null ? "—" : fmt(r.variance)]),
      totals: ["TOTAL", "", "", "", fmt(d.totals.opening_balance), fmt(d.totals.collections_cash),
        fmt(d.totals.expenses_total), fmt(d.totals.payouts_total), fmt(d.totals.expected_cash), "",
        fmt(d.totals.variance)],
    }),
  },
  "card-audit": {
    label: "Card Audit",
    path: "card-audit",
    range: true,
    fetch: (p) => api.getCardAudit(p),
    project: (d) => ({
      columns: ["Card", "Booking", "Room", "UID", "Type", "Issue Type", "Status", "Issued By", "Station", "Issued At"],
      rows: d.rows.map((r) => [r.card_id, r.booking_id, r.room, r.card_uid, r.card_type, r.issue_type,
        r.status, r.issued_by, r.station_id, (r.issued_at || "").replace("T", " ").slice(0, 16)]),
    }),
  },
  "fraud-summary": {
    label: "Fraud Summary",
    path: "fraud-summary",
    range: true,
    fetch: (p) => api.getFraudSummary(p),
    project: (d) => ({
      columns: ["Alert", "Type", "Severity", "Status", "Booking", "Room", "Detected At"],
      rows: d.rows.map((r) => [r.id, r.type, r.severity, r.status, r.booking_id, r.room_id,
        (r.detected_at || "").replace("T", " ").slice(0, 16)]),
    }),
  },
};

const TAB_ORDER = Object.keys(REPORTS);

export default function Reports() {
  const [tab, setTab] = useState("occupancy");
  const [range, setRange] = useState({ from: daysAgo(29), to: today() });
  const [groupBy, setGroupBy] = useState("day");
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [toast, setToast] = useState("");

  const cfg = REPORTS[tab];
  const showToast = (m) => {
    setToast(m);
    setTimeout(() => setToast(""), 3500);
  };

  const params = useMemo(() => {
    const p = {};
    if (cfg.range) {
      p.from = range.from;
      p.to = range.to;
    }
    if (cfg.groupBy) p.group_by = groupBy;
    return p;
  }, [cfg, range, groupBy]);

  const load = async () => {
    setLoading(true);
    setError("");
    try {
      setData(await cfg.fetch(params));
    } catch (e) {
      setError(e.message || "Failed to load report");
      setData(null);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
    // reload when the tab or its controls change
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab, groupBy]);

  const doExport = async (format) => {
    try {
      await api.exportReport(cfg.path, params, format);
    } catch (e) {
      showToast(e.message || "Export failed");
    }
  };

  // Safety net: never let a data/shape mismatch (e.g. an in-flight tab switch) crash the whole page.
  let projected = null;
  if (data) {
    try {
      projected = cfg.project(data);
    } catch {
      projected = null;
    }
  }

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-900 via-slate-800 to-slate-900 text-white p-6">
      <div className="max-w-7xl mx-auto">
        {toast && (
          <div className="fixed top-6 right-6 z-50 bg-slate-800 border border-[#E5C07B]/40 text-white px-5 py-3 rounded-xl shadow-2xl">
            {toast}
          </div>
        )}

        <div className="mb-6">
          <h1 className="text-4xl font-bold mb-2 bg-gradient-to-r from-[#E5C07B] to-[#FCD34D] bg-clip-text text-transparent">
            📊 Reports
          </h1>
          <p className="text-slate-400">Operational & financial reports — filter by date and export to CSV / PDF.</p>
        </div>

        {/* Tabs */}
        <div className="flex flex-wrap gap-2 mb-6">
          {TAB_ORDER.map((k) => (
            <button
              key={k}
              onClick={() => {
                // Clear the previous report's data in the SAME render as the tab change, so the new
                // tab's projector never runs against the old tab's differently-shaped response.
                setTab(k);
                setData(null);
                setError("");
              }}
              className={`px-4 py-2 rounded-lg font-semibold transition text-sm ${
                tab === k
                  ? "bg-gradient-to-r from-[#E5C07B] to-[#D4AF37] text-slate-900"
                  : "bg-slate-800/60 border border-slate-700 text-slate-300 hover:bg-slate-700/60"
              }`}
            >
              {REPORTS[k].label}
            </button>
          ))}
        </div>

        {/* Filter card */}
        <div className="bg-gradient-to-r from-slate-800/50 to-slate-700/50 border border-slate-700 p-5 rounded-2xl shadow-xl mb-6 backdrop-blur">
          <div className="grid grid-cols-1 md:grid-cols-5 gap-4 items-end">
            {cfg.range && (
              <>
                <Field label="From" type="date" value={range.from}
                  onChange={(e) => setRange({ ...range, from: e.target.value })} />
                <Field label="To" type="date" value={range.to}
                  onChange={(e) => setRange({ ...range, to: e.target.value })} />
              </>
            )}
            {cfg.groupBy && (
              <div className="flex flex-col">
                <label className="mb-2 text-sm font-semibold text-slate-300">Group by</label>
                <select value={groupBy} onChange={(e) => setGroupBy(e.target.value)} className={inputCls}>
                  <option value="day">Day</option>
                  <option value="room_type">Room type</option>
                </select>
              </div>
            )}
            <button
              onClick={load}
              className="bg-gradient-to-r from-[#E5C07B] to-[#D4AF37] text-slate-900 font-bold px-6 py-2.5 rounded-lg transition-all hover:scale-105 flex items-center justify-center gap-2"
            >
              <FaSearch size={14} /> Apply
            </button>
            <div className="flex gap-2">
              <button onClick={() => doExport("csv")}
                className="flex-1 bg-slate-700 hover:bg-slate-600 text-white font-semibold px-4 py-2.5 rounded-lg transition flex items-center justify-center gap-2">
                <FaFileCsv size={14} /> CSV
              </button>
              <button onClick={() => doExport("pdf")}
                className="flex-1 bg-slate-700 hover:bg-slate-600 text-white font-semibold px-4 py-2.5 rounded-lg transition flex items-center justify-center gap-2">
                <FaFilePdf size={14} /> PDF
              </button>
            </div>
          </div>
          {cfg.note && <p className="text-slate-500 text-xs mt-3">ⓘ {cfg.note}</p>}
        </div>

        {/* Table */}
        <div className="bg-gradient-to-r from-slate-800/50 to-slate-700/50 border border-slate-700 rounded-2xl shadow-xl overflow-hidden backdrop-blur">
          <div className="p-5 border-b border-slate-700 flex items-center gap-2">
            <FaChartBar className="text-[#E5C07B]" />
            <h2 className="text-xl font-bold">{cfg.label}</h2>
          </div>

          {loading ? (
            <div className="p-12 flex items-center justify-center">
              <div className="animate-spin">
                <div className="h-10 w-10 border-4 border-[#E5C07B] border-t-[#D4AF37] rounded-full" />
              </div>
            </div>
          ) : error ? (
            <div className="p-8 text-center text-red-300">{error}</div>
          ) : !projected || projected.rows.length === 0 ? (
            <div className="p-12 text-center text-slate-400">
              <div className="text-5xl mb-4">📭</div>
              <p>No data for the selected period.</p>
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead className="bg-slate-900/80 border-b border-slate-700">
                  <tr>
                    {projected.columns.map((c) => (
                      <th key={c} className="px-4 py-3 font-semibold whitespace-nowrap">{c}</th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-700">
                  {projected.rows.map((r, i) => (
                    <tr key={i} className="hover:bg-slate-700/30 transition">
                      {r.map((c, j) => (
                        <td key={j} className="px-4 py-2.5 text-slate-200 whitespace-nowrap">
                          {c == null ? "—" : String(c)}
                        </td>
                      ))}
                    </tr>
                  ))}
                  {projected.totals && (
                    <tr className="bg-[#E5C07B]/10 font-bold text-[#FCD34D] border-t border-[#E5C07B]/30">
                      {projected.totals.map((c, j) => (
                        <td key={j} className="px-4 py-3 whitespace-nowrap">{c == null ? "" : String(c)}</td>
                      ))}
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

const inputCls =
  "px-4 py-2 bg-slate-900/50 border border-slate-600 rounded-lg text-white focus:outline-none focus:border-[#E5C07B] focus:ring-2 focus:ring-[#E5C07B]/20 transition";

const Field = ({ label, ...p }) => (
  <div className="flex flex-col">
    <label className="mb-2 text-sm font-semibold text-slate-300">{label}</label>
    <input {...p} className={inputCls} />
  </div>
);
