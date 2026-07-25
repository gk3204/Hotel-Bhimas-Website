import React, { useEffect, useRef } from "react";
import { Outlet, useNavigate, useLocation } from "react-router-dom";
import AdminHeader from "../components/AdminHeader";
import RouteErrorBoundary from "../components/RouteErrorBoundary";
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

  return (
    <div className="min-h-screen bg-[#0F172A] text-white">
      <AdminHeader />
      <div className="p-6">
        <RouteErrorBoundary key={location.pathname}>
          <Outlet />
        </RouteErrorBoundary>
      </div>
    </div>
  );
};

export default AdminLayout;
