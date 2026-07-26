// Inventory / stock management (prompt 18c, slice 8).
// Stock items + append-only movement ledger + low-stock alerts. Built entirely through the shared
// BackofficeUI kit so it matches the Vendors / Companies / Compliance brand exactly.
import React, { useCallback, useEffect, useState } from "react";
import { FaBell, FaBoxOpen, FaDownload, FaPlus, FaSave, FaSearch } from "react-icons/fa";
import * as api from "../../api/stock";
import { useConfirm } from "../../components/ConfirmDialog";
import {
  Card, Chip, DataTable, Field, GhostButton, Modal, PageShell, PrimaryButton, SelectField,
  Spinner, Stat, Tabs, fmtDate, inputCls, money, useToast,
} from "../../components/admin/BackofficeUI";

const TABS = [
  ["items", "Items & stock"],
  ["movements", "Movements"],
  ["low", "Low stock"],
  ["settings", "Settings"],
];

const CATEGORIES = ["minibar", "toiletries", "linen", "supplies", "fnb", "cleaning", "other"];
const UNITS = ["pcs", "bottle", "kg", "litre", "pack", "roll", "set"];

/** Current quantity vs reorder point, at a glance. */
function StockLevelChip({ item }) {
  if (item.current_qty <= 0) return <Chip tone="danger">Out of stock</Chip>;
  if (item.is_low) return <Chip tone="warn">Low · {item.current_qty}</Chip>;
  return <Chip tone="ok">{item.current_qty}</Chip>;
}

export default function Inventory() {
  const [tab, setTab] = useState("items");
  const [toast, showToast] = useToast();

  return (
    <PageShell
      icon="📦"
      title="Inventory"
      subtitle="Minibar, toiletries, linen and supplies — stock levels, a full movement ledger, and low-stock alerts."
      toast={toast}
    >
      <Tabs tabs={TABS} active={tab} onChange={setTab} />
      {tab === "items" && <ItemsTab showToast={showToast} />}
      {tab === "movements" && <MovementsTab />}
      {tab === "low" && <LowStockTab showToast={showToast} />}
      {tab === "settings" && <SettingsTab showToast={showToast} />}
    </PageShell>
  );
}

/* ------------------------------------------------------------------ items */

const EMPTY_ITEM = {
  name: "", sku: "", category: "minibar", unit: "pcs", reorder_threshold: 5,
  opening_qty: 0, sale_price: "", gst_percent: "", notes: "",
};

function ItemsTab({ showToast }) {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [search, setSearch] = useState("");
  const [category, setCategory] = useState("");
  const [editing, setEditing] = useState(null);
  const [moving, setMoving] = useState(null);   // item being received/adjusted

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const res = await api.listItems({
        ...(search ? { q: search } : {}), ...(category ? { category } : {}),
      });
      setItems(res.data || []);
    } catch (e) {
      setError(e.message || "Failed to load stock items");
    } finally {
      setLoading(false);
    }
  }, [search, category]);

  useEffect(() => { load(); /* eslint-disable-next-line */ }, []);
  const { confirm } = useConfirm();

  const remove = async (it) => {
    if (!(await confirm({
      title: `Deactivate ${it.name}?`,
      message: "Past movements stay attributed to it.",
      confirmText: "Deactivate", tone: "danger",
    }))) return;
    try {
      await api.deactivateItem(it.id);
      showToast(`${it.name} deactivated`);
      load();
    } catch (e) {
      showToast(e.message || "Could not deactivate");
    }
  };

  return (
    <>
      <Card className="mb-6">
        <div className="p-5 grid grid-cols-1 md:grid-cols-4 gap-4 items-end">
          <Field label="Search">
            <input className={inputCls} value={search} placeholder="Name or SKU"
              onChange={(e) => setSearch(e.target.value)} onKeyDown={(e) => e.key === "Enter" && load()} />
          </Field>
          <SelectField label="Category" value={category} onChange={(e) => setCategory(e.target.value)}
            options={[{ value: "", label: "All categories" }, ...CATEGORIES.map((c) => ({ value: c, label: c }))]} />
          <PrimaryButton onClick={load}><FaSearch size={14} /> Search</PrimaryButton>
          <PrimaryButton onClick={() => setEditing({ ...EMPTY_ITEM })}>
            <FaPlus size={14} /> New item
          </PrimaryButton>
        </div>
      </Card>

      <Card title="Stock items" right={<span className="text-slate-400 text-sm">{items.length} item(s)</span>}>
        <DataTable
          columns={[
            { label: "Item", sort: (it) => it.name },
            { label: "Category", sort: (it) => it.category },
            "Unit",
            "In stock",
            { label: "Reorder at", sort: (it) => it.effective_threshold },
            { label: "Sale ₹", sort: (it) => it.sale_price },
            { label: "Status", sort: (it) => (it.is_active ? 1 : 0) },
            "",
          ]}
          rows={items} loading={loading} error={error}
          empty="No stock items yet. Add minibar drinks, toiletries and supplies here."
          renderRow={(it) => [
            <div key="n">
              <span className="font-semibold text-slate-100">{it.name}</span>
              {it.sku && <span className="text-slate-500 text-xs ml-2">{it.sku}</span>}
            </div>,
            <Chip key="c" tone="info">{it.category}</Chip>,
            it.unit,
            <StockLevelChip key="q" item={it} />,
            it.effective_threshold,
            it.sale_price != null ? money(it.sale_price) : "—",
            it.is_active ? <Chip key="s" tone="ok">Active</Chip> : <Chip key="s" tone="neutral">Inactive</Chip>,
            <div key="a" className="flex gap-2">
              <button onClick={() => setMoving({ item: it, mode: "receive" })} className="text-emerald-300 hover:underline text-xs">
                Receive
              </button>
              <button onClick={() => setMoving({ item: it, mode: "adjust" })} className="text-amber-300 hover:underline text-xs">
                Adjust
              </button>
              <button onClick={() => setEditing({ ...it })} className="text-sky-300 hover:underline text-xs">
                Edit
              </button>
              {it.is_active && (
                <button onClick={() => remove(it)} className="text-red-300 hover:underline text-xs">
                  Deactivate
                </button>
              )}
            </div>,
          ]}
        />
        <p className="px-5 py-4 text-slate-500 text-xs border-t border-slate-700">
          ⓘ Minibar charges posted from the housekeeping app automatically record a consumption
          movement here when the charge name matches a stock item.
        </p>
      </Card>

      {editing && (
        <ItemForm value={editing} onClose={() => setEditing(null)} showToast={showToast}
          onSaved={() => { setEditing(null); load(); }} />
      )}
      {moving && (
        <MovementForm entry={moving} onClose={() => setMoving(null)} showToast={showToast}
          onSaved={() => { setMoving(null); load(); }} />
      )}
    </>
  );
}

