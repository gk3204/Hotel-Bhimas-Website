import React from "react";
import { Link } from "react-router-dom";
import { FaCalendarAlt, FaMoneyBillWave, FaUser, FaClipboardList } from "react-icons/fa";

const Dashboard = () => {
  const stats = [
    {
      icon: FaCalendarAlt,
      label: "Bookings",
      value: "View & Manage",
      color: "from-blue-500 to-blue-600",
      link: "/admin/bookings",
    },
    {
      icon: FaMoneyBillWave,
      label: "Payments",
      value: "Payment Details",
      color: "from-green-500 to-green-600",
      link: "/admin/payments",
    },
    {
      icon: FaUser,
      label: "Users",
      value: "User Management",
      color: "from-purple-500 to-purple-600",
      link: "/admin/users",
    },
    {
      icon: FaClipboardList,
      label: "Room Types",
      value: "Manage Rooms",
      color: "from-orange-500 to-orange-600",
      link: "/admin/room-types",
    },
  ];

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-900 via-slate-800 to-slate-900 text-white p-6">
      <div className="max-w-7xl mx-auto">
        {/* Header */}
        <div className="mb-12">
          <h1 className="text-5xl font-bold mb-3 bg-gradient-to-r from-[#E5C07B] to-[#FCD34D] bg-clip-text text-transparent">
            🏨 Admin Dashboard
          </h1>
          <p className="text-slate-400 text-lg">Welcome back! Manage your hotel operations</p>
        </div>

        {/* Quick Stats */}
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6 mb-12">
          {stats.map((stat, idx) => {
            const Icon = stat.icon;
            return (
              <Link
                key={idx}
                to={stat.link}
                className="group cursor-pointer"
              >
                <div className={`bg-gradient-to-br ${stat.color} p-0.5 rounded-2xl shadow-xl hover:shadow-2xl transition-all duration-300 group-hover:scale-105`}>
                  <div className="bg-gradient-to-br from-slate-800 to-slate-900 p-6 rounded-2xl backdrop-blur">
                    <div className="flex items-center justify-between mb-4">
                      <div className={`bg-gradient-to-br ${stat.color} p-3 rounded-xl`}>
                        <Icon className="text-white text-2xl" />
                      </div>
                      <span className="text-[#E5C07B] group-hover:text-[#FCD34D] transition">→</span>
                    </div>
                    <h3 className="text-lg font-bold text-white mb-1 group-hover:text-[#E5C07B] transition">{stat.label}</h3>
                    <p className="text-sm text-slate-400 group-hover:text-slate-300 transition">{stat.value}</p>
                  </div>
                </div>
              </Link>
            );
          })}
        </div>

        {/* More Options */}
        <div className="bg-gradient-to-r from-slate-800/50 to-slate-700/50 border border-slate-700 rounded-2xl p-8 backdrop-blur">
          <h2 className="text-2xl font-bold text-[#E5C07B] mb-6">📋 Additional Options</h2>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <Link
              to="/admin/room-availability"
              className="group p-4 bg-slate-700/30 hover:bg-slate-700/50 rounded-xl border border-slate-600 hover:border-[#E5C07B]/50 transition"
            >
              <h3 className="font-semibold text-white mb-2 group-hover:text-[#E5C07B] transition">📅 Room Availability</h3>
              <p className="text-sm text-slate-400">Manage room availability and bookings</p>
            </Link>

            <Link
              to="/admin/enquiries"
              className="group p-4 bg-slate-700/30 hover:bg-slate-700/50 rounded-xl border border-slate-600 hover:border-[#E5C07B]/50 transition"
            >
              <h3 className="font-semibold text-white mb-2 group-hover:text-[#E5C07B] transition">💬 Enquiries</h3>
              <p className="text-sm text-slate-400">View and respond to guest enquiries</p>
            </Link>
          </div>
        </div>
      </div>
    </div>
  );
};

export default Dashboard;
