import React, { useEffect, useState } from "react";
import { PageShell } from "../../components/admin/BackofficeUI";
import {
  getAgents,
  createAgent,
  updateAgent,
  toggleAgent,
  deleteAgent,
  getAgentRates,
  createAgentRate,
  deleteAgentRate,
} from "../../api/travelAgents";
import { getRoomTypes } from "../../api/roomTypes";
import { FaPlus, FaEdit, FaTrash, FaToggleOn, FaToggleOff, FaTags } from "react-icons/fa";

const emptyForm = { name: "", contact: "", gst_no: "", commission_percent: "", credit_limit: "" };

const TravelAgents = () => {
  const [agents, setAgents] = useState([]);
  const [roomTypes, setRoomTypes] = useState([]);
  const [loading, setLoading] = useState(false);
  const [toast, setToast] = useState(null);
  const [form, setForm] = useState(emptyForm);
  const [editing, setEditing] = useState(null);
  const [confirmDeleteId, setConfirmDeleteId] = useState(null);

  // Rate-card modal
  const [ratesFor, setRatesFor] = useState(null);
  const [rates, setRates] = useState([]);
  const [rateForm, setRateForm] = useState({ room_type_id: "", rate: "", valid_from: "", valid_to: "" });

  useEffect(() => {
    load();
    getRoomTypes().then(setRoomTypes).catch(() => {});
  }, []);

  const load = async () => {
    setLoading(true);
    try {
      setAgents(await getAgents());
    } catch (e) {
      showToast(e.message, "error");
    }
    setLoading(false);
  };

  const showToast = (message, type = "success") => {
    setToast({ message, type });
    setTimeout(() => setToast(null), 3000);
  };

  const handleCreate = async () => {
    if (!form.name) return showToast("Agent name is required", "error");
    try {
      await createAgent({
        name: form.name,
        contact: form.contact || null,
        gst_no: form.gst_no || null,
        commission_percent: parseFloat(form.commission_percent) || 0,
        credit_limit: parseFloat(form.credit_limit) || 0,
      });
      setForm(emptyForm);
      load();
      showToast("Travel agent created");
    } catch (e) {
      showToast(e.message, "error");
    }
  };

  const handleUpdate = async () => {
    try {
      await updateAgent(editing.id, {
        name: editing.name,
        contact: editing.contact || null,
        gst_no: editing.gst_no || null,
        commission_percent: parseFloat(editing.commission_percent) || 0,
        credit_limit: parseFloat(editing.credit_limit) || 0,
      });
      setEditing(null);
      load();
      showToast("Travel agent updated");
    } catch (e) {
      showToast(e.message, "error");
    }
  };

  const handleDelete = async (id) => {
    try {
      await deleteAgent(id);
      setConfirmDeleteId(null);
      load();
      showToast("Travel agent deleted");
    } catch (e) {
      setConfirmDeleteId(null);
      showToast(e.message, "error");
    }
  };

  // ---- Rate cards ----
  const openRates = async (agent) => {
    setRatesFor(agent);
    setRateForm({ room_type_id: "", rate: "", valid_from: "", valid_to: "" });
    try {
      setRates(await getAgentRates(agent.id));
    } catch (e) {
      showToast(e.message, "error");
    }
  };

  const addRate = async () => {
    if (!rateForm.room_type_id || !rateForm.rate) return showToast("Room type and rate required", "error");
    try {
      await createAgentRate(ratesFor.id, {
        room_type_id: parseInt(rateForm.room_type_id),
        rate: parseFloat(rateForm.rate),
        valid_from: rateForm.valid_from || null,
        valid_to: rateForm.valid_to || null,
      });
      setRateForm({ room_type_id: "", rate: "", valid_from: "", valid_to: "" });
      setRates(await getAgentRates(ratesFor.id));
      showToast("Rate card added");
    } catch (e) {
      showToast(e.message, "error");
    }
  };

  const removeRate = async (rateId) => {
    try {
      await deleteAgentRate(rateId);
      setRates(await getAgentRates(ratesFor.id));
      showToast("Rate card removed");
    } catch (e) {
      showToast(e.message, "error");
    }
  };

  return (
    <PageShell icon="🧳" title="Travel Agents" subtitle="Manage agents, negotiated rate cards, and commission %">

        {/* CREATE */}
        <div className="bg-gradient-to-r from-slate-800/50 to-slate-700/50 border border-slate-700 p-8 rounded-2xl shadow-xl mb-10 backdrop-blur">
          <h2 className="text-2xl font-bold mb-6 text-[#E5C07B] flex items-center gap-2">
            <FaPlus size={20} /> Add Travel Agent
          </h2>
          <div className="grid grid-cols-1 md:grid-cols-5 gap-4">
            <Field label="Agent Name" value={form.name} placeholder="e.g., Acme Travels"
              onChange={(e) => setForm({ ...form, name: e.target.value })} />
            <Field label="Contact" value={form.contact} placeholder="phone / email"
              onChange={(e) => setForm({ ...form, contact: e.target.value })} />
            <Field label="GSTIN" value={form.gst_no} placeholder="agent GST no."
              onChange={(e) => setForm({ ...form, gst_no: e.target.value })} />
            <Field label="Commission %" type="number" value={form.commission_percent} placeholder="e.g., 10"
              onChange={(e) => setForm({ ...form, commission_percent: e.target.value })} />
            <Field label="Credit Limit (₹)" type="number" value={form.credit_limit} placeholder="e.g., 50000"
              onChange={(e) => setForm({ ...form, credit_limit: e.target.value })} />
          </div>
          <button onClick={handleCreate}
            className="mt-6 bg-gradient-to-r from-[#E5C07B] to-[#D4AF37] hover:shadow-xl text-slate-900 font-bold px-8 py-3 rounded-lg transition-all duration-300 hover:scale-105 flex items-center gap-2">
            <FaPlus size={16} /> Create Agent
          </button>
        </div>

        {/* LIST */}
        <div className="bg-gradient-to-r from-slate-800/50 to-slate-700/50 border border-slate-700 rounded-2xl shadow-xl overflow-hidden backdrop-blur">
          <div className="p-6 border-b border-slate-700">
            <h2 className="text-2xl font-bold">📇 Agents</h2>
          </div>
          {loading ? (
            <div className="p-12 flex items-center justify-center">
              <div className="animate-spin inline-block">
                <div className="h-12 w-12 border-4 border-[#E5C07B] border-t-[#D4AF37] rounded-full"></div>
              </div>
            </div>
          ) : agents.length === 0 ? (
            <div className="p-12 text-center text-slate-400">
              <div className="text-5xl mb-4">📭</div>
              <p>No travel agents yet. Add one above.</p>
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left">
                <thead className="bg-slate-900/80 border-b border-slate-700">
                  <tr>
                    <th className="px-6 py-4 font-semibold text-sm">ID</th>
                    <th className="px-6 py-4 font-semibold text-sm">Name</th>
                    <th className="px-6 py-4 font-semibold text-sm">Contact</th>
                    <th className="px-6 py-4 font-semibold text-sm">GSTIN</th>
                    <th className="px-6 py-4 font-semibold text-sm">Commission</th>
                    <th className="px-6 py-4 font-semibold text-sm">Credit Limit</th>
                    <th className="px-6 py-4 font-semibold text-sm">Status</th>
                    <th className="px-6 py-4 font-semibold text-sm">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-700">
                  {agents.map((a) => (
                    <tr key={a.id} className={`hover:bg-slate-700/30 transition ${!a.is_active ? "opacity-50" : ""}`}>
                      <td className="px-6 py-4 font-semibold text-[#FCD34D]">#{a.id}</td>
                      <td className="px-6 py-4 font-medium">{a.name}</td>
                      <td className="px-6 py-4 text-slate-300">{a.contact || "—"}</td>
                      <td className="px-6 py-4 text-slate-300">{a.gst_no || "—"}</td>
                      <td className="px-6 py-4 text-[#E5C07B] font-bold">{a.commission_percent}%</td>
                      <td className="px-6 py-4 text-slate-300">₹{a.credit_limit}</td>
                      <td className="px-6 py-4">
                        <span className={`px-3 py-1 rounded-full text-xs font-bold border ${a.is_active
                          ? "bg-green-500/20 text-green-300 border-green-500/30"
                          : "bg-red-500/20 text-red-300 border-red-500/30"}`}>
                          {a.is_active ? "✓ Active" : "✗ Inactive"}
                        </span>
                      </td>
                      <td className="px-6 py-4 space-x-2 whitespace-nowrap">
                        <button onClick={() => openRates(a)} title="Rate cards"
                          className="bg-[#8B6D2F] hover:bg-[#a07f38] px-3 py-2 rounded-lg text-white font-medium transition inline-flex items-center gap-1">
                          <FaTags size={13} /> Rates
                        </button>
                        <button onClick={() => toggleAgent(a.id, !a.is_active).then(load)} title="Toggle active"
                          className={`px-3 py-2 rounded-lg text-white font-medium transition inline-flex items-center gap-1 ${a.is_active
                            ? "bg-yellow-600 hover:bg-yellow-700" : "bg-blue-600 hover:bg-blue-700"}`}>
                          {a.is_active ? <FaToggleOn size={14} /> : <FaToggleOff size={14} />}
                        </button>
                        <button onClick={() => setEditing({ ...a })} title="Edit"
                          className="bg-blue-600 hover:bg-blue-700 px-3 py-2 rounded-lg text-white font-medium transition inline-flex items-center gap-1">
                          <FaEdit size={14} />
                        </button>
                        <button onClick={() => setConfirmDeleteId(a.id)} title="Delete"
                          className="bg-red-600 hover:bg-red-700 px-3 py-2 rounded-lg text-white font-medium transition inline-flex items-center gap-1">
                          <FaTrash size={14} />
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {/* EDIT MODAL */}
        {editing && (
          <div className="fixed inset-0 bg-black/70 backdrop-blur-sm flex items-center justify-center z-50 p-4">
            <div className="bg-gradient-to-br from-slate-800 to-slate-900 p-8 rounded-2xl w-full max-w-md border border-slate-700 shadow-2xl">
              <h2 className="text-2xl font-bold mb-6 text-[#E5C07B]">✏️ Edit Agent</h2>
              <div className="space-y-4">
                <Field label="Agent Name" value={editing.name} onChange={(e) => setEditing({ ...editing, name: e.target.value })} />
                <Field label="Contact" value={editing.contact || ""} onChange={(e) => setEditing({ ...editing, contact: e.target.value })} />
                <Field label="GSTIN" value={editing.gst_no || ""} onChange={(e) => setEditing({ ...editing, gst_no: e.target.value })} />
                <Field label="Commission %" type="number" value={editing.commission_percent} onChange={(e) => setEditing({ ...editing, commission_percent: e.target.value })} />
                <Field label="Credit Limit (₹)" type="number" value={editing.credit_limit} onChange={(e) => setEditing({ ...editing, credit_limit: e.target.value })} />
              </div>
              <div className="flex justify-end gap-4 mt-8">
                <button onClick={() => setEditing(null)} className="bg-slate-700 hover:bg-slate-600 px-6 py-2 rounded-lg font-medium transition">Cancel</button>
                <button onClick={handleUpdate} className="bg-gradient-to-r from-[#E5C07B] to-[#D4AF37] hover:shadow-lg text-slate-900 px-6 py-2 rounded-lg font-bold transition-all duration-300 hover:scale-105">Save Changes</button>
              </div>
            </div>
          </div>
        )}

        {/* RATE-CARD MODAL */}
        {ratesFor && (
          <div className="fixed inset-0 bg-black/70 backdrop-blur-sm flex items-center justify-center z-50 p-4">
            <div className="bg-gradient-to-br from-slate-800 to-slate-900 p-8 rounded-2xl w-full max-w-2xl border border-slate-700 shadow-2xl">
              <h2 className="text-2xl font-bold mb-1 text-[#E5C07B]">🏷️ Negotiated Rates — {ratesFor.name}</h2>
              <p className="text-slate-400 text-sm mb-6">Per-night rates (GST-exclusive). Leave dates blank for an always-on rate.</p>

              <div className="grid grid-cols-1 md:grid-cols-5 gap-3 mb-4">
                <div className="flex flex-col">
                  <label className="mb-2 text-sm font-semibold text-slate-300">Room Type</label>
                  <select value={rateForm.room_type_id} onChange={(e) => setRateForm({ ...rateForm, room_type_id: e.target.value })}
                    className={inputCls}>
                    <option value="">Select…</option>
                    {roomTypes.map((rt) => <option key={rt.room_type_id} value={rt.room_type_id}>{rt.name}</option>)}
                  </select>
                </div>
                <Field label="Rate (₹/night)" type="number" value={rateForm.rate} onChange={(e) => setRateForm({ ...rateForm, rate: e.target.value })} />
                <Field label="Valid From" type="date" value={rateForm.valid_from} onChange={(e) => setRateForm({ ...rateForm, valid_from: e.target.value })} />
                <Field label="Valid To" type="date" value={rateForm.valid_to} onChange={(e) => setRateForm({ ...rateForm, valid_to: e.target.value })} />
                <div className="flex items-end">
                  <button onClick={addRate} className="w-full bg-gradient-to-r from-[#E5C07B] to-[#D4AF37] text-slate-900 font-bold px-4 py-2 rounded-lg transition-all hover:scale-105">Add</button>
                </div>
              </div>

              <div className="max-h-64 overflow-y-auto rounded-lg border border-slate-700">
                <table className="w-full text-left text-sm">
                  <thead className="bg-slate-900/80 border-b border-slate-700">
                    <tr>
                      <th className="px-4 py-3 font-semibold">Room Type</th>
                      <th className="px-4 py-3 font-semibold">Rate</th>
                      <th className="px-4 py-3 font-semibold">Window</th>
                      <th className="px-4 py-3 font-semibold"></th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-700">
                    {rates.length === 0 ? (
                      <tr><td colSpan={4} className="px-4 py-6 text-center text-slate-400">No rate cards yet.</td></tr>
                    ) : rates.map((r) => (
                      <tr key={r.id} className="hover:bg-slate-700/30">
                        <td className="px-4 py-3">{r.room_type_name}</td>
                        <td className="px-4 py-3 text-[#E5C07B] font-bold">₹{r.rate}</td>
                        <td className="px-4 py-3 text-slate-300">{r.valid_from || "—"} → {r.valid_to || "—"}</td>
                        <td className="px-4 py-3 text-right">
                          <button onClick={() => removeRate(r.id)} className="bg-red-600 hover:bg-red-700 px-3 py-1.5 rounded-lg text-white inline-flex items-center gap-1"><FaTrash size={12} /></button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              <div className="flex justify-end mt-6">
                <button onClick={() => setRatesFor(null)} className="bg-slate-700 hover:bg-slate-600 px-6 py-2 rounded-lg font-medium transition">Close</button>
              </div>
            </div>
          </div>
        )}

        {/* DELETE CONFIRM */}
        {confirmDeleteId && (
          <div className="fixed inset-0 bg-black/70 backdrop-blur-sm flex items-center justify-center z-50 p-4">
            <div className="bg-gradient-to-br from-slate-800 to-slate-900 p-8 rounded-2xl w-full max-w-sm border border-slate-700 shadow-2xl">
              <h2 className="text-xl font-bold mb-3 text-red-300">Delete this agent?</h2>
              <p className="text-slate-400 mb-6">This removes the agent and its rate cards. Agents referenced by bookings can't be deleted — deactivate them instead.</p>
              <div className="flex justify-end gap-4">
                <button onClick={() => setConfirmDeleteId(null)} className="bg-slate-700 hover:bg-slate-600 px-6 py-2 rounded-lg font-medium transition">Cancel</button>
                <button onClick={() => handleDelete(confirmDeleteId)} className="bg-red-600 hover:bg-red-700 px-6 py-2 rounded-lg font-bold transition">Delete</button>
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

const Field = ({ label, ...props }) => (
  <div className="flex flex-col">
    <label className="mb-2 text-sm font-semibold text-slate-300">{label}</label>
    <input {...props} className={inputCls} />
  </div>
);

export default TravelAgents;
