import React, { useEffect, useState } from "react";
import {
  getRooms,
  createRoom,
  updateRoom,
  deleteRoom,
  toggleRoomActive,
  getRoomHistory,
} from "../../api/rooms";
import { getRoomTypes } from "../../api/roomTypes";
import { useConfirm } from "../../components/ConfirmDialog";
import { PageShell } from "../../components/admin/BackofficeUI";
import { FaPlus, FaEdit, FaTrash, FaToggleOn, FaToggleOff, FaHistory } from "react-icons/fa";

const STATUSES = ["vacant", "occupied", "cleaning", "inspected", "maintenance", "blocked"];

const STATUS_COLORS = {
  vacant: "bg-emerald-600/80",
  occupied: "bg-blue-600/80",
  cleaning: "bg-amber-600/80",
  inspected: "bg-teal-600/80",
  maintenance: "bg-orange-600/80",
  blocked: "bg-red-600/80",
};

const Rooms = () => {
  const [rooms, setRooms] = useState([]);
  const [roomTypes, setRoomTypes] = useState([]);
  const [editingRoom, setEditingRoom] = useState(null);
  const [toast, setToast] = useState(null);
  const [historyRoom, setHistoryRoom] = useState(null);
  const [history, setHistory] = useState(null);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState("");

  const openHistory = async (r) => {
    setHistoryRoom(r);
    setHistory(null);
    setHistoryError("");
    setHistoryLoading(true);
    try {
      setHistory(await getRoomHistory(r.room_id));
    } catch (e) {
      setHistoryError(e.message || "Failed to load history.");
    } finally {
      setHistoryLoading(false);
    }
  };

  const [formData, setFormData] = useState({
    room_number: "",
    room_type_id: "",
    building: "1",
    floor: "1",
    max_cards: "4",
    lock_no: "",
    status: "vacant",
  });

  useEffect(() => {
    loadAll();
  }, []);

  const loadAll = async () => {
    try {
      const [r, t] = await Promise.all([getRooms(), getRoomTypes()]);
      setRooms(r);
      setRoomTypes(t);
    } catch (err) {
      showToast(err.message || "Failed to load rooms", "error");
    }
  };

  const typeName = (id) => roomTypes.find((t) => t.room_type_id === id)?.name || "—";

  const handleChange = (e) => setFormData({ ...formData, [e.target.name]: e.target.value });

  const showToast = (message, type = "success") => {
    setToast({ message, type });
    setTimeout(() => setToast(null), 3000);
  };

  const handleCreate = async () => {
    if (!formData.room_number || !formData.room_type_id) {
      showToast("Room number and type are required", "error");
      return;
    }
    try {
      await createRoom(formData);
      setFormData({ room_number: "", room_type_id: "", building: "1", floor: "1", max_cards: "4", lock_no: "", status: "vacant" });
      loadAll();
      showToast("Room created successfully");
    } catch (err) {
      showToast(err.message || "Failed to create room", "error");
    }
  };

  const handleUpdate = async () => {
    try {
      await updateRoom(editingRoom.room_id, {
        room_number: editingRoom.room_number,
        room_type_id: parseInt(editingRoom.room_type_id),
        building: parseInt(editingRoom.building),
        floor: parseInt(editingRoom.floor),
        max_cards: parseInt(editingRoom.max_cards),
        lock_no: editingRoom.lock_no || null,
        status: editingRoom.status,
        is_active: editingRoom.is_active,
      });
      setEditingRoom(null);
      loadAll();
      showToast("Room updated successfully");
    } catch (err) {
      showToast(err.message || "Failed to update room", "error");
    }
  };

  const handleToggleActive = async (room) => {
    try {
      await toggleRoomActive(room.room_id, !room.is_active);
      loadAll();
      showToast(room.is_active ? "Room set to Inactive (out of service)" : "Room set to Active");
    } catch (err) {
      showToast(err.message || "Toggle failed", "error");
    }
  };

  const { confirm } = useConfirm();

  const handleDelete = async (room) => {
    if (!(await confirm({
      title: `Delete room ${room.room_number}?`,
      message: "This permanently removes the room.",
      confirmText: "Delete", tone: "danger",
    }))) return;
    try {
      await deleteRoom(room.room_id);
      loadAll();
      showToast("Room deleted");
    } catch (err) {
      showToast(err.message || "Delete failed", "error");
    }
  };

  return (
    <PageShell
      icon="🚪"
      title="Rooms Management"
      subtitle="Physical rooms — the room number, building & floor must match the door locks (card code BBFFRR)."
    >

        {/* CREATE FORM */}
        <div className="bg-gradient-to-r from-slate-800/50 to-slate-700/50 border border-slate-700 p-8 rounded-2xl shadow-xl mb-10 backdrop-blur">
          <h2 className="text-2xl font-bold mb-6 text-[#E5C07B] flex items-center gap-2">
            <FaPlus size={20} /> Add New Room
          </h2>

          <div className="grid grid-cols-1 md:grid-cols-6 gap-4">
            <InputField label="Room Number" name="room_number" placeholder="e.g., 45" value={formData.room_number} onChange={handleChange} />

            <div className="flex flex-col">
              <label className="mb-2 text-sm font-semibold text-slate-300">Room Type</label>
              <select name="room_type_id" value={formData.room_type_id} onChange={handleChange}
                className="px-4 py-2 bg-slate-900/50 border border-slate-600 rounded-lg text-white focus:outline-none focus:border-[#E5C07B]">
                <option value="">Select…</option>
                {roomTypes.map((t) => (
                  <option key={t.room_type_id} value={t.room_type_id}>{t.name}</option>
                ))}
              </select>
            </div>

            <InputField label="Building" name="building" type="number" value={formData.building} onChange={handleChange} />
            <InputField label="Floor" name="floor" type="number" value={formData.floor} onChange={handleChange} />
            <InputField label="Max Cards" name="max_cards" type="number" value={formData.max_cards} onChange={handleChange} />
            <InputField label="Lock No (optional)" name="lock_no" placeholder="optional" value={formData.lock_no} onChange={handleChange} />
          </div>

          <div className="flex justify-end mt-6">
            <button onClick={handleCreate}
              className="bg-gradient-to-r from-[#E5C07B] to-[#D4AF37] hover:shadow-lg text-slate-900 px-6 py-2 rounded-lg font-bold transition-all duration-300 hover:scale-105">
              Add Room
            </button>
          </div>
        </div>

        {/* LIST */}
        <div className="bg-slate-800/40 border border-slate-700 rounded-2xl overflow-hidden">
          <table className="w-full text-left">
            <thead className="bg-slate-900/60 text-slate-300 text-sm">
              <tr>
                <th className="px-5 py-3">Room</th>
                <th className="px-5 py-3">Type</th>
                <th className="px-5 py-3">Bldg / Floor</th>
                <th className="px-5 py-3">Max Cards</th>
                <th className="px-5 py-3">Lock No</th>
                <th className="px-5 py-3">Status</th>
                <th className="px-5 py-3">Active</th>
                <th className="px-5 py-3 text-right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {rooms.length === 0 && (
                <tr><td colSpan="8" className="px-5 py-6 text-slate-400">No rooms yet. Add your first room above.</td></tr>
              )}
              {rooms.map((r) => (
                <tr key={r.room_id} className={`border-t border-slate-700/60 hover:bg-slate-700/20 ${r.is_active ? "" : "opacity-50"}`}>
                  <td className="px-5 py-3 font-semibold">
                    {r.room_number}
                    {!r.is_active && (
                      <span className="ml-2 px-2 py-0.5 rounded-full text-[10px] font-semibold bg-red-600/80">INACTIVE</span>
                    )}
                  </td>
                  <td className="px-5 py-3">{typeName(r.room_type_id)}</td>
                  <td className="px-5 py-3">{r.building} / {r.floor}</td>
                  <td className="px-5 py-3">{r.max_cards}</td>
                  <td className="px-5 py-3 text-slate-400">{r.lock_no || "—"}</td>
                  <td className="px-5 py-3">
                    <span className={`px-3 py-1 rounded-full text-xs font-medium ${STATUS_COLORS[r.status] || "bg-slate-600/80"}`}>
                      {r.status}
                    </span>
                  </td>
                  <td className="px-5 py-3">
                    <button onClick={() => handleToggleActive(r)}
                      title={r.is_active ? "Active — click to take out of service" : "Inactive — click to activate"}
                      className={r.is_active ? "text-emerald-400 hover:text-emerald-300" : "text-slate-500 hover:text-slate-300"}>
                      {r.is_active ? <FaToggleOn size={22} /> : <FaToggleOff size={22} />}
                    </button>
                  </td>
                  <td className="px-5 py-3 text-right">
                    <button onClick={() => openHistory(r)} className="text-slate-300 hover:text-white mr-4" title="History">
                      <FaHistory />
                    </button>
                    <button onClick={() => setEditingRoom({ ...r })} className="text-[#E5C07B] hover:text-[#FCD34D] mr-4" title="Edit">
                      <FaEdit />
                    </button>
                    <button onClick={() => handleDelete(r)} className="text-red-400 hover:text-red-300" title="Delete">
                      <FaTrash />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {/* HISTORY MODAL (FE-8) */}
        {historyRoom && (
          <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-40 p-4" onClick={() => setHistoryRoom(null)}>
            <div className="bg-slate-800 border border-slate-700 rounded-2xl w-full max-w-3xl max-h-[88vh] overflow-y-auto" onClick={(e) => e.stopPropagation()}>
              <div className="flex items-center justify-between p-6 border-b border-slate-700 sticky top-0 bg-slate-800">
                <h2 className="text-2xl font-bold text-[#E5C07B]">Room {historyRoom.room_number} — history</h2>
                <button onClick={() => setHistoryRoom(null)} className="text-slate-400 hover:text-white text-xl" title="Close">✕</button>
              </div>
              <div className="p-6 space-y-6">
                {historyLoading && <p className="text-slate-400">Loading…</p>}
                {historyError && <p className="text-red-300">{historyError}</p>}
                {history && (
                  <>
                    <HistorySection title="Stays" count={history.counts?.stays}
                      empty="No bookings recorded for this room.">
                      {history.stays.map((s) => (
                        <div key={s.booking_id} className="flex justify-between gap-3 text-sm bg-slate-900/40 rounded px-3 py-2">
                          <span className="truncate">
                            #{s.booking_id} · <span className="text-slate-200">{s.guest_name || "—"}</span>
                            <span className="text-slate-500"> · {s.check_in} → {s.check_out}</span>
                          </span>
                          <span className="text-slate-400 whitespace-nowrap">{s.status} · ₹{(s.grand_total || 0).toLocaleString("en-IN")}</span>
                        </div>
                      ))}
                    </HistorySection>

                    <HistorySection title="Maintenance" count={history.counts?.tickets}
                      empty="No maintenance tickets for this room.">
                      {history.tickets.map((t) => (
                        <div key={t.id} className="flex justify-between gap-3 text-sm bg-slate-900/40 rounded px-3 py-2">
                          <span className="truncate">#{t.id} · <span className="text-slate-200">{t.category}</span> — {t.issue}</span>
                          <span className="text-slate-400 whitespace-nowrap">{t.status} · {(t.created_at || "").slice(0, 10)}</span>
                        </div>
                      ))}
                    </HistorySection>

                    <HistorySection title="Cards issued" count={history.counts?.cards}
                      empty="No cards issued for this room.">
                      {history.cards.map((c) => (
                        <div key={c.id} className="flex justify-between gap-3 text-sm bg-slate-900/40 rounded px-3 py-2">
                          <span className="truncate">#{c.id} · <span className="text-slate-200">{c.issue_type}</span> {c.booking_id ? `· booking #${c.booking_id}` : ""}</span>
                          <span className="text-slate-400 whitespace-nowrap">{c.status} · {(c.issued_at || "").slice(0, 10)}</span>
                        </div>
                      ))}
                    </HistorySection>
                  </>
                )}
              </div>
            </div>
          </div>
        )}

        {/* EDIT MODAL */}
        {editingRoom && (
          <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-40 p-4">
            <div className="bg-slate-800 border border-slate-700 rounded-2xl p-8 w-full max-w-2xl">
              <h2 className="text-2xl font-bold mb-6 text-[#E5C07B]">Edit Room {editingRoom.room_number}</h2>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <InputField label="Room Number" value={editingRoom.room_number}
                  onChange={(e) => setEditingRoom({ ...editingRoom, room_number: e.target.value })} />
                <div className="flex flex-col">
                  <label className="mb-2 text-sm font-semibold text-slate-300">Room Type</label>
                  <select value={editingRoom.room_type_id}
                    onChange={(e) => setEditingRoom({ ...editingRoom, room_type_id: e.target.value })}
                    className="px-4 py-2 bg-slate-900/50 border border-slate-600 rounded-lg text-white focus:outline-none focus:border-[#E5C07B]">
                    {roomTypes.map((t) => (<option key={t.room_type_id} value={t.room_type_id}>{t.name}</option>))}
                  </select>
                </div>
                <InputField label="Building" type="number" value={editingRoom.building}
                  onChange={(e) => setEditingRoom({ ...editingRoom, building: e.target.value })} />
                <InputField label="Floor" type="number" value={editingRoom.floor}
                  onChange={(e) => setEditingRoom({ ...editingRoom, floor: e.target.value })} />
                <InputField label="Max Cards" type="number" value={editingRoom.max_cards}
                  onChange={(e) => setEditingRoom({ ...editingRoom, max_cards: e.target.value })} />
                <InputField label="Lock No" value={editingRoom.lock_no || ""}
                  onChange={(e) => setEditingRoom({ ...editingRoom, lock_no: e.target.value })} />
                <div className="flex flex-col">
                  <label className="mb-2 text-sm font-semibold text-slate-300">Status</label>
                  <select value={editingRoom.status}
                    onChange={(e) => setEditingRoom({ ...editingRoom, status: e.target.value })}
                    className="px-4 py-2 bg-slate-900/50 border border-slate-600 rounded-lg text-white focus:outline-none focus:border-[#E5C07B]">
                    {STATUSES.map((s) => (<option key={s} value={s}>{s}</option>))}
                  </select>
                </div>
                <div className="flex flex-col">
                  <label className="mb-2 text-sm font-semibold text-slate-300">Active (in service)</label>
                  <select value={editingRoom.is_active ? "1" : "0"}
                    onChange={(e) => setEditingRoom({ ...editingRoom, is_active: e.target.value === "1" })}
                    className="px-4 py-2 bg-slate-900/50 border border-slate-600 rounded-lg text-white focus:outline-none focus:border-[#E5C07B]">
                    <option value="1">Active — bookable</option>
                    <option value="0">Inactive — out of service</option>
                  </select>
                </div>
              </div>
              <div className="flex justify-end gap-4 mt-8">
                <button onClick={() => setEditingRoom(null)} className="bg-slate-700 hover:bg-slate-600 px-6 py-2 rounded-lg font-medium transition">Cancel</button>
                <button onClick={handleUpdate} className="bg-gradient-to-r from-[#E5C07B] to-[#D4AF37] hover:shadow-lg text-slate-900 px-6 py-2 rounded-lg font-bold transition-all">Save Changes</button>
              </div>
            </div>
          </div>
        )}

        {/* TOAST */}
        {toast && (
          <div className="fixed top-6 right-6 z-50 animate-slide-in">
            <div className={`px-6 py-4 rounded-xl shadow-2xl font-medium backdrop-blur ${toast.type === "error" ? "bg-red-600/90 text-white" : "bg-green-600/90 text-white"}`}>
              {toast.type === "error" ? "❌" : "✅"} {toast.message}
            </div>
          </div>
        )}
    </PageShell>
  );
};

/* Reusable Input */
const InputField = ({ label, ...props }) => (
  <div className="flex flex-col">
    <label className="mb-2 text-sm font-semibold text-slate-300">{label}</label>
    <input
      {...props}
      className="px-4 py-2 bg-slate-900/50 border border-slate-600 rounded-lg text-white focus:outline-none focus:border-[#E5C07B] focus:ring-2 focus:ring-[#E5C07B]/20 transition"
    />
  </div>
);

const HistorySection = ({ title, count, empty, children }) => {
  const rows = React.Children.toArray(children);
  return (
    <div>
      <div className="flex items-center gap-2 mb-2">
        <h3 className="text-lg font-bold text-[#E5C07B]">{title}</h3>
        <span className="text-slate-500 text-xs">({count ?? rows.length})</span>
      </div>
      {rows.length === 0 ? (
        <p className="text-slate-500 text-sm italic">{empty}</p>
      ) : (
        <div className="space-y-1">{rows}</div>
      )}
    </div>
  );
};

export default Rooms;
