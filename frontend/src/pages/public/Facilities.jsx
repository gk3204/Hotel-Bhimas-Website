import React, { useEffect, useState } from "react";

// HERO SLIDESHOW IMAGES
import hero1 from "./images/rooms/double-deluxe.png";
import hero2 from "./images/ac-restaurant.png";
import hero3 from "./images/rooms/5-bed.png";

const heroImages = [hero1, hero2, hero3];

const Facilities = () => {
  const [current, setCurrent] = useState(0);

  // Auto slideshow
  useEffect(() => {
    const interval = setInterval(() => {
      setCurrent((prev) => (prev + 1) % heroImages.length);
    }, 2000);
    return () => clearInterval(interval);
  }, []);

  return (
    <div className="min-h-screen bg-[#F8FAFC] overflow-x-hidden">

      {/* ================= HERO SECTION ================= */}
      <section className="relative w-full h-[70vh] pt-28 overflow-hidden">
        {heroImages.map((img, index) => (
          <div
            key={index}
            className={`absolute inset-0 transition-opacity duration-700 ${
              index === current ? "opacity-100" : "opacity-0"
            }`}
            style={{
              backgroundImage: `linear-gradient(rgba(15,23,42,0.55), rgba(15,23,42,0.55)), url(${img})`,
              backgroundSize: "cover",
              backgroundPosition: "center",
            }}
          />
        ))}

        <div className="relative z-10 flex items-center justify-center h-full text-center px-6">
          <div>
            <h1 className="text-4xl md:text-5xl font-bold text-white mb-4">
              Our Facilities
            </h1>
            <p className="text-white/80 max-w-3xl mx-auto text-lg">
              Comfort, convenience, and hygiene — thoughtfully provided for pilgrims and families.
            </p>
          </div>
        </div>
      </section>

      {/* ================= FACILITIES GRID ================= */}
      <section className="max-w-7xl mx-auto mt-20 px-6">
        <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-10">
          <FacilityCard
            icon="⏰"
            title="24×7 Check-In & Check-Out"
            desc="Flexible check-in and check-out available round the clock, ideal for pilgrims arriving at any time."
          />
          {/* RESTAURANT */}
          <FacilityCard
            icon="🍽️"
            title="Pure Vegetarian Restaurant"
            desc="Serving delicious South Indian, North Indian, and Chinese cuisine prepared in a hygienic and traditional style."
          />

          {/* SWEETS & SAVORIES (SEPARATE CARD) */}
          <FacilityCard
            icon="🍡"
            title="Sweets & Savories"
            desc="Wide variety of freshly prepared traditional sweets and savories, perfect for pilgrims and families."
          />

          <FacilityCard
            icon="🚗"
            title="Spacious Car Parking"
            desc="Safe and convenient parking facility for cars and buses."
          />

          <FacilityCard
            icon="❄️"
            title="AC & Non-AC Rooms"
            desc="Well-maintained rooms with modern amenities for a peaceful stay."
          />

          <FacilityCard
            icon="🛎️"
            title="24×7 Front Desk"
            desc="Round-the-clock assistance for check-in, check-out, and guest support."
          />

          <FacilityCard
            icon="🚿"
            title="Hot Water Facility"
            desc="24-hour hot water supply available in all rooms."
          />

          <FacilityCard
            icon="🧹"
            title="Room Service"
            desc="Daily housekeeping and prompt room service for your comfort."
          />

          <FacilityCard
            icon="👨‍👩‍👧‍👦"
            title="Family-Friendly Rooms"
            desc="Spacious and comfortable rooms ideal for families and group stays."
          />

          <FacilityCard
            icon="🌿"
            title="Peaceful & Clean Environment"
            desc="Calm surroundings ensuring relaxation and a pleasant stay."
          />

          <FacilityCard
            icon="🛏️🍽️"
            title="In-Room Dining"
            desc="Enjoy meals from our pure vegetarian restaurant in the comfort of your room during restaurant operating hours."
          />

          <FacilityCard
            icon="🛗"
            title="Lift / Elevator Service"
            desc="Lift facility available for easy access to all floors, especially convenient for senior citizens and families."
          />


        </div>
      </section>

      {/* ================= CTA ================= */}
      <section className="mt-24 mb-24 px-6 text-center">
        <h2 className="text-3xl font-bold text-[#0F172A] mb-6">
          Comfortable Stay Awaits You
        </h2>
        <p className="text-gray-600 mb-8">
          Book your stay at Hotel Bhimas and enjoy comfort in Tirupati.
        </p>

        <a
          href="/booking"
          className="inline-block bg-[#E5C07B] text-[#0F172A] px-10 py-4 rounded-full font-semibold hover:bg-[#FCD34D] transition"
        >
          Book Your Stay
        </a>
      </section>

    </div>
  );
};

/* ================= FACILITY CARD COMPONENT ================= */
const FacilityCard = ({ icon, title, desc }) => (
  <div className="bg-white rounded-2xl shadow-md p-8 text-center hover:shadow-xl transition transform hover:-translate-y-2">
    <div className="text-5xl mb-5">{icon}</div>
    <h3 className="text-xl font-bold text-[#0F172A] mb-3">{title}</h3>
    <p className="text-gray-600 text-sm leading-relaxed">{desc}</p>
  </div>
);

export default Facilities;
