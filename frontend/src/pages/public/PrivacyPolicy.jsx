import React from "react";

const PrivacyPolicy = () => {
  return (
    <div className="min-h-screen bg-[#F8FAFC] px-6 py-20">
      <div className="max-w-4xl mx-auto bg-white rounded-2xl shadow-lg p-8 md:p-12">
        <h1 className="text-4xl font-bold text-[#0F172A] mb-8 text-center">
          Privacy Policy
        </h1>
        <p className="text-center text-gray-600 mb-8 text-sm">Last Updated: April 2026</p>

        <div className="space-y-8 text-gray-700">
          <section>
            <h2 className="text-2xl font-semibold text-[#0F172A] mb-4">1. Information We Collect</h2>
            <ul className="list-disc list-inside space-y-2 ml-2">
              <li><strong>Personal Information:</strong> Name, email, phone number, address</li>
              <li><strong>Payment Information:</strong> Credit/debit card details processed securely through Razorpay</li>
              <li><strong>Device Information:</strong> IP address, browser type, pages visited</li>
              <li><strong>Identification:</strong> Government-issued ID details at check-in</li>
            </ul>
          </section>

          <section>
            <h2 className="text-2xl font-semibold text-[#0F172A] mb-4">2. How We Use Your Information</h2>
            <ul className="list-disc list-inside space-y-2 ml-2">
              <li>Process and confirm your hotel booking</li>
              <li>Send booking confirmation and payment receipts</li>
              <li>Provide customer support and handle complaints</li>
              <li>Improve our website and services</li>
              <li>Comply with legal and regulatory requirements</li>
            </ul>
          </section>

          <section>
            <h2 className="text-2xl font-semibold text-[#0F172A] mb-4">3. Data Security</h2>
            <ul className="list-disc list-inside space-y-2 ml-2">
              <li>All payment information is encrypted using SSL technology and processed via Razorpay's secure gateway.</li>
              <li>We do not store complete credit/debit card details on our servers.</li>
              <li>Your data is protected from unauthorized access using industry-standard security measures.</li>
            </ul>
          </section>

          <section>
            <h2 className="text-2xl font-semibold text-[#0F172A] mb-4">4. Third-Party Sharing</h2>
            <ul className="list-disc list-inside space-y-2 ml-2">
              <li>We do not sell or rent your personal information to third parties.</li>
              <li>We may share information with payment processors (Razorpay) for transaction processing.</li>
              <li>Information may be shared with law enforcement if required by law.</li>
            </ul>
          </section>

          <section>
            <h2 className="text-2xl font-semibold text-[#0F172A] mb-4">5. Cookies</h2>
            <ul className="list-disc list-inside space-y-2 ml-2">
              <li>We use cookies to enhance your browsing experience and analyze website traffic.</li>
              <li>You can disable cookies in your browser settings.</li>
              <li>Essential cookies are necessary for the website to function properly.</li>
            </ul>
          </section>

          <section>
            <h2 className="text-2xl font-semibold text-[#0F172A] mb-4">6. Data Retention</h2>
            <ul className="list-disc list-inside space-y-2 ml-2">
              <li>We retain your personal data as long as your account is active or as needed for legal compliance.</li>
              <li>You can request deletion of your data at any time (subject to legal obligations).</li>
              <li>Booking records are retained for accounting and legal purposes.</li>
            </ul>
          </section>

          <section>
            <h2 className="text-2xl font-semibold text-[#0F172A] mb-4">7. Changes to Privacy Policy</h2>
            <ul className="list-disc list-inside space-y-2 ml-2">
              <li>We may update this privacy policy from time to time.</li>
              <li>Changes will be posted on this page with the updated date.</li>
              <li>Your continued use of our website indicates your acceptance of the updated policy.</li>
            </ul>
          </section>

          <div className="bg-green-50 border-l-4 border-green-400 p-4 mt-8">
            <p className="text-sm font-semibold text-green-800">
              🔒 We take your privacy seriously. For any concerns, contact us at <strong>hotelbhimas@gmail.com</strong>
            </p>
          </div>
        </div>
      </div>
    </div>
  );
};

export default PrivacyPolicy;
