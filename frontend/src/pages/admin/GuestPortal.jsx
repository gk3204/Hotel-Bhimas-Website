// Guest portal admin (prompt 18d, slice 10) — the desk/owner side of the in-room QR portal:
// fulfil guest requests, manage the room-service menu, and toggle features. On the BackofficeUI kit.
import React, { useCallback, useEffect, useState } from "react";
import { FaCheck, FaPlus, FaSave, FaSearch, FaTimes } from "react-icons/fa";
import * as api from "../../api/portal";
import { useConfirm } from "../../components/ConfirmDialog";
import {
  Card, Chip, DataTable, Field, GhostButton, Modal, PageShell, PrimaryButton, SelectField,
  Spinner, Tabs, fmtDate, inputCls, money, useToast,
} from "../../components/admin/BackofficeUI";

const TABS = [
  ["requests", "Guest requests"],
  ["menu", "Room-service menu"],
  ["settings", "Settings"],
];

const TYPE_LABEL = {
  room_service: "Room service", wifi: "WiFi", wakeup: "Wake-up", cab: "Cab", checkout: "Checkout",
};
const TYPE_TONE = {
  room_service: "gold", wifi: "info", wakeup: "info", cab: "info", checkout: "warn",
};
const STATUS_TONE = {
  requested: "warn", acknowledged: "info", completed: "ok", dismissed: "neutral",
};

export default function GuestPortal() {
  const [tab, setTab] = useState("requests");
  const [toast, showToast] = useToast();
  return (
    <PageShell
      icon="📱"
      title="Guest Portal"
      subtitle="In-room QR service — fulfil guest requests, manage the room-service menu, and toggle what guests can do."
      toast={toast}
    >
      <Tabs tabs={TABS} active={tab} onChange={setTab} />
      {tab === "requests" && <RequestsTab showToast={showToast} />}
      {tab === "menu" && <MenuTab showToast={showToast} />}
      {tab === "settings" && <SettingsTab showToast={showToast} />}
    </PageShell>
  );
}

/* ------------------------------------------------------------------ requests */

function RequestsTab({ showToast }) {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [type, setType] = useState("");
  const [openOnly, setOpenOnly] = useState(true);
  const [detail, setDetail] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const res = await api.listRequests({ ...(type ? { type } : {}), open_only: openOnly });
      setRows(res.data || []);
    } catch (e) {
      setError(e.message || "Failed to load requests");
    } finally {
      setLoading(false);
    }
  }, [type, openOnly]);

  useEffect(() => { load(); /* eslint-disable-next-line */ }, [type, openOnly]);

  const act = async (fn, msg) => {
    try { await fn(); showToast(msg); load(); }
    catch (e) { showToast(e.message || "Action failed"); }
  };

  return (
    <>
      <Card className="mb-6">
        <div className="p-5 grid grid-cols-1 md:grid-cols-4 gap-4 items-end">
          <SelectField label="Type" value={type} onChange={(e) => setType(e.target.value)}
            options={[{ value: "", label: "All types" },
              ...Object.entries(TYPE_LABEL).map(([v, l]) => ({ value: v, label: l }))]} />
          <label className="flex items-center gap-2 text-slate-300 text-sm mt-8">
            <input type="checkbox" checked={openOnly} onChange={(e) => setOpenOnly(e.target.checked)} />
            Open only
          </label>
          <PrimaryButton onClick={load}><FaSearch size={14} /> Refresh</PrimaryButton>
        </div>
      </Card>

      <Card title="Guest requests" right={<span className="text-slate-400 text-sm">{rows.length}</span>}>
        <DataTable
          columns={["#", "Room / guest", "Type", "Detail", "Amount", "Status", "When", ""]}
          rows={rows} loading={loading} error={error}
          empty={openOnly ? "No open requests — you're all caught up." : "No requests."}
          renderRow={(r) => [
            `#${r.id}`,
            <div key="g">
              <span className="text-slate-100">Room {r.room_number || "—"}</span>
              {r.guest_name && <span className="text-slate-500 text-xs ml-1">{r.guest_name}</span>}
            </div>,
            <Chip key="t" tone={TYPE_TONE[r.type] || "neutral"}>{TYPE_LABEL[r.type] || r.type}</Chip>,
            <span key="d" className="text-slate-300 text-xs max-w-xs inline-block truncate" title={r.note || ""}>
              {r.note || (r.payload?.items ? `${r.payload.items.length} item(s)` : "—")}
            </span>,
            r.amount != null ? money(r.amount) : "—",
            <Chip key="s" tone={STATUS_TONE[r.status] || "neutral"}>{r.status}</Chip>,
            fmtDate(r.created_at),
            <div key="a" className="flex gap-2">
              <button onClick={() => setDetail(r)} className="text-sky-300 hover:underline text-xs">View</button>
              {["requested", "acknowledged"].includes(r.status) && (
                <>
                  <button onClick={() => act(() => api.completeRequest(r.id), `Request #${r.id} completed`)}
                    className="text-emerald-300 hover:underline text-xs">Complete</button>
                  <button onClick={() => act(() => api.dismissRequest(r.id), `Request #${r.id} dismissed`)}
                    className="text-red-300 hover:underline text-xs">Dismiss</button>
                </>
              )}
            </div>,
          ]}
        />
        <p className="px-5 py-4 text-slate-500 text-xs border-t border-slate-700">
          ⓘ Completing a <b>room service</b> order posts the food charges to the guest's folio. A
          <b> checkout</b> request is a heads-up — finish the settlement on the reception desktop as usual.
        </p>
      </Card>

      {detail && <RequestDetail request={detail} onClose={() => setDetail(null)} showToast={showToast}
        onChanged={load} />}
    </>
  );
}

