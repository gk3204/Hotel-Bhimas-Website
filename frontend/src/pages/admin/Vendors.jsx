// Vendors & AMC contracts with renewal reminders (prompt 18, slice 13).
import React, { useCallback, useEffect, useState } from "react";
import { FaBell, FaPlus, FaSave, FaSearch, FaSync } from "react-icons/fa";
import * as api from "../../api/backoffice";
import { useConfirm } from "../../components/ConfirmDialog";
import {
  Card, Chip, DataTable, Field, GhostButton, Modal, PageShell, PrimaryButton, SelectField,
  Stat, Tabs, fmtDate, inputCls, money, today, useToast,
} from "../../components/admin/BackofficeUI";

const TABS = [
  ["vendors", "Vendors"],
  ["contracts", "Contracts & AMC"],
  ["renewals", "Renewals due"],
];

const CATEGORIES = [
  "lock_amc", "laundry", "linen", "electrical", "plumbing", "it", "fnb", "security", "other",
];
const CATEGORY_LABEL = {
  lock_amc: "Lock AMC", laundry: "Laundry", linen: "Linen", electrical: "Electrical",
  plumbing: "Plumbing", it: "IT", fnb: "F&B", security: "Security", other: "Other",
};

/** Days-to-renewal as a chip — the whole point of the screen at a glance. */
function RenewalChip({ contract }) {
  if (contract.status === "cancelled") return <Chip tone="neutral">Cancelled</Chip>;
  if (contract.status === "expired" || contract.expired) return <Chip tone="danger">Expired</Chip>;
  if (contract.days_to_renewal === null) return <Chip tone="neutral">Open-ended</Chip>;
  if (contract.due_for_renewal) return <Chip tone="warn">{contract.days_to_renewal} day(s) left</Chip>;
  return <Chip tone="ok">{contract.days_to_renewal} day(s) left</Chip>;
}

export default function Vendors() {
  const [tab, setTab] = useState("vendors");
  const [toast, showToast] = useToast();

  const [vendors, setVendors] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [search, setSearch] = useState("");
  const [category, setCategory] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const res = await api.listVendors({ ...(search ? { q: search } : {}), ...(category ? { category } : {}) });
      setVendors(res.data || []);
    } catch (e) {
      setError(e.message || "Failed to load vendors");
    } finally {
      setLoading(false);
    }
  }, [search, category]);

  useEffect(() => { load(); /* eslint-disable-next-line */ }, []);

  return (
    <PageShell
      icon="🔧"
      title="Vendors & AMC"
      subtitle="Suppliers, service contracts and lock-AMC renewals — so nothing lapses without you hearing about it."
      toast={toast}
    >
      <Tabs tabs={TABS} active={tab} onChange={setTab} />
      {tab === "vendors" && (
        <VendorsTab
          vendors={vendors} loading={loading} error={error} reload={load}
          search={search} setSearch={setSearch} category={category} setCategory={setCategory}
          showToast={showToast}
        />
      )}
      {tab === "contracts" && <ContractsTab vendors={vendors} reload={load} showToast={showToast} />}
      {tab === "renewals" && <RenewalsTab showToast={showToast} reloadVendors={load} />}
    </PageShell>
  );
}

/* ------------------------------------------------------------------ vendors */

const EMPTY_VENDOR = {
  name: "", category: "other", contact_person: "", phone: "", email: "",
  gstin: "", address: "", notes: "",
};

const VENDOR_FIELDS = [
  ["name", "Vendor name *", "text"],
  ["contact_person", "Contact person", "text"],
  ["phone", "Phone", "text"],
  ["email", "Email", "email"],
  ["gstin", "GSTIN", "text"],
  ["address", "Address", "text"],
];

