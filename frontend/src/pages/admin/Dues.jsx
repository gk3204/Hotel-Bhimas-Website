// v7s (owner, 16 Oct): travel-agent bills the guest left as "pay later" at check-out. One row per
// bill; filter by agent; receive the money (part or full) - each receipt is an RCPT-n and appears in
// that day's cashier summary under "Due Received".
import React, { useEffect, useMemo, useState } from "react";
import { PageShell, InfoTip } from "../../components/admin/BackofficeUI";
import { listDues, receiveDue, exportDues } from "../../api/backoffice";
import { getAgents } from "../../api/travelAgents";
import { FaMoneyBillWave, FaSearch, FaFileCsv, FaFilePdf, FaEye } from "react-icons/fa";

const fmt = (n) => `₹${Number(n || 0).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

const Dues = () => {
  const [status, setStatus] = useState("open");
  const [agentId, setAgentId] = useState("");
  const [q, setQ] = useState("");
  const [agents, setAgents] = useState([]);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [toast, setToast] = useState(null);
  const [payFor, setPayFor] = useState(null);
  const [pay, setPay] = useState({ amount: "", method: "bank", reference: "", note: "" });
  const [busy, setBusy] = useState(false);
  const [viewFor, setViewFor] = useState(null);

  const showToast = (message, type = "success") => {
    setToast({ message, type });
    setTimeout(() => setToast(null), 3500);
  };

  const params = useMemo(() => ({ status, agent_id: agentId || undefined, q: q.trim() || undefined }), [status, agentId, q]);

  const load = async (p = params) => {
    setLoading(true);
    try {
      setData(await listDues(p));
    } catch (e) {
      showToast(e.message, "error");
    }
    setLoading(false);
  };

  useEffect(() => {
    getAgents().then((a) => setAgents(Array.isArray(a) ? a : a?.data || a?.agents || [])).catch(() => {});
  }, []);
  // status / agent apply at once; the search box applies on Enter or Search
  useEffect(() => { load(); }, [status, agentId]); // eslint-disable-line react-hooks/exhaustive-deps

  const openPay = (r) => {
    setPayFor(r);
    setPay({ amount: String(r.outstanding), method: "bank", reference: "", note: "" });
  };

  const submitPay = async () => {
    const amount = parseFloat(pay.amount);
    if (!amount || amount <= 0) return showToast("Enter the amount received", "error");
    if (amount > payFor.outstanding + 0.005) return showToast(`Only ${fmt(payFor.outstanding)} is due on this bill`, "error");
    setBusy(true);
    try {
      const res = await receiveDue(payFor.folio_id, {
        amount, method: pay.method, reference: pay.reference.trim() || null, note: pay.note.trim() || null,
      });
      setPayFor(null);
      showToast(res.status === "paid"
        ? `${res.receipt_no} recorded - bill ${res.invoice_no} is now fully paid`
        : `${res.receipt_no} recorded - ${fmt(res.outstanding)} still due on ${res.invoice_no}`);
      load();
    } catch (e) {
      showToast(e.message, "error");
    }
    setBusy(false);
  };

  const doExport = async (format) => {
    try { await exportDues(params, format); } catch (e) { showToast(e.message, "error"); }
  };

  const rows = data?.rows || [];
  const t = data?.totals;

  return (
    <PageShell
      icon="🧾"
      title="Dues (pay later)"
      subtitle="Travel-agent bills the guest did not pay at check-out. Record the money here when it arrives - a receipt is issued and it shows in that day's cashier summary."
    >
      <div className="bg-gradient-to-r from-slate-800/50 to-slate-700/50 border border-slate-700 p-6 rounded-2xl shadow-xl mb-8 backdrop-blur">
        <div className="grid grid-cols-1 md:grid-cols-5 gap-4 items-end">
          <div className="flex flex-col">
            <label className="mb-2 text-sm font-semibold text-slate-300">Show</label>
            <select value={status} onChange={(e) => setStatus(e.target.value)} className={inputCls}>
              <option value="open">Still due</option>
              <option value="paid">Paid</option>
              <option value="all">All</option>
            </select>
          </div>
          <div className="flex flex-col">
            <label className="mb-2 text-sm font-semibold text-slate-300">Agent</label>
            <select value={agentId} onChange={(e) => setAgentId(e.target.value)} className={inputCls}>
              <option value="">All agents</option>
              {agents.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
            </select>
          </div>
          <div className="flex flex-col md:col-span-2">
            <label className="mb-2 text-sm font-semibold text-slate-300">Search</label>
            <input value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === "Enter" && load()}
              placeholder="Bill no, guest, phone, room, booking" className={inputCls} />
          </div>
          <div className="flex gap-2">
            <button onClick={() => load()} className="bg-gradient-to-r from-[#E5C07B] to-[#D4AF37] text-slate-900 font-bold px-5 py-2.5 rounded-lg flex items-center gap-2">
              <FaSearch size={14} /> Search
            </button>
            <button onClick={() => doExport("csv")} title="Download CSV" className="bg-slate-700 hover:bg-slate-600 px-3 py-2.5 rounded-lg"><FaFileCsv /></button>
            <button onClick={() => doExport("pdf")} title="Download PDF" className="bg-slate-700 hover:bg-slate-600 px-3 py-2.5 rounded-lg"><FaFilePdf /></button>
          </div>
        </div>
      </div>

      {t && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
          <Stat label="Bills" value={data.count} />
          <Stat label="Due at check-out" value={fmt(t.due_amount)} tip="What these guests left unpaid when they checked out." />
          <Stat label="Received since" value={fmt(t.received)} tip="Money recorded here against these bills." />
          <Stat label="Still due" value={fmt(t.outstanding)} highlight tip="What is still to be received." />
        </div>
      )}

      {(data?.agents || []).length > 1 && (
        <div className="flex flex-wrap gap-3 mb-6">
          {data.agents.map((a) => (
            <button key={a.agent_id || 0} onClick={() => setAgentId(a.agent_id ? String(a.agent_id) : "")}
              className="rounded-xl bg-slate-800/60 border border-slate-700 hover:border-[#E5C07B]/60 px-4 py-2 text-left">
              <div className="text-sm font-semibold">{a.agent}</div>
              <div className="text-xs text-slate-400">{a.bills} bill(s) · still due <span className="text-[#FCD34D] font-bold">{fmt(a.outstanding)}</span></div>
            </button>
          ))}
        </div>
      )}

      <div className="bg-gradient-to-r from-slate-800/50 to-slate-700/50 border border-slate-700 rounded-2xl shadow-xl overflow-hidden backdrop-blur">
        {loading ? (
          <div className="p-12 text-center text-slate-400">Loading…</div>
        ) : rows.length === 0 ? (
          <div className="p-12 text-center text-slate-400"><div className="text-5xl mb-4">📭</div>
            <p>{status === "open" ? "Nothing is due." : "No bills."}</p></div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="bg-slate-900/80 border-b border-slate-700">
                <tr>
                  {["Bill", "Guest", "Rooms", "Agent", "Checked out", "Bill amount", "Due at check-out", "Received", "Still due", "Days", "Status", ""].map((h) => (
                    <th key={h} className="px-4 py-3 font-semibold whitespace-nowrap">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-700">
                {rows.map((r) => (
                  <tr key={r.folio_id} className="hover:bg-slate-700/30">
                    <td className="px-4 py-3 whitespace-nowrap"><div className="font-medium">{r.invoice_no}</div><div className="text-xs text-slate-400">#{r.booking_id}</div></td>
                    <td className="px-4 py-3"><div>{r.guest}</div><div className="text-xs text-slate-400">{r.phone}</div></td>
                    <td className="px-4 py-3">{r.rooms}</td>
                    <td className="px-4 py-3">{r.agent}</td>
                    <td className="px-4 py-3 whitespace-nowrap text-slate-300">{r.checked_out_at}</td>
                    <td className="px-4 py-3 whitespace-nowrap text-slate-300">{fmt(r.bill_amount)}</td>
                    <td className="px-4 py-3 whitespace-nowrap">{fmt(r.due_amount)}</td>
                    <td className="px-4 py-3 whitespace-nowrap text-green-300">{fmt(r.received)}</td>
                    <td className="px-4 py-3 whitespace-nowrap font-bold text-[#E5C07B]">{fmt(r.outstanding)}</td>
                    <td className={`px-4 py-3 ${r.days_pending > 7 ? "text-red-300 font-bold" : "text-slate-300"}`}>{r.status === "due" ? r.days_pending : ""}</td>
                    <td className="px-4 py-3 whitespace-nowrap">
                      {r.status === "due"
                        ? <span className="px-2 py-1 rounded bg-amber-500/20 text-amber-300 text-xs font-bold">DUE</span>
                        : <span className="px-2 py-1 rounded bg-green-600/20 text-green-300 text-xs font-bold" title={r.paid_at}>PAID</span>}
                    </td>
                    <td className="px-4 py-3 whitespace-nowrap space-x-2">
                      <button onClick={() => setViewFor(r)} title="Receipts" className="bg-blue-600 hover:bg-blue-700 px-3 py-2 rounded-lg text-white inline-flex items-center"><FaEye size={12} /></button>
                      {r.status === "due" && (
                        <button onClick={() => openPay(r)} className="bg-green-700 hover:bg-green-800 px-3 py-2 rounded-lg text-white inline-flex items-center gap-1">
                          <FaMoneyBillWave size={12} /> Received
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {payFor && (
        <div className="fixed inset-0 bg-black/70 backdrop-blur-sm flex items-center justify-center z-50 p-4">
          <div className="bg-gradient-to-br from-slate-800 to-slate-900 p-8 rounded-2xl w-full max-w-md border border-slate-700 shadow-2xl">
            <h2 className="text-2xl font-bold mb-1 text-[#E5C07B]">💰 Money received</h2>
            <p className="text-slate-400 text-sm mb-6">Bill {payFor.invoice_no} · {payFor.guest} · {payFor.agent} — still due {fmt(payFor.outstanding)}</p>
            <div className="space-y-4">
              <Field label="Amount received (₹)" type="number" value={pay.amount} onChange={(e) => setPay({ ...pay, amount: e.target.value })} />
              <p className="text-xs text-slate-400 -mt-2">Less than the full amount is fine - the rest stays due.</p>
              <div className="flex flex-col">
                <label className="mb-2 text-sm font-semibold text-slate-300">Mode</label>
                <select value={pay.method} onChange={(e) => setPay({ ...pay, method: e.target.value })} className={inputCls}>
                  <option value="bank">Bank transfer</option>
                  <option value="upi">UPI</option>
                  <option value="cash">Cash</option>
                  <option value="card">Card</option>
                </select>
              </div>
              <Field label="Reference" value={pay.reference} placeholder="UTR / UPI / cheque no" onChange={(e) => setPay({ ...pay, reference: e.target.value })} />
              <Field label="Note" value={pay.note} placeholder="optional" onChange={(e) => setPay({ ...pay, note: e.target.value })} />
            </div>
            <div className="flex justify-end gap-4 mt-8">
              <button onClick={() => setPayFor(null)} className="bg-slate-700 hover:bg-slate-600 px-6 py-2 rounded-lg font-medium">Cancel</button>
              <button onClick={submitPay} disabled={busy} className="bg-green-700 hover:bg-green-800 disabled:opacity-50 px-6 py-2 rounded-lg font-bold">
                {busy ? "Saving…" : "Record receipt"}
              </button>
            </div>
          </div>
        </div>
      )}

      {viewFor && (
        <div className="fixed inset-0 bg-black/70 backdrop-blur-sm flex items-center justify-center z-50 p-4">
          <div className="bg-gradient-to-br from-slate-800 to-slate-900 p-8 rounded-2xl w-full max-w-2xl border border-slate-700 shadow-2xl">
            <h2 className="text-2xl font-bold mb-1 text-[#E5C07B]">📄 {viewFor.invoice_no}</h2>
            <p className="text-slate-400 text-sm mb-4">
              {viewFor.guest} · rooms {viewFor.rooms} · {viewFor.agent} · checked out {viewFor.checked_out_at}
              {viewFor.marked_by ? ` by ${viewFor.marked_by}` : ""}
            </p>
            <div className="grid grid-cols-3 gap-3 mb-5 text-sm">
              <Mini label="Due at check-out" value={fmt(viewFor.due_amount)} />
              <Mini label="Received" value={fmt(viewFor.received)} />
              <Mini label="Still due" value={fmt(viewFor.outstanding)} />
            </div>
            <div className="rounded-lg border border-slate-700 overflow-hidden">
              <table className="w-full text-left text-sm">
                <thead className="bg-slate-900/80 border-b border-slate-700">
                  <tr><th className="px-4 py-2">Receipt</th><th className="px-4 py-2">When</th><th className="px-4 py-2">Amount</th><th className="px-4 py-2">Mode</th><th className="px-4 py-2">Reference</th><th className="px-4 py-2">By</th></tr>
                </thead>
                <tbody className="divide-y divide-slate-700">
                  {viewFor.payments.length === 0 ? (
                    <tr><td colSpan={6} className="px-4 py-4 text-center text-slate-400">Nothing received yet.</td></tr>
                  ) : viewFor.payments.map((p) => (
                    <tr key={p.payment_id}>
                      <td className="px-4 py-2">{p.receipt_no}</td>
                      <td className="px-4 py-2 text-slate-300">{p.at}</td>
                      <td className="px-4 py-2 text-green-300">{fmt(p.amount)}</td>
                      <td className="px-4 py-2 text-slate-300">{p.method}</td>
                      <td className="px-4 py-2 text-slate-300">{p.reference || "—"}</td>
                      <td className="px-4 py-2 text-slate-300">{p.by}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="flex justify-end mt-6">
              <button onClick={() => setViewFor(null)} className="bg-slate-700 hover:bg-slate-600 px-6 py-2 rounded-lg font-medium">Close</button>
            </div>
          </div>
        </div>
      )}

      {toast && (
        <div className="fixed top-6 right-6 z-50">
          <div className={`px-6 py-4 rounded-xl shadow-2xl font-medium ${toast.type === "error" ? "bg-red-600/90 text-white" : "bg-green-600/90 text-white"}`}>
            {toast.type === "error" ? "❌" : "✅"} {toast.message}
          </div>
        </div>
      )}
    </PageShell>
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

const Stat = ({ label, value, highlight, tip }) => (
  <div className={`rounded-2xl border p-5 ${highlight ? "bg-[#E5C07B]/10 border-[#E5C07B]/40" : "bg-slate-800/50 border-slate-700"}`}>
    <p className="text-slate-400 text-sm mb-1">{label}{tip && <InfoTip text={tip} />}</p>
    <p className={`text-2xl font-bold ${highlight ? "text-[#FCD34D]" : "text-white"}`}>{value}</p>
  </div>
);

const Mini = ({ label, value }) => (
  <div className="rounded-lg bg-slate-900/50 border border-slate-700 p-3">
    <p className="text-slate-400 text-xs mb-1">{label}</p>
    <p className="font-bold text-white">{value}</p>
  </div>
);

export default Dues;