function ItemForm({ value, onClose, onSaved, showToast }) {
  const [form, setForm] = useState(value);
  const [saving, setSaving] = useState(false);
  const isEdit = !!value.id;

  const save = async () => {
    if (!form.name || form.name.trim().length < 2) { showToast("An item name is required"); return; }
    setSaving(true);
    try {
      const payload = {
        name: form.name.trim(), category: form.category, unit: form.unit,
        reorder_threshold: Number(form.reorder_threshold) || 0,
      };
      if (form.sku) payload.sku = form.sku.trim();
      if (form.sale_price !== "" && form.sale_price != null) payload.sale_price = Number(form.sale_price);
      if (form.gst_percent !== "" && form.gst_percent != null) payload.gst_percent = Number(form.gst_percent);
      if (form.notes) payload.notes = form.notes;
      if (isEdit) {
        payload.is_active = form.is_active !== false;
        await api.updateItem(value.id, payload);
      } else {
        payload.opening_qty = Number(form.opening_qty) || 0;
        await api.createItem(payload);
      }
      showToast(isEdit ? "Item updated" : "Item created");
      onSaved();
    } catch (e) {
      showToast(e.message || "Save failed");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal title={isEdit ? `Edit ${value.name}` : "New stock item"} onClose={onClose} wide>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <Field label="Name *" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
        <Field label="SKU / code" value={form.sku ?? ""} onChange={(e) => setForm({ ...form, sku: e.target.value })} />
        <SelectField label="Category" value={form.category}
          onChange={(e) => setForm({ ...form, category: e.target.value })} options={CATEGORIES} />
        <SelectField label="Unit" value={form.unit}
          onChange={(e) => setForm({ ...form, unit: e.target.value })} options={UNITS} />
        <Field label="Reorder threshold" type="number" value={form.reorder_threshold}
          hint="Alert when stock falls to or below this. 0 = use the configured default."
          onChange={(e) => setForm({ ...form, reorder_threshold: e.target.value })} />
        {!isEdit && (
          <Field label="Opening quantity" type="number" value={form.opening_qty}
            hint="Seeds the first ledger movement."
            onChange={(e) => setForm({ ...form, opening_qty: e.target.value })} />
        )}
        <Field label="Sale price ₹ (minibar)" type="number" value={form.sale_price ?? ""}
          onChange={(e) => setForm({ ...form, sale_price: e.target.value })} />
        <Field label="GST %" type="number" value={form.gst_percent ?? ""}
          onChange={(e) => setForm({ ...form, gst_percent: e.target.value })} />
      </div>
      <Field label="Notes">
        <textarea rows={2} className={inputCls} value={form.notes ?? ""}
          onChange={(e) => setForm({ ...form, notes: e.target.value })} />
      </Field>
      <div className="flex gap-3 mt-6">
        <PrimaryButton onClick={save} disabled={saving}>
          <FaSave size={14} /> {saving ? "Saving…" : "Save"}
        </PrimaryButton>
        <GhostButton onClick={onClose}>Cancel</GhostButton>
      </div>
    </Modal>
  );
}

function MovementForm({ entry, onClose, onSaved, showToast }) {
  const { item, mode } = entry;             // mode = "receive" | "adjust"
  const [qty, setQty] = useState("");
  const [unitCost, setUnitCost] = useState("");
  const [postExpense, setPostExpense] = useState(false);
  const [adjType, setAdjType] = useState("adjust");
  const [reason, setReason] = useState("");
  const [saving, setSaving] = useState(false);

  const save = async () => {
    setSaving(true);
    try {
      if (mode === "receive") {
        const n = Number(qty);
        if (!n || n <= 0) { showToast("Enter a quantity greater than zero"); setSaving(false); return; }
        await api.receiveStock(item.id, {
          qty: n,
          ...(unitCost !== "" ? { unit_cost: Number(unitCost) } : {}),
          post_expense: postExpense && unitCost !== "",
          ...(reason ? { reason } : {}),
        });
        showToast(`Received ${n} ${item.unit} of ${item.name}`);
      } else {
        const d = Number(qty);
        if (!d || d === 0) { showToast("Enter a non-zero change"); setSaving(false); return; }
        if (!reason || reason.trim().length < 2) { showToast("A reason is required"); setSaving(false); return; }
        await api.adjustStock(item.id, {
          delta_qty: adjType === "wastage" ? -Math.abs(d) : d,
          type: adjType, reason: reason.trim(),
        });
        showToast(`${item.name} adjusted`);
      }
      onSaved();
    } catch (e) {
      showToast(e.message || "Save failed");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal title={mode === "receive" ? `Receive stock — ${item.name}` : `Adjust stock — ${item.name}`} onClose={onClose}>
      <p className="text-slate-400 text-sm mb-4">
        Currently in stock: <span className="text-white font-semibold">{item.current_qty} {item.unit}</span>
      </p>
      {mode === "receive" ? (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <Field label="Quantity received *" type="number" value={qty}
            onChange={(e) => setQty(e.target.value)} />
          <Field label="Unit cost ₹" type="number" value={unitCost}
            onChange={(e) => setUnitCost(e.target.value)} />
          <Field label="Reason / note" value={reason} onChange={(e) => setReason(e.target.value)} />
          <label className="flex items-center gap-2 text-sm text-slate-300 mt-8">
            <input type="checkbox" checked={postExpense} onChange={(e) => setPostExpense(e.target.checked)} />
            Post the cost to the open cash shift as an expense
          </label>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <SelectField label="Type" value={adjType} onChange={(e) => setAdjType(e.target.value)}
            options={[{ value: "adjust", label: "Correction (count fix)" }, { value: "wastage", label: "Wastage / write-off" }]} />
          <Field label={adjType === "wastage" ? "Quantity wasted *" : "Change (+/−) *"} type="number" value={qty}
            hint={adjType === "wastage" ? "Positive number — it will reduce stock." : "e.g. 3 to add, -2 to remove."}
            onChange={(e) => setQty(e.target.value)} />
          <div className="md:col-span-2">
            <Field label="Reason *" value={reason} onChange={(e) => setReason(e.target.value)} />
          </div>
        </div>
      )}
      <div className="flex gap-3 mt-6">
        <PrimaryButton onClick={save} disabled={saving}>
          <FaBoxOpen size={14} /> {saving ? "Saving…" : "Save"}
        </PrimaryButton>
        <GhostButton onClick={onClose}>Cancel</GhostButton>
      </div>
    </Modal>
  );
}

/* ------------------------------------------------------------------ movements */

function MovementsTab() {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [type, setType] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const res = await api.listMovements({ ...(type ? { type } : {}) });
      setRows(res.data || []);
    } catch (e) {
      setError(e.message || "Failed to load movements");
    } finally {
      setLoading(false);
    }
  }, [type]);

  useEffect(() => { load(); /* eslint-disable-next-line */ }, [type]);

  const toneFor = (t) => (t === "receive" ? "ok" : t === "consume" ? "info" : t === "wastage" ? "danger" : "warn");

  return (
    <>
      <Card className="mb-6">
        <div className="p-5 grid grid-cols-1 md:grid-cols-4 gap-4 items-end">
          <SelectField label="Movement type" value={type} onChange={(e) => setType(e.target.value)}
            options={[{ value: "", label: "All types" },
              ...["receive", "consume", "adjust", "wastage"].map((t) => ({ value: t, label: t }))]} />
        </div>
      </Card>
      <Card title="Stock movements (append-only ledger)"
        right={<span className="text-slate-400 text-sm">{rows.length}</span>}>
        <DataTable
          columns={["When", "Item", "Type", "Change", "Balance", "Reason"]}
          rows={rows} loading={loading} error={error}
          empty="No stock movements yet."
          renderRow={(m) => [
            fmtDate(m.created_at),
            <span key="i" className="font-semibold text-slate-100">{m.item_name}</span>,
            <Chip key="t" tone={toneFor(m.type)}>{m.type}</Chip>,
            <span key="d" className={m.delta_qty < 0 ? "text-red-300" : "text-emerald-300"}>
              {m.delta_qty > 0 ? `+${m.delta_qty}` : m.delta_qty}
            </span>,
            m.qty_after,
            m.reason,
          ]}
        />
      </Card>
    </>
  );
}

