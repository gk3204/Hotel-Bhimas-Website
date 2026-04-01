import React from "react";
import { Link } from "react-router-dom";
import ImageWithSpinner from "../../components/ImageWithSpinner";
import heroImg from "./hero.png";
import doubleDeluxe from "./images/rooms/double-deluxe.png";
import threeBedAc from "./images/rooms/3 bed ac.png";
import fourBed from "./images/rooms/4 bed nonac.png";
import fiveBed from "./images/rooms/4-bed.png";
import balajiImg from "./images/temples/tirumala.png";
import food1 from "./images/idly.png";
import food2 from "./images/dosa.jpg";
import food3 from "./images/north-indian.png";
import food4 from "./images/thali.png";
import food5 from "./images/coffee.png";
import restaurant from "./images/ac-restaurant.png";
const Home = () => {
  const foodImages = [food1, food2, food3, food4, food5];
const [foodIndex, setFoodIndex] = React.useState(0);

React.useEffect(() => {
  window.scrollTo(0, 0);
  const interval = setInterval(() => {
    setFoodIndex((prev) => (prev + 1) % foodImages.length);
  }, 2000);

  return () => clearInterval(interval);
}, []);
const heroImages = [heroImg, doubleDeluxe, restaurant];
const [heroIndex, setHeroIndex] = React.useState(0);

React.useEffect(() => {
  const interval = setInterval(() => {
    setHeroIndex((prev) => (prev + 1) % heroImages.length);
  }, 2000); // change slide every 4 seconds

  return () => clearInterval(interval);
}, []);

  return (
    <div className="min-h-screen bg-[#F8FAFC] px-6 overflow-x-hidden">


      {/* ================= HERO SECTION (AUTO SLIDE) ================= */}
<section className="w-screen -mx-6 pt-28 relative overflow-hidden">
  
  {/* Sliding Images */}
  <div className="absolute inset-0">
    {heroImages.map((img, index) => (
  <div
    key={index}
    className={`absolute inset-0 transition-opacity duration-700 ${
      index === heroIndex ? "opacity-100" : "opacity-0"
    }`}
  >
    <ImageWithSpinner
      src={img}
      alt="Hotel Bhimas"
      style={{ filter: "brightness(0.55)" }}
    />
  </div>
))}
  </div>

  {/* Content */}
  <div className="relative py-24 px-6 max-w-7xl mx-auto text-center">
    <h1 className="text-4xl md:text-5xl font-medium text-white/90 mb-4">
      Trusted by Pilgrims for{" "}
      <span className="text-[#E5C07B]">Stay & Food</span>
    </h1>

    <p className="text-white/70 text-lg max-w-3xl mx-auto mb-8">
      Clean, comfortable, and affordable accommodation —
      Hotel Bhimas, Tirupati.
    </p>

    <div className="flex justify-center gap-6">
      <Link
        to="/booking"
        className="bg-[#0F172A]/90 text-[#E5C07B] px-8 py-3 rounded-full font-semibold hover:text-[#FCD34D] transition"
      >
        Book Now
      </Link>
      <Link
        to="/rooms"
        className="border border-white text-white px-8 py-3 rounded-full font-semibold hover:bg-white hover:text-[#0F172A] transition"
      >
        View Rooms
      </Link>
    </div>
  </div>
</section>






      {/* ================= ABOUT SHORT ================= */}
      <section className="max-w-5xl mx-auto mt-20 px-6">
        <div className="bg-white rounded-2xl shadow-lg p-10 md:p-16 text-center relative overflow-hidden">
          
          {/* Decorative gradient / overlay */}
          <div className="absolute -top-10 -left-10 w-40 h-40 bg-yellow-200 rounded-full opacity-30 blur-3xl"></div>
          <div className="absolute -bottom-10 -right-10 w-60 h-60 bg-blue-200 rounded-full opacity-20 blur-3xl"></div>

          <h2 className="text-3xl md:text-4xl font-bold text-[#0F172A] mb-6">
            About <span className="text-[#E5C07B]">Hotel Bhimas</span>
          </h2>

          <p className="text-gray-700 leading-relaxed text-lg md:text-xl max-w-3xl mx-auto">
            Hotel Bhimas offers <span className="font-semibold text-[#E5C07B]">clean, comfortable, and affordable</span> accommodation 
            in Tirupati. Ideal for pilgrims visiting <span className="font-semibold text-[#E5C07B]">Tirumala</span> and nearby temples, 
            our hotel ensures a peaceful stay with modern amenities and warm hospitality.
          </p>

          {/* Optional button */}
          <div className="mt-8">
            <Link
              to="/rooms"
              className="inline-block bg-[#E5C07B] text-[#0F172A] px-8 py-3 rounded-full font-semibold hover:bg-[#FCD34D] transition"
            >
              Reserve Your Room
            </Link>
          </div>
        </div>
      </section>



      {/* ================= ROOM HIGHLIGHTS ================= */}
      <section className="max-w-7xl mx-auto mt-20 px-6">
        <h2 className="text-3xl font-bold text-center text-[#0F172A] mb-12">
          Room Highlights
        </h2>

        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-8">
          {[
            {
              name: "Double Deluxe",
              desc: "Spacious deluxe room with AC and modern amenities.",
              img: doubleDeluxe,
              price: 1150,
            },
            {
              name: "Triple Bed",
              desc: "Premium triple bed room with AC, ideal for family relaxation.",
              img: threeBedAc,
              price: 1550,
            },
            {
              name: "4 Bed Room",
              desc: "Perfect choice for families and small groups.",
              img: fourBed,
              price: 1750,
            },
            {
              name: "5 Bed Room",
              desc: "Large room suitable for bigger families and group stays.",
              img: fiveBed,
              price: 1950,
            },
          ].map((room) => (
            <div
              key={room.name}
              className="bg-white rounded-2xl shadow-lg overflow-hidden group hover:shadow-2xl transition transform hover:-translate-y-2"
            >
              {/* Room Image */}
              <div className="h-48 overflow-hidden">
                <ImageWithSpinner
                  src={room.img}
                  alt={room.name}
                  className="group-hover:scale-110 transition-transform duration-500"
                  style={{ height: "192px" }}
                />
              </div>

              {/* Content */}
              <div className="p-5 text-center">
                <h3 className="text-lg font-bold text-[#0F172A] mb-2">
                  {room.name}
                </h3>
                <p className="text-gray-600 text-sm mb-3">
                  {room.desc}
                </p>
                <p className="text-[#E5C07B] font-bold text-lg mb-4">
                  Starting from ₹{room.price}/night
                </p>

                <Link
                  to="/booking"
                  className="inline-block bg-[#E5C07B] text-[#0F172A] px-6 py-2 rounded-full text-sm font-semibold hover:bg-[#FCD34D] transition"
                >
                  Book Now →
                </Link>
              </div>
            </div>
          ))}
        </div>
      </section>


      {/* ================= FAMOUS FOOD & SWEETS ================= */}
      <section className="max-w-7xl mx-auto mt-24 px-6">
        <div className="bg-gradient-to-r from-[#0F172A] to-[#1E293B] rounded-3xl shadow-xl overflow-hidden">
          
          <div className="grid md:grid-cols-2 items-center">
            
            {/* Left Content */}
            <div className="p-10 md:p-16 text-white">
              <h2 className="text-3xl md:text-4xl font-bold mb-6">
                ⭐ Famous <span className="text-[#E5C07B]">Bhimas Food & Sweets</span>
              </h2>

              <p className="text-white/80 text-lg leading-relaxed mb-6">
                Hotel Bhimas is not only known for comfortable stay but also for its 
                <span className="text-[#E5C07B] font-semibold"> famous pure vegetarian food </span> 
                and traditional sweets loved by visitors from all over India.
              </p>

              <p className="text-white/60 mb-8">
                Enjoy authentic South Indian meals, tiffin varieties, and signature Bhimas sweets prepared with quality ingredients and traditional recipes.
              </p>

              <Link
                to="/restaurant"
                className="inline-block bg-[#E5C07B] text-[#0F172A] px-8 py-3 rounded-full font-semibold hover:bg-[#FCD34D] transition"
              >
                View Restaurant
              </Link>
            </div>

          {/* Right Food Slideshow */}
          <div className="hidden md:flex items-center justify-center h-full relative p-10">
            
            {/* Glow Background */}
            <div className="absolute w-80 h-80 bg-[#E5C07B]/20 rounded-full blur-3xl"></div>

            {/* Slideshow Card */}
            <div className="relative w-80 h-80 rounded-2xl overflow-hidden shadow-2xl border border-white/10">

              {foodImages.map((img, index) => (
                <div
                  key={index}
                  className={`absolute inset-0 transition-opacity duration-700 ${
                    index === foodIndex ? "opacity-100" : "opacity-0"
                  }`}
                >
                  <ImageWithSpinner src={img} alt="Bhimas Food" />
                </div>
              ))}

              {/* Dark Overlay */}
              <div className="absolute inset-0 bg-black/20 pointer-events-none z-10"></div>

            </div>
          </div>


          </div>
        </div>
      </section>

      {/* ================= FACILITIES ================= */}
      <section className="max-w-7xl mx-auto mt-24 px-6">
        <h2 className="text-3xl font-bold text-center text-[#0F172A] mb-12">
          Our Facilities
        </h2>

        <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-8">
          {[{name:"24×7 Check-In & Check-Out",icon:"⏰"},
            { name: "Pure Veg Restaurant", icon: "🥗" },
            { name: "Spacious Car Parking", icon: "🚗" },
            { name: "AC Rooms", icon: "❄️" },
            { name: "24×7 Front Desk", icon: "🛎️" },
            { name: "Hot Water Facility", icon: "🚿" },
            { name: "Room Service", icon: "🧹" },
            { name: "Family-Friendly Rooms", icon: "👨‍👩‍👧‍👦" },
          ].map((facility) => (
            <div
              key={facility.name}
              className="bg-white rounded-2xl shadow-md p-6 text-center hover:shadow-xl transition transform hover:-translate-y-1"
            >
              <div className="text-4xl mb-4">{facility.icon}</div>
              <p className="font-semibold text-gray-700">
                {facility.name}
              </p>
            </div>
          ))}
        </div>
      </section>


      
      {/* ================= NEARBY TEMPLES ================= */}
      <section className="max-w-7xl mx-auto mt-24 px-6">
        <div className="bg-white rounded-3xl shadow-xl overflow-hidden">

          {/* Devotional Image */}
         <div
            className="h-[520px] bg-center bg-no-repeat relative"
            style={{
              backgroundImage: `url(${balajiImg})`,
              backgroundSize: "100% 100%",
            }}
          >


            {/* Overlay */}
            <div className="absolute inset-0 bg-black/40"></div>

            <div className="absolute inset-0 flex items-center justify-center">
              <h2 className="text-4xl md:text-5xl font-bold text-white">
                Nearby Temples
              </h2>
            </div>
          </div>

          {/* Temple Cards */}
          <div className="p-8 md:p-14">
            <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-6 text-center">
              {[
                { name: "Govindaraja Swamy Temple", distance: "1 km" },
                { name: "Sri Kodandarama Swamy Temple", distance: "1.5 km" },
                { name: "Tiruchanur Padmavathi Temple", distance: "5 km" },
                { name: "Kapila Theertham", distance: "6 km" },
                { name: "ISKCON Tirupati", distance: "4 km" },
                { name: "Tirumala Sri Venkateswara Temple", distance: "22 km" },
                { name: "Sri Kalahastri Shiva Temple", distance: "35 km" },
                { name: "Kanipakkam Vinayaka Temple", distance: "50 km" },
              ].map((temple) => (
                <div
                  key={temple.name}
                  className="bg-[#f9fafb] p-4 rounded-xl shadow-md hover:shadow-lg transition-shadow"
                >
                  <h3 className="font-semibold text-[#0F172A] text-lg md:text-xl">
                    {temple.name}
                  </h3>
                  <p className="text-gray-500 mt-1 text-sm md:text-base">{temple.distance}</p>
                </div>
              ))}
            </div>
          </div>
        </div>
      </section>

      {/* ================= TESTIMONIALS ================= */}
      <section className="max-w-7xl mx-auto mt-24 px-6">
        <h2 className="text-3xl md:text-4xl font-bold text-center text-[#0F172A] mb-4">
          What Our Guests Say
        </h2>
        <p className="text-center text-gray-500 mb-12">
          Based on genuine Google reviews from our guests
        </p>

        <div className="grid gap-8 sm:grid-cols-2 lg:grid-cols-3">
          {[
            {
              name: "Hariharan P.N",
              rating: 5,
              comment:
                "Very good rooms, reasonable price, hot water 24 hours. For Tirupati devotees this hotel is very good.",
              tag: "Family Stay",
            },
            {
              name: "Kanagavel D",
              rating: 5,
              comment:
                "Good spacious rooms. Well maintained and hygienic. Friendly staff members.",
              tag: "Great Value",
            },
            {
              name: "Vinod Joshi",
              rating: 4,
              comment:
                "Hotel Bhima in Tirupati is conveniently located just a stone's throw away from the railway station, making it easily accessible for travelers. The hotel has an attached vegetarian restaurant, which is a great convenience for guests looking for quality vegetarian meals. The area can be a bit noisy due to its proximity to the station, but the rooms are spacious, offering a comfortable stay overall.",
              tag: "Near Railway Station",
            },
            
            {
              name: "Ramani Krishnan",
              rating: 5,
              comment:
                "Good place very near to railway station. Good management. Good food and sweets.",
              tag: "Food & Location",
            },
            {
              name: "Murali L",
              rating: 4,
              comment:
                "A neat and comfortable budget hotel with a tasty pure vegetarian restaurant serving authentic South and North Indian food. Walkable distance from Tirupati Railway Station. AC and non-AC rooms available at moderate tariff. Recommended.",
              tag: "Near Railway Station",
            },
            {
              name: "Laksh G",
              rating: 5,
              comment:
                "A decent budget hotel with pure vegetarian food. Lunch was delicious and cleanliness well maintained.",
              tag: "Pure Veg Food",
            }

          ].map((review, index) => (
            <div
              key={index}
              className="bg-white rounded-2xl shadow-lg p-6 hover:shadow-2xl transition"
            >
              {/* Rating */}
              <div className="flex items-center mb-3">
                {Array.from({ length: review.rating }).map((_, i) => (
                  <span key={i} className="text-yellow-400 text-lg">★</span>
                ))}
              </div>

              {/* Comment */}
              <p className="text-gray-600 mb-4 leading-relaxed">
                “{review.comment}”
              </p>

              {/* Footer */}
              <div className="flex justify-between items-center mt-4">
                <p className="font-semibold text-[#0F172A]">{review.name}</p>
                <span className="text-sm bg-[#E5C07B]/20 text-[#0F172A] px-3 py-1 rounded-full">
                  {review.tag}
                </span>
              </div>
            </div>
          ))}
        </div>
      </section>



      {/* ================= GOOGLE MAP ================= */}
      <section className="max-w-7xl mx-auto mt-24 mb-24 px-6">
        {/* Section Header */}
        <div className="text-center mb-8">
          <h2 className="text-3xl md:text-4xl font-bold text-gray-800">
            Find Us on Google Maps
          </h2>
          <p className="text-gray-500 mt-2">
            Exact location of Hotel Bhimas and nearby temples
          </p>
        </div>

        {/* Map Container */}
        <div className="rounded-3xl overflow-hidden shadow-xl">
          <iframe
            src="https://www.google.com/maps?q=13.6292025,79.4193486&hl=en&z=15&output=embed"
            className="w-full h-96 md:h-[500px] border-0"
            allowFullScreen=""
            loading="lazy"
            referrerPolicy="no-referrer-when-downgrade"
          ></iframe>
        </div>
      </section>




    </div>
  );
};

export default Home;
