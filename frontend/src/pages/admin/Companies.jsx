// Corporate accounts — bill-to-company, city ledger, consolidated GST invoices (prompt 18, slice 7).
import React, { useCallback, useEffect, useMemo, useState } from "react";
import { FaFileCsv, FaFilePdf, FaPlus, FaSave, FaSearch } from "react-icons/fa";
import * as api from "../../api/backoffice";
import { useConfirm } from "../../components/ConfirmDialog";
import {
  Card, Chip, DataTable, Field, GhostButton, Modal, PageShell, PrimaryButton, SelectField,
  Spinner, Stat, Tabs, daysAgo, fmtDate, inputCls, money, today, useToast,
} from "../../components/admin/BackofficeUI";

const TABS = [
  ["companies", "Companies"],
  ["ledger", "Ledger & statement"],
  ["invoices", "Consolidated invoices"],
  ["settings", "Settings"],
];

/** Credit position as a chip — the thing the desk and the owner actually look at. */
function CreditChip({ company }) {
  if (!company) return null;
  const limit = Number(company.credit_limit || 0);
  const owed = Number(company.outstanding || 0);
  if (company.over_limit) return <Chip tone="danger">Over limit by {money(owed - limit)}</Chip>;
  if (limit > 0 && owed / limit >= 0.8) return <Chip tone="warn">{money(company.credit_available)} left</Chip>;
  return <Chip tone="ok">{money(company.credit_available)} available</Chip>;
}

export default function Companies() {
  const [tab, setTab] = useState("companies");
  const [toast, showToast] = useToast();

  const [companies, setCompanies] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [search, setSearch] = useState("");
  const [selectedId, setSelectedId] = useState(null);

  const loadCompanies = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const res = await api.listCompanies(search ? { q: search } : {});
      setCompanies(res.data || []);
      setSelectedId((cur) => cur ?? res.data?.[0]?.company_id ?? null);
    } catch (e) {
      setError(e.message || "Failed to load companies");
    } finally {
      setLoading(false);
    }
  }, [search]);

  useEffect(() => { loadCompanies(); /* eslint-disable-next-line */ }, []);

  const selected = useMemo(
    () => companies.find((c) => c.company_id === selectedId) || null,
    [companies, selectedId]
  );

  return (
    <PageShell
      icon="🏢"
      title="Corporate Accounts"
      subtitle="Bill a stay to the guest's employer, track what each company owes, and send one consolidated GST invoice a month."
      toast={toast}
    >
      <Tabs tabs={TABS} active={tab} onChange={setTab} />

      {tab === "companies" && (
        <CompaniesTab
          companies={companies} loading={loading} error={error}
          search={search} setSearch={setSearch} reload={loadCompanies}
          showToast={showToast} onOpenLedger={(id) => { setSelectedId(id); setTab("ledger"); }}
        />
      )}
      {tab === "ledger" && (
        <LedgerTab
          companies={companies} selected={selected} setSelectedId={setSelectedId}
          reload={loadCompanies} showToast={showToast}
        />
      )}
      {tab === "invoices" && (
        <InvoicesTab
          companies={companies} selected={selected} setSelectedId={setSelectedId}
          reload={loadCompanies} showToast={showToast}
        />
      )}
      {tab === "settings" && <SettingsTab showToast={showToast} />}
    </PageShell>
  );
}

/* ------------------------------------------------------------------ companies */

const EMPTY_COMPANY = {
  name: "", gstin: "", address: "", city: "", state: "", state_code: "",
  contact_person: "", phone: "", email: "", credit_limit: 0, credit_days: 30,
  payment_terms: "", notes: "",
};

