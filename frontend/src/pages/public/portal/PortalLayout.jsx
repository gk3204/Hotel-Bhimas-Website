// In-room guest portal — layout + the single data fetch (prompt 18d slice 10, split in v3).
//
// PUBLIC and token-scoped: no login, the QR in the room is the credential. Light,
// self-contained brand (see components/portal/PortalKit.jsx), no-bearer apiRequest.
//
// WHY THIS IS A LAYOUT. The portal used to be one long scrolling page. Once the room-service
// menu grew past a screenful, guests stopped finding WiFi / wake-up / cab below it. It is now
// a home hub with a page per service — but still ONE `/portal/{token}` request, held here and
// handed down through the Outlet context, so navigating between services costs no round trip
// and every page sees the same stay.
import React, { useCallback, useEffect, useState } from "react";
import { Outlet, useLocation, useParams } from "react-router-dom";
import { apiRequest } from "../../../api/api";
import { Shell } from "../../../components/portal/PortalKit";
import usePoll from "../../../utils/usePoll";

// Slow on purpose: a guest's own actions refresh immediately, so this only has to catch the
// desk completing something (v3 item 5).
const POLL_MS = 30000;

export default function PortalLayout() {
  const { token } = useParams();
  const { pathname } = useLocation();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [data, setData] = useState(null);
  const [toast, setToast] = useState("");

  const load = useCallback(async () => {
    try {
      setData(await apiRequest(`/portal/${token}`));
      setError("");
    } catch (e) {
      setError(e.message || "This link is not valid.");
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => { load(); }, [load]);
  usePoll(load, POLL_MS);

  const flash = (m) => { setToast(m); setTimeout(() => setToast(""), 3500); };

  const base = `/portal/${token}`;
  const onHome = pathname === base || pathname === `${base}/`;

  if (loading) {
    return (
      <Shell>
        <div className="py-16 flex justify-center">
          <div className="animate-spin h-10 w-10 border-4 border-[#D4AF37] border-t-transparent rounded-full" />
        </div>
      </Shell>
    );
  }

  // Anything that leaves us without data renders a message — never a blank page.
  // `!data.active` below would throw on a null `data`, which unmounts the whole tree and
  // shows the guest a white screen.
  if (!data) {
    return (
      <Shell>
        <div className="text-center py-10">
          <div className="text-4xl mb-3">🔒</div>
          <p className="text-slate-700">
            {error || "This link is not valid. Please ask the front desk for a new QR code."}
          </p>
        </div>
      </Shell>
    );
  }

  if (!data.active) {
    return (
      <Shell>
        <div className="text-center py-10">
          <div className="text-4xl mb-3">👋</div>
          <p className="text-slate-700">This stay is checked out. Thank you for staying with us!</p>
        </div>
      </Shell>
    );
  }

  return (
    <Shell
      subtitle={`Room ${data.room_number || "-"} · Welcome${data.guest_name ? ", " + data.guest_name : ""}`}
      backTo={onHome ? null : base}
    >
      {toast && (
        <div className="fixed top-4 inset-x-4 z-50 bg-slate-800 text-white text-sm px-4 py-3 rounded-xl shadow-lg text-center">
          {toast}
        </div>
      )}
      <Outlet context={{ token, data, reload: load, flash, base }} />
    </Shell>
  );
}
