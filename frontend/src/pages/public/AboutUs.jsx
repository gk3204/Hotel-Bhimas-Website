import React, { useEffect } from "react";
import ImageWithSpinner from "../../components/ImageWithSpinner";
import heroImg from "./hero.png";
import founderImg from "./images/founder.png"; // Late Sri K. R. Gopal Iyer

const AboutUs = () => {
  useEffect(() => {
    window.scrollTo(0, 0);
  }, []);

  return (
    <div className="min-h-screen bg-[#F8FAFC]">

      {/* ================= HERO SECTION ================= */}
      <section
        className="w-full h-[420px] flex items-center justify-center text-center relative overflow-hidden"
      >
        <ImageWithSpinner
          src={heroImg}
          alt="Hotel Bhimas"
          className="absolute inset-0"
          style={{
            position: "absolute",
            filter: "brightness(0.4)"
          }}
        />
        <div className="relative z-10 px-6">
          <h1 className="text-4xl md:text-5xl font-bold text-white mb-4">
            About <span className="text-[#E5C07B]">Hotel Bhimas</span>
          </h1>
          <p className="text-white/70 max-w-3xl mx-auto">
            One of the oldest and most trusted budget hotels in Tirupati, serving pilgrims with devotion since 1960.
          </p>
        </div>
      </section>

      {/* ================= ABOUT INTRO ================= */}
      <section className="max-w-5xl mx-auto px-6 mt-20">
        <div className="bg-white rounded-3xl shadow-xl p-10 md:p-14 text-center">
          <h2 className="text-3xl font-bold text-[#0F172A] mb-6">
            A Legacy of Trust & Hospitality
          </h2>
          <p className="text-gray-700 leading-relaxed text-lg">
            Hotel Bhimas is a leading branded budget hotel in Tirupati, known for its clean rooms, famous pure vegetarian food,
            and warm hospitality. Established in 1960, we are among the oldest and most reputed hotels in the city,
            trusted by generations of pilgrims and families visiting Tirumala.
          </p>
        </div>
      </section>

      {/* ================= FOUNDER SECTION ================= */}
      <section className="max-w-7xl mx-auto px-6 mt-24">
        <div className="bg-white rounded-3xl shadow-xl p-10 md:p-16">
          <div className="grid md:grid-cols-2 gap-12 items-center">

            {/* Founder Image */}
            <div className="flex justify-center h-80">
              <ImageWithSpinner
                src={founderImg}
                alt="Late Sri K. R. Gopal Iyer"
                className="rounded-2xl"
                style={{ height: "320px" }}
              />
            </div>

            {/* Founder Text */}
            <div>
              <h2 className="text-3xl font-bold text-[#0F172A] mb-4">
                Founder’s Legacy
              </h2>
              <p className="text-gray-700 leading-relaxed">
                Hotel Bhimas was founded in 1960 by Late Sri K. R. Gopal Iyer and his brothers with a simple yet powerful vision —
                to provide clean, affordable, and respectful accommodation for pilgrims visiting Tirupati.
                His principles of honesty, service, and devotion continue to guide the hotel even today.
              </p>
            </div>

          </div>
        </div>
      </section>

      {/* ================= VALUES ================= */}
      <section className="max-w-7xl mx-auto px-6 mt-24">
        <h2 className="text-3xl font-bold text-center text-[#0F172A] mb-12">
          Our Values
        </h2>

        <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-8">
          {[
            "Devotion to Service",
            "Cleanliness & Hygiene",
            "Honest Pricing",
            "Respect for Pilgrims",
          ].map((value) => (
            <div
              key={value}
              className="bg-white rounded-2xl shadow-lg p-6 text-center hover:shadow-xl transition"
            >
              <p className="font-semibold text-lg text-gray-700">{value}</p>
            </div>
          ))}
        </div>
      </section>

      {/* ================= WHAT SETS US APART ================= */}
      <section className="max-w-7xl mx-auto px-6 mt-24">
        <div className="bg-white rounded-3xl shadow-xl p-10 md:p-16">
          <h2 className="text-3xl font-bold text-[#0F172A] mb-8 text-center">
            What Sets Us Apart
          </h2>

          <ul className="grid sm:grid-cols-2 gap-6 text-gray-700 text-lg">
            <li>• One of the oldest hotels in Tirupati</li>
            <li>• Trusted by generations of pilgrims</li>
            <li>• Famous pure vegetarian restaurant & sweets</li>
            <li>• Prime location near temples & transport hubs</li>
            <li>• Family-friendly & senior citizen friendly</li>
            <li>• Affordable comfort with traditional values</li>
          </ul>
        </div>
      </section>

      {/* ================= LOCATION ADVANTAGE ================= */}
      <section className="max-w-7xl mx-auto px-6 mt-24">
        <div className="bg-gradient-to-r from-[#0F172A] to-[#1E293B] rounded-3xl p-12 text-white shadow-xl">
          <h2 className="text-3xl font-bold mb-4 text-center">
            Strategic Location Advantage
          </h2>
          <p className="text-white/80 max-w-4xl mx-auto text-center leading-relaxed">
            Hotel Bhimas is conveniently located in the heart of Tirupati, with easy access to Tirupati Railway Station,
            bus stand, major temples, and the Tirumala route — making it an ideal base for pilgrims and tourists alike.
          </p>
        </div>
      </section>

      {/* ================= MANAGEMENT MESSAGE ================= */}
      <section className="max-w-5xl mx-auto px-6 mt-24 mb-24">
        <div className="bg-white rounded-3xl shadow-xl p-10 md:p-14 text-center">
          <h2 className="text-3xl font-bold text-[#0F172A] mb-6">
            Management Message
          </h2>
          <p className="text-gray-700 leading-relaxed text-lg">
            We at Hotel Bhimas remain committed to upholding the values laid down by our founders.
            Our focus continues to be on cleanliness, comfort, quality food, and sincere service,
            ensuring every guest experiences a peaceful and memorable stay in Tirupati.
          </p>
        </div>
      </section>

    </div>
  );
};

export default AboutUs;
