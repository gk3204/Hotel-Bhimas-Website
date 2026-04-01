import React, { useEffect, useState } from "react";
import { getAllEnquiries, updateEnquiryStatus } from "../../api/enquiry";
import { FaSync } from "react-icons/fa";

const Enquiries = () => {
  const [enquiries, setEnquiries] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [selectedStatus, setSelectedStatus] = useState({});
  const [updating, setUpdating] = useState(null);

  useEffect(() => {
    fetchEnquiries();
  }, []);

  const fetchEnquiries = async () => {
    try {
      setLoading(true);
      const data = await getAllEnquiries();
      setEnquiries(data);
      setError(null);
    } catch (err) {
      setError(err.message || "Failed to load enquiries");
      setEnquiries([]);
    } finally {
      setLoading(false);
    }
  };

  const handleStatusChange = async (enquiryId, newStatus) => {
    try {
      setUpdating(enquiryId);
      await updateEnquiryStatus(enquiryId, newStatus);
      
      // Update local state
      setEnquiries(enquiries.map(e => 
        e.enquiry_id === enquiryId ? { ...e, status: newStatus } : e
      ));
      
      setSelectedStatus({ ...selectedStatus, [enquiryId]: newStatus });
    } catch (err) {
      alert("Failed to update status: " + err.message);
    } finally {
      setUpdating(null);
    }
  };

  const getStatusColor = (status) => {
    switch (status) {
      case "replied":
        return "bg-green-500/20 text-green-300 border-green-500/30";
      case "spam":
        return "bg-red-500/20 text-red-300 border-red-500/30";
      case "pending":
      default:
        return "bg-yellow-500/20 text-yellow-300 border-yellow-500/30";
    }
  };

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-900 via-slate-800 to-slate-900 text-white p-6">
      <div className="max-w-7xl mx-auto">
        {/* Header */}
        <div className="mb-8 flex justify-between items-start">
          <div>
            <h1 className="text-4xl font-bold mb-2 bg-gradient-to-r from-[#E5C07B] to-[#FCD34D] bg-clip-text text-transparent">
              💬 Contact Enquiries
            </h1>
            <p className="text-slate-400">Manage and respond to guest inquiries</p>
          </div>
          <button
            onClick={fetchEnquiries}
            className="bg-slate-700 hover:bg-slate-600 px-6 py-3 rounded-lg font-semibold transition flex items-center gap-2"
          >
            <FaSync size={16} /> Refresh
          </button>
        </div>

        {/* Error Message */}
        {error && (
          <div className="bg-red-500/20 border border-red-500/50 text-red-300 px-6 py-4 rounded-xl mb-8 flex items-center justify-between">
            <span>❌ {error}</span>
            <button
              onClick={fetchEnquiries}
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
              <p className="text-slate-400 font-medium">Loading enquiries...</p>
            </div>
          </div>
        ) : enquiries.length === 0 ? (
          <div className="bg-gradient-to-r from-slate-800/50 to-slate-700/50 border border-slate-700 rounded-2xl p-12 text-center backdrop-blur">
            <div className="text-5xl mb-4">📭</div>
            <p className="text-slate-300 text-lg font-medium">No enquiries found</p>
          </div>
        ) : (
          <div className="grid gap-6">
            {enquiries.map((enquiry) => (
              <div
                key={enquiry.enquiry_id}
                className="bg-gradient-to-r from-slate-800/50 to-slate-700/50 border border-slate-700 rounded-2xl p-6 hover:border-[#E5C07B]/50 hover:shadow-xl transition-all backdrop-blur"
              >
                {/* Card Header */}
                <div className="flex justify-between items-start mb-6">
                  <div>
                    <h3 className="text-2xl font-bold text-[#E5C07B] mb-1">
                      {enquiry.name}
                    </h3>
                    <p className="text-sm text-slate-400">Enquiry ID: #{enquiry.enquiry_id}</p>
                  </div>
                  <select
                    value={enquiry.status || "pending"}
                    onChange={(e) =>
                      handleStatusChange(enquiry.enquiry_id, e.target.value)
                    }
                    disabled={updating === enquiry.enquiry_id}
                    className={`px-4 py-2 rounded-lg font-semibold text-sm border ${getStatusColor(
                      enquiry.status
                    )} bg-slate-900/50 cursor-pointer disabled:opacity-50 transition`}
                  >
                    <option value="pending">⏳ Pending</option>
                    <option value="replied">✅ Replied</option>
                    <option value="spam">🚫 Spam</option>
                  </select>
                </div>

                {/* Contact Info */}
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-6 pb-6 border-b border-slate-700">
                  <div>
                    <p className="text-xs font-semibold text-slate-400 mb-1">📧 Email</p>
                    <a
                      href={`mailto:${enquiry.email}`}
                      className="text-[#E5C07B] hover:text-[#FCD34D] break-all font-medium transition"
                    >
                      {enquiry.email}
                    </a>
                  </div>
                  <div>
                    <p className="text-xs font-semibold text-slate-400 mb-1">📱 Phone</p>
                    <a
                      href={`tel:${enquiry.phone}`}
                      className="text-[#E5C07B] hover:text-[#FCD34D] font-medium transition"
                    >
                      {enquiry.phone}
                    </a>
                  </div>
                </div>

                {/* Message */}
                <div className="mb-4">
                  <p className="text-xs font-semibold text-slate-400 mb-2">💭 Message</p>
                  <p className="text-slate-300 bg-slate-900/50 p-4 rounded-lg border border-slate-600">
                    {enquiry.message}
                  </p>
                </div>

                {/* Footer - Date */}
                <div className="text-xs text-slate-500 pt-3 border-t border-slate-700">
                  📅 Received on {new Date(enquiry.created_at).toLocaleDateString()}{" "}
                  at{" "}
                  {new Date(enquiry.created_at).toLocaleTimeString([], {
                    hour: "2-digit",
                    minute: "2-digit",
                  })}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
};

export default Enquiries;