function RequestDetail({ request: r, onClose, showToast, onChanged }) {
  const [code, setCode] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const act = async (fn, msg) => {
    setBusy(true);
    try { await fn(); showToast(msg); onChanged(); onClose(); }
    catch (e) { showToast(e.message || "Action failed"); }
    finally { setBusy(false); }
  };
  const isOpen = ["requested", "acknowledged"].includes(r.status);
  const items = r.payload?.items || [];

  return (
    <Modal title={`Request #${r.id} · ${TYPE_LABEL[r.type] || r.type}`} onClose={onClose}>
      <div className="space-y-3 text-sm">
        <div className="text-slate-400">Room {r.room_number || "—"}{r.guest_name ? ` · ${r.guest_name}` : ""}</div>
        {items.length > 0 && (
          <div className="bg-slate-900/50 border border-slate-700 rounded-lg p-3">
            {items.map((it, i) => (
              <div key={i} className="flex justify-between text-slate-200">
                <span>{it.qty}× {it.name}</span><span>{money((it.unit_price || 0) * (it.qty || 1))}</span>
              </div>
            ))}
            <div className="flex justify-between border-t border-slate-700 mt-2 pt-2 font-semibold text-[#E5C07B]">
              <span>Total (excl. GST added on folio)</span><span>{money(r.amount)}</span>
            </div>
          </div>
        )}
        {r.payload && !items.length && (
          <pre className="bg-slate-900/50 border border-slate-700 rounded-lg p-3 text-xs text-slate-300 whitespace-pre-wrap">
            {JSON.stringify(r.payload, null, 2)}
          </pre>
        )}
        {r.note && <div className="text-slate-300">Note: {r.note}</div>}

        {isOpen && r.type === "wifi" && (
          <Field label="WiFi code to issue"><input className={inputCls} value={code}
            onChange={(e) => setCode(e.target.value)} placeholder="e.g. BHIMAS-4821" /></Field>
        )}
        {isOpen && (
          <Field label="Resolution note (optional)"><input className={inputCls} value={note}
            onChange={(e) => setNote(e.target.value)} /></Field>
        )}
      </div>

      {isOpen && (
        <div className="flex gap-3 mt-5">
          <PrimaryButton disabled={busy}
            onClick={() => act(() => api.completeRequest(r.id, { code: code || undefined, note: note || undefined }),
              `Request #${r.id} completed`)}>
            <FaCheck size={13} /> Complete
          </PrimaryButton>
          <GhostButton disabled={busy}
            onClick={() => act(() => api.ackRequest(r.id), `Request #${r.id} acknowledged`)}>
            Acknowledge
          </GhostButton>
          <GhostButton disabled={busy}
            onClick={() => act(() => api.dismissRequest(r.id, { note: note || undefined }), `Request #${r.id} dismissed`)}>
            <FaTimes size={13} /> Dismiss
          </GhostButton>
        </div>
      )}
    </Modal>
  );
}

