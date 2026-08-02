import React, { useEffect, useState } from "react";
import { PageShell, InfoTip } from "../../components/admin/BackofficeUI";
import {
  getAlerts, reviewAlert, runReconcile, getReconciliationReport,
  getDigest, getOtps, getConfig, resendOtp,
} from "../../api/fraud";
import { FaSyncAlt, FaCheck, FaTimes, FaShieldAlt, FaKey, FaClipboardList, FaChartLine } from "react-icons/fa";

const fmt = (n) => `₹${Number(n || 0).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const pretty = (t) => String(t || "").replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());

const ALERT_TYPES = [
  "card_without_booking", "card_without_payment", "cleaning_too_long",
  "off_hours_issuance", "off_station_issuance", "same_id_two_rooms",
  "repeated_refunds", "max_cards_exceeded", "lost_reissue_mismatch",
  "card_issue_without_approval",
];

const sevChip = (s) =>
  s === "high" ? "bg-red-500/20 text-red-300 border-red-500/30"
    : s === "med" ? "bg-yellow-500/20 text-yellow-300 border-yellow-500/30"
      : "bg-slate-500/20 text-slate-300 border-slate-500/30";

const statusChip = (s) =>
  s === "open" ? "bg-yellow-500/20 text-yellow-300 border-yellow-500/30"
    : s === "reviewed" ? "bg-blue-500/20 text-blue-300 border-blue-500/30"
      : "bg-slate-500/20 text-slate-300 border-slate-500/30";

const FraudDashboard = () => {
  const [tab, setTab] = useState("alerts");
  const [loading, setLoading] = useState(false);
  const [toast, setToast] = useState(null);

  const [alerts, setAlerts] = useState([]);
  const [summary, setSummary] = useState(null);
  const [filters, setFilters] = useState({ status: "open", type: "", severity: "" });
  const [config, setConfig] = useState(null);

  const [reviewFor, setReviewFor] = useState(null);
  const [reviewForm, setReviewForm] = useState({ status: "reviewed", note: "" });

  const [otps, setOtps] = useState([]);
  const [otpStatus, setOtpStatus] = useState("pending");
  const [report, setReport] = useState(null);
  const [digestDay, setDigestDay] = useState("");
  const [digest, setDigest] = useState(null);

  const showToast = (message, type = "success") => {
    setToast({ message, type });
    setTimeout(() => setToast(null), 3500);
  };

  useEffect(() => { loadAlerts(true); getConfig().then(setConfig).catch(() => {}); }, []); // eslint-disable-line

  const loadAlerts = async (reconcile = false) => {
    setLoading(true);
    try {
      const data = await getAlerts({
        status: filters.status || undefined,
        type: filters.type || undefined,
        severity: filters.severity || undefined,
        reconcile,
      });
      setAlerts(data.data);
      setSummary(data.summary);
    } catch (e) { showToast(e.message, "error"); }
    setLoading(false);
  };

  const doReconcile = async () => {
    setLoading(true);
    try {
      const r = await runReconcile();
      showToast(`Sweep complete — ${r.new_alerts} new alert(s)`);
      await loadAlerts(false);
    } catch (e) { showToast(e.message, "error"); }
    setLoading(false);
  };

  const submitReview = async () => {
    try {
      await reviewAlert(reviewFor.id, { status: reviewForm.status, note: reviewForm.note || null });
      setReviewFor(null);
      showToast("Alert updated");
      loadAlerts(false);
    } catch (e) { showToast(e.message, "error"); }
  };

  // Approvals inbox — the status was hard-pinned to "pending" with no way to
  // reload without leaving the tab (backlog v2 F-C).
  const loadOtps = async (status = otpStatus) => {
    setOtpStatus(status);
    try { setOtps((await getOtps(status)).data); }
    catch (e) { showToast(e.message, "error"); }
  };

  const copyCode = async (code) => {
    try {
      await navigator.clipboard.writeText(String(code));
      showToast("Approval code copied");
    } catch { showToast("Could not copy — read the code out instead", "error"); }
  };

  const doResendOtp = async (otpId) => {
    try {
      const r = await resendOtp(otpId);
      showToast(r?.delivery?.detail || "Resent", r?.delivery?.ok ? "success" : "error");
    } catch (e) { showToast(e.message, "error"); }
  };

  const openTab = async (t) => {
    setTab(t);
    try {
      if (t === "approvals") await loadOtps(otpStatus);
      if (t === "report") setReport(await getReconciliationReport());
      if (t === "digest") setDigest(await getDigest(digestDay || undefined));
    } catch (e) { showToast(e.message, "error"); }
  };

  const loadDigest = async () => {
    try { setDigest(await getDigest(digestDay || undefined)); }
    catch (e) { showToast(e.message, "error"); }
  };

  return (
    <PageShell
      icon="🛡️"
      title="Anti-Fraud & Controls"
      subtitle="Detection alerts, owner approvals, and card reconciliation. Run a sweep to re-check against the latest data."
    >

        {/* Stat tiles */}
        {summary && (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
            <Stat label="Open Alerts" value={summary.open} highlight={summary.open > 0}
              tip="Detected control breaches not yet reviewed — e.g. a card cut without a matching payment." />
            <Stat label="High Severity" value={summary.high_severity} />
            <Stat label="Cleaning Too Long" value={summary.cleaning_too_long}
              tip="Rooms stuck in 'cleaning' beyond the configured limit — a sign a room may be used off-book." />
            <Stat label="Pending Approvals" value={summary.pending_otp}
              tip="Sensitive actions (voids, refunds, discounts) awaiting an owner OTP approval." />
          </div>
        )}

        {/* Tabs */}
        <div className="flex flex-wrap gap-2 mb-6">
          <TabBtn active={tab === "alerts"} onClick={() => openTab("alerts")} icon={<FaShieldAlt />} label="Alerts" />
          <TabBtn active={tab === "approvals"} onClick={() => openTab("approvals")} icon={<FaKey />} label="Approvals" />
          <TabBtn active={tab === "report"} onClick={() => openTab("report")} icon={<FaClipboardList />} label="Card Reconciliation" />
          <TabBtn active={tab === "digest"} onClick={() => openTab("digest")} icon={<FaChartLine />} label="Daily Digest" />
        </div>

        {/* ALERTS TAB */}
        {tab === "alerts" && (
          <>
            <div className="bg-gradient-to-r from-slate-800/50 to-slate-700/50 border border-slate-700 p-6 rounded-2xl shadow-xl mb-6 backdrop-blur">
              <div className="grid grid-cols-1 md:grid-cols-5 gap-4 items-end">
                <Select label="Status" value={filters.status} onChange={(e) => setFilters({ ...filters, status: e.target.value })}
                  options={[["", "All"], ["open", "Open"], ["reviewed", "Reviewed"], ["dismissed", "Dismissed"]]} />
                <Select label="Type" value={filters.type} onChange={(e) => setFilters({ ...filters, type: e.target.value })}
                  options={[["", "All"], ...ALERT_TYPES.map((t) => [t, pretty(t)])]} />
                <Select label="Severity" value={filters.severity} onChange={(e) => setFilters({ ...filters, severity: e.target.value })}
                  options={[["", "All"], ["high", "High"], ["med", "Medium"], ["low", "Low"]]} />
                <button onClick={() => loadAlerts(false)} className="bg-slate-700 hover:bg-slate-600 font-semibold px-6 py-2.5 rounded-lg transition">Apply</button>
                <button onClick={doReconcile} className="bg-gradient-to-r from-[#E5C07B] to-[#D4AF37] text-slate-900 font-bold px-6 py-2.5 rounded-lg transition-all hover:scale-105 flex items-center justify-center gap-2">
                  <FaSyncAlt size={13} /> Run Reconciliation
                </button>
              </div>
              {config && (
                <p className="text-slate-500 text-xs mt-4">
                  Thresholds: cleaning &gt; {config.cleaning_max_hours}h · issue hours {config.allowed_issue_hours} ·
                  {config.allowed_stations.length ? ` stations ${config.allowed_stations.join(", ")}` : " any station"} ·
                  repeat-refund ≥ {config.repeat_refund_threshold} ·
                  refund OTP {config.refund_requires_owner_otp ? "ON" : "off"} · discount OTP {config.discount_otp_required ? "ON" : "off"}
                </p>
              )}
            </div>

            <div className="bg-gradient-to-r from-slate-800/50 to-slate-700/50 border border-slate-700 rounded-2xl shadow-xl overflow-hidden backdrop-blur">
              {loading ? (
                <div className="p-12 flex items-center justify-center">
                  <div className="animate-spin"><div className="h-12 w-12 border-4 border-[#E5C07B] border-t-[#D4AF37] rounded-full"></div></div>
                </div>
              ) : alerts.length === 0 ? (
                <div className="p-12 text-center text-slate-400"><div className="text-5xl mb-4">✅</div><p>No alerts match this filter.</p></div>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-left">
                    <thead className="bg-slate-900/80 border-b border-slate-700">
                      <tr>
                        <th className="px-6 py-4 font-semibold text-sm">Type</th>
                        <th className="px-6 py-4 font-semibold text-sm">Severity</th>
                        <th className="px-6 py-4 font-semibold text-sm">Room / Booking</th>
                        <th className="px-6 py-4 font-semibold text-sm">Detected</th>
                        <th className="px-6 py-4 font-semibold text-sm">Detail</th>
                        <th className="px-6 py-4 font-semibold text-sm">Status</th>
                        <th className="px-6 py-4 font-semibold text-sm">Actions</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-700">
                      {alerts.map((a) => (
                        <tr key={a.id} className="hover:bg-slate-700/30 transition align-top">
                          <td className="px-6 py-4 font-medium">{pretty(a.type)}</td>
                          <td className="px-6 py-4"><span className={`px-3 py-1 rounded-full text-xs font-bold border ${sevChip(a.severity)}`}>{a.severity}</span></td>
                          <td className="px-6 py-4 text-slate-300 whitespace-nowrap">
                            {a.room_number ? `Room ${a.room_number}` : "—"}{a.booking_id ? ` · #${a.booking_id}` : ""}
                          </td>
                          <td className="px-6 py-4 text-slate-400 text-sm whitespace-nowrap">{(a.detected_at || "").slice(0, 16).replace("T", " ")}</td>
                          <td className="px-6 py-4 text-slate-400 text-xs max-w-xs">{a.detail?.note || JSON.stringify(a.detail)}</td>
                          <td className="px-6 py-4"><span className={`px-3 py-1 rounded-full text-xs font-bold border ${statusChip(a.status)}`}>{a.status}</span></td>
                          <td className="px-6 py-4 whitespace-nowrap">
                            {a.status === "open" ? (
                              <button onClick={() => { setReviewFor(a); setReviewForm({ status: "reviewed", note: "" }); }}
                                className="bg-blue-600 hover:bg-blue-700 px-3 py-2 rounded-lg text-white text-sm">Review</button>
                            ) : (
                              <span className="text-slate-500 text-xs">{a.review_note || "—"}</span>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </>
        )}

        {/* APPROVALS TAB */}
        {tab === "approvals" && (
          <div className="bg-gradient-to-r from-slate-800/50 to-slate-700/50 border border-slate-700 rounded-2xl shadow-xl overflow-hidden backdrop-blur">
            <div className="p-6 border-b border-slate-700">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <h2 className="text-2xl font-bold">🔐 Owner Approvals</h2>
                <div className="flex items-center gap-2">
                  <select
                    className={inputCls + " w-auto"}
                    value={otpStatus}
                    onChange={(e) => loadOtps(e.target.value)}
                  >
                    <option value="pending">Pending</option>
                    <option value="used">Used</option>
                    <option value="expired">Expired</option>
                    <option value="all">All</option>
                  </select>
                  <button
                    onClick={() => loadOtps(otpStatus)}
                    className="px-4 py-2 rounded-lg bg-slate-700 hover:bg-slate-600 transition flex items-center gap-2 text-sm"
                  >
                    <FaSyncAlt /> Refresh
                  </button>
                </div>
              </div>
              <p className="text-slate-400 text-sm mt-2">
                Read the code to the receptionist to approve a sensitive action. Codes expire; each is single-use.
                When a WhatsApp provider and owner number are configured the code is also sent to the owner
                automatically — use Resend if it did not arrive.
              </p>
            </div>
            {otps.length === 0 ? (
              <div className="p-12 text-center text-slate-400"><div className="text-5xl mb-4">📭</div><p>No {otpStatus === "all" ? "" : otpStatus} approval requests.</p></div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-left">
                  <thead className="bg-slate-900/80 border-b border-slate-700">
                    <tr>
                      <th className="px-6 py-4 font-semibold text-sm">Action</th>
                      <th className="px-6 py-4 font-semibold text-sm">Context</th>
                      <th className="px-6 py-4 font-semibold text-sm">Code</th>
                      <th className="px-6 py-4 font-semibold text-sm">Expires</th>
                      <th className="px-6 py-4 font-semibold text-sm text-right">Actions</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-700">
                    {otps.map((o) => (
                      <tr key={o.otp_id} className="hover:bg-slate-700/30 transition">
                        <td className="px-6 py-4 font-medium whitespace-nowrap">{pretty(o.action)}</td>
                        <td className="px-6 py-4 max-w-xs">
                          <div className="flex flex-wrap gap-1">
                            {Object.entries(o.context || {}).map(([k, v]) => (
                              <span key={k} className="px-2 py-0.5 rounded border border-slate-600 bg-slate-800/70 text-slate-300 text-xs">
                                {pretty(k)}: <span className="text-slate-100">{String(v)}</span>
                              </span>
                            ))}
                            {!o.context && <span className="text-slate-500 text-xs">—</span>}
                          </div>
                        </td>
                        <td className="px-6 py-4">
                          {o.code
                            ? <span className="font-mono text-2xl font-bold tracking-widest text-[#FCD34D]">{o.code}</span>
                            : <span className="text-slate-500 text-sm">{o.used ? "used" : "expired"}</span>}
                        </td>
                        <td className="px-6 py-4 text-slate-400 text-sm whitespace-nowrap">{(o.expires_at || "").slice(0, 16).replace("T", " ")}</td>
                        <td className="px-6 py-4 text-right whitespace-nowrap">
                          {o.code && (
                            <div className="inline-flex gap-2">
                              <button
                                onClick={() => copyCode(o.code)}
                                className="px-3 py-1.5 rounded-lg bg-slate-700 hover:bg-slate-600 transition text-xs"
                              >
                                Copy
                              </button>
                              <button
                                onClick={() => doResendOtp(o.otp_id)}
                                className="px-3 py-1.5 rounded-lg bg-slate-700 hover:bg-slate-600 transition text-xs"
                              >
                                Resend
                              </button>
                            </div>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}

        {/* CARD RECONCILIATION TAB */}
        {tab === "report" && report && (
          <div className="bg-gradient-to-r from-slate-800/50 to-slate-700/50 border border-slate-700 rounded-2xl shadow-xl overflow-hidden backdrop-blur">
            <div className="p-6 border-b border-slate-700 flex items-center justify-between">
              <h2 className="text-2xl font-bold">🗝️ Card vs Booking vs Payment</h2>
              <span className="text-slate-400 text-sm">{report.total} active cards · <span className="text-red-300 font-semibold">{report.unmatched} unmatched</span></span>
            </div>
            {report.data.length === 0 ? (
              <div className="p-12 text-center text-slate-400"><div className="text-5xl mb-4">📭</div><p>No active cards.</p></div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-left">
                  <thead className="bg-slate-900/80 border-b border-slate-700">
                    <tr>
                      <th className="px-6 py-4 font-semibold text-sm">Card</th>
                      <th className="px-6 py-4 font-semibold text-sm">Room</th>
                      <th className="px-6 py-4 font-semibold text-sm">Booking</th>
                      <th className="px-6 py-4 font-semibold text-sm">Checked-in</th>
                      <th className="px-6 py-4 font-semibold text-sm">On Booking</th>
                      <th className="px-6 py-4 font-semibold text-sm">Paid</th>
                      <th className="px-6 py-4 font-semibold text-sm">Result</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-700">
                    {report.data.map((r) => (
                      <tr key={r.card_id} className="hover:bg-slate-700/30 transition">
                        <td className="px-6 py-4 text-[#FCD34D]">#{r.card_id}</td>
                        <td className="px-6 py-4 text-slate-300">{r.room_number || "—"}</td>
                        <td className="px-6 py-4 text-slate-300">{r.booking_id ? `#${r.booking_id}` : "—"}</td>
                        <td className="px-6 py-4"><Yn v={r.has_checked_in_booking} /></td>
                        <td className="px-6 py-4"><Yn v={r.room_on_booking} /></td>
                        <td className="px-6 py-4"><Yn v={r.has_payment} /></td>
                        <td className="px-6 py-4">
                          <span className={`px-3 py-1 rounded-full text-xs font-bold border ${r.matched ? "bg-green-500/20 text-green-300 border-green-500/30" : "bg-red-500/20 text-red-300 border-red-500/30"}`}>
                            {r.matched ? "OK" : "Unmatched"}
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}

        {/* DIGEST TAB */}
        {tab === "digest" && (
          <div className="bg-gradient-to-r from-slate-800/50 to-slate-700/50 border border-slate-700 p-6 rounded-2xl shadow-xl backdrop-blur">
            <div className="flex items-end gap-4 mb-6">
              <Field label="Day" type="date" value={digestDay} onChange={(e) => setDigestDay(e.target.value)} />
              <button onClick={loadDigest} className="bg-slate-700 hover:bg-slate-600 font-semibold px-6 py-2.5 rounded-lg transition">Show</button>
            </div>
            {digest && (
              <>
                <p className="text-slate-400 mb-4">Owner digest for <span className="text-[#E5C07B] font-semibold">{digest.day}</span></p>
                <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                  <Stat label="Occupancy" value={`${digest.occupancy.occupancy_pct}%`} sub={`${digest.occupancy.occupied}/${digest.occupancy.total_rooms}`} />
                  <Stat label="Revenue" value={fmt(digest.revenue_today)} />
                  <Stat label="Expected Cash" value={fmt(digest.expected_cash_today)} highlight />
                  <Stat label="Open Alerts" value={digest.open_alerts} />
                  <Stat label="Arrivals" value={digest.arrivals} />
                  <Stat label="Departures" value={digest.departures} />
                  <Stat label="In Cleaning" value={digest.occupancy.cleaning} />
                  <Stat label="High-Sev Alerts" value={digest.high_severity_alerts} />
                </div>
              </>
            )}
          </div>
        )}

        {/* REVIEW MODAL */}
        {reviewFor && (
          <div className="fixed inset-0 bg-black/70 backdrop-blur-sm flex items-center justify-center z-50 p-4">
            <div className="bg-gradient-to-br from-slate-800 to-slate-900 p-8 rounded-2xl w-full max-w-md border border-slate-700 shadow-2xl">
              <h2 className="text-2xl font-bold mb-1 text-[#E5C07B]">{pretty(reviewFor.type)}</h2>
              <p className="text-slate-400 text-sm mb-6">
                {reviewFor.room_number ? `Room ${reviewFor.room_number}` : ""}{reviewFor.booking_id ? ` · Booking #${reviewFor.booking_id}` : ""}
              </p>
              <div className="rounded-lg bg-slate-900/50 border border-slate-700 p-3 mb-5 text-xs text-slate-300 break-words">
                {reviewFor.detail?.note || JSON.stringify(reviewFor.detail)}
              </div>
              <div className="space-y-4">
                <div className="flex flex-col">
                  <label className="mb-2 text-sm font-semibold text-slate-300">Decision</label>
                  <select value={reviewForm.status} onChange={(e) => setReviewForm({ ...reviewForm, status: e.target.value })} className={inputCls}>
                    <option value="reviewed">Acknowledge (reviewed)</option>
                    <option value="dismissed">Dismiss (false alarm)</option>
                  </select>
                </div>
                <Field label="Note" value={reviewForm.note} placeholder="what you found / action taken" onChange={(e) => setReviewForm({ ...reviewForm, note: e.target.value })} />
              </div>
              <div className="flex justify-end gap-4 mt-8">
                <button onClick={() => setReviewFor(null)} className="bg-slate-700 hover:bg-slate-600 px-6 py-2 rounded-lg font-medium transition flex items-center gap-2"><FaTimes size={13} /> Cancel</button>
                <button onClick={submitReview} className="bg-green-700 hover:bg-green-800 px-6 py-2 rounded-lg font-bold transition flex items-center gap-2"><FaCheck size={13} /> Save</button>
              </div>
            </div>
          </div>
        )}

        {/* TOAST */}
        {toast && (
          <div className="fixed top-6 right-6 z-50">
            <div className={`px-6 py-4 rounded-xl shadow-2xl font-medium backdrop-blur ${toast.type === "error" ? "bg-red-600/90 text-white" : "bg-green-600/90 text-white"}`}>
              {toast.type === "error" ? "❌" : "✅"} {toast.message}
            </div>
          </div>
        )}
    </PageShell>
  );
};

const inputCls =
  "px-4 py-2 bg-slate-900/50 border border-slate-600 rounded-lg text-white focus:outline-none focus:border-[#E5C07B] focus:ring-2 focus:ring-[#E5C07B]/20 transition";

const Field = ({ label, ...props }) => (
  <div className="flex flex-col">
    <label className="mb-2 text-sm font-semibold text-slate-300">{label}</label>
    <input {...props} className={inputCls} />
  </div>
);

const Select = ({ label, options, ...props }) => (
  <div className="flex flex-col">
    <label className="mb-2 text-sm font-semibold text-slate-300">{label}</label>
    <select {...props} className={inputCls}>
      {options.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
    </select>
  </div>
);

const Stat = ({ label, value, sub, highlight, tip }) => (
  <div className={`rounded-2xl border p-5 ${highlight ? "bg-[#E5C07B]/10 border-[#E5C07B]/40" : "bg-slate-800/50 border-slate-700"}`}>
    <p className="text-slate-400 text-sm mb-1">{label}{tip && <InfoTip text={tip} />}</p>
    <p className={`text-2xl font-bold ${highlight ? "text-[#FCD34D]" : "text-white"}`}>{value}</p>
    {sub && <p className="text-slate-500 text-xs mt-1">{sub}</p>}
  </div>
);

const TabBtn = ({ active, onClick, icon, label }) => (
  <button onClick={onClick}
    className={`px-5 py-2.5 rounded-lg font-semibold transition flex items-center gap-2 ${active ? "bg-gradient-to-r from-[#E5C07B] to-[#D4AF37] text-slate-900" : "bg-slate-800/60 border border-slate-700 text-slate-300 hover:bg-slate-700/60"}`}>
    {icon} {label}
  </button>
);

const Yn = ({ v }) => (
  <span className={v ? "text-green-300" : "text-red-300"}>{v ? "✓" : "✗"}</span>
);

export default FraudDashboard;
