// "Report a problem" (v8q, owner 5 Nov) — the guest tells us something is wrong in the room,
// without having to phone or walk to the desk. It becomes a guest complaint with the hotel's
// response / resolve timers (the owner is WhatsApped if it is ignored), the technician sees it as a
// priority job, and the desk gets it on the Guest requests board. Progress shows under
// "Your requests".
import React, { useState } from "react";
import { useNavigate, useOutletContext } from "react-router-dom";
import { apiRequest } from "../../../api/api";
import { Btn, Card } from "../../../components/portal/PortalKit";
import { inCls } from "../../../components/portal/portalTokens";

// key = what the server files it under; keep in step with routers/portal.py GUEST_PROBLEMS
const PROBLEMS = [
  ["ac", "❄️", "AC / cooling"],
  ["water", "🚿", "Water / bathroom"],
  ["electrical", "💡", "Lights / power"],
  ["cleanliness", "🧹", "Cleanliness"],
  ["tv_wifi", "📺", "TV / WiFi"],
  ["noise", "🔊", "Noise"],
  ["service", "🙋", "Staff / service"],
  ["other", "📝", "Something else"],
];

export default function PortalComplaint() {
  const { token, reload, flash, base } = useOutletContext();
  const navigate = useNavigate();
  const [kind, setKind] = useState("");
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [ref] = useState(() => `cmp-${token}-${Date.now()}`);

  const label = (PROBLEMS.find((p) => p[0] === kind) || [])[2];
  const ok = kind && (kind !== "other" || text.trim().length >= 3);

  const submit = async () => {
    if (!ok) return;
    setBusy(true);
    try {
      await apiRequest(`/portal/${token}/complaint`, {
        method: "POST",
        body: JSON.stringify({ category: kind, issue: text.trim() || label, client_ref: ref }),
      });
      flash("Thank you — we've got it and someone is on the way.");
      await reload();
      navigate(`${base}/requests`);
    } catch (e) {
      flash(e.message || "Could not send that — please call the front desk.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card icon="🛠️" title="Report a problem">
      <p className="text-sm text-slate-600 mb-3">What's wrong? Tap one — the front desk and our technician are told straight away.</p>
      <div className="grid grid-cols-2 gap-2 mb-3">
        {PROBLEMS.map(([k, icon, name]) => (
          <button key={k} onClick={() => setKind(k)}
            className={`rounded-xl border px-3 py-3 text-sm font-semibold flex items-center gap-2 text-left transition ${
              kind === k ? "border-[#D4AF37] bg-[#D4AF37]/10 text-slate-900" : "border-slate-200 bg-white text-slate-700"}`}>
            <span className="text-xl">{icon}</span> {name}
          </button>
        ))}
      </div>
      <textarea className={`${inCls} mb-3`} rows={3} maxLength={400} value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder={kind === "other" ? "Tell us what's wrong" : "Anything to add? (optional)"} />
      <Btn onClick={submit} disabled={busy || !ok}>{busy ? "Sending…" : "Send to the front desk"}</Btn>
    </Card>
  );
}
