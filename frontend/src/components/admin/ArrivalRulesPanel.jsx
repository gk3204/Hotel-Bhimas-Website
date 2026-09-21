// v5m — Arrival & departure rules editor (Settings → "Arrival & departure").
//
// Three rule tables (early check-in / late arrival / hourly extension), each a list of rows
// keyed by booking source. The first row whose sources include the booking's source wins;
// the "*" row is the fallback and every table must have one. Charges are a flat ₹ amount or a
// percent of the stay's one-night rate (GST inclusive); approval decides when an admin login or
// owner OTP is needed. Saved as one JSON document via PUT /settings/arrival-rules.
import React, { useEffect, useState } from "react";
import { FaPlus, FaSave, FaTimes, FaUndo } from "react-icons/fa";
import { Card, GhostButton, PrimaryButton, inputCls } from "./BackofficeUI";
import { getArrivalRules, setArrivalRules, getCategories } from "../../api/settings";

const KINDS = [
  {
    key: "early_checkin", label: "Early check-in",
    hint: "The guest arrives before the expected time (booked time, or 12:00 for OTA stays). Free up to the threshold; a fee up to the full-night bound; beyond that a whole extra night is posted (only when a room is free the night before). The paid stay still runs from the expected time.",
    charge: true, bound: true,
  },
  {
    key: "late_arrival", label: "Late arrival",
    hint: "The guest arrives after the expected time. Within the threshold the 24-hour clock starts at the actual arrival; beyond it the clock starts at the expected time so the checkout date does not slide. Never charged; OTA stays are always 12:00 → 12:00.",
    charge: false, bound: false,
  },
  {
    key: "hourly_extension", label: "Hourly extension (late checkout)",
    hint: "The desk extends checkout by whole hours. Free up to the threshold; one fee for the extension up to the full-night bound; beyond it the desk is refused (or a full night is charged, per Overflow) and should extend the stay by a night instead.",
    charge: true, bound: true, hours: true,
  },
];

const APPROVALS = [
  ["on_change", "Only when the desk changes the amount"],
  ["always", "Always"],
  ["never", "Never"],
];

const num = (v, d = 0) => (v === "" || v == null || Number.isNaN(Number(v)) ? d : Number(v));

function SourcePicker({ value, options, onChange }) {
  const set = new Set(value || []);
  const toggle = (s) => {
    const next = new Set(set);
    if (next.has(s)) next.delete(s); else next.add(s);
    onChange(Array.from(next));
  };
  return (
    <div className="flex flex-wrap gap-1.5">
      {["*", ...options].map((s) => (
        <button
          key={s} type="button" onClick={() => toggle(s)}
          className={`px-2 py-0.5 rounded-full text-xs border transition ${set.has(s)
            ? "bg-[#E5C07B]/20 border-[#E5C07B] text-[#E5C07B]"
            : "border-slate-600 text-slate-400 hover:border-slate-400"}`}
          title={s === "*" ? "Every source not matched by another row" : s}
        >
          {s === "*" ? "all sources (default)" : s.replace(/_/g, " ")}
        </button>
      ))}
    </div>
  );
}

