// "Your requests" (v3 item 3) — its own page now, and the landing spot after placing an
// order, so the guest gets confirmation that the desk has it rather than a toast that fades.
// The layout polls, so a status moves to Done here without the guest doing anything.
import React from "react";
import { useOutletContext } from "react-router-dom";
import { Card, StatusPill } from "../../../components/portal/PortalKit";
import { TYPE_LABEL, money } from "../../../components/portal/portalTokens";

export default function PortalRequests() {
  const { data } = useOutletContext();
  const requests = data.requests || [];

  return (
    <Card icon="🧾" title="Your requests">
      {requests.length === 0 ? (
        <p className="text-sm text-slate-500">
          Nothing yet. Anything you ask for from the home screen will show up here.
        </p>
      ) : (
        <div className="divide-y divide-slate-100">
          {requests.map((r) => (
            <div key={r.id} className="py-2 flex items-center justify-between gap-3 text-sm">
              <div className="min-w-0">
                <span className="text-slate-800">{TYPE_LABEL[r.type] || r.type}</span>
                {r.amount != null && <span className="text-slate-500"> · {money(r.amount)}</span>}
                {r.note && <div className="text-xs text-slate-500 truncate max-w-[220px]">{r.note}</div>}
              </div>
              <StatusPill status={r.status} />
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}
