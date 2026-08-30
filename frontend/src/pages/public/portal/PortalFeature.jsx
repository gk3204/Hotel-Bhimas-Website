// Feature gate for a portal sub-page (v3 item 3).
//
// The hotel can switch any of the five services off in admin. On the old single page that
// simply hid a card; now each service also has its own URL, and a guest who bookmarked one
// (or a stale QR) must not land on a working page for a service the hotel has withdrawn.
// Sends them to the home hub instead — the backend refuses the action anyway, so this is
// about not showing a form that cannot succeed.
import React from "react";
import { Navigate, useOutletContext } from "react-router-dom";

export default function PortalFeature({ flag, children }) {
  const { data, base } = useOutletContext();
  if (!data?.features?.[flag]) return <Navigate to={base} replace />;
  return children;
}
