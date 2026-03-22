import React, { useState, useEffect } from "react";
import { getRoomTypes } from "../../api/roomTypes";

import doubleOrdinary from "./images/rooms/double-ordinary.png";
import doubleDeluxe from "./images/rooms/double-deluxe.png";
import fourBed from "./images/rooms/4-bed.png";
import fourBedAc from "./images/rooms/4 bed ac.png";
import fourBedAc1 from "./images/rooms/4 bed ac-1.png";
import threeBedAc from "./images/rooms/3 bed ac.png";
import threeBedAc1 from "./images/rooms/3 bed ac-2.png";
import threeBedAc2 from "./images/rooms/3 bed ac-1.png";
import threeBedNonAc from "./images/rooms/3 bed nonac.png";
import threeBedNonAc1 from "./images/rooms/3 bed nonac-1.png";
import fourBedNonAc from "./images/rooms/4 bed nonac.png";
import fourBedNonAc1 from "./images/rooms/4 bed nonac-1.png";
import fiveBed from "./images/rooms/5 bed-2.png";
import fiveBed1 from "./images/rooms/5 bed-1.png";
import doubleDeluxe1 from "./images/rooms/double-deluxe1.png";
import washroom from "./images/rooms/wash.png";
import washroom1 from "./images/rooms/wash-1.png";
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
      capacity: "2 Adults + Children",
      images: [doubleOrdinary,washroom, washroom1],
      desc: "Comfortable and budget-friendly room ideal for short stays.",
      features: ["Non-AC", "1 King Size Bed", "24x7 Hot Water", "Free Parking", "TV"],
    },
    2: {
      size: "200 sq.ft",
      capacity: "2 Adults + Children",
      images: [doubleDeluxe, doubleDeluxe1,washroom, washroom1],
      desc: "Spacious deluxe room with AC and modern amenities.",
      features: ["AC Room", "1 King Size Bed", "24x7 Hot Water", "Free Parking", "TV"],
    },
    3: {
      size: "320 sq.ft",
      capacity: "3 Adults + Children",
      images: [threeBedNonAc, threeBedNonAc1,washroom, washroom1],
      desc: "Perfect choice for small families with excellent space and comfort.",
      features: ["Non-AC", "1 King Size Bed + 1 Single Bed", "24x7 Hot Water", "Free Parking", "TV"],
    },
    4: {
      size: "320 sq.ft",
      capacity: "3 Adults + Children",
      images: [threeBedAc, threeBedAc1,washroom, washroom1],
      desc: "Premium triple bed room with AC, ideal for family relaxation.",
      features: ["AC Room", "1 King Size Bed + 1 Single Bed", "24x7 Hot Water", "Free Parking", "TV"],
    },
    5: {
      size: "480 sq.ft",
      capacity: "4 Adults + Children",
      images: [fourBedAc, fourBedAc1,washroom, washroom1],
      desc: "Spacious four bed AC room perfect for larger groups and families.",
      features: ["AC Room", "2 King Size Beds or 1 King Size Bed + 2 Single Beds", "24x7 Hot Water", "Free Parking", "TV"],
    },
    6: {
      size: "480 sq.ft",
      capacity: "4 Adults + Children",
      images: [fourBedNonAc, fourBedNonAc1,washroom, washroom1],
      desc: "Budget-friendly four bed non-AC room offering comfort for groups.",
      features: ["Non-AC", "2 King Size Beds or 1 King Size Bed + 2 Single Beds", "24x7 Hot Water", "Free Parking", "TV"],
    },
    7: {
      size: "580 sq.ft",
      capacity: "5 Adults + Children",
      images: [fourBed, threeBedAc, threeBedAc2,washroom, washroom1],
      desc: "Luxury five bed AC room with premium amenities for large family stays.",
      features: ["AC Room", "2 King Size Beds + 1 Single Bed or 1 King Size Bed + 3 Single Beds", "24x7 Hot Water", "Free Parking", "TV"],
    },
    8: {
      size: "580 sq.ft",
      capacity: "5 Adults + Children",
      images: [threeBedNonAc, fiveBed,washroom, washroom1],
      desc: "Spacious five bed non-AC room ideal for large family gatherings.",
      features: ["Non-AC", "2 King Size Beds + 1 Single Bed or 1 King Size Bed + 3 Single Beds", "24x7 Hot Water", "Free Parking", "TV"],
    },
  };

  /* ================= FETCH ROOM TYPES ================= */

  useEffect(() => {
    const fetchRooms = async () => {
      try {
        const data = await getRoomTypes();

        const merged = data
          .filter((room) => room.is_active)
          .map((room) => ({
            ...room,
            ...(roomExtraDetails[room.room_type_id] || {}),
          }))
          .sort((a, b) => a.room_type_id - b.room_type_id);

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
    <div className="min-h-screen bg-gradient-to-br from-[#F8FAFC] via-white to-[#F0F4F8] px-6 py-20">

      {/* ================= TITLE SECTION ================= */}
      <div className="text-center mb-20">
        <h2 className="text-5xl font-bold text-center text-[#0F172A] mb-4">
          Choose Your Room
        </h2>
        <p className="text-lg text-gray-600">
          Discover our carefully curated selection of comfortable and luxurious rooms
        </p>
      </div>

      {/* ================= ROOM LIST ================= */}
      <div className="grid md:grid-cols-2 gap-10 max-w-6xl mx-auto">
        {rooms.map((room) => (
          <div
            key={room.room_type_id}
            className="bg-white rounded-3xl shadow-lg overflow-hidden hover:shadow-2xl hover:scale-105 transition-all duration-300 group"
          >
            <div className="relative overflow-hidden">
              <img
                src={room.images?.[0]}
                alt={room.name}
                className="w-full h-72 object-cover group-hover:scale-110 transition-transform duration-500"
              />
              <div className="absolute top-4 right-4 flex gap-2">
                {room.name?.includes("AC") && (
                  <span className="bg-blue-500 text-white px-3 py-1 rounded-full text-sm font-semibold">
                    AC
                  </span>
                )}
              </div>
            </div>

            <div className="p-8">
              <h3 className="text-2xl font-bold text-[#0F172A] mb-2">
                {room.name}
              </h3>

              <p className="text-gray-600 mb-4 text-sm">{room.desc}</p>

              <div className="flex justify-between mb-4 pb-4 border-b border-gray-200">
                <div>
                  <p className="text-xs text-gray-500 font-semibold">SIZE</p>
                  <p className="text-sm font-bold text-[#0F172A]">{room.size}</p>
                </div>
                <div>
                  <p className="text-xs text-gray-500 font-semibold">CAPACITY</p>
                  <p className="text-sm font-bold text-[#0F172A]">{room.capacity}</p>
                </div>
              </div>

              <div className="mb-6">
                <p className="text-3xl font-bold text-[#E5C07B]">
                  ₹ {room.price_per_night}
                  <span className="text-lg text-gray-500 font-normal"> / night</span>
                </p>
              </div>

              <ul className="mb-6 text-gray-600 space-y-2">
                {room.features?.slice(0, 4).map((feature, index) => (
                  <li key={index} className="flex items-center gap-2">
                    <span className="text-[#E5C07B] font-bold">✓</span> {feature}
                  </li>
                ))}
              </ul>

              <div className="flex gap-3">
                <button
                  onClick={() => {
                    setActiveRoom(room);
                    setActiveImage(0);
                    setShowModal(true);
                  }}
                  className="flex-1 border-2 border-[#0F172A] text-[#0F172A] py-3 rounded-full font-semibold hover:bg-[#0F172A] hover:text-white transition-all duration-300"
                >
                  View Details
                </button>

                <button
                  onClick={() => handleBookNow(room.room_type_id)}
                  className="flex-1 bg-[#E5C07B] text-[#0F172A] py-3 rounded-full font-semibold hover:bg-[#FCD34D] hover:shadow-lg transition-all duration-300"
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
        <div className="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center z-50 p-4 animate-fade">
          <div className="bg-white max-w-3xl w-full max-h-[90vh] overflow-y-auto rounded-3xl shadow-2xl relative flex flex-col">

            <button
              onClick={() => setShowModal(false)}
              className="absolute top-4 right-4 bg-white rounded-full w-10 h-10 flex items-center justify-center text-2xl font-bold hover:bg-gray-100 transition z-10"
            >
              ✕
            </button>

            <div className="flex-shrink-0">
              <img
                src={activeRoom.images?.[activeImage]}
                alt={activeRoom.name}
                className="w-full h-auto max-h-96 object-contain bg-gray-50"
              />

              <div className="flex gap-2 p-4 justify-center bg-gray-50">
                {activeRoom.images?.map((img, index) => (
                  <img
                    key={index}
                    src={img}
                    alt="thumbnail"
                    onClick={() => setActiveImage(index)}
                    className={`h-16 w-20 object-cover rounded-lg cursor-pointer border-2 transition-all ${
                      activeImage === index
                        ? "border-[#E5C07B] scale-110"
                        : "border-gray-300 hover:border-gray-400"
                    }`}
                  />
                ))}
              </div>
            </div>

            <div className="flex-1 overflow-y-auto p-8">
              <h3 className="text-3xl font-bold mb-2">
                {activeRoom.name}
              </h3>
              <p className="text-gray-500 mb-4">Room ID: {activeRoom.room_type_id}</p>

              <div className="grid grid-cols-2 gap-4 mb-6 pb-6 border-b border-gray-200">
                <div>
                  <p className="text-xs text-gray-500 font-semibold">ROOM SIZE</p>
                  <p className="text-lg font-bold text-[#0F172A]">{activeRoom.size}</p>
                </div>
                <div>
                  <p className="text-xs text-gray-500 font-semibold">CAPACITY</p>
                  <p className="text-lg font-bold text-[#0F172A]">{activeRoom.capacity}</p>
                </div>
              </div>

              <p className="text-gray-700 mb-6 leading-relaxed">
                {activeRoom.desc}
              </p>

              <div className="mb-6">
                <p className="text-xs text-gray-500 font-semibold mb-3">AMENITIES</p>
                <ul className="grid grid-cols-2 gap-3">
                  {activeRoom.features?.map((feature, index) => (
                    <li key={index} className="flex items-center gap-2 text-gray-700">
                      <span className="text-[#E5C07B] font-bold text-lg">✓</span> {feature}
                    </li>
                  ))}
                </ul>
              </div>

              <div className="flex items-center justify-between gap-4 bg-gradient-to-r from-[#F8FAFC] to-[#F0F4F8] p-6 rounded-2xl">
                <div>
                  <p className="text-xs text-gray-500 font-semibold">PRICE PER NIGHT</p>
                  <span className="text-3xl font-bold text-[#E5C07B]">
                    ₹ {activeRoom.price_per_night}
                  </span>
                </div>

                <button
                  onClick={() => {
                    handleBookNow(activeRoom.room_type_id);
                    setShowModal(false);
                  }}
                  className="bg-[#0F172A] text-white px-8 py-4 rounded-full font-semibold hover:bg-[#1E293B] hover:shadow-lg transition-all duration-300 text-lg"
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
