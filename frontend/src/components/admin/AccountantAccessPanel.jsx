// v8a (owner, 18 Oct) — Settings → "Accountant access".
//
// The ACCOUNTANT is a read-only web login: Reports, Documents, Dues (pay later) and Payments. Which reports
// they may open is chosen here; the server enforces the same list on every report.
import React, { useEffect, useState } from "react";
import { FaSave } from "react-icons/fa";
import { Card, PrimaryButton } from "./BackofficeUI";
import { getAccountantAccess, setAccountantAccess } from "../../api/settings";

export default function AccountantAccessPanel({ showToast }) {
  const [catalogue, setCatalogue] = useState([]);
  const [picked, setPicked] = useState(new Set());
  const [saved, setSaved] = useState("[]");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    getAccountantAccess()
      .then((d) => {
        setCatalogue(d.catalogue || []);
        setPicked(new Set(d.reports || []));
        setSaved(JSON.stringify([...(d.reports || [])].sort()));
      })
      .catch((e) => showToast?.(e.message || "Could not load accountant access", "error"));
  }, [showToast]);

  const toggle = (k) => setPicked((s) => {
    const n = new Set(s);
    if (n.has(k)) n.delete(k); else n.add(k);
    return n;
  });
  const dirty = JSON.stringify([...picked].sort()) !== saved;

  const save = async () => {
    setBusy(true);
    try {
      const d = await setAccountantAccess([...picked]);
      setPicked(new Set(d.reports || []));
      setSaved(JSON.stringify([...(d.reports || [])].sort()));
      showToast?.("Accountant access saved.", "success");
    } catch (e) {
      showToast?.(e.message || "Save failed.", "error");
    }
    setBusy(false);
  };

  return (
    <Card>
      <h2 className="text-xl font-bold text-[#E5C07B] mb-1">Accountant access</h2>
      <p className="text-slate-400 text-sm mb-4">
        A user with the <b>Accountant</b> role (Users page) signs in to the web portal and sees only Reports,
        Documents, Dues (pay later) and Payments - read-only. Tick the reports they may open.
      </p>
      <div className="flex gap-2 mb-3">
        <button className="text-xs text-slate-300 underline" onClick={() => setPicked(new Set(catalogue.map((c) => c.key)))}>Tick all</button>
        <button className="text-xs text-slate-300 underline" onClick={() => setPicked(new Set())}>Clear</button>
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2 mb-5">
        {catalogue.map((c) => (
          <label key={c.key} className="flex items-center gap-2 text-sm text-slate-200 cursor-pointer">
            <input type="checkbox" checked={picked.has(c.key)} onChange={() => toggle(c.key)} />
            {c.label}
          </label>
        ))}
      </div>
      <PrimaryButton onClick={save} disabled={busy || !dirty}><FaSave size={12} /> {busy ? "Saving…" : "Save"}</PrimaryButton>
    </Card>
  );
}
