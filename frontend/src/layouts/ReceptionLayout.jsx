import React from "react";
import { Outlet, useLocation } from "react-router-dom";
import ReceptionHeader from "../components/ReceptionHeader";
import RouteErrorBoundary from "../components/RouteErrorBoundary";

const ReceptionLayout = () => {
  const location = useLocation();
  return (
    <div className="min-h-screen bg-[#0F172A] text-white">
      <ReceptionHeader />
      <div className="p-6">
        <RouteErrorBoundary key={location.pathname}>
          <Outlet />
        </RouteErrorBoundary>
      </div>
    </div>
  );
};

export default ReceptionLayout;
