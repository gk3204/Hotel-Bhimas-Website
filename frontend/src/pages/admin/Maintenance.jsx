import React, { useEffect, useState } from "react";
import { FaWrench, FaSyncAlt, FaCheckDouble, FaCheck } from "react-icons/fa";
import {
  getTickets,
  getTicket,
  assignTicket,
  verifyTicket,
  approveItem,
  purchaseItem,
  getMaintenanceStaff,
} from "../../api/maintenance";

const inputCls =
  "px-4 py-2 bg-slate-900/50 border border-slate-600 rounded-lg text-white focus:outline-none focus:border-[#E5C07B] focus:ring-2 focus:ring-[#E5C07B]/20 transition";

const fmt = (n) =>
  n === null || n === undefined ? "—" : `₹${Number(n).toLocaleString("en-IN", { minimumFractionDigits: 2 })}`;

const statusChip = (s) => {
  const map = {
    open: "bg-slate-600/30 text-slate-300 border-slate-600/40",
    assigned: "bg-blue-500/20 text-blue-300 border-blue-500/30",
    in_progress: "bg-yellow-500/20 text-yellow-300 border-yellow-500/30",
    awaiting_parts: "bg-amber-500/20 text-amber-300 border-amber-500/30",
    resolved: "bg-cyan-500/20 text-cyan-300 border-cyan-500/30",
    verified: "bg-green-500/20 text-green-300 border-green-500/30",
    closed: "bg-green-500/20 text-green-300 border-green-500/30",
  };
  return map[s] || "bg-slate-600/30 text-slate-300 border-slate-600/40";
};

const CATEGORIES = ["", "electrical", "plumbing", "carpentry", "appliance", "lock", "other"];
const STATUSES = ["", "open", "assigned", "in_progress", "awaiting_parts", "resolved", "verified"];

