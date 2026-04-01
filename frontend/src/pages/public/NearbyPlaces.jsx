import React, { useEffect } from "react";
import { Link } from "react-router-dom";
import ImageWithSpinner from "../../components/ImageWithSpinner";
import heroTemple from "./images/temples/balaji-padmavati.png";

/* ================= DATA ================= */

const temples = [
  {
    name: "Sri Govindaraja Swamy Temple",
    distance: "1 km",
    info: "One of Tirupati’s oldest temples, walkable from Hotel Bhimas",
  },
  {
    name: "Sri Kodandarama Swamy Temple",
    distance: "1.5 km",
    info: "Peaceful temple associated with the Ramayana",
  },
  {
    name: "Kapila Theertham",
    distance: "3 – 6 km",
    info: "Sacred Shiva temple with natural waterfall",
  },
  {
    name: "Tiruchanur Padmavathi Temple",
    distance: "5 km",
    info: "Temple of Goddess Padmavathi, must-visit for pilgrims",
  },
  {
    name: "ISKCON Tirupati",
    distance: "4 km",
    info: "Modern Krishna temple with serene ambience",
  },
  {
    name: "Tirumala Sri Venkateswara Temple",
    distance: "22 km",
    info: "World-famous Lord Balaji temple on Tirumala hills",
  },
  {
    name: "Sri Kalahasti Temple",
    distance: "35 km",
    info: "Famous Rahu–Ketu dosha nivarana temple",
  },
  {
    name: "Kanipakkam Vinayaka Temple",
    distance: "60 km",
    info: "Self-manifested Ganesh idol, popular day trip",
  },
];

const attractions = [
  {
    name: "Chandragiri Fort",
    distance: "12 km",
    info: "Historic fort of Vijayanagara kings",
  },
  {
    name: "Silathoranam",
    distance: "25 km",
    info: "Rare natural rock arch near Tirumala",
  },
  {
    name: "Sri Venkateswara Zoo Park",
    distance: "10 km",
    info: "Large zoo, ideal for families",
  },
  {
    name: "Talakona Waterfalls",
    distance: "55 km",
    info: "Tallest waterfall in Andhra Pradesh (day trip)",
  },
  {
    name: "Akasa Ganga",
    distance: "24 km",
    info: "Holy waterfall used in temple rituals",
  },
  {
    name: "Papavinasanam",
    distance: "25 km",
    info: "Believed to cleanse sins, scenic spot",
  },
];

const transport = [
  {
    name: "Tirupati Railway Station",
    distance: "1 – 2 km",
    info: "Major rail hub, very close to the hotel",
  },
  {
    name: "APSRTC Bus Stand",
    distance: "2 km",
    info: "Buses to Tirumala & nearby towns",
  },
  {
    name: "Renigunta Airport",
    distance: "15 km",
    info: "Domestic airport with taxi access",
  },
  {
    name: "Alipiri Footpath",
    distance: "4 km",
    info: "Sacred walking path to Tirumala",
  },
  {
    name: "Srivari Mettu",
    distance: "18 km",
    info: "Shorter footpath route to Tirumala",
  },
];

/* ================= COMPONENT ================= */

const NearbyPlaces = () => {
  useEffect(() => {
    window.scrollTo(0, 0);
  }, []);

  return (
    <div className="min-h-screen bg-[#F8FAFC]">

      {/* HERO */}
      <section
        className="w-full h-[420px] flex items-center justify-center text-center relative overflow-hidden"
      >
        <ImageWithSpinner
          src={heroTemple}
          alt="Temples Near Tirupati"
          className="absolute inset-0"
          style={{ position: "absolute" }}
        />
        <div className="absolute inset-0 bg-black/50"></div>
        <div className="relative z-10 px-6">
          <h1 className="text-4xl md:text-5xl font-bold text-white mb-4">
            Nearby Places Around{" "}
            <span className="text-[#E5C07B]">Hotel Bhimas</span>
          </h1>
          <p className="text-white/70 max-w-2xl mx-auto">
            Convenient access to famous temples, tourist attractions and
            transport hubs for pilgrims and tourists visiting Tirupati.
          </p>
        </div>
      </section>

      {/* TEMPLES */}
      <section className="max-w-7xl mx-auto px-6 mt-20">
        <h2 className="text-3xl font-bold text-[#0F172A] text-center mb-12">
          🛕 Major Temples
        </h2>

        <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-8">
          {temples.map((item) => (
            <div
              key={item.name}
              className="bg-white rounded-2xl shadow-lg p-6 text-center hover:shadow-2xl transition transform hover:-translate-y-2"
            >
              <h3 className="font-bold text-lg text-[#0F172A]">
                {item.name}
              </h3>
              <p className="text-[#E5C07B] font-semibold mt-2">
                {item.distance}
              </p>
              <p className="text-sm text-slate-500 mt-3">
                {item.info}
              </p>
            </div>
          ))}
        </div>
      </section>

      {/* ATTRACTIONS */}
      <section className="max-w-7xl mx-auto px-6 mt-24">
        <h2 className="text-3xl font-bold text-[#0F172A] text-center mb-12">
          🌄 Tourist & Nature Attractions
        </h2>

        <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-8">
          {attractions.map((item) => (
            <div
              key={item.name}
              className="bg-white rounded-2xl shadow-lg p-6 text-center hover:shadow-2xl transition transform hover:-translate-y-2"
            >
              <h3 className="font-bold text-lg text-[#0F172A]">
                {item.name}
              </h3>
              <p className="text-[#E5C07B] font-semibold mt-2">
                {item.distance}
              </p>
              <p className="text-sm text-slate-500 mt-3">
                {item.info}
              </p>
            </div>
          ))}
        </div>
      </section>

      {/* TRANSPORT */}
      <section className="max-w-7xl mx-auto px-6 mt-24 mb-24">
        <h2 className="text-3xl font-bold text-[#0F172A] text-center mb-12">
          🚆 Transport Connectivity
        </h2>

        <div className="grid sm:grid-cols-2 lg:grid-cols-5 gap-8">
          {transport.map((item) => (
            <div
              key={item.name}
              className="bg-white rounded-2xl shadow-lg p-6 text-center hover:shadow-2xl transition transform hover:-translate-y-2"
            >
              <h3 className="font-bold text-md text-[#0F172A]">
                {item.name}
              </h3>
              <p className="text-[#E5C07B] font-semibold mt-2">
                {item.distance}
              </p>
              <p className="text-sm text-slate-500 mt-3">
                {item.info}
              </p>
            </div>
          ))}
        </div>
      </section>

      {/* CTA */}
      <section className="max-w-5xl mx-auto px-6 pb-24">
        <div className="bg-gradient-to-r from-[#0F172A] to-[#1E293B] rounded-3xl p-12 text-center text-white shadow-xl">
          <h3 className="text-3xl font-bold mb-4">
            Stay Close to Everything in Tirupati
          </h3>
          <p className="text-white/70 mb-8">
            Hotel Bhimas offers comfortable stay, famous pure veg food,
            and easy access to Tirumala.
          </p>

          <Link
            to="/booking"
            className="bg-[#E5C07B] text-[#0F172A] px-8 py-3 rounded-full font-semibold hover:bg-[#FCD34D] transition"
          >
            Book Your Stay
          </Link>
        </div>
      </section>

    </div>
  );
};

export default NearbyPlaces;