/* ------------------------------------------------------------------ menu */

const EMPTY_MENU = { name: "", description: "", category: "food", price: "", gst_percent: "", is_available: true, sort_order: 0 };
const MENU_CATEGORIES = ["food", "beverage", "snack", "service"];

function MenuTab({ showToast }) {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [editing, setEditing] = useState(null);
  const { confirm } = useConfirm();

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try { setRows((await api.listMenu()).data || []); }
    catch (e) { setError(e.message || "Failed to load the menu"); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { load(); /* eslint-disable-next-line */ }, []);

  const remove = async (m) => {
    if (!(await confirm({
      title: `Make "${m.name}" unavailable?`,
      message: "Guests won't see it on the room-service menu.",
      confirmText: "Hide item", tone: "danger",
    }))) return;
    try { await api.deactivateMenuItem(m.id); showToast(`${m.name} hidden`); load(); }
    catch (e) { showToast(e.message || "Could not update"); }
  };

  return (
    <>
      <Card className="mb-6">
        <div className="p-5 flex justify-end">
          <PrimaryButton onClick={() => setEditing({ ...EMPTY_MENU })}><FaPlus size={14} /> New menu item</PrimaryButton>
        </div>
      </Card>
      <Card title="Room-service menu" right={<span className="text-slate-400 text-sm">{rows.length}</span>}>
        <DataTable
          columns={[
            { label: "Item", sort: (m) => m.name },
            { label: "Category", sort: (m) => m.category },
            { label: "Price", sort: (m) => m.price },
            { label: "GST %", sort: (m) => m.gst_percent },
            { label: "Available", sort: (m) => (m.is_available ? 1 : 0) },
            "",
          ]}
          rows={rows} loading={loading} error={error}
          empty="No menu items yet. Add dishes and drinks guests can order from their room."
          renderRow={(m) => [
            <div key="n">
              <span className="font-semibold text-slate-100">{m.name}</span>
              {m.description && <div className="text-xs text-slate-500 truncate max-w-xs">{m.description}</div>}
            </div>,
            <Chip key="c" tone="info">{m.category}</Chip>,
            money(m.price),
            m.gst_percent != null ? `${m.gst_percent}%` : "—",
            m.is_available ? <Chip key="a" tone="ok">Available</Chip> : <Chip key="a" tone="neutral">Hidden</Chip>,
            <div key="x" className="flex gap-2">
              <button onClick={() => setEditing({ ...m })} className="text-sky-300 hover:underline text-xs">Edit</button>
              {m.is_available && <button onClick={() => remove(m)} className="text-red-300 hover:underline text-xs">Hide</button>}
            </div>,
          ]}
        />
      </Card>
      {editing && <MenuForm value={editing} onClose={() => setEditing(null)} showToast={showToast}
        onSaved={() => { setEditing(null); load(); }} />}
    </>
  );
}

