// WiFi (v3 item 3) — lifted from the old single-page portal, unchanged in behaviour.
import React, { useState } from "react";
import { useOutletContext } from "react-router-dom";
import { apiRequest } from "../../../api/api";
import { Btn, Card } from "../../../components/portal/PortalKit";

export default function PortalWifi() {
  const { token, data, reload, flash } = useOutletContext();
  const wifi = data.wifi || {};
  const [code, setCode] = useState(wifi.code || null);
  const [busy, setBusy] = useState(false);

  const getCode = async () => {
    setBusy(true);
    try {
      const res = await apiRequest(`/portal/${token}/wifi`, {
        method: "POST",
        body: JSON.stringify({ client_ref: `wifi-${token}` }),
      });
      if (res.code) {
        setCode(res.code);
        flash("Here's your WiFi code.");
      } else {
        flash("Requested — the desk will send your WiFi code shortly.");
      }
      await reload();
    } catch (e) {
      flash(e.message || "Could not get the WiFi code.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card icon="📶" title="WiFi">
      <div className="text-sm text-slate-700">
        Network: <b>{wifi.ssid || "—"}</b>
      </div>
      {code ? (
        <div className="mt-2 text-center bg-amber-50 border border-amber-200 rounded-lg py-3">
          <div className="text-xs text-slate-500">Password</div>
          <div className="text-lg font-bold tracking-widest text-[#B8860B]">{code}</div>
        </div>
      ) : (
        <div className="mt-2">
          <Btn onClick={getCode} disabled={busy}>{busy ? "Getting…" : "Get WiFi password"}</Btn>
        </div>
      )}
    </Card>
  );
}