function VendorsTab({ vendors, loading, error, reload, search, setSearch, category, setCategory, showToast }) {
  const [editing, setEditing] = useState(null);
  const { confirm } = useConfirm();

  const remove = async (v) => {
    if (!(await confirm({
      title: `Deactivate ${v.name}?`,
      message: "Past expenses stay attributed to it.",
      confirmText: "Deactivate", tone: "danger",
    }))) return;
    try {
      await api.deactivateVendor(v.id);
      showToast(`${v.name} deactivated`);
      reload();
    } catch (e) {
      showToast(e.message || "Could not deactivate");
    }
  };

  return (
    <>
      <Card className="mb-6">
        <div className="p-5 grid grid-cols-1 md:grid-cols-4 gap-4 items-end">
          <Field label="Search">
            <input className={inputCls} value={search} placeholder="Name, contact, phone or GSTIN"
              onChange={(e) => setSearch(e.target.value)} onKeyDown={(e) => e.key === "Enter" && reload()} />
          </Field>
          <SelectField label="Category" value={category} onChange={(e) => setCategory(e.target.value)}
            options={[{ value: "", label: "All categories" },
              ...CATEGORIES.map((c) => ({ value: c, label: CATEGORY_LABEL[c] }))]} />
          <PrimaryButton onClick={reload}><FaSearch size={14} /> Search</PrimaryButton>
          <PrimaryButton onClick={() => setEditing({ ...EMPTY_VENDOR })}>
            <FaPlus size={14} /> New vendor
          </PrimaryButton>
        </div>
      </Card>

      <Card title="Vendors" right={<span className="text-slate-400 text-sm">{vendors.length} vendor(s)</span>}>
        <DataTable
          columns={[
            { label: "Vendor", sort: (v) => v.name },
            { label: "Category", sort: (v) => CATEGORY_LABEL[v.category] || v.category },
            "Contact",
            "Contracts",
            { label: "Renewals due", sort: (v) => v.renewals_due },
            { label: "Total spend", sort: (v) => v.total_spend },
            { label: "Status", sort: (v) => (v.is_active ? 1 : 0) },
            "",
          ]}
          rows={vendors} loading={loading} error={error} onRetry={reload}
          empty="No vendors yet. Add your laundry, lock-AMC and linen suppliers here."
          renderRow={(v) => [
            <span key="n" className="font-semibold text-slate-100">{v.name}</span>,
            <Chip key="c" tone="info">{CATEGORY_LABEL[v.category] || v.category}</Chip>,
            v.contact_person ? `${v.contact_person}${v.phone ? ` · ${v.phone}` : ""}` : v.phone,
            `${v.active_contracts} active / ${v.contract_count}`,
            v.renewals_due > 0 ? <Chip key="r" tone="warn">{v.renewals_due} due</Chip> : "—",
            money(v.total_spend),
            v.is_active ? <Chip key="s" tone="ok">Active</Chip> : <Chip key="s" tone="neutral">Inactive</Chip>,
            <div key="a" className="flex gap-2">
              <button onClick={() => setEditing({ ...v })} className="text-sky-300 hover:underline text-xs">
                Edit
              </button>
              {v.is_active && (
                <button onClick={() => remove(v)} className="text-red-300 hover:underline text-xs">
                  Deactivate
                </button>
              )}
            </div>,
          ]}
        />
        <p className="px-5 py-4 text-slate-500 text-xs border-t border-slate-700">
          ⓘ "Total spend" comes from the petty-cash / expense ledger — tag an expense with a vendor and
          it lands here automatically.
        </p>
      </Card>

      {editing && (
        <VendorForm value={editing} onClose={() => setEditing(null)} showToast={showToast}
          onSaved={() => { setEditing(null); reload(); }} />
      )}
    </>
  );
}

