import { Navigate, Routes, Route } from "react-router-dom";

import PublicLayout from "./layouts/PublicLayout";
import AdminLayout from "./layouts/AdminLayout";
import ReceptionLayout from "./layouts/ReceptionLayout";
import StaffLayout from "./layouts/StaffLayout";
import ProtectedRoute from "./components/ProtectedRoute";

// Public Pages
import Home from "./pages/public/Home";
import Facilities from "./pages/public/Facilities";
import NearbyPlaces from "./pages/public/NearbyPlaces";
import AboutUs from "./pages/public/AboutUs";
import ContactUs from "./pages/public/ContactUs";
import Policies from "./pages/public/Policies";
import TermsAndConditions from "./pages/public/TermsAndConditions";
import PrivacyPolicy from "./pages/public/PrivacyPolicy";
import RefundPolicy from "./pages/public/RefundPolicy";
import CancellationPolicy from "./pages/public/CancellationPolicy";
import Restaurant from "./pages/public/Restaurant";
import Rooms from "./pages/public/Rooms";
import Booking from "./pages/public/Booking";
import PreArrival from "./pages/public/PreArrival";
// In-room guest portal (split into a layout + one page per service in v3).
import PortalLayout from "./pages/public/portal/PortalLayout";
import PortalHome from "./pages/public/portal/PortalHome";
import PortalFeature from "./pages/public/portal/PortalFeature";
import PortalRoomService from "./pages/public/portal/PortalRoomService";
import PortalWifi from "./pages/public/portal/PortalWifi";
import PortalWakeup from "./pages/public/portal/PortalWakeup";
import PortalCab from "./pages/public/portal/PortalCab";
import PortalCheckout from "./pages/public/portal/PortalCheckout";
import PortalRequests from "./pages/public/portal/PortalRequests";
import PaymentSuccess from "./pages/public/PaymentSuccess";
import PaymentFailed from "./pages/public/PaymentFailed";


// Admin Pages
import Dashboard from "./pages/admin/Dashboard";
import AdminLogin from "./pages/admin/AdminLogin";
import RoomTypes from "./pages/admin/RoomTypes";
import AdminRooms from "./pages/admin/Rooms";
import RoomAvailability from "./pages/admin/RoomAvailability";
import UserSecurityCheck from "./pages/admin/UserSecurityCheck";
import UserManagement from "./pages/admin/UserManagement";
import Bookings from "./pages/admin/Bookings";
import Payments from "./pages/admin/Payments";
import Enquiries from "./pages/admin/Enquiries";
import Promotions from "./pages/admin/Promotions";
import TravelAgents from "./pages/admin/TravelAgents";
import RatePlans from "./pages/admin/RatePlans";
import AgentSettlements from "./pages/admin/AgentSettlements";
import FraudDashboard from "./pages/admin/FraudDashboard";
import CashShiftSettings from "./pages/admin/CashShiftSettings";
import AdminHousekeeping from "./pages/admin/Housekeeping";
import AdminMaintenance from "./pages/admin/Maintenance";
import GuestDirectory from "./pages/admin/GuestDirectory";
import WhatsAppMessaging from "./pages/admin/WhatsAppMessaging";
import OwnerDashboard from "./pages/admin/OwnerDashboard";
import Reports from "./pages/admin/Reports";
import OtaChannels from "./pages/admin/OtaChannels";
import Compliance from "./pages/admin/Compliance";
import AuditLog from "./pages/admin/AuditLog";
import Reviews from "./pages/admin/Reviews";
import Companies from "./pages/admin/Companies";
import Vendors from "./pages/admin/Vendors";
import Staff from "./pages/admin/Staff";
import Inventory from "./pages/admin/Inventory";
import Complaints from "./pages/admin/Complaints";
import GuestPortalAdmin from "./pages/admin/GuestPortal";
import TwoFactorSettings from "./pages/admin/TwoFactorSettings";
import Settings from "./pages/admin/Settings";
import Linen from "./pages/admin/Linen";

