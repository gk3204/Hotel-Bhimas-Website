import { useEffect, useState } from "react";
import {
  FaWhatsapp, FaSave, FaSyncAlt, FaPlay, FaPaperPlane, FaTrash, FaBan, FaListUl, FaSlidersH,
} from "react-icons/fa";
import {
  getWhatsappConfig, updateWhatsappConfig, getTemplates, getMessages,
  getOptOuts, addOptOut, removeOptOut, runJobs, sendTest,
} from "../../api/whatsapp";

const inputCls =
  "px-4 py-2 bg-slate-900/50 border border-slate-600 rounded-lg text-white focus:outline-none focus:border-[#E5C07B] focus:ring-2 focus:ring-[#E5C07B]/20 transition";

const statusChip = (s) =>
  s === "delivered" || s === "read" || s === "sent"
    ? "bg-green-500/20 text-green-300 border-green-500/30"
    : s === "queued"
      ? "bg-yellow-500/20 text-yellow-300 border-yellow-500/30"
      : s === "failed"
        ? "bg-red-500/20 text-red-300 border-red-500/30"
        : "bg-slate-500/20 text-slate-300 border-slate-500/30";

const TabBtn = ({ active, onClick, icon, label }) => (
  <button
    onClick={onClick}
    className={`px-5 py-2.5 rounded-lg font-semibold transition flex items-center gap-2 ${
      active
        ? "bg-gradient-to-r from-[#E5C07B] to-[#D4AF37] text-slate-900"
        : "bg-slate-800/60 border border-slate-700 text-slate-300 hover:bg-slate-700/60"
    }`}
  >
    {icon} {label}
  </button>
);

// The automation toggles rendered as a checkbox grid, bound to the config form.
const TOGGLES = [
  ["checkout_reminder_enabled", "Checkout reminder (≈2h before)"],
  ["overstay_enabled", "Overstay alert (guest + owner)"],
  ["confirmation_enabled", "Booking confirmation"],
  ["receipt_enabled", "Payment receipt"],
  ["room_ready_enabled", "Room ready / welcome"],
  ["review_enabled", "Post-checkout review + feedback"],
  ["owner_alerts_enabled", "Owner alerts (OTP / digest / fraud)"],
];

