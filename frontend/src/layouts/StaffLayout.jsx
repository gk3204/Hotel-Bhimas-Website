import React from "react";
import { Outlet, useLocation } from "react-router-dom";
import StaffHeader from "../components/StaffHeader";
import RouteErrorBoundary from "../components/RouteErrorBoundary";

// Mobile-first shell for the housekeeper/maintenance PWA.
const StaffLayout = () => {
  const location = useLocation();
  return (
    <div className="min-h-screen bg-[#0F172A] text-white">
      <StaffHeader />
      <div className="max-w-3xl mx-auto p-4">
        <RouteErrorBoundary key={location.pathname}>
          <Outlet />
        </RouteErrorBoundary>
      </div>
    </div>
  );
};

export default StaffLayout;
