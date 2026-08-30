// Cab / tour desk (v3 item 3) — lifted from the old single-page portal, unchanged in behaviour.
import React, { useState } from "react";
import { useNavigate, useOutletContext } from "react-router-dom";
import { apiRequest } from "../../../api/api";
import { Btn, Card } from "../../../components/portal/PortalKit";
import { inCls } from "../../../components/portal/portalTokens";

export default function PortalCab() {
  const { token, reload, flash, base } = useOutletContext();
  const navigate = useNavigate();
  const [dest, setDest] = useState("");
  const [time, setTime] = useState("");
  const [pax, setPax] = useState(1);
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    if (!dest || dest.trim().length < 2) return;
    setBusy(true);
    try {
      await apiRequest(`/portal/${token}/cab`, {
        method: "POST",
        body: JSON.stringify({
          destination: dest.trim(),
          pickup_time: time || null,
          passengers: Number(pax) || 1,
          client_ref: `cab-${token}-${Date.now()}`,
        }),
      });
      flash("Cab requested — the desk will confirm.");
      setDest("");
      setTime("");
      await reload();
      navigate(`${base}/requests`);
    } catch (e) {
      flash(e.message || "Could not request a cab.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card icon="🚕" title="Cab / tour desk">
      <div className="space-y-2">
        <div>
          <label className="text-xs text-slate-500" htmlFor="cab-dest">Where to?</label>
          <input id="cab-dest" className={inCls} value={dest} placeholder="Tirupati railway station"
            onChange={(e) => setDest(e.target.value)} />
        </div>
        <div className="flex gap-2">
          <div className="flex-1">
            <label className="text-xs text-slate-500" htmlFor="cab-time">Pickup time</label>
            <input id="cab-time" type="time" className={inCls} value={time}
              onChange={(e) => setTime(e.target.value)} />
          </div>
          <div className="w-24">
            <label className="text-xs text-slate-500" htmlFor="cab-pax">Guests</label>
            <input id="cab-pax" type="number" min="1" className={inCls} value={pax}
              onChange={(e) => setPax(e.target.value)} />
          </div>
        </div>
        <Btn onClick={submit} disabled={busy || dest.trim().length < 2}>
          {busy ? "Requesting…" : "Request a cab"}
        </Btn>
      </div>
    </Card>
  );
}
