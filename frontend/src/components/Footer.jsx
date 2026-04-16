import React from "react";
import { Link } from "react-router-dom";
import { FaFacebook, FaTwitter, FaWhatsapp, FaLinkedin } from "react-icons/fa";

const Footer = () => {
  return (
    <footer className="bg-[#0F172A] text-[#E5C07B] mt-24">
      <div className="max-w-7xl mx-auto px-6 py-12 text-center">
        
        {/* Links Section */}
        <div className="mb-8 flex justify-center gap-6 flex-wrap text-sm">
          <Link to="/" className="hover:text-[#FCD34D] transition">Home</Link>
          <Link to="/about-us" className="hover:text-[#FCD34D] transition">About Us</Link>
          <Link to="/contact-us" className="hover:text-[#FCD34D] transition">Contact Us</Link>
          <span className="text-[#6B7280]">|</span>
          <Link to="/terms-and-conditions" className="hover:text-[#FCD34D] transition">Terms & Conditions</Link>
          <Link to="/privacy-policy" className="hover:text-[#FCD34D] transition">Privacy Policy</Link>
          <Link to="/refund-policy" className="hover:text-[#FCD34D] transition">Refund Policy</Link>
          <Link to="/cancellation-policy" className="hover:text-[#FCD34D] transition">Cancellation Policy</Link>
        </div>
        
        <p className="mb-6 text-sm tracking-wide">
          &copy; 2026 Hotel Bhimas. All rights reserved.
        </p>

        <div className="flex justify-center space-x-8 text-2xl">
          {/* Facebook */}
          <a
            className="hover:text-[#FCD34D] transition"
            href="https://www.facebook.com/bhimashotels/"
            target="_blank"
            rel="noopener noreferrer"
            aria-label="Facebook"
          >
            <FaFacebook />
          </a>

          {/* Twitter / X */}
          <a
            className="hover:text-[#FCD34D] transition"
            href="https://x.com/hotelbhimas"
            target="_blank"
            rel="noopener noreferrer"
            aria-label="Twitter"
          >
            <FaTwitter />
          </a>

          {/* WhatsApp */}
          <a
            className="hover:text-[#FCD34D] transition"
            href="https://wa.me/919347172758" // replace with hotel number
            target="_blank"
            rel="noopener noreferrer"
            aria-label="WhatsApp"
          >
            <FaWhatsapp />
          </a>

          {/* LinkedIn */}
          <a
            className="hover:text-[#FCD34D] transition"
            href="https://www.linkedin.com/in/hotelbhimas/?originalSubdomain=in"
            target="_blank"
            rel="noopener noreferrer"
            aria-label="LinkedIn"
          >
            <FaLinkedin />
          </a>
        </div>

      </div>
    </footer>
  );
};

export default Footer;
