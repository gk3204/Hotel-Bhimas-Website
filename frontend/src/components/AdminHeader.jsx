import React, { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { HiMenu, HiX } from "react-icons/hi";
import logo from "../assets/logo-gold.svg";

const AdminHeader = () => {
  const [menuOpen, setMenuOpen] = useState(false);
  const navigate = useNavigate();

  const handleLogout = () => {
    localStorage.removeItem("adminToken");
    navigate("/admin-login");
  };

  return (
    <>
      <header className="sticky top-0 z-50 bg-[#0F172A] shadow-md">
        <div className="max-w-7xl mx-auto px-6 py-4 flex justify-between items-center">

          {/* Logo + Title */}
          <div className="flex items-center space-x-3">
            <img src={logo} alt="Logo" className="h-10 w-auto" />
            <h1 className="text-xl font-bold text-[#E5C07B]">
              Admin Panel
            </h1>
          </div>

          {/* Desktop Nav */}
          <nav className="hidden md:flex space-x-6 text-[#E5C07B] font-medium">
            <Link to="/admin" className="hover:text-[#FCD34D]">Dashboard</Link>
            <Link to="/admin/room-types" className="hover:text-[#FCD34D]">Room Types</Link>
            <Link to="/admin/room-availability" className="hover:text-[#FCD34D]">Room Availability</Link>
            <Link to="/admin/user-check" className="hover:text-[#FCD34D]">Users</Link>
            <Link to="/admin/bookings" className="hover:text-[#FCD34D]">Bookings</Link>
            <Link to="/admin/payments" className="hover:text-[#FCD34D]">Payments</Link>
            <Link to="/admin/enquiries" className="hover:text-[#FCD34D]">Enquiries</Link>

            <button
              onClick={handleLogout}
              className="bg-red-600 px-4 py-1 rounded text-white"
            >
              Logout
            </button>
          </nav>

          {/* Hamburger */}
          <button
            onClick={() => setMenuOpen(true)}
            className="md:hidden text-[#E5C07B] text-3xl"
          >
            <HiMenu />
          </button>
        </div>
      </header>

      {/* Overlay */}
      <div
        onClick={() => setMenuOpen(false)}
        className={`fixed inset-0 bg-black/50 z-40 transition-opacity duration-300
        ${menuOpen ? "opacity-100 visible" : "opacity-0 invisible"}`}
      />

      {/* Drawer */}
      <div
        className={`fixed top-0 right-0 h-full w-64 bg-[#0F172A] z-50
        transform transition-transform duration-300
        ${menuOpen ? "translate-x-0" : "translate-x-full"}`}
      >
        <div className="flex justify-between items-center px-6 py-4 border-b border-[#E5C07B]/20">
          <span className="text-[#E5C07B] font-semibold text-lg">Admin Menu</span>
          <button
            onClick={() => setMenuOpen(false)}
            className="text-[#E5C07B] text-2xl"
          >
            <HiX />
          </button>
        </div>

        <nav className="flex flex-col px-6 py-6 space-y-6 text-[#E5C07B] font-medium">
          <Link to="/admin" onClick={() => setMenuOpen(false)}>Dashboard</Link>
          <Link to="/admin/room-type" onClick={() => setMenuOpen(false)}>Room Types</Link>
          <Link to="/admin/room-availability" onClick={() => setMenuOpen(false)}>Room Availability</Link>
          <Link to="/admin/user-check" onClick={() => setMenuOpen(false)}>Users</Link>
          <Link to="/admin/bookings" onClick={() => setMenuOpen(false)}>Bookings</Link>
          <Link to="/admin/payments" onClick={() => setMenuOpen(false)}>Payments</Link>
          <Link to="/admin/enquiries" onClick={() => setMenuOpen(false)}>Enquiries</Link>

          <button
            onClick={handleLogout}
            className="bg-red-600 px-4 py-2 rounded text-white mt-4"
          >
            Logout
          </button>
        </nav>
      </div>
    </>
  );
};

export default AdminHeader;
