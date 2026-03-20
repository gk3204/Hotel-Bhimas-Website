import { Routes, Route } from "react-router-dom";

import PublicLayout from "./layouts/PublicLayout";
import AdminLayout from "./layouts/AdminLayout";
import ReceptionLayout from "./layouts/ReceptionLayout";
import ProtectedRoute from "./components/ProtectedRoute";

// Public Pages
import Home from "./pages/public/Home";
import Facilities from "./pages/public/Facilities";
import NearbyPlaces from "./pages/public/NearbyPlaces";
import AboutUs from "./pages/public/AboutUs";
import ContactUs from "./pages/public/ContactUs";
import Policies from "./pages/public/Policies";
import Restaurant from "./pages/public/Restaurant";
import Rooms from "./pages/public/Rooms";
import Booking from "./pages/public/Booking";
import PaymentSuccess from "./pages/public/PaymentSuccess";
import PaymentFailed from "./pages/public/PaymentFailed";


// Admin Pages
import Dashboard from "./pages/admin/Dashboard";
import AdminLogin from "./pages/admin/AdminLogin";
import RoomTypes from "./pages/admin/RoomTypes";
import RoomAvailability from "./pages/admin/RoomAvailability";
import UserSecurityCheck from "./pages/admin/UserSecurityCheck";
import UserManagement from "./pages/admin/UserManagement";
import Bookings from "./pages/admin/Bookings";

//Reception Pages
import ReceptionDashboard from "./pages/reception/ReceptionDashboard";

function App() {
  return (
    <Routes>

      {/* PUBLIC LAYOUT */}
      <Route element={<PublicLayout />}>
        <Route path="/" element={<Home />} />
        <Route path="/rooms" element={<Rooms />} />
        <Route path="/booking/:roomTypeId" element={<Booking />} />
        <Route path="/facilities" element={<Facilities />} />
        <Route path="/about-us" element={<AboutUs />} />
        <Route path="/contact-us" element={<ContactUs />} />
        <Route path="/nearby-places" element={<NearbyPlaces />} />
        <Route path="/policies" element={<Policies />} />
        <Route path="/restaurant" element={<Restaurant />} />
        <Route path="/payment-success/:bookingId" element={<PaymentSuccess />} />
        <Route path="/payment-failed/:bookingId" element={<PaymentFailed />} />
      </Route>

      {/* ADMIN LOGIN */}
      <Route path="/admin-login" element={<AdminLogin />} />

      {/* ADMIN LAYOUT (Protected) */}
      <Route
        element={
          <ProtectedRoute allowedRoles={["admin"]}>
            <AdminLayout />
          </ProtectedRoute>
        }
      >
        <Route path="/admin" element={<Dashboard />} />
        <Route path="/admin/room-types" element={<RoomTypes />} />
        <Route path="/admin/room-availability" element={<RoomAvailability />} />
        <Route path="/admin/bookings" element={<Bookings />} />
        <Route path="/admin/user-check" element={<UserSecurityCheck />} />
        <Route path="/admin/users" element={<UserManagement />} />
      </Route>

      {/* Reception LAYOUT (Protected) */}
      <Route
        element={
          <ProtectedRoute allowedRoles={["reception"]}>
            <ReceptionLayout />
          </ProtectedRoute>
        }
      >
        <Route path="/reception" element={<ReceptionDashboard />} />
        <Route path="/reception/bookings" element={<Bookings />} />
        <Route path="/reception/availability" element={<RoomAvailability />} />
      </Route>

    </Routes>
  );
}

export default App;
