import React from "react";
import { Link } from "react-router-dom";
import { FaCalendarAlt, FaDoorOpen, FaMoneyBillWave, FaComments } from "react-icons/fa";

const ReceptionDashboard = () => {
  const options = [
    {
      icon: FaCalendarAlt,
      label: "Bookings",
      description: "Manage guest bookings",
      color: "from-blue-500 to-blue-600",
      link: "/reception/bookings",
    },
    {
      icon: FaDoorOpen,
      label: "Room Availability",
      description: "Check room status",
      color: "from-green-500 to-green-600",
      link: "/reception/availability",
    },
    {
      icon: FaMoneyBillWave,
      label: "Payments",
      description: "View payment records",
      color: "from-purple-500 to-purple-600",
      link: "/reception/payments",
    },
    {
      icon: FaComments,
      label: "Enquiries",
      description: "Manage guest enquiries",
      color: "from-orange-500 to-orange-600",
      link: "/reception/enquiries",
    },
  ];

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-900 via-slate-800 to-slate-900 text-white p-6">
      <div className="max-w-7xl mx-auto">
        {/* Header */}
        <div className="mb-12">
          <h1 className="text-5xl font-bold mb-3 bg-gradient-to-r from-[#E5C07B] to-[#FCD34D] bg-clip-text text-transparent">
            🏨 Reception Dashboard
          </h1>
          <p className="text-slate-400 text-lg">Manage daily operations and guest interactions</p>
        </div>

        {/* Quick Links Grid */}
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
          {options.map((option, idx) => {
            const Icon = option.icon;
            return (
              <Link
                key={idx}
                to={option.link}
                className="group cursor-pointer"
              >
                <div className={`bg-gradient-to-br ${option.color} p-0.5 rounded-2xl shadow-xl hover:shadow-2xl transition-all duration-300 group-hover:scale-105`}>
                  <div className="bg-gradient-to-br from-slate-800 to-slate-900 p-6 rounded-2xl backdrop-blur">
                    <div className="flex items-center justify-between mb-4">
                      <div className={`bg-gradient-to-br ${option.color} p-3 rounded-xl`}>
                        <Icon className="text-white text-2xl" />
                      </div>
                      <span className="text-[#E5C07B] group-hover:text-[#FCD34D] transition">→</span>
                    </div>
                    <h3 className="text-lg font-bold text-white mb-1 group-hover:text-[#E5C07B] transition">{option.label}</h3>
                    <p className="text-sm text-slate-400 group-hover:text-slate-300 transition">{option.description}</p>
                  </div>
                </div>
              </Link>
            );
          })}
        </div>
      </div>
    </div>
  );
};

export default ReceptionDashboard;
