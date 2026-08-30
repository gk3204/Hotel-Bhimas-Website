import React, { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { HiMenu, HiX } from "react-icons/hi";
import { jwtDecode } from "jwt-decode";
import logo from "../assets/logo-gold.svg";

// Role-aware staff header for the housekeeper/maintenance PWA.
const StaffHeader = () => {
  const [menuOpen, setMenuOpen] = useState(false);
  const navigate = useNavigate();

  let role = "";
  try {
    role = jwtDecode(localStorage.getItem("adminToken") || "").role || "";
  } catch {
    role = "";
  }

  const handleLogout = () => {
    localStorage.removeItem("adminToken");
    navigate("/admin-login");
  };

  // Per-role title + links. `roomservice` used to fall through to the housekeeper branch:
  // titled "Housekeeping", with a My Rooms link it has no business seeing (v4b8).
  // The housekeeper now also carries the merged supervisor duties, so it gets the admin
  // Housekeeping board — that is where a room is inspected and made re-sellable.
  const SHELLS = {
    maintenance: {
      title: "Maintenance",
      links: [
        { to: "/staff", label: "Home" },
        { to: "/staff/tickets", label: "My Tickets" },
      ],
    },
    roomservice: {
      title: "Room Service",
      links: [
        { to: "/staff", label: "Home" },
        { to: "/staff/room-service", label: "Orders" },
      ],
    },
    housekeeper: {
      title: "Housekeeping",
      links: [
        { to: "/staff", label: "Home" },
        { to: "/staff/rooms", label: "My Rooms" },
        { to: "/admin/housekeeping", label: "Board" },
      ],
    },
  };
  const shell = SHELLS[role] || SHELLS.housekeeper;
  const links = shell.links;

  return (
    <>
      <header className="sticky top-0 z-50 bg-[#0F172A] shadow-md">
        <div className="max-w-3xl mx-auto px-4 py-3 flex justify-between items-center">
          <div className="flex items-center space-x-3">
            <img src={logo} alt="Logo" className="h-9 w-auto" />
            <h1 className="text-lg font-bold text-[#E5C07B]">
              {shell.title}
            </h1>
          </div>

          <nav className="hidden md:flex space-x-6 text-[#E5C07B] font-medium items-center">
            {links.map((l) => (
              <Link key={l.to} to={l.to} className="hover:text-[#FCD34D]">
                {l.label}
              </Link>
            ))}
            <button onClick={handleLogout} className="bg-red-600 px-4 py-1 rounded text-white">
              Logout
            </button>
          </nav>

          <button onClick={() => setMenuOpen(true)} className="md:hidden text-[#E5C07B] text-3xl">
            <HiMenu />
          </button>
        </div>
      </header>

      <div
        onClick={() => setMenuOpen(false)}
        className={`fixed inset-0 bg-black/50 z-40 transition-opacity duration-300 ${
          menuOpen ? "opacity-100 visible" : "opacity-0 invisible"
        }`}
      />

      <div
        className={`fixed top-0 right-0 h-full w-64 bg-[#0F172A] z-50 transform transition-transform duration-300 ${
          menuOpen ? "translate-x-0" : "translate-x-full"
        }`}
      >
        <div className="flex justify-between items-center px-6 py-4 border-b border-[#E5C07B]/20">
          <span className="text-[#E5C07B] font-semibold text-lg">Menu</span>
          <button onClick={() => setMenuOpen(false)} className="text-[#E5C07B] text-2xl">
            <HiX />
          </button>
        </div>
        <nav className="flex flex-col px-6 py-6 space-y-6 text-[#E5C07B] font-medium">
          {links.map((l) => (
            <Link key={l.to} to={l.to} onClick={() => setMenuOpen(false)}>
              {l.label}
            </Link>
          ))}
          <button onClick={handleLogout} className="bg-red-600 px-4 py-2 rounded text-white mt-4">
            Logout
          </button>
        </nav>
      </div>
    </>
  );
};

export default StaffHeader;
