// One settings panel to rule them all (backlog v2 FE-12).
//
// Before this, ten config surfaces lived on ten different pages, each hand-rolling its own
// checkbox, its own dirty-tracking and its own save semantics (Reviews even PUT on every
// keystroke). A settings group is really just {load, save, fields} — so it is declared as
// data in settingsGroups.js and rendered here, which means the Settings hub and the origin
// pages show the SAME editor rather than two that can drift.
import React, { useCallback, useEffect, useMemo, useState } from "react";
import { FaSave, FaUndo } from "react-icons/fa";
import { Card, GhostButton, PrimaryButton, Spinner, inputCls } from "./BackofficeUI";

/** Gold-accented switch. The kit had no toggle, so every settings page hand-rolled one. */
export function Toggle({ checked, onChange, label, hint, disabled }) {
  return (
    <label className={`flex items-start gap-3 ${disabled ? "opacity-50" : "cursor-pointer"}`}>
      <input
        type="checkbox"
        className="mt-1 w-4 h-4 accent-[#E5C07B] shrink-0"
        checked={!!checked}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
      />
      <span>
        <span className="block text-slate-200 text-sm font-medium">{label}</span>
        {hint && <span className="block text-slate-500 text-xs mt-0.5">{hint}</span>}
      </span>
    </label>
  );
}

function FieldRow({ field, value, onChange }) {
  const { key, label, hint, type = "text", options, min, max, step, placeholder } = field;

  // A select over people (or rooms) cannot be declared statically — it goes stale the moment
  // one is added. `optionsLoad` fetches them at render; the declared `options` stay as the
  // fallback so the field is still usable when that fetch fails.
  const [loaded, setLoaded] = useState(null);
  useEffect(() => {
    if (!field.optionsLoad) return undefined;
    let alive = true;
    field.optionsLoad()
      .then((o) => { if (alive && Array.isArray(o)) setLoaded(o); })
      .catch(() => { /* keep the declared options */ });
    return () => { alive = false; };
  }, [field]);

  if (type === "toggle") {
    return <Toggle checked={value} onChange={onChange} label={label} hint={hint} />;
  }

  // A csv field is a list on the wire but a comma-separated string in the box.
  const shown = type === "csv" && Array.isArray(value) ? value.join(", ") : value ?? "";
  const common = {
    className: inputCls,
    value: shown,
    placeholder,
    onChange: (e) => onChange(e.target.value),
  };

  const renderInput = () => {
    if (type === "select") {
      const opts = loaded || options || [];
      // A <select> hands back a string. Send back the type the option carried, or a saved
      // numeric id would arrive as "2" and read as a change on every render.
      const numeric = opts.some((o) => typeof o === "object" && typeof o.value === "number");
      return (
        <select
          id={`cfg-${key}`}
          className={inputCls}
          value={shown}
          onChange={(e) => onChange(numeric ? Number(e.target.value) : e.target.value)}
        >
          {opts.map((o) => {
            const v = typeof o === "string" ? o : o.value;
            const l = typeof o === "string" ? o : o.label;
            return <option key={v} value={v}>{l}</option>;
          })}
        </select>
      );
    }
    if (type === "textarea") return <textarea id={`cfg-${key}`} rows={4} {...common} />;
    const htmlType = type === "csv" ? "text" : type;
    return <input id={`cfg-${key}`} type={htmlType} min={min} max={max} step={step} {...common} />;
  };

  return (
    <div>
      <label className="block text-sm font-semibold text-slate-300 mb-1" htmlFor={`cfg-${key}`}>
        {label}
      </label>
      {renderInput()}
      {hint && <p className="text-slate-500 text-xs mt-1">{hint}</p>}
    </div>
  );
}

/**
 * @param {object}   group   one entry from settingsGroups.js
 * @param {function} showToast
 */
export default function ConfigPanel({ group, showToast }) {
  const { title, description, fields, load, save, readOnlyNote, renderExtra } = group;
  const [saved, setSaved] = useState(null);     // last known server state
  const [form, setForm] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const data = await load();
      setSaved(data);
      setForm(data);
    } catch (e) {
      setError(e.message || "Could not load these settings.");
    } finally {
      setLoading(false);
    }
  }, [load]);

  useEffect(() => { refresh(); }, [refresh]);

  const dirty = useMemo(
    () => !!form && !!saved && fields.some((f) => JSON.stringify(form[f.key]) !== JSON.stringify(saved[f.key])),
    [form, saved, fields],
  );

  const set = (key, v) => setForm((f) => ({ ...f, [key]: v }));

  const submit = async () => {
    setSaving(true);
    try {
      // Send only what changed, coerced to the type the API expects.
      const payload = {};
      for (const f of fields) {
        let v = form[f.key];
        if (f.type === "csv" && Array.isArray(v)) v = v.join(", ");
        let was = saved[f.key];
        if (f.type === "csv" && Array.isArray(was)) was = was.join(", ");
        if (JSON.stringify(v) === JSON.stringify(was)) continue;
        if (f.type === "number") v = Number(v);
        if (f.type === "csv") v = String(v || "").split(",").map((s) => s.trim()).filter(Boolean);
        payload[f.key] = v;
      }
      const res = await save(payload);
      const next = res && typeof res === "object" ? res : { ...saved, ...payload };
      setSaved(next);
      setForm(next);
      showToast(`${title} saved.`, "success");
    } catch (e) {
      showToast(e.message || "Save failed.", "error");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Card
      title={title}
      right={dirty ? <span className="text-amber-300 text-xs">Unsaved changes</span> : null}
      className="mb-6"
    >
      <div className="p-5">
        {description && <p className="text-slate-400 text-sm mb-5">{description}</p>}

        {loading && (
          <div className="flex items-center gap-3 text-slate-400 text-sm py-4">
            <Spinner /> Loading…
          </div>
        )}
        {error && !loading && (
          <div className="p-4 rounded-lg bg-red-500/10 border border-red-500/30 text-red-300 text-sm">
            {error}
            <button onClick={refresh} className="ml-3 underline hover:text-red-200">Retry</button>
          </div>
        )}

        {!loading && !error && form && (
          <>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-x-6 gap-y-5">
              {fields.map((f) => (
                <div key={f.key} className={f.wide ? "md:col-span-2" : ""}>
                  <FieldRow field={f} value={form[f.key]} onChange={(v) => set(f.key, v)} />
                </div>
              ))}
            </div>

            {/* Read-only status a group wants to show alongside its editable fields. */}
            {renderExtra && <div className="mt-5">{renderExtra(saved)}</div>}
            {readOnlyNote && <p className="text-slate-500 text-xs mt-5">{readOnlyNote}</p>}

            <div className="flex justify-end gap-2 mt-6">
              <GhostButton onClick={() => setForm(saved)} disabled={!dirty || saving}>
                <FaUndo size={12} /> Discard
              </GhostButton>
              <PrimaryButton onClick={submit} disabled={!dirty || saving}>
                <FaSave size={13} /> {saving ? "Saving…" : "Save settings"}
              </PrimaryButton>
            </div>
          </>
        )}
      </div>
    </Card>
  );
}
