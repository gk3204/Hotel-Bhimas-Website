import React from "react";
import { Link } from "react-router-dom";

const ReceptionDashboard = () => {
  return (
    <div>
      <h2 className="text-2xl text-[#E5C07B] mb-6">
        Reception Dashboard
      </h2>

      <div className="flex gap-6">
        <Link
          to="/reception/bookings"
          className="bg-green-600 px-6 py-3 rounded"
        >
          Manage Bookings
        </Link>

        <Link
          to="/reception/availability"
          className="bg-blue-600 px-6 py-3 rounded"
        >
          Room Availability
        </Link>
      </div>
    </div>
  );
};

export default ReceptionDashboard;
