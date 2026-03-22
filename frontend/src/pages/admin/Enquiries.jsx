import React, { useEffect, useState } from "react";
import { getAllEnquiries, updateEnquiryStatus } from "../../api/enquiry";

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
        return "bg-green-100 text-green-800";
      case "spam":
        return "bg-red-100 text-red-800";
      case "pending":
      default:
        return "bg-yellow-100 text-yellow-800";
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <h1 className="text-3xl font-bold text-gray-800">Contact Enquiries</h1>
        <button
          onClick={fetchEnquiries}
          className="bg-blue-600 text-white px-4 py-2 rounded-lg hover:bg-blue-700"
        >
          Refresh
        </button>
      </div>

      {error && (
        <div className="bg-red-100 border border-red-400 text-red-700 px-4 py-3 rounded">
          {error}
        </div>
      )}

      {loading ? (
        <div className="text-center py-12">
          <div className="inline-block animate-spin">
            <div className="w-8 h-8 border-4 border-blue-600 border-t-transparent rounded-full"></div>
          </div>
          <p className="mt-4 text-gray-600">Loading enquiries...</p>
        </div>
      ) : enquiries.length === 0 ? (
        <div className="bg-gray-100 rounded-lg p-8 text-center">
          <p className="text-gray-600 text-lg">No enquiries found</p>
        </div>
      ) : (
        <div className="grid gap-6">
          {enquiries.map((enquiry) => (
            <div
              key={enquiry.enquiry_id}
              className="bg-white rounded-lg shadow-lg p-6 border-l-4 border-blue-500"
            >
              <div className="flex justify-between items-start mb-4">
                <div>
                  <h3 className="text-xl font-bold text-gray-800">
                    {enquiry.name}
                  </h3>
                  <p className="text-sm text-gray-600">ID: {enquiry.enquiry_id}</p>
                </div>
                <select
                  value={enquiry.status || "pending"}
                  onChange={(e) =>
                    handleStatusChange(enquiry.enquiry_id, e.target.value)
                  }
                  disabled={updating === enquiry.enquiry_id}
                  className={`px-3 py-2 rounded-lg font-semibold text-sm ${getStatusColor(
                    enquiry.status
                  )} border-0 cursor-pointer`}
                >
                  <option value="pending">Pending</option>
                  <option value="replied">Replied</option>
                  <option value="spam">Spam</option>
                </select>
              </div>

              <div className="grid grid-cols-2 gap-4 mb-4">
                <div>
                  <p className="text-sm text-gray-600">Email</p>
                  <a
                    href={`mailto:${enquiry.email}`}
                    className="text-blue-600 hover:underline break-all"
                  >
                    {enquiry.email}
                  </a>
                </div>
                <div>
                  <p className="text-sm text-gray-600">Phone</p>
                  <a
                    href={`tel:${enquiry.phone}`}
                    className="text-blue-600 hover:underline"
                  >
                    {enquiry.phone}
                  </a>
                </div>
              </div>

              <div className="mb-4">
                <p className="text-sm text-gray-600 mb-2">Message</p>
                <p className="text-gray-800 bg-gray-50 p-4 rounded-lg border border-gray-200">
                  {enquiry.message}
                </p>
              </div>

              <div className="text-xs text-gray-500">
                Received on{" "}
                {new Date(enquiry.created_at).toLocaleDateString()}{" "}
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
  );
};

export default Enquiries;
