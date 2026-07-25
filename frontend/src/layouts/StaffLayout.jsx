import React from "react";
import { Outlet } from "react-router-dom";
import StaffHeader from "../components/StaffHeader";

// Mobile-first shell for the housekeeper/maintenance PWA.
const StaffLayout = () => {
  return (
    <div className="min-h-screen bg-[#0F172A] text-white">
      <StaffHeader />
      <div className="max-w-3xl mx-auto p-4">
        <Outlet />
      </div>
    </div>
  );
};

export default StaffLayout;
