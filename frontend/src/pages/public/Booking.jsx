import React, { useEffect, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { apiRequest } from "../../api/api";

const Booking = () => {
  const { roomTypeId } = useParams();
  const navigate = useNavigate();

  const [room, setRoom] = useState(null);
  const [priceBreakdown, setPriceBreakdown] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const [formData, setFormData] = useState({
    guest_name: "",
    phone: "",
    email: "",
    check_in: "",
    check_out: "",
  });

  /* ================= FETCH ROOM ================= */

  useEffect(() => {
    apiRequest("/room-types/")
      .then((data) => {
        const selected = data.find(
          (r) => r.room_type_id === parseInt(roomTypeId)
        );
        setRoom(selected);
      })
      .catch((err) => console.error(err));
  }, [roomTypeId]);

  /* ================= HANDLE INPUT ================= */

  const handleChange = (e) => {
    setFormData({
      ...formData,
      [e.target.name]: e.target.value,
    });
  };

  const calculateNights = () => {
    if (!formData.check_in || !formData.check_out) return 0;

    const checkIn = new Date(formData.check_in);
    const checkOut = new Date(formData.check_out);

    const diffTime = checkOut - checkIn;
    const diffDays = diffTime / (1000 * 60 * 60 * 24);

    return diffDays > 0 ? diffDays : 0;
  };

  const nights = calculateNights();
  const pricePerNight = room?.price_per_night || 0;

  const roomBaseTotal = nights * pricePerNight;
  const roomGST = roomBaseTotal * 0.05; // assuming 12% GST
  const roomTotalWithGST = roomBaseTotal + roomGST;
  /* ================= HANDLE BOOKING ================= */

  const handleBooking = async (e) => {
    e.preventDefault();

    try {
      setLoading(true);

      // 1️⃣ Create booking
      const bookingData = await apiRequest("/bookings/", {
        method: "POST",
        body: JSON.stringify({
          ...formData,
          room_type_id: parseInt(roomTypeId),
          booking_source: "website",
        }),
      });

      setPriceBreakdown(bookingData);

      // 2️⃣ Create Razorpay order
      const orderData = await apiRequest(
        `/payments/create-order/${bookingData.booking_id}`,
        {
          method: "POST",
        }
      );

      openRazorpay(orderData, bookingData.booking_id);

    } catch (error) {
      const errorMessage = 
        typeof error === 'string' ? error :
        error?.response?.data?.detail ? error.response.data.detail :
        error?.message || "An unexpected error occurred";
      setError(errorMessage);
    } finally {
      setLoading(false);
    }
  };

  /* ================= RAZORPAY ================= */

  const openRazorpay = (orderData, bookingId) => {
    const options = {
      key: orderData.key,
      amount: orderData.amount,
      currency: orderData.currency,
      order_id: orderData.order_id,
      name: "Hotel Bhimas",
      description: "Room Booking Payment",
      image: "https://raw.githubusercontent.com/Gk200432/Hotel-Bhimas-Website/refs/heads/main/logo-gold.svg",
      prefill: {
        name: formData.guest_name,
        email: formData.email,
        contact: formData.phone,
      },
      notes: {
        booking_id: bookingId,
        check_in: formData.check_in,
        check_out: formData.check_out,
      },
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
        } catch {
          navigate(`/payment-failed/${bookingId}`);
        }
      },

      modal: {
        ondismiss: async function () {
          try {
            await apiRequest("/payments/verify", {
              method: "POST",
              body: JSON.stringify({
                razorpay_order_id: orderData.order_id, // ✅ FIXED
                status: "failed",
              }),
            });
          } catch (err) {
            console.error("Failed to mark payment as failed");
          }

          navigate(`/payment-failed/${bookingId}`);
        },
      },

      theme: { color: "#D4AF37" },
    };

    const rzp = new window.Razorpay(options);
    rzp.open();
  };

  if (!room) return <div className="p-20">Loading...</div>;

  return (
    <div className="min-h-screen bg-[#F8FAFC] px-6 py-20">
      <div className="max-w-4xl mx-auto bg-white p-12 rounded-3xl shadow-xl">
        <h2 className="text-3xl font-bold text-center mb-10">
          Book {room.name}
        </h2>

        {error && (
        <div className="bg-red-100 text-red-700 p-3 rounded-lg mb-6 text-center">
          {error}
        </div>
      )}

        <form onSubmit={handleBooking} className="grid md:grid-cols-2 gap-6">

          <input
            type="text"
            name="guest_name"
            placeholder="Full Name"
            required
            onChange={handleChange}
            className="border p-3 rounded-lg"
          />

          <input
            type="tel"
            name="phone"
            placeholder="Phone"
            required
            onChange={handleChange}
            className="border p-3 rounded-lg"
          />

          <input
            type="email"
            name="email"
            placeholder="Email"
            required
            onChange={handleChange}
            className="border p-3 rounded-lg md:col-span-2"
          />

          <input
            type="date"
            name="check_in"
            required
            onChange={handleChange}
            className="border p-3 rounded-lg"
          />

          <input
            type="date"
            name="check_out"
            required
            onChange={handleChange}
            className="border p-3 rounded-lg"
          />


          <button
            type="submit"
            disabled={loading}
            className="md:col-span-2 bg-[#E5C07B] py-4 rounded-full font-semibold hover:bg-[#FCD34D]"
          >
            {loading ? "Processing..." : "Proceed to Payment"}
          </button>
        </form>

        <div className="mt-10 bg-gray-50 p-6 rounded-xl">
           <h3 className="text-xl font-bold mb-4">Price Breakdown</h3>

    

   {nights > 0 && (
  <div className="md:col-span-2 mt-10 bg-gray-50 p-6 rounded-xl">
    <h3 className="text-xl font-bold mb-4">Payment Summary</h3>

    {/* Room Calculation */}
    <p>
      {nights} Night{nights > 1 ? "s" : ""} × ₹ {pricePerNight} = ₹ {roomBaseTotal}
    </p>
    <p>Room GST (12%) = ₹ {roomGST.toFixed(2)}</p>

    <hr className="my-2" />

    <p className="font-semibold">
      Room Total (Incl. GST) = ₹{" "}
      {priceBreakdown
        ? priceBreakdown.room_total
        : roomTotalWithGST.toFixed(2)}
    </p>

    {/* Show convenience fee only after backend response */}
    {priceBreakdown && (
      <>
        <p>
          Convenience Fee (Incl. GST) = ₹{" "}
          {priceBreakdown.convenience_fee}
        </p>

        <hr className="my-3" />

        <p className="text-xl font-bold">
          Total Payable = ₹ {priceBreakdown.payable_amount}
        </p>
      </>
    )}
  </div>
)}
  </div>
      </div>
    </div>
  );
};

export default Booking;