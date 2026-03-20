import React, { useEffect, useState } from "react";
import { getRoomTypes } from "../../api/roomTypes";
import { jwtDecode } from "jwt-decode";
import {
  getBlockedDates,
  blockDate,
  unblockDate,
} from "../../api/availability";

const RoomAvailability = () => {
  const [roomTypes, setRoomTypes] = useState([]);
  const [selectedRoomType, setSelectedRoomType] = useState("");
  const [blockedDates, setBlockedDates] = useState([]);
  const [role, setRole] = useState(null);
  const [date, setDate] = useState("");
  const [reason, setReason] = useState("");

  useEffect(() => {
    loadRoomTypes();
  }, []);

  useEffect(() => {
    const token = localStorage.getItem("adminToken");
    if (token) {
      const decoded = jwtDecode(token);
      setRole(decoded.role);
    }
  }, []);

  const loadRoomTypes = async () => {
    const data = await getRoomTypes();
    setRoomTypes(data);
  };

  const loadBlockedDates = async (roomTypeId) => {
    const data = await getBlockedDates(roomTypeId);
    setBlockedDates(data);
  };

  const handleRoomChange = (e) => {
    const value = e.target.value;
    setSelectedRoomType(value);
    if (value) loadBlockedDates(value);
  };

  const handleBlock = async () => {
    if (!selectedRoomType || !date) return alert("Select room & date");

    await blockDate({
      room_type_id: parseInt(selectedRoomType),
      date,
      reason,
    });

    setDate("");
    setReason("");
    loadBlockedDates(selectedRoomType);
  };

  const handleUnblock = async (blockedDate) => {
    await unblockDate(selectedRoomType, blockedDate);
    loadBlockedDates(selectedRoomType);
  };

  return (
    <div className="min-h-screen bg-[#0F172A] text-[#E5C07B] p-6">
      <h1 className="text-2xl font-bold mb-6">
        Room Availability Management
      </h1>

      {/* Room Selector */}
      <div className="bg-[#111827] p-6 rounded-xl mb-6">
        <label className="block mb-2">Select Room Type</label>

        <select
          className="w-full p-3 bg-[#1F2937] rounded"
          value={selectedRoomType}
          onChange={handleRoomChange}
        >
          <option value="">-- Select Room Type --</option>
          {roomTypes.map((room) => (
            <option key={room.room_type_id} value={room.room_type_id}>
              {room.name}
            </option>
          ))}
        </select>
      </div>

      {/* Block Date Section */}
      {selectedRoomType && (
        <div className="bg-[#111827] p-6 rounded-xl mb-6">
          <h2 className="mb-4 font-semibold">Block Date</h2>

          <div className="grid md:grid-cols-3 gap-4">
            <input
              type="date"
              className="p-3 bg-[#1F2937] rounded"
              value={date}
              onChange={(e) => setDate(e.target.value)}
            />

            <input
              placeholder="Reason (Maintenance, Full, etc.)"
              className="p-3 bg-[#1F2937] rounded"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
            />

            <button
              onClick={handleBlock}
              className="bg-[#E5C07B] text-black rounded px-4 py-2"
            >
              Block Date
            </button>
          </div>
        </div>
      )}

      {/* Blocked Dates List */}
      {selectedRoomType && (
        <div className="bg-[#111827] p-6 rounded-xl">
          <h2 className="mb-4 font-semibold">Blocked Dates</h2>

          {blockedDates.length === 0 ? (
            <p className="text-gray-400">No blocked dates</p>
          ) : (
            <div className="grid md:grid-cols-3 gap-4">
              {blockedDates.map((item) => (
                <div
                  key={item.id}
                  className="bg-[#1F2937] p-4 rounded flex justify-between items-center"
                >
                  <div>
                    <p>{item.date}</p>
                    <p className="text-sm text-gray-400">
                      {item.reason}
                    </p>
                  </div>

                  {role === "admin" && (
                    <button
                      onClick={() => handleUnblock(item.date)}
                      className="bg-red-600 px-3 py-1 rounded text-white"
                    >
                      Unblock
                    </button>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
};

export default RoomAvailability;