export default function WhatsAppMessaging() {
  const [tab, setTab] = useState("settings");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [running, setRunning] = useState(false);
  const [toast, setToast] = useState(null);

  const [form, setForm] = useState(null);
  const [provider, setProvider] = useState(null);
  const [templates, setTemplates] = useState([]);
  const [messages, setMessages] = useState([]);
  const [msgStatus, setMsgStatus] = useState("");
  const [optOuts, setOptOuts] = useState([]);
  const [newOptOut, setNewOptOut] = useState({ phone: "", reason: "" });
  const [test, setTest] = useState({ to: "", template: "checkout_reminder" });

  const showToast = (message, type = "success") => {
    setToast({ message, type });
    setTimeout(() => setToast(null), 3500);
  };

  const loadConfig = async () => {
    setLoading(true);
    try {
      const cfg = await getWhatsappConfig();
      setProvider(cfg.provider_status || null);
      setForm(cfg);
    } catch (e) { showToast(e.message, "error"); }
    setLoading(false);
  };

  useEffect(() => { loadConfig(); }, []); // eslint-disable-line

  const openTab = async (t) => {
    setTab(t);
    try {
      if (t === "templates" && templates.length === 0) {
        const r = await getTemplates();
        setTemplates(r.templates || []);
      }
      if (t === "log") await loadMessages();
      if (t === "optouts") setOptOuts((await getOptOuts()).data || []);
    } catch (e) { showToast(e.message, "error"); }
  };

  const loadMessages = async () => {
    const r = await getMessages({ status: msgStatus || undefined, limit: 150 });
    setMessages(r.data || []);
  };

  const save = async () => {
    if (!form) return;
    setSaving(true);
    try {
      const payload = {
        checkout_reminder_enabled: !!form.checkout_reminder_enabled,
        checkout_reminder_lead_hours: Number(form.checkout_reminder_lead_hours),
        overstay_enabled: !!form.overstay_enabled,
        confirmation_enabled: !!form.confirmation_enabled,
        receipt_enabled: !!form.receipt_enabled,
        room_ready_enabled: !!form.room_ready_enabled,
        review_enabled: !!form.review_enabled,
        review_delay_hours: Number(form.review_delay_hours),
        owner_alerts_enabled: !!form.owner_alerts_enabled,
        owner_whatsapp: form.owner_whatsapp || "",
        google_review_url: form.google_review_url || "",
        job_interval_minutes: Number(form.job_interval_minutes),
        daily_digest_hour: Number(form.daily_digest_hour),
      };
      const cfg = await updateWhatsappConfig(payload);
      setProvider(cfg.provider_status || null);
      setForm(cfg);
      showToast("WhatsApp settings saved");
    } catch (e) { showToast(e.message, "error"); }
    setSaving(false);
  };

  const doRunJobs = async () => {
    setRunning(true);
    try {
      const r = await runJobs();
      showToast(`Sweep done: ${JSON.stringify(r.result)}`);
      if (tab === "log") await loadMessages();
    } catch (e) { showToast(e.message, "error"); }
    setRunning(false);
  };

  const doSendTest = async () => {
    if (!test.to) { showToast("Enter a phone number", "error"); return; }
    try {
      await sendTest({ to: test.to, template: test.template, params: {} });
      showToast("Test message queued — check the Message log");
    } catch (e) { showToast(e.message, "error"); }
  };

  const doAddOptOut = async () => {
    if (!newOptOut.phone) return;
    try {
      await addOptOut(newOptOut.phone, newOptOut.reason || undefined);
      setNewOptOut({ phone: "", reason: "" });
      setOptOuts((await getOptOuts()).data || []);
      showToast("Added to opt-out list");
    } catch (e) { showToast(e.message, "error"); }
  };

  const doRemoveOptOut = async (phone) => {
    try {
      await removeOptOut(phone);
      setOptOuts((await getOptOuts()).data || []);
      showToast("Removed from opt-out list");
    } catch (e) { showToast(e.message, "error"); }
  };

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-900 via-slate-800 to-slate-900 text-white p-6">
      <div className="max-w-7xl mx-auto">
        <div className="mb-6 flex items-start justify-between flex-wrap gap-4">
          <div>
            <h1 className="text-4xl font-bold mb-2 bg-gradient-to-r from-[#E5C07B] to-[#FCD34D] bg-clip-text text-transparent flex items-center gap-3">
              <FaWhatsapp /> WhatsApp
            </h1>
            <p className="text-slate-400">
              Automated guest &amp; owner messaging — reminders, alerts, receipts and the review/complaint loop.
            </p>
          </div>
          {provider && (
            <span
              className={`px-3 py-1.5 rounded-full text-xs font-bold border ${
                provider.configured
                  ? "bg-green-500/20 text-green-300 border-green-500/30"
                  : "bg-yellow-500/20 text-yellow-300 border-yellow-500/30"
              }`}
            >
              Provider: {provider.provider}{provider.configured ? " (live)" : " (stub — set WHATSAPP_* env)"}
            </span>
          )}
        </div>

        <div className="flex flex-wrap gap-2 mb-6">
          <TabBtn active={tab === "settings"} onClick={() => openTab("settings")} icon={<FaSlidersH />} label="Templates & toggles" />
          <TabBtn active={tab === "templates"} onClick={() => openTab("templates")} icon={<FaListUl />} label="Template catalog" />
          <TabBtn active={tab === "log"} onClick={() => openTab("log")} icon={<FaPaperPlane />} label="Message log" />
          <TabBtn active={tab === "optouts"} onClick={() => openTab("optouts")} icon={<FaBan />} label="Opt-outs" />
        </div>

        {/* ---------------- Settings ---------------- */}
        {tab === "settings" && form && (
          <>
            <div className="bg-gradient-to-r from-slate-800/50 to-slate-700/50 border border-slate-700 p-6 rounded-2xl shadow-xl mb-6 backdrop-blur">
              <h2 className="text-xl font-bold text-[#E5C07B] mb-5">Automations</h2>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                {TOGGLES.map(([key, label]) => (
                  <label key={key} className="flex items-center gap-3 px-4 py-2.5 bg-slate-900/50 border border-slate-600 rounded-lg cursor-pointer">
                    <input
                      type="checkbox"
                      checked={!!form[key]}
                      onChange={(e) => setForm({ ...form, [key]: e.target.checked })}
                      className="w-4 h-4 accent-[#E5C07B]"
                    />
                    <span className="text-slate-300 text-sm">{label}</span>
                  </label>
                ))}
              </div>

              <div className="grid grid-cols-1 md:grid-cols-3 gap-5 items-end mt-6">
                <div className="flex flex-col">
                  <label className="mb-2 text-sm font-semibold text-slate-300">Checkout reminder lead (hours)</label>
                  <input type="number" step="0.5" min="0.5" value={form.checkout_reminder_lead_hours}
                    onChange={(e) => setForm({ ...form, checkout_reminder_lead_hours: e.target.value })} className={inputCls} />
                </div>
                <div className="flex flex-col">
                  <label className="mb-2 text-sm font-semibold text-slate-300">Review delay after checkout (hours)</label>
                  <input type="number" step="1" min="0" value={form.review_delay_hours}
                    onChange={(e) => setForm({ ...form, review_delay_hours: e.target.value })} className={inputCls} />
                </div>
                <div className="flex flex-col">
                  <label className="mb-2 text-sm font-semibold text-slate-300">Daily digest hour (IST, 0–23)</label>
                  <input type="number" step="1" min="0" max="23" value={form.daily_digest_hour}
                    onChange={(e) => setForm({ ...form, daily_digest_hour: e.target.value })} className={inputCls} />
                </div>
                <div className="flex flex-col">
                  <label className="mb-2 text-sm font-semibold text-slate-300">Owner WhatsApp number (E.164)</label>
                  <input type="text" placeholder="919347172758" value={form.owner_whatsapp || ""}
                    onChange={(e) => setForm({ ...form, owner_whatsapp: e.target.value })} className={inputCls} />
                </div>
                <div className="flex flex-col">
                  <label className="mb-2 text-sm font-semibold text-slate-300">Google review URL</label>
                  <input type="text" placeholder="https://g.page/r/..." value={form.google_review_url || ""}
                    onChange={(e) => setForm({ ...form, google_review_url: e.target.value })} className={inputCls} />
                </div>
                <div className="flex flex-col">
                  <label className="mb-2 text-sm font-semibold text-slate-300">Scheduler interval (minutes)</label>
                  <input type="number" step="1" min="1" value={form.job_interval_minutes}
                    onChange={(e) => setForm({ ...form, job_interval_minutes: e.target.value })} className={inputCls} />
                </div>
              </div>

              <div className="flex items-center gap-4 mt-6 flex-wrap">
                <button onClick={save} disabled={saving}
                  className="bg-gradient-to-r from-[#E5C07B] to-[#D4AF37] text-slate-900 font-bold px-6 py-2.5 rounded-lg transition-all hover:scale-105 disabled:opacity-50 flex items-center gap-2">
                  <FaSave size={13} /> {saving ? "Saving…" : "Save settings"}
                </button>
                <button onClick={loadConfig} className="bg-slate-700 hover:bg-slate-600 font-semibold px-6 py-2.5 rounded-lg transition flex items-center gap-2">
                  <FaSyncAlt size={13} /> Refresh
                </button>
                <button onClick={doRunJobs} disabled={running}
                  className="bg-blue-600 hover:bg-blue-700 font-semibold px-6 py-2.5 rounded-lg transition flex items-center gap-2 disabled:opacity-50">
                  <FaPlay size={13} /> {running ? "Running…" : "Run jobs now"}
                </button>
              </div>
              <p className="text-slate-500 text-xs mt-3">
                Interval changes apply on next backend restart (the in-process scheduler reads the interval at startup).
                Use “Run jobs now” to fire the sweep immediately.
              </p>
            </div>

            <div className="bg-gradient-to-r from-slate-800/50 to-slate-700/50 border border-slate-700 p-6 rounded-2xl shadow-xl backdrop-blur">
              <h2 className="text-xl font-bold text-[#E5C07B] mb-5">Send a test message</h2>
              <div className="grid grid-cols-1 md:grid-cols-3 gap-5 items-end">
                <div className="flex flex-col">
                  <label className="mb-2 text-sm font-semibold text-slate-300">To (phone)</label>
                  <input type="text" placeholder="919876543210" value={test.to}
                    onChange={(e) => setTest({ ...test, to: e.target.value })} className={inputCls} />
                </div>
                <div className="flex flex-col">
                  <label className="mb-2 text-sm font-semibold text-slate-300">Template</label>
                  <select value={test.template} onChange={(e) => setTest({ ...test, template: e.target.value })} className={inputCls}>
                    {(templates.length ? templates.map((t) => t.name) : ["checkout_reminder", "booking_confirmation", "review_feedback", "owner_otp"]).map((n) => (
                      <option key={n} value={n}>{n}</option>
                    ))}
                  </select>
                </div>
                <button onClick={doSendTest}
                  className="bg-green-700 hover:bg-green-800 font-bold px-6 py-2.5 rounded-lg transition flex items-center gap-2 justify-center">
                  <FaPaperPlane size={13} /> Send test
                </button>
              </div>
            </div>
          </>
        )}

        {/* ---------------- Template catalog ---------------- */}
        {tab === "templates" && (
          <div className="bg-gradient-to-r from-slate-800/50 to-slate-700/50 border border-slate-700 rounded-2xl shadow-xl overflow-hidden backdrop-blur">
            <div className="px-6 py-4 border-b border-slate-700"><h2 className="text-lg font-bold text-white">Templates (submit for approval in Meta / BSP)</h2></div>
            <div className="overflow-x-auto">
              <table className="w-full text-left">
                <thead className="bg-slate-900/80 border-b border-slate-700">
                  <tr>
                    <th className="px-5 py-4 font-semibold text-sm">Name</th>
                    <th className="px-5 py-4 font-semibold text-sm">Category</th>
                    <th className="px-5 py-4 font-semibold text-sm">Opt-out?</th>
                    <th className="px-5 py-4 font-semibold text-sm">Sample</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-700">
                  {templates.map((t) => (
                    <tr key={t.name} className="hover:bg-slate-700/30 transition align-top">
                      <td className="px-5 py-4 font-medium">{t.name}</td>
                      <td className="px-5 py-4 text-slate-300 text-sm">{t.category}</td>
                      <td className="px-5 py-4 text-slate-400 text-sm">{t.respect_optout ? "respects" : "bypasses"}</td>
                      <td className="px-5 py-4 text-slate-400 text-sm max-w-md">{t.body_preview}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {/* ---------------- Message log ---------------- */}
        {tab === "log" && (
          <div className="bg-gradient-to-r from-slate-800/50 to-slate-700/50 border border-slate-700 rounded-2xl shadow-xl overflow-hidden backdrop-blur">
            <div className="px-6 py-4 border-b border-slate-700 flex items-center justify-between gap-4 flex-wrap">
              <h2 className="text-lg font-bold text-white">Message log</h2>
              <div className="flex items-center gap-3">
                <select value={msgStatus} onChange={(e) => setMsgStatus(e.target.value)} className={inputCls}>
                  <option value="">All statuses</option>
                  <option value="sent">Sent</option>
                  <option value="delivered">Delivered</option>
                  <option value="read">Read</option>
                  <option value="failed">Failed</option>
                  <option value="received">Received (inbound)</option>
                </select>
                <button onClick={loadMessages} className="bg-slate-700 hover:bg-slate-600 font-semibold px-4 py-2 rounded-lg transition flex items-center gap-2">
                  <FaSyncAlt size={12} /> Refresh
                </button>
              </div>
            </div>
            {messages.length === 0 ? (
              <div className="p-12 text-center text-slate-400"><div className="text-5xl mb-4">💬</div><p>No messages yet.</p></div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-left">
                  <thead className="bg-slate-900/80 border-b border-slate-700">
                    <tr>
                      <th className="px-5 py-4 font-semibold text-sm">#</th>
                      <th className="px-5 py-4 font-semibold text-sm">Dir</th>
                      <th className="px-5 py-4 font-semibold text-sm">To / From</th>
                      <th className="px-5 py-4 font-semibold text-sm">Template</th>
                      <th className="px-5 py-4 font-semibold text-sm">Status</th>
                      <th className="px-5 py-4 font-semibold text-sm">When</th>
                      <th className="px-5 py-4 font-semibold text-sm">Body / error</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-700">
                    {messages.map((m) => (
                      <tr key={m.id} className="hover:bg-slate-700/30 transition align-top">
                        <td className="px-5 py-4 text-slate-400">{m.id}</td>
                        <td className="px-5 py-4 text-slate-300 text-sm">{m.direction === "in" ? "⬅ in" : "➡ out"}</td>
                        <td className="px-5 py-4 text-slate-300 text-sm whitespace-nowrap">{m.to}</td>
                        <td className="px-5 py-4 text-slate-300 text-sm">{m.template}</td>
                        <td className="px-5 py-4"><span className={`px-3 py-1 rounded-full text-xs font-bold border ${statusChip(m.status)}`}>{m.status}</span></td>
                        <td className="px-5 py-4 text-slate-400 text-sm whitespace-nowrap">{(m.created_at || "").slice(0, 16).replace("T", " ")}</td>
                        <td className="px-5 py-4 text-slate-400 text-sm max-w-md">{m.error ? <span className="text-red-300">{m.error}</span> : m.body}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}

        {/* ---------------- Opt-outs ---------------- */}
        {tab === "optouts" && (
          <>
            <div className="bg-gradient-to-r from-slate-800/50 to-slate-700/50 border border-slate-700 p-6 rounded-2xl shadow-xl mb-6 backdrop-blur">
              <h2 className="text-xl font-bold text-[#E5C07B] mb-5">Add opt-out</h2>
              <div className="grid grid-cols-1 md:grid-cols-3 gap-5 items-end">
                <div className="flex flex-col">
                  <label className="mb-2 text-sm font-semibold text-slate-300">Phone</label>
                  <input type="text" placeholder="919876543210" value={newOptOut.phone}
                    onChange={(e) => setNewOptOut({ ...newOptOut, phone: e.target.value })} className={inputCls} />
                </div>
                <div className="flex flex-col">
                  <label className="mb-2 text-sm font-semibold text-slate-300">Reason (optional)</label>
                  <input type="text" value={newOptOut.reason}
                    onChange={(e) => setNewOptOut({ ...newOptOut, reason: e.target.value })} className={inputCls} />
                </div>
                <button onClick={doAddOptOut}
                  className="bg-gradient-to-r from-[#E5C07B] to-[#D4AF37] text-slate-900 font-bold px-6 py-2.5 rounded-lg transition-all hover:scale-105 flex items-center gap-2 justify-center">
                  <FaBan size={13} /> Add opt-out
                </button>
              </div>
            </div>

            <div className="bg-gradient-to-r from-slate-800/50 to-slate-700/50 border border-slate-700 rounded-2xl shadow-xl overflow-hidden backdrop-blur">
              <div className="px-6 py-4 border-b border-slate-700"><h2 className="text-lg font-bold text-white">Opted-out numbers</h2></div>
              {optOuts.length === 0 ? (
                <div className="p-12 text-center text-slate-400"><div className="text-5xl mb-4">🔕</div><p>No opt-outs.</p></div>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-left">
                    <thead className="bg-slate-900/80 border-b border-slate-700">
                      <tr>
                        <th className="px-5 py-4 font-semibold text-sm">Phone</th>
                        <th className="px-5 py-4 font-semibold text-sm">Source</th>
                        <th className="px-5 py-4 font-semibold text-sm">Reason</th>
                        <th className="px-5 py-4 font-semibold text-sm">Since</th>
                        <th className="px-5 py-4 font-semibold text-sm"></th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-700">
                      {optOuts.map((o) => (
                        <tr key={o.phone} className="hover:bg-slate-700/30 transition">
                          <td className="px-5 py-4 font-medium">{o.phone}</td>
                          <td className="px-5 py-4 text-slate-300 text-sm">{o.source}</td>
                          <td className="px-5 py-4 text-slate-400 text-sm">{o.reason || "—"}</td>
                          <td className="px-5 py-4 text-slate-400 text-sm whitespace-nowrap">{(o.created_at || "").slice(0, 16).replace("T", " ")}</td>
                          <td className="px-5 py-4">
                            <button onClick={() => doRemoveOptOut(o.phone)} className="bg-red-600 hover:bg-red-700 px-3 py-2 rounded-lg text-white text-sm flex items-center gap-2">
                              <FaTrash size={11} /> Remove
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </>
        )}

        {loading && <div className="p-12 flex items-center justify-center"><div className="animate-spin"><div className="h-12 w-12 border-4 border-[#E5C07B] border-t-[#D4AF37] rounded-full" /></div></div>}
      </div>

      {toast && (
        <div className="fixed top-6 right-6 z-50">
          <div className={`px-6 py-4 rounded-xl shadow-2xl font-medium backdrop-blur max-w-md ${toast.type === "error" ? "bg-red-600/90 text-white" : "bg-green-600/90 text-white"}`}>
            {toast.type === "error" ? "❌" : "✅"} {toast.message}
          </div>
        </div>
      )}
    </div>
  );
}
