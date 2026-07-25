import React, { useEffect, useState } from "react";
import { getRatePlans, createRatePlan, deleteRatePlan, getQuote } from "../../api/ratePlans";
import { getRoomTypes } from "../../api/roomTypes";
import { getAgents } from "../../api/travelAgents";
import { FaPlus, FaTrash, FaCalculator } from "react-icons/fa";

const CHANNELS = [
  { value: "walk_in", label: "Walk-in" },
  { value: "website", label: "Website" },
  { value: "agent", label: "Agent" },
];
const DAYS = [
  { n: 0, label: "Mon" }, { n: 1, label: "Tue" }, { n: 2, label: "Wed" },
  { n: 3, label: "Thu" }, { n: 4, label: "Fri" }, { n: 5, label: "Sat" }, { n: 6, label: "Sun" },
];

const emptyForm = {
  channel: "walk_in", agent_id: "", price: "", valid_from: "", valid_to: "",
  days: [], priority: "0",
};

const RatePlans = () => {
  const [roomTypes, setRoomTypes] = useState([]);
  const [agents, setAgents] = useState([]);
  const [selectedRt, setSelectedRt] = useState("");
  const [plans, setPlans] = useState([]);
  const [loading, setLoading] = useState(false);
  const [toast, setToast] = useState(null);
  const [form, setForm] = useState(emptyForm);

  // preview
  const [preview, setPreview] = useState({ check_in: "", check_out: "", channel: "walk_in", agent_id: "" });
  const [quote, setQuote] = useState(null);

  useEffect(() => {
    getRoomTypes().then((rts) => {
      setRoomTypes(rts);
      if (rts.length) setSelectedRt(String(rts[0].room_type_id));
    }).catch((e) => showToast(e.message, "error"));
    getAgents(true).then(setAgents).catch(() => {});
  }, []);

  useEffect(() => {
    if (selectedRt) loadPlans(selectedRt);
  }, [selectedRt]);

  const showToast = (message, type = "success") => {
    setToast({ message, type });
    setTimeout(() => setToast(null), 3000);
  };

  const loadPlans = async (rtId) => {
    setLoading(true);
    try {
      setPlans(await getRatePlans(rtId));
    } catch (e) {
      showToast(e.message, "error");
    }
    setLoading(false);
  };

  const toggleDay = (n) => {
    setForm((f) => ({ ...f, days: f.days.includes(n) ? f.days.filter((d) => d !== n) : [...f.days, n].sort() }));
  };

  const handleCreate = async () => {
    if (!form.price) return showToast("Price is required", "error");
    if (form.channel === "agent" && !form.agent_id) return showToast("Pick an agent for an agent-channel plan", "error");
    try {
      await createRatePlan(selectedRt, {
        channel: form.channel,
        agent_id: form.channel === "agent" ? parseInt(form.agent_id) : null,
        price: parseFloat(form.price),
        valid_from: form.valid_from || null,
        valid_to: form.valid_to || null,
        days_of_week: form.days.length ? form.days.join(",") : null,
        priority: parseInt(form.priority) || 0,
        is_active: true,
      });
      setForm(emptyForm);
      loadPlans(selectedRt);
      showToast("Rate plan added");
    } catch (e) {
      showToast(e.message, "error");
    }
  };

  const handleDelete = async (id) => {
    try {
      await deleteRatePlan(id);
      loadPlans(selectedRt);
      showToast("Rate plan deleted");
    } catch (e) {
      showToast(e.message, "error");
    }
  };

  const runQuote = async () => {
    if (!preview.check_in || !preview.check_out) return showToast("Pick preview dates", "error");
    try {
      setQuote(await getQuote(selectedRt, {
        check_in: preview.check_in, check_out: preview.check_out,
        channel: preview.channel, agent_id: preview.channel === "agent" ? preview.agent_id : undefined,
        quantity: 1,
      }));
    } catch (e) {
      setQuote(null);
      showToast(e.message, "error");
    }
  };

  const daysLabel = (csv) => {
    if (!csv) return "All days";
    return csv.split(",").map((n) => DAYS.find((d) => d.n === parseInt(n))?.label || n).join(", ");
  };
  const channelLabel = (c) => CHANNELS.find((x) => x.value === c)?.label || c;

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-900 via-slate-800 to-slate-900 text-white p-6">
      <div className="max-w-7xl mx-auto">
        <div className="mb-8">
          <h1 className="text-4xl font-bold mb-2 bg-gradient-to-r from-[#E5C07B] to-[#FCD34D] bg-clip-text text-transparent">
            📅 Rate Plans
          </h1>
          <p className="text-slate-400">
            Seasonal / weekend / per-channel & per-agent price overrides. Highest priority wins;
            an agent's own rate applies unless a higher-priority plan matches the night.
          </p>
        </div>

        {/* Room type picker */}
        <div className="bg-gradient-to-r from-slate-800/50 to-slate-700/50 border border-slate-700 p-6 rounded-2xl shadow-xl mb-8 backdrop-blur">
          <label className="mb-2 text-sm font-semibold text-slate-300 block">Room Type</label>
          <select value={selectedRt} onChange={(e) => setSelectedRt(e.target.value)} className={`${inputCls} md:w-72`}>
            {roomTypes.map((rt) => <option key={rt.room_type_id} value={rt.room_type_id}>{rt.name} (rack ₹{rt.price_per_night})</option>)}
          </select>
        </div>

        {/* CREATE */}
        <div className="bg-gradient-to-r from-slate-800/50 to-slate-700/50 border border-slate-700 p-8 rounded-2xl shadow-xl mb-10 backdrop-blur">
          <h2 className="text-2xl font-bold mb-6 text-[#E5C07B] flex items-center gap-2">
            <FaPlus size={20} /> Add Rate Plan
          </h2>
          <div className="grid grid-cols-1 md:grid-cols-6 gap-4">
            <div className="flex flex-col">
              <label className="mb-2 text-sm font-semibold text-slate-300">Channel</label>
              <select value={form.channel} onChange={(e) => setForm({ ...form, channel: e.target.value })} className={inputCls}>
                {CHANNELS.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
              </select>
            </div>
            {form.channel === "agent" && (
              <div className="flex flex-col">
                <label className="mb-2 text-sm font-semibold text-slate-300">Agent</label>
                <select value={form.agent_id} onChange={(e) => setForm({ ...form, agent_id: e.target.value })} className={inputCls}>
                  <option value="">Select…</option>
                  {agents.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
                </select>
              </div>
            )}
            <Field label="Price (₹/night)" type="number" value={form.price} placeholder="e.g., 1200"
              onChange={(e) => setForm({ ...form, price: e.target.value })} />
            <Field label="Valid From" type="date" value={form.valid_from} onChange={(e) => setForm({ ...form, valid_from: e.target.value })} />
            <Field label="Valid To" type="date" value={form.valid_to} onChange={(e) => setForm({ ...form, valid_to: e.target.value })} />
            <Field label="Priority" type="number" value={form.priority} placeholder="0"
              onChange={(e) => setForm({ ...form, priority: e.target.value })} />
          </div>

          <div className="mt-4">
            <label className="mb-2 text-sm font-semibold text-slate-300 block">Weekdays (leave all off = every day)</label>
            <div className="flex flex-wrap gap-2">
              {DAYS.map((d) => (
                <button key={d.n} onClick={() => toggleDay(d.n)} type="button"
                  className={`px-3 py-1.5 rounded-lg text-sm font-medium border transition ${form.days.includes(d.n)
                    ? "bg-[#E5C07B]/20 text-[#FCD34D] border-[#E5C07B]/50"
                    : "bg-slate-900/50 text-slate-400 border-slate-600 hover:border-slate-500"}`}>
                  {d.label}
                </button>
              ))}
              <button onClick={() => setForm({ ...form, days: [5, 6] })} type="button"
                className="px-3 py-1.5 rounded-lg text-sm font-medium border border-slate-600 bg-slate-900/50 text-slate-300 hover:border-slate-500 transition">
                Weekend preset
              </button>
            </div>
          </div>

          <button onClick={handleCreate}
            className="mt-6 bg-gradient-to-r from-[#E5C07B] to-[#D4AF37] hover:shadow-xl text-slate-900 font-bold px-8 py-3 rounded-lg transition-all duration-300 hover:scale-105 flex items-center gap-2">
            <FaPlus size={16} /> Add Rate Plan
          </button>
        </div>

        {/* LIST */}
        <div className="bg-gradient-to-r from-slate-800/50 to-slate-700/50 border border-slate-700 rounded-2xl shadow-xl overflow-hidden backdrop-blur mb-10">
          <div className="p-6 border-b border-slate-700"><h2 className="text-2xl font-bold">🗂️ Plans for this room type</h2></div>
          {loading ? (
            <div className="p-12 flex items-center justify-center">
              <div className="animate-spin inline-block"><div className="h-12 w-12 border-4 border-[#E5C07B] border-t-[#D4AF37] rounded-full"></div></div>
            </div>
          ) : plans.length === 0 ? (
            <div className="p-12 text-center text-slate-400"><div className="text-5xl mb-4">📭</div><p>No rate plans. The rack rate applies to every channel.</p></div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left">
                <thead className="bg-slate-900/80 border-b border-slate-700">
                  <tr>
                    <th className="px-6 py-4 font-semibold text-sm">Channel</th>
                    <th className="px-6 py-4 font-semibold text-sm">Agent</th>
                    <th className="px-6 py-4 font-semibold text-sm">Price</th>
                    <th className="px-6 py-4 font-semibold text-sm">Window</th>
                    <th className="px-6 py-4 font-semibold text-sm">Days</th>
                    <th className="px-6 py-4 font-semibold text-sm">Priority</th>
                    <th className="px-6 py-4 font-semibold text-sm"></th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-700">
                  {plans.map((p) => (
                    <tr key={p.id} className="hover:bg-slate-700/30 transition">
                      <td className="px-6 py-4"><span className="px-3 py-1 rounded-full text-xs font-bold border bg-slate-600/30 text-slate-200 border-slate-500/40">{channelLabel(p.channel)}</span></td>
                      <td className="px-6 py-4 text-slate-300">{p.agent_name || "—"}</td>
                      <td className="px-6 py-4 text-[#E5C07B] font-bold">₹{p.price}</td>
                      <td className="px-6 py-4 text-slate-300">{p.valid_from || "—"} → {p.valid_to || "—"}</td>
                      <td className="px-6 py-4 text-slate-300">{daysLabel(p.days_of_week)}</td>
                      <td className="px-6 py-4 text-slate-300 font-semibold">{p.priority}</td>
                      <td className="px-6 py-4 text-right">
                        <button onClick={() => handleDelete(p.id)} className="bg-red-600 hover:bg-red-700 px-3 py-2 rounded-lg text-white inline-flex items-center gap-1"><FaTrash size={13} /></button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {/* PREVIEW */}
        <div className="bg-gradient-to-r from-slate-800/50 to-slate-700/50 border border-slate-700 p-8 rounded-2xl shadow-xl backdrop-blur">
          <h2 className="text-2xl font-bold mb-6 text-[#E5C07B] flex items-center gap-2"><FaCalculator size={18} /> Price Preview</h2>
          <p className="text-slate-400 text-sm mb-4">Resolves the exact price the desk would charge for these dates (per-night, before GST).</p>
          <div className="grid grid-cols-1 md:grid-cols-5 gap-4">
            <Field label="Check-in" type="date" value={preview.check_in} onChange={(e) => setPreview({ ...preview, check_in: e.target.value })} />
            <Field label="Check-out" type="date" value={preview.check_out} onChange={(e) => setPreview({ ...preview, check_out: e.target.value })} />
            <div className="flex flex-col">
              <label className="mb-2 text-sm font-semibold text-slate-300">Channel</label>
              <select value={preview.channel} onChange={(e) => setPreview({ ...preview, channel: e.target.value })} className={inputCls}>
                {CHANNELS.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
              </select>
            </div>
            {preview.channel === "agent" && (
              <div className="flex flex-col">
                <label className="mb-2 text-sm font-semibold text-slate-300">Agent</label>
                <select value={preview.agent_id} onChange={(e) => setPreview({ ...preview, agent_id: e.target.value })} className={inputCls}>
                  <option value="">Select…</option>
                  {agents.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
                </select>
              </div>
            )}
            <div className="flex items-end">
              <button onClick={runQuote} className="w-full bg-gradient-to-r from-[#E5C07B] to-[#D4AF37] text-slate-900 font-bold px-4 py-2 rounded-lg transition-all hover:scale-105">Resolve</button>
            </div>
          </div>
          {quote && (
            <div className="mt-6 bg-slate-900/50 border border-slate-700 rounded-xl p-5">
              <p className="text-lg">
                <span className="text-slate-400">Room base ({quote.nights} night{quote.nights !== 1 ? "s" : ""}):</span>{" "}
                <span className="text-[#E5C07B] font-bold text-2xl">₹{quote.base}</span>
                <span className="text-slate-500 text-sm"> + {quote.gst_percent}% GST</span>
              </p>
              <div className="mt-3 flex flex-wrap gap-2">
                {quote.nightly.map((n) => (
                  <span key={n.date} className="px-3 py-1 rounded-lg text-xs bg-slate-800 border border-slate-700 text-slate-300">
                    {n.date}: ₹{n.rate} <span className="text-slate-500">({n.source})</span>
                  </span>
                ))}
              </div>
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

export default RatePlans;
