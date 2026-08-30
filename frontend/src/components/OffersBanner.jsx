import React, { useEffect, useState } from "react";
import { getActivePromotions } from "../api/promotions";

// "Current offers" strip for the public site (Home + Rooms). Reads the same /promotions/active feed
// the Booking page uses; the backend stays authoritative on the actual discounted price.
const offerLabel = (p) =>
  p.discount_type === "percent"
    ? `${Math.round(p.discount_value)}% OFF`
    : `₹${Math.round(p.discount_value)} OFF`;

const todayISO = () => new Date().toISOString().slice(0, 10);

// Honour the promo's date window (matches the booking backend + Booking.jsx preview).
const liveNow = (p) => {
  const d = todayISO();
  if (p.valid_from && d < p.valid_from) return false;
  if (p.valid_to && d > p.valid_to) return false;
  return true;
};

export default function OffersBanner({ className = "" }) {
  const [promos, setPromos] = useState([]);

  useEffect(() => {
    getActivePromotions()
      .then((data) => setPromos((data || []).filter(liveNow)))
      .catch(() => setPromos([]));
  }, []);

  if (promos.length === 0) return null;

  return (
    <section className={`max-w-6xl mx-auto mb-12 ${className}`}>
      <div className="bg-gradient-to-r from-[#FFF8E7] to-[#FDF2D6] border border-[#E5C07B]/50 rounded-3xl shadow-lg p-6 md:p-8">
        <div className="flex items-center gap-3 mb-5">
          <span className="text-2xl">🏷️</span>
          <h2 className="text-2xl font-bold text-[#0F172A]">Current offers</h2>
        </div>
        <div className="flex flex-wrap gap-4">
          {promos.map((p) => (
            <div key={p.promotion_id}
                 className="bg-white rounded-2xl border border-[#E5C07B]/40 shadow-sm px-5 py-4 min-w-[220px]">
              <div className="flex items-center gap-3">
                <span className="bg-[#E5C07B] text-[#0F172A] font-extrabold px-3 py-1 rounded-full text-sm whitespace-nowrap">
                  {offerLabel(p)}
                </span>
                <span className="font-semibold text-[#0F172A]">{p.name}</span>
              </div>
              <div className="mt-2 text-xs text-gray-500">
                {p.room_type_id ? "On a selected room type" : "On all rooms"}
                {(p.valid_from || p.valid_to) && (
                  <> · valid {p.valid_from || "now"} → {p.valid_to || "ongoing"}</>
                )}
              </div>
            </div>
          ))}
        </div>
        <p className="mt-4 text-sm text-gray-600">Book direct to enjoy these rates.</p>
      </div>
    </section>
  );
}
