// v6m — "Check Razorpay for missed payments" (Payments page, admin).
//
// Production had never received a Razorpay webhook, so a guest who paid and did not make it back to
// the site (booking 181) was recorded nowhere and the booking timed out. The server now also checks
// Razorpay every 15 minutes for the last two days; this panel looks further back on demand. A dry
// run lists what Razorpay says was paid; "Record these" settles them: the booking is reinstated
// when its rooms are still free, otherwise the money is recorded and flagged for a refund.
import React, { useState } from "react";
import { FaSearchDollar } from "react-icons/fa";

const BASE_URL = import.meta.env.VITE_API_URL || "http://127.0.0.1:8000";
const headers = () => ({
  "Content-Type": "application/json",
  Authorization: `Bearer ${localStorage.getItem("adminToken")}`,
});

async function reconcile(days, dryRun) {
  const res = await fetch(`${BASE_URL}/payments/razorpay/reconcile?days=${days}&dry_run=${dryRun}`,
    { method: "POST", headers: headers() });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data?.detail || `HTTP ${res.status}`);
  return data;
}

const OUTCOME = {
  would_settle: "Paid on Razorpay — not recorded here yet",
  reinstated: "Recorded — booking reinstated",
  confirmed: "Recorded — booking confirmed",
  paid_no_room: "Recorded — rooms gone: REFUND the guest (see Fraud alerts)",
  already: "Already recorded",
  recorded: "Recorded",
};

export default function RazorpayReconcilePanel({ onDone }) {
  const [days, setDays] = useState(30);
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const run = async (dryRun) => {
    setBusy(true); setError("");
    try {
      const r = await reconcile(days, dryRun);
      setResult(r);
      if (!dryRun && onDone) onDone();
    } catch (e) { setError(e.message || "Check failed"); }
    finally { setBusy(false); }
  };

  const pending = (result?.found || []).filter((f) => f.outcome === "would_settle");
  return (
    <div className="mb-8 p-5 rounded-2xl border border-[#E5C07B]/30 bg-slate-800/40">
      <div className="flex flex-wrap items-center gap-3">
        <FaSearchDollar className="text-[#E5C07B]" />
        <div className="flex-1 min-w-[220px]">
          <div className="font-semibold text-white">Missed website payments</div>
          <div className="text-xs text-slate-400">
            Asks Razorpay about website orders still marked unpaid. Runs by itself every 15 minutes for the
            last 2 days; use this to look further back.
          </div>
        </div>
        <label className="text-xs text-slate-400">Last
          <input type="number" min="1" max="120" value={days} onChange={(e) => setDays(e.target.value)}
            className="mx-2 w-16 px-2 py-1 bg-slate-900/50 border border-slate-600 rounded text-white" />days</label>
        <button onClick={() => run(true)} disabled={busy}
          className="bg-slate-700 hover:bg-slate-600 px-4 py-2 rounded-lg text-white text-sm font-semibold disabled:opacity-50">
          {busy ? "Checking…" : "Check Razorpay"}
        </button>
      </div>
      {error && <div className="mt-3 text-sm text-red-300">{error}</div>}
      {result && result.configured === false && (
        <div className="mt-3 text-sm text-amber-300">Razorpay keys are not set on the server, so it cannot be asked.</div>
      )}
      {result && result.configured !== false && (
        <div className="mt-4 text-sm">
          <div className="text-slate-400 mb-2">
            Checked {result.checked} unpaid order(s).{" "}
            {result.found.length === 0 ? "Razorpay shows no missed payment." : `${result.found.length} paid on Razorpay:`}
            {result.errors ? ` (${result.errors} could not be looked up)` : ""}
          </div>
          {result.found.length > 0 && (
            <div className="space-y-1">
              {result.found.map((f) => (
                <div key={f.payment_id} className="flex flex-wrap justify-between gap-2 px-3 py-2 rounded bg-slate-900/50">
                  <span className="text-white">#{f.booking_id} · {f.guest_name || "—"} · {f.check_in} → {f.check_out}</span>
                  <span className="text-[#E5C07B]">₹{Number(f.amount).toFixed(2)} · {f.gateway_payment_id}</span>
                  <span className={f.outcome === "paid_no_room" ? "text-red-300" : "text-slate-300"}>
                    {OUTCOME[f.outcome] || f.outcome}
                  </span>
                </div>
              ))}
            </div>
          )}
          {pending.length > 0 && (
            <button onClick={() => run(false)} disabled={busy}
              className="mt-3 bg-[#E5C07B] hover:bg-[#D4AF37] px-4 py-2 rounded-lg text-slate-900 font-semibold disabled:opacity-50">
              Record these {pending.length} payment(s)
            </button>
          )}
        </div>
      )}
    </div>
  );
}
