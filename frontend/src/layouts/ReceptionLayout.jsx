import React from "react";
import { Outlet } from "react-router-dom";
import ReceptionHeader from "../components/ReceptionHeader";

const ReceptionLayout = () => {
  return (
    <div className="min-h-screen bg-[#0F172A] text-white">
      <ReceptionHeader />
      <div className="p-6">
        <Outlet />
      </div>
    </div>
  );
};

export default ReceptionLayout;
