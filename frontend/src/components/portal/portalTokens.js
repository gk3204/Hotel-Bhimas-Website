// Non-component bits of the guest-portal kit (v3 item 3).
//
// Kept out of PortalKit.jsx so that file exports components only — a module that mixes the
// two breaks Vite's Fast Refresh (react-refresh/only-export-components).
export const money = (v) =>
  `₹${Number(v || 0).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

export const STATUS_LABEL = {
  requested: "Received", acknowledged: "In progress", completed: "Done", dismissed: "Closed",
};

export const TYPE_LABEL = {
  room_service: "Room service", wifi: "WiFi", wakeup: "Wake-up call", cab: "Cab", checkout: "Checkout",
};

export const inCls =
  "w-full px-3 py-2 border border-slate-300 rounded-lg focus:outline-none focus:border-[#D4AF37] focus:ring-2 focus:ring-[#D4AF37]/20";
