import React from "react";
import { useNavigate, useParams } from "react-router-dom";
import { apiRequest } from "../../api/api";

const PaymentFailed = () => {
  const navigate = useNavigate();
  const { bookingId } = useParams();

  const handleRetry = async () => {
    try {
      // 🔹 Retry endpoint
      const data = await apiRequest(`/payments/retry/${bookingId}`, {
        method: "POST",
      });

      const options = {
        key: data.key,
        amount: data.amount,
        currency: data.currency,
        order_id: data.order_id,
        name: "Hotel Bhimas",
        description: "Room Booking Payment",
        image: "https://raw.githubusercontent.com/Gk200432/Hotel-Bhimas-Website/refs/heads/main/logo-gold.svg",
        

        handler: async function (response) {
          try {
            await apiRequest("/payments/verify", {
              method: "POST",
              body: JSON.stringify({
                razorpay_order_id: response.razorpay_order_id,
                razorpay_payment_id: response.razorpay_payment_id,
                razorpay_signature: response.razorpay_signature,
                status: "success",
              }),
            });

            navigate(`/payment-success/${bookingId}`);
          } catch (error) {
            navigate(`/payment-failed/${bookingId}`);
          }
        },

        modal: {
          ondismiss: async function () {
            try {
              await apiRequest("/payments/verify", {
                method: "POST",
                body: JSON.stringify({
                  razorpay_order_id: data.order_id,
                  status: "failed",
                }),
              });
            } catch (err) {
              console.error("Dismiss verify failed:", err);
            }

            navigate(`/payment-failed/${bookingId}`);
          },
        },

        theme: {
          color: "#D4AF37",
        },
      };

      const rzp = new window.Razorpay(options);
      rzp.open();

    } catch (error) {
      console.error("Retry failed:", error);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-red-50 px-6">
      <div className="bg-white shadow-xl rounded-3xl p-10 max-w-md w-full text-center">

        <div className="text-red-600 text-6xl mb-6">✕</div>

        <h1 className="text-3xl font-bold mb-4 text-gray-800">
          Payment Failed
        </h1>

        <p className="text-gray-600 mb-6">
          Your booking is still reserved. Please retry payment to confirm it.
        </p>

        {/* 💳 Refund Information */}
        <div className="bg-blue-50 border border-blue-200 rounded-xl p-4 mb-6 text-sm">
          <p className="text-blue-700 font-semibold mb-2">💳 Refund Information</p>
          <p className="text-blue-600 text-xs">
            If any amount has been deducted from your account, it will be automatically returned to your original payment method within <span className="font-semibold">5-7 working days</span>.
          </p>
        </div>

        <div className="flex flex-col gap-4 mt-6">
          <button
            onClick={handleRetry}
            className="bg-red-600 text-white py-3 rounded-full hover:bg-red-700 transition"
          >
            Retry Payment
          </button>

          <button
            onClick={() => navigate("/")}
            className="border border-red-600 text-red-600 py-3 rounded-full hover:bg-red-100 transition"
          >
            Back to Home
          </button>
        </div>

      </div>
    </div>
  );
};

export default PaymentFailed;