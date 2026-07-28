// Grouped navigation model for the admin sidebar. One source of truth for the sidebar and the
// quick-jump search. Keep labels short; icons are react-icons/fa (already a dependency).
import {
  FaTachometerAlt, FaCalendarCheck, FaCalendarAlt, FaBed, FaDoorOpen, FaBroom, FaTools,
  FaUsers, FaEnvelopeOpenText, FaWhatsapp, FaStar, FaMobileAlt, FaBullhorn,
  FaTags, FaPercent, FaUserTie, FaHandshake, FaGlobe,
  FaMoneyBillWave, FaCashRegister, FaChartBar,
  FaShieldAlt, FaClipboardCheck, FaExclamationTriangle, FaHistory,
  FaBuilding, FaTruck, FaBoxes,
  FaUserShield, FaUserCog, FaUserCheck, FaKey, FaCog,
} from "react-icons/fa";

export const NAV_GROUPS = [
  {
    label: "Front Desk",
    items: [
      { to: "/admin", label: "Dashboard", icon: FaTachometerAlt, end: true },
      { to: "/admin/bookings", label: "Bookings", icon: FaCalendarCheck },
      { to: "/admin/room-availability", label: "Availability", icon: FaCalendarAlt },
      { to: "/admin/rooms", label: "Rooms", icon: FaDoorOpen },
      { to: "/admin/room-types", label: "Room Types", icon: FaBed },
      { to: "/admin/housekeeping", label: "Housekeeping", icon: FaBroom },
      { to: "/admin/maintenance", label: "Maintenance", icon: FaTools },
    ],
  },
  {
    label: "Guests & Comms",
    items: [
      { to: "/admin/guests", label: "Guest Directory", icon: FaUsers },
      { to: "/admin/enquiries", label: "Enquiries", icon: FaEnvelopeOpenText },
      { to: "/admin/messaging", label: "WhatsApp", icon: FaWhatsapp },
      { to: "/admin/reviews", label: "Reviews", icon: FaStar },
      { to: "/admin/guest-portal", label: "Guest Portal", icon: FaMobileAlt },
      { to: "/admin/complaints", label: "Complaints", icon: FaBullhorn },
    ],
  },
  {
    label: "Rates & Agents",
    items: [
      { to: "/admin/rate-plans", label: "Rate Plans", icon: FaTags },
      { to: "/admin/promotions", label: "Promotions", icon: FaPercent },
      { to: "/admin/travel-agents", label: "Travel Agents", icon: FaUserTie },
      { to: "/admin/agent-settlements", label: "Settlements", icon: FaHandshake },
      { to: "/admin/ota", label: "OTA Channels", icon: FaGlobe },
    ],
  },
  {
    label: "Money",
    items: [
      { to: "/admin/payments", label: "Payments", icon: FaMoneyBillWave },
      { to: "/admin/cash-shift", label: "Cash / Shift", icon: FaCashRegister },
      { to: "/admin/reports", label: "Reports", icon: FaChartBar },
    ],
  },
  {
    label: "Compliance & Risk",
    items: [
      { to: "/admin/fraud", label: "Fraud", icon: FaExclamationTriangle },
      { to: "/admin/compliance", label: "Compliance", icon: FaClipboardCheck },
      { to: "/admin/audit-log", label: "Audit Log", icon: FaHistory },
    ],
  },
  {
    label: "Back Office",
    items: [
      { to: "/admin/companies", label: "Companies", icon: FaBuilding },
      { to: "/admin/vendors", label: "Vendors", icon: FaTruck },
      { to: "/admin/inventory", label: "Inventory", icon: FaBoxes },
    ],
  },
  {
    label: "People & Access",
    items: [
      { to: "/admin/staff", label: "Staff", icon: FaUsers },
      { to: "/admin/users", label: "Users", icon: FaUserCog },
      { to: "/admin/user-check", label: "User Check", icon: FaUserCheck },
      { to: "/admin/security-2fa", label: "2FA Security", icon: FaKey },
      { to: "/admin/settings", label: "Settings", icon: FaCog },
    ],
  },
];

// Flat list for the quick-jump search.
export const NAV_ITEMS = NAV_GROUPS.flatMap((g) =>
  g.items.map((it) => ({ ...it, group: g.label }))
);

export { FaShieldAlt, FaUserShield };
