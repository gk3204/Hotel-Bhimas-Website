import React, { useEffect, useState } from "react";
import { PageShell } from "../../components/admin/BackofficeUI";
import ConfigPanel from "../../components/admin/ConfigPanel";
import { groupByKey } from "../../components/admin/settingsGroups";
import { getCashConfig, getShifts } from "../../api/cashShift";
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
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

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
