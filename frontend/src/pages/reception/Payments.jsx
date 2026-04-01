import React, { useEffect, useState } from "react";
import { getAllPayments } from "../../api/payments";
import { FaSync } from "react-icons/fa";

const Payments = () => {
  const [payments, setPayments] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    fetchPayments();
  }, []);

  const fetchPayments = async () => {
    try {
      setLoading(true);
      setError(null);
      const data = await getAllPayments();
      
      // Sort by created_at descending
      const sorted = [...data].sort((a, b) => {
        const dateA = new Date(a.created_at);
        const dateB = new Date(b.created_at);
        return dateB - dateA;
      });
      
      setPayments(sorted);
    } catch (err) {
      setError(err.message || "Failed to fetch payments");
      console.error("Error fetching payments:", err);
    } finally {
      setLoading(false);
    }
  };

  const getStatusColor = (status) => {
    switch (status) {
      case "paid":
        return "bg-green-500/20 text-green-300 border-green-500/30";
      case "failed":
        return "bg-red-500/20 text-red-300 border-red-500/30";
      case "pending":
        return "bg-yellow-500/20 text-yellow-300 border-yellow-500/30";
      default:
        return "bg-blue-500/20 text-blue-300 border-blue-500/30";
    }
  };

  const totalAmount = payments.reduce((sum, p) => sum + parseFloat(p.amount || 0), 0);
  const paidAmount = payments
    .filter(p => p.status === "paid")
    .reduce((sum, p) => sum + parseFloat(p.amount || 0), 0);

  if (loading) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-slate-900 via-slate-800 to-slate-900 flex items-center justify-center">
        <div className="text-center">
          <div className="animate-spin mb-4 mx-auto inline-block">
            <div className="h-12 w-12 border-4 border-[#E5C07B] border-t-[#D4AF37] rounded-full"></div>
          </div>
          <p className="text-slate-400 font-medium">Loading payments...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="p-6 bg-gradient-to-br from-slate-900 via-slate-800 to-slate-900 min-h-screen">
      <div className="max-w-7xl mx-auto">
        {/* Header */}
        <div className="mb-8">
          <h1 className="text-4xl font-bold mb-2 bg-gradient-to-r from-[#E5C07B] to-[#FCD34D] bg-clip-text text-transparent">
            💳 Payments
          </h1>
          <p className="text-slate-400">View all payment transactions</p>
        </div>

        {/* Stats Cards */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-6 mb-8">
          <div className="bg-gradient-to-br from-slate-800/50 to-slate-700/50 border border-slate-700 p-6 rounded-2xl">
            <p className="text-slate-400 text-sm font-medium mb-2">Total Transactions</p>
            <p className="text-3xl font-bold text-white">{payments.length}</p>
          </div>
          <div className="bg-gradient-to-br from-green-500/10 to-green-600/10 border border-green-500/30 p-6 rounded-2xl">
            <p className="text-green-400 text-sm font-medium mb-2">Successfully Paid</p>
            <p className="text-3xl font-bold text-green-300">₹{paidAmount.toFixed(2)}</p>
          </div>
          <div className="bg-gradient-to-br from-blue-500/10 to-blue-600/10 border border-blue-500/30 p-6 rounded-2xl">
            <p className="text-blue-400 text-sm font-medium mb-2">Total Amount</p>
            <p className="text-3xl font-bold text-blue-300">₹{totalAmount.toFixed(2)}</p>
          </div>
        </div>

        {/* Error Message */}
        {error && (
          <div className="bg-red-500/20 border border-red-500/50 text-red-300 px-6 py-4 rounded-xl mb-8 flex items-center justify-between">
            <span>❌ {error}</span>
            <button
              onClick={fetchPayments}
              className="bg-red-600 hover:bg-red-700 px-4 py-2 rounded-lg transition font-medium text-sm"
            >
              Retry
            </button>
          </div>
        )}

        {/* Empty State */}
        {payments.length === 0 && !error && (
          <div className="bg-gradient-to-r from-slate-800/50 to-slate-700/50 border border-slate-700 rounded-2xl p-12 text-center backdrop-blur">
            <div className="text-5xl mb-4">📭</div>
            <p className="text-slate-300 text-lg font-medium">No payments found</p>
          </div>
        )}

        {/* Payments Table */}
        {payments.length > 0 && (
          <div className="bg-gradient-to-r from-slate-800/50 to-slate-700/50 border border-slate-700 rounded-2xl shadow-xl overflow-hidden backdrop-blur">
            {/* Header with Refresh */}
            <div className="p-6 border-b border-slate-700 flex items-center justify-between">
              <h2 className="text-xl font-semibold">📊 Payment Transactions</h2>
              <button
                onClick={fetchPayments}
                className="bg-slate-700 hover:bg-slate-600 px-4 py-2 rounded-lg text-white font-medium transition flex items-center gap-2"
              >
                <FaSync size={14} /> Refresh
              </button>
            </div>

            {/* Table */}
            <div className="overflow-x-auto max-h-[600px] overflow-y-auto">
              <table className="w-full">
                <thead className="bg-slate-900/80 sticky top-0 border-b border-slate-700">
                  <tr>
                    <th className="px-6 py-4 text-left font-semibold text-sm">Payment ID</th>
                    <th className="px-6 py-4 text-left font-semibold text-sm">Booking ID</th>
                    <th className="px-6 py-4 text-left font-semibold text-sm">Amount</th>
                    <th className="px-6 py-4 text-left font-semibold text-sm">Status</th>
                    <th className="px-6 py-4 text-left font-semibold text-sm">Gateway</th>
                    <th className="px-6 py-4 text-left font-semibold text-sm">Date & Time</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-700">
                  {payments.map((payment) => (
                    <tr key={payment.payment_id} className="hover:bg-slate-700/30 transition">
                      <td className="px-6 py-4 text-[#FCD34D] font-mono font-semibold">
                        {payment.payment_id}
                      </td>
                      <td className="px-6 py-4 text-slate-300 font-semibold">
                        #{payment.booking_id}
                      </td>
                      <td className="px-6 py-4 text-[#E5C07B] font-bold">
                        ₹{parseFloat(payment.amount).toFixed(2)}
                      </td>
                      <td className="px-6 py-4">
                        <span className={`px-3 py-1 rounded-full text-xs font-semibold border ${getStatusColor(payment.status)}`}>
                          {payment.status?.toUpperCase() || "PENDING"}
                        </span>
                      </td>
                      <td className="px-6 py-4 text-slate-300">
                        {payment.gateway || "—"}
                      </td>
                      <td className="px-6 py-4 text-slate-400">
                        <div>{new Date(payment.created_at).toLocaleDateString()}</div>
                        <div className="text-xs">{new Date(payment.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};

export default Payments;
