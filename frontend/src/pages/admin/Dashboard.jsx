import React from "react";
import { Link } from "react-router-dom";

const Dashboard = () => {
  return (
    <div className="p-8">
      <h2 className="text-2xl font-bold mb-4">Admin Dashboard</h2>
      <div className="flex gap-4">
        <Link
          to="/admin/bookings"
          className="bg-green-600 text-white px-4 py-2 rounded"
        >
          View Bookings
        </Link>
        <Link
          to="/admin/payments"
          className="bg-yellow-600 text-white px-4 py-2 rounded"
        >
          View Payments
        </Link>
      </div>
    </div>
  );
};

export default Dashboard;