//Reception Pages
import ReceptionDashboard from "./pages/reception/ReceptionDashboard";
import ReceptionPayments from "./pages/reception/Payments";
import ReceptionEnquiries from "./pages/reception/Enquiries";

// Staff PWA (housekeeper + maintenance)
import StaffHome from "./pages/staff/StaffHome";
import MyRooms from "./pages/staff/MyRooms";
import MyTickets from "./pages/staff/MyTickets";
import RoomService from "./pages/staff/RoomService";

function App() {
  return (
    <Routes>

      {/* PUBLIC LAYOUT */}
      <Route element={<PublicLayout />}>
        <Route path="/" element={<Home />} />
        <Route path="/rooms" element={<Rooms />} />
        <Route path="/booking" element={<Booking />} />
        <Route path="/facilities" element={<Facilities />} />
        <Route path="/about-us" element={<AboutUs />} />
        <Route path="/contact-us" element={<ContactUs />} />
        <Route path="/nearby-places" element={<NearbyPlaces />} />
        <Route path="/policies" element={<Policies />} />
        <Route path="/terms-and-conditions" element={<TermsAndConditions />} />
        <Route path="/privacy-policy" element={<PrivacyPolicy />} />
        <Route path="/refund-policy" element={<RefundPolicy />} />
        <Route path="/cancellation-policy" element={<CancellationPolicy />} />
        <Route path="/restaurant" element={<Restaurant />} />
        <Route path="/payment-success/:bookingId" element={<PaymentSuccess />} />
        <Route path="/payment-failed/:bookingId" element={<PaymentFailed />} />
      </Route>

      {/* PRE-ARRIVAL DIGITAL REGISTRATION (public, tokenized — prompt 14) */}
      <Route path="/pre-arrival/:token" element={<PreArrival />} />

      {/* IN-ROOM GUEST PORTAL (public, tokenized — prompt 18d; split into pages in v3).
          A home hub plus one page per service: the room-service menu had grown past a
          screenful, which pushed WiFi / wake-up / cab out of sight. PortalLayout holds the
          single /portal/{token} fetch and hands it to each child, so navigating between
          services costs no extra request. A service the hotel has switched off redirects
          home rather than rendering a form the backend would refuse. */}
      <Route path="/portal/:token" element={<PortalLayout />}>
        <Route index element={<PortalHome />} />
        <Route path="room-service" element={<PortalFeature flag="room_service"><PortalRoomService /></PortalFeature>} />
        <Route path="wifi" element={<PortalFeature flag="wifi"><PortalWifi /></PortalFeature>} />
        <Route path="wakeup" element={<PortalFeature flag="wakeup"><PortalWakeup /></PortalFeature>} />
        <Route path="cab" element={<PortalFeature flag="cab"><PortalCab /></PortalFeature>} />
        <Route path="checkout" element={<PortalFeature flag="contactless_checkout"><PortalCheckout /></PortalFeature>} />
        <Route path="requests" element={<PortalRequests />} />
        {/* An unknown sub-path is a mistyped/stale link, not a 404 for the whole portal. */}
        <Route path="*" element={<Navigate to="." replace />} />
      </Route>

      {/* ADMIN LOGIN */}
      <Route path="/admin-login" element={<AdminLogin />} />

      {/* ADMIN LAYOUT (Protected) */}
      <Route
        element={
          <ProtectedRoute allowedRoles={["admin", "housekeeper"]}>
            <AdminLayout />
          </ProtectedRoute>
        }
      >
        <Route path="/admin" element={<OwnerDashboard />} />
        <Route path="/admin/home" element={<Dashboard />} />
        <Route path="/admin/reports" element={<Reports />} />
        <Route path="/admin/room-types" element={<RoomTypes />} />
        <Route path="/admin/rooms" element={<AdminRooms />} />
        <Route path="/admin/promotions" element={<Promotions />} />
        <Route path="/admin/travel-agents" element={<TravelAgents />} />
        <Route path="/admin/rate-plans" element={<RatePlans />} />
        <Route path="/admin/agent-settlements" element={<AgentSettlements />} />
        <Route path="/admin/fraud" element={<FraudDashboard />} />
        <Route path="/admin/cash-shift" element={<CashShiftSettings />} />
        <Route path="/admin/ota" element={<OtaChannels />} />
        <Route path="/admin/housekeeping" element={<AdminHousekeeping />} />
        <Route path="/admin/maintenance" element={<AdminMaintenance />} />
        <Route path="/admin/guests" element={<GuestDirectory />} />
        <Route path="/admin/messaging" element={<WhatsAppMessaging />} />
        <Route path="/admin/room-availability" element={<RoomAvailability />} />
        <Route path="/admin/bookings" element={<Bookings />} />
        <Route path="/admin/payments" element={<Payments />} />
        <Route path="/admin/enquiries" element={<Enquiries />} />
        <Route path="/admin/user-check" element={<UserSecurityCheck />} />
        <Route path="/admin/users" element={<UserManagement />} />
        <Route path="/admin/compliance" element={<Compliance />} />
        <Route path="/admin/audit-log" element={<AuditLog />} />
        <Route path="/admin/reviews" element={<Reviews />} />
        <Route path="/admin/companies" element={<Companies />} />
        <Route path="/admin/vendors" element={<Vendors />} />
        <Route path="/admin/staff" element={<Staff />} />
        <Route path="/admin/inventory" element={<Inventory />} />
        <Route path="/admin/complaints" element={<Complaints />} />
        <Route path="/admin/guest-portal" element={<GuestPortalAdmin />} />
        <Route path="/admin/security-2fa" element={<TwoFactorSettings />} />
        <Route path="/admin/settings" element={<Settings />} />
        <Route path="/admin/linen" element={<Linen />} />
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
        <Route path="/reception/payments" element={<ReceptionPayments />} />
        <Route path="/reception/enquiries" element={<ReceptionEnquiries />} />
      </Route>

      {/* STAFF PWA LAYOUT (housekeeper + maintenance + room service, Protected) */}
      <Route
        element={
          <ProtectedRoute allowedRoles={["housekeeper", "maintenance", "roomservice", "reception"]}>
            <StaffLayout />
          </ProtectedRoute>
        }
      >
        <Route path="/staff" element={<StaffHome />} />
        <Route path="/staff/rooms" element={<MyRooms />} />
        <Route path="/staff/tickets" element={<MyTickets />} />
        {/* Room service runs on a tablet in the same installable shell (TBC-4). */}
        <Route path="/staff/room-service" element={<RoomService />} />
      </Route>

      {/* Catch-all. Without this an unmatched URL rendered NOTHING — a white page — which is
          what a guest saw when an in-room QR pointed at a host that didn't have the portal
          route. A wrong link should always say so. */}
      <Route path="*" element={<NotFound />} />
    </Routes>
  );
}

function NotFound() {
  return (
    <div className="min-h-screen bg-[#0F172A] text-white flex items-center justify-center p-6">
      <div className="text-center max-w-md">
        <div className="text-5xl mb-4">🧭</div>
        <h1 className="text-2xl font-bold mb-2 bg-gradient-to-r from-[#E5C07B] to-[#FCD34D] bg-clip-text text-transparent">
          Page not found
        </h1>
        <p className="text-slate-400 text-sm">
          This link doesn't lead anywhere. If you scanned a QR code in your room, please ask the
          front desk for a fresh one.
        </p>
        <a href="/" className="inline-block mt-6 px-5 py-2.5 rounded-lg bg-[#E5C07B] text-slate-900 font-semibold">
          Go to the home page
        </a>
      </div>
    </div>
  );
}

export default App;
