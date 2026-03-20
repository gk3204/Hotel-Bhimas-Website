import React from "react";
import { Outlet } from "react-router-dom";
import AdminHeader from "../components/AdminHeader";

const AdminLayout = () => {
  return (
    <>
      <AdminHeader />
      <div className="p-6">
        <Outlet />
      </div>
    </>
  );
};

export default AdminLayout;
