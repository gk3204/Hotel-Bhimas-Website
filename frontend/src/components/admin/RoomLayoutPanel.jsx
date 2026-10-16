// v7q — Room layout (Settings → "Room layout").
//
// The buildings and floors offered in the Rooms dropdowns, and the columns of the desk housekeeping
// grid. Each is a NUMBER - it is written onto the key card (BBFFRR), so it must match the door locks -
// with an optional name ("Main block", "Annexe", "Ground"). A value a room still uses cannot be removed.
import React, { useEffect, useState } from "react";
import { FaPlus, FaSave, FaTrash } from "react-icons/fa";
import { Card, GhostButton, PrimaryButton, inputCls } from "./BackofficeUI";
import { getRoomLayout, setRoomLayout } from "../../api/settings";

function ListEditor({ title, prefix, items, used, onChange }) {
  const set = (i, patch) => onChange(items.map((x, j) => (j === i ? { ...x, ...patch } : x)));
  const add = () => {
    const next = Math.max(0, ...items.map((x) => Number(x.value) || 0)) + 1;
    onChange([...items, { value: next, name: "" }]);
  };
  return (
    <div>
      <h3 className="text-[#E5C07B] font-semibold mb-2">{title}</h3>
      <div className="space-y-2">
        {items.map((x, i) => {
          const inUse = used.includes(Number(x.value));
          return (
            <div key={i} className="flex items-center gap-2">
              <span className="text-slate-400 text-sm w-6">{prefix}</span>
              <input type="number" min="1" max="99" className={`${inputCls} w-24`} value={x.value}
                     onChange={(e) => set(i, { value: e.target.value })} disabled={inUse}
                     title={inUse ? "Rooms use this number - it is on their key cards" : "1-99, written on the key card"} />
              <input className={`${inputCls} flex-1`} placeholder="Name (optional), e.g. Main block" maxLength={30}
                     value={x.name || ""} onChange={(e) => set(i, { name: e.target.value })} />
              <GhostButton onClick={() => onChange(items.filter((_, j) => j !== i))} disabled={inUse}
                           title={inUse ? "Rooms are on it - move them first" : "Remove"}>
                <FaTrash size={11} />
              </GhostButton>
            </div>
          );
        })}
      </div>
      <GhostButton className="mt-3" onClick={add}><FaPlus size={11} /> Add {title.toLowerCase().replace(/s$/, "")}</GhostButton>
    </div>
  );
}

export default function RoomLayoutPanel({ showToast }) {
  const [cfg, setCfg] = useState(null);
  const [saved, setSaved] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const load = () => getRoomLayout()
    .then((c) => { setCfg(c); setSaved(JSON.stringify({ b: c.buildings, f: c.floors })); })
    .catch((e) => setError(e.message || "Failed to load."));
  useEffect(() => { load(); }, []);

  if (error) return <div className="p-4 rounded-lg bg-red-500/10 border border-red-500/30 text-red-300 text-sm">{error}</div>;
  if (!cfg) return <Card><div className="p-6 text-slate-400 text-sm">Loading…</div></Card>;

  const dirty = JSON.stringify({ b: cfg.buildings, f: cfg.floors }) !== saved;
  const save = async () => {
    setSaving(true);
    try {
      const res = await setRoomLayout({
        buildings: cfg.buildings.map((x) => ({ value: Number(x.value), name: x.name || "" })),
        floors: cfg.floors.map((x) => ({ value: Number(x.value), name: x.name || "" })),
      });
      setCfg(res); setSaved(JSON.stringify({ b: res.buildings, f: res.floors }));
      showToast("Room layout saved.", "success");
    } catch (e) { showToast(e.message || "Save failed.", "error"); }
    finally { setSaving(false); }
  };

  return (
    <Card className="mb-5">
      <div className="p-5">
        <p className="text-slate-400 text-sm mb-4">
          The buildings and floors you pick from on <b>Rooms</b>, and the columns of the desk&apos;s
          housekeeping board (B1·F1, B1·F2 …). The <b>number</b> is written on the key card, so it must
          match the door locks; the name is only for people. A number that rooms are on cannot be changed
          or removed here — move those rooms first.
        </p>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-8 max-w-4xl">
          <ListEditor title="Buildings" prefix="B" items={cfg.buildings} used={cfg.used?.buildings || []}
                      onChange={(buildings) => setCfg({ ...cfg, buildings })} />
          <ListEditor title="Floors" prefix="F" items={cfg.floors} used={cfg.used?.floors || []}
                      onChange={(floors) => setCfg({ ...cfg, floors })} />
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
