import React, { useEffect, useState } from "react";
import {
  getBookings,
  getBookingById,
  cancelBooking,
} from "../../api/bookings";
import { jwtDecode } from "jwt-decode";
import { FaSort, FaSortUp, FaSortDown, FaEye, FaTrash } from "react-icons/fa";

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
  const [sortField, setSortField] = useState("booking_id");
  const [sortOrder, setSortOrder] = useState("desc");

  const limit = 15;
  const totalPages = Math.ceil(total / limit);

  useEffect(() => {
    loadBookings();
  }, [page, fromDate]);

  const loadBookings = async () => {
    try {
      setLoading(true);
      const res = await getBookings(page, fromDate);
      let sortedBookings = [...res.data];

      sortedBookings.sort((a, b) => {
        let aVal = a[sortField];
        let bVal = b[sortField];

        if (
          sortField === "booking_id" ||
          sortField === "payable_amount" ||
          sortField === "room_count"
        ) {
          aVal = Number(aVal);
          bVal = Number(bVal);
        }

        if (aVal < bVal) return sortOrder === "asc" ? -1 : 1;
        if (aVal > bVal) return sortOrder === "asc" ? 1 : -1;
        return 0;
      });

      setBookings(sortedBookings);
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

  const handleSort = (field) => {
    if (sortField === field) {
      setSortOrder(sortOrder === "asc" ? "desc" : "asc");
    } else {
      setSortField(field);
      setSortOrder("desc");
    }
  };

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

  const getSortIcon = (field) => {
    if (sortField !== field)
      return <FaSort className="ml-1 opacity-40" size={12} />;
    return sortOrder === "asc" ? (
      <FaSortUp className="ml-1" size={12} />
    ) : (
      <FaSortDown className="ml-1" size={12} />
    );
  };

  const getStatusColor = (status) => {
    switch (status) {
      case "cancelled":
        return "bg-red-500/20 text-red-300 border-red-500/30";
      case "pending_payment":
        return "bg-yellow-500/20 text-yellow-300 border-yellow-500/30";
      case "payment_failed":
        return "bg-orange-500/20 text-orange-300 border-orange-500/30";
      case "confirmed":
        return "bg-green-500/20 text-green-300 border-green-500/30";
      default:
        return "bg-blue-500/20 text-blue-300 border-blue-500/30";
    }
  };

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-900 via-slate-800 to-slate-900 text-white p-6">
      <div className="max-w-7xl mx-auto">

        {/* Header */}
        <div className="mb-8">
          <h1 className="text-4xl font-bold mb-2 bg-gradient-to-r from-[#E5C07B] to-[#FCD34D] bg-clip-text text-transparent">
            📋 Bookings Management
          </h1>
          <p className="text-slate-400">Manage and track all hotel bookings</p>
        </div>

        {/* Filter Section */}
        <div className="bg-gradient-to-r from-slate-800/50 to-slate-700/50 border border-slate-700 p-6 rounded-2xl shadow-xl mb-8 backdrop-blur">
          <h2 className="text-xl font-semibold mb-4 text-[#E5C07B]">
            🔍 Filter by Check-In Date
          </h2>
          <div className="flex gap-4 flex-wrap">
            <input
              type="date"
              className="px-4 py-3 bg-slate-900/50 border border-slate-600 rounded-lg text-white focus:outline-none focus:border-[#E5C07B] transition"
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
              className="bg-slate-700 hover:bg-slate-600 px-6 py-3 rounded-lg text-white font-medium transition"
            >
              Reset Filter
            </button>
          </div>
        </div>

        {/* Table Section */}
        <div className="bg-gradient-to-r from-slate-800/50 to-slate-700/50 border border-slate-700 rounded-2xl shadow-xl overflow-hidden backdrop-blur">
          <div className="p-6 border-b border-slate-700">
            <h2 className="text-xl font-semibold">📊 All Bookings ({total})</h2>
          </div>

          {loading ? (
            <div className="flex justify-center items-center h-64">
              <div className="text-center">
                <div className="animate-spin mb-4 mx-auto">
                  <div className="h-12 w-12 border-4 border-[#E5C07B] border-t-[#D4AF37] rounded-full"></div>
                </div>
                <p className="text-slate-400 font-medium">Loading bookings...</p>
              </div>
            </div>
          ) : (
            <>
              <div className="overflow-x-auto max-h-[600px] overflow-y-auto">
                <table className="w-full text-left">
                  <thead className="bg-slate-900/80 sticky top-0 border-b border-slate-700">
                    <tr>
                      <th
                        className="px-6 py-4 font-semibold cursor-pointer hover:text-[#E5C07B] transition group"
                        onClick={() => handleSort("booking_id")}
                      >
                        <div className="flex items-center">
                          Booking ID
                          {getSortIcon("booking_id")}
                        </div>
                      </th>
                      <th
                        className="px-6 py-4 font-semibold cursor-pointer hover:text-[#E5C07B] transition"
                        onClick={() => handleSort("guest_name")}
                      >
                        <div className="flex items-center">
                          Guest
                          {getSortIcon("guest_name")}
                        </div>
                      </th>
                      <th className="px-6 py-4 font-semibold text-center">
                        Rooms
                      </th>
                      <th
                        className="px-6 py-4 font-semibold cursor-pointer hover:text-[#E5C07B] transition"
                        onClick={() => handleSort("check_in")}
                      >
                        <div className="flex items-center">
                          Check In
                          {getSortIcon("check_in")}
                        </div>
                      </th>
                      <th className="px-6 py-4 font-semibold">Check Out</th>
                      <th className="px-6 py-4 font-semibold text-center">
                        Status
                      </th>
                      <th
                        className="px-6 py-4 font-semibold text-right cursor-pointer hover:text-[#E5C07B] transition"
                        onClick={() => handleSort("payable_amount")}
                      >
                        <div className="flex items-center justify-end">
                          Amount
                          {getSortIcon("payable_amount")}
                        </div>
                      </th>
                      <th className="px-6 py-4 font-semibold text-center">
                        Actions
                      </th>
                    </tr>
                  </thead>

                  <tbody className="divide-y divide-slate-700">
                    {bookings.length === 0 ? (
                      <tr>
                        <td
                          colSpan="8"
                          className="px-6 py-12 text-center text-slate-400"
                        >
                          No bookings found
                        </td>
                      </tr>
                    ) : (
                      bookings.map((b) => (
                        <tr
                          key={b.booking_id}
                          className="hover:bg-slate-700/30 transition border-opacity-50"
                        >
                          <td className="px-6 py-4 font-mono text-sm font-semibold text-[#FCD34D]">
                            #{b.booking_id}
                          </td>
                          <td className="px-6 py-4 text-sm">
                            <div className="font-medium">{b.guest_name}</div>
                          </td>
                          <td className="px-6 py-4 text-center">
                            <span className="bg-blue-500/20 text-blue-300 border border-blue-500/30 px-3 py-1 rounded-full text-xs font-semibold">
                              {b.room_count}{" "}
                              {b.room_count === 1 ? "room" : "rooms"}
                            </span>
                          </td>
                          <td className="px-6 py-4 text-sm">{b.check_in}</td>
                          <td className="px-6 py-4 text-sm">{b.check_out}</td>
                          <td className="px-6 py-4 text-center">
                            <span
                              className={`px-3 py-1 rounded-full text-xs font-semibold border ${getStatusColor(b.status)}`}
                            >
                              {b.status.replace(/_/g, " ")}
                            </span>
                          </td>
                          <td className="px-6 py-4 text-right text-sm font-semibold text-[#E5C07B]">
                            ₹{Number(b.payable_amount).toFixed(2)}
                          </td>
                          <td className="px-6 py-4 text-center space-x-2">
                            <button
                              onClick={() => handleView(b.booking_id)}
                              title="View Details"
                              className="bg-blue-600 hover:bg-blue-700 px-3 py-2 rounded-lg text-white text-sm font-medium transition inline-flex items-center gap-1"
                            >
                              <FaEye size={12} /> View
                            </button>

                            {role === "admin" && b.status !== "cancelled" && (
                              <button
                                onClick={() => setConfirmId(b.booking_id)}
                                title="Cancel Booking"
                                className="bg-red-600 hover:bg-red-700 px-3 py-2 rounded-lg text-white text-sm font-medium transition inline-flex items-center gap-1"
                              >
                                <FaTrash size={12} /> Cancel
                              </button>
                            )}
                          </td>
                        </tr>
                      ))
                    )}
                  </tbody>
                </table>
              </div>

              {/* Pagination */}
              {totalPages > 1 && (
                <div className="flex justify-between items-center p-6 border-t border-slate-700 bg-slate-900/40">
                  <button
                    disabled={page === 1}
                    onClick={() => setPage(page - 1)}
                    className="bg-slate-700 hover:bg-slate-600 disabled:opacity-50 px-6 py-2 rounded-lg font-medium transition"
                  >
                    ← Previous
                  </button>
                  <span className="text-slate-300">
                    Page{" "}
                    <span className="font-bold text-[#E5C07B]">{page}</span> of{" "}
                    <span className="font-bold text-[#E5C07B]">{totalPages}</span>
                  </span>
                  <button
                    disabled={page === totalPages}
                    onClick={() => setPage(page + 1)}
                    className="bg-slate-700 hover:bg-slate-600 disabled:opacity-50 px-6 py-2 rounded-lg font-medium transition"
                  >
                    Next →
                  </button>
                </div>
              )}
            </>
          )}
        </div>

        {/* ── VIEW MODAL ── */}
        {selectedBooking && (
          <div className="fixed inset-0 bg-black/70 backdrop-blur-sm flex items-center justify-center z-50 p-4">
            <div className="bg-gradient-to-br from-slate-800 to-slate-900 rounded-2xl w-full max-w-2xl max-h-[90vh] overflow-hidden flex flex-col text-white border border-slate-700 shadow-2xl">

              {/* Modal Header */}
              <div className="bg-gradient-to-r from-[#E5C07B] to-[#D4AF37] p-6 flex-shrink-0">
                <h2 className="text-2xl font-bold text-slate-900">
                  Booking #{selectedBooking.booking_id}
                </h2>
                <p className="text-sm text-slate-800 mt-1">
                  Complete booking details
                </p>
              </div>

              {/* Modal Scrollable Content */}
              <div className="overflow-y-auto p-6 md:p-8 space-y-6 flex-1">

                {/* Guest Info Card */}
                <div className="bg-slate-700/50 border border-slate-600 p-5 rounded-xl">
                  <h3 className="text-lg font-bold text-[#E5C07B] mb-4">
                    👤 Guest Information
                  </h3>
                  <div className="space-y-2 text-sm">
                    <p>
                      <span className="text-slate-400">Name:</span>{" "}
                      <span className="font-medium">
                        {selectedBooking.guest.name}
                      </span>
                    </p>
                    <p>
                      <span className="text-slate-400">Phone:</span>{" "}
                      <span className="font-medium">
                        {selectedBooking.guest.phone}
                      </span>
                    </p>
                    <p>
                      <span className="text-slate-400">Email:</span>{" "}
                      <span className="font-medium break-all">
                        {selectedBooking.guest.email}
                      </span>
                    </p>
                  </div>
                </div>

                {/* Room Info Card */}
                <div className="bg-slate-700/50 border border-slate-600 p-5 rounded-xl">
                  <h3 className="text-lg font-bold text-[#E5C07B] mb-4">
                    🛏️ Rooms ({selectedBooking.rooms.length})
                  </h3>
                  <div className="space-y-3">
                    {selectedBooking.rooms.map((room, idx) => (
                      <div
                        key={idx}
                        className="bg-slate-800/50 p-4 rounded-lg border border-slate-600"
                      >
                        <div className="flex justify-between items-start mb-2">
                          <p className="font-semibold text-[#FCD34D]">
                            {room.room_type_name}
                          </p>
                          <span className="bg-blue-500/30 text-blue-200 px-2 py-1 rounded text-xs font-bold">
                            ×{room.quantity}
                          </span>
                        </div>
                        <div className="grid grid-cols-2 gap-2 text-xs text-slate-400">
                          <p>₹{room.price_per_night}/night</p>
                          <p className="text-right">Base: ₹{room.base_amount}</p>
                        </div>
                        <div className="grid grid-cols-2 gap-2 text-xs text-slate-400 mt-1 pt-2 border-t border-slate-600">
                          <p>GST: ₹{room.gst_amount}</p>
                          <p className="text-right font-semibold text-[#E5C07B]">
                            Total: ₹{room.total_amount}
                          </p>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>

                {/* Stay Info Card */}
                <div className="bg-slate-700/50 border border-slate-600 p-5 rounded-xl">
                  <h3 className="text-lg font-bold text-[#E5C07B] mb-4">
                    📅 Stay Details
                  </h3>
                  <div className="grid grid-cols-3 gap-4 text-sm">
                    <div>
                      <p className="text-slate-400 text-xs mb-1">Check In</p>
                      <p className="font-semibold">
                        {selectedBooking.stay.check_in}
                      </p>
                    </div>
                    <div>
                      <p className="text-slate-400 text-xs mb-1">Check Out</p>
                      <p className="font-semibold">
                        {selectedBooking.stay.check_out}
                      </p>
                    </div>
                    <div>
                      <p className="text-slate-400 text-xs mb-1">Duration</p>
                      <p className="font-semibold text-[#E5C07B]">
                        {selectedBooking.stay.nights} nights
                      </p>
                    </div>
                  </div>
                </div>

                {/* Charges Card */}
                <div className="bg-gradient-to-br from-slate-700/50 to-slate-800/50 border border-[#E5C07B]/30 p-6 rounded-xl">
                  <h3 className="text-lg font-bold text-[#E5C07B] mb-4">
                    💰 Charges Breakdown
                  </h3>
                  <div className="space-y-3 text-sm">
                    <div className="flex justify-between">
                      <span className="text-slate-400">Room Base</span>
                      <span className="font-semibold">
                        ₹{selectedBooking.charges.room_base}
                      </span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-slate-400">Room GST (18%)</span>
                      <span className="font-semibold">
                        ₹{selectedBooking.charges.room_gst}
                      </span>
                    </div>
                    <div className="flex justify-between font-semibold border-t border-slate-600 pt-2">
                      <span>Room Total</span>
                      <span className="text-[#FCD34D]">
                        ₹{selectedBooking.charges.room_total}
                      </span>
                    </div>
                    <div className="flex justify-between text-slate-400 text-xs mt-3">
                      <span>Convenience Fee (2%)</span>
                      <span>₹{selectedBooking.charges.convenience_fee}</span>
                    </div>
                    <div className="flex justify-between text-slate-400 text-xs">
                      <span>Convenience GST (18%)</span>
                      <span>₹{selectedBooking.charges.convenience_gst}</span>
                    </div>
                    <div className="flex justify-between text-xl font-bold border-t-2 border-[#E5C07B] pt-4 mt-4 text-[#E5C07B]">
                      <span>Total Payable</span>
                      <span>₹{selectedBooking.charges.payable_amount}</span>
                    </div>
                  </div>
                </div>

              </div>
              {/* ── end scrollable content ── */}

              {/* Modal Footer — pinned outside scroll area */}
              <div className="border-t border-slate-700 p-6 flex-shrink-0 text-right bg-slate-900/50">
                <button
                  onClick={() => setSelectedBooking(null)}
                  className="bg-slate-700 hover:bg-slate-600 px-6 py-2 rounded-lg transition font-medium"
                >
                  Close Details
                </button>
              </div>

            </div>
          </div>
        )}

        {/* ── CANCEL CONFIRM MODAL ── */}
        {confirmId && (
          <div className="fixed inset-0 bg-black/70 backdrop-blur-sm flex items-center justify-center z-50 p-4">
            <div className="bg-gradient-to-br from-slate-800 to-slate-900 p-8 rounded-2xl w-full max-w-sm text-white border border-slate-700 shadow-2xl">
              <div className="text-red-400 text-3xl mb-4">⚠️</div>
              <h2 className="text-2xl font-bold mb-3 text-[#E5C07B]">
                Confirm Cancellation
              </h2>
              <p className="text-slate-300 mb-6">
                Are you sure you want to cancel booking{" "}
                <span className="font-bold text-[#FCD34D]">#{confirmId}</span>?
                This action cannot be undone.
              </p>
              <div className="flex justify-end gap-4">
                <button
                  onClick={() => setConfirmId(null)}
                  className="bg-slate-700 hover:bg-slate-600 px-6 py-2 rounded-lg font-medium transition"
                >
                  Keep Booking
                </button>
                <button
                  onClick={confirmCancel}
                  className="bg-red-600 hover:bg-red-700 px-6 py-2 rounded-lg font-semibold transition"
                >
                  Cancel Booking
                </button>
              </div>
            </div>
          </div>
        )}

        {/* ── TOAST NOTIFICATION ── */}
        {toast && (
          <div className="fixed top-6 right-6 z-50 animate-slide-in">
            <div
              className={`px-6 py-4 rounded-xl shadow-2xl font-medium backdrop-blur ${
                toast.type === "error"
                  ? "bg-red-600/90 text-white"
                  : "bg-green-600/90 text-white"
              }`}
            >
              {toast.type === "error" ? "❌" : "✅"} {toast.message}
            </div>
          </div>
        )}

      </div>
    </div>
  );
};

export default Bookings;