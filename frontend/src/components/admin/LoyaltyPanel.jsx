import React, { useEffect, useState } from "react";
import { FaGift, FaSave } from "react-icons/fa";
import { getCrmConfig, updateCrmConfig } from "../../api/crm";

// Master on/off (and rates) for the loyalty programme. When off, no points accrue at checkout and
// redemption is refused server-side (routers/crm.py gates both on crm_config.loyalty_enabled).
export default function LoyaltyPanel({ showToast }) {
  const [enabled, setEnabled] = useState(true);
  const [ppr, setPpr] = useState("");   // points earned per ₹1
  const [rpp, setRpp] = useState("");   // ₹ value of 1 point
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      const c = await getCrmConfig();
      setEnabled(c.loyalty_enabled !== false);
      setPpr(String(c.loyalty_points_per_rupee ?? ""));
      setRpp(String(c.loyalty_rupee_per_point ?? ""));
    } catch (e) { showToast(e.message, "error"); }
    setLoading(false);
  };
  useEffect(() => { load(); }, []); // eslint-disable-line

  const save = async () => {
    setSaving(true);
    try {
      await updateCrmConfig({
        loyalty_enabled: enabled,
        loyalty_points_per_rupee: Number(ppr) || 0,
        loyalty_rupee_per_point: Number(rpp) || 0,
      });
      showToast("Loyalty settings saved.", "success");
      await load();
    } catch (e) { showToast(e.message, "error"); }
    setSaving(false);
  };

  if (loading) return <div className="p-8 text-slate-400">Loading…</div>;

  return (
    <div className="bg-gradient-to-r from-slate-800/50 to-slate-700/50 border border-slate-700 rounded-2xl p-6 max-w-xl">
      <h2 className="text-lg font-bold text-[#E5C07B] mb-1 flex items-center gap-2"><FaGift /> Loyalty points</h2>
      <p className="text-slate-400 text-sm mb-5">
        Turn the loyalty programme on or off. When off, no points are earned at checkout and points can't be redeemed.
      </p>

      <label className="flex items-center gap-3 mb-5 cursor-pointer">
        <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} className="w-5 h-5 accent-[#E5C07B]" />
        <span className="font-semibold text-white">Loyalty programme enabled</span>
      </label>

      <div className={enabled ? "" : "opacity-40 pointer-events-none"}>
        <div className="grid grid-cols-2 gap-4 mb-5">
          <div className="flex flex-col">
            <label className="text-xs text-slate-400 mb-1">Points earned per ₹1 spent</label>
            <input type="number" min="0" step="0.001" value={ppr} onChange={(e) => setPpr(e.target.value)}
              className="px-4 py-2 bg-slate-900/50 border border-slate-600 rounded-lg text-white focus:outline-none focus:border-[#E5C07B]" />
          </div>
          <div className="flex flex-col">
            <label className="text-xs text-slate-400 mb-1">₹ value of 1 point (on redeem)</label>
            <input type="number" min="0" step="0.01" value={rpp} onChange={(e) => setRpp(e.target.value)}
              className="px-4 py-2 bg-slate-900/50 border border-slate-600 rounded-lg text-white focus:outline-none focus:border-[#E5C07B]" />
          </div>
        </div>
      </div>

      <button onClick={save} disabled={saving}
        className="bg-gradient-to-r from-[#E5C07B] to-[#D4AF37] text-slate-900 font-bold px-6 py-2.5 rounded-lg hover:scale-105 transition disabled:opacity-50 flex items-center gap-2">
        <FaSave size={12} /> {saving ? "Saving…" : "Save"}
      </button>
    </div>
  );
}
