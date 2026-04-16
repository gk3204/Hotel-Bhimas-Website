import React from "react";

const RefundPolicy = () => {
  return (
    <div className="min-h-screen bg-[#F8FAFC] px-6 py-20">
      <div className="max-w-4xl mx-auto bg-white rounded-2xl shadow-lg p-8 md:p-12">
        <h1 className="text-4xl font-bold text-[#0F172A] mb-8 text-center">
          Refund Policy
        </h1>
        <p className="text-center text-gray-600 mb-8 text-sm">Last Updated: April 2026</p>

        <div className="space-y-8 text-gray-700">
          <section>
            <h2 className="text-2xl font-semibold text-[#0F172A] mb-4">1. Refund Eligibility</h2>
            <ul className="list-disc list-inside space-y-2 ml-2">
              <li>Refunds are processed only for cancelled bookings as per the Cancellation Policy.</li>
              <li>No refunds are provided for no-shows or parties with invalid/missing identification.</li>
              <li>No refunds for disputes regarding room quality or amenities after check-in.</li>
              <li>Validity of refund claim must be within 30 days of the booking date.</li>
            </ul>
          </section>

          <section>
            <h2 className="text-2xl font-semibold text-[#0F172A] mb-4">2. Refund Timeline</h2>
            <ul className="list-disc list-inside space-y-2 ml-2">
              <li>Refunds are processed within <strong>5-7 working days</strong> from the cancellation date.</li>
              <li>Refunds are credited to the original payment method used.</li>
              <li>If you don't receive your refund within 7 days, contact us immediately at <strong>hotelbhimas@gmail.com</strong></li>
              <li>Please allow up to 10 business days for your bank to reflect the refund.</li>
            </ul>
          </section>

          <section>
            <h2 className="text-2xl font-semibold text-[#0F172A] mb-4">3. Refund Amount</h2>
            <ul className="list-disc list-inside space-y-2 ml-2">
              <li>The refund amount depends on the cancellation policy applicable to your booking.</li>
              <li>Convenience fees paid at booking are <strong>non-refundable</strong> in most cases.</li>
              <li>GST will be refunded if the entire booking is cancelled and refund is eligible.</li>
            </ul>
          </section>

          <section>
            <h2 className="text-2xl font-semibold text-[#0F172A] mb-4">4. Payment Method Issues</h2>
            <ul className="list-disc list-inside space-y-2 ml-2">
              <li>If your payment method is closed or invalid, contact your bank to resolve the issue.</li>
              <li>Refunds may take longer if your bank has processing delays.</li>
              <li>The hotel is not responsible for delays caused by your financial institution.</li>
              <li>International cards may take 14-21 days to reflect refunds.</li>
            </ul>
          </section>

          <section>
            <h2 className="text-2xl font-semibold text-[#0F172A] mb-4">5. Disputes & Adjustments</h2>
            <ul className="list-disc list-inside space-y-2 ml-2">
              <li>If you dispute a charge, contact us within 30 days of the transaction.</li>
              <li>We will investigate and respond within 10 working days.</li>
              <li>Charges for damages, violations, or policy breaches will <strong>not</strong> be refunded.</li>
              <li>No refunds for bookings cancelled due to guest's change of plans without justification.</li>
            </ul>
          </section>

          <section>
            <h2 className="text-2xl font-semibold text-[#0F172A] mb-4">6. Partial Refunds</h2>
            <ul className="list-disc list-inside space-y-2 ml-2">
              <li>Partial refunds may be issued for cancellations within the prescribed refund window.</li>
              <li>Deductions may be made for actual costs incurred by the hotel.</li>
              <li>Amount details will be clearly communicated in cancellation confirmation email.</li>
            </ul>
          </section>

          <section>
            <h2 className="text-2xl font-semibold text-[#0F172A] mb-4">7. How to Request a Refund</h2>
            <ul className="list-disc list-inside space-y-2 ml-2">
              <li>Visit our <strong>Contact Us</strong> page and fill out the enquiry form with your refund request details.</li>
              <li>Or email us at <strong>hotelbhimas@gmail.com</strong> with your booking reference and reason for refund request.</li>
              <li>Our team will review your request and send you a confirmation and refund status updates via email.</li>
            </ul>
          </section>

          <div className="bg-yellow-50 border-l-4 border-yellow-400 p-4 mt-8">
            <p className="text-sm font-semibold text-yellow-800">
              ⚠️ <strong>Important:</strong> Check your Cancellation Policy for the refund amount applicable to your booking. Not all cancellations are fully refundable. Contact us for clarification.
            </p>
          </div>
        </div>
      </div>
    </div>
  );
};

export default RefundPolicy;