export default function Maintenance() {
  const [tickets, setTickets] = useState([]);
  const [summary, setSummary] = useState({});
  const [staff, setStaff] = useState([]);
  const [filters, setFilters] = useState({ status: "", category: "", overdue: false });
  const [loading, setLoading] = useState(false);
  const [toast, setToast] = useState(null);
  const [detail, setDetail] = useState({}); // id -> full ticket
  const [busy, setBusy] = useState(false);

  const showToast = (message, type = "success") => {
    setToast({ message, type });
    setTimeout(() => setToast(null), 3500);
  };

  const load = async () => {
    setLoading(true);
    try {
      const data = await getTickets({
        status: filters.status || undefined,
        category: filters.category || undefined,
        overdue: filters.overdue || undefined,
      });
      setTickets(data.tickets || []);
      setSummary(data.summary || {});
    } catch (e) {
      showToast(e.message, "error");
    }
    setLoading(false);
  };

  useEffect(() => {
    load();
  }, [filters]); // eslint-disable-line

  useEffect(() => {
    getMaintenanceStaff().then(setStaff).catch(() => {});
  }, []);

  const toggleDetail = async (id) => {
    if (detail[id]) {
      setDetail((d) => ({ ...d, [id]: null }));
      return;
    }
    try {
      const t = await getTicket(id);
      setDetail((d) => ({ ...d, [id]: t }));
    } catch (e) {
      showToast(e.message, "error");
    }
  };

  const refreshDetail = async (id) => {
    try {
      const t = await getTicket(id);
      setDetail((d) => ({ ...d, [id]: t }));
    } catch (e) {
      showToast(e.message, "error");
    }
  };

  const doAssign = async (t, assigneeId) => {
    if (!assigneeId) return;
    setBusy(true);
    try {
      await assignTicket(t.id, { assignee_id: Number(assigneeId) });
      showToast(`Ticket #${t.id} assigned`);
      await load();
    } catch (e) {
      showToast(e.message, "error");
    }
    setBusy(false);
  };

  const doVerify = async (t) => {
    setBusy(true);
    try {
      await verifyTicket(t.id, {});
      showToast(`Ticket #${t.id} verified & closed`);
      await load();
    } catch (e) {
      showToast(e.message, "error");
    }
    setBusy(false);
  };

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-900 via-slate-800 to-slate-900 text-white p-6">
      <div className="max-w-7xl mx-auto">
        <div className="mb-6">
          <h1 className="text-4xl font-bold mb-2 bg-gradient-to-r from-[#E5C07B] to-[#FCD34D] bg-clip-text text-transparent flex items-center gap-3">
            <FaWrench /> Maintenance
          </h1>
          <p className="text-slate-400">
            Assign tickets to a technician, approve required parts, and verify-close. Purchased parts post to the shift ledger.
          </p>
        </div>

        {/* Filters + summary */}
        <div className="bg-gradient-to-r from-slate-800/50 to-slate-700/50 border border-slate-700 p-5 rounded-2xl shadow-xl mb-6 backdrop-blur flex flex-wrap items-end gap-4">
          <div className="flex flex-col">
            <label className="mb-1 text-sm font-semibold text-slate-300">Status</label>
            <select value={filters.status} onChange={(e) => setFilters({ ...filters, status: e.target.value })} className={inputCls}>
              {STATUSES.map((s) => (
                <option key={s} value={s}>{s || "All"}</option>
              ))}
            </select>
          </div>
          <div className="flex flex-col">
            <label className="mb-1 text-sm font-semibold text-slate-300">Category</label>
            <select value={filters.category} onChange={(e) => setFilters({ ...filters, category: e.target.value })} className={inputCls}>
              {CATEGORIES.map((c) => (
                <option key={c} value={c}>{c || "All"}</option>
              ))}
            </select>
          </div>
          <label className="flex items-center gap-2 px-4 py-2.5 bg-slate-900/50 border border-slate-600 rounded-lg cursor-pointer">
            <input type="checkbox" checked={filters.overdue} onChange={(e) => setFilters({ ...filters, overdue: e.target.checked })} className="w-4 h-4 accent-[#E5C07B]" />
            <span className="text-slate-300 text-sm">Overdue only</span>
          </label>
          <button onClick={load} className="bg-slate-700 hover:bg-slate-600 font-semibold px-6 py-2.5 rounded-lg transition flex items-center gap-2">
            <FaSyncAlt size={13} /> Refresh
          </button>
          <div className="ml-auto text-sm text-slate-400">
            <span className="mr-4">Open: <b className="text-slate-200">{summary.open ?? 0}</b></span>
            <span>Overdue: <b className="text-red-300">{summary.overdue ?? 0}</b></span>
          </div>
        </div>

        {/* Tickets */}
        <div className="bg-gradient-to-r from-slate-800/50 to-slate-700/50 border border-slate-700 rounded-2xl shadow-xl overflow-hidden backdrop-blur">
          {loading ? (
            <div className="p-12 flex items-center justify-center">
              <div className="animate-spin"><div className="h-12 w-12 border-4 border-[#E5C07B] border-t-[#D4AF37] rounded-full"></div></div>
            </div>
          ) : tickets.length === 0 ? (
            <div className="p-12 text-center text-slate-400"><div className="text-5xl mb-4">🔧</div><p>No tickets.</p></div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left">
                <thead className="bg-slate-900/80 border-b border-slate-700">
                  <tr>
                    <th className="px-5 py-4 font-semibold text-sm">#</th>
                    <th className="px-5 py-4 font-semibold text-sm">Location</th>
                    <th className="px-5 py-4 font-semibold text-sm">Category</th>
                    <th className="px-5 py-4 font-semibold text-sm">Priority</th>
                    <th className="px-5 py-4 font-semibold text-sm">Status</th>
                    <th className="px-5 py-4 font-semibold text-sm">Assignee</th>
                    <th className="px-5 py-4 font-semibold text-sm">Age</th>
                    <th className="px-5 py-4 font-semibold text-sm text-right">Action</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-700">
                  {tickets.map((t) => (
                    <React.Fragment key={t.id}>
                      <tr className="hover:bg-slate-700/30 transition">
                        <td className="px-5 py-4 font-medium">{t.id}</td>
                        <td className="px-5 py-4 text-slate-300 text-sm">{t.room_number ? `Room ${t.room_number}` : t.area || "Common"}</td>
                        <td className="px-5 py-4 text-slate-300 text-sm">{t.category}</td>
                        <td className="px-5 py-4 text-slate-300 text-sm">{t.priority}</td>
                        <td className="px-5 py-4"><span className={`px-3 py-1 rounded-full text-xs font-bold border ${statusChip(t.status)}`}>{t.status}</span></td>
                        <td className="px-5 py-4 text-slate-300 text-sm">{t.assignee_name || "—"}</td>
                        <td className={`px-5 py-4 text-sm ${t.overdue ? "text-red-300 font-semibold" : "text-slate-400"}`}>
                          {t.age_days}d {t.overdue && "⚠"}
                        </td>
                        <td className="px-5 py-4 text-right">
                          <div className="flex items-center gap-2 justify-end">
                            {(t.status === "open" || t.status === "assigned") && (
                              <select
                                defaultValue={t.assignee || ""}
                                onChange={(e) => doAssign(t, e.target.value)}
                                disabled={busy}
                                className="px-3 py-1.5 bg-slate-900/50 border border-slate-600 rounded-lg text-sm text-white"
                              >
                                <option value="">Assign…</option>
                                {staff.map((u) => (
                                  <option key={u.user_id} value={u.user_id}>{u.full_name || u.username}</option>
                                ))}
                              </select>
                            )}
                            {t.status === "resolved" && (
                              <button disabled={busy} onClick={() => doVerify(t)} className="bg-green-700 hover:bg-green-800 px-3 py-1.5 rounded-lg text-sm font-semibold flex items-center gap-2 disabled:opacity-50">
                                <FaCheckDouble /> Verify
                              </button>
                            )}
                            <button onClick={() => toggleDetail(t.id)} className="bg-slate-700 hover:bg-slate-600 px-3 py-1.5 rounded-lg text-sm font-semibold">
                              {detail[t.id] ? "Hide" : "Items"}
                            </button>
                          </div>
                        </td>
                      </tr>
                      {detail[t.id] && (
                        <tr className="bg-slate-900/40">
                          <td colSpan={8} className="px-5 py-4">
                            <TicketItems ticket={detail[t.id]} onChanged={() => refreshDetail(t.id)} showToast={showToast} />
                          </td>
                        </tr>
                      )}
                    </React.Fragment>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {toast && (
          <div className="fixed top-6 right-6 z-50">
            <div className={`px-6 py-4 rounded-xl shadow-2xl font-medium backdrop-blur ${toast.type === "error" ? "bg-red-600/90 text-white" : "bg-green-600/90 text-white"}`}>
              {toast.type === "error" ? "❌" : "✅"} {toast.message}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function TicketItems({ ticket, onChanged, showToast }) {
  const [busyId, setBusyId] = useState(null);

  const approve = async (it) => {
    setBusyId(it.id);
    try {
      await approveItem(it.id);
      showToast("Item approved");
      await onChanged();
    } catch (e) {
      showToast(e.message, "error");
    }
    setBusyId(null);
  };

  const purchase = async (it) => {
    const cost = window.prompt(`Actual cost for "${it.item}" (₹):`, it.est_cost ?? "");
    if (cost === null) return;
    setBusyId(it.id);
    try {
      await purchaseItem(it.id, { actual_cost: Number(cost), category: "vendor", post_to_expenses: true });
      showToast("Item purchased — posted to expenses");
      await onChanged();
    } catch (e) {
      showToast(e.message, "error");
    }
    setBusyId(null);
  };

  return (
    <div>
      <div className="text-sm text-slate-300 mb-2"><b className="text-[#E5C07B]">Issue:</b> {ticket.issue}</div>
      <div className="text-sm font-semibold text-[#E5C07B] mb-2">Required parts</div>
      {(!ticket.items || ticket.items.length === 0) ? (
        <div className="text-slate-400 text-sm">No parts listed by the technician yet.</div>
      ) : (
        <table className="w-full text-left text-sm">
          <thead className="text-slate-400">
            <tr>
              <th className="py-1 pr-4">Item</th>
              <th className="py-1 pr-4">Qty</th>
              <th className="py-1 pr-4">Est ₹</th>
              <th className="py-1 pr-4">Status</th>
              <th className="py-1 text-right">Action</th>
            </tr>
          </thead>
          <tbody>
            {ticket.items.map((it) => (
              <tr key={it.id} className="border-t border-slate-700/60">
                <td className="py-2 pr-4">{it.item}</td>
                <td className="py-2 pr-4">{it.qty}</td>
                <td className="py-2 pr-4">{fmt(it.est_cost)}</td>
                <td className="py-2 pr-4">{it.status}</td>
                <td className="py-2 text-right">
                  {it.status === "needed" && (
                    <button disabled={busyId === it.id} onClick={() => approve(it)} className="bg-blue-700 hover:bg-blue-800 px-3 py-1 rounded text-xs font-semibold flex items-center gap-1 ml-auto disabled:opacity-50">
                      <FaCheck /> Approve
                    </button>
                  )}
                  {it.status === "approved" && (
                    <button disabled={busyId === it.id} onClick={() => purchase(it)} className="bg-green-700 hover:bg-green-800 px-3 py-1 rounded text-xs font-semibold ml-auto disabled:opacity-50">
                      Log purchase
                    </button>
                  )}
                  {it.status === "purchased" && <span className="text-slate-400 text-xs">posted #{it.expense_id}</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
