import React, { useEffect } from "react";
import ImageWithSpinner from "../../components/ImageWithSpinner";
import restaurantImg from "./images/ac-restaurant.png";
import food1 from "./images/idly.png";
import food2 from "./images/dosa.jpg";
import food3 from "./images/thali.png";
import food4 from "./images/noodles.jpg";
import food5 from "./images/paneer-tikka.jpg";
import food6 from "./images/north-indian.png";
import sweet1 from "./images/jilebi.jpg";
import sweet2 from "./images/mysorepak.png";
import sweet3 from "./images/sonpapdi.jpg";
import sweet4 from "./images/Basundi.jpg";
import sweet5 from "./images/kaju.png";
import sweet6 from "./images/mixture.png";

const Restaurant = () => {
  /* ================= SCROLL TO TOP ON MOUNT ================= */

  useEffect(() => {
    window.scrollTo(0, 0);
  }, []);

  return (
    <div className="min-h-screen bg-[#F8FAFC]">

      {/* ================= HERO ================= */}
<section className="relative w-full h-[70vh] flex items-center justify-center overflow-hidden">
  <div
    className="absolute inset-0 bg-cover bg-center scale-105"
    style={{ backgroundImage: `url(${restaurantImg})` }}
  ></div>

  {/* Gradient Overlay */}
  <div className="absolute inset-0 bg-gradient-to-b from-black/70 via-black/60 to-black/80"></div>

  <div className="relative z-10 text-center px-6">
    <h1 className="text-4xl md:text-6xl font-bold text-white mb-6 tracking-wide">
      Bhimas <span className="text-[#E5C07B]">Restaurant & Sweets</span>
    </h1>
    <p className="text-white/80 text-lg md:text-xl max-w-2xl mx-auto">
      Pure vegetarian dining experience loved by pilgrims and families in Tirupati
    </p>
  </div>
</section>


      {/* ================= ABOUT RESTAURANT ================= */}
<section className="max-w-5xl mx-auto px-6 py-20 text-center">
  <h2 className="text-3xl font-bold text-[#0F172A] mb-6">
    About Our Restaurant
  </h2>

  <p className="text-gray-700 text-lg leading-relaxed">
    <strong>Hotel Bhimas</strong> is well known in Tirupati for its 
    <span className="font-semibold"> authentic South Indian food</span> 
    loved by pilgrims and locals. We also serve a variety of 
    <span className="font-semibold"> North Indian</span> and 
    <span className="font-semibold"> Chinese dishes</span>, 
    making it a perfect place for family dining. All food is prepared fresh 
    with quality ingredients and proper hygiene.
  </p>
</section>



      {/* ================= RESTAURANT HIGHLIGHTS ================= */}
      <section className="bg-[#F1F5F9] py-24">
  <div className="max-w-7xl mx-auto px-6">

  
        <h2 className="text-3xl font-bold text-center text-[#0F172A] mb-12">
          Restaurant Highlights
        </h2>

        <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-5 gap-8">
          {[
            "Pure Vegetarian Kitchen",
            "Family Dining",
            "Hygienic Preparation",
            "Traditional Taste",
            "Breakfast / Lunch / Dinner",
          ].map((item) => (
            <div
              key={item}
              className="bg-white rounded-2xl shadow-md p-6 text-center 
hover:-translate-y-2 hover:shadow-2xl transition-all duration-300"

            >
              <p className="font-semibold text-gray-700">{item}</p>
            </div>
          ))}
        </div>
        </div>
      </section>

      {/* ================= FOOD GALLERY ================= */}
      <section className="max-w-7xl mx-auto px-6 py-16">
        <h2 className="text-3xl font-bold text-center text-[#0F172A] mb-12">
          Our Food
        </h2>

        <div className="grid sm:grid-cols-2 md:grid-cols-3 gap-8">
          {[food1, food2, food3, food4, food5, food6].map((img, index) => (
            <div
              key={index}
              className="w-full h-64 overflow-hidden rounded-lg group"
            >
              <ImageWithSpinner
                src={img}
                alt="Food"
                className="group-hover:scale-110 transition duration-500"
                style={{ height: "256px" }}
              />
            </div>
          ))}
        </div>
      </section>

      {/* ================= SWEETS SECTION ================= */}
      <section className="max-w-7xl mx-auto px-6 py-16">
        <h2 className="text-3xl font-bold text-center text-[#0F172A] mb-6">
          Famous Bhimas Sweets & Savouries
        </h2>

        <p className="text-center text-gray-600 max-w-3xl mx-auto mb-12">
          Bhimas is especially famous for its traditional Indian sweets & savouries prepared fresh every day.
          A must-buy for pilgrims and visitors.
        </p>

        <div className="grid grid-cols-2 sm:grid-cols-4 gap-6 mb-12">
          {[
            "Traditional Indian Sweets",
            "Festival Specials",
            "Fresh Daily Preparation",
            "Takeaway Available",
          ].map((item) => (
            <div
              key={item}
              className="bg-white rounded-xl shadow-md p-4 text-center font-semibold text-gray-700"
            >
              {item}
            </div>
          ))}
        </div>

        <div className="grid sm:grid-cols-2 md:grid-cols-3 gap-8">
          {[sweet1, sweet2, sweet3, sweet4, sweet5, sweet6].map((img, index) => (
            <div
              key={index}
              className="rounded-2xl overflow-hidden shadow-lg hover:shadow-2xl transition duration-300 h-64"
            >
              <ImageWithSpinner
                src={img}
                alt="Sweets"
                className=""
                style={{ height: "256px" }}
              />
            </div>
          ))}
        </div>
      </section>

     <section className="bg-[#0F172A] py-24">
  <div className="max-w-5xl mx-auto px-6 text-center text-white">
    <h2 className="text-3xl font-bold mb-6">
      Online Delivery Available
    </h2>

    <p className="text-white/80 text-lg mb-10">
      Order your favorite Bhimas dishes through
      <span className="text-[#E5C07B] font-semibold"> Swiggy </span>
      and
      <span className="text-[#E5C07B] font-semibold"> Zomato</span>.
    </p>

    <div className="flex flex-col sm:flex-row justify-center gap-6">
  <a
    href="https://www.swiggy.com/city/tirupati/hotel-bhimas-ramanuja-circle-near-railyway-station-rest294647"
    target="_blank"
    rel="noopener noreferrer"
    className="bg-[#FC8019] text-white px-10 py-4 rounded-full font-semibold shadow-lg hover:scale-105 hover:shadow-2xl transition duration-300 text-center"
  >
    Order on Swiggy
  </a>

  <a
    href="https://www.zomato.com/tirupati/hotel-bhimas-1-tirumala/order"
    target="_blank"
    rel="noopener noreferrer"
    className="bg-[#E23744] text-white px-10 py-4 rounded-full font-semibold shadow-lg hover:scale-105 hover:shadow-2xl transition duration-300 text-center"
  >
    Order on Zomato
  </a>
</div>

  </div>
</section>


      <section className="bg-[#F1F5F9] py-24">
  <div className="max-w-4xl mx-auto px-6 text-center">
    <div className="bg-white rounded-3xl p-12 shadow-xl">
      <h2 className="text-3xl font-bold mb-4 text-[#0F172A]">
        Visit Bhimas Restaurant
      </h2>
      <p className="text-gray-600 text-lg">
        Enjoy authentic vegetarian food and traditional sweets during your stay in Tirupati.
      </p>
    </div>
  </div>
</section>

    </div>
  );
};

export default Restaurant;
