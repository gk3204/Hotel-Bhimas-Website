import React, { useState } from "react";
import { FaTimes, FaWrench } from "react-icons/fa";
import { createTicket } from "../api/maintenance";
import { useCategoryList, prettyCategory } from "../utils/useCategoryList";

// Shipped defaults only — the live lists are edited in admin Settings and win (F-A / FE-6).
// This modal used to hard-code them, so a category the admin added never reached the staff
// tablet and one they removed was still offered here.
const CATEGORY_DEFAULTS = ["electrical", "plumbing", "carpentry", "appliance", "lock", "other"];
const PRIORITY_DEFAULTS = ["low", "normal", "high", "urgent"];

const inputCls =
  "w-full px-4 py-2 bg-slate-900/50 border border-slate-600 rounded-lg text-white focus:outline-none focus:border-[#E5C07B] focus:ring-2 focus:ring-[#E5C07B]/20 transition";

// Reusable "raise a maintenance ticket" modal. `rooms` is an optional [{room_id, room_number}]
// picker; when omitted the ticket is a common-area ticket (no room).
export default function RaiseTicketModal({ open, onClose, onRaised, rooms = [], defaultRoomId = null }) {
  const [roomId, setRoomId] = useState(defaultRoomId ?? "");
  const [area, setArea] = useState("");
  const [category, setCategory] = useState("other");
  const [priority, setPriority] = useState("normal");
  const [issue, setIssue] = useState("");
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState("");
  const categories = useCategoryList("maintenance", CATEGORY_DEFAULTS);
  const priorities = useCategoryList("priority", PRIORITY_DEFAULTS);

  // The shipped default may not survive an admin edit, so fall back to the list's first
  // entry — derived at render rather than written back into state, which would fight the
  // user's own selection on every re-render.
  const effCategory = categories.includes(category) ? category : (categories[0] || category);
  const effPriority = priorities.includes(priority) ? priority : (priorities[0] || priority);

  if (!open) return null;

  const submit = async () => {
    if (issue.trim().length < 3) {
      setErr("Please describe the issue.");
      return;
    }
    setErr("");
    setSaving(true);
    try {
      await createTicket({
        room_id: roomId ? Number(roomId) : null,
        area: area || null,
        category: effCategory,
        priority: effPriority,
        issue: issue.trim(),
      });
      setSaving(false);
      setIssue("");
      setArea("");
      onRaised?.();
      onClose?.();
    } catch (e) {
      setSaving(false);
      setErr(e.message);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/70 backdrop-blur-sm flex items-center justify-center z-50 p-4">
      <div className="bg-gradient-to-br from-slate-800 to-slate-900 p-6 rounded-2xl w-full max-w-md border border-slate-700 shadow-2xl">
        <h2 className="text-xl font-bold mb-4 text-[#E5C07B] flex items-center gap-2">
          <FaWrench /> Raise maintenance ticket
        </h2>

        {err && (
          <div className="mb-3 p-2 rounded-lg bg-red-500/10 border border-red-500 text-red-300 text-sm">{err}</div>
        )}

        {rooms.length > 0 && (
          <>
            <label className="block mb-1 text-sm font-semibold text-slate-300">Room (optional)</label>
            <select className={`${inputCls} mb-3`} value={roomId} onChange={(e) => setRoomId(e.target.value)}>
              <option value="">Common area / none</option>
              {rooms.map((r) => (
                <option key={r.room_id} value={r.room_id}>
                  {r.room_number}
                </option>
              ))}
            </select>
          </>
        )}

        <label className="block mb-1 text-sm font-semibold text-slate-300">Area / location (optional)</label>
        <input className={`${inputCls} mb-3`} value={area} placeholder="e.g. Lobby AC" onChange={(e) => setArea(e.target.value)} />

        <div className="grid grid-cols-2 gap-3 mb-3">
          <div>
            <label className="block mb-1 text-sm font-semibold text-slate-300">Category</label>
            <select className={inputCls} value={effCategory} onChange={(e) => setCategory(e.target.value)}>
              {categories.map((c) => (
                <option key={c} value={c}>
                  {prettyCategory(c)}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="block mb-1 text-sm font-semibold text-slate-300">Priority</label>
            <select className={inputCls} value={effPriority} onChange={(e) => setPriority(e.target.value)}>
              {priorities.map((p) => (
                <option key={p} value={p}>
                  {prettyCategory(p)}
                </option>
              ))}
            </select>
          </div>
        </div>

        <label className="block mb-1 text-sm font-semibold text-slate-300">Issue</label>
        <textarea
          className={`${inputCls} mb-4`}
          rows={3}
          value={issue}
          placeholder="e.g. AC not cooling in room 101"
          onChange={(e) => setIssue(e.target.value)}
        />

        <div className="flex justify-end gap-3">
          <button onClick={onClose} className="bg-slate-700 hover:bg-slate-600 px-5 py-2 rounded-lg flex items-center gap-2">
            <FaTimes /> Cancel
          </button>
          <button
            onClick={submit}
            disabled={saving}
            className="bg-gradient-to-r from-[#E5C07B] to-[#D4AF37] text-slate-900 font-bold px-5 py-2 rounded-lg transition-all hover:scale-105 disabled:opacity-50"
          >
            {saving ? "Raising..." : "Raise ticket"}
          </button>
        </div>
      </div>
    </div>
  );
}
