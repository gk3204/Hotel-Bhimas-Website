import React from "react";
import { Outlet } from "react-router-dom";
import AdminHeader from "../components/AdminHeader";

const AdminLayout = () => {
  return (
    <div className="min-h-screen bg-[#0F172A] text-white">
      <AdminHeader />
      <div className="p-6">
        <Outlet />
      </div>
    </div>
  );
};

export default AdminLayout;