function CompaniesTab({ companies, loading, error, search, setSearch, reload, showToast, onOpenLedger }) {
  const [editing, setEditing] = useState(null);
  const { confirm } = useConfirm();

  const remove = async (c) => {
    if (!(await confirm({
      title: `Deactivate ${c.name}?`,
      message: "Its ledger and invoices stay readable.",
      confirmText: "Deactivate", tone: "danger",
    }))) return;
    try {
      await api.deactivateCompany(c.company_id);
      showToast(`${c.name} deactivated`);
      reload();
    } catch (e) {
      showToast(e.message || "Could not deactivate");
    }
  };

  return (
    <>
      <Card className="mb-6">
        <div className="p-5 grid grid-cols-1 md:grid-cols-4 gap-4 items-end">
          <div className="md:col-span-2">
            <Field label="Search">
              <input
                className={inputCls} value={search} placeholder="Name, GSTIN, contact or phone"
                onChange={(e) => setSearch(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && reload()}
              />
            </Field>
          </div>
          <PrimaryButton onClick={reload}><FaSearch size={14} /> Search</PrimaryButton>
          <PrimaryButton onClick={() => setEditing({ ...EMPTY_COMPANY })}>
            <FaPlus size={14} /> New company
          </PrimaryButton>
        </div>
      </Card>

      <Card title="Companies" right={<span className="text-slate-400 text-sm">{companies.length} account(s)</span>}>
        <DataTable
          columns={["Company", "GSTIN", "Contact", "Credit limit", "Outstanding", "Position", "Status", ""]}
          rows={companies} loading={loading} error={error}
          empty="No corporate accounts yet. Add one to start billing employers directly."
          renderRow={(c) => [
            <button key="n" onClick={() => onOpenLedger(c.company_id)}
              className="text-[#E5C07B] hover:underline font-semibold">{c.name}</button>,
            c.gstin,
            c.contact_person ? `${c.contact_person}${c.phone ? ` · ${c.phone}` : ""}` : c.phone,
            money(c.credit_limit),
            money(c.outstanding),
            <CreditChip key="cc" company={c} />,
            c.is_active ? <Chip key="s" tone="ok">Active</Chip> : <Chip key="s" tone="neutral">Inactive</Chip>,
            <div key="a" className="flex gap-2">
              <button onClick={() => setEditing({ ...c, id: c.company_id })}
                className="text-sky-300 hover:underline text-xs">Edit</button>
              {c.is_active && (
                <button onClick={() => remove(c)} className="text-red-300 hover:underline text-xs">
                  Deactivate
                </button>
              )}
            </div>,
          ]}
        />
      </Card>

      {editing && (
        <CompanyForm
          value={editing} onClose={() => setEditing(null)}
          onSaved={() => { setEditing(null); reload(); }} showToast={showToast}
        />
      )}
    </>
  );
}

const COMPANY_FIELDS = [
  ["name", "Company name *", "text"],
  ["gstin", "GSTIN", "text"],
  ["contact_person", "Contact person", "text"],
  ["phone", "Phone", "text"],
  ["email", "Billing email", "email"],
  ["address", "Address", "text"],
  ["city", "City", "text"],
  ["state", "State", "text"],
  ["state_code", "GST state code", "text"],
  ["credit_limit", "Credit limit (₹)", "number"],
  ["credit_days", "Credit days", "number"],
  ["payment_terms", "Payment terms", "text"],
];

function CompanyForm({ value, onClose, onSaved, showToast }) {
  const [form, setForm] = useState(value);
  const [saving, setSaving] = useState(false);
  const isEdit = !!value.id;

  const save = async () => {
    if (!form.name || form.name.trim().length < 2) {
      showToast("A company name is required");
      return;
    }
    setSaving(true);
    try {
      const payload = {};
      COMPANY_FIELDS.forEach(([k, , type]) => {
        const v = form[k];
        if (v === "" || v === null || v === undefined) return;
        payload[k] = type === "number" ? Number(v) : v;
      });
      if (form.notes) payload.notes = form.notes;
      if (isEdit) payload.is_active = form.is_active !== false;
      if (isEdit) await api.updateCompany(value.id, payload);
      else await api.createCompany(payload);
      showToast(isEdit ? "Company updated" : "Company created");
      onSaved();
    } catch (e) {
      showToast(e.message || "Save failed");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal title={isEdit ? `Edit ${value.name}` : "New corporate account"} onClose={onClose} wide>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {COMPANY_FIELDS.map(([k, label, type]) => (
          <Field key={k} label={label} type={type} value={form[k] ?? ""}
            onChange={(e) => setForm({ ...form, [k]: e.target.value })} />
        ))}
      </div>
      <Field label="Notes" hint="Anything the desk should know when billing this company.">
        <textarea rows={3} className={inputCls} value={form.notes ?? ""}
          onChange={(e) => setForm({ ...form, notes: e.target.value })} />
      </Field>
      <p className="text-slate-500 text-xs mt-3">
        ⓘ A credit limit of 0 means no credit: routing a folio to this company will be refused.
        An admin can still override with a reason, and the override is recorded in the audit log.
      </p>
      <div className="flex gap-3 mt-6">
        <PrimaryButton onClick={save} disabled={saving}>
          <FaSave size={14} /> {saving ? "Saving…" : "Save"}
        </PrimaryButton>
        <GhostButton onClick={onClose}>Cancel</GhostButton>
      </div>
    </Modal>
  );
}

/* ------------------------------------------------------------------ ledger */

function CompanyPicker({ companies, selected, setSelectedId }) {
  return (
    <SelectField
      label="Company"
      value={selected?.company_id ?? ""}
      onChange={(e) => setSelectedId(Number(e.target.value))}
      options={[
        { value: "", label: "— select a company —" },
        ...companies.map((c) => ({ value: c.company_id, label: c.name })),
      ]}
    />
  );
}

function LedgerTab({ companies, selected, setSelectedId, reload, showToast }) {
  const [range, setRange] = useState({ from: daysAgo(89), to: today() });
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [payOpen, setPayOpen] = useState(false);
  const [adjOpen, setAdjOpen] = useState(false);

  const load = useCallback(async () => {
    if (!selected) { setData(null); return; }
    setLoading(true);
    setError("");
    try {
      setData(await api.getCompanyLedger(selected.company_id, range));
    } catch (e) {
      setError(e.message || "Failed to load the statement");
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [selected, range]);

  useEffect(() => { load(); /* eslint-disable-next-line */ }, [selected?.company_id]);

  const doExport = async (format) => {
    try {
      await api.exportCompanyLedger(selected.company_id, range, format);
    } catch (e) {
      showToast(e.message || "Export failed");
    }
  };

  const ageing = data?.ageing;

  return (
    <>
      <Card className="mb-6">
        <div className="p-5 grid grid-cols-1 md:grid-cols-5 gap-4 items-end">
          <CompanyPicker companies={companies} selected={selected} setSelectedId={setSelectedId} />
          <Field label="From" type="date" value={range.from}
            onChange={(e) => setRange({ ...range, from: e.target.value })} />
          <Field label="To" type="date" value={range.to}
            onChange={(e) => setRange({ ...range, to: e.target.value })} />
          <PrimaryButton onClick={load} disabled={!selected}><FaSearch size={14} /> Apply</PrimaryButton>
          <div className="flex gap-2">
            <GhostButton onClick={() => doExport("csv")} disabled={!selected} className="flex-1">
              <FaFileCsv size={14} /> CSV
            </GhostButton>
            <GhostButton onClick={() => doExport("pdf")} disabled={!selected} className="flex-1">
              <FaFilePdf size={14} /> PDF
            </GhostButton>
          </div>
        </div>
        {selected && (
          <div className="px-5 pb-5 flex flex-wrap gap-3">
            <PrimaryButton onClick={() => setPayOpen(true)}>Record payment received</PrimaryButton>
            <GhostButton onClick={() => setAdjOpen(true)}>Add adjustment / credit note</GhostButton>
          </div>
        )}
      </Card>

      {!selected ? (
        <Card><div className="p-12 text-center text-slate-400">Pick a company to see its statement.</div></Card>
      ) : (
        <>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
            <Stat label="Outstanding" value={money(selected.outstanding)}
              tone={selected.over_limit ? "text-red-300" : "text-white"} />
            <Stat label="Credit limit" value={money(selected.credit_limit)} />
            <Stat label="Available credit" value={money(selected.credit_available)}
              tone={selected.credit_available < 0 ? "text-red-300" : "text-emerald-300"} />
            <Stat label="Terms" value={`${selected.credit_days} days`} />
          </div>

          {ageing && (
            <Card title="Ageing" className="mb-6">
              <div className="p-5 grid grid-cols-2 md:grid-cols-5 gap-4">
                <Stat label="0–30 days" value={money(ageing.current)} />
                <Stat label="31–60 days" value={money(ageing.d31_60)} />
                <Stat label="61–90 days" value={money(ageing.d61_90)} tone="text-amber-300" />
                <Stat label="90+ days" value={money(ageing.d90_plus)} tone="text-red-300" />
                <Stat label="Unapplied credit" value={money(ageing.unapplied_credit)} tone="text-sky-300" />
              </div>
              <p className="px-5 pb-4 text-slate-500 text-xs">
                ⓘ Payments are applied to the oldest charge first, the convention used on an Indian
                city-ledger statement.
              </p>
            </Card>
          )}

          <Card
            title={`Statement — ${selected.name}`}
            right={
              data && (
                <span className="text-slate-400 text-sm">
                  Charges {money(data.charges_total)} · Payments {money(data.payments_total)} ·
                  Closing {money(data.closing_outstanding)}
                </span>
              )
            }
          >
            <DataTable
              columns={["Date", "Type", "Description", "Reference", "Amount", "Balance after"]}
              rows={data?.rows} loading={loading} error={error}
              empty="No ledger movement in this period."
              renderRow={(r) => [
                fmtDate(r.when),
                <Chip key="t" tone={r.type === "payment" ? "ok" : r.type === "adjustment" ? "info" : "gold"}>
                  {r.type}
                </Chip>,
                r.description,
                r.reference,
                <span key="a" className={r.amount < 0 ? "text-emerald-300" : "text-slate-100"}>
                  {money(r.amount)}
                </span>,
                money(r.balance_after),
              ]}
            />
          </Card>
        </>
      )}

      {payOpen && (
        <PaymentForm company={selected} onClose={() => setPayOpen(false)} showToast={showToast}
          onSaved={() => { setPayOpen(false); reload(); load(); }} />
      )}
      {adjOpen && (
        <AdjustmentForm company={selected} onClose={() => setAdjOpen(false)} showToast={showToast}
          onSaved={() => { setAdjOpen(false); reload(); load(); }} />
      )}
    </>
  );
}

function PaymentForm({ company, onClose, onSaved, showToast }) {
  const [form, setForm] = useState({ amount: "", method: "bank_transfer", reference: "", description: "" });
  const [saving, setSaving] = useState(false);

  const save = async () => {
    const amount = Number(form.amount);
    if (!(amount > 0)) { showToast("Enter an amount greater than zero"); return; }
    setSaving(true);
    try {
      await api.recordCompanyPayment(company.company_id, {
        amount,
        method: form.method,
        reference: form.reference || null,
        description: form.description || null,
        client_ref: `web-pay-${company.company_id}-${Date.now()}`,
      });
      showToast(`${money(amount)} recorded against ${company.name}`);
      onSaved();
    } catch (e) {
      showToast(e.message || "Could not record the payment");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal title={`Payment received — ${company.name}`} onClose={onClose}>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <Field label="Amount (₹) *" type="number" value={form.amount}
          onChange={(e) => setForm({ ...form, amount: e.target.value })} />
        <SelectField label="Method" value={form.method}
          onChange={(e) => setForm({ ...form, method: e.target.value })}
          options={[
            { value: "bank_transfer", label: "Bank transfer / NEFT" },
            { value: "cheque", label: "Cheque" },
            { value: "upi", label: "UPI" },
            { value: "cash", label: "Cash" },
            { value: "card", label: "Card" },
          ]} />
        <Field label="Reference" hint="UTR / cheque number / receipt no" value={form.reference}
          onChange={(e) => setForm({ ...form, reference: e.target.value })} />
        <Field label="Note" value={form.description}
          onChange={(e) => setForm({ ...form, description: e.target.value })} />
      </div>
      <p className="text-slate-500 text-xs mt-3">
        ⓘ Currently outstanding: <b className="text-slate-300">{money(company.outstanding)}</b>.
        This adds a credit row to the append-only ledger — it never edits an earlier entry.
      </p>
      <div className="flex gap-3 mt-6">
        <PrimaryButton onClick={save} disabled={saving}>{saving ? "Recording…" : "Record payment"}</PrimaryButton>
        <GhostButton onClick={onClose}>Cancel</GhostButton>
      </div>
    </Modal>
  );
}

function AdjustmentForm({ company, onClose, onSaved, showToast }) {
  const [form, setForm] = useState({ amount: "", reason: "" });
  const [saving, setSaving] = useState(false);

  const save = async () => {
    const amount = Number(form.amount);
    if (!amount) { showToast("Enter a non-zero amount"); return; }
    if ((form.reason || "").trim().length < 3) { showToast("A reason is required"); return; }
    setSaving(true);
    try {
      await api.recordCompanyAdjustment(company.company_id, {
        amount, reason: form.reason,
        client_ref: `web-adj-${company.company_id}-${Date.now()}`,
      });
      showToast("Adjustment recorded");
      onSaved();
    } catch (e) {
      showToast(e.message || "Could not record the adjustment");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal title={`Adjustment — ${company.name}`} onClose={onClose}>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <Field label="Amount (₹) *" type="number" value={form.amount}
          hint="Positive = the company owes more. Negative = credit note."
          onChange={(e) => setForm({ ...form, amount: e.target.value })} />
        <Field label="Reason *" value={form.reason}
          onChange={(e) => setForm({ ...form, reason: e.target.value })} />
      </div>
      <div className="flex gap-3 mt-6">
        <PrimaryButton onClick={save} disabled={saving}>{saving ? "Saving…" : "Record adjustment"}</PrimaryButton>
        <GhostButton onClick={onClose}>Cancel</GhostButton>
      </div>
    </Modal>
  );
}

/* ------------------------------------------------------------------ consolidated invoices */

function InvoicesTab({ companies, selected, setSelectedId, showToast }) {
  const firstOfMonth = () => {
    const d = new Date();
    return new Date(d.getFullYear(), d.getMonth(), 1).toISOString().slice(0, 10);
  };
  const [period, setPeriod] = useState({ from: firstOfMonth(), to: today() });
  const [preview, setPreview] = useState(null);
  const [invoices, setInvoices] = useState([]);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const loadInvoices = useCallback(async () => {
    if (!selected) { setInvoices([]); return; }
    setLoading(true);
    setError("");
    try {
      const res = await api.listCompanyInvoices(selected.company_id);
      setInvoices(res.data || []);
    } catch (e) {
      setError(e.message || "Failed to load invoices");
    } finally {
      setLoading(false);
    }
  }, [selected]);

  useEffect(() => { loadInvoices(); setPreview(null); /* eslint-disable-next-line */ }, [selected?.company_id]);

  const doPreview = async () => {
    setBusy(true);
    try {
      setPreview(await api.previewCompanyInvoice(selected.company_id, { from: period.from, to: period.to }));
    } catch (e) {
      showToast(e.message || "Preview failed");
      setPreview(null);
    } finally { setBusy(false); }
  };

  const doCreate = async () => {
    setBusy(true);
    try {
      const inv = await api.createCompanyInvoice(selected.company_id, {
        period_from: period.from, period_to: period.to,
      });
      showToast(`${inv.invoice_no} raised — ${money(inv.grand_total)}`);
      setPreview(null);
      loadInvoices();
    } catch (e) {
      showToast(e.message || "Could not raise the invoice");
    } finally { setBusy(false); }
  };

  const doCancel = async (inv) => {
    const reason = window.prompt(`Cancel ${inv.invoice_no}? Give a reason (the number is kept — a GST series must have no holes):`);
    if (!reason || reason.trim().length < 3) return;
    try {
      await api.cancelCompanyInvoice(inv.id, reason.trim());
      showToast(`${inv.invoice_no} cancelled — its stays are billable again`);
      loadInvoices();
    } catch (e) {
      showToast(e.message || "Could not cancel");
    }
  };

  return (
    <>
      <Card className="mb-6">
        <div className="p-5 grid grid-cols-1 md:grid-cols-5 gap-4 items-end">
          <CompanyPicker companies={companies} selected={selected} setSelectedId={setSelectedId} />
          <Field label="Period from" type="date" value={period.from}
            onChange={(e) => setPeriod({ ...period, from: e.target.value })} />
          <Field label="Period to" type="date" value={period.to}
            onChange={(e) => setPeriod({ ...period, to: e.target.value })} />
          <GhostButton onClick={doPreview} disabled={!selected || busy}>
            <FaSearch size={14} /> Preview
          </GhostButton>
          <PrimaryButton onClick={doCreate} disabled={!selected || busy || !preview?.stay_count}>
            <FaPlus size={14} /> Raise invoice
          </PrimaryButton>
        </div>
        <p className="px-5 pb-5 text-slate-500 text-xs">
          ⓘ Only stays that were transferred to this company and not already billed appear. Preview first —
          raising the invoice locks those stays so they can never be billed twice.
        </p>
      </Card>

      {preview && (
        <Card title={`Preview — ${preview.stay_count} stay(s)`} className="mb-6">
          <div className="p-5 grid grid-cols-2 md:grid-cols-4 gap-4">
            <Stat label="Taxable" value={money(preview.taxable_total)} />
            <Stat label="CGST" value={money(preview.cgst_total)} />
            <Stat label="SGST" value={money(preview.sgst_total)} />
            <Stat label="Grand total" value={money(preview.grand_total)} tone="text-[#FCD34D]" />
          </div>
          <DataTable
            columns={["Date", "Description", "Amount"]}
            rows={preview.stays} empty="Nothing unbilled in this period."
            renderRow={(s) => [fmtDate(s.when), s.description, money(s.amount)]}
          />
        </Card>
      )}

      <Card title="Consolidated invoices"
        right={<span className="text-slate-400 text-sm">{invoices.length} issued</span>}>
        <DataTable
          columns={["Invoice no", "Date", "Period", "Stays", "Taxable", "CGST", "SGST", "Total", "Paid", "Status", ""]}
          rows={invoices} loading={loading} error={error}
          empty="No consolidated invoices raised for this company yet."
          renderRow={(i) => [
            <span key="n" className="font-semibold text-[#E5C07B]">{i.invoice_no}</span>,
            fmtDate(i.invoice_date),
            `${fmtDate(i.period_from)} → ${fmtDate(i.period_to)}`,
            i.stay_count,
            money(i.taxable_total), money(i.cgst_total), money(i.sgst_total),
            money(i.grand_total), money(i.paid_total),
            <Chip key="s" tone={i.status === "paid" ? "ok" : i.status === "cancelled" ? "danger" : "warn"}>
              {i.status}
            </Chip>,
            <div key="a" className="flex gap-2">
              <button onClick={() => api.downloadCompanyInvoice(i.id).catch((e) => showToast(e.message))}
                className="text-sky-300 hover:underline text-xs">PDF</button>
              <button
                onClick={() => api.emailCompanyInvoice(i.id)
                  .then(() => showToast(`Emailed to ${selected?.email || "the billing contact"}`))
                  .catch((e) => showToast(e.message))}
                className="text-emerald-300 hover:underline text-xs">Email</button>
              {i.status !== "cancelled" && (
                <button onClick={() => doCancel(i)} className="text-red-300 hover:underline text-xs">
                  Cancel
                </button>
              )}
            </div>,
          ]}
        />
      </Card>
    </>
  );
}

/* ------------------------------------------------------------------ settings */

const CONFIG_FIELDS = [
  ["company_default_credit_days", "Default credit days for a new company", "number"],
  ["company_invoice_prefix", "Consolidated invoice prefix", "text"],
  ["vendor_renewal_lead_days", "Vendor renewal reminder lead (days)", "number"],
  ["attendance_auto_close_hours", "Auto-close a forgotten clock-out after (hours)", "number"],
];

const CONFIG_TOGGLES = [
  ["company_credit_block", "Refuse a folio that would breach a company's credit limit (an admin can still override with a reason)"],
  ["vendor_renewal_alerts_enabled", "WhatsApp the owner when a vendor contract enters its renewal window"],
  ["attendance_pin_enabled", "Allow staff to clock in/out with their PIN at the desk"],
];

function SettingsTab({ showToast }) {
  const [cfg, setCfg] = useState(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    api.getBackofficeConfig().then(setCfg).catch((e) => showToast(e.message || "Failed to load config"));
    // eslint-disable-next-line
  }, []);

  const save = async () => {
    setSaving(true);
    try {
      const payload = {};
      CONFIG_FIELDS.forEach(([k, , type]) => {
        payload[k] = type === "number" ? Number(cfg[k]) : cfg[k];
      });
      CONFIG_TOGGLES.forEach(([k]) => { payload[k] = !!cfg[k]; });
      payload.roster_default_shift_type = cfg.roster_default_shift_type;
      const updated = await api.updateBackofficeConfig(payload);
      setCfg((c) => ({ ...c, ...updated }));
      showToast("Settings saved");
    } catch (e) {
      showToast(e.message || "Save failed");
    } finally {
      setSaving(false);
    }
  };

  if (!cfg) return <Card><Spinner className="p-12" /></Card>;

  return (
    <Card title="Back-office configuration">
      <div className="p-6">
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {CONFIG_FIELDS.map(([k, label, type]) => (
            <Field key={k} label={label} type={type} value={cfg[k] ?? ""}
              onChange={(e) => setCfg({ ...cfg, [k]: e.target.value })} />
          ))}
          <SelectField label="Default roster shift type" value={cfg.roster_default_shift_type || "general"}
            onChange={(e) => setCfg({ ...cfg, roster_default_shift_type: e.target.value })}
            options={["general", "morning", "evening", "night"]} />
        </div>
        <div className="mt-6 space-y-3">
          {CONFIG_TOGGLES.map(([k, label]) => (
            <label key={k} className="flex items-start gap-3 text-slate-200">
              <input type="checkbox" checked={!!cfg[k]} className="h-5 w-5 accent-[#E5C07B] mt-0.5"
                onChange={(e) => setCfg({ ...cfg, [k]: e.target.checked })} />
              <span className="text-sm">{label}</span>
            </label>
          ))}
        </div>
        <PrimaryButton onClick={save} disabled={saving} className="mt-6">
          <FaSave size={14} /> {saving ? "Saving…" : "Save settings"}
        </PrimaryButton>
      </div>
    </Card>
  );
}
