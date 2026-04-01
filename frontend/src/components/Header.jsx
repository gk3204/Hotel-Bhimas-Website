import React, { useState } from "react";
import { Link } from "react-router-dom";
import { HiMenu, HiX } from "react-icons/hi";
import logo from "../assets/logo-gold.svg";

const Header = () => {
  const [menuOpen, setMenuOpen] = useState(false);

  return (
    <>
      <header className="sticky top-0 z-50 bg-[#0F172A] shadow-md">
        <div className="max-w-7xl mx-auto px-6 py-4 flex justify-between items-center">

          {/* Logo */}
          <div className="flex items-center space-x-3">
            <img src={logo} alt="Hotel Bhimas Logo" className="h-12 w-auto" />
            <h1 className="text-2xl font-bold text-[#E5C07B] tracking-wide">
              Hotel Bhimas
            </h1>
          </div>

          {/* Desktop Nav */}
          <nav className="hidden md:flex space-x-8 font-medium text-[#E5C07B]">
            <Link to="/" className="hover:text-[#FCD34D] transition">Home</Link>
            <Link to="/rooms" className="hover:text-[#FCD34D] transition">Rooms</Link>
            <Link to="/booking" className="hover:text-[#FCD34D] transition">Booking</Link>
            <Link to="/facilities" className="hover:text-[#FCD34D] transition">Facilities</Link>
            <Link to="/restaurant" className="hover:text-[#FCD34D] transition">Restaurant</Link>
            <Link to="/about-us" className="hover:text-[#FCD34D] transition">About Us</Link>
            <Link to="/contact-us" className="hover:text-[#FCD34D] transition">Contact Us</Link>
            <Link to="/nearby-places" className="hover:text-[#FCD34D] transition">Nearby Places</Link>
            <Link to="/policies" className="hover:text-[#FCD34D] transition">Policies</Link>
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

      {/* Right Side Drawer */}
      <div
        className={`fixed top-0 right-0 h-full w-64 bg-[#0F172A] z-50
        transform transition-transform duration-300 ease-in-out
        ${menuOpen ? "translate-x-0" : "translate-x-full"}`}
      >
        <div className="flex justify-between items-center px-6 py-4 border-b border-[#E5C07B]/20">
          <span className="text-[#E5C07B] font-semibold text-lg">Menu</span>
          <button
            onClick={() => setMenuOpen(false)}
            className="text-[#E5C07B] text-2xl"
          >
            <HiX />
          </button>
        </div>

        <nav className="flex flex-col px-6 py-6 space-y-6 text-[#E5C07B] font-medium">
          <Link to="/" onClick={() => setMenuOpen(false)} className="hover:text-[#FCD34D]">
            Home
          </Link>
          <Link to="/rooms" onClick={() => setMenuOpen(false)} className="hover:text-[#FCD34D]">
            Rooms
          </Link>
          <Link to="/booking" onClick={() => setMenuOpen(false)} className="hover:text-[#FCD34D]">
            Booking
          </Link>
          <Link to="/facilities" onClick={() => setMenuOpen(false)} className="hover:text-[#FCD34D]">
            Facilities
          </Link>
          <Link to="/restaurant" onClick={() => setMenuOpen(false)} className="hover:text-[#FCD34D]">
            Restaurant
          </Link>
          <Link to="/about-us" onClick={() => setMenuOpen(false)} className="hover:text-[#FCD34D]">
            About Us
          </Link>
          <Link to="/contact-us" onClick={() => setMenuOpen(false)} className="hover:text-[#FCD34D]">
            Contact Us
          </Link>
          <Link to="/nearby-places" onClick={() => setMenuOpen(false)} className="hover:text-[#FCD34D]">
            Nearby Places
          </Link>
          <Link to="/policies" onClick={() => setMenuOpen(false)} className="hover:text-[#FCD34D]">
            Policies
          </Link>
        </nav>
      </div>
    </>
  );
};

export default Header;
