import React, { useEffect, useState } from "react";
import { PageShell } from "../../components/admin/BackofficeUI";
import ConfigPanel from "../../components/admin/ConfigPanel";
import { groupByKey } from "../../components/admin/settingsGroups";
import { getCashConfig, getShifts, getShiftDetail, openShiftPdf } from "../../api/cashShift";
import { FaSyncAlt } from "react-icons/fa";

const fmt = (n) =>
  n === null || n === undefined
    ? "—"
    : `₹${Number(n).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

const statusChip = (s) =>
  s === "open"
    ? "bg-yellow-500/20 text-yellow-300 border-yellow-500/30"
    : "bg-slate-500/20 text-slate-300 border-slate-500/30";

const CashShiftSettings = () => {
  const [loading, setLoading] = useState(false);
  const [toast, setToast] = useState(null);

  const [form, setForm] = useState({ cycle: "shift", variance_threshold: 100, variance_alert_enabled: true });
  const [shifts, setShifts] = useState([]);
  // v7d: the shift report on screen
  const [detail, setDetail] = useState(null);
  const showDetail = async (id) => {
    try { setDetail({ id, ...(await getShiftDetail(id)) }); } catch (e) { showToast(e.message, "error"); }
  };
  const pdf = async (id) => { try { await openShiftPdf(id); } catch (e) { showToast(e.message, "error"); } };

  const showToast = (message, type = "success") => {
    setToast({ message, type });
    setTimeout(() => setToast(null), 3500);
  };

  const load = async () => {
    setLoading(true);
    try {
      const cfg = await getCashConfig();
      setForm({
        cycle: cfg.cycle || "shift",
        variance_threshold: cfg.variance_threshold ?? 100,
        variance_alert_enabled: !!cfg.variance_alert_enabled,
      });
      const list = await getShifts(50);
      setShifts(list.data || []);
    } catch (e) {
      showToast(e.message, "error");
    }
    setLoading(false);
  };

  useEffect(() => { load(); }, []); // eslint-disable-line

  // `form` is no longer edited here — it is kept so the shift table can still highlight
  // an over-threshold variance. The editor itself lives in ConfigPanel (FE-12).
  const flagged = (s) =>
    s.status === "closed" && form.variance_alert_enabled &&
    Math.abs(Number(s.variance || 0)) > Number(form.variance_threshold || 0);

  return (
    <PageShell
      icon="🧾"
      title="Cash & Shift"
      subtitle="Set the reconciliation cycle and the variance-alert threshold, and review recent shifts. A closed shift whose variance exceeds the threshold raises an owner alert."
    >

        {/* Config — the same editor the Settings hub renders (FE-12). */}
        <ConfigPanel group={groupByKey("cash")} showToast={showToast} />

        {/* Recent shifts */}
        <div className="bg-gradient-to-r from-slate-800/50 to-slate-700/50 border border-slate-700 rounded-2xl shadow-xl overflow-hidden backdrop-blur">
          <div className="px-6 py-4 border-b border-slate-700">
            <h2 className="text-lg font-bold text-white">Recent shifts</h2>
          </div>
          {loading ? (
            <div className="p-12 flex items-center justify-center">
              <div className="animate-spin"><div className="h-12 w-12 border-4 border-[#E5C07B] border-t-[#D4AF37] rounded-full"></div></div>
            </div>
          ) : shifts.length === 0 ? (
            <div className="p-12 text-center text-slate-400"><div className="text-5xl mb-4">🧾</div><p>No shifts recorded yet.</p></div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left">
                <thead className="bg-slate-900/80 border-b border-slate-700">
                  <tr>
                    <th className="px-5 py-4 font-semibold text-sm">#</th>
                    <th className="px-5 py-4 font-semibold text-sm">Staff</th>
                    <th className="px-5 py-4 font-semibold text-sm">Opened</th>
                    <th className="px-5 py-4 font-semibold text-sm">Status</th>
                    <th className="px-5 py-4 font-semibold text-sm text-right">Float</th>
                    <th className="px-5 py-4 font-semibold text-sm text-right">Collections</th>
                    <th className="px-5 py-4 font-semibold text-sm text-right">Expenses</th>
                    <th className="px-5 py-4 font-semibold text-sm text-right">Expected</th>
                    <th className="px-5 py-4 font-semibold text-sm text-right">Counted</th>
                    <th className="px-5 py-4 font-semibold text-sm text-right">Variance</th>
                    <th className="px-5 py-4 font-semibold text-sm">Report</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-700">
                  {shifts.map((s) => (
                    <tr key={s.id} className="hover:bg-slate-700/30 transition">
                      <td className="px-5 py-4 font-medium">{s.id}</td>
                      <td className="px-5 py-4 text-slate-300">{s.staff_name || "—"}</td>
                      <td className="px-5 py-4 text-slate-400 text-sm whitespace-nowrap">{(s.opened_at || "").slice(0, 16).replace("T", " ")}</td>
                      <td className="px-5 py-4"><span className={`px-3 py-1 rounded-full text-xs font-bold border ${statusChip(s.status)}`}>{s.status}</span></td>
                      <td className="px-5 py-4 text-right text-slate-300">{fmt(s.opening_balance)}</td>
                      <td className="px-5 py-4 text-right text-slate-300">{fmt(s.collections_cash)}</td>
                      <td className="px-5 py-4 text-right text-slate-300">{fmt(s.expenses_total)}</td>
                      <td className="px-5 py-4 text-right text-slate-300">{fmt(s.expected_cash)}</td>
                      <td className="px-5 py-4 text-right text-slate-300">{fmt(s.counted_cash)}</td>
                      <td className={`px-5 py-4 text-right font-semibold ${flagged(s) ? "text-red-300" : "text-slate-300"}`}>
                        {s.variance === null || s.variance === undefined ? "—" : fmt(s.variance)}
                        {flagged(s) && <span className="ml-2 text-xs">⚠</span>}
                      </td>
                      <td className="px-5 py-4 whitespace-nowrap">
                        <button onClick={() => showDetail(s.id)}
                          className="bg-slate-700 hover:bg-slate-600 px-3 py-1.5 rounded-lg text-white text-xs font-semibold mr-2">Details</button>
                        <button onClick={() => pdf(s.id)}
                          className="bg-[#E5C07B]/80 hover:bg-[#E5C07B] px-3 py-1.5 rounded-lg text-slate-900 text-xs font-semibold">PDF</button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {/* v7d: one shift's receipts, paid-outs, prepaid receipts and checked-out bills (incl. nil amount) */}
        {detail && (
          <div className="fixed inset-0 bg-black/70 z-50 flex items-start justify-center p-4 overflow-y-auto"
               onClick={() => setDetail(null)}>
            <div className="bg-slate-900 border border-slate-700 rounded-2xl w-full max-w-5xl mt-10 p-5"
                 onClick={(e) => e.stopPropagation()}>
              <div className="flex items-center justify-between mb-3">
                <h3 className="text-lg font-bold text-[#E5C07B]">
                  Shift #{detail.id} · {detail.shift?.staff_name || "—"} · {(detail.shift?.opened_at || "").slice(0, 16).replace("T", " ")}
                  {detail.shift?.closed_at ? ` → ${(detail.shift.closed_at || "").slice(0, 16).replace("T", " ")}` : " (open)"}
                </h3>
                <div className="flex gap-2">
                  <button onClick={() => pdf(detail.id)}
                    className="bg-[#E5C07B]/80 hover:bg-[#E5C07B] px-3 py-1.5 rounded-lg text-slate-900 text-xs font-semibold">PDF</button>
                  <button onClick={() => setDetail(null)}
                    className="bg-slate-700 hover:bg-slate-600 px-3 py-1.5 rounded-lg text-white text-xs font-semibold">Close</button>
                </div>
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-left text-sm">
                  <thead className="bg-slate-800">
                    <tr>{(detail.receipts?.columns || []).map((c) => <th key={c} className="px-3 py-2 whitespace-nowrap">{c}</th>)}</tr>
                  </thead>
                  <tbody className="divide-y divide-slate-800">
                    {(detail.receipts?.rows || []).map((r, i) => {
                      const n = (detail.receipts?.columns || []).length;
                      if (r && !Array.isArray(r) && r.section !== undefined)
                        return <tr key={i}><td colSpan={n} className="px-3 py-2 font-semibold text-[#E5C07B] whitespace-pre">{r.section}</td></tr>;
                      if (r && !Array.isArray(r) && r.subtotal)
                        return <tr key={i} className="bg-slate-800/60 font-semibold">{r.subtotal.map((c, j) => <td key={j} className="px-3 py-2 whitespace-nowrap">{c}</td>)}</tr>;
                      return <tr key={i}>{(r || []).map((c, j) => <td key={j} className="px-3 py-2 whitespace-nowrap text-slate-300">{c}</td>)}</tr>;
                    })}
                    {detail.receipts?.totals && (
                      <tr className="bg-slate-800 font-bold">{detail.receipts.totals.map((c, j) => <td key={j} className="px-3 py-2 whitespace-nowrap">{c}</td>)}</tr>
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        )}

        {/* TOAST */}
        {toast && (
          <div className="fixed top-6 right-6 z-50">
            <div className={`px-6 py-4 rounded-xl shadow-2xl font-medium backdrop-blur ${toast.type === "error" ? "bg-red-600/90 text-white" : "bg-green-600/90 text-white"}`}>
              {toast.type === "error" ? "❌" : "✅"} {toast.message}
            </div>
          </div>
        )}
    </PageShell>
  );
};

const inputCls =
  "px-4 py-2 bg-slate-900/50 border border-slate-600 rounded-lg text-white focus:outline-none focus:border-[#E5C07B] focus:ring-2 focus:ring-[#E5C07B]/20 transition";

export default CashShiftSettings;
