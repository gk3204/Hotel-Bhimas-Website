import React, { useState, useEffect } from "react";
import { getRoomTypes } from "../../api/roomTypes";

import doubleOrdinary from "./images/rooms/double-ordinary.png";
import doubleDeluxe from "./images/rooms/double-deluxe.png";
import fourBed from "./images/rooms/4-bed.png";
import fiveBed from "./images/rooms/5-bed.png";
import { useNavigate } from "react-router-dom";



const Rooms = () => {
  const navigate = useNavigate();
  const [rooms, setRooms] = useState([]);
  const [showModal, setShowModal] = useState(false);
  const [activeRoom, setActiveRoom] = useState(null);
  const [activeImage, setActiveImage] = useState(0);

  /* ================= FRONTEND EXTRA DETAILS ================= */

  const roomExtraDetails = {
    1: {
      size: "200 sq.ft",
      capacity: "2 Adults",
      images: [doubleOrdinary, doubleOrdinary],
      desc: "Comfortable and budget-friendly room ideal for short stays.",
      features: ["Non-AC", "24x7 Hot Water", "Free Parking", "TV"],
    },
    2: {
      size: "250 sq.ft",
      capacity: "2 Adults + 1 Child",
      images: [doubleDeluxe, doubleDeluxe],
      desc: "Spacious deluxe room with AC and modern amenities.",
      features: ["AC Room", "24x7 Hot Water", "Free Parking", "TV"],
    },
    3: {
      size: "350 sq.ft",
      capacity: "4 Adults + 1 Child",
      images: [fourBed, fourBed],
      desc: "Perfect choice for families and small groups.",
      features: ["AC", "Family Room", "24x7 Hot Water", "TV"],
    },
    4: {
      size: "450 sq.ft",
      capacity: "5 Adults + 2 Children",
      images: [fiveBed, fiveBed],
      desc: "Large room suitable for bigger families and group stays.",
      features: ["AC", "Large Space", "24x7 Hot Water", "TV"],
    },
  };

  /* ================= FETCH ROOM TYPES ================= */

  useEffect(() => {
    const fetchRooms = async () => {
      try {
        const data = await getRoomTypes();

        const merged = data.map((room) => ({
          ...room,
          ...(roomExtraDetails[room.room_type_id] || {}),
        }));

        setRooms(merged);
      } catch (error) {
        console.error("Error fetching room types", error);
      }
    };

    fetchRooms();
  }, []);

  const handleBookNow = (roomId) => {
    navigate(`/booking/${roomId}`);
  };

  return (
    <div className="min-h-screen bg-[#F8FAFC] px-6 py-20">

      {/* ================= TITLE ================= */}
      <h2 className="text-4xl font-bold text-center text-[#0F172A] mb-16">
        Choose Your Room
      </h2>

      {/* ================= ROOM LIST ================= */}
      <div className="grid md:grid-cols-2 gap-10 max-w-6xl mx-auto">
        {rooms.map((room) => (
          <div
            key={room.room_type_id}
            className="bg-white rounded-3xl shadow-lg overflow-hidden hover:shadow-2xl transition"
          >
            <img
              src={room.images?.[0]}
              alt={room.name}
              className="w-full h-72 object-cover"
            />

            <div className="p-8">
              <h3 className="text-2xl font-bold text-[#0F172A] mb-2">
                {room.name}
              </h3>

              <p className="text-gray-600 mb-2">{room.desc}</p>

              <p className="text-sm text-gray-500">
                <strong>Size:</strong> {room.size}
              </p>

              <p className="text-sm text-gray-500">
                <strong>Capacity:</strong> {room.capacity}
              </p>

              <p className="text-sm text-gray-500 mb-4">
                <strong>Max Occupancy:</strong> {room.max_occupancy}
              </p>

              <ul className="mb-6 text-gray-500 space-y-1">
                {room.features?.map((feature, index) => (
                  <li key={index}>✔ {feature}</li>
                ))}
              </ul>

              <div className="flex gap-3">
                <button
                  onClick={() => {
                    setActiveRoom(room);
                    setActiveImage(0);
                    setShowModal(true);
                  }}
                  className="flex-1 border border-[#0F172A] text-[#0F172A] py-2 rounded-full font-semibold hover:bg-[#0F172A] hover:text-white transition"
                >
                  View Details
                </button>

                <button
                  onClick={() => handleBookNow(room.room_type_id)}
                  className="flex-1 bg-[#E5C07B] text-[#0F172A] py-2 rounded-full font-semibold hover:bg-[#FCD34D] transition"
                >
                  Book Now
                </button>
              </div>
            </div>
          </div>
        ))}
      </div>


      {/* ================= MODAL ================= */}
      {showModal && activeRoom && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4">
          <div className="bg-white max-w-3xl w-full rounded-3xl overflow-hidden shadow-2xl relative">

            <button
              onClick={() => setShowModal(false)}
              className="absolute top-4 right-4 text-xl font-bold"
            >
              ✕
            </button>

            <img
              src={activeRoom.images?.[activeImage]}
              alt={activeRoom.name}
              className="w-full h-72 object-cover"
            />

            <div className="flex gap-3 p-4 justify-center">
              {activeRoom.images?.map((img, index) => (
                <img
                  key={index}
                  src={img}
                  alt="thumbnail"
                  onClick={() => setActiveImage(index)}
                  className={`h-16 w-20 object-cover rounded cursor-pointer border ${
                    activeImage === index
                      ? "border-[#E5C07B]"
                      : "border-gray-300"
                  }`}
                />
              ))}
            </div>

            <div className="p-8">
              <h3 className="text-2xl font-bold mb-3">
                {activeRoom.name}
              </h3>

              <p className="text-gray-600 mb-2">
                <strong>Room Size:</strong> {activeRoom.size}
              </p>

              <p className="text-gray-600 mb-2">
                <strong>Capacity:</strong> {activeRoom.capacity}
              </p>

              <p className="text-gray-600 mb-4">
                {activeRoom.desc}
              </p>

              <div className="flex justify-between items-center">
                <span className="text-xl font-bold text-[#E5C07B]">
                  ₹ {activeRoom.price_per_night} / night
                </span>

                <button
                  onClick={() => {
                    handleBookNow(activeRoom.room_type_id);
                    setShowModal(false);
                  }}
                  className="bg-[#0F172A] text-white px-6 py-3 rounded-full font-semibold hover:bg-[#1E293B] transition"
                >
                  Book This Room
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

    </div>
  );
};

export default Rooms;