function VendorForm({ value, onClose, onSaved, showToast }) {
  const [form, setForm] = useState(value);
  const [saving, setSaving] = useState(false);
  const isEdit = !!value.id;

  const save = async () => {
    if (!form.name || form.name.trim().length < 2) { showToast("A vendor name is required"); return; }
    setSaving(true);
    try {
      const payload = { category: form.category };
      VENDOR_FIELDS.forEach(([k]) => {
        if (form[k]) payload[k] = form[k];
      });
      if (form.notes) payload.notes = form.notes;
      if (isEdit) payload.is_active = form.is_active !== false;
      if (isEdit) await api.updateVendor(value.id, payload);
      else await api.createVendor(payload);
      showToast(isEdit ? "Vendor updated" : "Vendor created");
      onSaved();
    } catch (e) {
      showToast(e.message || "Save failed");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal title={isEdit ? `Edit ${value.name}` : "New vendor"} onClose={onClose} wide>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {VENDOR_FIELDS.map(([k, label, type]) => (
          <Field key={k} label={label} type={type} value={form[k] ?? ""}
            onChange={(e) => setForm({ ...form, [k]: e.target.value })} />
        ))}
        <SelectField label="Category" value={form.category}
          onChange={(e) => setForm({ ...form, category: e.target.value })}
          options={CATEGORIES.map((c) => ({ value: c, label: CATEGORY_LABEL[c] }))} />
      </div>
      <Field label="Notes">
        <textarea rows={3} className={inputCls} value={form.notes ?? ""}
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

/* ------------------------------------------------------------------ contracts */

const EMPTY_CONTRACT = {
  title: "", contract_type: "amc", start_date: "", end_date: "",
  renewal_reminder_days: 30, amount: "", billing_cycle: "annual", notes: "",
};

function ContractsTab({ vendors, reload, showToast }) {
  const [vendorId, setVendorId] = useState("");
  const [contracts, setContracts] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [editing, setEditing] = useState(null);

  const load = useCallback(async () => {
    if (!vendorId) { setContracts([]); return; }
    setLoading(true);
    setError("");
    try {
      const res = await api.listContracts(vendorId);
      setContracts(res.data || []);
    } catch (e) {
      setError(e.message || "Failed to load contracts");
    } finally {
      setLoading(false);
    }
  }, [vendorId]);

  useEffect(() => { load(); /* eslint-disable-next-line */ }, [vendorId]);

  const doRenew = async (c) => {
    const next = window.prompt(
      `Renew "${c.title}" — new end date (YYYY-MM-DD):`,
      c.end_date || today()
    );
    if (!next) return;
    try {
      await api.renewContract(c.id, next);
      showToast(`${c.title} renewed to ${fmtDate(next)} — the reminder is re-armed`);
      load();
      reload();
    } catch (e) {
      showToast(e.message || "Could not renew");
    }
  };

  const doCancel = async (c) => {
    const reason = window.prompt(`Cancel "${c.title}"? Give a reason:`);
    if (!reason || reason.trim().length < 3) return;
    try {
      await api.cancelContract(c.id, reason.trim());
      showToast("Contract cancelled");
      load();
      reload();
    } catch (e) {
      showToast(e.message || "Could not cancel");
    }
  };

  return (
    <>
      <Card className="mb-6">
        <div className="p-5 grid grid-cols-1 md:grid-cols-3 gap-4 items-end">
          <SelectField label="Vendor" value={vendorId} onChange={(e) => setVendorId(e.target.value)}
            options={[{ value: "", label: "— select a vendor —" },
              ...vendors.map((v) => ({ value: v.id, label: v.name }))]} />
          <PrimaryButton onClick={load} disabled={!vendorId}><FaSync size={14} /> Refresh</PrimaryButton>
          <PrimaryButton onClick={() => setEditing({ ...EMPTY_CONTRACT })} disabled={!vendorId}>
            <FaPlus size={14} /> New contract
          </PrimaryButton>
        </div>
      </Card>

      {!vendorId ? (
        <Card><div className="p-12 text-center text-slate-400">Pick a vendor to see its contracts.</div></Card>
      ) : (
        <Card title="Contracts" right={<span className="text-slate-400 text-sm">{contracts.length}</span>}>
          <DataTable
            columns={["Title", "Type", "Start", "End", "Renewal", "Amount", "Cycle", "Reminder", "Status", ""]}
            rows={contracts} loading={loading} error={error}
            empty="No contracts for this vendor yet."
            renderRow={(c) => [
              <span key="t" className="font-semibold text-slate-100">{c.title}</span>,
              c.contract_type,
              fmtDate(c.start_date),
              fmtDate(c.end_date),
              <RenewalChip key="r" contract={c} />,
              c.amount != null ? money(c.amount) : "—",
              c.billing_cycle,
              c.last_reminder_sent_at
                ? <Chip key="rm" tone="info">sent {fmtDate(c.last_reminder_sent_at)}</Chip>
                : `${c.renewal_reminder_days}d window`,
              <Chip key="s" tone={c.status === "active" ? "ok" : c.status === "expired" ? "danger" : "neutral"}>
                {c.status}
              </Chip>,
              <div key="a" className="flex gap-2">
                <button onClick={() => setEditing({ ...c })} className="text-sky-300 hover:underline text-xs">
                  Edit
                </button>
                <button onClick={() => doRenew(c)} className="text-emerald-300 hover:underline text-xs">
                  Renew
                </button>
                {c.status !== "cancelled" && (
                  <button onClick={() => doCancel(c)} className="text-red-300 hover:underline text-xs">
                    Cancel
                  </button>
                )}
              </div>,
            ]}
          />
        </Card>
      )}

      {editing && (
        <ContractForm value={editing} vendorId={vendorId} onClose={() => setEditing(null)}
          showToast={showToast} onSaved={() => { setEditing(null); load(); reload(); }} />
      )}
    </>
  );
}

function ContractForm({ value, vendorId, onClose, onSaved, showToast }) {
  const [form, setForm] = useState(value);
  const [saving, setSaving] = useState(false);
  const isEdit = !!value.id;

  const save = async () => {
    if (!form.title || form.title.trim().length < 2) { showToast("A contract title is required"); return; }
    if (form.start_date && form.end_date && form.end_date < form.start_date) {
      showToast("The end date must be on or after the start date");
      return;
    }
    setSaving(true);
    try {
      const payload = {
        title: form.title,
        contract_type: form.contract_type,
        renewal_reminder_days: Number(form.renewal_reminder_days) || 30,
      };
      if (form.start_date) payload.start_date = form.start_date;
      if (form.end_date) payload.end_date = form.end_date;
      if (form.amount !== "" && form.amount != null) payload.amount = Number(form.amount);
      if (form.billing_cycle) payload.billing_cycle = form.billing_cycle;
      if (form.notes) payload.notes = form.notes;
      if (isEdit) await api.updateContract(value.id, payload);
      else await api.createContract(vendorId, payload);
      showToast(isEdit ? "Contract updated" : "Contract created");
      onSaved();
    } catch (e) {
      showToast(e.message || "Save failed");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal title={isEdit ? `Edit "${value.title}"` : "New contract"} onClose={onClose} wide>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <Field label="Title *" value={form.title}
          onChange={(e) => setForm({ ...form, title: e.target.value })} />
        <SelectField label="Type" value={form.contract_type}
          onChange={(e) => setForm({ ...form, contract_type: e.target.value })}
          options={[
            { value: "amc", label: "AMC (annual maintenance)" },
            { value: "rental", label: "Rental" },
            { value: "service", label: "Service" },
            { value: "supply", label: "Supply" },
          ]} />
        <Field label="Start date" type="date" value={form.start_date || ""}
          onChange={(e) => setForm({ ...form, start_date: e.target.value })} />
        <Field label="End date" type="date" value={form.end_date || ""}
          hint="Leave blank for an open-ended contract (never chased for renewal)."
          onChange={(e) => setForm({ ...form, end_date: e.target.value })} />
        <Field label="Remind me this many days before it ends" type="number"
          value={form.renewal_reminder_days}
          onChange={(e) => setForm({ ...form, renewal_reminder_days: e.target.value })} />
        <Field label="Amount (₹)" type="number" value={form.amount ?? ""}
          onChange={(e) => setForm({ ...form, amount: e.target.value })} />
        <SelectField label="Billing cycle" value={form.billing_cycle || "annual"}
          onChange={(e) => setForm({ ...form, billing_cycle: e.target.value })}
          options={["monthly", "quarterly", "half_yearly", "annual", "one_time"]} />
      </div>
      <Field label="Notes">
        <textarea rows={3} className={inputCls} value={form.notes ?? ""}
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

/* ------------------------------------------------------------------ renewals */

function RenewalsTab({ showToast, reloadVendors }) {
  const [withinDays, setWithinDays] = useState(60);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      setData(await api.getRenewals(withinDays));
    } catch (e) {
      setError(e.message || "Failed to load renewals");
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [withinDays]);

  useEffect(() => { load(); /* eslint-disable-next-line */ }, []);

  const runReminders = async () => {
    setBusy(true);
    try {
      const res = await api.runRenewalReminders();
      showToast(
        `Sweep done — ${res.reminders_sent} reminder(s) sent, ${res.expired} contract(s) marked expired`
      );
      load();
      reloadVendors();
    } catch (e) {
      showToast(e.message || "Sweep failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <Card className="mb-6">
        <div className="p-5 grid grid-cols-1 md:grid-cols-4 gap-4 items-end">
          <Field label="Ending within (days)" type="number" value={withinDays}
            onChange={(e) => setWithinDays(Number(e.target.value) || 60)} />
          <PrimaryButton onClick={load}><FaSearch size={14} /> Apply</PrimaryButton>
          <GhostButton onClick={runReminders} disabled={busy}>
            <FaBell size={14} /> {busy ? "Running…" : "Send reminders now"}
          </GhostButton>
        </div>
        <p className="px-5 pb-5 text-slate-500 text-xs">
          ⓘ Reminders also go out automatically on the scheduled sweep. Each contract is chased once per
          renewal window — renewing it re-arms the reminder for the next one.
        </p>
      </Card>

      {data && (
        <div className="grid grid-cols-2 md:grid-cols-3 gap-4 mb-6">
          <Stat label="Needing attention" value={data.total} />
          <Stat label="Already expired" value={data.expired} tone="text-red-300" />
          <Stat label="Due soon" value={data.due_soon} tone="text-amber-300" />
        </div>
      )}

      <Card title="Renewals">
        <DataTable
          columns={["Vendor", "Contract", "Type", "Ends", "Renewal", "Amount", "Reminder sent"]}
          rows={data?.data} loading={loading} error={error}
          empty="Nothing due in this window. Every contract is current."
          renderRow={(c) => [
            <span key="v" className="font-semibold text-slate-100">{c.vendor_name}</span>,
            c.title,
            c.contract_type,
            fmtDate(c.end_date),
            <RenewalChip key="r" contract={c} />,
            c.amount != null ? money(c.amount) : "—",
            c.last_reminder_sent_at ? fmtDate(c.last_reminder_sent_at) : "—",
          ]}
        />
      </Card>
    </>
  );
}