function MenuForm({ value, onClose, onSaved, showToast }) {
  const [form, setForm] = useState(value);
  const [saving, setSaving] = useState(false);
  const isEdit = !!value.id;
  const save = async () => {
    if (!form.name || form.name.trim().length < 2) { showToast("A name is required"); return; }
    if (form.price === "" || Number(form.price) < 0) { showToast("Enter a price"); return; }
    setSaving(true);
    try {
      const payload = {
        name: form.name.trim(), category: form.category, price: Number(form.price),
        sort_order: Number(form.sort_order) || 0, is_available: form.is_available !== false,
      };
      if (form.description) payload.description = form.description;
      if (form.gst_percent !== "" && form.gst_percent != null) payload.gst_percent = Number(form.gst_percent);
      if (isEdit) await api.updateMenuItem(value.id, payload);
      else await api.createMenuItem(payload);
      showToast(isEdit ? "Menu item updated" : "Menu item added");
      onSaved();
    } catch (e) { showToast(e.message || "Save failed"); }
    finally { setSaving(false); }
  };
  return (
    <Modal title={isEdit ? `Edit ${value.name}` : "New menu item"} onClose={onClose} wide>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <Field label="Name *" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
        <SelectField label="Category" value={form.category}
          onChange={(e) => setForm({ ...form, category: e.target.value })} options={MENU_CATEGORIES} />
        <Field label="Price ₹ *" type="number" value={form.price}
          onChange={(e) => setForm({ ...form, price: e.target.value })} />
        <Field label="GST %" type="number" value={form.gst_percent ?? ""}
          onChange={(e) => setForm({ ...form, gst_percent: e.target.value })} />
        <Field label="Sort order" type="number" value={form.sort_order}
          onChange={(e) => setForm({ ...form, sort_order: e.target.value })} />
        <label className="flex items-center gap-2 text-slate-300 mt-8">
          <input type="checkbox" checked={form.is_available !== false}
            onChange={(e) => setForm({ ...form, is_available: e.target.checked })} /> Available to order
        </label>
      </div>
      <Field label="Description"><textarea rows={2} className={inputCls} value={form.description ?? ""}
        onChange={(e) => setForm({ ...form, description: e.target.value })} /></Field>
      <div className="flex gap-3 mt-6">
        <PrimaryButton onClick={save} disabled={saving}><FaSave size={14} /> {saving ? "Saving…" : "Save"}</PrimaryButton>
        <GhostButton onClick={onClose}>Cancel</GhostButton>
      </div>
    </Modal>
  );
}

/* ------------------------------------------------------------------ settings */

const TOGGLES = [
  ["enabled", "Guest portal enabled (master)"],
  ["room_service_enabled", "Room service"],
  ["wifi_enabled", "WiFi voucher"],
  ["wakeup_enabled", "Wake-up call"],
  ["cab_enabled", "Cab / tour desk"],
  ["contactless_checkout_enabled", "Contactless checkout"],
];

function SettingsTab({ showToast }) {
  const [cfg, setCfg] = useState(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    api.getPortalConfig().then(setCfg).catch((e) => setError(e.message || "Failed to load settings"));
  }, []);

  const save = async () => {
    setSaving(true);
    try {
      await api.updatePortalConfig({
        guest_portal_enabled: cfg.enabled,
        portal_room_service_enabled: cfg.room_service_enabled,
        portal_wifi_enabled: cfg.wifi_enabled,
        portal_wakeup_enabled: cfg.wakeup_enabled,
        portal_cab_enabled: cfg.cab_enabled,
        portal_contactless_checkout_enabled: cfg.contactless_checkout_enabled,
        wifi_ssid: cfg.wifi_ssid,
        wifi_voucher_mode: cfg.wifi_voucher_mode,
      });
      showToast("Settings saved");
    } catch (e) { showToast(e.message || "Save failed"); }
    finally { setSaving(false); }
  };

  if (error) return <Card><div className="p-8 text-center text-red-300">{error}</div></Card>;
  if (!cfg) return <Card><div className="p-12"><Spinner /></div></Card>;

  return (
    <Card title="Guest portal settings">
      <div className="p-6 space-y-4 max-w-2xl">
        {TOGGLES.map(([k, label]) => (
          <label key={k} className="flex items-center gap-3 text-slate-200">
            <input type="checkbox" checked={!!cfg[k]} onChange={(e) => setCfg({ ...cfg, [k]: e.target.checked })} />
            {label}
          </label>
        ))}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 pt-2">
          <Field label="WiFi SSID" value={cfg.wifi_ssid || ""}
            onChange={(e) => setCfg({ ...cfg, wifi_ssid: e.target.value })} />
          <SelectField label="WiFi voucher mode" value={cfg.wifi_voucher_mode}
            onChange={(e) => setCfg({ ...cfg, wifi_voucher_mode: e.target.value })}
            options={[{ value: "auto", label: "Auto — show a per-stay code" },
              { value: "manual", label: "Manual — desk issues a code" }]} />
        </div>
        <PrimaryButton onClick={save} disabled={saving}><FaSave size={14} /> {saving ? "Saving…" : "Save settings"}</PrimaryButton>
      </div>
    </Card>
  );
}
