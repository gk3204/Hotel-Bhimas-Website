import React from "react";
import { Link } from "react-router-dom";

const Policies = () => {
  const policies = [
    {
      title: "Terms and Conditions",
      description: "Important rules and regulations for booking and staying at Hotel Bhimas",
      icon: "📋",
      link: "/terms-and-conditions",
      color: "from-[#E5C07B] to-[#D4AF37]",
      textColor: "text-[#0F172A]"
    },
    {
      title: "Privacy Policy",
      description: "How we collect, use, and protect your personal information",
      icon: "🔒",
      link: "/privacy-policy",
      color: "from-blue-500 to-blue-600",
      textColor: "text-white"
    },
    {
      title: "Refund Policy",
      description: "Clear information about refunds, eligibility, and the refund process",
      icon: "💰",
      link: "/refund-policy",
      color: "from-green-500 to-green-600",
      textColor: "text-white"
    },
    {
      title: "Cancellation Policy",
      description: "Guidelines for cancelling bookings and no-show policies",
      icon: "❌",
      link: "/cancellation-policy",
      color: "from-red-500 to-red-600",
      textColor: "text-white"
    }
  ];

  return (
    <div className="min-h-screen bg-[#F8FAFC] px-6 py-20">
      <div className="max-w-5xl mx-auto">
        <div className="text-center mb-12">
          <h1 className="text-4xl md:text-5xl font-bold text-[#0F172A] mb-4">
            Hotel Bhimas Policies
          </h1>
          <p className="text-gray-600 text-lg">
            Please read our policies carefully before booking with us
          </p>
        </div>

        {/* Policy Cards Grid */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          {policies.map((policy) => (
            <Link
              key={policy.title}
              to={policy.link}
              className="group"
            >
              <div className={`bg-gradient-to-br ${policy.color} rounded-2xl shadow-lg p-8 h-full transform transition hover:shadow-xl hover:-translate-y-1`}>
                <div className="text-5xl mb-4">{policy.icon}</div>
                <h2 className={`text-2xl font-bold mb-3 ${policy.textColor}`}>
                  {policy.title}
                </h2>
                <p className={`${policy.textColor === "text-white" ? "text-gray-100" : "text-gray-700"} mb-4`}>
                  {policy.description}
                </p>
                <div className="inline-block px-4 py-2 rounded-lg font-semibold transition bg-white text-[#0F172A] group-hover:bg-gray-100">
                  Read Full Policy →
                </div>
              </div>
            </Link>
          ))}
        </div>

        {/* Information Section */}
        <div className="bg-white rounded-2xl shadow-lg p-8 md:p-12 mt-12">
          <h2 className="text-3xl font-bold text-[#0F172A] mb-6 text-center">
            Questions About Our Policies?
          </h2>
          <p className="text-gray-700 text-center mb-6 text-lg">
            If you have any questions or concerns about our policies, please don't hesitate to reach out to us.
          </p>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-6 text-center">
            <div>
              <p className="text-2xl mb-2">📧</p>
              <p className="font-semibold text-[#0F172A] mb-2">Email</p>
              <a
                href="mailto:hotelbhimas@gmail.com"
                className="text-[#E5C07B] hover:text-[#D4AF37] font-semibold transition"
              >
                hotelbhimas@gmail.com
              </a>
            </div>
            <div>
              <p className="text-2xl mb-2">📞</p>
              <p className="font-semibold text-[#0F172A] mb-2">Phone</p>
              <a
                href="tel:+919347172758"
                className="text-[#E5C07B] hover:text-[#D4AF37] font-semibold transition"
              >
                +919347172758
              </a>
            </div>
            <div>
              <p className="text-2xl mb-2">💬</p>
              <p className="font-semibold text-[#0F172A] mb-2">Contact Form</p>
              <Link
                to="/contact-us"
                className="text-[#E5C07B] hover:text-[#D4AF37] font-semibold transition"
              >
                Get in Touch
              </Link>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default Policies;

