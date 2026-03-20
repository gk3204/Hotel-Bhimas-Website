import React, { useEffect, useState } from "react";
import {
  getBookings,
  getBookingById,
  cancelBooking,
} from "../../api/bookings";
import { jwtDecode } from "jwt-decode";

const Bookings = () => {
  const [bookings, setBookings] = useState([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [fromDate, setFromDate] = useState("");
  const [loading, setLoading] = useState(false);
  const [role, setRole] = useState(null);
  const [selectedBooking, setSelectedBooking] = useState(null);
  const [confirmId, setConfirmId] = useState(null);
  const [toast, setToast] = useState(null);

  const limit = 15;
  const totalPages = Math.ceil(total / limit);

  useEffect(() => {
    loadBookings();
  }, [page, fromDate]);

  const loadBookings = async () => {
    try {
      setLoading(true);
      const res = await getBookings(page, fromDate);
      setBookings(res.data);
      setTotal(res.total);
    } catch (err) {
      showToast("Failed to load bookings", "error");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
  const token = localStorage.getItem("adminToken");
  if (token) {
    const decoded = jwtDecode(token);
    setRole(decoded.role);
  }
}, []);


  const handleView = async (id) => {
    try {
      const data = await getBookingById(id);
      setSelectedBooking(data);
    } catch {
      showToast("Failed to load booking details", "error");
    }
  };

  const confirmCancel = async () => {
    try {
      await cancelBooking(confirmId);
      showToast("Booking cancelled successfully");
      setConfirmId(null);
      loadBookings();
    } catch {
      showToast("Failed to cancel booking", "error");
    }
  };

  const showToast = (message, type = "success") => {
    setToast({ message, type });
    setTimeout(() => setToast(null), 3000);
  };

  return (
    <div className="min-h-screen bg-[#0F172A] text-[#E5C07B] p-6">
      <h1 className="text-3xl font-bold mb-8">Bookings Management</h1>

      {/* FILTER */}
      <div className="bg-[#111827] p-6 rounded-2xl shadow-xl mb-8">
        <h2 className="text-xl mb-4">Filter by Check-In Date</h2>

        <div className="flex gap-4">
          <input
            type="date"
            className="p-2 bg-[#1F2937] rounded-lg text-white"
            value={fromDate}
            onChange={(e) => {
              setPage(1);
              setFromDate(e.target.value);
            }}
          />

          <button
            onClick={() => {
              setFromDate("");
              setPage(1);
            }}
            className="bg-gray-600 px-4 py-2 rounded text-white"
          >
            Reset
          </button>
        </div>
      </div>

      {/* TABLE */}
      <div className="bg-[#111827] rounded-2xl shadow-xl p-6">
        <h2 className="text-xl mb-6">All Bookings</h2>

        {loading ? (
          <p>Loading...</p>
        ) : (
          <>
            <div className="overflow-x-auto max-h-[550px] overflow-y-auto">
              <table className="w-full text-left">
                <thead>
                  <tr className="border-b border-[#E5C07B]/30">
                    <th className="py-3">Booking ID</th>
                    <th>Guest</th>
                    <th>Room Type</th>
                    <th>Check In</th>
                    <th>Check Out</th>
                    <th>Status</th>
                    <th>Payable</th>
                    <th>Actions</th>
                  </tr>
                </thead>

                <tbody>
                  {bookings.map((b) => (
                    <tr
                      key={b.booking_id}
                      className="border-b border-[#E5C07B]/10 hover:bg-[#1F2937]"
                    >
                      <td className="py-3 font-mono text-sm text-[#FCD34D]">
                        {b.booking_id}
                      </td>
                      <td className="py-3">{b.guest_name}</td>
                      <td>{b.room_type}</td>
                      <td>{b.check_in}</td>
                      <td>{b.check_out}</td>

                      <td>
                        <span
                          className={`px-3 py-1 rounded-full text-sm ${
                            b.status === "cancelled"
                              ? "bg-red-600 text-white"
                              : b.status === "pending_payment"
                              ? "bg-yellow-600 text-black"
                              : b.status === "payment_failed"
                              ? "bg-orange-600 text-white"
                              : "bg-green-600 text-white"
                          }`}
                        >
                          {b.status}
                        </span>
                      </td>

                      <td>₹{b.payable_amount}</td>

                      <td className="space-x-2">
                        <button
                          onClick={() => handleView(b.booking_id)}
                          className="bg-blue-600 px-3 py-1 rounded text-white"
                        >
                          View
                        </button>

                        {role === "admin" && b.status !== "cancelled" && (
                        <button
                            onClick={() => setConfirmId(b.booking_id)}
                            className="bg-red-600 px-3 py-1 rounded text-white"
                        >
                            Cancel
                        </button>
                        )}

                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {/* PAGINATION */}
            <div className="flex justify-between items-center mt-6">
              <button
                disabled={page === 1}
                onClick={() => setPage(page - 1)}
                className="bg-gray-600 px-4 py-2 rounded disabled:opacity-50"
              >
                Previous
              </button>

              <span>
                Page {page} of {totalPages || 1}
              </span>

              <button
                disabled={page === totalPages}
                onClick={() => setPage(page + 1)}
                className="bg-gray-600 px-4 py-2 rounded disabled:opacity-50"
              >
                Next
              </button>
            </div>
          </>
        )}
      </div>

      {/* VIEW MODAL */}
      {selectedBooking && (
  <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50">
    <div className="bg-[#111827] p-8 rounded-2xl w-[550px] text-white border border-[#E5C07B]/30 relative">

      {/* Title */}
      <h2 className="text-2xl mb-6 text-[#E5C07B] font-semibold">
        Booking Details
      </h2>

      <div className="mb-4 text-sm text-gray-400">
        <b>Booking ID:</b> {selectedBooking.booking_id}
      </div>

      {/* Guest Info */}
      <div className="space-y-1 text-sm">
        <p><b>Guest:</b> {selectedBooking.guest.name}</p>
        <p><b>Phone:</b> {selectedBooking.guest.phone}</p>
        <p><b>Email:</b> {selectedBooking.guest.email}</p>
      </div>

      <hr className="my-4 border-[#E5C07B]/30" />

      {/* Room Info */}
      <div className="space-y-1 text-sm">
        <p><b>Room Type:</b> {selectedBooking.room_type.name}</p>
        <p><b>Price/Night:</b> ₹{selectedBooking.room_type.price_per_night}</p>
      </div>

      <hr className="my-4 border-[#E5C07B]/30" />

      {/* Stay Info */}
      <div className="space-y-1 text-sm">
        <p><b>Check In:</b> {selectedBooking.stay.check_in}</p>
        <p><b>Check Out:</b> {selectedBooking.stay.check_out}</p>
        <p><b>Nights:</b> {selectedBooking.stay.nights}</p>
      </div>

      <hr className="my-4 border-[#E5C07B]/30" />

      {/* Charges Section */}
      <div className="bg-[#1F2937] p-5 rounded-xl border border-[#E5C07B]/20">
        <h3 className="text-lg mb-4 text-[#E5C07B] font-semibold">
          Charges Breakdown
        </h3>

        <div className="space-y-2 text-sm">

          <div className="flex justify-between">
            <span>Room Base</span>
            <span>₹{selectedBooking.charges.room_base}</span>
          </div>

          <div className="flex justify-between">
            <span>Room GST</span>
            <span>₹{selectedBooking.charges.room_gst}</span>
          </div>

          <div className="flex justify-between font-medium border-t border-gray-600 pt-2">
            <span>Room Total</span>
            <span>₹{selectedBooking.charges.room_total}</span>
          </div>

          <div className="flex justify-between mt-3">
            <span>Convenience Fee</span>
            <span>₹{selectedBooking.charges.convenience_fee}</span>
          </div>

          <div className="flex justify-between">
            <span>Convenience GST</span>
            <span>₹{selectedBooking.charges.convenience_gst}</span>
          </div>

          <div className="flex justify-between text-lg font-bold border-t border-[#E5C07B] pt-3 mt-3 text-[#FCD34D]">
            <span>Payable Amount</span>
            <span>₹{selectedBooking.charges.payable_amount}</span>
          </div>

        </div>
      </div>

      {/* Close Button */}
      <div className="mt-6 text-right">
        <button
          onClick={() => setSelectedBooking(null)}
          className="bg-gray-600 hover:bg-gray-500 px-4 py-2 rounded-lg transition"
        >
          Close
        </button>
      </div>

    </div>
  </div>
)}


      {/* CANCEL CONFIRM MODAL */}
      {confirmId && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center">
          <div className="bg-[#111827] p-8 rounded-2xl w-96 text-white">
            <h2 className="text-xl mb-4 text-[#E5C07B]">
              Confirm Cancellation
            </h2>

            <p>Are you sure you want to cancel this booking?</p>

            <div className="flex justify-end gap-4 mt-6">
              <button
                onClick={() => setConfirmId(null)}
                className="bg-gray-600 px-4 py-2 rounded"
              >
                No
              </button>

              <button
                onClick={confirmCancel}
                className="bg-red-600 px-4 py-2 rounded"
              >
                Yes, Cancel
              </button>
            </div>
          </div>
        </div>
      )}

      {/* TOAST */}
      {toast && (
        <div className="fixed top-6 right-6 z-50">
          <div
            className={`px-6 py-3 rounded-xl shadow-lg text-white ${
              toast.type === "error"
                ? "bg-red-600"
                : "bg-green-600"
            }`}
          >
            {toast.message}
          </div>
        </div>
      )}
    </div>
  );
};

export default Bookings;
