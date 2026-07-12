import React, { useEffect, useState } from "react";
import {
  getPromotions,
  createPromotion,
  updatePromotion,
  togglePromotion,
  deletePromotion,
} from "../../api/promotions";
import { getRoomTypes } from "../../api/roomTypes";
import { FaPlus, FaEdit, FaTrash, FaToggleOn, FaToggleOff } from "react-icons/fa";

const emptyForm = {
  name: "",
  discount_type: "percent",
  discount_value: "",
  room_type_id: "", // "" = all room types
  valid_from: "",
  valid_to: "",
  is_active: false,
};

const Promotions = () => {
  const [promotions, setPromotions] = useState([]);
  const [roomTypes, setRoomTypes] = useState([]);
  const [loading, setLoading] = useState(false);
  const [form, setForm] = useState(emptyForm);
  const [editing, setEditing] = useState(null);
  const [toast, setToast] = useState(null);
  const [confirmDeleteId, setConfirmDeleteId] = useState(null);

  useEffect(() => {
    loadAll();
  }, []);

  const loadAll = async () => {
    setLoading(true);
    try {
      const [promos, rooms] = await Promise.all([getPromotions(), getRoomTypes()]);
      setPromotions(promos);
      setRoomTypes(rooms.sort((a, b) => a.room_type_id - b.room_type_id));
    } catch (err) {
      showToast(err.message || "Failed to load promotions", "error");
    } finally {
      setLoading(false);
    }
  };

  const showToast = (message, type = "success") => {
    setToast({ message, type });
    setTimeout(() => setToast(null), 3000);
  };

  const roomTypeName = (id) => {
    if (id === null || id === undefined) return "All room types";
    const rt = roomTypes.find((r) => r.room_type_id === id);
    return rt ? rt.name : `#${id}`;
  };

  // Build API payload from a form-like object
  const buildPayload = (f) => {
    const payload = {
      name: f.name,
      discount_type: f.discount_type,
      discount_value: parseFloat(f.discount_value),
      room_type_id: f.room_type_id === "" ? null : parseInt(f.room_type_id),
      is_active: !!f.is_active,
      valid_from: f.valid_from || null,
      valid_to: f.valid_to || null,
    };
    return payload;
  };

  const handleCreate = async () => {
    if (!form.name || !form.discount_value) {
      showToast("Name and discount value are required", "error");
      return;
    }
    try {
      await createPromotion(buildPayload(form));
      setForm(emptyForm);
      loadAll();
      showToast("Promotion created");
    } catch (err) {
      showToast(err.message || "Failed to create promotion", "error");
    }
  };

  const handleUpdate = async () => {
    try {
      await updatePromotion(editing.promotion_id, buildPayload(editing));
      setEditing(null);
      loadAll();
      showToast("Promotion updated");
    } catch (err) {
      showToast(err.message || "Failed to update promotion", "error");
    }
  };

  const handleToggle = async (promo) => {
    try {
      await togglePromotion(promo.promotion_id, !promo.is_active);
      loadAll();
      showToast(promo.is_active ? "Promotion deactivated" : "Promotion activated");
    } catch (err) {
      showToast(err.message || "Failed to toggle promotion", "error");
    }
  };

  const handleDelete = async (id) => {
    try {
      await deletePromotion(id);
      setConfirmDeleteId(null);
      loadAll();
      showToast("Promotion deleted");
    } catch (err) {
      showToast(err.message || "Failed to delete promotion", "error");
    }
  };

  const formatValue = (p) =>
    p.discount_type === "percent" ? `${p.discount_value}%` : `₹${p.discount_value}`;

  const formatWindow = (p) => {
    if (!p.valid_from && !p.valid_to) return "Always";
    return `${p.valid_from || "…"} → ${p.valid_to || "…"}`;
  };

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-900 via-slate-800 to-slate-900 text-white p-6">
      <div className="max-w-7xl mx-auto">
        {/* Header */}
        <div className="mb-8">
          <h1 className="text-4xl font-bold mb-2 bg-gradient-to-r from-[#E5C07B] to-[#FCD34D] bg-clip-text text-transparent">
            🎉 Promotions & Discounts
          </h1>
          <p className="text-slate-400">
            Turn on discounts during slow periods to attract more bookings. Active promotions apply
            automatically to matching bookings.
          </p>
        </div>

        {/* CREATE FORM */}
        <div className="bg-gradient-to-r from-slate-800/50 to-slate-700/50 border border-slate-700 p-8 rounded-2xl shadow-xl mb-10 backdrop-blur">
          <h2 className="text-2xl font-bold mb-6 text-[#E5C07B] flex items-center gap-2">
            <FaPlus size={20} /> Add New Promotion
          </h2>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <Field label="Promotion Name">
              <input
                className={inputCls}
                placeholder="e.g., Monsoon Offer"
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
              />
            </Field>

            <Field label="Discount Type">
              <select
                className={inputCls}
                value={form.discount_type}
                onChange={(e) => setForm({ ...form, discount_type: e.target.value })}
              >
                <option value="percent">Percentage (%)</option>
                <option value="flat">Flat (₹)</option>
              </select>
            </Field>

            <Field label={form.discount_type === "percent" ? "Discount (%)" : "Discount (₹)"}>
              <input
                type="number"
                className={inputCls}
                placeholder={form.discount_type === "percent" ? "e.g., 15" : "e.g., 500"}
                value={form.discount_value}
                onChange={(e) => setForm({ ...form, discount_value: e.target.value })}
              />
            </Field>

            <Field label="Applies To">
              <select
                className={inputCls}
                value={form.room_type_id}
                onChange={(e) => setForm({ ...form, room_type_id: e.target.value })}
              >
                <option value="">All room types</option>
                {roomTypes.map((rt) => (
                  <option key={rt.room_type_id} value={rt.room_type_id}>
                    {rt.name}
                  </option>
                ))}
              </select>
            </Field>

            <Field label="Valid From (optional)">
              <input
                type="date"
                className={inputCls}
                value={form.valid_from}
                onChange={(e) => setForm({ ...form, valid_from: e.target.value })}
              />
            </Field>

            <Field label="Valid To (optional)">
              <input
                type="date"
                className={inputCls}
                value={form.valid_to}
                onChange={(e) => setForm({ ...form, valid_to: e.target.value })}
              />
            </Field>
          </div>

          <label className="flex items-center gap-2 mt-4 text-slate-300 cursor-pointer w-fit">
            <input
              type="checkbox"
              checked={form.is_active}
              onChange={(e) => setForm({ ...form, is_active: e.target.checked })}
              className="w-4 h-4 accent-[#E5C07B]"
            />
            Activate immediately
          </label>

          <button
            onClick={handleCreate}
            className="mt-6 bg-gradient-to-r from-[#E5C07B] to-[#D4AF37] hover:shadow-xl text-slate-900 font-bold px-8 py-3 rounded-lg transition-all duration-300 hover:scale-105 flex items-center gap-2"
          >
            <FaPlus size={16} /> Create Promotion
          </button>
        </div>

        {/* LIST */}
        <div className="bg-gradient-to-r from-slate-800/50 to-slate-700/50 border border-slate-700 rounded-2xl shadow-xl overflow-hidden backdrop-blur">
          <div className="p-6 border-b border-slate-700">
            <h2 className="text-2xl font-bold">📊 Existing Promotions</h2>
          </div>

          {loading ? (
            <div className="p-12 flex items-center justify-center">
              <div className="animate-spin h-12 w-12 border-4 border-[#E5C07B] border-t-[#D4AF37] rounded-full" />
            </div>
          ) : promotions.length === 0 ? (
            <div className="p-12 text-center text-slate-400">
              <div className="text-5xl mb-4">📭</div>
              <p>No promotions yet. Create one above!</p>
            </div>
          ) : (
            <div className="overflow-x-auto max-h-[600px] overflow-y-auto">
              <table className="w-full text-left">
                <thead className="bg-slate-900/80 sticky top-0 border-b border-slate-700">
                  <tr>
                    <th className="px-6 py-4 font-semibold text-sm">Name</th>
                    <th className="px-6 py-4 font-semibold text-sm">Discount</th>
                    <th className="px-6 py-4 font-semibold text-sm">Applies To</th>
                    <th className="px-6 py-4 font-semibold text-sm">Window</th>
                    <th className="px-6 py-4 font-semibold text-sm">Status</th>
                    <th className="px-6 py-4 font-semibold text-sm">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-700">
                  {promotions.map((p) => (
                    <tr key={p.promotion_id} className="hover:bg-slate-700/30 transition">
                      <td className="px-6 py-4 font-medium">{p.name}</td>
                      <td className="px-6 py-4 text-[#E5C07B] font-bold">{formatValue(p)}</td>
                      <td className="px-6 py-4 text-slate-300">{roomTypeName(p.room_type_id)}</td>
                      <td className="px-6 py-4 text-slate-400 text-sm">{formatWindow(p)}</td>
                      <td className="px-6 py-4">
                        <span
                          className={`px-3 py-1 rounded-full text-xs font-bold border ${
                            p.is_active
                              ? "bg-green-500/20 text-green-300 border-green-500/30"
                              : "bg-red-500/20 text-red-300 border-red-500/30"
                          }`}
                        >
                          {p.is_active ? "✓ Active" : "✗ Inactive"}
                        </span>
                      </td>
                      <td className="px-6 py-4 space-x-2 whitespace-nowrap">
                        <button
                          onClick={() => handleToggle(p)}
                          title={p.is_active ? "Deactivate" : "Activate"}
                          className={`px-3 py-2 rounded-lg text-white font-medium transition inline-flex items-center gap-1 ${
                            p.is_active
                              ? "bg-yellow-600 hover:bg-yellow-700"
                              : "bg-blue-600 hover:bg-blue-700"
                          }`}
                        >
                          {p.is_active ? <FaToggleOn size={14} /> : <FaToggleOff size={14} />}
                          {p.is_active ? "On" : "Off"}
                        </button>
                        <button
                          onClick={() =>
                            setEditing({
                              ...p,
                              discount_value: p.discount_value,
                              room_type_id: p.room_type_id ?? "",
                              valid_from: p.valid_from || "",
                              valid_to: p.valid_to || "",
                            })
                          }
                          className="bg-blue-600 hover:bg-blue-700 px-3 py-2 rounded-lg text-white font-medium transition inline-flex items-center gap-1"
                        >
                          <FaEdit size={14} /> Edit
                        </button>
                        <button
                          onClick={() => setConfirmDeleteId(p.promotion_id)}
                          className="bg-red-600 hover:bg-red-700 px-3 py-2 rounded-lg text-white font-medium transition inline-flex items-center gap-1"
                        >
                          <FaTrash size={14} /> Delete
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
            <div className="bg-gradient-to-br from-slate-800 to-slate-900 p-8 rounded-2xl w-full max-w-md text-white border border-slate-700 shadow-2xl">
              <h2 className="text-2xl font-bold mb-6 text-[#E5C07B]">✏️ Edit Promotion</h2>
              <div className="space-y-4">
                <Field label="Promotion Name">
                  <input
                    className={inputCls}
                    value={editing.name}
                    onChange={(e) => setEditing({ ...editing, name: e.target.value })}
                  />
                </Field>
                <Field label="Discount Type">
                  <select
                    className={inputCls}
                    value={editing.discount_type}
                    onChange={(e) => setEditing({ ...editing, discount_type: e.target.value })}
                  >
                    <option value="percent">Percentage (%)</option>
                    <option value="flat">Flat (₹)</option>
                  </select>
                </Field>
                <Field label="Discount Value">
                  <input
                    type="number"
                    className={inputCls}
                    value={editing.discount_value}
                    onChange={(e) => setEditing({ ...editing, discount_value: e.target.value })}
                  />
                </Field>
                <Field label="Applies To">
                  <select
                    className={inputCls}
                    value={editing.room_type_id}
                    onChange={(e) => setEditing({ ...editing, room_type_id: e.target.value })}
                  >
                    <option value="">All room types</option>
                    {roomTypes.map((rt) => (
                      <option key={rt.room_type_id} value={rt.room_type_id}>
                        {rt.name}
                      </option>
                    ))}
                  </select>
                </Field>
                <div className="grid grid-cols-2 gap-4">
                  <Field label="Valid From">
                    <input
                      type="date"
                      className={inputCls}
                      value={editing.valid_from}
                      onChange={(e) => setEditing({ ...editing, valid_from: e.target.value })}
                    />
                  </Field>
                  <Field label="Valid To">
                    <input
                      type="date"
                      className={inputCls}
                      value={editing.valid_to}
                      onChange={(e) => setEditing({ ...editing, valid_to: e.target.value })}
                    />
                  </Field>
                </div>
              </div>
              <div className="flex justify-end gap-4 mt-8">
                <button
                  onClick={() => setEditing(null)}
                  className="bg-slate-700 hover:bg-slate-600 px-6 py-2 rounded-lg font-medium transition"
                >
                  Cancel
                </button>
                <button
                  onClick={handleUpdate}
                  className="bg-gradient-to-r from-[#E5C07B] to-[#D4AF37] hover:shadow-lg text-slate-900 px-6 py-2 rounded-lg font-bold transition-all duration-300 hover:scale-105"
                >
                  Save Changes
                </button>
              </div>
            </div>
          </div>
        )}

        {/* DELETE CONFIRM */}
        {confirmDeleteId && (
          <div className="fixed inset-0 bg-black/70 backdrop-blur-sm flex items-center justify-center z-50 p-4">
            <div className="bg-slate-800 p-8 rounded-2xl w-full max-w-sm text-white border border-slate-700 shadow-2xl">
              <h3 className="text-xl font-bold mb-4">Delete this promotion?</h3>
              <p className="text-slate-400 mb-6">This action cannot be undone.</p>
              <div className="flex justify-end gap-4">
                <button
                  onClick={() => setConfirmDeleteId(null)}
                  className="bg-slate-700 hover:bg-slate-600 px-6 py-2 rounded-lg font-medium transition"
                >
                  Cancel
                </button>
                <button
                  onClick={() => handleDelete(confirmDeleteId)}
                  className="bg-red-600 hover:bg-red-700 px-6 py-2 rounded-lg font-bold transition"
                >
                  Delete
                </button>
              </div>
            </div>
          </div>
        )}

        {/* TOAST */}
        {toast && (
          <div className="fixed top-6 right-6 z-50">
            <div
              className={`px-6 py-4 rounded-xl shadow-2xl font-medium backdrop-blur ${
                toast.type === "error" ? "bg-red-600/90 text-white" : "bg-green-600/90 text-white"
              }`}
            >
              {toast.type === "error" ? "❌" : "✅"} {toast.message}
            </div>
          </div>
        )}
      </div>
    </div>
  );
};

const inputCls =
  "px-4 py-2 bg-slate-900/50 border border-slate-600 rounded-lg text-white focus:outline-none focus:border-[#E5C07B] focus:ring-2 focus:ring-[#E5C07B]/20 transition w-full";

const Field = ({ label, children }) => (
  <div className="flex flex-col">
    <label className="mb-2 text-sm font-semibold text-slate-300">{label}</label>
    {children}
  </div>
);

export default Promotions;
