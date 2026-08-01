import React, { useEffect, useRef } from "react";
import { Outlet, useNavigate, useLocation } from "react-router-dom";
import { jwtDecode } from "jwt-decode";
import AdminSidebar from "../components/admin/AdminSidebar";
import GlobalSearch from "../components/admin/GlobalSearch";
import RouteErrorBoundary from "../components/RouteErrorBoundary";
import { NAV_ITEMS, SUPERVISOR_PATHS } from "../components/admin/adminNav";
import { getComplianceConfig } from "../api/compliance";

const AdminLayout = () => {
  const navigate = useNavigate();
  const location = useLocation();
  const timerRef = useRef(null);
  const minutesRef = useRef(15); // default; refined from compliance config

  // F-B: a supervisor is a web-only oversight role — keep it inside its allowed screens.
  // Backend deps are the real gate; this is UX so a typed/landing URL doesn't 403 a supervisor.
  useEffect(() => {
    let role = null;
    try { role = jwtDecode(localStorage.getItem("adminToken")).role; } catch { role = null; }
    if (role === "supervisor" && !SUPERVISOR_PATHS.includes(location.pathname)) {
      navigate("/admin/housekeeping", { replace: true });
    }
  }, [location.pathname, navigate]);

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

  // Keyboard shortcuts: "/" opens search; "g" then a key jumps to a section.
  useEffect(() => {
    // Second key of a "g <key>" chord → destination route.
    const GOTO = {
      d: "/admin", b: "/admin/bookings", r: "/admin/reports", p: "/admin/payments",
      u: "/admin/guests", h: "/admin/housekeeping", c: "/admin/complaints", i: "/admin/inventory",
    };
    let gPending = false;
    let gTimer = null;
    const clearG = () => { gPending = false; if (gTimer) clearTimeout(gTimer); };

    const isTyping = () => {
      const el = document.activeElement;
      if (!el) return false;
      const tag = el.tagName;
      return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || el.isContentEditable;
    };

    const onKey = (e) => {
      if (e.metaKey || e.ctrlKey || e.altKey || isTyping()) return;
      const k = e.key.toLowerCase();
      if (gPending) {
        clearG();
        if (GOTO[k]) { e.preventDefault(); navigate(GOTO[k]); }
        return;
      }
      if (k === "/") {
        e.preventDefault();
        window.dispatchEvent(new Event("admin:open-search"));
      } else if (k === "g") {
        gPending = true;
        gTimer = setTimeout(clearG, 1200);
      }
    };

    window.addEventListener("keydown", onKey);
    return () => { window.removeEventListener("keydown", onKey); clearG(); };
  }, [navigate]);

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
