import React, { useEffect, useState } from "react";
import { getSettlement, getAgentSettlement, recordPayout } from "../../api/settlements";
import { FaMoneyBillWave, FaEye, FaSearch } from "react-icons/fa";

const fmt = (n) => `₹${Number(n || 0).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

const AgentSettlements = () => {
  const [range, setRange] = useState({ from: "", to: "" });
  const [report, setReport] = useState(null);
  const [loading, setLoading] = useState(false);
  const [toast, setToast] = useState(null);

  const [detailFor, setDetailFor] = useState(null);
  const [detail, setDetail] = useState(null);

  const [payoutFor, setPayoutFor] = useState(null);
  const [payout, setPayout] = useState({ amount: "", paid_on: "", mode: "bank", reference: "", note: "" });

  useEffect(() => { load(); }, []);

  const showToast = (message, type = "success") => {
    setToast({ message, type });
    setTimeout(() => setToast(null), 3000);
  };

  const load = async () => {
    setLoading(true);
    try {
      setReport(await getSettlement(range.from || undefined, range.to || undefined));
    } catch (e) {
      showToast(e.message, "error");
    }
    setLoading(false);
  };

  const openDetail = async (row) => {
    setDetailFor(row);
    setDetail(null);
    try {
      setDetail(await getAgentSettlement(row.agent_id, range.from || undefined, range.to || undefined));
    } catch (e) {
      showToast(e.message, "error");
    }
  };

  const openPayout = (row) => {
    setPayoutFor(row);
    setPayout({
      amount: row.outstanding > 0 ? String(row.outstanding) : "",
      paid_on: new Date().toISOString().slice(0, 10),
      mode: "bank", reference: "", note: "",
    });
  };

  const submitPayout = async () => {
    if (!payout.amount || !payout.paid_on) return showToast("Amount and date required", "error");
    try {
      await recordPayout(payoutFor.agent_id, {
        amount: parseFloat(payout.amount),
        paid_on: payout.paid_on,
        mode: payout.mode,
        reference: payout.reference || null,
        note: payout.note || null,
      });
      setPayoutFor(null);
      load();
      showToast("Payout recorded");
    } catch (e) {
      showToast(e.message, "error");
    }
  };

  const totals = report?.totals;

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-900 via-slate-800 to-slate-900 text-white p-6">
      <div className="max-w-7xl mx-auto">
        <div className="mb-8">
          <h1 className="text-4xl font-bold mb-2 bg-gradient-to-r from-[#E5C07B] to-[#FCD34D] bg-clip-text text-transparent">
            💼 Agent Settlement
          </h1>
          <p className="text-slate-400">Booked, commission accrued, paid, and outstanding per agent. Leave dates empty for the lifetime balance.</p>
        </div>

        {/* Filter */}
        <div className="bg-gradient-to-r from-slate-800/50 to-slate-700/50 border border-slate-700 p-6 rounded-2xl shadow-xl mb-8 backdrop-blur">
          <div className="grid grid-cols-1 md:grid-cols-4 gap-4 items-end">
            <Field label="From (check-in)" type="date" value={range.from} onChange={(e) => setRange({ ...range, from: e.target.value })} />
            <Field label="To (check-in)" type="date" value={range.to} onChange={(e) => setRange({ ...range, to: e.target.value })} />
            <div>
              <button onClick={load} className="bg-gradient-to-r from-[#E5C07B] to-[#D4AF37] text-slate-900 font-bold px-6 py-2.5 rounded-lg transition-all hover:scale-105 flex items-center gap-2">
                <FaSearch size={14} /> Apply
              </button>
            </div>
          </div>
        </div>

        {/* Totals */}
        {totals && (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-8">
            <Stat label="Room Revenue" value={fmt(totals.room_revenue)} />
            <Stat label="Commission Accrued" value={fmt(totals.commission_accrued)} />
            <Stat label="Commission Paid" value={fmt(totals.commission_paid)} />
            <Stat label="Outstanding" value={fmt(totals.outstanding)} highlight />
          </div>
        )}

        {/* Table */}
        <div className="bg-gradient-to-r from-slate-800/50 to-slate-700/50 border border-slate-700 rounded-2xl shadow-xl overflow-hidden backdrop-blur">
          <div className="p-6 border-b border-slate-700"><h2 className="text-2xl font-bold">📊 Agents</h2></div>
          {loading ? (
            <div className="p-12 flex items-center justify-center">
              <div className="animate-spin inline-block"><div className="h-12 w-12 border-4 border-[#E5C07B] border-t-[#D4AF37] rounded-full"></div></div>
            </div>
          ) : !report || report.agents.length === 0 ? (
            <div className="p-12 text-center text-slate-400"><div className="text-5xl mb-4">📭</div><p>No agents to settle.</p></div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left">
                <thead className="bg-slate-900/80 border-b border-slate-700">
                  <tr>
                    <th className="px-6 py-4 font-semibold text-sm">Agent</th>
                    <th className="px-6 py-4 font-semibold text-sm">Comm %</th>
                    <th className="px-6 py-4 font-semibold text-sm">Bookings</th>
                    <th className="px-6 py-4 font-semibold text-sm">Room Revenue</th>
                    <th className="px-6 py-4 font-semibold text-sm">Accrued</th>
                    <th className="px-6 py-4 font-semibold text-sm">Paid</th>
                    <th className="px-6 py-4 font-semibold text-sm">Outstanding</th>
                    <th className="px-6 py-4 font-semibold text-sm">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-700">
                  {report.agents.map((r) => (
                    <tr key={r.agent_id} className="hover:bg-slate-700/30 transition">
                      <td className="px-6 py-4 font-medium">{r.agent_name}</td>
                      <td className="px-6 py-4 text-slate-300">{r.commission_percent}%</td>
                      <td className="px-6 py-4 text-slate-300">{r.bookings}</td>
                      <td className="px-6 py-4 text-slate-300">{fmt(r.room_revenue)}</td>
                      <td className="px-6 py-4 text-slate-300">{fmt(r.commission_accrued)}</td>
                      <td className="px-6 py-4 text-green-300">{fmt(r.commission_paid)}</td>
                      <td className="px-6 py-4 font-bold text-[#E5C07B]">{fmt(r.outstanding)}</td>
                      <td className="px-6 py-4 space-x-2 whitespace-nowrap">
                        <button onClick={() => openDetail(r)} title="View detail"
                          className="bg-blue-600 hover:bg-blue-700 px-3 py-2 rounded-lg text-white inline-flex items-center gap-1"><FaEye size={13} /></button>
                        <button onClick={() => openPayout(r)} title="Record payout"
                          className="bg-green-700 hover:bg-green-800 px-3 py-2 rounded-lg text-white inline-flex items-center gap-1"><FaMoneyBillWave size={13} /> Pay</button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {/* DETAIL MODAL */}
        {detailFor && (
          <div className="fixed inset-0 bg-black/70 backdrop-blur-sm flex items-center justify-center z-50 p-4">
            <div className="bg-gradient-to-br from-slate-800 to-slate-900 p-8 rounded-2xl w-full max-w-3xl border border-slate-700 shadow-2xl max-h-[85vh] overflow-y-auto">
              <h2 className="text-2xl font-bold mb-1 text-[#E5C07B]">📄 {detailFor.agent_name}</h2>
              {!detail ? (
                <p className="text-slate-400 py-8">Loading…</p>
              ) : (
                <>
                  <div className="grid grid-cols-2 md:grid-cols-4 gap-3 my-5 text-sm">
                    <Mini label="Bookings" value={detail.summary.bookings} />
                    <Mini label="Accrued" value={fmt(detail.summary.commission_accrued)} />
                    <Mini label="Paid" value={fmt(detail.summary.commission_paid)} />
                    <Mini label="Outstanding" value={fmt(detail.summary.outstanding)} />
                  </div>
                  <h3 className="font-bold text-slate-200 mt-4 mb-2">Bookings</h3>
                  <div className="rounded-lg border border-slate-700 overflow-hidden mb-5">
                    <table className="w-full text-left text-sm">
                      <thead className="bg-slate-900/80 border-b border-slate-700">
                        <tr><th className="px-4 py-2">#</th><th className="px-4 py-2">Guest</th><th className="px-4 py-2">Stay</th><th className="px-4 py-2">Room Total</th><th className="px-4 py-2">Commission</th></tr>
                      </thead>
                      <tbody className="divide-y divide-slate-700">
                        {detail.bookings.length === 0 ? (
                          <tr><td colSpan={5} className="px-4 py-4 text-center text-slate-400">No bookings in range.</td></tr>
                        ) : detail.bookings.map((b) => (
                          <tr key={b.booking_id}>
                            <td className="px-4 py-2 text-[#FCD34D]">#{b.booking_id}</td>
                            <td className="px-4 py-2">{b.guest_name}</td>
                            <td className="px-4 py-2 text-slate-300">{b.check_in} → {b.check_out}</td>
                            <td className="px-4 py-2 text-slate-300">{fmt(b.room_total)}</td>
                            <td className="px-4 py-2 text-[#E5C07B] font-semibold">{fmt(b.commission_amount)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <h3 className="font-bold text-slate-200 mb-2">Payouts</h3>
                  <div className="rounded-lg border border-slate-700 overflow-hidden">
                    <table className="w-full text-left text-sm">
                      <thead className="bg-slate-900/80 border-b border-slate-700">
                        <tr><th className="px-4 py-2">Date</th><th className="px-4 py-2">Amount</th><th className="px-4 py-2">Mode</th><th className="px-4 py-2">Reference</th></tr>
                      </thead>
                      <tbody className="divide-y divide-slate-700">
                        {detail.payments.length === 0 ? (
                          <tr><td colSpan={4} className="px-4 py-4 text-center text-slate-400">No payouts yet.</td></tr>
                        ) : detail.payments.map((p) => (
                          <tr key={p.id}>
                            <td className="px-4 py-2 text-slate-300">{p.paid_on}</td>
                            <td className="px-4 py-2 text-green-300">{fmt(p.amount)}</td>
                            <td className="px-4 py-2 text-slate-300">{p.mode}</td>
                            <td className="px-4 py-2 text-slate-300">{p.reference || "—"}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </>
              )}
              <div className="flex justify-end mt-6">
                <button onClick={() => { setDetailFor(null); setDetail(null); }} className="bg-slate-700 hover:bg-slate-600 px-6 py-2 rounded-lg font-medium transition">Close</button>
              </div>
            </div>
          </div>
        )}

        {/* PAYOUT MODAL */}
        {payoutFor && (
          <div className="fixed inset-0 bg-black/70 backdrop-blur-sm flex items-center justify-center z-50 p-4">
            <div className="bg-gradient-to-br from-slate-800 to-slate-900 p-8 rounded-2xl w-full max-w-md border border-slate-700 shadow-2xl">
              <h2 className="text-2xl font-bold mb-1 text-[#E5C07B]">💸 Record Payout</h2>
              <p className="text-slate-400 text-sm mb-6">{payoutFor.agent_name} — outstanding {fmt(payoutFor.outstanding)}</p>
              <div className="space-y-4">
                <Field label="Amount (₹)" type="number" value={payout.amount} onChange={(e) => setPayout({ ...payout, amount: e.target.value })} />
                <Field label="Paid On" type="date" value={payout.paid_on} onChange={(e) => setPayout({ ...payout, paid_on: e.target.value })} />
                <div className="flex flex-col">
                  <label className="mb-2 text-sm font-semibold text-slate-300">Mode</label>
                  <select value={payout.mode} onChange={(e) => setPayout({ ...payout, mode: e.target.value })} className={inputCls}>
                    <option value="bank">Bank</option>
                    <option value="upi">UPI</option>
                    <option value="cash">Cash</option>
                    <option value="adjustment">Adjustment</option>
                  </select>
                </div>
                <Field label="Reference" value={payout.reference} placeholder="UPI / bank / cheque ref" onChange={(e) => setPayout({ ...payout, reference: e.target.value })} />
                <Field label="Note" value={payout.note} placeholder="optional" onChange={(e) => setPayout({ ...payout, note: e.target.value })} />
              </div>
              <div className="flex justify-end gap-4 mt-8">
                <button onClick={() => setPayoutFor(null)} className="bg-slate-700 hover:bg-slate-600 px-6 py-2 rounded-lg font-medium transition">Cancel</button>
                <button onClick={submitPayout} className="bg-green-700 hover:bg-green-800 px-6 py-2 rounded-lg font-bold transition">Record</button>
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
      </div>
    </div>
  );
};

const inputCls =
  "px-4 py-2 bg-slate-900/50 border border-slate-600 rounded-lg text-white focus:outline-none focus:border-[#E5C07B] focus:ring-2 focus:ring-[#E5C07B]/20 transition";

const Field = ({ label, ...props }) => (
  <div className="flex flex-col">
    <label className="mb-2 text-sm font-semibold text-slate-300">{label}</label>
    <input {...props} className={inputCls} />
  </div>
);

const Stat = ({ label, value, highlight }) => (
  <div className={`rounded-2xl border p-5 ${highlight ? "bg-[#E5C07B]/10 border-[#E5C07B]/40" : "bg-slate-800/50 border-slate-700"}`}>
    <p className="text-slate-400 text-sm mb-1">{label}</p>
    <p className={`text-2xl font-bold ${highlight ? "text-[#FCD34D]" : "text-white"}`}>{value}</p>
  </div>
);

const Mini = ({ label, value }) => (
  <div className="rounded-lg bg-slate-900/50 border border-slate-700 p-3">
    <p className="text-slate-400 text-xs mb-1">{label}</p>
    <p className="font-bold text-white">{value}</p>
  </div>
);

export default AgentSettlements;
