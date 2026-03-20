import React from "react";

const Policies = () => {
  return (
    <div className="min-h-screen bg-[#F8FAFC] px-6 py-20">
      <div className="max-w-4xl mx-auto bg-white rounded-3xl shadow-xl p-8 md:p-14">

        <h1 className="text-3xl md:text-4xl font-bold text-[#0F172A] mb-10 text-center">
          Hotel Policies
        </h1>

        {/* ================= CHECK-IN / CHECK-OUT ================= */}
        <section className="mb-10">
          <h2 className="text-2xl font-semibold mb-3">Check-In & Check-Out</h2>
          <ul className="list-disc list-inside space-y-2 text-gray-700">
            <li>
              <strong>24-hour</strong> check-in and check-out is available. Stay duration is calculated from
              the time of check-in.
            </li>
            <li>Early check-in or late check-out is subject to availability and additional charges.</li>
          </ul>
        </section>

        {/* ================= PAYMENT POLICY ================= */}
        <section className="mb-10">
          <h2 className="text-2xl font-semibold mb-3">Payment Policy</h2>
          <ul className="list-disc list-inside space-y-2 text-gray-700">
            <li>Payment may be required to confirm the booking.</li>
            <li>Accepted payment modes: UPI, Debit/Credit Cards, Net Banking, and Cash.</li>
            <li>Room tariffs are subject to applicable government taxes.</li>
          </ul>
        </section>

        {/* ================= CANCELLATION & REFUND ================= */}
        <section className="mb-10">
          <h2 className="text-2xl font-semibold mb-3">Cancellation & Refund Policy</h2>
          <ul className="list-disc list-inside space-y-2 text-gray-700">
            <li>Free cancellation up to <strong>24 hours</strong> before check-in.</li>
            <li>Cancellations within 24 hours of check-in are non-refundable.</li>
            <li>No-show bookings will be charged for one night.</li>
            <li>Refunds (if applicable) will be processed within <strong>5–7 working days</strong>.</li>
          </ul>
        </section>

        {/* ================= ID & AGE POLICY ================= */}
        <section className="mb-10">
          <h2 className="text-2xl font-semibold mb-3">ID Proof & Age Policy</h2>
          <ul className="list-disc list-inside space-y-2 text-gray-700">
            <li>Valid government-issued photo ID is mandatory for all guests.</li>
            <li>Accepted IDs: Aadhaar Card, Passport, Driving License, Voter ID.</li>
            <li>Foreign guests must present Passport and valid Visa.</li>
            <li>Guests below <strong>18 years</strong> are not allowed to check in alone.</li>
            <li>Local ID bookings are accepted at management discretion.</li>
          </ul>
        </section>

        {/* ================= ROOM OCCUPANCY ================= */}
        <section className="mb-10">
          <h2 className="text-2xl font-semibold mb-3">Room Occupancy Policy</h2>
          <ul className="list-disc list-inside space-y-2 text-gray-700">
            <li>Maximum occupancy per room must be strictly followed.</li>
            <li>Extra guests beyond room capacity will be charged.</li>
          </ul>
        </section>

        {/* ================= GUEST & VISITOR POLICY ================= */}
        <section className="mb-10">
          <h2 className="text-2xl font-semibold mb-3">Guest & Visitor Policy</h2>
          <ul className="list-disc list-inside space-y-2 text-gray-700">
            <li>Only registered guests are allowed inside rooms.</li>
            <li>Visitors are permitted only in lobby and reception areas.</li>
            <li>Visitors must leave the premises by <strong>9:00 PM</strong>.</li>
          </ul>
        </section>

        {/* ================= PARKING POLICY ================= */}
        <section className="mb-10">
          <h2 className="text-2xl font-semibold mb-3">Parking Policy</h2>
          <ul className="list-disc list-inside space-y-2 text-gray-700">
            <li>Free parking is available for hotel guests.</li>
            <li>Parking is subject to availability on a first-come basis.</li>
            <li>Parking space is available for cars and two-wheelers.</li>
            <li>The hotel is not responsible for valuables left inside vehicles.</li>
          </ul>
        </section>

        {/* ================= FOOD POLICY ================= */}
        <section className="mb-10">
          <h2 className="text-2xl font-semibold mb-3">Food & Restaurant Policy</h2>
          <ul className="list-disc list-inside space-y-2 text-gray-700">
            <li>Hotel Bhimas has an in-house <strong>pure vegetarian restaurant</strong>.</li>
            <li>Both South Indian and North Indian dishes are served.</li>
            <li>Outside food is not permitted inside the hotel premises.</li>
            <li>Room service is available during restaurant operating hours.</li>
          </ul>
        </section>

        {/* ================= HOUSEKEEPING ================= */}
        <section className="mb-10">
          <h2 className="text-2xl font-semibold mb-3">Housekeeping Policy</h2>
          <ul className="list-disc list-inside space-y-2 text-gray-700">
            <li>Housekeeping services are provided between <strong>9:00 AM – 4:00 PM</strong>.</li>
            <li>Linen and towels are changed once every two days or on request.</li>
            <li>Guests are advised to secure valuables properly.</li>
          </ul>
        </section>

        {/* ================= DAMAGE & LOSS ================= */}
        <section className="mb-10">
          <h2 className="text-2xl font-semibold mb-3">Damage & Loss Policy</h2>
          <ul className="list-disc list-inside space-y-2 text-gray-700">
            <li>Any damage to hotel property will be charged to the guest.</li>
            <li>The hotel is not responsible for loss of personal belongings.</li>
            <li>Please contact reception immediately for assistance.</li>
          </ul>
        </section>

        {/* ================= BEHAVIOUR & SAFETY ================= */}
        <section>
          <h2 className="text-2xl font-semibold mb-3">Behaviour, Safety & Hygiene</h2>
          <ul className="list-disc list-inside space-y-2 text-gray-700">
            <li>Smoking and alcohol consumption are strictly prohibited.</li>
            <li>Loud music and disturbances are not allowed.</li>
            <li>Quiet hours: <strong>10:00 PM – 6:00 AM</strong>.</li>
            <li>Cooking inside rooms is not permitted.</li>
            <li>Pets are not allowed.</li>
          </ul>
        </section>

      </div>
    </div>
  );
};

export default Policies;