/* ------------------------------------------------------------------ low stock */

function LowStockTab({ showToast }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      setData(await api.getLowStock());
    } catch (e) {
      setError(e.message || "Failed to load low-stock list");
      setData(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); /* eslint-disable-next-line */ }, []);

  const runAlerts = async () => {
    setBusy(true);
    try {
      const res = await api.runAlerts();
      showToast(`Sweep done — ${res.low_stock_alerts ?? 0} alert(s) sent to the owner`);
      load();
    } catch (e) {
      showToast(e.message || "Sweep failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <Card className="mb-6">
        <div className="p-5 flex flex-wrap gap-4 items-center justify-between">
          <div className="flex gap-4">
            <Stat label="Items to reorder" value={data?.total ?? "—"}
              tone={data?.total ? "text-amber-300" : ""} />
          </div>
          <div className="flex gap-3">
            <PrimaryButton onClick={load}><FaSearch size={14} /> Refresh</PrimaryButton>
            <GhostButton onClick={runAlerts} disabled={busy}>
              <FaBell size={14} /> {busy ? "Running…" : "Send low-stock alerts now"}
            </GhostButton>
            <GhostButton onClick={() => api.exportStockReport("pdf")}>
              <FaDownload size={14} /> Valuation PDF
            </GhostButton>
          </div>
        </div>
        <p className="px-5 pb-5 text-slate-500 text-xs">
          ⓘ Alerts also fire automatically on the scheduled sweep. Each item is chased once per low-stock
          episode — receiving it back above the reorder point re-arms the alert.
        </p>
      </Card>

      <Card title="Low stock — reorder now">
        <DataTable
          columns={["Item", "Category", "In stock", "Reorder at", "Supplier"]}
          rows={data?.data} loading={loading} error={error}
          empty="Nothing is low. Every item is above its reorder point."
          renderRow={(it) => [
            <span key="n" className="font-semibold text-slate-100">{it.name}</span>,
            <Chip key="c" tone="info">{it.category}</Chip>,
            <Chip key="q" tone={it.current_qty <= 0 ? "danger" : "warn"}>{it.current_qty} {it.unit}</Chip>,
            it.effective_threshold,
            it.vendor_name,
          ]}
        />
      </Card>
    </>
  );
}

/* ------------------------------------------------------------------ settings */

function SettingsTab({ showToast }) {
  const [cfg, setCfg] = useState(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    api.getStockConfig().then(setCfg).catch((e) => setError(e.message || "Failed to load settings"));
  }, []);

  const save = async () => {
    setSaving(true);
    try {
      await api.updateStockConfig({
        low_stock_alerts_enabled: cfg.low_stock_alerts_enabled,
        low_stock_default_threshold: Number(cfg.low_stock_default_threshold) || 0,
      });
      showToast("Settings saved");
    } catch (e) {
      showToast(e.message || "Save failed");
    } finally {
      setSaving(false);
    }
  };

  if (error) return <Card><div className="p-8 text-center text-red-300">{error}</div></Card>;
  if (!cfg) return <Card><div className="p-12"><Spinner /></div></Card>;

  return (
    <Card title="Inventory settings">
      <div className="p-6 grid grid-cols-1 md:grid-cols-2 gap-6 max-w-2xl">
        <label className="flex items-center gap-3 text-slate-200">
          <input type="checkbox" checked={cfg.low_stock_alerts_enabled}
            onChange={(e) => setCfg({ ...cfg, low_stock_alerts_enabled: e.target.checked })} />
          WhatsApp the owner on low stock
        </label>
        <Field label="Default reorder threshold" type="number" value={cfg.low_stock_default_threshold}
          hint="Used for items whose own threshold is left at 0."
          onChange={(e) => setCfg({ ...cfg, low_stock_default_threshold: e.target.value })} />
      </div>
      <div className="px-6 pb-6">
        <PrimaryButton onClick={save} disabled={saving}>
          <FaSave size={14} /> {saving ? "Saving…" : "Save settings"}
        </PrimaryButton>
      </div>
    </Card>
  );
}
