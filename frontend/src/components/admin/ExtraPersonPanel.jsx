// v6l — Extra person (Settings → "Extra person").
//
// A one-time charge per extra person, posted from the desk's folio ("Add extra person"). The
// price depends on the room type's AC flag (Rooms → room types), is entered BEFORE GST, and the
// GST % here is added on top: ₹500 at 18% is posted as ₹590 and the invoice shows ₹500 taxable +
// ₹90 GST. 0 for both prices means "not set", and the desk refuses rather than posting ₹0.
import React, { useEffect, useState } from "react";
import { FaSave } from "react-icons/fa";
import { Card, PrimaryButton, inputCls } from "./BackofficeUI";
import { getExtraPerson, setExtraPerson } from "../../api/settings";

const num = (v, d = 0) => (v === "" || v == null || Number.isNaN(Number(v)) ? d : Number(v));
const inr = (v) => `₹${Number(v || 0).toLocaleString("en-IN", { maximumFractionDigits: 2 })}`;

export default function ExtraPersonPanel({ showToast }) {
  const [cfg, setCfg] = useState(null);
  const [saved, setSaved] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    getExtraPerson()
      .then((c) => { setCfg(c); setSaved(JSON.stringify(c)); })
      .catch((e) => setError(e.message || "Failed to load."));
  }, []);

  if (error) return <div className="p-4 rounded-lg bg-red-500/10 border border-red-500/30 text-red-300 text-sm">{error}</div>;
  if (!cfg) return <Card><div className="p-6 text-slate-400 text-sm">Loading…</div></Card>;

  const dirty = JSON.stringify(cfg) !== saved;
  const withGst = (p) => num(p) * (1 + num(cfg.gst_percent) / 100);

  const save = async () => {
    setSaving(true);
    try {
      const res = await setExtraPerson(cfg);
      setCfg(res); setSaved(JSON.stringify(res));
      showToast("Extra-person pricing saved.", "success");
    } catch (e) { showToast(e.message || "Save failed.", "error"); }
    finally { setSaving(false); }
  };

  return (
    <Card className="mb-5">
      <div className="p-5">
        <p className="text-slate-400 text-sm mb-4">
          A one-time charge for each extra person in a room, added by the desk from the folio
          (<b>Add extra person</b>). The price follows the room type&apos;s AC setting. Prices are
          <b> before GST</b>; the GST % is added on top and printed on the invoice.
        </p>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4 max-w-3xl">
          <label className="text-xs text-slate-400">
            AC room, per person (₹, before GST)
            <input type="number" min="0" step="1" className={inputCls} value={cfg.ac_amount ?? 0}
                   onChange={(e) => setCfg({ ...cfg, ac_amount: num(e.target.value) })} />
            <span className="block mt-1 text-slate-500">Guest pays {inr(withGst(cfg.ac_amount))}</span>
          </label>
          <label className="text-xs text-slate-400">
            Non-AC room, per person (₹, before GST)
            <input type="number" min="0" step="1" className={inputCls} value={cfg.non_ac_amount ?? 0}
                   onChange={(e) => setCfg({ ...cfg, non_ac_amount: num(e.target.value) })} />
            <span className="block mt-1 text-slate-500">Guest pays {inr(withGst(cfg.non_ac_amount))}</span>
          </label>
          <label className="text-xs text-slate-400">
            GST % (added on top)
            <input type="number" min="0" max="28" step="0.5" className={inputCls} value={cfg.gst_percent ?? 0}
                   onChange={(e) => setCfg({ ...cfg, gst_percent: num(e.target.value) })} />
          </label>
        </div>
        <div className="flex justify-end mt-5">
          <PrimaryButton onClick={save} disabled={saving || !dirty}>
            <FaSave size={13} /> {saving ? "Saving…" : "Save"}
          </PrimaryButton>
        </div>
      </div>
    </Card>
  );
}
