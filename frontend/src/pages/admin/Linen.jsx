// Linen / laundry management (FE-9). Web-only: laundry board (stage counts + moves),
// item catalog, and per-room-type default sets. On the shared BackofficeUI kit.
import React, { useCallback, useEffect, useMemo, useState } from "react";
import { FaPlus, FaSave } from "react-icons/fa";
import {
  Card, Chip, Field, GhostButton, Modal, PageShell, PrimaryButton, SelectField, Tabs, inputCls, useToast,
} from "../../components/admin/BackofficeUI";
import { useConfirm } from "../../components/ConfirmDialog";
import * as api from "../../api/linen";

const TABS = [["board", "Laundry board"], ["items", "Items"], ["sets", "Room-type sets"]];

// Stage-move metadata: label + which api call + the source stage that caps the qty.
const MOVES = {
  send: { label: "Send to laundry", call: api.sendToLaundry, cap: "dirty" },
  receive: { label: "Receive laundered", call: api.receiveLaundry, cap: "at_laundry" },
  replenish: { label: "Replenish (add clean stock)", call: api.replenish, cap: null },
  issue: { label: "Issue / use", call: api.issue, cap: "clean" },
};

const num = (v) => Number(v || 0).toLocaleString("en-IN");

export default function Linen() {
  const [toast, showToast] = useToast();
  const { confirm } = useConfirm();
  const [tab, setTab] = useState("board");
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [move, setMove] = useState(null);       // { item, action }
  const [editItem, setEditItem] = useState(null); // item form modal

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const d = await api.listItems();
      setItems(d.items || []);
    } catch (e) {
      setError(e.message || "Failed to load linen.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  return (
    <PageShell icon="🧺" title="Linen & Laundry"
      subtitle="Track linen through the laundry cycle and set each room type's default linen set."
      toast={toast}
      right={tab === "items" ? (
        <PrimaryButton onClick={() => setEditItem({ launderable: true, unit: "pcs", reorder_threshold: 0, opening_clean: 0 })}>
          <FaPlus size={13} /> New item
        </PrimaryButton>
      ) : null}
    >
      <Tabs tabs={TABS} active={tab} onChange={setTab} />
      {error && <div className="mb-5 p-4 rounded-lg bg-red-500/10 border border-red-500/30 text-red-300 text-sm">{error}</div>}

      {tab === "board" && (
        <BoardTab items={items} loading={loading} onMove={(item, action) => setMove({ item, action })} />
      )}
      {tab === "items" && (
        <ItemsTab items={items} loading={loading} onEdit={setEditItem} onReload={load}
          confirm={confirm} showToast={showToast} />
      )}
      {tab === "sets" && <SetsTab items={items} showToast={showToast} />}

      {move && (
        <MoveModal move={move} onClose={() => setMove(null)}
          onDone={() => { setMove(null); load(); }} showToast={showToast} />
      )}
      {editItem && (
        <ItemModal value={editItem} onClose={() => setEditItem(null)}
          onDone={() => { setEditItem(null); load(); }} showToast={showToast} />
      )}
    </PageShell>
  );
}

