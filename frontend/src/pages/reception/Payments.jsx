import React, { useEffect, useState } from "react";
import { getAllPayments } from "../../api/payments";

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
      setPayments(data);
    } catch (err) {
      setError(err.message || "Failed to fetch payments");
      console.error("Error fetching payments:", err);
    } finally {
      setLoading(false);
    }
  };

  if (loading) {
    return (
      <div className="flex justify-center items-center h-96">
        <div className="animate-spin rounded-full h-16 w-16 border-b-4 border-[#E5C07B]"></div>
      </div>
    );
  }

  return (
    <div className="p-6 bg-gradient-to-br from-[#0F172A] to-[#111827] min-h-screen">
      <div className="max-w-7xl mx-auto">
        {/* Header */}
        <div className="flex justify-between items-center mb-8">
          <h1 className="text-3xl font-bold text-[#E5C07B]">Payments</h1>
          <button
            onClick={fetchPayments}
            className="bg-[#E5C07B] text-[#0F172A] px-6 py-2 rounded-lg font-semibold hover:bg-[#FCD34D] transition"
          >
            Refresh
          </button>
        </div>

        {/* Error Message */}
        {error && (
          <div className="bg-red-500/20 border border-red-500 text-red-400 px-4 py-3 rounded mb-6">
            {error}
          </div>
        )}

        {/* Empty State */}
        {payments.length === 0 && !error && (
          <div className="bg-[#1E293B] border border-[#E5C07B]/20 rounded-lg p-8 text-center">
            <p className="text-[#94A3B8]">No payments found</p>
          </div>
        )}

        {/* Payments Table */}
        {payments.length > 0 && (
          <div className="overflow-x-auto bg-[#1E293B] rounded-lg shadow-lg">
            <table className="w-full">
              <thead className="bg-[#E5C07B] text-[#0F172A]">
                <tr>
                  <th className="px-6 py-3 text-left font-semibold">Payment ID</th>
                  <th className="px-6 py-3 text-left font-semibold">Booking ID</th>
                  <th className="px-6 py-3 text-left font-semibold">Amount</th>
                  <th className="px-6 py-3 text-left font-semibold">Status</th>
                  <th className="px-6 py-3 text-left font-semibold">Gateway</th>
                  <th className="px-6 py-3 text-left font-semibold">Date</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[#334155]">
                {payments.map((payment) => (
                  <tr key={payment.payment_id} className="hover:bg-[#0F172A] transition">
                    <td className="px-6 py-4 text-[#E5C07B] font-semibold">
                      {payment.payment_id}
                    </td>
                    <td className="px-6 py-4 text-[#CBD5E1]">
                      {payment.booking_id}
                    </td>
                    <td className="px-6 py-4 text-[#CBD5E1]">
                      ₹{payment.amount}
                    </td>
                    <td className="px-6 py-4">
                      <span
                        className={`px-3 py-1 rounded-full text-sm font-semibold ${
                          payment.status === "paid"
                            ? "bg-green-500/20 text-green-400"
                            : payment.status === "failed"
                              ? "bg-red-500/20 text-red-400"
                              : "bg-yellow-500/20 text-yellow-400"
                        }`}
                      >
                        {payment.status}
                      </span>
                    </td>
                    <td className="px-6 py-4 text-[#CBD5E1]">
                      {payment.gateway || "N/A"}
                    </td>
                    <td className="px-6 py-4 text-[#CBD5E1]">
                      {new Date(payment.created_at).toLocaleDateString()} {new Date(payment.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
};

export default Payments;
