import React, { useEffect, useState } from "react";
import { getAllEnquiries, updateEnquiryStatus } from "../../api/enquiry";

const Enquiries = () => {
  const [enquiries, setEnquiries] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [updating, setUpdating] = useState(null);

  useEffect(() => {
    fetchEnquiries();
  }, []);

  const fetchEnquiries = async () => {
    try {
      setLoading(true);
      setError(null);
      const data = await getAllEnquiries();
      setEnquiries(data);
    } catch (err) {
      setError(err.message || "Failed to fetch enquiries");
      console.error("Error fetching enquiries:", err);
    } finally {
      setLoading(false);
    }
  };

  const handleStatusChange = async (enquiryId, newStatus) => {
    try {
      setUpdating(enquiryId);
      await updateEnquiryStatus(enquiryId, newStatus);
      setEnquiries(
        enquiries.map((e) =>
          e.enquiry_id === enquiryId ? { ...e, status: newStatus } : e
        )
      );
    } catch (err) {
      setError("Failed to update status");
      console.error("Error updating status:", err);
    } finally {
      setUpdating(null);
    }
  };

  const getStatusColor = (status) => {
    switch (status) {
      case "replied":
        return "bg-green-500/20 text-green-400";
      case "spam":
        return "bg-red-500/20 text-red-400";
      default:
        return "bg-yellow-500/20 text-yellow-400";
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
          <h1 className="text-3xl font-bold text-[#E5C07B]">Enquiries</h1>
          <button
            onClick={fetchEnquiries}
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
        {enquiries.length === 0 && !error && (
          <div className="bg-[#1E293B] border border-[#E5C07B]/20 rounded-lg p-8 text-center">
            <p className="text-[#94A3B8]">No enquiries found</p>
          </div>
        )}

        {/* Enquiries Cards */}
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {enquiries.map((enquiry) => (
            <div
              key={enquiry.enquiry_id}
              className="bg-[#1E293B] border border-[#E5C07B]/20 rounded-lg p-6 hover:border-[#E5C07B]/50 transition"
            >
              {/* Header */}
              <div className="flex justify-between items-start mb-4">
                <div>
                  <h3 className="text-lg font-bold text-[#E5C07B]">
                    {enquiry.name}
                  </h3>
                  <p className="text-sm text-[#94A3B8]">
                    ID: {enquiry.enquiry_id}
                  </p>
                </div>
              </div>

              {/* Status Dropdown */}
              <div className="mb-4">
                <select
                  value={enquiry.status || "pending"}
                  onChange={(e) =>
                    handleStatusChange(enquiry.enquiry_id, e.target.value)
                  }
                  disabled={updating === enquiry.enquiry_id}
                  className={`w-full px-3 py-2 rounded-lg font-semibold text-sm ${getStatusColor(enquiry.status)} bg-[#0F172A] border border-current disabled:opacity-50`}
                >
                  <option value="pending">Pending</option>
                  <option value="replied">Replied</option>
                  <option value="spam">Spam</option>
                </select>
              </div>

              {/* Contact Info */}
              <div className="mb-4 grid grid-cols-2 gap-2">
                <a
                  href={`mailto:${enquiry.email}`}
                  className="text-[#E5C07B] hover:text-[#FCD34D] text-sm underline break-all"
                >
                  {enquiry.email}
                </a>
                <a
                  href={`tel:${enquiry.phone}`}
                  className="text-[#E5C07B] hover:text-[#FCD34D] text-sm"
                >
                  {enquiry.phone}
                </a>
              </div>

              {/* Message */}
              <div className="mb-4">
                <p className="text-[#CBD5E1] text-sm bg-[#0F172A] p-3 rounded">
                  {enquiry.message}
                </p>
              </div>

              {/* Received Date */}
              <div className="text-xs text-[#64748B]">
                Received: {new Date(enquiry.created_at).toLocaleDateString()}{" "}
                {new Date(enquiry.created_at).toLocaleTimeString([], {
                  hour: "2-digit",
                  minute: "2-digit",
                })}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};

export default Enquiries;
