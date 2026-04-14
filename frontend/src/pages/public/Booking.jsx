import React, { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { apiRequest } from "../../api/api";
import { checkRoomAvailability } from "../../api/roomtype";
import { FaPlus, FaMinus, FaUsers, FaBed, FaCoffee } from "react-icons/fa";
import LoadingSpinner from "../../components/LoadingSpinner";
import { withMinimumDelay } from "../../utils/loadingDelay";

const Booking = () => {
  const navigate = useNavigate();

  const [roomTypes, setRoomTypes] = useState([]);
  const [selectedRooms, setSelectedRooms] = useState({});
  const [availability, setAvailability] = useState({});
  const [priceBreakdown, setPriceBreakdown] = useState(null);
  const [loading, setLoading] = useState(false);
  const [paymentLoading, setPaymentLoading] = useState(false);
  const [error, setError] = useState(null);

  const [formData, setFormData] = useState({
    guest_name: "",
    phone: "",
    email: "",
    check_in: "",
    check_out: "",
  });

  /* ================= SCROLL TO TOP ON MOUNT ================= */

  useEffect(() => {
    window.scrollTo(0, 0);
  }, []);

  /* ================= FETCH ROOM TYPES ================= */

  useEffect(() => {
    apiRequest("/room-types/")
      .then((data) => {
        setRoomTypes(data);
        // Initialize selected rooms object
        const initial = {};
        data.forEach(room => {
          initial[room.room_type_id] = 0;
        });
        setSelectedRooms(initial);
      })
      .catch((err) => console.error("Error fetching room types:", err));
  }, []);

  /* ================= CHECK AVAILABILITY ON DATE CHANGE ================= */

  useEffect(() => {
    if (!formData.check_in || !formData.check_out) {
      setAvailability({});
      return;
    }

    const checkAvailability = async () => {
      try {
        // 🚀 Parallelize all availability checks instead of sequential
        const availabilityPromises = roomTypes.map(roomType =>
          checkRoomAvailability(
            roomType.room_type_id,
            formData.check_in,
            formData.check_out
          ).then(availData => ({
            roomTypeId: roomType.room_type_id,
            data: availData
          }))
        );

        const results = await Promise.all(availabilityPromises);
        
        const newAvailability = {};
        results.forEach(result => {
          newAvailability[result.roomTypeId] = result.data;
        });
        
        setAvailability(newAvailability);
      } catch (err) {
        console.error("Error checking availability:", err);
      }
    };

    checkAvailability();
  }, [formData.check_in, formData.check_out, roomTypes]);

  /* ================= SCROLL TO TOP ON ERROR ================= */

  useEffect(() => {
    if (error) {
      window.scrollTo(0, 0);
    }
  }, [error]);

  /* ================= HANDLE INPUT ================= */

  const handleChange = (e) => {
    setFormData({
      ...formData,
      [e.target.name]: e.target.value,
    });
  };

  const handleRoomQuantityChange = (roomTypeId, quantity) => {
    const q = parseInt(quantity) || 0;
    const maxAvailable = availability[roomTypeId]?.available_rooms || 5;
    
    if (q < 0 || q > maxAvailable) return; // Limit to 0-maxAvailable

    setSelectedRooms(prev => ({
      ...prev,
      [roomTypeId]: q
    }));
  };

  const calculateNights = () => {
    if (!formData.check_in || !formData.check_out) return 0;

    const checkIn = new Date(formData.check_in);
    const checkOut = new Date(formData.check_out);

    const diffTime = checkOut - checkIn;
    const diffDays = diffTime / (1000 * 60 * 60 * 24);

    return diffDays > 0 ? diffDays : 0;
  };

  /* ================= CALCULATE TOTAL PRICE ================= */

  const calculateTotal = () => {
    const nights = calculateNights();
    if (nights <= 0) return null;

    let totalBase = 0;
    let totalGST = 0;

    Object.entries(selectedRooms).forEach(([roomTypeId, quantity]) => {
      if (quantity > 0) {
        const roomType = roomTypes.find(r => r.room_type_id === parseInt(roomTypeId));
        if (roomType) {
          const baseAmount = nights * roomType.price_per_night * quantity;
          const gstAmount = baseAmount * (roomType.gst_percent / 100);
          totalBase += baseAmount;
          totalGST += gstAmount;
        }
      }
    });

    if (totalBase === 0) return null;

    const roomTotal = totalBase + totalGST;
    const convenienceBase = roomTotal * 0.02;
    const convenienceGST = convenienceBase * 0.18;
    const convenienceFeeTotal = convenienceBase + convenienceGST;
    const grandTotal = roomTotal + convenienceFeeTotal;

    return {
      nights,
      baseAmount: totalBase.toFixed(2),
      gstAmount: totalGST.toFixed(2),
      roomTotal: roomTotal.toFixed(2),
      convenienceFeeTotal: convenienceFeeTotal.toFixed(2),
      grandTotal: grandTotal.toFixed(2),
    };
  };

  /* ================= VALIDATE BOOKING ================= */

  const getSelectedRoomsList = () => {
    return Object.entries(selectedRooms)
      .filter(([_, qty]) => qty > 0)
      .map(([roomTypeId, quantity]) => ({
        room_type_id: parseInt(roomTypeId),
        quantity: quantity
      }));
  };

  /* ================= HANDLE BOOKING ================= */

  const handleBooking = async (e) => {
    e.preventDefault();

    const selectedRoomsList = getSelectedRoomsList();

    if (selectedRoomsList.length === 0) {
      setError("Please select at least one room");
      return;
    }

    if (!formData.check_in || !formData.check_out) {
      setError("Please select check-in and check-out dates");
      return;
    }

    try {
      setLoading(true);
      setError(null);

      // 1️⃣ Create multi-room booking
      const bookingData = await withMinimumDelay(
        apiRequest("/bookings/", {
          method: "POST",
          body: JSON.stringify({
            guest_name: formData.guest_name,
            phone: formData.phone,
            email: formData.email,
            check_in: formData.check_in,
            check_out: formData.check_out,
            rooms: selectedRoomsList,
            booking_source: "website",
          }),
        })
      );

      setPriceBreakdown(bookingData);

      // 2️⃣ Create Razorpay order
      const orderData = await withMinimumDelay(
        apiRequest(
          `/payments/create-order/${bookingData.booking_id}`,
          {
            method: "POST",
          }
        )
      );

      openRazorpay(orderData, bookingData.booking_id);

    } catch (error) {
      const errorMessage =
        typeof error === 'string' ? error :
          error?.response?.data?.detail ? error.response.data.detail :
            error?.message || "An unexpected error occurred";
      setError(errorMessage);
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

      handler: async function (response) {
        try {
          setPaymentLoading(true);
          setLoading(false);
          await withMinimumDelay(
            apiRequest("/payments/verify", {
              method: "POST",
              body: JSON.stringify({
                razorpay_order_id: response.razorpay_order_id,
                razorpay_payment_id: response.razorpay_payment_id,
                razorpay_signature: response.razorpay_signature,
                status: "success",
              }),
            })
          );

          navigate(`/payment-success/${bookingId}`);
        } catch {
          navigate(`/payment-failed/${bookingId}`);
        } finally {
          setPaymentLoading(false);
        }
      },

      modal: {
        ondismiss: async function () {
          try {
            setPaymentLoading(true);
            setLoading(false);
            await withMinimumDelay(
              apiRequest("/payments/verify", {
                method: "POST",
                body: JSON.stringify({
                  razorpay_order_id: orderData.order_id,
                  status: "failed",
                }),
              })
            );
          } catch (err) {
            console.error("Failed to mark payment as failed");
          } finally {
            setPaymentLoading(false);
            navigate(`/payment-failed/${bookingId}`);
          }
        },
      },

      theme: { color: "#D4AF37" },
    };

    const rzp = new window.Razorpay(options);
    rzp.open();
  };

  const priceInfo = calculateTotal();
  const selectedRoomCount = Object.values(selectedRooms).reduce((a, b) => a + b, 0);

  return (
    <>
      {/* Loading Spinner for Payment Processing */}
      {(loading || paymentLoading) && (
        <LoadingSpinner 
          message={loading ? "Setting up your booking..." : "Processing your payment..."} 
        />
      )}
      
      <div className="min-h-screen bg-gradient-to-br from-slate-50 via-blue-50 to-slate-100 px-4 md:px-6 py-12 md:py-20">
        <div className="max-w-7xl mx-auto">
        {/* Header */}
        <div className="text-center mb-12">
          <h1 className="text-5xl md:text-6xl font-bold text-gray-900 mb-3">
            Reserve Your Perfect Stay
          </h1>
          <p className="text-gray-600 text-lg">Choose your rooms and complete your booking in seconds</p>
        </div>

        {/* Error Alert */}
        {error && (
          <div className="mb-8 bg-red-50 border-l-4 border-red-500 p-4 rounded-lg animate-pulse">
            <p className="text-red-700 font-medium">⚠️ {error}</p>
          </div>
        )}

        <div className="grid lg:grid-cols-3 gap-8">
          {/* LEFT SIDE - DATES, ROOMS & GUEST INFO */}
          <div className="lg:col-span-2 space-y-8">
            {/* DATE SELECTION - FIRST STEP */}
            <div className="bg-white rounded-2xl shadow-xl overflow-hidden">
              <div className="bg-gradient-to-r from-purple-600 to-purple-700 p-8">
                <h2 className="text-2xl font-bold text-white flex items-center gap-2">
                  📅 When do you want to visit?
                </h2>
                <p className="text-purple-100 text-sm mt-2">Select your check-in and check-out dates</p>
              </div>

              <div className="p-8">
                <div className="grid md:grid-cols-2 gap-6">
                  <div>
                    <label className="block text-sm font-semibold text-gray-700 mb-2">Check-In Date *</label>
                    <input
                      type="date"
                      name="check_in"
                      value={formData.check_in}
                      onChange={handleChange}
                      className="w-full px-4 py-3 border-2 border-gray-200 rounded-lg focus:outline-none focus:border-purple-600 focus:ring-2 focus:ring-purple-600/20 transition"
                    />
                  </div>

                  <div>
                    <label className="block text-sm font-semibold text-gray-700 mb-2">Check-Out Date *</label>
                    <input
                      type="date"
                      name="check_out"
                      value={formData.check_out}
                      onChange={handleChange}
                      className="w-full px-4 py-3 border-2 border-gray-200 rounded-lg focus:outline-none focus:border-purple-600 focus:ring-2 focus:ring-purple-600/20 transition"
                    />
                  </div>
                </div>

                {formData.check_in && formData.check_out && (
                  <div className="mt-4 p-3 bg-purple-50 border border-purple-200 rounded-lg">
                    <p className="text-purple-800 text-sm font-medium">
                      ✅ {calculateNights()} nights selected
                    </p>
                  </div>
                )}
              </div>
            </div>

            {/* ROOM SELECTION - SECOND STEP */}
            <div className="bg-white rounded-2xl shadow-xl overflow-hidden">
              <div className="bg-gradient-to-r from-[#E5C07B] to-[#D4AF37] p-8">
                <h2 className="text-2xl font-bold text-white flex items-center gap-2">
                  <FaBed size={28} />
                  Select Your Rooms
                </h2>
                <p className="text-yellow-50 text-sm mt-2">Available rooms for your stay</p>
              </div>

              <div className="p-8">
                {!formData.check_in || !formData.check_out ? (
                  <div className="flex justify-center items-center h-48">
                    <div className="text-center">
                      <div className="text-5xl mb-4">📅</div>
                      <p className="text-gray-500 font-medium">Please select your dates first to see available rooms</p>
                    </div>
                  </div>
                ) : roomTypes.length === 0 ? (
                  <div className="flex justify-center items-center h-48">
                    <div className="text-center">
                      <div className="animate-spin mb-4 mx-auto">
                        <div className="h-12 w-12 border-4 border-[#E5C07B] border-t-[#D4AF37] rounded-full"></div>
                      </div>
                      <p className="text-gray-500 font-medium">Loading available rooms...</p>
                    </div>
                  </div>
                ) : (
                  <div className="space-y-5">
                    {roomTypes.map((roomType) => (
                      <div
                        key={roomType.room_type_id}
                        className="border-2 border-gray-100 p-6 rounded-2xl hover:border-[#E5C07B] hover:shadow-lg transition-all duration-300 bg-gradient-to-br from-white to-blue-50"
                      >
                        <div className="flex justify-between items-start mb-4">
                          <div className="flex-1">
                            <h3 className="text-xl font-bold text-gray-900 mb-2">
                              {roomType.name}
                            </h3>
                            <div className="flex gap-4 flex-wrap">
                              <div className="flex items-center gap-1 text-gray-600">
                                <FaUsers size={16} />
                                <span className="text-sm">Up to {roomType.max_occupancy} guests</span>
                              </div>
                              <div className="flex items-center gap-1 text-gray-600">
                                <FaCoffee size={16} />
                                <span className="text-sm">Complimentary amenities</span>
                              </div>
                            </div>
                          </div>
                          <div className="text-right">
                            <p className="text-3xl font-bold text-[#E5C07B] mb-1">
                              ₹{roomType.price_per_night}
                            </p>
                            <p className="text-xs text-gray-500 font-medium">
                              per night (+ {roomType.gst_percent}% GST)
                            </p>
                            {availability[roomType.room_type_id] && (
                              <p className={`text-xs font-semibold mt-2 ${
                                availability[roomType.room_type_id].is_blocked
                                  ? "text-red-600"
                                  : availability[roomType.room_type_id].available_rooms > 0
                                  ? "text-green-600"
                                  : "text-red-600"
                              }`}>
                                {availability[roomType.room_type_id].is_blocked
                                  ? `❌ Not available`
                                  : `✅ ${availability[roomType.room_type_id].available_rooms} available`}
                              </p>
                            )}
                          </div>
                        </div>

                        {/* Interactive Quantity Selector */}
                        <div className="flex items-center justify-between pt-4 border-t-2 border-gray-100">
                          <label className="text-sm font-semibold text-gray-700">
                            Number of Rooms:
                          </label>
                          <div className="flex items-center gap-3 bg-gray-100 rounded-full p-1">
                            <button
                              type="button"
                              onClick={() =>
                                handleRoomQuantityChange(
                                  roomType.room_type_id,
                                  (selectedRooms[roomType.room_type_id] || 0) - 1
                                )
                              }
                              className="p-2 hover:bg-white rounded-full transition text-gray-600 hover:text-[#E5C07B] disabled:opacity-50 disabled:cursor-not-allowed"
                              disabled={(selectedRooms[roomType.room_type_id] || 0) <= 0}
                            >
                              <FaMinus size={18} />
                            </button>
                            <span className="w-12 text-center font-bold text-lg text-gray-900">
                              {selectedRooms[roomType.room_type_id] || 0}
                            </span>
                            <button
                              type="button"
                              onClick={() =>
                                handleRoomQuantityChange(
                                  roomType.room_type_id,
                                  (selectedRooms[roomType.room_type_id] || 0) + 1
                                )
                              }
                              className="p-2 hover:bg-white rounded-full transition text-gray-600 hover:text-[#E5C07B] disabled:opacity-50 disabled:cursor-not-allowed"
                              disabled={
                                !availability[roomType.room_type_id] ||
                                availability[roomType.room_type_id].is_blocked ||
                                (selectedRooms[roomType.room_type_id] || 0) >= (availability[roomType.room_type_id]?.available_rooms || 0)
                              }
                            >
                              <FaPlus size={18} />
                            </button>
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>

            {/* GUEST & DETAILS - THIRD STEP */}
            <div className="bg-white rounded-2xl shadow-xl overflow-hidden">
              <div className="bg-gradient-to-r from-blue-600 to-blue-700 p-8">
                <h2 className="text-2xl font-bold text-white">Guest Information</h2>
                <p className="text-blue-100 text-sm mt-2">Tell us about yourself</p>
              </div>

              <form onSubmit={handleBooking} className="p-8 space-y-6">
                {/* Name and Phone */}
                <div className="grid md:grid-cols-2 gap-6">
                  <div>
                    <label className="block text-sm font-semibold text-gray-700 mb-2">Full Name *</label>
                    <input
                      type="text"
                      name="guest_name"
                      placeholder="e.g., John Doe"
                      required
                      value={formData.guest_name}
                      onChange={handleChange}
                      className="w-full px-4 py-3 border-2 border-gray-200 rounded-lg focus:outline-none focus:border-[#E5C07B] focus:ring-2 focus:ring-[#E5C07B]/20 transition"
                    />
                  </div>

                  <div>
                    <label className="block text-sm font-semibold text-gray-700 mb-2">Phone Number *</label>
                    <input
                      type="tel"
                      name="phone"
                      placeholder="e.g., +91 98765 43210"
                      required
                      value={formData.phone}
                      onChange={handleChange}
                      className="w-full px-4 py-3 border-2 border-gray-200 rounded-lg focus:outline-none focus:border-[#E5C07B] focus:ring-2 focus:ring-[#E5C07B]/20 transition"
                    />
                  </div>
                </div>

                {/* Email */}
                <div>
                  <label className="block text-sm font-semibold text-gray-700 mb-2">Email Address *</label>
                  <input
                    type="email"
                    name="email"
                    placeholder="e.g., john@example.com"
                    required
                    value={formData.email}
                    onChange={handleChange}
                    className="w-full px-4 py-3 border-2 border-gray-200 rounded-lg focus:outline-none focus:border-[#E5C07B] focus:ring-2 focus:ring-[#E5C07B]/20 transition"
                  />
                </div>

                {/* Submit Button */}
                {selectedRoomCount > 0 && (
                  <button
                    type="submit"
                    disabled={loading || paymentLoading || !priceInfo}
                    className="w-full bg-gradient-to-r from-[#E5C07B] to-[#D4AF37] text-white py-4 rounded-lg font-bold text-lg hover:shadow-xl hover:scale-105 disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:scale-100 transition-all duration-300 mt-8"
                  >
                    {loading || paymentLoading ? "🔄 Processing..." : "💳 Proceed to Payment"}
                  </button>
                )}

                {selectedRoomCount === 0 && (
                  <div className="bg-blue-50 border-l-4 border-blue-500 p-4 rounded-lg">
                    <p className="text-blue-700 font-medium text-sm">👆 Please select at least one room above to continue</p>
                  </div>
                )}
              </form>
            </div>
          </div>

          {/* RIGHT SIDE - PRICE SUMMARY */}
          <div className="lg:col-span-1">
            <div className="bg-white rounded-2xl shadow-xl overflow-hidden sticky top-24 h-fit">
              <div className="bg-gradient-to-r from-[#E5C07B] to-[#D4AF37] p-6">
                <h2 className="text-2xl font-bold text-white">Booking Summary</h2>
              </div>

              <div className="p-8">
                {selectedRoomCount > 0 && priceInfo ? (
                  <div className="space-y-4 text-sm">
                    <div className="space-y-2">
                      <h3 className="font-bold text-gray-900 text-base">Selected Rooms:</h3>
                      {Object.entries(selectedRooms)
                        .filter(([_, qty]) => qty > 0)
                        .map(([roomTypeId, qty]) => {
                          const room = roomTypes.find(r => r.room_type_id === parseInt(roomTypeId));
                          return (
                            <p key={roomTypeId} className="text-gray-600">
                              {room?.name} x {qty}
                            </p>
                          );
                        })}
                    </div>

                    <div className="border-t pt-3">
                      <div className="flex justify-between text-gray-700 mb-1">
                        <span>Nights:</span>
                        <span className="font-semibold">{priceInfo.nights}</span>
                      </div>
                      <div className="flex justify-between text-gray-700 mb-3">
                        <span>Cost per Night:</span>
                        <span className="font-semibold">
                          ₹{(parseFloat(priceInfo.baseAmount) / priceInfo.nights).toFixed(2)}
                        </span>
                      </div>
                    </div>

                    <div className="border-t pt-3 space-y-2">
                      <div className="flex justify-between text-gray-700">
                        <span>Base Amount:</span>
                        <span className="font-semibold">₹{priceInfo.baseAmount}</span>
                      </div>
                      <div className="flex justify-between text-gray-700">
                        <span>GST:</span>
                        <span className="font-semibold">₹{priceInfo.gstAmount}</span>
                      </div>
                      <div className="flex justify-between text-gray-700">
                        <span>Convenience Fee (incl. GST):</span>
                        <span className="font-semibold">₹{priceInfo.convenienceFeeTotal}</span>
                      </div>
                    </div>

                    <div className="border-t-2 border-[#E5C07B] pt-4">
                      <div className="flex justify-between text-lg font-bold text-[#E5C07B]">
                        <span>Grand Total:</span>
                        <span>₹{priceInfo.grandTotal}</span>
                      </div>
                    </div>
                  </div>
                ) : (
                  <div className="text-center py-8">
                    <p className="text-gray-400 text-sm">
                      👈 Select rooms to see pricing
                    </p>
                  </div>
                )}
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
    </>
  );
};

export default Booking;
