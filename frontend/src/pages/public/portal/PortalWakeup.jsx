// Wake-up call (v3 item 3) — lifted from the old single-page portal, unchanged in behaviour.
import React, { useState } from "react";
import { useNavigate, useOutletContext } from "react-router-dom";
import { apiRequest } from "../../../api/api";
import { Btn, Card } from "../../../components/portal/PortalKit";
import { inCls } from "../../../components/portal/portalTokens";

export default function PortalWakeup() {
  const { token, reload, flash, base } = useOutletContext();
  const navigate = useNavigate();
  const [time, setTime] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    if (!time) return;
    setBusy(true);
    try {
      await apiRequest(`/portal/${token}/wakeup`, {
        method: "POST",
        body: JSON.stringify({ time, client_ref: `wake-${token}-${time}` }),
      });
      flash(`Wake-up call set for ${time}.`);
      setTime("");
      await reload();
      navigate(`${base}/requests`);
    } catch (e) {
      flash(e.message || "Could not set the wake-up call.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card icon="⏰" title="Wake-up call">
      <p className="text-sm text-slate-600 mb-3">
        Pick a time and the front desk will ring your room.
      </p>
      <div className="flex gap-2">
        <input type="time" className={inCls} value={time} onChange={(e) => setTime(e.target.value)} />
        <div className="w-32">
          <Btn onClick={submit} disabled={busy || !time}>{busy ? "…" : "Set"}</Btn>
        </div>
      </div>
    </Card>
  );
}
