import React from "react";

const TermsAndConditions = () => {
  return (
    <div className="min-h-screen bg-[#F8FAFC] px-6 py-20">
      <div className="max-w-4xl mx-auto bg-white rounded-2xl shadow-lg p-8 md:p-12">
        <h1 className="text-4xl font-bold text-[#0F172A] mb-8 text-center">
          Terms and Conditions
        </h1>
        <p className="text-center text-gray-600 mb-8 text-sm">Last Updated: April 2026</p>

        <div className="space-y-8 text-gray-700">
          <section>
            <h2 className="text-2xl font-semibold text-[#0F172A] mb-4">1. Booking & Reservation</h2>
            <ul className="list-disc list-inside space-y-2 ml-2">
              <li>All bookings made through our website are subject to these terms and conditions.</li>
              <li>A valid email and phone number are required to complete the booking.</li>
              <li>By making a booking, you confirm that you are at least 18 years old.</li>
              <li>The booking is confirmed only after payment is received in full.</li>
            </ul>
          </section>

          <section>
            <h2 className="text-2xl font-semibold text-[#0F172A] mb-4">2. Check-In & Check-Out</h2>
            <ul className="list-disc list-inside space-y-2 ml-2">
              <li><strong>24-Hour Check-in and Check-out:</strong> You can check in and check out at any time during your stay.</li>
              <li>Early check-in or late check-out is subject to availability and may incur additional charges.</li>
              <li>Guests are liable for damages caused during their stay.</li>
              <li>ID verification is mandatory at check-in (Government-issued photo ID required).</li>
            </ul>
          </section>

          <section>
            <h2 className="text-2xl font-semibold text-[#0F172A] mb-4">3. Guest Conduct</h2>
            <ul className="list-disc list-inside space-y-2 ml-2">
              <li>Smoking is strictly prohibited in rooms.</li>
              <li>Alcohol consumption within hotel premises is strictly forbidden.</li>
              <li>Loud music, disturbances, or noise is not permitted after 10:00 PM.</li>
              <li>Outside guests/visitors are not allowed in rooms.</li>
              <li>Cooking in rooms is prohibited.</li>
              <li>Pets are not allowed inside the hotel.</li>
              <li>The hotel reserves the right to deny service or ask guests to vacate without refund for violations.</li>
            </ul>
          </section>

          <section>
            <h2 className="text-2xl font-semibold text-[#0F172A] mb-4">4. Room Occupancy</h2>
            <ul className="list-disc list-inside space-y-2 ml-2">
              <li>Maximum occupancy limits must be strictly followed per room type.</li>
              <li>Additional guests beyond capacity will incur extra charges.</li>
            </ul>
          </section>

          <section>
            <h2 className="text-2xl font-semibold text-[#0F172A] mb-4">5. Liability</h2>
            <ul className="list-disc list-inside space-y-2 ml-2">
              <li>The hotel is not responsible for loss, theft, or damage to personal belongings.</li>
              <li>Any damage to hotel property will be charged to the guest at replacement cost.</li>
              <li>The hotel is not liable for external events or circumstances beyond its control.</li>
            </ul>
          </section>

          <section>
            <h2 className="text-2xl font-semibold text-[#0F172A] mb-4">6. Payment Terms</h2>
            <ul className="list-disc list-inside space-y-2 ml-2">
              <li>Payment must be made in full at the time of booking.</li>
              <li>Accepted payment modes: Credit/Debit Cards, UPI, Net Banking via Razorpay.</li>
              <li>All prices include applicable government taxes.</li>
              <li>Convenience fee is non-refundable.</li>
            </ul>
          </section>

          <section>
            <h2 className="text-2xl font-semibold text-[#0F172A] mb-4">7. Changes to These Terms</h2>
            <ul className="list-disc list-inside space-y-2 ml-2">
              <li>We reserve the right to update these terms at any time.</li>
              <li>Changes will be posted on this page with the updated date.</li>
              <li>Your continued use of the website constitutes acceptance of updated terms.</li>
            </ul>
          </section>

          <div className="bg-blue-50 border-l-4 border-blue-400 p-4 mt-8">
            <p className="text-sm font-semibold text-blue-800">
              📞 For any questions regarding these terms, please contact us at <strong>hotelbhimas@gmail.com</strong> or call <strong>+919347172758</strong>
            </p>
          </div>
        </div>
      </div>
    </div>
  );
};

export default TermsAndConditions;
