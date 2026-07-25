import React, { useState } from "react";
import { Link } from "react-router-dom";
import { jwtDecode } from "jwt-decode";
import { FaBroom, FaWrench, FaClipboardList } from "react-icons/fa";
import RaiseTicketModal from "../../components/RaiseTicketModal";

function role() {
  try {
    return jwtDecode(localStorage.getItem("adminToken") || "").role || "";
  } catch {
    return "";
  }
}

const Tile = ({ to, onClick, icon, title, subtitle }) => {
  const inner = (
    <div className="bg-gradient-to-r from-slate-800/60 to-slate-700/60 border border-slate-700 rounded-2xl p-6 shadow-xl h-full group-hover:scale-105 transition-transform">
      <div className="text-4xl text-[#E5C07B] mb-3">{icon}</div>
      <div className="text-lg font-bold">{title}</div>
      <div className="text-slate-400 text-sm">{subtitle}</div>
    </div>
  );
  return to ? (
    <Link to={to} className="group block">
      {inner}
    </Link>
  ) : (
    <button onClick={onClick} className="group block text-left w-full">
      {inner}
    </button>
  );
};

export default function StaffHome() {
  const r = role();
  const [showRaise, setShowRaise] = useState(false);

  return (
    <div>
      <h1 className="text-3xl font-bold mb-1 bg-gradient-to-r from-[#E5C07B] to-[#FCD34D] bg-clip-text text-transparent">
        {r === "maintenance" ? "Maintenance" : "Housekeeping"}
      </h1>
      <p className="text-slate-400 mb-6">Welcome — pick a task below.</p>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-5">
        {r === "maintenance" ? (
          <Tile to="/staff/tickets" icon={<FaClipboardList />} title="My Tickets" subtitle="Jobs assigned to you" />
        ) : (
          <>
            <Tile to="/staff/rooms" icon={<FaBroom />} title="My Rooms" subtitle="Clean & inspect rooms" />
            <Tile onClick={() => setShowRaise(true)} icon={<FaWrench />} title="Raise Ticket" subtitle="Report a maintenance issue" />
          </>
        )}
      </div>

      <RaiseTicketModal open={showRaise} onClose={() => setShowRaise(false)} onRaised={() => {}} />
    </div>
  );
}
