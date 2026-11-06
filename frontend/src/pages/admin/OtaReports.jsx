// P2b (owner, 5-6 Nov): OTA management and accounting reports for Goibibo, MakeMyTrip and Yatra.
// Admin sees every tab; the read-only accountant login sees the accounting ones (receivables, registers,
// Tally summary, month-end statement) - the server enforces the same split. Nothing here changes data.
// Server: routers/ota.py /ota/reports/*, services/ota_reports.py. Brief: docs/build-prompts/p2-ota-yatra-and-reports.md
import React, { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { jwtDecode } from "jwt-decode";
import {
  PageShell, Card, Spinner, Chip, Stat, Tabs, GhostButton, InfoTip, inputCls, money,
} from "../../components/admin/BackofficeUI";
import { getOtaReport } from "../../api/ota";
import { exportCsv } from "../../utils/exportCsv";

const LABEL = { makemytrip: "MakeMyTrip", goibibo: "Goibibo", yatra: "Yatra", direct: "Direct", agent: "Travel agents",
  other_ota: "Other OTAs", go_mmt: "Go-MMT" };
const CHANNELS = [["all", "All"], ["makemytrip", "MakeMyTrip"], ["goibibo", "Goibibo"], ["yatra", "Yatra"]];
const chanParam = (c) => (c && c !== "all" ? c : undefined);
const thisMonth = () => new Date().toISOString().slice(0, 7);
const prevMonth = () => { const d = new Date(); d.setDate(1); d.setMonth(d.getMonth() - 1); return d.toISOString().slice(0, 7); };
const MAIL_TO = { makemytrip: "payments@go-mmt.com", goibibo: "payments@go-mmt.com", yatra: "" };

const Th = ({ children, right }) => (
  <th className={`px-3 py-3 font-semibold text-slate-300 text-xs uppercase ${right ? "text-right" : "text-left"}`}>{children}</th>
);
const Td = ({ children, right, className = "" }) => (
  <td className={`px-3 py-2 align-top ${right ? "text-right whitespace-nowrap" : ""} ${className}`}>{children}</td>
);
const Table = ({ head, children, foot }) => (
  <div className="overflow-x-auto">
    <table className="w-full text-sm">
      <thead className="bg-slate-900/40"><tr>{head}</tr></thead>
      <tbody className="divide-y divide-slate-700/60">{children}</tbody>
      {foot && <tfoot className="border-t-2 border-slate-600 font-semibold">{foot}</tfoot>}
    </table>
  </div>
);
const FilterRow = ({ children }) => (
  <div className="px-5 py-4 flex flex-wrap gap-2 items-center text-sm [&>input]:w-auto [&>select]:w-auto">{children}</div>
);
const Empty = ({ text }) => <div className="p-10 text-center text-slate-400">{text}</div>;
const ErrorBox = ({ error, onRetry }) => (
  <div className="p-6 text-center text-red-300">{error} {onRetry && <button className="underline ml-2" onClick={onRetry}>Retry</button>}</div>
);
const Loading = ({ st, reload, children }) =>
  st.loading ? <Spinner className="p-10" /> : st.error ? <ErrorBox error={st.error} onRetry={reload} /> : children;

function useLoad(fn, deps) {
  const [state, setState] = useState({ loading: true, error: "", data: null });
  const load = useCallback(async () => {
    setState((s) => ({ ...s, loading: true, error: "" }));
    try { setState({ loading: false, error: "", data: await fn() }); }
    catch (e) { setState({ loading: false, error: e.message || "Could not load", data: null }); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  useEffect(() => { load(); }, [load]);
  return [state, load];
}

function role() {
  try { return jwtDecode(localStorage.getItem("adminToken") || "").role; } catch { return null; }
}

const MonthInput = ({ value, onChange, label = "Month" }) => (
  <label className="flex items-center gap-2 text-slate-300">{label}
    <input type="month" className={inputCls} value={value} onChange={(e) => e.target.value && onChange(e.target.value)} />
  </label>
);

// ============================================================================
export default function OtaReports() {
  const admin = role() === "admin";
  const tabs = admin
    ? [["receivables", "Receivables"], ["forecast", "Cash forecast"], ["earnings", "Channel earnings"],
      ["leakage", "Leakage"], ["registers", "GST / TCS / TDS registers"], ["tally", "Tally summary"], ["statement", "Month-end statement"]]
    : [["receivables", "Receivables"], ["registers", "GST / TCS / TDS registers"], ["tally", "Tally summary"],
      ["statement", "Month-end statement"]];
  const [tab, setTab] = useState("receivables");
  const [channel, setChannel] = useState("all");
  return (
    <PageShell icon="📊" title="OTA Reports"
      subtitle="Goibibo, MakeMyTrip and Yatra: what is owed, what is coming, what each channel really earns, and the registers your CA needs.">
      <div className="flex flex-wrap gap-1 mb-4" role="group" aria-label="Channel">
        {CHANNELS.map(([k, label]) => (
          <button key={k} onClick={() => setChannel(k)}
            className={`px-3 py-1.5 rounded-full text-sm font-semibold border transition ${channel === k
              ? "bg-[#E5C07B] text-slate-900 border-[#E5C07B]" : "border-slate-600 text-slate-300 hover:bg-slate-700/60"}`}>{label}</button>
        ))}
        {admin && <Link to="/admin/ota-payments" className="ml-auto text-sm underline text-[#E5C07B] self-center">OTA Payments →</Link>}
      </div>
      <Tabs tabs={tabs} active={tab} onChange={setTab} />
      {tab === "receivables" && <Receivables channel={channel} admin={admin} />}
      {tab === "forecast" && admin && <Forecast channel={channel} />}
      {tab === "earnings" && admin && <Earnings channel={channel} />}
      {tab === "leakage" && admin && <Leakage channel={channel} />}
      {tab === "registers" && <Registers channel={channel} />}
      {tab === "tally" && <Tally channel={channel} />}
      {tab === "statement" && <Statement channel={channel} />}
    </PageShell>
  );
}

// ---------------------------------------------------------------- receivables + disputes
function disputeMail(ch, rows) {
  const lines = rows.slice(0, 40).map((r) => `${r.ota_booking_id}  ${r.guest_name || ""}  ${r.check_in} to ${r.check_out}  ` +
    (r.reason === "short_paid" ? `short paid Rs ${r.owed} (voucher ${r.expected}, paid ${r.paid})`
      : r.reason === "paid_but_cancelled" ? `paid Rs ${r.paid} for a cancelled stay` : `unpaid Rs ${r.owed}`));
  const subject = `Hotel Bhimas Tirupati - ${LABEL[ch]} bookings to settle (${rows.length})`;
  const body = `Dear ${LABEL[ch]} payments team,\n\nPlease check and settle the following bookings for Hotel Bhimas, Tirupati` +
    `${ch === "yatra" ? " (Yatra vendor ID 58270)" : " (Go-MMT code HTLDOM014889)"}:\n\n${lines.join("\n")}` +
    `${rows.length > 40 ? `\n... and ${rows.length - 40} more (list attached)` : ""}\n\nRegards,\nHotel Bhimas`;
  return `mailto:${MAIL_TO[ch] || ""}?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`;
}

function Receivables({ channel, admin }) {
  const [st, reload] = useLoad(() => getOtaReport("receivables", { channel: chanParam(channel) }), [channel]);
  const d = st.data;
  const csv = (rows, name) => exportCsv(name, rows, [
    { header: "Channel", value: (r) => LABEL[r.channel] }, { header: "OTA booking ID", value: (r) => r.ota_booking_id },
    { header: "PMS booking", value: (r) => r.booking_id }, { header: "Guest", value: (r) => r.guest_name },
    { header: "Check-in", value: (r) => r.check_in }, { header: "Check-out", value: (r) => r.check_out },
    { header: "Voucher payable", value: (r) => r.expected }, { header: "Paid", value: (r) => r.paid },
    { header: "Owed", value: (r) => r.owed }, { header: "Due from", value: (r) => r.due_from },
    { header: "Age (days)", value: (r) => r.age_days }, { header: "Reason", value: (r) => r.reason },
  ]);
  const disputesBy = useMemo(() => {
    const out = {};
    (d?.disputes || []).forEach((r) => { (out[r.channel] = out[r.channel] || []).push(r); });
    return out;
  }, [d]);
  return (
    <Loading st={st} reload={reload}>
      {d && (
        <div className="space-y-6">
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <Stat label="Owed by the OTAs (stays begun)" value={money(d.total)} tone="text-amber-300" />
            {d.by_channel.map((c) => <Stat key={c.channel} label={`${c.label} · ${c.count} booking(s)`} value={money(c.total)} />)}
          </div>
          <Card title={<>Ageing <InfoTip text="Days since the payment fell due: check-out for Go-MMT, check-in for Yatra. A short payment counts as owed for the difference." /></>}
            right={<GhostButton onClick={() => csv(d.rows, `ota-receivables-${d.today}.csv`)} disabled={!d.rows.length}>CSV</GhostButton>}>
            {!d.by_channel.length ? <Empty text="Nothing owed - every stay that has begun is paid." /> : (
              <Table head={<><Th>Channel</Th><Th right>Not yet due</Th>{d.buckets.map((b) => <Th key={b} right>{b} days</Th>)}<Th right>Total</Th></>}
                foot={<tr><Td>Total</Td><Td right>{money(d.by_channel.reduce((s, c) => s + c.not_due, 0))}</Td>
                  {d.buckets.map((b) => <Td key={b} right>{money(d.by_channel.reduce((s, c) => s + c[b], 0))}</Td>)}<Td right>{money(d.total)}</Td></tr>}>
                {d.by_channel.map((c) => (
                  <tr key={c.channel}><Td>{c.label}</Td><Td right>{money(c.not_due)}</Td>
                    {d.buckets.map((b) => <Td key={b} right className={b === "30+" && c[b] ? "text-red-300" : ""}>{money(c[b])}</Td>)}
                    <Td right>{money(c.total)}</Td></tr>
                ))}
              </Table>
            )}
          </Card>
          <Card title="Disputes to raise"
            right={<GhostButton onClick={() => csv(d.disputes, `ota-disputes-${d.today}.csv`)} disabled={!d.disputes.length}>CSV</GhostButton>}>
            {!d.disputes.length ? <Empty text="No overdue, short-paid or paid-but-cancelled bookings." /> : (
              <>
                <FilterRow>
                  {Object.entries(disputesBy).map(([ch, rows]) => (
                    <a key={ch} href={disputeMail(ch, rows)}
                      className="px-3 py-1.5 rounded-lg border border-[#E5C07B]/60 text-[#E5C07B] hover:bg-[#E5C07B]/10 text-sm font-semibold">
                      E-mail {LABEL[ch]} ({rows.length})
                    </a>
                  ))}
                  <span className="text-xs text-slate-400">Opens a ready-written mail in your mail app{admin ? "" : ""}. Yatra: add your account manager.</span>
                </FilterRow>
                <Table head={<><Th>Booking</Th><Th>Stay</Th><Th right>Voucher</Th><Th right>Paid</Th><Th right>Owed</Th><Th>Why</Th></>}>
                  {d.disputes.map((r) => (
                    <tr key={`${r.booking_id}-${r.reason}`}>
                      <Td><span className="font-mono text-xs">{r.ota_booking_id}</span><div className="text-xs text-slate-500">{LABEL[r.channel]} · #{r.booking_id} · {r.guest_name || "—"}</div></Td>
                      <Td className="whitespace-nowrap text-xs">{r.check_in} → {r.check_out}</Td>
                      <Td right>{money(r.expected)}</Td><Td right>{r.paid ? money(r.paid) : "—"}</Td><Td right>{money(r.owed)}</Td>
                      <Td>{r.reason === "short_paid" ? <Chip tone="warn">Short paid</Chip> : r.reason === "paid_but_cancelled"
                        ? <Chip tone="danger">Paid but cancelled</Chip> : <Chip tone="danger">Overdue {r.age_days}d</Chip>}</Td>
                    </tr>
                  ))}
                </Table>
              </>
            )}
          </Card>
        </div>
      )}
    </Loading>
  );
}

// ---------------------------------------------------------------- cash forecast
function Forecast({ channel }) {
  const [days, setDays] = useState(7);
  const [st, reload] = useLoad(() => getOtaReport("forecast", { channel: chanParam(channel), days }), [channel, days]);
  const d = st.data;
  const chans = d ? Object.keys(d.lag) : [];
  return (
    <Card title={<>OTA money expected <InfoTip text="Every unpaid booking, expected on check-in + that OTA's usual lag - learned from the last 180 days of payments once there are 5 or more, otherwise Go-MMT +1 day and Yatra -1 day." /></>}>
      <FilterRow>
        <select className={inputCls} value={days} onChange={(e) => setDays(Number(e.target.value))}>
          {[7, 14, 30].map((n) => <option key={n} value={n}>Next {n} days</option>)}
        </select>
        {d && <span className="text-xs text-slate-400">Timing used: {chans.map((c) => `${LABEL[c]} ${d.lag[c].days >= 0 ? "+" : ""}${d.lag[c].days} day(s) from check-in${d.lag[c].learned ? ` (learned from ${d.lag[c].samples})` : " (default)"}`).join(" · ")}</span>}
      </FilterRow>
      <Loading st={st} reload={reload}>
        {d && (
          <>
            <div className="px-5 grid grid-cols-2 md:grid-cols-3 gap-3 mb-4">
              <Stat label={`Expected in the next ${d.days} days`} value={money(d.total)} tone="text-emerald-300" />
              <Stat label="Already late (should have come)" value={money(d.late_total)} tone={d.late_total ? "text-red-300" : ""} />
            </div>
            <Table head={<><Th>Day</Th>{chans.map((c) => <Th key={c} right>{LABEL[c]}</Th>)}<Th right>Total</Th></>}
              foot={<tr><Td>Total</Td>{chans.map((c) => <Td key={c} right>{money(d.by_day.reduce((s, x) => s + (x.by_channel[c] || 0), 0))}</Td>)}<Td right>{money(d.total)}</Td></tr>}>
              {d.by_day.map((x) => (
                <tr key={x.date}><Td className="whitespace-nowrap">{new Date(x.date).toLocaleDateString("en-IN", { weekday: "short", day: "numeric", month: "short" })}</Td>
                  {chans.map((c) => <Td key={c} right>{x.by_channel[c] ? money(x.by_channel[c]) : "—"}</Td>)}<Td right>{x.total ? money(x.total) : "—"}</Td></tr>
              ))}
            </Table>
          </>
        )}
      </Loading>
    </Card>
  );
}

// ---------------------------------------------------------------- channel earnings
function Earnings({ channel }) {
  const [from, setFrom] = useState(() => { const d = new Date(); d.setDate(1); d.setMonth(d.getMonth() - 5); return d.toISOString().slice(0, 7); });
  const [to, setTo] = useState(thisMonth());
  const [st, reload] = useLoad(() => getOtaReport("earnings", { from, to, channel: chanParam(channel) }), [from, to, channel]);
  const rows = st.data?.rows || [];
  const csv = () => exportCsv(`channel-earnings-${from}-${to}.csv`, rows, [
    { header: "Month", value: (r) => r.label }, { header: "Channel", value: (r) => r.channel_label },
    { header: "Stays", value: (r) => r.bookings }, { header: "Room-nights", value: (r) => r.room_nights },
    { header: "Gross", value: (r) => r.gross }, { header: "Commission incl GST", value: (r) => r.commission },
    { header: "Commission %", value: (r) => r.commission_pct }, { header: "TCS", value: (r) => r.tcs },
    { header: "TDS", value: (r) => r.tds }, { header: "Net kept", value: (r) => r.net },
    { header: "Net per room-night", value: (r) => r.net_per_room_night }, { header: "Stays without voucher figures", value: (r) => r.voucher_missing },
  ]);
  return (
    <Card title={<>What each channel really earns <InfoTip text="By check-in month. Gross includes taxes (OTA: the voucher's gross; direct: the bill). Commission % and the net are over the stays whose commission is known; stays with no voucher figure are counted but kept out of the %." /></>}
      right={<GhostButton onClick={csv} disabled={!rows.length}>CSV</GhostButton>}>
      <FilterRow><MonthInput label="From" value={from} onChange={setFrom} /><MonthInput label="To" value={to} onChange={setTo} /></FilterRow>
      <Loading st={st} reload={reload}>
        {!rows.length ? <Empty text="No stays in these months." /> : (
          <Table head={<><Th>Month</Th><Th>Channel</Th><Th right>Stays</Th><Th right>Room-nights</Th><Th right>Gross</Th><Th right>Commission</Th><Th right>Comm. %</Th><Th right>TCS + TDS</Th><Th right>Net kept</Th><Th right>Net / room-night</Th></>}>
            {rows.map((r) => (
              <tr key={`${r.month}-${r.channel}`}>
                <Td className="whitespace-nowrap">{r.label}</Td><Td>{r.channel_label}{r.voucher_missing > 0 && <div className="text-xs text-slate-500">{r.voucher_missing} without voucher figures</div>}</Td>
                <Td right>{r.bookings}</Td><Td right>{r.room_nights}</Td><Td right>{money(r.gross)}</Td><Td right>{money(r.commission)}</Td>
                <Td right>{r.commission_pct == null ? "—" : `${r.commission_pct}%`}</Td><Td right>{money(r.tcs + r.tds)}</Td>
                <Td right>{money(r.net)}</Td><Td right className="font-semibold">{r.net_per_room_night == null ? "—" : money(r.net_per_room_night)}</Td>
              </tr>
            ))}
          </Table>
        )}
      </Loading>
    </Card>
  );
}

// ---------------------------------------------------------------- leakage
const LEAK = [
  ["paid_not_in_pms", "Paid by the OTA, not in the PMS", "A stay nobody entered (unrecorded revenue) or a booking ID typed wrong. Link or explain each on OTA Payments → Exceptions.", "danger"],
  ["never_paid", "Never paid (overdue)", "The OTA has not paid past the overdue date. Raise it from Receivables → Disputes.", "danger"],
  ["paid_cancelled", "Paid for a cancelled stay", "Retention the OTA passed on, or a booking cancelled in the PMS by mistake.", "warn"],
  ["mispaid", "Paid the wrong amount", "Paid minus the voucher's payable, beyond Rs 1.", "warn"],
];
function Leakage({ channel }) {
  const [st, reload] = useLoad(() => getOtaReport("leakage", { channel: chanParam(channel) }), [channel]);
  const d = st.data;
  return (
    <Loading st={st} reload={reload}>
      {d && (
        <div className="space-y-6">
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            {LEAK.map(([k, label]) => <Stat key={k} label={`${label} (${d[k].count})`} value={money(d[k].amount)} tone={d[k].count ? "text-amber-300" : ""} />)}
          </div>
          {LEAK.map(([k, label, help]) => (
            <Card key={k} title={<>{label} <InfoTip text={help} /></>}
              right={<GhostButton disabled={!d[k].rows.length} onClick={() => exportCsv(`ota-leakage-${k}.csv`, d[k].rows, [
                { header: "Channel", value: (r) => LABEL[r.channel] }, { header: "OTA booking ID", value: (r) => r.ota_booking_id },
                { header: "PMS booking", value: (r) => r.booking_id }, { header: "Guest", value: (r) => r.guest_name },
                { header: "Check-in", value: (r) => r.check_in }, { header: "Amount", value: (r) => r.amount },
                { header: "UTR", value: (r) => r.utr || (r.utrs || []).join(" ") }])}>CSV</GhostButton>}>
              {!d[k].rows.length ? <Empty text="None." /> : (
                <Table head={<><Th>Booking</Th><Th>Check-in</Th><Th right>Amount</Th><Th>UTR</Th></>}>
                  {d[k].rows.slice(0, 200).map((r, i) => (
                    <tr key={`${k}-${i}`}><Td><span className="font-mono text-xs">{r.ota_booking_id}</span><div className="text-xs text-slate-500">{LABEL[r.channel]}{r.booking_id ? ` · #${r.booking_id}` : ""} · {r.guest_name || "—"}</div></Td>
                      <Td className="text-xs">{r.check_in || "—"}</Td><Td right>{money(r.amount)}</Td><Td className="font-mono text-xs">{r.utr || (r.utrs || []).join(", ") || "—"}</Td></tr>
                  ))}
                </Table>
              )}
            </Card>
          ))}
        </div>
      )}
    </Loading>
  );
}

// ---------------------------------------------------------------- registers
function Registers({ channel }) {
  const [month, setMonth] = useState(prevMonth());
  const [kind, setKind] = useState("gst");
  const [st, reload] = useLoad(() => getOtaReport("registers", { month, kind, channel: chanParam(channel) }), [month, kind, channel]);
  const d = st.data;
  const rows = d?.rows || [];
  const csv = () => kind === "gst"
    ? exportCsv(`ota-gst-input-credit-${month}.csv`, rows, [
      { header: "Supplier", value: (r) => r.supplier }, { header: "Supplier GSTIN", value: (r) => r.supplier_gstin },
      { header: "Document", value: (r) => r.doc_type }, { header: "Number", value: (r) => r.doc_no },
      { header: "Reverses invoice", value: (r) => r.ref_doc_no }, { header: "IRN", value: (r) => r.irn },
      { header: "Bookings", value: (r) => r.bookings }, { header: "Taxable value", value: (r) => r.taxable },
      { header: "GST", value: (r) => r.gst }, { header: "Total", value: (r) => r.total }])
    : exportCsv(`ota-${kind}-register-${month}.csv`, rows, [
      { header: "Channel", value: (r) => LABEL[r.channel] }, { header: "Deductor", value: (r) => r.deductor },
      { header: "Deductor PAN", value: (r) => r.deductor_pan }, { header: "OTA booking ID", value: (r) => r.ota_booking_id },
      { header: "Guest", value: (r) => r.guest_name }, { header: "Check-in", value: (r) => r.check_in },
      { header: "Base", value: (r) => r.base }, { header: kind.toUpperCase(), value: (r) => r.amount }, { header: "Source", value: (r) => r.source }]);
  const help = { gst: "GST charged by the OTAs on their commission - claimable input credit. Go-MMT: one invoice a month; Yatra: one per booking, credit notes for cancellations. Match against GSTR-2B.",
    tcs: "TCS (GST, 0.5%) the OTAs collected - claim it from the GST portal's TCS credit (GSTR-2B). Go-MMT from its monthly statement, Yatra from each voucher (by check-in month).",
    tds: "TDS (194-O, 0.1%) the OTAs deducted - it appears in Form 26AS / AIS under each deductor's PAN. Go-MMT from its monthly statement, Yatra from each voucher." }[kind];
  return (
    <Card title={<>Registers for the CA <InfoTip text={help} /></>} right={<GhostButton onClick={csv} disabled={!rows.length}>CSV</GhostButton>}>
      <FilterRow>
        <MonthInput value={month} onChange={setMonth} />
        {[["gst", "GST input credit"], ["tcs", "TCS"], ["tds", "TDS"]].map(([k, l]) => (
          <button key={k} onClick={() => setKind(k)} className={`px-3 py-1.5 rounded-lg text-sm font-semibold border ${kind === k
            ? "bg-[#E5C07B] text-slate-900 border-[#E5C07B]" : "border-slate-600 text-slate-300 hover:bg-slate-700/60"}`}>{l}</button>
        ))}
      </FilterRow>
      <Loading st={st} reload={reload}>
        {!rows.length ? <Empty text="Nothing for this month yet." /> : kind === "gst" ? (
          <Table head={<><Th>Supplier</Th><Th>Document</Th><Th>IRN</Th><Th right>Bookings</Th><Th right>Taxable</Th><Th right>GST</Th><Th right>Total</Th></>}
            foot={<tr><Td>Total</Td><Td /><Td /><Td /><Td right>{money(d.totals.taxable)}</Td><Td right>{money(d.totals.gst)}</Td><Td right>{money(d.totals.total)}</Td></tr>}>
            {rows.map((r) => (
              <tr key={r.statement_id}><Td>{r.supplier}<div className="text-xs font-mono text-slate-500">{r.supplier_gstin || "GSTIN not read"}</div></Td>
                <Td>{r.doc_type} <span className="font-mono text-xs">{r.doc_no}</span>{r.ref_doc_no && <div className="text-xs text-slate-500">reverses {r.ref_doc_no}</div>}</Td>
                <Td className="font-mono text-[10px] break-all max-w-[12rem]">{r.irn || "—"}</Td><Td right>{r.bookings}</Td>
                <Td right>{money(r.taxable)}</Td><Td right>{money(r.gst)}</Td><Td right>{money(r.total)}</Td></tr>
            ))}
          </Table>
        ) : (
          <>
            <div className="px-5 pb-3 flex flex-wrap gap-2">{Object.entries(d.by_channel).map(([c, v]) => <Chip key={c}>{LABEL[c]}: {money(v)}</Chip>)}</div>
            <Table head={<><Th>Booking</Th><Th>Deductor</Th><Th>Check-in</Th><Th right>Base</Th><Th right>{kind.toUpperCase()}</Th><Th>Source</Th></>}
              foot={<tr><Td>Total</Td><Td /><Td /><Td /><Td right>{money(d.totals.amount)}</Td><Td /></tr>}>
              {rows.map((r) => (
                <tr key={`${r.channel}-${r.ota_booking_id}`}><Td><span className="font-mono text-xs">{r.ota_booking_id}</span><div className="text-xs text-slate-500">{LABEL[r.channel]} · {r.guest_name || "—"}</div></Td>
                  <Td className="text-xs">{r.deductor}<div className="font-mono text-slate-500">{r.deductor_pan}</div></Td><Td className="text-xs">{r.check_in}</Td>
                  <Td right>{r.base == null ? "—" : money(r.base)}</Td><Td right>{money(r.amount)}</Td><Td className="text-xs text-slate-400">{r.source}</Td></tr>
              ))}
            </Table>
          </>
        )}
      </Loading>
    </Card>
  );
}

// ---------------------------------------------------------------- Tally summary
function Tally({ channel }) {
  const [month, setMonth] = useState(prevMonth());
  const [st, reload] = useLoad(() => getOtaReport("tally", { month, channel: chanParam(channel) }), [month, channel]);
  const d = st.data;
  const csv = () => exportCsv(`ota-tally-journals-${month}.csv`,
    (d?.journals || []).flatMap((j) => j.lines.map((l) => ({ ...l, journal: j.journal }))), [
      { header: "Journal", value: (r) => r.journal }, { header: "Ledger", value: (r) => r.ledger },
      { header: "Debit", value: (r) => r.debit || "" }, { header: "Credit", value: (r) => r.credit || "" }]);
  return (
    <Loading st={st} reload={reload}>
      {d && (
        <div className="space-y-6">
          <Card title={<>The month in figures <InfoTip text="Gross: stays starting this month (the OTA voucher). Commission and its GST: the OTA's invoices dated this month (Go-MMT's monthly invoice is split by brand). TCS / TDS: this month's registers. Received: payments by payment date, including ones not yet matched to a booking." /></>}>
            <FilterRow><MonthInput value={month} onChange={setMonth} /></FilterRow>
            <Table head={<><Th>Channel</Th><Th right>Gross stays</Th><Th right>Commission (taxable)</Th><Th right>GST on commission</Th><Th right>TCS</Th><Th right>TDS</Th><Th right>Received in bank</Th></>}
              foot={<tr><Td>Total</Td>{["gross_stays", "commission_taxable", "commission_gst", "tcs", "tds", "received"].map((k) => <Td key={k} right>{money(d.totals[k])}</Td>)}</tr>}>
              {d.rows.map((r) => (
                <tr key={r.channel}><Td>{r.label}</Td>{["gross_stays", "commission_taxable", "commission_gst", "tcs", "tds", "received"].map((k) => <Td key={k} right>{money(r[k])}</Td>)}</tr>
              ))}
            </Table>
          </Card>
          <Card title={<>Suggested journal entries <InfoTip text="Assumes each stay's sale is already in the books with the OTA party as debtor for the gross (your own GST invoice). J1 books the OTA's deductions, J2 the money received. Check with your CA before posting." /></>}
            right={<GhostButton onClick={csv} disabled={!d.journals.length}>CSV</GhostButton>}>
            {!d.journals.length ? <Empty text="Nothing to post for this month." /> : (
              <div className="p-5 grid md:grid-cols-2 gap-4">
                {d.journals.map((j) => (
                  <div key={j.journal} className="bg-slate-900/40 border border-slate-700 rounded-xl p-3">
                    <div className="font-semibold text-[#E5C07B] mb-2 text-sm">{j.journal}</div>
                    <table className="w-full text-sm"><tbody>
                      {j.lines.map((l) => (
                        <tr key={l.ledger}><td className={`py-0.5 ${l.credit ? "pl-6 text-slate-300" : ""}`}>{l.credit ? "To " : "Dr "}{l.ledger}</td>
                          <td className="text-right whitespace-nowrap">{l.debit ? money(l.debit) : ""}</td><td className="text-right whitespace-nowrap pl-3">{l.credit ? money(l.credit) : ""}</td></tr>
                      ))}
                    </tbody></table>
                  </div>
                ))}
              </div>
            )}
          </Card>
        </div>
      )}
    </Loading>
  );
}

// ---------------------------------------------------------------- month-end statement
function Statement({ channel }) {
  const [month, setMonth] = useState(prevMonth());
  const [st, reload] = useLoad(() => getOtaReport("statement", { month, channel: chanParam(channel) }), [month, channel]);
  const d = st.data;
  const cols = [["opening", "Opening owed"], ["stays", "+ Stays this month"], ["received", "− Received"], ["closing", "= Closing owed"]];
  const csv = () => exportCsv(`ota-statement-${month}.csv`, d?.rows || [], [
    { header: "Channel", value: (r) => r.label }, ...cols.map(([k, l]) => ({ header: l, value: (r) => r[k] })),
    { header: "Receipts for bookings not in the PMS", value: (r) => r.unallocated_receipts }]);
  return (
    <Card title={<>Month-end statement per OTA <InfoTip text="Stays count when they begin (the voucher's payable). Received = payments matched to a booking, by payment date. Receipts for bookings the PMS does not have are shown apart until someone links them on OTA Payments → Exceptions." /></>}
      right={<GhostButton onClick={csv} disabled={!d?.rows?.length}>CSV</GhostButton>}>
      <FilterRow><MonthInput value={month} onChange={setMonth} />{d && <span className="text-xs text-slate-400">Tracked from {d.tracking_from}</span>}</FilterRow>
      <Loading st={st} reload={reload}>
        {d && (
          <Table head={<><Th>OTA</Th>{cols.map(([k, l]) => <Th key={k} right>{l}</Th>)}<Th right>Not yet linked</Th></>}
            foot={<tr><Td>Total</Td>{cols.map(([k]) => <Td key={k} right>{money(d.totals[k])}</Td>)}<Td right>{money(d.totals.unallocated_receipts)}</Td></tr>}>
            {d.rows.map((r) => (
              <tr key={r.channel}><Td>{r.label}</Td>{cols.map(([k]) => <Td key={k} right className={k === "closing" ? "font-semibold" : ""}>{money(r[k])}</Td>)}
                <Td right className={r.unallocated_receipts ? "text-amber-300" : ""}>{money(r.unallocated_receipts)}</Td></tr>
            ))}
          </Table>
        )}
      </Loading>
    </Card>
  );
}

// ---------------------------------------------------------------- owner dashboard tiles
/** The OTA row on the owner dashboard. Admin only on the server; renders nothing for anyone else. */
export function OtaDashboardTiles() {
  const [d, setD] = useState(null);
  useEffect(() => {
    let live = true;
    getOtaReport("tiles").then((x) => live && setD(x)).catch(() => {});
    return () => { live = false; };
  }, []);
  if (!d) return null;
  const tile = (label, value, sub, tone, to) => (
    <Link to={to} className="bg-gradient-to-br from-slate-800/80 to-slate-900/80 border border-slate-700 hover:border-[#E5C07B]/60 rounded-2xl p-4 transition block">
      <div className="text-slate-400 text-xs mb-1">{label}</div>
      <div className={`text-xl font-bold ${tone}`}>{value}</div>
      {sub && <div className="text-xs text-slate-500 mt-1">{sub}</div>}
    </Link>
  );
  return (
    <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-8">
      {tile("OTA money owed", money(d.receivable), `${d.receivable_count} stay(s) begun, unpaid`, "text-amber-300", "/admin/ota-reports")}
      {tile("OTA payments overdue", money(d.overdue), `${d.overdue_count} booking(s)`, d.overdue_count ? "text-red-300" : "text-white", "/admin/ota-payments")}
      {tile("OTA commission this month", d.commission_pct_this_month == null ? "—" : `${d.commission_pct_this_month}%`, "incl. GST, of OTA gross", "text-white", "/admin/ota-reports")}
      {tile("OTA gross this month", money(d.ota_gross_this_month), "stays checking in this month", "text-white", "/admin/ota-reports")}
    </div>
  );
}