function BoardTab({ items, loading, onMove }) {
  const launderable = items.filter((i) => i.launderable && i.is_active !== false);
  const consumables = items.filter((i) => !i.launderable && i.is_active !== false);
  if (loading) return <Card><div className="p-6 text-slate-400 text-sm">Loading…</div></Card>;

  return (
    <>
      <Card title="Launderable linen" className="mb-6">
        {launderable.length === 0 ? (
          <div className="p-6 text-slate-400 text-sm">No launderable items yet — add some on the Items tab.</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="text-slate-400 border-b border-slate-700">
                <tr>
                  <th className="px-4 py-3">Item</th>
                  <th className="px-4 py-3 text-right">Clean</th>
                  <th className="px-4 py-3 text-right">Dirty</th>
                  <th className="px-4 py-3 text-right">At laundry</th>
                  <th className="px-4 py-3 text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800">
                {launderable.map((i) => (
                  <tr key={i.id} className="hover:bg-slate-700/20">
                    <td className="px-4 py-3">{i.name} <span className="text-slate-500 text-xs">/ {i.unit}</span>
                      {i.low && <span className="ml-2"><Chip tone="warn">low</Chip></span>}</td>
                    <td className="px-4 py-3 text-right font-semibold text-emerald-300">{num(i.clean)}</td>
                    <td className="px-4 py-3 text-right text-amber-300">{num(i.dirty)}</td>
                    <td className="px-4 py-3 text-right text-blue-300">{num(i.at_laundry)}</td>
                    <td className="px-4 py-3">
                      <div className="flex gap-2 justify-end">
                        <GhostButton onClick={() => onMove(i, "send")} disabled={!i.dirty}>Send</GhostButton>
                        <GhostButton onClick={() => onMove(i, "receive")} disabled={!i.at_laundry}>Receive</GhostButton>
                        <GhostButton onClick={() => onMove(i, "replenish")}>Replenish</GhostButton>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card title="Consumables (soap / toiletries / amenities)">
        {consumables.length === 0 ? (
          <div className="p-6 text-slate-400 text-sm">No consumable items yet.</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="text-slate-400 border-b border-slate-700">
                <tr>
                  <th className="px-4 py-3">Item</th>
                  <th className="px-4 py-3 text-right">In stock</th>
                  <th className="px-4 py-3 text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800">
                {consumables.map((i) => (
                  <tr key={i.id} className="hover:bg-slate-700/20">
                    <td className="px-4 py-3">{i.name} <span className="text-slate-500 text-xs">/ {i.unit}</span>
                      {i.low && <span className="ml-2"><Chip tone="warn">low</Chip></span>}</td>
                    <td className="px-4 py-3 text-right font-semibold text-emerald-300">{num(i.clean)}</td>
                    <td className="px-4 py-3">
                      <div className="flex gap-2 justify-end">
                        <GhostButton onClick={() => onMove(i, "issue")} disabled={!i.clean}>Issue</GhostButton>
                        <GhostButton onClick={() => onMove(i, "replenish")}>Replenish</GhostButton>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </>
  );
}

function MoveModal({ move, onClose, onDone, showToast }) {
  const meta = MOVES[move.action];
  const capVal = meta.cap ? Number(move.item[meta.cap] || 0) : null;
  const [qty, setQty] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    const n = Number(qty);
    if (!n || n <= 0) { showToast("Enter a quantity.", "error"); return; }
    if (capVal !== null && n > capVal) { showToast(`Only ${capVal} available.`, "error"); return; }
    setBusy(true);
    try {
      await meta.call(move.item.id, n);
      showToast(`${meta.label}: ${n} ${move.item.unit}`, "success");
      onDone();
    } catch (e) {
      showToast(e.message || "Move failed.", "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal title={`${meta.label} — ${move.item.name}`} onClose={onClose}>
      {capVal !== null && (
        <p className="text-slate-400 text-sm mb-3">Available in {meta.cap.replace("_", " ")}: <b>{capVal}</b> {move.item.unit}</p>
      )}
      <Field label={`Quantity (${move.item.unit})`}>
        <input className={inputCls} type="number" min="1" autoFocus value={qty}
          onChange={(e) => setQty(e.target.value)} onKeyDown={(e) => e.key === "Enter" && submit()} />
      </Field>
      <div className="flex justify-end gap-3 mt-5">
        <GhostButton onClick={onClose}>Cancel</GhostButton>
        <PrimaryButton onClick={submit} disabled={busy}><FaSave size={13} /> {busy ? "Saving…" : "Confirm"}</PrimaryButton>
      </div>
    </Modal>
  );
}

function ItemsTab({ items, loading, onEdit, onReload, confirm, showToast }) {
  const remove = async (item) => {
    if (!(await confirm({ title: `Deactivate ${item.name}?`, message: "It stays in history but leaves the board.", tone: "danger" }))) return;
    try { await api.deactivateItem(item.id); showToast("Item deactivated."); onReload(); }
    catch (e) { showToast(e.message || "Failed.", "error"); }
  };
  if (loading) return <Card><div className="p-6 text-slate-400 text-sm">Loading…</div></Card>;

  return (
    <Card title="Item catalog">
      <div className="overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead className="text-slate-400 border-b border-slate-700">
            <tr>
              <th className="px-4 py-3">Name</th><th className="px-4 py-3">Unit</th>
              <th className="px-4 py-3">Type</th><th className="px-4 py-3 text-right">Reorder at</th>
              <th className="px-4 py-3">Status</th><th className="px-4 py-3 text-right">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-800">
            {items.map((i) => (
              <tr key={i.id} className={`hover:bg-slate-700/20 ${i.is_active === false ? "opacity-50" : ""}`}>
                <td className="px-4 py-3">{i.name}</td>
                <td className="px-4 py-3 text-slate-400">{i.unit}</td>
                <td className="px-4 py-3">{i.launderable ? <Chip tone="info">launderable</Chip> : <Chip>consumable</Chip>}</td>
                <td className="px-4 py-3 text-right text-slate-400">{num(i.reorder_threshold)}</td>
                <td className="px-4 py-3">{i.is_active === false ? <Chip tone="danger">inactive</Chip> : <Chip tone="ok">active</Chip>}</td>
                <td className="px-4 py-3">
                  <div className="flex gap-2 justify-end">
                    <GhostButton onClick={() => onEdit(i)}>Edit</GhostButton>
                    {i.is_active !== false && <GhostButton onClick={() => remove(i)}>Deactivate</GhostButton>}
                  </div>
                </td>
              </tr>
            ))}
            {items.length === 0 && <tr><td colSpan={6} className="px-4 py-8 text-center text-slate-400">No items yet.</td></tr>}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

function ItemModal({ value, onClose, onDone, showToast }) {
  const isEdit = !!value.id;
  const [form, setForm] = useState({
    name: value.name || "", unit: value.unit || "pcs", launderable: value.launderable !== false,
    reorder_threshold: value.reorder_threshold ?? 0, opening_clean: value.opening_clean ?? 0, notes: value.notes || "",
  });
  const [busy, setBusy] = useState(false);
  const set = (patch) => setForm((f) => ({ ...f, ...patch }));

  const save = async () => {
    if (!form.name || form.name.trim().length < 2) { showToast("Enter a name.", "error"); return; }
    setBusy(true);
    try {
      if (isEdit) {
        await api.updateItem(value.id, {
          name: form.name.trim(), unit: form.unit, launderable: form.launderable,
          reorder_threshold: Number(form.reorder_threshold) || 0, notes: form.notes,
        });
      } else {
        await api.createItem({
          name: form.name.trim(), unit: form.unit, launderable: form.launderable,
          reorder_threshold: Number(form.reorder_threshold) || 0,
          opening_clean: Number(form.opening_clean) || 0, notes: form.notes,
        });
      }
      showToast(isEdit ? "Item updated." : "Item created.");
      onDone();
    } catch (e) {
      showToast(e.message || "Save failed.", "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal title={isEdit ? `Edit ${value.name}` : "New linen item"} onClose={onClose}>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <Field label="Name *"><input className={inputCls} value={form.name} onChange={(e) => set({ name: e.target.value })} /></Field>
        <Field label="Unit"><input className={inputCls} value={form.unit} onChange={(e) => set({ unit: e.target.value })} placeholder="pcs" /></Field>
        <SelectField label="Type" value={form.launderable ? "launderable" : "consumable"}
          onChange={(e) => set({ launderable: e.target.value === "launderable" })}
          options={[{ value: "launderable", label: "Launderable (cycles through laundry)" }, { value: "consumable", label: "Consumable (soap / toiletries)" }]} />
        <Field label="Reorder threshold"><input className={inputCls} type="number" min="0" value={form.reorder_threshold}
          onChange={(e) => set({ reorder_threshold: e.target.value })} /></Field>
        {!isEdit && (
          <Field label="Opening stock (clean)"><input className={inputCls} type="number" min="0" value={form.opening_clean}
            onChange={(e) => set({ opening_clean: e.target.value })} /></Field>
        )}
      </div>
      <div className="flex justify-end gap-3 mt-5">
        <GhostButton onClick={onClose}>Cancel</GhostButton>
        <PrimaryButton onClick={save} disabled={busy}><FaSave size={13} /> {busy ? "Saving…" : "Save"}</PrimaryButton>
      </div>
    </Modal>
  );
}

function SetsTab({ items, showToast }) {
  const [roomTypes, setRoomTypes] = useState([]);
  const [rtId, setRtId] = useState("");
  const [qtys, setQtys] = useState({});   // item_id -> qty
  const [busy, setBusy] = useState(false);
  const activeItems = useMemo(() => items.filter((i) => i.is_active !== false), [items]);

  const load = useCallback(async () => {
    try {
      const d = await api.getSets();
      setRoomTypes(d.room_types || []);
      if (!rtId && d.room_types?.length) setRtId(String(d.room_types[0].room_type_id));
    } catch (e) { showToast(e.message || "Failed to load sets.", "error"); }
  }, [rtId, showToast]);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    const rt = roomTypes.find((t) => String(t.room_type_id) === String(rtId));
    const m = {};
    (rt?.items || []).forEach((it) => { m[it.item_id] = it.qty; });
    setQtys(m);
  }, [rtId, roomTypes]);

  const save = async () => {
    setBusy(true);
    try {
      const payload = Object.entries(qtys)
        .map(([item_id, qty]) => ({ item_id: Number(item_id), qty: Number(qty) || 0 }))
        .filter((r) => r.qty > 0);
      await api.updateSet(Number(rtId), payload);
      showToast("Default set saved.");
      await load();
    } catch (e) { showToast(e.message || "Save failed.", "error"); }
    finally { setBusy(false); }
  };

  return (
    <Card title="Room-type default linen sets"
      right={roomTypes.length > 0 ? (
        <select className={inputCls} style={{ maxWidth: 220 }} value={rtId} onChange={(e) => setRtId(e.target.value)}>
          {roomTypes.map((t) => <option key={t.room_type_id} value={t.room_type_id}>{t.name}</option>)}
        </select>
      ) : null}
    >
      <div className="p-5">
        <p className="text-slate-400 text-sm mb-4">
          Set how much of each item a room of this type gets. On checkout, the launderable items here move to <b>dirty</b>.
        </p>
        {activeItems.length === 0 ? (
          <p className="text-slate-500 text-sm">Add some items first (Items tab).</p>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {activeItems.map((i) => (
              <div key={i.id} className="flex items-center justify-between gap-3 bg-slate-900/40 rounded-lg px-3 py-2">
                <span className="text-sm">{i.name} <span className="text-slate-500 text-xs">/ {i.unit}</span></span>
                <input className={inputCls} style={{ maxWidth: 90 }} type="number" min="0"
                  value={qtys[i.id] ?? ""} placeholder="0"
                  onChange={(e) => setQtys((q) => ({ ...q, [i.id]: e.target.value }))} />
              </div>
            ))}
          </div>
        )}
        <div className="flex justify-end mt-5">
          <PrimaryButton onClick={save} disabled={busy || !rtId}><FaSave size={13} /> {busy ? "Saving…" : "Save set"}</PrimaryButton>
        </div>
      </div>
    </Card>
  );
}
