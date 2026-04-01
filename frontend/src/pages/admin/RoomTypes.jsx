import React, { useEffect, useState } from "react";
import {
  getRoomTypes,
  createRoomType,
  toggleRoomType,
  updateRoomType,
} from "../../api/roomTypes";
import { FaPlus, FaEdit, FaToggleOn, FaToggleOff } from "react-icons/fa";

const RoomTypes = () => {
  const [roomTypes, setRoomTypes] = useState([]);
  const [loading, setLoading] = useState(false);
  const [editingRoom, setEditingRoom] = useState(null);
  const [toast, setToast] = useState(null);

  const [formData, setFormData] = useState({
    name: "",
    price: "",
    gst: "",
    occupancy: "",
  });

  useEffect(() => {
    loadRoomTypes();
  }, []);

  const loadRoomTypes = async () => {
    setLoading(true);
    const data = await getRoomTypes();
    const sorted = data.sort((a, b) => a.room_type_id - b.room_type_id);
    setRoomTypes(sorted);
    setLoading(false);
  };

  const handleChange = (e) => {
    setFormData({ ...formData, [e.target.name]: e.target.value });
  };

  const showToast = (message, type = "success") => {
    setToast({ message, type });
    setTimeout(() => setToast(null), 3000);
  };

  const handleCreate = async () => {
    if (!formData.name || !formData.price) {
      showToast("Name and Price required", "error");
      return;
    }

    try {
      await createRoomType(formData);
      setFormData({ name: "", price: "", gst: "", occupancy: "" });
      loadRoomTypes();
      showToast("Room type created successfully");
    } catch (err) {
      showToast("Failed to create room type", "error");
    }
  };

  const handleUpdate = async () => {
    try {
      await updateRoomType(editingRoom.room_type_id, {
        name: editingRoom.name,
        price_per_night: editingRoom.price_per_night,
        gst_percent: editingRoom.gst_percent,
        max_occupancy: editingRoom.max_occupancy,
      });

      setEditingRoom(null);
      loadRoomTypes();
      showToast("Room type updated successfully");
    } catch (err) {
      showToast("Failed to update room type", "error");
    }
  };

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-900 via-slate-800 to-slate-900 text-white p-6">
      <div className="max-w-7xl mx-auto">
        {/* Header */}
        <div className="mb-8">
          <h1 className="text-4xl font-bold mb-2 bg-gradient-to-r from-[#E5C07B] to-[#FCD34D] bg-clip-text text-transparent">
            🛏️ Room Types Management
          </h1>
          <p className="text-slate-400">Create and manage room types</p>
        </div>

        {/* CREATE FORM */}
        <div className="bg-gradient-to-r from-slate-800/50 to-slate-700/50 border border-slate-700 p-8 rounded-2xl shadow-xl mb-10 backdrop-blur">
          <h2 className="text-2xl font-bold mb-6 text-[#E5C07B] flex items-center gap-2">
            <FaPlus size={20} /> Add New Room Type
          </h2>

          <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
            <InputField
              label="Room Name"
              name="name"
              placeholder="e.g., Deluxe Suite"
              value={formData.name}
              onChange={handleChange}
            />

            <InputField
              label="Price (₹/night)"
              name="price"
              type="number"
              placeholder="e.g., 5000"
              value={formData.price}
              onChange={handleChange}
            />

            <InputField
              label="GST %"
              name="gst"
              type="number"
              placeholder="e.g., 18"
              value={formData.gst}
              onChange={handleChange}
            />

            <InputField
              label="Max Occupancy"
              name="occupancy"
              type="number"
              placeholder="e.g., 4"
              value={formData.occupancy}
              onChange={handleChange}
            />
          </div>

          <button
            onClick={handleCreate}
            className="mt-6 bg-gradient-to-r from-[#E5C07B] to-[#D4AF37] hover:shadow-xl text-slate-900 font-bold px-8 py-3 rounded-lg transition-all duration-300 hover:scale-105 flex items-center gap-2"
          >
            <FaPlus size={16} /> Create Room Type
          </button>
        </div>

        {/* LIST */}
        <div className="bg-gradient-to-r from-slate-800/50 to-slate-700/50 border border-slate-700 rounded-2xl shadow-xl overflow-hidden backdrop-blur">
          <div className="p-6 border-b border-slate-700">
            <h2 className="text-2xl font-bold">📊 Existing Room Types</h2>
          </div>

          {loading ? (
            <div className="p-12 flex items-center justify-center">
              <div className="text-center">
                <div className="animate-spin mb-4 mx-auto inline-block">
                  <div className="h-12 w-12 border-4 border-[#E5C07B] border-t-[#D4AF37] rounded-full"></div>
                </div>
                <p className="text-slate-400 font-medium">Loading room types...</p>
              </div>
            </div>
          ) : roomTypes.length === 0 ? (
            <div className="p-12 text-center text-slate-400">
              <div className="text-5xl mb-4">📭</div>
              <p>No room types yet. Create one above!</p>
            </div>
          ) : (
            <div className="overflow-x-auto max-h-[600px] overflow-y-auto">
              <table className="w-full text-left">
                <thead className="bg-slate-900/80 sticky top-0 border-b border-slate-700">
                  <tr>
                    <th className="px-6 py-4 font-semibold text-sm">ID</th>
                    <th className="px-6 py-4 font-semibold text-sm">Name</th>
                    <th className="px-6 py-4 font-semibold text-sm">Price/Night</th>
                    <th className="px-6 py-4 font-semibold text-sm">GST</th>
                    <th className="px-6 py-4 font-semibold text-sm">Occupancy</th>
                    <th className="px-6 py-4 font-semibold text-sm">Status</th>
                    <th className="px-6 py-4 font-semibold text-sm">Actions</th>
                  </tr>
                </thead>

                <tbody className="divide-y divide-slate-700">
                  {roomTypes.map((room) => (
                    <tr
                      key={room.room_type_id}
                      className="hover:bg-slate-700/30 transition"
                    >
                      <td className="px-6 py-4 font-semibold text-[#FCD34D]">#{room.room_type_id}</td>
                      <td className="px-6 py-4 font-medium">{room.name}</td>
                      <td className="px-6 py-4 text-[#E5C07B] font-bold">₹{room.price_per_night}</td>
                      <td className="px-6 py-4 text-slate-300">{room.gst_percent}%</td>
                      <td className="px-6 py-4 text-slate-300">{room.max_occupancy} guests</td>
                      <td className="px-6 py-4">
                        <span
                          className={`px-3 py-1 rounded-full text-xs font-bold border ${
                            room.is_active
                              ? "bg-green-500/20 text-green-300 border-green-500/30"
                              : "bg-red-500/20 text-red-300 border-red-500/30"
                          }`}
                        >
                          {room.is_active ? "✓ Active" : "✗ Inactive"}
                        </span>
                      </td>
                      <td className="px-6 py-4 space-x-2">
                        <button
                          onClick={() =>
                            toggleRoomType(
                              room.room_type_id,
                              !room.is_active
                            ).then(loadRoomTypes)
                          }
                          title={room.is_active ? "Deactivate" : "Activate"}
                          className={`px-3 py-2 rounded-lg text-white font-medium transition flex items-center gap-1 ${
                            room.is_active
                              ? "bg-yellow-600 hover:bg-yellow-700"
                              : "bg-blue-600 hover:bg-blue-700"
                          }`}
                        >
                          {room.is_active ? <FaToggleOn size={14} /> : <FaToggleOff size={14} />}
                          {room.is_active ? "Active" : "Inactive"}
                        </button>

                        <button
                          onClick={() => setEditingRoom(room)}
                          title="Edit Room"
                          className="bg-blue-600 hover:bg-blue-700 px-3 py-2 rounded-lg text-white font-medium transition inline-flex items-center gap-1"
                        >
                          <FaEdit size={14} /> Edit
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
        {editingRoom && (
          <div className="fixed inset-0 bg-black/70 backdrop-blur-sm flex items-center justify-center z-50 p-4">
            <div className="bg-gradient-to-br from-slate-800 to-slate-900 p-8 rounded-2xl w-full max-w-md text-white border border-slate-700 shadow-2xl">
              <h2 className="text-2xl font-bold mb-6 text-[#E5C07B]">✏️ Edit Room Type</h2>

              <div className="space-y-4">
                <InputField
                  label="Room Name"
                  value={editingRoom.name}
                  onChange={(e) =>
                    setEditingRoom({
                      ...editingRoom,
                      name: e.target.value,
                    })
                  }
                />

                <InputField
                  label="Price (₹/night)"
                  type="number"
                  value={editingRoom.price_per_night}
                  onChange={(e) =>
                    setEditingRoom({
                      ...editingRoom,
                      price_per_night: e.target.value,
                    })
                  }
                />

                <InputField
                  label="GST %"
                  type="number"
                  value={editingRoom.gst_percent}
                  onChange={(e) =>
                    setEditingRoom({
                      ...editingRoom,
                      gst_percent: e.target.value,
                    })
                  }
                />

                <InputField
                  label="Max Occupancy"
                  type="number"
                  value={editingRoom.max_occupancy}
                  onChange={(e) =>
                    setEditingRoom({
                      ...editingRoom,
                      max_occupancy: e.target.value,
                    })
                  }
                />
              </div>

              <div className="flex justify-end gap-4 mt-8">
                <button
                  onClick={() => setEditingRoom(null)}
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

        {/* TOAST */}
        {toast && (
          <div className="fixed top-6 right-6 z-50 animate-slide-in">
            <div
              className={`px-6 py-4 rounded-xl shadow-2xl font-medium backdrop-blur ${
                toast.type === "error"
                  ? "bg-red-600/90 text-white"
                  : "bg-green-600/90 text-white"
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

export default RoomTypes;
