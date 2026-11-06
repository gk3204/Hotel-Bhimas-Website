// P2a (owner, 6 Nov): Yatra too - its YES Bank payment advices, its per-booking commission invoices, a
// channel switch on every tab, expected vs received per payment, and the per-booking money chain.
// P1 batch 2 (owner, 5 Nov): which Goibibo / MakeMyTrip booking Go-MMT has paid, in which transfer, and
// whether that money reached the bank. Three states per booking - Payment pending / Payment done /
// Payment received. "Confirm received" is per payment (one UTR, all its bookings) and admin only.
// Server: routers/ota.py /ota/payments*, services/ota_payments.py. Design: docs/ota-payment-tracking-plan.md
import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  PageShell, Card, Spinner, Chip, Stat, Tabs, Modal, Field, PrimaryButton, GhostButton, DangerButton,
  InfoTip, inputCls, money, today, daysAgo, useToast,
} from "../../components/admin/BackofficeUI";
import {
  getPaymentSummary, getPayments, getPayment, getPaymentBookings, getPaymentExceptions,
  getPaymentCandidates, receivePayment, undoPaymentReceipt, addPayment, linkPaymentLine,
  ignorePaymentLine, importPayments, getBookingPaymentState,
  getStatementMonths, getStatementMonth, downloadStatementFile, uploadOtaMail,
  getBookingChain, getPaymentSettings, putPaymentSettings,
} from "../../api/ota";
import { exportCsv } from "../../utils/exportCsv";

const BRAND = { makemytrip: "MakeMyTrip", goibibo: "Goibibo", yatra: "Yatra" };
const CHANNELS = [["all", "All"], ["makemytrip", "MakeMyTrip"], ["goibibo", "Goibibo"], ["yatra", "Yatra"]];
const chanParam = (c) => (c && c !== "all" ? c : undefined);
const FLAG_LABEL = {
  short_paid: "Short paid", over_paid: "Over paid", paid_but_cancelled: "Paid but cancelled",
  no_voucher_amount: "No voucher amount", paid_in_parts: "Paid in parts",
  totals_mismatch: "Totals don't add up", bank_account_mismatch: "Wrong bank account",
  several_bookings: "Several bookings in one advice",
};
const LINE_LABEL = {
  matched: ["Matched", "ok"], amount_diff: ["Amount differs", "warn"], unknown_booking: ["Booking not found", "danger"],
  cancelled_booking: ["Booking cancelled", "danger"], other_property: ["Other property", "neutral"],
  before_tracking: ["Before PMS", "neutral"], ignored: ["Ignored", "neutral"],
};

export function PaymentStateChip({ st }) {
  if (!st) return null;
  if (st.state === "pending" && st.overdue) return <Chip tone="danger">Overdue</Chip>;
  const map = {
    pending: ["Payment pending", "warn"], done: ["Payment done", "info"],
    received: ["Payment received", "ok"], not_applicable: ["Not applicable", "neutral"],
  };
  const [label, tone] = map[st.state] || [st.state, "neutral"];
  return <Chip tone={tone}>{label}</Chip>;
}

/** The booking-detail badge: "MMT payment: Payment received ₹9,050 · 12 Oct · HDFCH…". Admin only on
 *  the server, so for any other login (or a non-Go-MMT booking) it simply renders nothing. */
export function OtaPaymentBadge({ bookingId, source }) {
  const [got, setGot] = useState(null);      // {id, state} - kept per booking so a stale one never shows
  const [chain, setChain] = useState(false);
  useEffect(() => {
    let live = true;
    if (bookingId && BRAND[source]) {
      getBookingPaymentState(bookingId).then((r) => live && setGot({ id: bookingId, state: r.state })).catch(() => {});
    }
    return () => { live = false; };
  }, [bookingId, source]);
  const st = got && got.id === bookingId ? got.state : null;
  if (!st) return null;
  return (
    <div className="mt-1 text-xs text-slate-300 flex flex-wrap items-center gap-1">
      <span className="text-slate-400">{BRAND[source]} payment:</span> <PaymentStateChip st={st} />
      {st.paid > 0 && <span>{money(st.paid)}</span>}
      {st.payment_date && <span>· paid {st.payment_date}</span>}
      {st.utrs?.length > 0 && <span className="font-mono">· {st.utrs.join(", ")}</span>}
      {st.received_on && <span>· in bank {st.received_on}</span>}
      <button className="underline text-[#E5C07B] ml-1" onClick={() => setChain(true)}>Money chain</button>
      <Flags flags={st.flags} />
      {chain && <ChainModal bookingId={bookingId} onClose={() => setChain(false)} />}
    </div>
  );
}

const Flags = ({ flags }) =>
  (flags || []).length ? (
    <div className="flex flex-wrap gap-1 mt-1">
      {flags.map((f) => (
        <Chip key={f} tone={f === "bank_account_mismatch" || f === "paid_but_cancelled" ? "danger" : "warn"}>
          {FLAG_LABEL[f] || f}
        </Chip>
      ))}
    </div>
  ) : null;

const Th = ({ children, right }) => (
  <th className={`px-3 py-3 font-semibold text-slate-300 text-xs uppercase ${right ? "text-right" : "text-left"}`}>
    {children}
  </th>
);
const Td = ({ children, right, className = "" }) => (
  <td className={`px-3 py-2 align-top ${right ? "text-right whitespace-nowrap" : ""} ${className}`}>{children}</td>
);
const Table = ({ head, children }) => (
  <div className="overflow-x-auto">
    <table className="w-full text-sm">
      <thead className="bg-slate-900/40"><tr>{head}</tr></thead>
      <tbody className="divide-y divide-slate-700/60">{children}</tbody>
    </table>
  </div>
);
// Filters live in the card body (not Card's `right` slot), in a row that wraps on a phone.
const FilterRow = ({ children }) => (
  <div className="px-5 py-4 flex flex-wrap gap-2 items-center text-sm [&>input]:w-auto [&>select]:w-auto">{children}</div>
);
const Empty = ({ text }) => <div className="p-10 text-center text-slate-400">{text}</div>;
const ErrorBox = ({ error, onRetry }) => (
  <div className="p-6 text-center text-red-300">
    {error} {onRetry && <button className="underline ml-2" onClick={onRetry}>Retry</button>}
  </div>
);

