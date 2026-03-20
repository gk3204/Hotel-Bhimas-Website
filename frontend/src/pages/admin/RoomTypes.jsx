import React, { useEffect, useState } from "react";
import {
  getRoomTypes,
  createRoomType,
  toggleRoomType,
  updateRoomType,
} from "../../api/roomTypes";

const RoomTypes = () => {
  const [roomTypes, setRoomTypes] = useState([]);
  const [loading, setLoading] = useState(false);
  const [editingRoom, setEditingRoom] = useState(null);

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
    setRoomTypes(data);
    setLoading(false);
  };

  const handleChange = (e) => {
    setFormData({ ...formData, [e.target.name]: e.target.value });
  };

  const handleCreate = async () => {
    if (!formData.name || !formData.price) {
      alert("Name and Price required");
      return;
    }

    await createRoomType(formData);
    setFormData({ name: "", price: "", gst: "", occupancy: "" });
    loadRoomTypes();
  };

  const handleUpdate = async () => {
    await updateRoomType(editingRoom.room_type_id, {
      name: editingRoom.name,
      price_per_night: editingRoom.price_per_night,
      gst_percent: editingRoom.gst_percent,
      max_occupancy: editingRoom.max_occupancy,
    });

    setEditingRoom(null);
    loadRoomTypes();
  };

  return (
    <div className="min-h-screen bg-[#0F172A] text-[#E5C07B] p-6">
      <h1 className="text-3xl font-bold mb-8">Room Types Management</h1>

      {/* CREATE FORM */}
      <div className="bg-[#111827] p-6 rounded-2xl shadow-xl mb-10">
        <h2 className="text-xl font-semibold mb-6">Add New Room Type</h2>

        <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
          <InputField
            label="Room Name"
            name="name"
            value={formData.name}
            onChange={handleChange}
          />

          <InputField
            label="Price (₹)"
            name="price"
            type="number"
            value={formData.price}
            onChange={handleChange}
          />

          <InputField
            label="GST %"
            name="gst"
            type="number"
            value={formData.gst}
            onChange={handleChange}
          />

          <InputField
            label="Max Occupancy"
            name="occupancy"
            type="number"
            value={formData.occupancy}
            onChange={handleChange}
          />
        </div>

        <button
          onClick={handleCreate}
          className="mt-6 bg-[#E5C07B] hover:bg-yellow-500 transition text-black font-semibold px-8 py-2 rounded-xl"
        >
          Create Room
        </button>
      </div>

      {/* LIST */}
      <div className="bg-[#111827] rounded-2xl shadow-xl p-6">
        <h2 className="text-xl font-semibold mb-6">Existing Room Types</h2>

        {loading ? (
          <p>Loading...</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left">
              <thead>
                <tr className="border-b border-[#E5C07B]/30">
                  <th className="py-3">Name</th>
                  <th>Price</th>
                  <th>GST</th>
                  <th>Occupancy</th>
                  <th>Status</th>
                  <th>Actions</th>
                </tr>
              </thead>

              <tbody>
                {roomTypes.map((room) => (
                  <tr
                    key={room.room_type_id}
                    className="border-b border-[#E5C07B]/10 hover:bg-[#1F2937] transition"
                  >
                    <td className="py-3">{room.name}</td>
                    <td>₹{room.price_per_night}</td>
                    <td>{room.gst_percent}%</td>
                    <td>{room.max_occupancy}</td>
                    <td>
                      <span
                        className={`px-3 py-1 rounded-full text-sm ${
                          room.is_active
                            ? "bg-green-600 text-white"
                            : "bg-red-600 text-white"
                        }`}
                      >
                        {room.is_active ? "Active" : "Inactive"}
                      </span>
                    </td>
                    <td className="space-x-2">
                      <button
                        onClick={() =>
                          toggleRoomType(
                            room.room_type_id,
                            !room.is_active
                          ).then(loadRoomTypes)
                        }
                        className="bg-yellow-600 px-3 py-1 rounded text-white"
                      >
                        Toggle
                      </button>

                      <button
                        onClick={() => setEditingRoom(room)}
                        className="bg-blue-600 px-3 py-1 rounded text-white"
                      >
                        Edit
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
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center">
          <div className="bg-[#111827] p-8 rounded-2xl w-96">
            <h2 className="text-xl mb-6">Edit Room</h2>

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
              label="Price"
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
              label="Occupancy"
              type="number"
              value={editingRoom.max_occupancy}
              onChange={(e) =>
                setEditingRoom({
                  ...editingRoom,
                  max_occupancy: e.target.value,
                })
              }
            />

            <div className="flex justify-between mt-6">
              <button
                onClick={() => setEditingRoom(null)}
                className="bg-gray-600 px-4 py-2 rounded"
              >
                Cancel
              </button>

              <button
                onClick={handleUpdate}
                className="bg-[#E5C07B] text-black px-4 py-2 rounded"
              >
                Save
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

/* Reusable Input */
const InputField = ({ label, ...props }) => (
  <div className="flex flex-col">
    <label className="mb-2 text-sm text-[#E5C07B]/80">{label}</label>
    <input
      {...props}
      className="p-2 bg-[#1F2937] rounded-lg focus:outline-none focus:ring-2 focus:ring-[#E5C07B]"
    />
  </div>
);

export default RoomTypes;
