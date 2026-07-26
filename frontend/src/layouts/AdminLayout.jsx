import React, { useEffect, useRef } from "react";
import { Outlet, useNavigate, useLocation } from "react-router-dom";
import AdminSidebar from "../components/admin/AdminSidebar";
import GlobalSearch from "../components/admin/GlobalSearch";
import RouteErrorBoundary from "../components/RouteErrorBoundary";
import { NAV_ITEMS } from "../components/admin/adminNav";
import { getComplianceConfig } from "../api/compliance";

const AdminLayout = () => {
  const navigate = useNavigate();
  const location = useLocation();
  const timerRef = useRef(null);
  const minutesRef = useRef(15); // default; refined from compliance config

  useEffect(() => {
    let cancelled = false;

    const logout = () => {
      localStorage.removeItem("adminToken");
      navigate("/admin-login");
    };

    const resetTimer = () => {
      if (timerRef.current) clearTimeout(timerRef.current);
      timerRef.current = setTimeout(logout, minutesRef.current * 60 * 1000);
    };

    const events = ["mousemove", "mousedown", "keydown", "touchstart", "scroll"];
    events.forEach((e) => window.addEventListener(e, resetTimer, { passive: true }));
    resetTimer();

    // Pull the configured idle window (prompt 18); fall back silently to 15 min.
    getComplianceConfig()
      .then((cfg) => {
        if (cancelled) return;
        const m = Number(cfg?.admin_idle_logout_minutes);
        if (m && m > 0) {
          minutesRef.current = m;
          resetTimer();
        }
      })
      .catch(() => { /* keep default */ });

    return () => {
      cancelled = true;
      if (timerRef.current) clearTimeout(timerRef.current);
      events.forEach((e) => window.removeEventListener(e, resetTimer));
    };
  }, [navigate]);

  // Reflect the current page in the browser tab title.
  useEffect(() => {
    const match = [...NAV_ITEMS]
      .filter((it) => location.pathname === it.to || (!it.end && location.pathname.startsWith(it.to)))
      .sort((a, b) => b.to.length - a.to.length)[0];
    document.title = match ? `${match.label} · Hotel Bhimas Admin` : "Hotel Bhimas Admin";
  }, [location.pathname]);

  return (
    <div className="min-h-screen bg-[#0F172A] text-white">
      <AdminSidebar />
      <GlobalSearch />
      {/* Content sits to the right of the fixed sidebar on md+, full-width on mobile. */}
      <div className="md:ml-64 min-w-0">
        <RouteErrorBoundary key={location.pathname}>
          <Outlet />
        </RouteErrorBoundary>
      </div>
    </div>
  );
};

export default AdminLayout;