function useLoad(fn, deps) {
  const [state, setState] = useState({ loading: true, error: "", data: null });
  const load = useCallback(async () => {
    setState((s) => ({ ...s, loading: true, error: "" }));
    try {
      setState({ loading: false, error: "", data: await fn() });
    } catch (e) {
      setState({ loading: false, error: e.message || "Could not load", data: null });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  useEffect(() => { load(); }, [load]);
  return [state, load];
}

// ============================================================================
const OtaPayments = () => {
  const [toast, showToast] = useToast();
  const [tab, setTab] = useState("payments");
  const [version, setVersion] = useState(0);          // bump to reload everything after an action
  const refresh = () => setVersion((v) => v + 1);
  const [adding, setAdding] = useState(false);
  const [importing, setImporting] = useState(false);
  const [settings, setSettings] = useState(false);
  const [channel, setChannel] = useState("all");
  const [summary] = useLoad(() => getPaymentSummary(chanParam(channel)), [version, channel]);
  const s = summary.data;

  const runImport = async () => {
    const since = window.prompt("Read OTA payment mails (Go-MMT, Yatra) received on or after (YYYY-MM-DD):", "2025-12-01");
    if (!since) return;
    setImporting(true);
    try {
      const r = await importPayments(since);
      const p = r.payments || {};
      showToast(r.configured === false
        ? "The OTA mailbox is not configured on this server."
        : `Read ${r.processed || 0} mail(s): ${p.stored || 0} payment(s) saved, ${p.skipped || 0} skipped ` +
          `(already saved or other property), ${p.statements || 0} statement(s), ${p.documents || 0} Yatra invoice(s).`);
      refresh();
    } catch (e) { showToast(`❌ ${e.message}`); }
    setImporting(false);
  };

  return (
    <PageShell
      icon="💸"
      title="OTA Payments"
      subtitle="Goibibo, MakeMyTrip and Yatra: which booking the OTA has paid, in which bank transfer (UTR), and whether you have seen that money in the bank."
      toast={toast}
    >
      {/* not PageShell's `right` slot: that slot never shrinks, so on a phone it pushed the page sideways */}
      <div className="flex flex-wrap gap-2 mb-4 items-center">
        <div className="flex flex-wrap gap-1 mr-auto" role="group" aria-label="Channel">
          {CHANNELS.map(([k, label]) => (
            <button key={k} onClick={() => setChannel(k)}
              className={`px-3 py-1.5 rounded-full text-sm font-semibold border transition ${channel === k
                ? "bg-[#E5C07B] text-slate-900 border-[#E5C07B]" : "border-slate-600 text-slate-300 hover:bg-slate-700/60"}`}>
              {label}
            </button>
          ))}
        </div>
        <GhostButton onClick={() => setSettings(true)}>Settings</GhostButton>
        <GhostButton onClick={runImport} disabled={importing}>{importing ? "Reading mail…" : "Import history"}</GhostButton>
        <PrimaryButton onClick={() => setAdding(true)}>+ Add payment</PrimaryButton>
      </div>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-6">
        <Stat label="Payment pending (stay started)" value={s ? `${s.pending.count} · ${money(s.pending.expected)}` : "…"} tone="text-amber-300" />
        <Stat label={`Overdue (${s ? s.overdue_days : 3}+ days after checkout; Yatra: check-in)`} value={s ? `${s.overdue.count} · ${money(s.overdue.expected)}` : "…"} tone="text-red-300" />
        <Stat label="Payment done - check the bank" value={s ? `${s.awaiting_bank_check.count} · ${money(s.awaiting_bank_check.amount)}` : "…"} tone="text-sky-300" />
        <Stat label="Received this month" value={s ? `${s.received_this_month.count} · ${money(s.received_this_month.amount)}` : "…"} tone="text-emerald-300" />
      </div>
      {summary.error && <ErrorBox error={summary.error} />}

      <Tabs tabs={[["payments", "Payments"], ["bookings", "Bookings"], ["exceptions", "Exceptions"], ["tax", "TDS / TCS"]]} active={tab} onChange={setTab} />
      {tab === "payments" && <PaymentsTab version={version} refresh={refresh} showToast={showToast} channel={channel} />}
      {tab === "bookings" && <BookingsTab version={version} channel={channel} />}
      {tab === "exceptions" && <ExceptionsTab version={version} refresh={refresh} showToast={showToast} channel={channel} />}
      {tab === "tax" && <TaxTab key={channel} version={version} refresh={refresh} showToast={showToast} channel={channel} />}
      {settings && <SettingsModal onClose={() => setSettings(false)} onSaved={() => { setSettings(false); refresh(); showToast("Settings saved"); }} />}

      {adding && <AddPaymentModal onClose={() => setAdding(false)} onSaved={() => { setAdding(false); refresh(); showToast("Payment added"); }} />}
    </PageShell>
  );
};

// ---------------------------------------------------------------- Payments
function PaymentsTab({ version, refresh, showToast, channel }) {
  const [from, setFrom] = useState(daysAgo(89));
  const [to, setTo] = useState(today());
  const [list, reload] = useLoad(() => getPayments({ from, to, channel: chanParam(channel) }), [from, to, version, channel]);
  const [open, setOpen] = useState(null);         // payment id expanded
  const [receiving, setReceiving] = useState(null);
  const [undoing, setUndoing] = useState(null);
  const rows = list.data?.rows || [];

  return (
    <Card title="OTA transfers">
      <FilterRow>
        <input type="date" className={inputCls} value={from} onChange={(e) => setFrom(e.target.value)} aria-label="From" />
        <span className="text-slate-400">to</span>
        <input type="date" className={inputCls} value={to} onChange={(e) => setTo(e.target.value)} aria-label="To" />
      </FilterRow>
      {list.loading ? <Spinner className="p-10" /> : list.error ? <ErrorBox error={list.error} onRetry={reload} /> : !rows.length ? (
        <Empty text="No Go-MMT payments in these dates. Use Import history to read past payment mails." />
      ) : (
        <Table head={<><Th>Paid on</Th><Th>UTR</Th><Th right>Amount</Th><Th>Bookings</Th><Th>State</Th><Th></Th></>}>
          {rows.map((p) => (
            <React.Fragment key={p.id}>
              <tr className="hover:bg-slate-800/40">
                <Td>{p.payment_date}<div className="text-xs text-slate-500">{p.source === "manual" ? "added by hand" : "from mail"}</div>
                  <div className="flex flex-wrap gap-1 mt-1">{(p.channels || []).map((c) => <Chip key={c}>{BRAND[c] || c}</Chip>)}</div></Td>
                <Td className="font-mono text-xs">{p.utr}</Td>
                <Td right>{money(p.amount)}<ExpectedLine p={p} /></Td>
                <Td>
                  {p.bookings}
                  {p.unmatched > 0 && <span className="ml-2"><Chip tone="danger">{p.unmatched} not found</Chip></span>}
                  {p.differences > 0 && <span className="ml-2"><Chip tone="warn">{p.differences} differ</Chip></span>}
                  <Flags flags={p.flags} />
                </Td>
                <Td>
                  {p.state === "received" ? <Chip tone="ok">Received {p.received_on}</Chip> : <Chip tone="info">Payment done</Chip>}
                  {p.received_difference ? <div className="text-xs text-amber-300 mt-1">Bank showed {money(p.received_amount)} ({p.received_difference > 0 ? "+" : ""}{p.received_difference})</div> : null}
                </Td>
                <Td right>
                  <div className="flex gap-2 justify-end">
                    <GhostButton onClick={() => setOpen(open === p.id ? null : p.id)}>{open === p.id ? "Hide" : "Bookings"}</GhostButton>
                    {p.state === "done"
                      ? <PrimaryButton onClick={() => setReceiving(p)}>Confirm received</PrimaryButton>
                      : <DangerButton onClick={() => setUndoing(p)}>Undo</DangerButton>}
                  </div>
                </Td>
              </tr>
              {open === p.id && (
                <tr><td colSpan={6} className="bg-slate-900/40 p-3"><PaymentLines id={p.id} version={version} /></td></tr>
              )}
            </React.Fragment>
          ))}
        </Table>
      )}
      {receiving && (
        <ReceiveModal p={receiving} onClose={() => setReceiving(null)}
          onDone={() => { setReceiving(null); refresh(); showToast(`UTR ${receiving.utr} marked received`); }} />
      )}
      {undoing && (
        <UndoModal p={undoing} onClose={() => setUndoing(null)}
          onDone={() => { setUndoing(null); refresh(); showToast(`UTR ${undoing.utr} back to payment done`); }} />
      )}
    </Card>
  );
}

function PaymentLines({ id, version }) {
  const [st, reload] = useLoad(() => getPayment(id), [id, version]);
  if (st.loading) return <Spinner className="p-4" />;
  if (st.error) return <ErrorBox error={st.error} onRetry={reload} />;
  const p = st.data;
  return (
    <div>
      <div className="text-xs text-slate-400 mb-2">
        {p.bank_name} a/c {p.bank_account} · Go-MMT account {p.ota_account_code || "—"}
        {p.received_note && <> · Note: {p.received_note}</>}
        {p.undo_reason && <> · Last undo: {p.undo_reason}</>}
      </div>
      <Table head={<><Th>Booking ID</Th><Th>Guest</Th><Th>Stay</Th><Th right>Voucher payable</Th><Th right>Paid</Th><Th>Match</Th></>}>
        {p.lines.map((x) => {
          const [label, tone] = LINE_LABEL[x.match_status] || [x.match_status, "neutral"];
          return (
            <tr key={x.id}>
              <Td className="font-mono text-xs">{x.ota_booking_id}<div className="text-slate-500">{BRAND[x.channel_code] || x.brand}{x.booking_id ? ` · #${x.booking_id}` : ""}</div></Td>
              <Td>{x.guest_name || "—"}</Td>
              <Td className="whitespace-nowrap">{x.check_in} → {x.check_out}</Td>
              <Td right>{x.expected_amount == null ? "—" : money(x.expected_amount)}</Td>
              <Td right>{money(x.amount_paid)}</Td>
              <Td><Chip tone={tone}>{label}</Chip>{x.linked_manually && <span className="text-xs text-slate-500 ml-1">by hand</span>}</Td>
            </tr>
          );
        })}
      </Table>
    </div>
  );
}

function ReceiveModal({ p, onClose, onDone }) {
  const [on, setOn] = useState(p.payment_date || today());
  const [amount, setAmount] = useState(String(p.amount));
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const diff = Number(amount || 0) - Number(p.amount || 0);
  const save = async () => {
    setBusy(true); setErr("");
    try {
      await receivePayment(p.id, { received_on: on, received_amount: Number(amount), note: note || null });
      onDone();
    } catch (e) { setErr(e.message); }
    setBusy(false);
  };
  return (
    <Modal title="Confirm received in bank" onClose={onClose}>
      <p className="text-slate-300 mb-4">
        Find <span className="font-mono text-[#E5C07B]">{p.utr}</span> for <b>{money(p.amount)}</b> in the bank statement.
        Confirming marks all {p.bookings} booking(s) in this payment as <b>Payment received</b>.
      </p>
      <div className="grid md:grid-cols-2 gap-4">
        <Field label="Credited in bank on" type="date" value={on} onChange={(e) => setOn(e.target.value)} />
        <Field label="Amount the bank shows" type="number" step="0.01" value={amount} onChange={(e) => setAmount(e.target.value)}
          hint={Math.abs(diff) >= 0.01 ? `Differs from Go-MMT's figure by ${diff.toFixed(2)} - it will be shown on the payment` : "Same as Go-MMT's figure"} />
      </div>
      <div className="mt-4"><Field label="Note (optional)" value={note} onChange={(e) => setNote(e.target.value)} maxLength={500} /></div>
      {err && <div className="text-red-300 mt-3">{err}</div>}
      <div className="flex justify-end gap-2 mt-6">
        <GhostButton onClick={onClose}>Cancel</GhostButton>
        <PrimaryButton onClick={save} disabled={busy || !on || amount === ""}>{busy ? "Saving…" : "Confirm received"}</PrimaryButton>
      </div>
    </Modal>
  );
}

function UndoModal({ p, onClose, onDone }) {
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const save = async () => {
    setBusy(true); setErr("");
    try { await undoPaymentReceipt(p.id, reason.trim()); onDone(); } catch (e) { setErr(e.message); }
    setBusy(false);
  };
  return (
    <Modal title="Undo 'received'" onClose={onClose}>
      <p className="text-slate-300 mb-4">
        UTR <span className="font-mono text-[#E5C07B]">{p.utr}</span> ({money(p.amount)}) goes back to <b>Payment done</b>.
        The earlier confirmation stays in the audit log.
      </p>
      <Field label="Why?" value={reason} onChange={(e) => setReason(e.target.value)} maxLength={500}
        hint="Required - e.g. 'checked the wrong statement'" />
      {err && <div className="text-red-300 mt-3">{err}</div>}
      <div className="flex justify-end gap-2 mt-6">
        <GhostButton onClick={onClose}>Cancel</GhostButton>
        <DangerButton onClick={save} disabled={busy || reason.trim().length < 3}>{busy ? "Saving…" : "Undo"}</DangerButton>
      </div>
    </Modal>
  );
}

// ---------------------------------------------------------------- Bookings
function BookingsTab({ version, channel }) {
  const [chainOf, setChainOf] = useState(null);
  const [from, setFrom] = useState(daysAgo(29));
  const [to, setTo] = useState(today());
  const [state, setState] = useState("");
  const [overdueOnly, setOverdueOnly] = useState(false);
  const [list, reload] = useLoad(
    () => getPaymentBookings({ from, to, state: state || undefined, overdue: overdueOnly ? true : undefined,
      channel: chanParam(channel) }),
    [from, to, state, overdueOnly, version, channel]);
  const rows = list.data?.rows || [];
  const c = list.data?.counts || {};
  const csv = () => exportCsv(`ota-payments-${from}-${to}.csv`, rows, [
    { header: "Booking", value: (r) => r.booking_id },
    { header: "OTA booking ID", value: (r) => r.ota_booking_id },
    { header: "Channel", value: (r) => BRAND[r.booking_source] || r.booking_source },
    { header: "Guest", value: (r) => r.guest_name },
    { header: "Check-in", value: (r) => r.check_in },
    { header: "Check-out", value: (r) => r.check_out },
    { header: "State", value: (r) => (r.overdue ? "overdue" : r.state) },
    { header: "Voucher payable", value: (r) => r.expected },
    { header: "Paid", value: (r) => r.paid },
    { header: "Difference", value: (r) => r.difference },
    { header: "UTR", value: (r) => (r.utrs || []).join(" ") },
    { header: "Paid on", value: (r) => r.payment_date },
    { header: "Received on", value: (r) => r.received_on },
    { header: "Flags", value: (r) => (r.flags || []).join(" ") },
  ]);
  return (
    <Card title="Bookings by checkout date">
      <FilterRow>
          <input type="date" className={inputCls} value={from} onChange={(e) => setFrom(e.target.value)} aria-label="Checkout from" />
          <input type="date" className={inputCls} value={to} onChange={(e) => setTo(e.target.value)} aria-label="Checkout to" />
          <select className={inputCls} value={state} onChange={(e) => setState(e.target.value)}>
            <option value="">All states</option>
            <option value="pending">Payment pending</option>
            <option value="done">Payment done</option>
            <option value="received">Payment received</option>
            <option value="not_applicable">Not applicable</option>
          </select>
          <label className="flex items-center gap-1 whitespace-nowrap text-slate-300">
            <input type="checkbox" checked={overdueOnly} onChange={(e) => setOverdueOnly(e.target.checked)} /> Overdue only
          </label>
          <GhostButton onClick={csv} disabled={!rows.length}>CSV</GhostButton>
      </FilterRow>
      <div className="px-5 pt-4 flex flex-wrap gap-2 text-xs">
        <Chip tone="warn">Pending {c.pending || 0}</Chip><Chip tone="danger">Overdue {c.overdue || 0}</Chip>
        <Chip tone="info">Done {c.done || 0}</Chip><Chip tone="ok">Received {c.received || 0}</Chip>
        <Chip>Not applicable {c.not_applicable || 0}</Chip>
      </div>
      {list.loading ? <Spinner className="p-10" /> : list.error ? <ErrorBox error={list.error} onRetry={reload} /> : !rows.length ? (
        <Empty text="No Goibibo / MakeMyTrip bookings match." />
      ) : (
        <Table head={<><Th>Booking</Th><Th>Guest</Th><Th>Stay</Th><Th right>Voucher payable</Th><Th right>Paid</Th><Th>UTR</Th><Th>State</Th></>}>
          {rows.map((r) => (
            <tr key={r.booking_id} className="hover:bg-slate-800/40">
              <Td>#{r.booking_id}<div className="font-mono text-xs text-slate-500">{r.ota_booking_id}</div><div className="text-xs text-slate-500">{BRAND[r.booking_source]}</div></Td>
              <Td>{r.guest_name || "—"}{r.status === "cancelled" && <div className="text-xs text-red-300">cancelled</div>}</Td>
              <Td className="whitespace-nowrap">{r.check_in} → {r.check_out}</Td>
              <Td right>{r.expected == null ? "—" : money(r.expected)}</Td>
              <Td right>{r.paid ? money(r.paid) : "—"}{r.difference ? <div className="text-xs text-amber-300">{r.difference > 0 ? "+" : ""}{r.difference}</div> : null}</Td>
              <Td className="font-mono text-xs">{(r.utrs || []).join(", ") || "—"}{r.payment_date && <div className="text-slate-500">{r.payment_date}</div>}</Td>
              <Td><PaymentStateChip st={r} /><Flags flags={r.flags} />
                <button className="block text-xs underline text-[#E5C07B] mt-1" onClick={() => setChainOf(r.booking_id)}>Money chain</button></Td>
            </tr>
          ))}
        </Table>
      )}
      {chainOf && <ChainModal bookingId={chainOf} onClose={() => setChainOf(null)} />}
    </Card>
  );
}

// ---------------------------------------------------------------- Exceptions
function ExceptionsTab({ version, refresh, showToast, channel }) {
  const [ex, reload] = useLoad(() => getPaymentExceptions(chanParam(channel)), [version, channel]);
  const [linking, setLinking] = useState(null);
  if (ex.loading) return <Spinner className="p-10" />;
  if (ex.error) return <ErrorBox error={ex.error} onRetry={reload} />;
  const { lines = [], payments = [], bookings = [] } = ex.data || {};
  const ignore = async (x) => {
    if (!window.confirm(`Take ${x.ota_booking_id} off this list? (Linking it later brings it back.)`)) return;
    try { await ignorePaymentLine(x.id); refresh(); showToast("Line ignored"); } catch (e) { showToast(`❌ ${e.message}`); }
  };
  return (
    <div className="space-y-6">
      <Card title={<>Payment lines to check <InfoTip text="A booking in a Go-MMT payment that the PMS could not tie to a booking, or whose amount differs from the voucher by more than ₹1." /></>}>
        {!lines.length ? <Empty text="Nothing to check - every paid booking matched." /> : (
          <Table head={<><Th>Paid on / UTR</Th><Th>Booking ID</Th><Th>Guest</Th><Th>Stay</Th><Th right>Voucher</Th><Th right>Paid</Th><Th>Problem</Th><Th></Th></>}>
            {lines.map((x) => {
              const [label, tone] = LINE_LABEL[x.match_status] || [x.match_status, "neutral"];
              return (
                <tr key={x.id}>
                  <Td>{x.payment_date}<div className="font-mono text-xs text-slate-500">{x.utr}</div></Td>
                  <Td className="font-mono text-xs">{x.ota_booking_id}{x.booking_id && <div className="text-slate-500">#{x.booking_id}</div>}</Td>
                  <Td>{x.guest_name || "—"}</Td>
                  <Td className="whitespace-nowrap">{x.check_in} → {x.check_out}</Td>
                  <Td right>{x.expected_amount == null ? "—" : money(x.expected_amount)}</Td>
                  <Td right>{money(x.amount_paid)}</Td>
                  <Td><Chip tone={tone}>{label}</Chip></Td>
                  <Td right>
                    <div className="flex gap-2 justify-end">
                      <GhostButton onClick={() => setLinking(x)}>Link booking</GhostButton>
                      <GhostButton onClick={() => ignore(x)}>Ignore</GhostButton>
                    </div>
                  </Td>
                </tr>
              );
            })}
          </Table>
        )}
      </Card>

      <Card title="Payments with a warning">
        {!payments.length ? <Empty text="No payment has a wrong bank account or totals that don't add up." /> : (
          <Table head={<><Th>Paid on</Th><Th>UTR</Th><Th right>Amount</Th><Th right>Rows add up to</Th><Th>Bank account</Th><Th>Warning</Th></>}>
            {payments.map((p) => (
              <tr key={p.id}>
                <Td>{p.payment_date}</Td><Td className="font-mono text-xs">{p.utr}</Td>
                <Td right>{money(p.amount)}</Td><Td right>{p.lines_total == null ? "—" : money(p.lines_total)}</Td>
                <Td>{p.bank_name} {p.bank_account}</Td><Td><Flags flags={p.flags} /></Td>
              </tr>
            ))}
          </Table>
        )}
      </Card>

      <Card title="Bookings overdue or paid the wrong amount">
        {!bookings.length ? <Empty text="No overdue or mis-paid bookings." /> : (
          <Table head={<><Th>Booking</Th><Th>Guest</Th><Th>Stay</Th><Th right>Voucher payable</Th><Th right>Paid</Th><Th>State</Th></>}>
            {bookings.map((r) => (
              <tr key={r.booking_id}>
                <Td>#{r.booking_id}<div className="font-mono text-xs text-slate-500">{r.ota_booking_id}</div></Td>
                <Td>{r.guest_name || "—"}</Td>
                <Td className="whitespace-nowrap">{r.check_in} → {r.check_out}</Td>
                <Td right>{r.expected == null ? "—" : money(r.expected)}</Td>
                <Td right>{r.paid ? money(r.paid) : "—"}</Td>
                <Td><PaymentStateChip st={r} /><Flags flags={r.flags} /></Td>
              </tr>
            ))}
          </Table>
        )}
        {bookings.some((r) => r.overdue) && (
          <div className="px-5 pb-4 text-xs text-slate-400">Overdue: ask Go-MMT at payments@go-mmt.com, or your Yatra account manager, with the booking IDs.</div>
        )}
      </Card>

      {linking && (
        <LinkModal line={linking} onClose={() => setLinking(null)}
          onDone={() => { setLinking(null); refresh(); showToast("Line linked"); }} />
      )}
    </div>
  );
}

// ---------------------------------------------------------------- TDS / TCS (batch 3)
const KIND_SHORT = { tds: "TDS", tcs: "TCS", commission: "Commission invoice", credit_memo: "Credit memo" };

/** A pre-filled e-mail asking Go-MMT for a statement that never came. No address: the owner's mail
 *  app asks for it (their Go-MMT account manager). */
function askMmtHref(kind, label) {
  const what = { tds: "TDS statement", tcs: "TCS statement", commission: "monthly commission invoice / statement" }[kind];
  const subject = `Hotel Bhimas Tirupati (HTLDOM014889) - ${what} for ${label} not received`;
  const body = `Dear Go-MMT team,\n\nWe have not received the ${what} for bookings in ${label} for Hotel Bhimas, ` +
    `Tirupati (vendor code HTLDOM014889). Please send it to this address.\n\nRegards,\nHotel Bhimas`;
  return `mailto:?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`;
}

function TaxCell({ m, k }) {
  const t = m[k];
  if (m.channel === "yatra") {
    // Yatra sends no TDS / TCS statement - its vouchers are the record
    return t.voucher == null ? <span className="text-slate-500 text-xs">{t.bookings ? "no voucher figures" : "—"}</span> : (
      <div>
        <div className="font-semibold">{money(t.voucher)}</div>
        <div className="text-xs text-slate-400">from {t.with_voucher} voucher(s) · Yatra issues no statement</div>
      </div>
    );
  }
  if (!t.received) {
    if (!m.due) return <span className="text-slate-500 text-xs">due by {m.due_on}</span>;
    return (
      <div>
        <Chip tone="danger">Not received</Chip>
        <a className="block text-xs text-[#E5C07B] underline mt-1" href={askMmtHref(k, m.label)}>Ask Go-MMT</a>
      </div>
    );
  }
  return (
    <div>
      <div className="font-semibold">{money(t.statement)}</div>
      <div className="text-xs text-slate-400">
        {t.bookings} booking(s)
        {t.with_voucher > 0
          ? <> · vouchers {money(t.voucher)} for {t.with_voucher} (statement {money(t.statement_with_voucher)})</>
          : <> · no voucher figures</>}
      </div>
      {t.differs > 0 && <Chip tone="warn">{t.differs} differ</Chip>}
    </div>
  );
}

function TaxTab({ version, refresh, showToast, channel }) {
  // starts from the page's channel switch; the parent remounts this tab (key) when that switch changes
  const [ota, setOta] = useState(channel === "yatra" ? "yatra" : "go_mmt");
  const [rep, reload] = useLoad(() => getStatementMonths(12, ota), [version, ota]);
  const [open, setOpen] = useState(null);
  const [uploading, setUploading] = useState(false);
  const months = rep.data?.months || [];
  const upload = async (e) => {
    const f = e.target.files?.[0];
    e.target.value = "";
    if (!f) return;
    setUploading(true);
    try {
      const r = await uploadOtaMail(f);
      showToast(r.statement
        ? (r.stored ? (r.channel === "yatra" ? `Yatra ${KIND_SHORT[r.kind]?.toLowerCase()} saved`
          : `${KIND_SHORT[r.kind]} for ${r.period?.slice(0, 7)} saved (${r.rows} bookings)`) : `Not saved: ${r.reason}`)
        : (r.stored || r.stored_count ? "Payment saved" : `Not saved: ${r.reason || "already recorded"}`));
      refresh();
    } catch (err) { showToast(`❌ ${err.message}`); }
    setUploading(false);
  };
  const dl = (s, part) => downloadStatementFile(s.id, part, part === "pdf" ? s.pdf_name : s.file_name)
    .catch((err) => showToast(`❌ ${err.message}`));
  return (
    <Card title={<>{ota === "yatra" ? "Yatra" : "Go-MMT"} statements by month <InfoTip text={ota === "yatra"
      ? "Yatra sends a GST commission invoice with each booking and a credit note when one is cancelled; TDS (0.1%) and TCS (0.5%) are as each voucher states them - Yatra issues no monthly statement. For filing, Form 26AS and the GST portal's TCS credit are final."
      : "TDS (0.1%, 194-O) and TCS (0.5%, GST) Go-MMT deducted, as its monthly statements say, against the figures on each booking's voucher. For filing, Form 26AS and the GST portal's TCS credit are final; this is the booking-by-booking check."} /></>}>
      <FilterRow>
        <div className="flex gap-1 mr-2">
          {[["go_mmt", "Go-MMT"], ["yatra", "Yatra"]].map(([k, l]) => (
            <button key={k} onClick={() => setOta(k)} className={`px-3 py-1.5 rounded-lg text-sm font-semibold border ${ota === k
              ? "bg-[#E5C07B] text-slate-900 border-[#E5C07B]" : "border-slate-600 text-slate-300 hover:bg-slate-700/60"}`}>{l}</button>
          ))}
        </div>
        <label className={`cursor-pointer ${uploading ? "opacity-50" : ""}`}>
          <span className="inline-block bg-slate-800/60 border border-slate-600 hover:bg-slate-700/60 px-4 py-2 rounded-lg font-semibold">
            {uploading ? "Reading…" : "Upload an OTA mail (.eml)"}
          </span>
          <input type="file" accept=".eml,message/rfc822" className="hidden" onChange={upload} disabled={uploading} />
        </label>
        <span className="text-xs text-slate-400">For a Go-MMT statement / payment mail, a Yatra payment advice, or a Yatra booking mail with its invoice, that reached your main inbox only (Gmail: ⋮ → Download message).</span>
      </FilterRow>
      {rep.loading ? <Spinner className="p-10" /> : rep.error ? <ErrorBox error={rep.error} onRetry={reload} /> : !months.length ? (
        <Empty text={ota === "yatra" ? "No Yatra invoices yet. They come with each Yatra booking mail; use Import history to read past ones."
          : "No Go-MMT statements yet. They arrive by mail each month; use Import history to read past ones."} />
      ) : (
        <Table head={<><Th>Month</Th><Th>TDS deducted</Th><Th>TCS collected</Th><Th>Commission</Th><Th>{ota === "yatra" ? "Invoices" : "Files"}</Th><Th></Th></>}>
          {months.map((m) => (
            <tr key={m.period} className="hover:bg-slate-800/40">
              <Td className="font-semibold whitespace-nowrap">{m.label}</Td>
              <Td><TaxCell m={m} k="tds" /></Td>
              <Td><TaxCell m={m} k="tcs" /></Td>
              <Td>
                {m.commission.received ? (
                  <div>
                    <div className="font-semibold">{money(m.commission.total)}</div>
                    <div className="text-xs text-slate-400">{money(m.commission.commission)} + GST {money(m.commission.gst)}</div>
                    <div className="text-xs text-slate-500 font-mono">{ota === "yatra" ? `${m.commission.invoices.length} invoice(s)` : m.commission.invoices.join(", ")}</div>
                    {m.commission.differs > 0 && <Chip tone="warn">{m.commission.differs} differ from voucher</Chip>}
                  </div>
                ) : ota === "yatra" ? <span className="text-slate-500 text-xs">—</span> : m.due ? (
                  <div><Chip tone="danger">Not received</Chip>
                    <a className="block text-xs text-[#E5C07B] underline mt-1" href={askMmtHref("commission", m.label)}>Ask Go-MMT</a></div>
                ) : <span className="text-slate-500 text-xs">due by {m.due_on}</span>}
                {m.commission.credit_memos !== 0 && <div className="text-xs text-emerald-300 mt-1">Credit memos {money(m.commission.credit_memos)}</div>}
              </Td>
              <Td>
                <div className="flex flex-col gap-1">
                  {(ota === "yatra" ? m.files.slice(0, 4) : m.files).map((s) => (
                    <div key={s.id} className="flex flex-wrap gap-1 items-center text-xs">
                      <span className="text-slate-400">{ota === "yatra" ? s.doc_no : KIND_SHORT[s.kind]}</span>
                      {s.file_name && <button className="underline text-[#E5C07B]" onClick={() => dl(s, "xlsx")}>xlsx</button>}
                      {s.pdf_name && <button className="underline text-[#E5C07B]" onClick={() => dl(s, "pdf")}>pdf</button>}
                    </div>
                  ))}
                  {ota === "yatra" && m.files.length > 4 && <span className="text-xs text-slate-500">+{m.files.length - 4} more - see Bookings</span>}
                  {!m.files.length && <span className="text-slate-500 text-xs">—</span>}
                </div>
              </Td>
              <Td right>{m.files.length > 0 && <GhostButton onClick={() => setOpen(m)}>Bookings</GhostButton>}</Td>
            </tr>
          ))}
        </Table>
      )}
      {open && <TaxMonthModal m={open} ota={ota} onClose={() => setOpen(null)} />}
    </Card>
  );
}

const CHECK_CHIP = {
  ok: ["Matches voucher", "ok"], differs: ["Differs", "warn"], no_voucher: ["No voucher figure", "neutral"],
  not_in_statement: ["Not in statement", "neutral"], voucher_only: ["Voucher (no statement)", "neutral"],
};

function TaxMonthModal({ m, onClose, ota = "go_mmt" }) {
  const [st] = useLoad(() => getStatementMonth(m.period.slice(0, 7), ota), [m.period, ota]);
  const [onlyProblems, setOnlyProblems] = useState(false);
  const rows = (st.data?.rows || []).filter((r) => !onlyProblems || r.flags.length);
  const csv = () => exportCsv(`${ota === "yatra" ? "yatra" : "go-mmt"}-statements-${m.period.slice(0, 7)}.csv`, rows, [
    { header: "OTA booking ID", value: (r) => r.ota_booking_id },
    { header: "PMS booking", value: (r) => r.booking_id },
    { header: "Guest", value: (r) => r.guest_name },
    { header: "Check-in", value: (r) => r.check_in },
    { header: "Check-out", value: (r) => r.check_out },
    { header: "TDS statement", value: (r) => r.tds },
    { header: "TDS voucher", value: (r) => r.tds_voucher },
    { header: "TDS check", value: (r) => r.tds_check },
    { header: "TCS statement", value: (r) => r.tcs },
    { header: "TCS voucher", value: (r) => r.tcs_voucher },
    { header: "TCS check", value: (r) => r.tcs_check },
    { header: "Commission incl GST", value: (r) => r.commission },
    { header: "GST on commission", value: (r) => r.commission_gst },
    { header: "Credit memo", value: (r) => r.credit_memo },
    { header: "Commission voucher", value: (r) => r.commission_voucher },
    { header: "Commission check", value: (r) => r.commission_check },
    { header: "Flags", value: (r) => r.flags.join(" ") },
  ]);
  const Check = ({ v }) => { const [l, t] = CHECK_CHIP[v] || [v, "neutral"]; return <Chip tone={t}>{l}</Chip>; };
  return (
    <Modal title={`${ota === "yatra" ? "Yatra" : "Go-MMT"} statements - ${m.label}`} onClose={onClose} wide>
      <div className="flex flex-wrap gap-3 items-center mb-3 text-sm">
        <label className="flex items-center gap-1 text-slate-300">
          <input type="checkbox" checked={onlyProblems} onChange={(e) => setOnlyProblems(e.target.checked)} /> Only bookings to check
        </label>
        <GhostButton onClick={csv} disabled={!rows.length}>CSV</GhostButton>
        <span className="text-xs text-slate-400">"Not in PMS" = an OTA booking this hotel's PMS has no record of.</span>
      </div>
      {st.loading ? <Spinner className="p-8" /> : st.error ? <ErrorBox error={st.error} /> : !rows.length ? <Empty text="Nothing to show." /> : (
        <div className="max-h-[60vh] overflow-y-auto">
          <Table head={<><Th>Booking</Th><Th>Stay</Th><Th right>TDS</Th><Th right>TCS</Th><Th right>Commission</Th><Th>Check</Th></>}>
            {rows.map((r) => (
              <tr key={r.ota_booking_id}>
                <Td className="font-mono text-xs">{r.ota_booking_id}<div className="text-slate-500 font-sans">{r.booking_id ? `#${r.booking_id}` : "not in PMS"} · {r.guest_name || "—"}</div></Td>
                <Td className="whitespace-nowrap text-xs">{r.check_in} → {r.check_out}</Td>
                <Td right>{r.tds_check === "not_in_statement" ? "—" : money(r.tds)}{r.tds_voucher != null && r.tds_check !== "not_in_statement" && <div className="text-xs text-slate-500">voucher {money(r.tds_voucher)}</div>}</Td>
                <Td right>{r.tcs_check === "not_in_statement" ? "—" : money(r.tcs)}{r.tcs_voucher != null && r.tcs_check !== "not_in_statement" && <div className="text-xs text-slate-500">voucher {money(r.tcs_voucher)}</div>}</Td>
                <Td right>{r.commission ? money(r.commission) : "—"}{r.credit_memo ? <div className="text-xs text-emerald-300">memo {money(r.credit_memo)}</div> : null}
                  {r.commission_voucher != null && r.commission_check !== "not_in_statement" && <div className="text-xs text-slate-500">voucher {money(r.commission_voucher)}</div>}</Td>
                <Td>
                  <div className="flex flex-col gap-1 items-start">
                    {r.tds_check !== "not_in_statement" && <span className="text-xs">TDS <Check v={r.tds_check} /></span>}
                    {r.tcs_check !== "not_in_statement" && <span className="text-xs">TCS <Check v={r.tcs_check} /></span>}
                    {r.commission_check && r.commission_check !== "not_in_statement" && <span className="text-xs">Commission <Check v={r.commission_check} /></span>}
                    {r.booking_status === "cancelled" && <Chip tone="neutral">Cancelled</Chip>}
                  </div>
                </Td>
              </tr>
            ))}
          </Table>
        </div>
      )}
    </Modal>
  );
}

// ---------------------------------------------------------------- P2a: expected vs received, chain, settings
/** Under a payment amount: what the vouchers say it should have been. */
function ExpectedLine({ p }) {
  if (p.expected_total == null) return <div className="text-xs text-slate-500">expected: —</div>;
  if (p.expected_missing) {
    return <div className="text-xs text-slate-400">expected {money(p.expected_total)} for {p.bookings - p.expected_missing} of {p.bookings}</div>;
  }
  const d = p.expected_difference || 0;
  return (
    <div className={`text-xs ${Math.abs(d) > 1 ? "text-amber-300" : "text-slate-400"}`}>
      expected {money(p.expected_total)}{Math.abs(d) >= 0.01 && <> · {d > 0 ? "+" : ""}{d.toFixed(2)}</>}
    </div>
  );
}

const STEP_CHIP = {
  ok: ["OK", "ok"], differs: ["Differs", "warn"], missing: ["Not yet", "neutral"], no_voucher: ["No voucher figure", "neutral"],
  voucher_only: ["Voucher only", "neutral"], pending: ["Check the bank", "info"], info: ["", ""],
};

/** The booking's money from the guest's gross to the hotel's bank, step by step. */
function ChainModal({ bookingId, onClose }) {
  const [st] = useLoad(() => getBookingChain(bookingId), [bookingId]);
  const ch = st.data?.chain;
  return (
    <Modal title={`Money chain - booking #${bookingId}`} onClose={onClose} wide>
      {st.loading ? <Spinner className="p-8" /> : st.error ? <ErrorBox error={st.error} /> : !ch ? <Empty text="Not a Goibibo / MakeMyTrip / Yatra booking." /> : (
        <div>
          <div className="text-sm text-slate-300 mb-3 flex flex-wrap gap-2 items-center">
            <span className="font-mono">{ch.ota_booking_id}</span> <Chip>{BRAND[ch.channel] || ch.channel}</Chip>
            <PaymentStateChip st={ch.state} />
            {ch.breaks.length ? <Chip tone="warn">Breaks at: {ch.breaks.join(", ")}</Chip> : <Chip tone="ok">No break found</Chip>}
          </div>
          <Table head={<><Th>Step</Th><Th right>Voucher says</Th><Th right>OTA document / actual</Th><Th>Check</Th><Th>Reference</Th></>}>
            {ch.steps.map((x) => {
              const [label, tone] = STEP_CHIP[x.status] || [x.status, "neutral"];
              return (
                <tr key={x.key}>
                  <Td>{x.label}{x.note && <div className="text-xs text-slate-500">{x.note}</div>}</Td>
                  <Td right>{x.voucher == null ? "—" : money(x.voucher)}</Td>
                  <Td right>{x.actual == null ? "—" : money(x.actual)}</Td>
                  <Td>{label && <Chip tone={tone}>{label}</Chip>}{x.overdue && <Chip tone="danger">Overdue</Chip>}</Td>
                  <Td className="text-xs font-mono">{[...(x.documents || []), x.date].filter(Boolean).join(" · ") || "—"}</Td>
                </tr>
              );
            })}
          </Table>
        </div>
      )}
    </Modal>
  );
}

function SettingsModal({ onClose, onSaved }) {
  const [st] = useLoad(getPaymentSettings, []);
  const [edits, setEdits] = useState({});           // only what the admin changed, over the loaded values
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const form = st.data ? { ...st.data, ...edits } : null;
  const set = (k) => (e) => setEdits((f) => ({ ...f, [k]: e.target.value }));
  const save = async () => {
    setBusy(true); setErr("");
    try {
      await putPaymentSettings({ forwarders: form.forwarders, yatra_vendor_id: String(form.yatra_vendor_id),
        bank_account: String(form.bank_account), overdue_days: Number(form.overdue_days), tracking_start: form.tracking_start });
      onSaved();
    } catch (e) { setErr(e.message); }
    setBusy(false);
  };
  return (
    <Modal title="OTA payment settings" onClose={onClose}>
      {!form ? (st.error ? <ErrorBox error={st.error} /> : <Spinner className="p-8" />) : (
        <div className="grid gap-4">
          <Field label="Bank account the OTAs must pay into" value={form.bank_account} onChange={set("bank_account")}
            hint="A payment to any other account raises a high alert" />
          <Field label="Yatra vendor ID" value={form.yatra_vendor_id} onChange={set("yatra_vendor_id")}
            hint="As printed on Yatra's YES Bank payment advice (58270). Advices for any other vendor are ignored." />
          <Field label="Overdue after (days)" type="number" min="0" max="60" value={form.overdue_days} onChange={set("overdue_days")}
            hint="Go-MMT: days after checkout. Yatra: days after check-in." />
          <Field label="Forwarding address(es)" value={form.forwarders} onChange={set("forwarders")}
            hint="Your own address(es), comma separated, allowed to send OTA mails in as Gmail 'Forward as attachment' (e.g. hotelbhimas@gmail.com)" />
          <Field label="Track bookings from" type="date" value={form.tracking_start} onChange={set("tracking_start")}
            hint="Leave empty to start from the first OTA stay in the PMS. Payments for earlier stays are kept but not listed as problems." />
          <div className="text-xs text-slate-500">Go-MMT accounts: {Object.entries(form.accounts || {}).map(([k, v]) => `${k} = ${v === "this" ? "this hotel" : "ignored"}`).join(", ")}</div>
          {err && <div className="text-red-300">{err}</div>}
          <div className="flex justify-end gap-2">
            <GhostButton onClick={onClose}>Cancel</GhostButton>
            <PrimaryButton onClick={save} disabled={busy}>{busy ? "Saving…" : "Save"}</PrimaryButton>
          </div>
        </div>
      )}
    </Modal>
  );
}

// ---------------------------------------------------------------- pickers
function CandidateSearch({ unpaidOnly, initial = "", children }) {
  const [q, setQ] = useState(initial);
  const [term, setTerm] = useState(initial);
  useEffect(() => { const t = setTimeout(() => setTerm(q), 350); return () => clearTimeout(t); }, [q]);
  const [list] = useLoad(() => getPaymentCandidates({ q: term || undefined, unpaid_only: unpaidOnly }), [term, unpaidOnly]);
  return (
    <div>
      <Field label="Find booking" value={q} onChange={(e) => setQ(e.target.value)} placeholder="OTA booking ID, guest name or #booking" />
      <div className="mt-3 max-h-80 overflow-y-auto border border-slate-700 rounded-lg">
        {list.loading ? <Spinner className="p-6" /> : list.error ? <ErrorBox error={list.error} /> :
          !(list.data?.rows || []).length ? <Empty text={unpaidOnly ? "No unpaid Goibibo / MakeMyTrip booking matches." : "No booking matches."} /> :
            children(list.data.rows)}
      </div>
    </div>
  );
}

function LinkModal({ line, onClose, onDone }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const pick = async (b) => {
    setBusy(true); setErr("");
    try { await linkPaymentLine(line.id, b.booking_id); onDone(); } catch (e) { setErr(e.message); }
    setBusy(false);
  };
  return (
    <Modal title={`Link ${line.ota_booking_id}`} onClose={onClose} wide>
      <p className="text-slate-300 mb-4">
        Go-MMT paid <b>{money(line.amount_paid)}</b> for {line.guest_name || "this guest"} ({line.check_in} → {line.check_out}).
        Pick the PMS booking it belongs to.
      </p>
      <CandidateSearch unpaidOnly={false} initial={line.guest_name || ""}>
        {(rows) => rows.map((b) => (
          <button key={b.booking_id} disabled={busy} onClick={() => pick(b)}
            className="w-full text-left px-4 py-2 hover:bg-slate-700/60 border-b border-slate-700/60 flex justify-between gap-4">
            <span>#{b.booking_id} · {b.guest_name || "—"} · <span className="font-mono text-xs">{b.ota_booking_id || "no OTA id"}</span></span>
            <span className="text-slate-400 text-sm whitespace-nowrap">{b.check_in} → {b.check_out} · {b.expected == null ? "—" : money(b.expected)} <PaymentStateChip st={b} /></span>
          </button>
        ))}
      </CandidateSearch>
      {err && <div className="text-red-300 mt-3">{err}</div>}
    </Modal>
  );
}

function AddPaymentModal({ onClose, onSaved }) {
  const [utr, setUtr] = useState("");
  const [date, setDate] = useState(today());
  const [amount, setAmount] = useState("");
  const [note, setNote] = useState("");
  const [picked, setPicked] = useState({});          // booking_id -> row
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const total = useMemo(() => Object.values(picked).reduce((t, b) => t + Number(b.expected || 0), 0), [picked]);
  const toggle = (b) => setPicked((p) => {
    const n = { ...p };
    if (n[b.booking_id]) delete n[b.booking_id]; else n[b.booking_id] = b;
    return n;
  });
  const save = async () => {
    setBusy(true); setErr("");
    try {
      await addPayment({ utr: utr.trim(), payment_date: date, amount: Number(amount),
        booking_ids: Object.keys(picked).map(Number), note: note || null });
      onSaved();
    } catch (e) { setErr(e.message); }
    setBusy(false);
  };
  const ids = Object.keys(picked);
  return (
    <Modal title="Add a Go-MMT payment by hand" onClose={onClose} wide>
      <p className="text-slate-400 text-sm mb-4">
        Only for a transfer whose Go-MMT mail never arrived. Copy the UTR, date and amount from the bank or the
        Extranet's Payments page, then tick the bookings it paid.
      </p>
      <div className="grid md:grid-cols-3 gap-4">
        <Field label="UTR / bank reference" value={utr} onChange={(e) => setUtr(e.target.value)} placeholder="HDFCH0…" />
        <Field label="Paid on" type="date" value={date} onChange={(e) => setDate(e.target.value)} />
        <Field label="Amount transferred" type="number" step="0.01" value={amount} onChange={(e) => setAmount(e.target.value)} />
      </div>
      <div className="mt-4">
        <CandidateSearch unpaidOnly>
          {(rows) => rows.map((b) => (
            <label key={b.booking_id} className="flex items-center gap-3 px-4 py-2 hover:bg-slate-700/60 border-b border-slate-700/60 cursor-pointer">
              <input type="checkbox" checked={!!picked[b.booking_id]} onChange={() => toggle(b)} />
              <span className="flex-1">#{b.booking_id} · {b.guest_name || "—"} · <span className="font-mono text-xs">{b.ota_booking_id}</span></span>
              <span className="text-slate-400 text-sm whitespace-nowrap">{b.check_in} → {b.check_out} · {b.expected == null ? "no voucher amount" : money(b.expected)}</span>
            </label>
          ))}
        </CandidateSearch>
      </div>
      <div className={`mt-3 text-sm ${amount && Math.abs(total - Number(amount)) > 0.5 ? "text-amber-300" : "text-slate-300"}`}>
        {ids.length} booking(s) ticked · voucher payables add up to {money(total)}
        {amount && Math.abs(total - Number(amount)) > 0.5 && " - not the amount transferred; it will be saved with a warning"}
      </div>
      <div className="mt-4"><Field label="Note (optional)" value={note} onChange={(e) => setNote(e.target.value)} maxLength={500} /></div>
      {err && <div className="text-red-300 mt-3">{err}</div>}
      <div className="flex justify-end gap-2 mt-6">
        <GhostButton onClick={onClose}>Cancel</GhostButton>
        <PrimaryButton onClick={save} disabled={busy || !utr.trim() || !date || !(Number(amount) > 0) || !ids.length}>
          {busy ? "Saving…" : "Add payment"}
        </PrimaryButton>
      </div>
    </Modal>
  );
}

export default OtaPayments;
