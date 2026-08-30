// Contactless checkout (v3 item 3) — lifted from the old single-page portal.
//
// This raises a REQUEST; it never settles anything. The desk opens the existing staff
// checkout for the stay, so there is no public payment path and no parallel settlement.
import React, { useState } from "react";
import { useNavigate, useOutletContext } from "react-router-dom";
import { apiRequest } from "../../../api/api";
import { Btn, Card } from "../../../components/portal/PortalKit";
import { inCls, money } from "../../../components/portal/portalTokens";

export default function PortalCheckout() {
  const { token, data, reload, flash, base } = useOutletContext();
  const navigate = useNavigate();
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");

  const submit = async () => {
    setBusy(true);
    try {
      const res = await apiRequest(`/portal/${token}/checkout`, {
        method: "POST",
        body: JSON.stringify({ note: note || null, client_ref: `co-${token}` }),
      });
      flash(res.message || "Checkout requested — please leave your key card at the desk.");
      await reload();
      navigate(`${base}/requests`);
    } catch (e) {
      flash(e.message || "Could not request checkout.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card icon="🧳" title="Contactless checkout">
      <p className="text-sm text-slate-600 mb-2">
        Your current bill: <b>{money(data.folio?.balance)}</b>. Tap below and drop your key card at
        the desk — we'll bring your final bill over and settle any balance.
      </p>
      <input
        className={inCls + " mb-2"}
        value={note}
        placeholder="Anything we should know? (optional)"
        onChange={(e) => setNote(e.target.value)}
      />
      <Btn onClick={submit} disabled={busy}>{busy ? "Sending…" : "Request checkout"}</Btn>
    </Card>
  );
}
