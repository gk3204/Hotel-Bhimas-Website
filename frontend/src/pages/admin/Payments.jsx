import React, { useEffect, useMemo, useState } from "react";
import { getAllPayments } from "../../api/payments";
import { FaSync } from "react-icons/fa";
import { usePaged, Paginator } from "../../components/admin/Paginator";
import { PageShell } from "../../components/admin/BackofficeUI";

const filterInputCls =
  "px-4 py-2 bg-slate-900/50 border border-slate-600 rounded-lg text-white focus:outline-none focus:border-[#E5C07B] focus:ring-2 focus:ring-[#E5C07B]/20 transition";

const Payments = () => {
  const [payments, setPayments] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [statusFilter, setStatusFilter] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");

  const filtered = useMemo(
    () =>
      payments.filter((p) => {
        if (statusFilter && p.status !== statusFilter) return false;
        if (dateFrom && new Date(p.created_at) < new Date(`${dateFrom}T00:00:00`)) return false;
        if (dateTo && new Date(p.created_at) > new Date(`${dateTo}T23:59:59`)) return false;
        return true;
      }),
    [payments, statusFilter, dateFrom, dateTo]
  );
  const paged = usePaged(filtered, 25);

  useEffect(() => {
    fetchPayments();
  }, []);

  const fetchPayments = async () => {
    try {
      setLoading(true);
      const data = await getAllPayments();
      
      // Sort by created_at descending by default
      const sorted = [...data].sort((a, b) => {
        const dateA = new Date(a.created_at);
        const dateB = new Date(b.created_at);
        return dateB - dateA;
      });
      
      setPayments(sorted);
      setError(null);
    } catch (err) {
      setError(err.message || "Failed to load payments");
      setPayments([]);
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

  const getRefundStatusColor = (status) => {
    switch (status) {
      case "completed":
        return "bg-green-500/20 text-green-300 border-green-500/30";
      case "failed":
        return "bg-red-500/20 text-red-300 border-red-500/30";
      case "pending":
        return "bg-yellow-500/20 text-yellow-300 border-yellow-500/30";
      default:
        return "bg-slate-500/20 text-slate-300 border-slate-500/30";
    }
  };

  const totalAmount = payments.reduce((sum, p) => sum + parseFloat(p.amount || 0), 0);
  const paidAmount = payments
    .filter(p => p.status === "paid")
    .reduce((sum, p) => sum + parseFloat(p.amount || 0), 0);

  return (
    <PageShell icon="💰" title="Payments Management" subtitle="Track all payment transactions">

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

        {/* Filters */}
        <div className="bg-gradient-to-r from-slate-800/50 to-slate-700/50 border border-slate-700 p-5 rounded-2xl mb-6 backdrop-blur">
          <div className="grid grid-cols-1 md:grid-cols-4 gap-4 items-end">
            <div className="flex flex-col">
              <label className="mb-2 text-sm font-semibold text-slate-300">Status</label>
              <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)} className={filterInputCls}>
                <option value="">All statuses</option>
                <option value="paid">Paid</option>
                <option value="pending">Pending</option>
                <option value="failed">Failed</option>
                <option value="refunded">Refunded</option>
              </select>
            </div>
            <div className="flex flex-col">
              <label className="mb-2 text-sm font-semibold text-slate-300">From</label>
              <input type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} className={filterInputCls} />
            </div>
            <div className="flex flex-col">
              <label className="mb-2 text-sm font-semibold text-slate-300">To</label>
              <input type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)} className={filterInputCls} />
            </div>
            <button
              onClick={() => { setStatusFilter(""); setDateFrom(""); setDateTo(""); }}
              className="bg-slate-700 hover:bg-slate-600 text-white font-semibold px-4 py-2.5 rounded-lg transition"
            >
              Clear filters
            </button>
          </div>
          <p className="text-slate-500 text-xs mt-3">Showing {filtered.length} of {payments.length} transactions.</p>
        </div>

        {/* Error State */}
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

        {/* Loading State */}
        {loading ? (
          <div className="bg-gradient-to-r from-slate-800/50 to-slate-700/50 border border-slate-700 rounded-2xl p-12 flex items-center justify-center backdrop-blur">
            <div className="text-center">
              <div className="animate-spin mb-4 mx-auto inline-block">
                <div className="h-12 w-12 border-4 border-[#E5C07B] border-t-[#D4AF37] rounded-full"></div>
              </div>
              <p className="text-slate-400 font-medium">Loading payments...</p>
            </div>
          </div>
        ) : payments.length === 0 ? (
          <div className="bg-gradient-to-r from-slate-800/50 to-slate-700/50 border border-slate-700 rounded-2xl p-12 text-center backdrop-blur">
            <div className="text-5xl mb-4">📭</div>
            <p className="text-slate-300 text-lg font-medium">No payments found</p>
            <p className="text-slate-500 text-sm mt-2">No payment records yet</p>
          </div>
        ) : (
          <div className="bg-gradient-to-r from-slate-800/50 to-slate-700/50 border border-slate-700 rounded-2xl shadow-xl overflow-hidden backdrop-blur">
            {/* Header with Refresh */}
            <div className="p-6 border-b border-slate-700 flex items-center justify-between">
              <h2 className="text-xl font-semibold">📊 Payment Transactions</h2>
              <button
                onClick={fetchPayments}
                disabled={loading}
                className="bg-slate-700 hover:bg-slate-600 disabled:opacity-50 disabled:cursor-not-allowed px-4 py-2 rounded-lg text-white font-medium transition flex items-center gap-2"
              >
                <FaSync size={14} className={loading ? "animate-spin" : ""} /> {loading ? "Loading..." : "Refresh"}
              </button>
            </div>

            {/* Table */}
            <div className="overflow-x-auto max-h-[600px] overflow-y-auto">
              <table className="w-full text-left">
                <thead className="bg-slate-900/80 sticky top-0 border-b border-slate-700">
                  <tr>
                    <th className="px-6 py-4 font-semibold text-sm">Payment ID</th>
                    <th className="px-6 py-4 font-semibold text-sm">Booking ID</th>
                    <th className="px-6 py-4 font-semibold text-sm">Amount</th>
                    <th className="px-6 py-4 font-semibold text-sm">Status</th>
                    <th className="px-6 py-4 font-semibold text-sm">Refund ID</th>
                    <th className="px-6 py-4 font-semibold text-sm">Refund Amount</th>
                    <th className="px-6 py-4 font-semibold text-sm">Refund Status</th>
                    <th className="px-6 py-4 font-semibold text-sm">Gateway</th>
                    <th className="px-6 py-4 font-semibold text-sm">Date & Time</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-700">
                  {paged.pageItems.length === 0 && (
                    <tr><td colSpan={9} className="px-6 py-10 text-center text-slate-400">No payments match these filters.</td></tr>
                  )}
                  {paged.pageItems.map((payment) => (
                    <tr key={payment.payment_id} className="hover:bg-slate-700/30 transition">
                      <td className="px-6 py-4 text-sm font-mono text-[#FCD34D]">
                        {payment.payment_id}
                      </td>
                      <td className="px-6 py-4 text-sm font-semibold text-slate-300">
                        #{payment.booking_id}
                      </td>
                      <td className="px-6 py-4 text-sm font-bold text-[#E5C07B]">
                        ₹{parseFloat(payment.amount).toFixed(2)}
                      </td>
                      <td className="px-6 py-4 text-sm">
                        <span
                          className={`px-3 py-1 rounded-full text-xs font-semibold border ${getStatusColor(payment.status)}`}
                        >
                          {payment.status?.toUpperCase() || "PENDING"}
                        </span>
                      </td>
                      <td className="px-6 py-4 text-sm font-mono text-slate-400">
                        {payment.refund_id ? (
                          <span className="text-[#FCD34D]">{payment.refund_id}</span>
                        ) : (
                          <span className="text-slate-500">—</span>
                        )}
                      </td>
                      <td className="px-6 py-4 text-sm">
                        {payment.refund_amount ? (
                          <span className="text-green-400">₹{parseFloat(payment.refund_amount).toFixed(2)}</span>
                        ) : (
                          <span className="text-slate-500">—</span>
                        )}
                      </td>
                      <td className="px-6 py-4 text-sm">
                        {payment.refund_status ? (
                          <span
                            className={`px-3 py-1 rounded-full text-xs font-semibold border ${getRefundStatusColor(payment.refund_status)}`}
                          >
                            {payment.refund_status?.toUpperCase()}
                          </span>
                        ) : (
                          <span className="text-slate-500 text-xs">No Refund</span>
                        )}
                      </td>
                      <td className="px-6 py-4 text-sm text-slate-300">
                        {payment.gateway || "—"}
                      </td>
                      <td className="px-6 py-4 text-sm text-slate-400">
                        <div>
                          {new Date(payment.created_at).toLocaleDateString()}
                        </div>
                        <div className="text-xs">
                          {new Date(payment.created_at).toLocaleTimeString([], {
                            hour: "2-digit",
                            minute: "2-digit",
                          })}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <Paginator {...paged} />
          </div>
        )}
    </PageShell>
  );
};

export default Payments;
