import React, { useState, useEffect } from "react";
import { sendEnquiry } from "../../api/enquiry";
import ImageWithSpinner from "../../components/ImageWithSpinner";
import receptionImg from "./images/reception.png";

const ContactUs = () => {
    const [formData, setFormData] = useState({
    name: "",
    phone: "",
    email: "",
    subject: "",
    message: "",
  });

  const [status, setStatus] = useState("");

  useEffect(() => {
    window.scrollTo(0, 0);
  }, []);

  const handleChange = (e) => {
    setFormData({ ...formData, [e.target.name]: e.target.value });
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    setStatus("Sending...");

    try {
      await sendEnquiry(formData);
      setStatus("Enquiry sent successfully!");
      setFormData({ name: "", phone: "", email: "", subject: "", message: "" });
    } catch (err) {
      setStatus("Failed to send enquiry. Please try again.");
    }
  };

  return (
    <div className="min-h-screen bg-[#F8FAFC]">

      {/* ================= HERO ================= */}
     <section
        className="relative h-[70vh] flex items-center justify-center"
        style={{
            backgroundImage: `url(${receptionImg})`,
            backgroundSize: "cover",
            backgroundPosition: "center",
        }}
        >
        {/* Dark overlay */}
        <div className="absolute inset-0 bg-black/60"></div>

        {/* Content */}
        <div className="relative z-10 text-center px-6">
            <h1 className="text-4xl md:text-5xl font-bold text-white mb-4">
            Contact <span className="text-[#E5C07B]">Hotel Bhimas</span>
            </h1>
            <p className="text-white/80 max-w-2xl mx-auto text-lg">
            We’re here to assist you with room bookings, food enquiries,
            and a comfortable stay in Tirupati.
            </p>
        </div>
        </section>


      {/* ================= CONTACT INFO ================= */}
      <section className="max-w-7xl mx-auto px-6 mt-20">
        <div className="grid md:grid-cols-3 gap-8">

          {/* Phone */}
          <div className="bg-white rounded-2xl shadow-lg p-8 text-center">
            <div className="text-4xl mb-4">📞</div>
            <h3 className="text-xl font-bold mb-2">Call Us</h3>
            <p className="text-gray-600">
              <a href="tel:+919347172758" className="text-[#E5C07B] font-semibold block">
                +91 9347172758
              </a>
              <a href="tel:+918772225744" className="text-[#E5C07B] font-semibold block mt-1">
                +91 8772225744
              </a>
            </p>
          </div>

          {/* Email */}
          <div className="bg-white rounded-2xl shadow-lg p-8 text-center">
            <div className="text-4xl mb-4">📧</div>
            <h3 className="text-xl font-bold mb-2">Email</h3>
            <a
              href="mailto:hotelbhimas@gmail.com"
              className="text-[#E5C07B] font-semibold"
            >
              hotelbhimas@gmail.com
            </a>
          </div>

          {/* Address */}
          <div className="bg-white rounded-2xl shadow-lg p-8 text-center">
            <div className="text-4xl mb-4">📍</div>
            <h3 className="text-xl font-bold mb-2">Address</h3>
            <p className="text-gray-600">
              Hotel Bhimas,<br />
              42, G Car Street,<br />
              Near Tirupati Railway Station,<br />
              Tirupati, Andhra Pradesh – 517501
            </p>
          </div>

        </div>
      </section>

      {/* ================= WHATSAPP CTA ================= */}
      <section className="max-w-5xl mx-auto px-6 mt-16">
        <div className="bg-green-500 rounded-2xl p-8 text-center text-white shadow-lg">
          <h3 className="text-2xl font-bold mb-3">
            WhatsApp Us for Quick Enquiry
          </h3>
          <p className="mb-6">
            Fast responses for room availability, food timings & directions
          </p>
          <a
            href="https://wa.me/919347172758"
            target="_blank"
            rel="noopener noreferrer"
            className="bg-white text-green-600 px-8 py-3 rounded-full font-semibold hover:bg-green-100 transition"
          >
            Chat on WhatsApp
          </a>
        </div>
      </section>

      {/* ================= ENQUIRY FORM ================= */}
       <section className="max-w-5xl mx-auto px-6 mt-20">
        <div className="bg-white rounded-3xl shadow-xl p-10">
          <h2 className="text-3xl font-bold text-center mb-8">
            Send Us an Enquiry
          </h2>

          <form className="grid grid-cols-1 md:grid-cols-2 gap-6" onSubmit={handleSubmit}>
            <input
              type="text"
              name="name"
              placeholder="Full Name"
              value={formData.name}
              onChange={handleChange}
              className="border rounded-xl p-4 focus:outline-none focus:ring-2 focus:ring-[#E5C07B]"
              required
            />
            <input
              type="tel"
              name="phone"
              placeholder="Phone Number"
              value={formData.phone}
              onChange={handleChange}
              className="border rounded-xl p-4 focus:outline-none focus:ring-2 focus:ring-[#E5C07B]"
              required
            />
            <input
              type="email"
              name="email"
              placeholder="Email Address"
              value={formData.email}
              onChange={handleChange}
              className="border rounded-xl p-4 focus:outline-none focus:ring-2 focus:ring-[#E5C07B]"
              required
            />
            <input
              type="text"
              name="subject"
              placeholder="Subject"
              value={formData.subject}
              onChange={handleChange}
              className="border rounded-xl p-4 focus:outline-none focus:ring-2 focus:ring-[#E5C07B]"
            />

            <textarea
              name="message"
              placeholder="Your Message"
              rows="5"
              value={formData.message}
              onChange={handleChange}
              className="md:col-span-2 border rounded-xl p-4 focus:outline-none focus:ring-2 focus:ring-[#E5C07B]"
              required
            ></textarea>

            <button
              type="submit"
              className="md:col-span-2 bg-[#0F172A] text-[#E5C07B] py-3 rounded-full font-semibold hover:bg-[#1E293B] transition"
            >
              Submit Enquiry
            </button>
          </form>

          {status && <p className="mt-4 text-center text-[#E5C07B]">{status}</p>}
        </div>
      </section>

      {/* ================= GOOGLE MAP ================= */}
      <section className="max-w-7xl mx-auto px-6 mt-24 mb-24">
        <div className="rounded-3xl overflow-hidden shadow-xl">
          <iframe
            src="https://www.google.com/maps?q=13.6292025,79.4193486&hl=en&z=15&output=embed"
            className="w-full h-96 md:h-[500px] border-0"
            loading="lazy"
            referrerPolicy="no-referrer-when-downgrade"
          ></iframe>
        </div>
      </section>

    </div>
  );
};

export default ContactUs;