function RuleRow({ kind, row, sources, onChange, onRemove, isLast }) {
  const upd = (patch) => onChange({ ...row, ...patch });
  const updCharge = (patch) => onChange({ ...row, charge: { ...(row.charge || {}), ...patch } });
  const mode = row.charge?.mode || "free";
  return (
    <div className="rounded-lg border border-slate-700 bg-slate-900/40 p-4 mb-3">
      <div className="flex items-start justify-between gap-3 mb-3">
        <div className="flex-1">
          <div className="text-xs uppercase tracking-wide text-slate-500 mb-1">Applies to</div>
          <SourcePicker value={row.sources} options={sources} onChange={(v) => upd({ sources: v })} />
        </div>
        {!isLast && (
          <button type="button" onClick={onRemove} className="text-slate-500 hover:text-red-400" title="Remove row">
            <FaTimes />
          </button>
        )}
      </div>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <label className="text-xs text-slate-400">
          Free up to (minutes)
          <input type="number" min="0" className={inputCls} value={row.threshold_minutes ?? 0}
                 onChange={(e) => upd({ threshold_minutes: num(e.target.value) })} />
        </label>
        {kind.bound && (
          <label className="text-xs text-slate-400">
            Full night after (minutes)
            <input type="number" min="0" className={inputCls} value={row.full_night_after_minutes ?? 0}
                   onChange={(e) => upd({ full_night_after_minutes: num(e.target.value) })} />
          </label>
        )}
        {kind.hours && (
          <label className="text-xs text-slate-400">
            Max hours per extension
            <input type="number" min="1" className={inputCls} value={row.max_hours ?? 8}
                   onChange={(e) => upd({ max_hours: num(e.target.value, 1) })} />
          </label>
        )}
        {kind.hours && (
          <label className="text-xs text-slate-400">
            Beyond the bound
            <select className={inputCls} value={row.overflow || "refuse"} onChange={(e) => upd({ overflow: e.target.value })}>
              <option value="refuse">Refuse — extend by a night</option>
              <option value="full_night">Charge a full night</option>
            </select>
          </label>
        )}
        {kind.charge && (
          <label className="text-xs text-slate-400">
            Charge
            <select className={inputCls} value={mode} onChange={(e) => updCharge({ mode: e.target.value })}>
              <option value="free">Free</option>
              <option value="fixed">Fixed ₹</option>
              <option value="percent">% of one night</option>
            </select>
          </label>
        )}
        {kind.charge && mode === "fixed" && (
          <label className="text-xs text-slate-400">
            Amount (₹)
            <input type="number" min="0" step="1" className={inputCls} value={row.charge?.amount ?? 0}
                   onChange={(e) => updCharge({ amount: num(e.target.value) })} />
          </label>
        )}
        {kind.charge && mode === "percent" && (
          <label className="text-xs text-slate-400">
            Percent of one night
            <input type="number" min="0" max="100" step="1" className={inputCls} value={row.charge?.percent ?? 0}
                   onChange={(e) => updCharge({ percent: num(e.target.value) })} />
          </label>
        )}
        {kind.charge && (
          <label className="text-xs text-slate-400">
            Needs approval
            <select className={inputCls} value={row.approval || "on_change"} onChange={(e) => upd({ approval: e.target.value })}>
              {APPROVALS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
          </label>
        )}
      </div>
    </div>
  );
}

function blankRow(kind) {
  const r = { sources: [], threshold_minutes: 30 };
  if (kind.bound) r.full_night_after_minutes = 360;
  if (kind.charge) { r.charge = { mode: "fixed", amount: 300, percent: 25 }; r.approval = "on_change"; }
  if (kind.hours) { r.max_hours = 8; r.overflow = "refuse"; }
  return r;
}

// Keep the "*" row last so the first-match-wins order reads naturally.
const isStar = (r) => (r.sources || []).includes("*");
const sortRows = (rows) => [...rows].sort((a, b) => Number(isStar(a)) - Number(isStar(b)));

export default function ArrivalRulesPanel({ showToast }) {
  const [rules, setRules] = useState(null);
  const [saved, setSaved] = useState("");
  const [defaults, setDefaults] = useState(null);
  const [sources, setSources] = useState([]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    (async () => {
      try {
        const [r, cats] = await Promise.all([getArrivalRules(), getCategories()]);
        setRules(r.rules); setSaved(JSON.stringify(r.rules)); setDefaults(r.defaults);
        setSources(cats.families?.booking_source || []);
      } catch (e) { setError(e.message || "Failed to load rules."); }
    })();
  }, []);

  if (error) return <div className="p-4 rounded-lg bg-red-500/10 border border-red-500/30 text-red-300 text-sm">{error}</div>;
  if (!rules) return <Card><div className="p-6 text-slate-400 text-sm">Loading…</div></Card>;

  const dirty = JSON.stringify(rules) !== saved;
  const setKind = (key, rows) => setRules({ ...rules, [key]: rows });

  const save = async () => {
    setSaving(true);
    try {
      const res = await setArrivalRules(rules);
      setRules(res.rules); setSaved(JSON.stringify(res.rules));
      showToast("Arrival & departure rules saved.", "success");
    } catch (e) { showToast(e.message || "Save failed.", "error"); }
    finally { setSaving(false); }
  };

  return (
    <>
      <Card className="mb-5">
        <div className="p-5">
          <p className="text-slate-400 text-sm">
            How the desk handles guests who arrive early, arrive late, or want a few more hours at checkout.
            Rules are matched by booking source (first row that lists the source wins; the <b>all sources</b> row is the fallback).
            Fees are posted to the folio as room lines and appear on the GST invoice. Everything is recorded in the
            Arrival exceptions report.
          </p>
          <label className="flex items-center gap-2 mt-3 text-sm text-slate-300">
            <input type="checkbox" checked={!!rules.exempt_comp} onChange={(e) => setRules({ ...rules, exempt_comp: e.target.checked })} />
            Complimentary stays are never charged (the event is still recorded)
          </label>
        </div>
      </Card>

      {KINDS.map((kind) => {
        const rows = sortRows(rules[kind.key] || []);
        const starCount = rows.filter(isStar).length;
        return (
          <div key={kind.key} className="mb-6">
            <div className="text-slate-300 font-semibold mb-1">{kind.label}</div>
            <p className="text-slate-500 text-xs mb-3">{kind.hint}</p>
            {rows.map((row, i) => (
              <RuleRow
                key={i} kind={kind} row={row} sources={sources}
                isLast={isStar(row) && starCount === 1}
                onChange={(r) => setKind(kind.key, rows.map((x, j) => (j === i ? r : x)))}
                onRemove={() => setKind(kind.key, rows.filter((_, j) => j !== i))}
              />
            ))}
            <GhostButton onClick={() => setKind(kind.key, [blankRow(kind), ...rows])}>
              <FaPlus size={11} /> Add a source-specific row
            </GhostButton>
          </div>
        );
      })}

      <div className="flex justify-end gap-2 sticky bottom-3">
        <GhostButton onClick={() => defaults && setRules(JSON.parse(JSON.stringify(defaults)))} title="Reset to the values that shipped with the app">
          <FaUndo size={12} /> Reset to defaults
        </GhostButton>
        <PrimaryButton onClick={save} disabled={saving || !dirty}>
          <FaSave size={13} /> {saving ? "Saving…" : "Save rules"}
        </PrimaryButton>
      </div>
    </>
  );
}
