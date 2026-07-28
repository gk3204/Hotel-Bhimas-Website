// Operational Settings hub (F-A backbone). Admin edits the option-lists that used to be
// hard-coded (expense / maintenance / complaint / menu categories) and the registration-slip
// rules text — no redeploy needed. Changes flow to the desk dropdowns and backend validation.
// On the shared BackofficeUI kit.
import React, { useCallback, useEffect, useMemo, useState } from "react";
import { FaTags, FaFileSignature, FaPlus, FaTimes, FaUndo, FaSave } from "react-icons/fa";
import {
  Card, GhostButton, PageShell, PrimaryButton, inputCls, useToast,
} from "../../components/admin/BackofficeUI";
import {
  getCategories, setCategoryList, getRegistrationRules, setRegistrationRules,
} from "../../api/settings";

const FAMILY_META = {
  expense: { label: "Expense categories", hint: "Petty-cash expense types the desk picks when logging a shift expense." },
  maintenance: { label: "Maintenance categories", hint: "Ticket types when raising a maintenance job." },
  complaint: { label: "Complaint categories", hint: "Guest-complaint types logged at the front desk." },
  menu: { label: "Room-service menu categories", hint: "Sections your room-service menu items are grouped under." },
};

// A category is stored as a lowercase slug; show it title-cased for readability.
const pretty = (s) => (s || "").replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
const toSlug = (s) => (s || "").trim().toLowerCase().replace(/[\s-]+/g, "_").replace(/[^a-z0-9_]/g, "");

function CategoryEditor({ family, meta, items, defaults, onSave }) {
  const [list, setList] = useState(items);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => { setList(items); }, [items]);

  const dirty = useMemo(
    () => JSON.stringify(list) !== JSON.stringify(items),
    [list, items],
  );

  const add = () => {
    const slug = toSlug(draft);
    if (!slug) return;
    if (slug.length < 2) return;
    if (list.includes(slug)) { setDraft(""); return; }
    setList([...list, slug]);
    setDraft("");
  };
  const remove = (slug) => setList(list.filter((c) => c !== slug));

  const save = async () => {
    if (list.length === 0) return;
    setSaving(true);
    try {
      await onSave(family, list);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Card
      title={meta.label}
      right={<span className="text-slate-500 text-xs">{list.length} option{list.length === 1 ? "" : "s"}</span>}
      className="mb-5"
    >
      <div className="p-5">
        <p className="text-slate-400 text-sm mb-4">{meta.hint}</p>
        <div className="flex flex-wrap gap-2 mb-4">
          {list.map((slug) => (
            <span key={slug}
              className="inline-flex items-center gap-2 px-3 py-1.5 rounded-full text-sm bg-slate-700/60 text-slate-100 border border-slate-600">
              {pretty(slug)}
              <button type="button" onClick={() => remove(slug)}
                disabled={list.length <= 1}
                title={list.length <= 1 ? "At least one option is required" : "Remove"}
                className="text-slate-400 hover:text-red-300 disabled:opacity-30 disabled:hover:text-slate-400">
                <FaTimes size={11} />
              </button>
            </span>
          ))}
          {list.length === 0 && <span className="text-slate-500 text-sm italic">No options — add at least one.</span>}
        </div>
        <div className="flex items-center gap-2">
          <input
            className={inputCls}
            style={{ maxWidth: 260 }}
            placeholder="Add a category…"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); add(); } }}
          />
          <GhostButton onClick={add} disabled={!toSlug(draft)}><FaPlus size={12} /> Add</GhostButton>
          <div className="flex-1" />
          <GhostButton
            onClick={() => setList(defaults)}
            disabled={JSON.stringify(list) === JSON.stringify(defaults)}
            title="Reset to the values that shipped with the app">
            <FaUndo size={12} /> Reset
          </GhostButton>
          <PrimaryButton onClick={save} disabled={!dirty || saving || list.length === 0}>
            <FaSave size={13} /> {saving ? "Saving…" : "Save"}
          </PrimaryButton>
        </div>
        {draft && !toSlug(draft) && (
          <p className="text-amber-300 text-xs mt-2">Use letters, numbers and underscores only.</p>
        )}
      </div>
    </Card>
  );
}

export default function Settings() {
  const [toast, showToast] = useToast();
  const [families, setFamilies] = useState(null);
  const [defaults, setDefaults] = useState({});
  const [rules, setRules] = useState("");
  const [rulesSaved, setRulesSaved] = useState("");
  const [rulesSaving, setRulesSaving] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setError("");
    try {
      const [cats, rr] = await Promise.all([getCategories(), getRegistrationRules()]);
      setFamilies(cats.families || {});
      setDefaults(cats.defaults || {});
      setRules(rr.text || "");
      setRulesSaved(rr.text || "");
    } catch (e) {
      setError(e.message || "Failed to load settings.");
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const saveCategory = async (family, items) => {
    try {
      const res = await setCategoryList(family, items);
      setFamilies((f) => ({ ...f, [family]: res.items }));
      showToast(`${FAMILY_META[family]?.label || family} saved.`, "success");
    } catch (e) {
      showToast(e.message || "Save failed.", "error");
      throw e;
    }
  };

  const saveRules = async () => {
    setRulesSaving(true);
    try {
      const res = await setRegistrationRules(rules);
      setRules(res.text || "");
      setRulesSaved(res.text || "");
      showToast("Registration-slip rules saved.", "success");
    } catch (e) {
      showToast(e.message || "Save failed.", "error");
    } finally {
      setRulesSaving(false);
    }
  };

  return (
    <PageShell
      icon="⚙️"
      title="Settings"
      subtitle="Editable option-lists and printed text — change these without a redeploy."
      toast={toast}
    >
      {error && (
        <div className="mb-5 p-4 rounded-lg bg-red-500/10 border border-red-500/30 text-red-300 text-sm">{error}</div>
      )}

      <div className="flex items-center gap-2 text-slate-300 font-semibold mb-3">
        <FaTags className="text-[#E5C07B]" /> Category lists
      </div>
      {families === null ? (
        <Card className="mb-5"><div className="p-6 text-slate-400 text-sm">Loading…</div></Card>
      ) : (
        Object.keys(FAMILY_META).map((family) => (
          <CategoryEditor
            key={family}
            family={family}
            meta={FAMILY_META[family]}
            items={families[family] || []}
            defaults={defaults[family] || []}
            onSave={saveCategory}
          />
        ))
      )}

      <div className="flex items-center gap-2 text-slate-300 font-semibold mb-3 mt-8">
        <FaFileSignature className="text-[#E5C07B]" /> Registration-slip rules
      </div>
      <Card>
        <div className="p-5">
          <p className="text-slate-400 text-sm mb-3">
            Terms of stay printed on the guest registration slip (one rule per line). The guest signs below these.
          </p>
          <textarea
            value={rules}
            onChange={(e) => setRules(e.target.value)}
            rows={8}
            className="w-full px-4 py-3 bg-slate-900/50 border border-slate-600 rounded-lg text-white text-sm font-mono focus:outline-none focus:border-[#E5C07B] focus:ring-2 focus:ring-[#E5C07B]/20 transition"
            placeholder="1. Check-out time is 24 hours from check-in…"
          />
          <div className="flex justify-end mt-3">
            <PrimaryButton onClick={saveRules} disabled={rulesSaving || rules === rulesSaved}>
              <FaSave size={13} /> {rulesSaving ? "Saving…" : "Save rules"}
            </PrimaryButton>
          </div>
        </div>
      </Card>
    </PageShell>
  );
}
