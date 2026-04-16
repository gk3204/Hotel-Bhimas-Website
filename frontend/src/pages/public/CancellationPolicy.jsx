import React from "react";

const CancellationPolicy = () => {
  return (
    <div className="min-h-screen bg-[#F8FAFC] px-6 py-20">
      <div className="max-w-4xl mx-auto bg-white rounded-2xl shadow-lg p-8 md:p-12">
        <h1 className="text-4xl font-bold text-[#0F172A] mb-8 text-center">
          Cancellation Policy
        </h1>
        <p className="text-center text-gray-600 mb-8 text-sm">Last Updated: April 2026</p>

        <div className="space-y-8 text-gray-700">
          <section className="bg-green-50 border-l-4 border-green-500 p-4">
            <h2 className="text-2xl font-semibold text-green-800 mb-3">✅ Free Cancellation</h2>
            <ul className="list-disc list-inside space-y-2 ml-2">
              <li><strong>Cancel up to 24 hours before your check-in date for a full refund.</strong></li>
              <li>Full booking amount will be refunded to your original payment method.</li>
              <li>No questions asked - completely hassle-free cancellation.</li>
              <li>Refund processing takes 5-7 working days.</li>
            </ul>
          </section>

          <section className="bg-red-50 border-l-4 border-red-500 p-4">
            <h2 className="text-2xl font-semibold text-red-800 mb-3">❌ Non-Refundable Cancellations</h2>
            <ul className="list-disc list-inside space-y-2 ml-2">
              <li><strong>Cancellations within 24 hours of check-in are non-refundable.</strong></li>
              <li>The full booking amount will be charged to your account.</li>
              <li>No exceptions or partial refunds are provided for late cancellations.</li>
              <li>This applies regardless of the reason for cancellation.</li>
            </ul>
          </section>

          <section>
            <h2 className="text-2xl font-semibold text-[#0F172A] mb-4">How to Cancel Your Booking</h2>
            <ul className="list-disc list-inside space-y-2 ml-2">
              <li><strong>Online:</strong> Visit our <strong>Contact Us</strong> page and fill out the enquiry form with your cancellation request and booking details.</li>
              <li><strong>By Phone:</strong> Call us at <strong>+919347172758</strong> (Available 24 hours)</li>
              <li><strong>By Email:</strong> Send cancellation request to <strong>hotelbhimas@gmail.com</strong></li>
              <li>Your cancellation is confirmed once you receive an email confirmation from us.</li>
              <li>Keep your confirmation email as proof for refund tracking.</li>
            </ul>
          </section>

          <section>
            <h2 className="text-2xl font-semibold text-[#0F172A] mb-4">No-Show Policy</h2>
            <ul className="list-disc list-inside space-y-2 ml-2">
              <li>If you don't check in on your booking date without prior cancellation, <strong>the full booking amount will be charged.</strong></li>
              <li>No refunds are provided for no-shows.</li>
              <li>To avoid being marked as no-show, you must cancel your booking or contact us before your check-in date.</li>
              <li>No-shows may affect your ability to book with us in the future.</li>
            </ul>
          </section>

          <section>
            <h2 className="text-2xl font-semibold text-[#0F172A] mb-4">Early Checkout Charges</h2>
            <ul className="list-disc list-inside space-y-2 ml-2">
              <li>Cancellation charges apply to early checkouts within 24 hours of your booking confirmation.</li>
              <li>Early checkouts after this period are charged for the full booked duration.</li>
              <li>No refunds will be issued for early checkouts where the customer chooses to leave before check-out time.</li>
            </ul>
          </section>

          <section>
            <h2 className="text-2xl font-semibold text-[#0F172A] mb-4">Special Circumstances - No Refund</h2>
            <ul className="list-disc list-inside space-y-2 ml-2">
              <li>❌ Bookings for guests with invalid or missing government-issued ID</li>
              <li>❌ Cancellations due to guest violations of hotel policies</li>
              <li>❌ Claims arising from guest misuse of rooms or facilities</li>
              <li>❌ Refusal to check-in due to hotel's safety or security concerns</li>
              <li>❌ Damaged rooms or facilities caused by the guest</li>
            </ul>
          </section>

          <section>
            <h2 className="text-2xl font-semibold text-[#0F172A] mb-4">Modification of Bookings</h2>
            <ul className="list-disc list-inside space-y-2 ml-2">
              <li>If you need to change your dates, you can modify your booking up to 24 hours before check-in.</li>
              <li>Modifications are subject to availability and price changes.</li>
              <li>Price difference (if any) will be charged or refunded based on new dates.</li>
              <li>Contact us at <strong>support@hotelbhimas.com</strong> to request modifications.</li>
            </ul>
          </section>

          <section>
            <h2 className="text-2xl font-semibold text-[#0F172A] mb-4">Cancellation Timeline Reference</h2>
            <div className="overflow-x-auto">
              <table className="w-full border-collapse border border-gray-300">
                <thead className="bg-[#E5C07B] text-white">
                  <tr>
                    <th className="border p-3 text-left">Time Before Check-in</th>
                    <th className="border p-3 text-left">Refund Status</th>
                    <th className="border p-3 text-left">Amount Refunded</th>
                  </tr>
                </thead>
                <tbody>
                  <tr className="bg-green-50">
                    <td className="border p-3">More than 24 hours</td>
                    <td className="border p-3 font-semibold text-green-700">✅ REFUNDABLE</td>
                    <td className="border p-3">100% (minus convenience fee)</td>
                  </tr>
                  <tr className="bg-red-50">
                    <td className="border p-3">Within 24 hours</td>
                    <td className="border p-3 font-semibold text-red-700">❌ NON-REFUNDABLE</td>
                    <td className="border p-3">0% - Full amount charged</td>
                  </tr>
                  <tr className="bg-red-50">
                    <td className="border p-3">No-Show (Did not cancel)</td>
                    <td className="border p-3 font-semibold text-red-700">❌ CHARGED</td>
                    <td className="border p-3">100% - Full amount charged</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </section>

          <div className="bg-blue-50 border-l-4 border-blue-400 p-4 mt-8">
            <p className="text-sm font-semibold text-blue-800 mb-2">
              💡 <strong>Pro Tips:</strong>
            </p>
            <ul className="list-disc list-inside space-y-1 text-sm text-blue-800">
              <li>Note your check-in date and time before confirming the booking</li>
              <li>Set a reminder to cancel before the 24-hour deadline if your plans change</li>
              <li>Keep your booking confirmation email safe</li>
              <li>Cancellations are free and instant - no hidden charges!</li>
            </ul>
          </div>

          <div className="bg-yellow-50 border-l-4 border-yellow-400 p-4 mt-6">
            <p className="text-sm font-semibold text-yellow-800">
              📞 <strong>Need Help?</strong> Contact us at <strong>hotelbhimas@gmail.com</strong> or call <strong>+919347172758</strong> (24 hours).
            </p>
          </div>
        </div>
      </div>
    </div>
  );
};

export default CancellationPolicy;
