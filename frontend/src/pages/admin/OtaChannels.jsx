import React, { useEffect, useState } from "react";
import { FaGlobe, FaSyncAlt, FaSave, FaEnvelopeOpenText, FaBalanceScale, FaListUl } from "react-icons/fa";
import {
  getChannels, updateChannel, getOtaBookings, getReconciliation,
  getSettlements, createSettlement, getDrafts, confirmDraft, dismissDraft, pollMailbox,
} from "../../api/ota";
import { getRoomTypes } from "../../api/roomTypes";

const fmt = (n) =>
  n === null || n === undefined
    ? "—"
    : `₹${Number(n).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

const inputCls =
  "px-4 py-2 bg-slate-900/50 border border-slate-600 rounded-lg text-white focus:outline-none focus:border-[#E5C07B] focus:ring-2 focus:ring-[#E5C07B]/20 transition";

const iso = (d) => d.toISOString().slice(0, 10);
const CHANNEL_LABEL = {
  makemytrip: "MakeMyTrip", goibibo: "Goibibo", booking_com: "Booking.com",
  agoda: "Agoda", other_ota: "Other OTA",
};

const OtaChannels = () => {
  const [tab, setTab] = useState("channels");
  const [toast, setToast] = useState(null);
  const showToast = (message, type = "success") => {
    setToast({ message, type });
    setTimeout(() => setToast(null), 3500);
  };

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-900 via-slate-800 to-slate-900 text-white p-6">
      <div className="max-w-7xl mx-auto">
        <div className="mb-6">
          <h1 className="text-4xl font-bold mb-2 bg-gradient-to-r from-[#E5C07B] to-[#FCD34D] bg-clip-text text-transparent flex items-center gap-3">
            <FaGlobe /> OTA Tracking
          </h1>
          <p className="text-slate-400">
            Track MakeMyTrip / Goibibo / Booking.com / Agoda business inside the PMS — commission, net payout,
            payout reconciliation, and email-parsed booking drafts. (Two-way channel-manager sync is a later phase.)
          </p>
        </div>

        <div className="flex flex-wrap gap-2 mb-6">
          {[
            ["channels", "Channels", <FaGlobe key="i" />],
            ["bookings", "OTA bookings", <FaListUl key="i" />],
            ["recon", "Payout reconciliation", <FaBalanceScale key="i" />],
            ["drafts", "Email drafts", <FaEnvelopeOpenText key="i" />],
          ].map(([id, label, icon]) => (
            <button
              key={id}
              onClick={() => setTab(id)}
              className={`px-4 py-2 rounded-lg font-semibold text-sm flex items-center gap-2 transition ${
                tab === id
                  ? "bg-gradient-to-r from-[#E5C07B] to-[#D4AF37] text-slate-900"
                  : "bg-slate-800 text-slate-300 hover:bg-slate-700"
              }`}
            >
              {icon} {label}
            </button>
          ))}
        </div>

        {tab === "channels" && <ChannelsTab showToast={showToast} />}
        {tab === "bookings" && <BookingsTab showToast={showToast} />}
        {tab === "recon" && <ReconTab showToast={showToast} />}
        {tab === "drafts" && <DraftsTab showToast={showToast} />}

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

const Spinner = () => (
  <div className="p-12 flex items-center justify-center">
    <div className="animate-spin"><div className="h-12 w-12 border-4 border-[#E5C07B] border-t-[#D4AF37] rounded-full"></div></div>
  </div>
);

const Empty = ({ emoji = "📭", text }) => (
  <div className="p-12 text-center text-slate-400"><div className="text-5xl mb-4">{emoji}</div><p>{text}</p></div>
);

const Card = ({ children, className = "" }) => (
  <div className={`bg-gradient-to-r from-slate-800/50 to-slate-700/50 border border-slate-700 rounded-2xl shadow-xl backdrop-blur ${className}`}>
    {children}
  </div>
);

// ---------------- Channels tab ----------------
const ChannelsTab = ({ showToast }) => {
  const [loading, setLoading] = useState(false);
  const [channels, setChannels] = useState([]);
  const [poller, setPoller] = useState({});
  const [savingCode, setSavingCode] = useState(null);

  const load = async () => {
    setLoading(true);
    try {
      const data = await getChannels();
      setChannels(data.channels || []);
      setPoller(data.poller || {});
    } catch (e) { showToast(e.message, "error"); }
    setLoading(false);
  };
  useEffect(() => { load(); }, []); // eslint-disable-line

  const setField = (code, field, value) =>
    setChannels((cs) => cs.map((c) => (c.code === code ? { ...c, [field]: value } : c)));

  const save = async (c) => {
    setSavingCode(c.code);
    try {
      await updateChannel(c.code, {
        commission_percent: Number(c.commission_percent),
        active: c.active,
        mailbox_parsing_enabled: c.mailbox_parsing_enabled,
        cancellation_policy: c.cancellation_policy,
      });
      showToast(`${CHANNEL_LABEL[c.code] || c.code} saved`);
    } catch (e) { showToast(e.message, "error"); }
    setSavingCode(null);
  };

  return (
    <Card>
      <div className="px-6 py-4 border-b border-slate-700 flex items-center justify-between">
        <h2 className="text-lg font-bold text-white">Per-OTA configuration</h2>
        <span className="text-xs text-slate-400">
          Mailbox poller: {poller.imap_configured ? <span className="text-green-300">configured</span> : <span className="text-slate-400">off (set OTA_IMAP_* env)</span>}
        </span>
      </div>
      {loading ? <Spinner /> : (
        <div className="p-6 space-y-4">
          {channels.map((c) => (
            <div key={c.code} className="bg-slate-900/40 border border-slate-700 rounded-xl p-4">
              <div className="flex flex-wrap items-end gap-4">
                <div className="min-w-[140px]">
                  <div className="text-[#E5C07B] font-bold">{c.display_name}</div>
                  <div className="text-xs text-slate-500">{c.code}</div>
                </div>
                <div className="flex flex-col">
                  <label className="mb-1 text-xs font-semibold text-slate-400">Commission %</label>
                  <input type="number" min="0" max="100" step="0.5" value={c.commission_percent}
                    onChange={(e) => setField(c.code, "commission_percent", e.target.value)}
                    className={`${inputCls} w-28`} />
                </div>
                <label className="flex items-center gap-2 px-3 py-2.5 bg-slate-900/50 border border-slate-600 rounded-lg cursor-pointer">
                  <input type="checkbox" checked={!!c.active}
                    onChange={(e) => setField(c.code, "active", e.target.checked)}
                    className="w-4 h-4 accent-[#E5C07B]" />
                  <span className="text-slate-300 text-sm">Active</span>
                </label>
                <label className="flex items-center gap-2 px-3 py-2.5 bg-slate-900/50 border border-slate-600 rounded-lg cursor-pointer">
                  <input type="checkbox" checked={!!c.mailbox_parsing_enabled}
                    onChange={(e) => setField(c.code, "mailbox_parsing_enabled", e.target.checked)}
                    className="w-4 h-4 accent-[#E5C07B]" />
                  <span className="text-slate-300 text-sm">Mailbox auto-draft</span>
                </label>
                <button onClick={() => save(c)} disabled={savingCode === c.code}
                  className="ml-auto bg-gradient-to-r from-[#E5C07B] to-[#D4AF37] text-slate-900 font-bold px-5 py-2.5 rounded-lg hover:scale-105 transition disabled:opacity-50 flex items-center gap-2">
                  <FaSave size={12} /> {savingCode === c.code ? "Saving…" : "Save"}
                </button>
              </div>
              <div className="mt-3">
                <label className="mb-1 text-xs font-semibold text-slate-400 block">Cancellation / no-show rules</label>
                <textarea rows="2" value={c.cancellation_policy || ""}
                  onChange={(e) => setField(c.code, "cancellation_policy", e.target.value)}
                  placeholder="e.g. Free cancellation up to 24h before check-in; no-show charged 1 night."
                  className={`${inputCls} w-full`} />
              </div>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
};

// ---------------- OTA bookings tab ----------------
const BookingsTab = ({ showToast }) => {
  const today = new Date();
  const monthAgo = new Date(today.getTime() - 29 * 86400000);
  const [from, setFrom] = useState(iso(monthAgo));
  const [to, setTo] = useState(iso(today));
  const [source, setSource] = useState("");
  const [loading, setLoading] = useState(false);
  const [rows, setRows] = useState([]);
  const [totals, setTotals] = useState({});

  const load = async () => {
    setLoading(true);
    try {
      const data = await getOtaBookings({ from, to, source });
      setRows(data.rows || []);
      setTotals(data.totals || {});
    } catch (e) { showToast(e.message, "error"); }
    setLoading(false);
  };
  useEffect(() => { load(); }, []); // eslint-disable-line

  return (
    <Card className="overflow-hidden">
      <div className="px-6 py-4 border-b border-slate-700 flex flex-wrap items-end gap-3">
        <div className="flex flex-col"><label className="text-xs text-slate-400 mb-1">From</label>
          <input type="date" value={from} onChange={(e) => setFrom(e.target.value)} className={inputCls} /></div>
        <div className="flex flex-col"><label className="text-xs text-slate-400 mb-1">To</label>
          <input type="date" value={to} onChange={(e) => setTo(e.target.value)} className={inputCls} /></div>
        <div className="flex flex-col"><label className="text-xs text-slate-400 mb-1">Channel</label>
          <select value={source} onChange={(e) => setSource(e.target.value)} className={inputCls}>
            <option value="">All OTAs</option>
            {Object.entries(CHANNEL_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select></div>
        <button onClick={load} className="bg-slate-700 hover:bg-slate-600 font-semibold px-5 py-2.5 rounded-lg transition flex items-center gap-2">
          <FaSyncAlt size={13} /> Apply
        </button>
      </div>
      {loading ? <Spinner /> : rows.length === 0 ? <Empty text="No OTA bookings in this range." /> : (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="bg-slate-900/80 border-b border-slate-700">
              <tr>
                <th className="px-5 py-3 font-semibold">#</th>
                <th className="px-5 py-3 font-semibold">Guest</th>
                <th className="px-5 py-3 font-semibold">Channel</th>
                <th className="px-5 py-3 font-semibold">OTA ID</th>
                <th className="px-5 py-3 font-semibold">Stay</th>
                <th className="px-5 py-3 font-semibold text-right">Gross</th>
                <th className="px-5 py-3 font-semibold text-right">Comm %</th>
                <th className="px-5 py-3 font-semibold text-right">Commission</th>
                <th className="px-5 py-3 font-semibold text-right">Net payout</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-700">
              {rows.map((r) => (
                <tr key={r.booking_id} className="hover:bg-slate-700/30 transition">
                  <td className="px-5 py-3 font-medium">{r.booking_id}</td>
                  <td className="px-5 py-3 text-slate-300">{r.guest_name || "—"}</td>
                  <td className="px-5 py-3 text-slate-300">{CHANNEL_LABEL[r.source] || r.source}</td>
                  <td className="px-5 py-3 text-slate-400">{r.ota_booking_id || "—"}</td>
                  <td className="px-5 py-3 text-slate-400 whitespace-nowrap">{r.check_in} → {r.check_out}</td>
                  <td className="px-5 py-3 text-right text-slate-300">{fmt(r.gross)}</td>
                  <td className="px-5 py-3 text-right text-slate-400">{r.commission_percent ?? "—"}</td>
                  <td className="px-5 py-3 text-right text-slate-300">{fmt(r.commission_amount)}</td>
                  <td className="px-5 py-3 text-right text-[#E5C07B] font-semibold">{fmt(r.net_payout)}</td>
                </tr>
              ))}
              <tr className="bg-slate-900/60 font-bold">
                <td className="px-5 py-3" colSpan={5}>TOTAL ({totals.bookings} bookings)</td>
                <td className="px-5 py-3 text-right">{fmt(totals.gross)}</td>
                <td className="px-5 py-3"></td>
                <td className="px-5 py-3 text-right">{fmt(totals.commission_amount)}</td>
                <td className="px-5 py-3 text-right text-[#E5C07B]">{fmt(totals.net_payout)}</td>
              </tr>
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
};

// ---------------- Reconciliation tab ----------------
const ReconTab = ({ showToast }) => {
  const today = new Date();
  const monthAgo = new Date(today.getTime() - 29 * 86400000);
  const [from, setFrom] = useState(iso(monthAgo));
  const [to, setTo] = useState(iso(today));
  const [loading, setLoading] = useState(false);
  const [recon, setRecon] = useState({ rows: [], totals: {} });
  const [settlements, setSettlements] = useState([]);
  const [form, setForm] = useState({ channel_code: "makemytrip", amount: "", reference: "", notes: "" });
  const [saving, setSaving] = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      const r = await getReconciliation({ from, to });
      setRecon(r);
      const s = await getSettlements({ from, to });
      setSettlements(s.rows || []);
    } catch (e) { showToast(e.message, "error"); }
    setLoading(false);
  };
  useEffect(() => { load(); }, []); // eslint-disable-line

  const save = async () => {
    if (!form.amount || Number(form.amount) <= 0) { showToast("Enter a settlement amount", "error"); return; }
    setSaving(true);
    try {
      await createSettlement({
        channel_code: form.channel_code, amount: Number(form.amount),
        reference: form.reference || null, notes: form.notes || null,
      });
      setForm({ ...form, amount: "", reference: "", notes: "" });
      showToast("Settlement recorded");
      await load();
    } catch (e) { showToast(e.message, "error"); }
    setSaving(false);
  };

  return (
    <div className="space-y-6">
      <Card>
        <div className="px-6 py-4 border-b border-slate-700 flex flex-wrap items-end gap-3">
          <div className="flex flex-col"><label className="text-xs text-slate-400 mb-1">From</label>
            <input type="date" value={from} onChange={(e) => setFrom(e.target.value)} className={inputCls} /></div>
          <div className="flex flex-col"><label className="text-xs text-slate-400 mb-1">To</label>
            <input type="date" value={to} onChange={(e) => setTo(e.target.value)} className={inputCls} /></div>
          <button onClick={load} className="bg-slate-700 hover:bg-slate-600 font-semibold px-5 py-2.5 rounded-lg transition flex items-center gap-2">
            <FaSyncAlt size={13} /> Apply
          </button>
        </div>
        {loading ? <Spinner /> : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="bg-slate-900/80 border-b border-slate-700">
                <tr>
                  <th className="px-5 py-3 font-semibold">Channel</th>
                  <th className="px-5 py-3 font-semibold text-right">Bookings</th>
                  <th className="px-5 py-3 font-semibold text-right">Gross</th>
                  <th className="px-5 py-3 font-semibold text-right">Expected payout</th>
                  <th className="px-5 py-3 font-semibold text-right">Actual settled</th>
                  <th className="px-5 py-3 font-semibold text-right">Difference</th>
                  <th className="px-5 py-3 font-semibold text-center">Status</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-700">
                {recon.rows.map((r) => (
                  <tr key={r.channel} className="hover:bg-slate-700/30 transition">
                    <td className="px-5 py-3 text-slate-300">{CHANNEL_LABEL[r.channel] || r.channel}</td>
                    <td className="px-5 py-3 text-right text-slate-400">{r.bookings}</td>
                    <td className="px-5 py-3 text-right text-slate-300">{fmt(r.gross)}</td>
                    <td className="px-5 py-3 text-right text-slate-300">{fmt(r.expected_payout)}</td>
                    <td className="px-5 py-3 text-right text-slate-300">{fmt(r.actual_settled)}</td>
                    <td className={`px-5 py-3 text-right font-semibold ${r.mismatch ? "text-red-300" : "text-slate-300"}`}>{fmt(r.difference)}</td>
                    <td className="px-5 py-3 text-center">
                      <span className={`px-3 py-1 rounded-full text-xs font-bold border ${r.mismatch ? "bg-red-500/20 text-red-300 border-red-500/30" : "bg-green-500/20 text-green-300 border-green-500/30"}`}>
                        {r.mismatch ? "Mismatch" : "Matched"}
                      </span>
                    </td>
                  </tr>
                ))}
                <tr className="bg-slate-900/60 font-bold">
                  <td className="px-5 py-3">TOTAL</td>
                  <td className="px-5 py-3"></td>
                  <td className="px-5 py-3 text-right">{fmt(recon.totals.gross)}</td>
                  <td className="px-5 py-3 text-right">{fmt(recon.totals.expected_payout)}</td>
                  <td className="px-5 py-3 text-right">{fmt(recon.totals.actual_settled)}</td>
                  <td className="px-5 py-3 text-right">{fmt(recon.totals.difference)}</td>
                  <td className="px-5 py-3"></td>
                </tr>
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card className="p-6">
        <h2 className="text-lg font-bold text-[#E5C07B] mb-4">Record a bank settlement</h2>
        <div className="grid grid-cols-1 md:grid-cols-5 gap-4 items-end">
          <div className="flex flex-col"><label className="text-xs text-slate-400 mb-1">Channel</label>
            <select value={form.channel_code} onChange={(e) => setForm({ ...form, channel_code: e.target.value })} className={inputCls}>
              {Object.entries(CHANNEL_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select></div>
          <div className="flex flex-col"><label className="text-xs text-slate-400 mb-1">Amount (₹)</label>
            <input type="number" min="0" step="0.01" value={form.amount} onChange={(e) => setForm({ ...form, amount: e.target.value })} className={inputCls} /></div>
          <div className="flex flex-col"><label className="text-xs text-slate-400 mb-1">Reference (UTR)</label>
            <input value={form.reference} onChange={(e) => setForm({ ...form, reference: e.target.value })} className={inputCls} /></div>
          <div className="flex flex-col"><label className="text-xs text-slate-400 mb-1">Notes</label>
            <input value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} className={inputCls} /></div>
          <button onClick={save} disabled={saving}
            className="bg-gradient-to-r from-[#E5C07B] to-[#D4AF37] text-slate-900 font-bold px-5 py-2.5 rounded-lg hover:scale-105 transition disabled:opacity-50 flex items-center gap-2 justify-center">
            <FaSave size={12} /> {saving ? "Saving…" : "Record"}
          </button>
        </div>
        {settlements.length > 0 && (
          <div className="mt-6 overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="bg-slate-900/60 border-b border-slate-700">
                <tr>
                  <th className="px-4 py-2 font-semibold">Channel</th>
                  <th className="px-4 py-2 font-semibold">Reference</th>
                  <th className="px-4 py-2 font-semibold text-right">Amount</th>
                  <th className="px-4 py-2 font-semibold">Recorded</th>
                  <th className="px-4 py-2 font-semibold">Notes</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-700">
                {settlements.map((s) => (
                  <tr key={s.id}>
                    <td className="px-4 py-2 text-slate-300">{CHANNEL_LABEL[s.channel_code] || s.channel_code}</td>
                    <td className="px-4 py-2 text-slate-400">{s.reference || "—"}</td>
                    <td className="px-4 py-2 text-right text-slate-300">{fmt(s.amount)}</td>
                    <td className="px-4 py-2 text-slate-400">{(s.created_at || "").slice(0, 10)}</td>
                    <td className="px-4 py-2 text-slate-400">{s.notes || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
};

// ---------------- Email drafts tab ----------------
const DraftsTab = ({ showToast }) => {
  const [loading, setLoading] = useState(false);
  const [drafts, setDrafts] = useState([]);
  const [roomTypes, setRoomTypes] = useState([]);
  const [confirmFor, setConfirmFor] = useState(null); // draft being confirmed
  const [cform, setCform] = useState({});
  const [busy, setBusy] = useState(false);
  const [polling, setPolling] = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      const data = await getDrafts();
      setDrafts(data.rows || []);
      const rt = await getRoomTypes();
      setRoomTypes(Array.isArray(rt) ? rt : rt.data || []);
    } catch (e) { showToast(e.message, "error"); }
    setLoading(false);
  };
  useEffect(() => { load(); }, []); // eslint-disable-line

  const openConfirm = (d) => {
    setConfirmFor(d);
    setCform({
      room_type_id: roomTypes[0]?.room_type_id || "",
      quantity: 1,
      guest_name: d.guest_name || "",
      phone: d.phone || "",
      email: d.email || "",
      check_in: d.check_in || "",
      check_out: d.check_out || "",
    });
  };

  const doConfirm = async () => {
    setBusy(true);
    try {
      const payload = {
        room_type_id: Number(cform.room_type_id),
        quantity: Number(cform.quantity) || 1,
        guest_name: cform.guest_name || null,
        phone: cform.phone || null,
        email: cform.email || null,
        check_in: cform.check_in || null,
        check_out: cform.check_out || null,
      };
      const res = await confirmDraft(confirmFor.id, payload);
      showToast(`Booking #${res.booking?.booking_id} created`);
      setConfirmFor(null);
      await load();
    } catch (e) { showToast(e.message, "error"); }
    setBusy(false);
  };

  const doDismiss = async (id) => {
    try { await dismissDraft(id); showToast("Draft dismissed"); await load(); }
    catch (e) { showToast(e.message, "error"); }
  };

  const doPoll = async () => {
    setPolling(true);
    try {
      const res = await pollMailbox();
      if (!res.configured) showToast("Mailbox poller is off (set OTA_IMAP_* env)", "error");
      else showToast(`Polled: ${res.processed || 0} email(s), ${res.created || 0} new draft(s)`);
      await load();
    } catch (e) { showToast(e.message, "error"); }
    setPolling(false);
  };

  const statusChip = (s) => ({
    pending: "bg-yellow-500/20 text-yellow-300 border-yellow-500/30",
    confirmed: "bg-green-500/20 text-green-300 border-green-500/30",
    dismissed: "bg-slate-500/20 text-slate-300 border-slate-500/30",
    flagged: "bg-red-500/20 text-red-300 border-red-500/30",
  }[s] || "bg-slate-500/20 text-slate-300 border-slate-500/30");

  return (
    <Card className="overflow-hidden">
      <div className="px-6 py-4 border-b border-slate-700 flex items-center justify-between">
        <h2 className="text-lg font-bold text-white">Email-parsed drafts</h2>
        <button onClick={doPoll} disabled={polling}
          className="bg-slate-700 hover:bg-slate-600 font-semibold px-5 py-2.5 rounded-lg transition flex items-center gap-2 disabled:opacity-50">
          <FaSyncAlt size={13} /> {polling ? "Polling…" : "Poll mailbox now"}
        </button>
      </div>
      {loading ? <Spinner /> : drafts.length === 0 ? (
        <Empty emoji="✉️" text="No drafts. Forwarded/parsed OTA emails will appear here for one-click confirm." />
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="bg-slate-900/80 border-b border-slate-700">
              <tr>
                <th className="px-5 py-3 font-semibold">Channel</th>
                <th className="px-5 py-3 font-semibold">Kind</th>
                <th className="px-5 py-3 font-semibold">OTA ID</th>
                <th className="px-5 py-3 font-semibold">Guest</th>
                <th className="px-5 py-3 font-semibold">Stay</th>
                <th className="px-5 py-3 font-semibold text-right">Amount</th>
                <th className="px-5 py-3 font-semibold text-center">Status</th>
                <th className="px-5 py-3 font-semibold text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-700">
              {drafts.map((d) => (
                <tr key={d.id} className="hover:bg-slate-700/30 transition">
                  <td className="px-5 py-3 text-slate-300">{CHANNEL_LABEL[d.channel_code] || d.channel_code}</td>
                  <td className="px-5 py-3 text-slate-400">{d.kind}</td>
                  <td className="px-5 py-3 text-slate-400">{d.ota_booking_id || "—"}</td>
                  <td className="px-5 py-3 text-slate-300">{d.guest_name || "—"}</td>
                  <td className="px-5 py-3 text-slate-400 whitespace-nowrap">{d.check_in || "?"} → {d.check_out || "?"}</td>
                  <td className="px-5 py-3 text-right text-slate-300">{fmt(d.amount)}</td>
                  <td className="px-5 py-3 text-center">
                    <span className={`px-3 py-1 rounded-full text-xs font-bold border ${statusChip(d.status)}`}>{d.status}</span>
                    {d.linked_booking_id && <div className="text-xs text-slate-500 mt-1">#{d.linked_booking_id}</div>}
                  </td>
                  <td className="px-5 py-3 text-right whitespace-nowrap">
                    {d.status === "pending" && d.kind === "confirmation" && (
                      <button onClick={() => openConfirm(d)}
                        className="bg-gradient-to-r from-[#E5C07B] to-[#D4AF37] text-slate-900 font-bold px-3 py-1.5 rounded-lg text-xs hover:scale-105 transition mr-2">
                        Confirm
                      </button>
                    )}
                    {(d.status === "pending" || d.status === "flagged") && (
                      <button onClick={() => doDismiss(d.id)}
                        className="bg-slate-700 hover:bg-slate-600 font-semibold px-3 py-1.5 rounded-lg text-xs transition">
                        Dismiss
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {confirmFor && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
          <div className="bg-slate-800 border border-slate-700 rounded-2xl shadow-2xl w-full max-w-lg p-6">
            <h3 className="text-xl font-bold text-[#E5C07B] mb-1">Confirm OTA booking</h3>
            <p className="text-slate-400 text-sm mb-4">
              {CHANNEL_LABEL[confirmFor.channel_code]} · {confirmFor.ota_booking_id || "no OTA id"} — creates a real
              booking that consumes availability. Pick the PMS room type.
            </p>
            <div className="grid grid-cols-2 gap-3">
              <div className="flex flex-col col-span-2"><label className="text-xs text-slate-400 mb-1">Room type</label>
                <select value={cform.room_type_id} onChange={(e) => setCform({ ...cform, room_type_id: e.target.value })} className={inputCls}>
                  {roomTypes.map((rt) => <option key={rt.room_type_id} value={rt.room_type_id}>{rt.name}</option>)}
                </select>
                {confirmFor.room_type_hint && <span className="text-xs text-slate-500 mt-1">OTA room: “{confirmFor.room_type_hint}”</span>}
              </div>
              <div className="flex flex-col"><label className="text-xs text-slate-400 mb-1">Quantity</label>
                <input type="number" min="1" max="5" value={cform.quantity} onChange={(e) => setCform({ ...cform, quantity: e.target.value })} className={inputCls} /></div>
              <div className="flex flex-col"><label className="text-xs text-slate-400 mb-1">Phone</label>
                <input value={cform.phone} onChange={(e) => setCform({ ...cform, phone: e.target.value })} className={inputCls} /></div>
              <div className="flex flex-col col-span-2"><label className="text-xs text-slate-400 mb-1">Guest name</label>
                <input value={cform.guest_name} onChange={(e) => setCform({ ...cform, guest_name: e.target.value })} className={inputCls} /></div>
              <div className="flex flex-col"><label className="text-xs text-slate-400 mb-1">Check-in</label>
                <input type="date" value={cform.check_in} onChange={(e) => setCform({ ...cform, check_in: e.target.value })} className={inputCls} /></div>
              <div className="flex flex-col"><label className="text-xs text-slate-400 mb-1">Check-out</label>
                <input type="date" value={cform.check_out} onChange={(e) => setCform({ ...cform, check_out: e.target.value })} className={inputCls} /></div>
            </div>
            <div className="flex items-center gap-3 mt-6">
              <button onClick={doConfirm} disabled={busy}
                className="bg-gradient-to-r from-[#E5C07B] to-[#D4AF37] text-slate-900 font-bold px-6 py-2.5 rounded-lg hover:scale-105 transition disabled:opacity-50">
                {busy ? "Creating…" : "Create booking"}
              </button>
              <button onClick={() => setConfirmFor(null)} className="bg-slate-700 hover:bg-slate-600 font-semibold px-6 py-2.5 rounded-lg transition">
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}
    </Card>
  );
};

export default OtaChannels;
