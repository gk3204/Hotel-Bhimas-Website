import React from "react";
import { useNavigate, useParams } from "react-router-dom";

const PaymentSuccess = () => {
  const navigate = useNavigate();
  const { bookingId } = useParams();

  return (
    <div className="min-h-screen flex items-center justify-center bg-green-50 px-6">
      <div className="bg-white shadow-xl rounded-3xl p-12 max-w-lg w-full text-center">
        
        <div className="text-green-600 text-6xl mb-6">✓</div>

        <h1 className="text-3xl font-bold mb-4 text-gray-800">
          Payment Successful!
        </h1>

        <p className="text-gray-600 mb-4">
          Your booking has been confirmed.
        </p>

        {bookingId && (
          <p className="text-sm text-gray-500 mb-4">
            Booking ID: <span className="font-semibold">{bookingId}</span>
          </p>
        )}

        {/* ✅ Email Notification Message */}
        <div className="bg-green-100 text-green-700 p-4 rounded-xl mb-6 text-sm">
          You will receive an email confirmation shortly with your booking details.
        </div>

        <button
          onClick={() => navigate("/")}
          className="bg-green-600 text-white py-3 px-6 rounded-full hover:bg-green-700 transition"
        >
          Back to Home
        </button>

      </div>
    </div>
  );
};

export default PaymentSuccess;