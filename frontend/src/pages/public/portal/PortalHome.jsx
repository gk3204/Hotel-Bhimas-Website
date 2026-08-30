// Portal home hub (v3 item 3) — the page the in-room QR lands on.
//
// One tile per enabled service, so nothing is buried under a long menu, plus a live summary
// of what the guest has already asked for. Tiles are gated by the hotel's feature flags: a
// service the hotel has switched off has no tile AND no reachable route (see App.jsx).
import React from "react";
import { Link, useOutletContext } from "react-router-dom";
import { Card, Tile } from "../../../components/portal/PortalKit";
import { money } from "../../../components/portal/portalTokens";

export default function PortalHome() {
  const { data, base } = useOutletContext();
  const f = data.features || {};
  const open = (data.requests || []).filter(
    (r) => r.status !== "completed" && r.status !== "dismissed",
  ).length;
  const menuCount = (data.menu || []).filter((m) => m.orderable_now).length;

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3">
        {f.room_service && (
          <Tile to={`${base}/room-service`} icon="🍽️" title="Room service"
            subtitle={menuCount ? `${menuCount} items` : "Menu"} />
        )}
        {f.wifi && (
          <Tile to={`${base}/wifi`} icon="📶" title="WiFi" subtitle={data.wifi?.ssid || "Get the password"} />
        )}
        {f.wakeup && <Tile to={`${base}/wakeup`} icon="⏰" title="Wake-up call" subtitle="Set a time" />}
        {f.cab && <Tile to={`${base}/cab`} icon="🚕" title="Cab / tour desk" subtitle="Book a ride" />}
        {f.contactless_checkout && (
          <Tile to={`${base}/checkout`} icon="🧳" title="Checkout" subtitle={money(data.folio?.balance)} />
        )}
      </div>

      <Link to={`${base}/requests`} className="block">
        <Card>
          <div className="flex items-center justify-between">
            <span className="font-semibold text-slate-800 flex items-center gap-2">
              <span className="text-xl">🧾</span> Your requests
            </span>
            <span className="text-sm text-slate-500">
              {open > 0 ? `${open} in progress` : "View all"} ›
            </span>
          </div>
        </Card>
      </Link>
    </div>
  );
}
