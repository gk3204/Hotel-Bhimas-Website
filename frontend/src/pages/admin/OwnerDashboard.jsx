import React, { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { FaChartLine, FaBed, FaMoneyBillWave, FaCashRegister, FaSignInAlt,
  FaSignOutAlt, FaExclamationTriangle, FaFileAlt, FaMoon, FaStar } from "react-icons/fa";
import { getDashboard, getWeeklyTrends, runDayClose } from "../../api/reports";
import { getReviewTrends } from "../../api/reviews";
import BarList from "../../components/charts/BarList";

const fmt = (n) =>
  `₹${Number(n || 0).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const fmtInt = (n) => Number(n || 0).toLocaleString("en-IN");

export default function OwnerDashboard() {
  const [dash, setDash] = useState(null);
  const [trends, setTrends] = useState(null);
  const [reviewTrends, setReviewTrends] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [toast, setToast] = useState("");
  const [closing, setClosing] = useState(false);

  const showToast = (msg) => {
    setToast(msg);
    setTimeout(() => setToast(""), 3500);
  };

  const load = async () => {
    setLoading(true);
    setError("");
    try {
      const [d, t, rt] = await Promise.all([
        getDashboard(),
        getWeeklyTrends(),
        getReviewTrends().catch(() => null), // reviews are additive — don't break the dashboard
      ]);
      setDash(d);
      setTrends(t);
      setReviewTrends(rt);
    } catch (e) {
      setError(e.message || "Failed to load dashboard");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, []);

  const doDayClose = async () => {
    setClosing(true);
    try {
      const res = await runDayClose();
      showToast(`Day-close done for ${res.business_date} (${res.folios_posted} folio(s) posted)`);
      load();
    } catch (e) {
      showToast(e.message || "Day-close failed");
    } finally {
      setClosing(false);
    }
  };

  const occ = dash?.occupancy || {};
  const rev = (p) => (p ? p.revenue : 0);

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-900 via-slate-800 to-slate-900 text-white p-6">
      <div className="max-w-7xl mx-auto">
        {toast && (
          <div className="fixed top-6 right-6 z-50 bg-slate-800 border border-[#E5C07B]/40 text-white px-5 py-3 rounded-xl shadow-2xl">
            {toast}
          </div>
        )}

        {/* Header */}
        <div className="flex flex-wrap items-center justify-between gap-4 mb-8">
          <div>
            <h1 className="text-5xl font-bold mb-2 bg-gradient-to-r from-[#E5C07B] to-[#FCD34D] bg-clip-text text-transparent">
              🏨 Owner Dashboard
            </h1>
            <p className="text-slate-400">
              Live snapshot for {dash?.date || "today"} — desk + website activity
            </p>
          </div>
          <div className="flex items-center gap-3">
            <button
              onClick={doDayClose}
              disabled={closing}
              className="bg-slate-700 hover:bg-slate-600 disabled:opacity-60 text-white font-semibold px-4 py-2.5 rounded-lg transition flex items-center gap-2"
            >
              <FaMoon size={14} /> {closing ? "Running…" : "Run Day-Close"}
            </button>
            <Link
              to="/admin/reports"
              className="bg-gradient-to-r from-[#E5C07B] to-[#D4AF37] text-slate-900 font-bold px-5 py-2.5 rounded-lg transition-all hover:scale-105 flex items-center gap-2"
            >
              <FaFileAlt size={14} /> Reports
            </Link>
          </div>
        </div>

        {error && (
          <div className="bg-red-500/10 border border-red-500/40 text-red-300 px-5 py-3 rounded-xl mb-6">
            {error}
          </div>
        )}

        {loading ? (
          <div className="p-16 flex items-center justify-center">
            <div className="animate-spin">
              <div className="h-12 w-12 border-4 border-[#E5C07B] border-t-[#D4AF37] rounded-full" />
            </div>
          </div>
        ) : (
          <>
            {/* Overdue alert: in-house guests past their checkout date */}
            {dash?.overdue_count > 0 && (
              <div className="bg-red-500/15 border border-red-500/40 text-red-200 px-5 py-3 rounded-xl mb-6">
                <span className="font-semibold">⚠ {dash.overdue_count} guest(s) past checkout still in-house</span>
                {dash.overdue?.length > 0 && (
                  <span className="text-red-300/80 text-sm ml-2">
                    — {dash.overdue.slice(0, 5).map((o) => `${o.guest_name || "?"} (out ${o.check_out})`).join(", ")}
                    {dash.overdue.length > 5 ? "…" : ""}
                  </span>
                )}
              </div>
            )}

            {/* KPI tiles */}
            <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4 mb-8">
              <Kpi icon={<FaBed />} label="Occupancy"
                value={`${occ.occupancy_pct ?? 0}%`}
                sub={`${occ.occupied ?? 0}/${occ.total_rooms ?? 0} rooms`} highlight />
              <Kpi icon={<FaMoneyBillWave />} label="Revenue Today" value={fmt(dash?.revenue_today)} />
              <Kpi icon={<FaCashRegister />} label="Expected Cash"
                value={fmt(dash?.expected_cash_today)} />
              <Kpi icon={<FaSignInAlt />} label="Arrivals" value={fmtInt(dash?.arrivals)} />
              <Kpi icon={<FaSignOutAlt />} label="Departures" value={fmtInt(dash?.departures)} />
              <Kpi icon={<FaExclamationTriangle />} label="Open Alerts"
                value={fmtInt(dash?.open_alerts_count)}
                sub={dash?.high_severity_alerts ? `${dash.high_severity_alerts} high` : ""}
                highlight={dash?.open_alerts_count > 0} />
            </div>

            {reviewTrends && (
              <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4 mb-8">
                <Kpi icon={<FaStar />} label="Unanswered reviews"
                  value={fmtInt(reviewTrends.unanswered_count)}
                  sub={reviewTrends.unanswered_count ? "need a reply" : "all caught up"}
                  highlight={reviewTrends.unanswered_count > 0} />
                <div className="lg:col-span-5">
                  <Card title="Weekly rating trend" icon={<FaStar />}>
                    <BarList
                      max={5}
                      formatValue={(v) => (v ? `${v}★` : "—")}
                      data={(reviewTrends.days || []).map((d) => ({
                        label: d.date.slice(5),
                        value: d.avg_rating || 0,
                        sub: d.count ? `${d.count} review${d.count > 1 ? "s" : ""}` : "no reviews",
                      }))}
                      empty="No reviews this week."
                    />
                  </Card>
                </div>
              </div>
            )}

            {/* Trend charts */}
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-8">
              <Card title="Revenue — this week vs history" icon={<FaChartLine />}>
                <BarList
                  formatValue={fmt}
                  data={[
                    { label: "This week", value: rev(trends?.this_week) },
                    { label: "vs last month", value: rev(trends?.vs_last_month) },
                    { label: "vs last year", value: rev(trends?.vs_last_year) },
                  ]}
                />
              </Card>
              <Card title="Avg occupancy — this week vs history" icon={<FaBed />}>
                <BarList
                  max={100}
                  formatValue={(v) => `${v}%`}
                  data={[
                    { label: "This week", value: trends?.this_week?.avg_occupancy_pct || 0 },
                    { label: "vs last month", value: trends?.vs_last_month?.avg_occupancy_pct || 0 },
                    { label: "vs last year", value: trends?.vs_last_year?.avg_occupancy_pct || 0 },
                  ]}
                />
              </Card>
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-8">
              <Card title="Booking source mix (this week)">
                <BarList
                  formatValue={fmtInt}
                  data={Object.entries(trends?.source_mix || {}).map(([label, value]) => ({
                    label, value,
                  }))}
                  empty="No bookings this week."
                />
              </Card>
              <Card title="Open maintenance by age">
                <BarList
                  formatValue={fmtInt}
                  data={Object.entries(trends?.open_maintenance_by_age || {}).map(([label, value]) => ({
                    label, value,
                  }))}
                  empty="No open tickets."
                />
                <div className="mt-4 grid grid-cols-3 gap-3 text-center">
                  <MiniStat label="Discounts" value={fmt(trends?.discount_total)} />
                  <MiniStat label="Voids" value={fmt(trends?.void_total)} />
                  <MiniStat label="Refunds" value={fmt(trends?.refund_total)} />
                </div>
              </Card>
            </div>

            {/* Pending arrivals + open alerts */}
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
              <Card title={`Pending arrivals today (${dash?.pending_arrivals?.length || 0})`}>
                {dash?.pending_arrivals?.length ? (
                  <ul className="divide-y divide-slate-700">
                    {dash.pending_arrivals.map((p) => (
                      <li key={p.booking_id} className="py-2.5 flex items-center justify-between">
                        <span className="text-slate-200">
                          #{p.booking_id} · {p.guest_name || "Guest"}
                        </span>
                        <span className="text-slate-400 text-sm">
                          {p.check_in_time ? p.check_in_time.slice(0, 5) : "—"} · out {p.check_out}
                        </span>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="text-slate-500 text-sm py-6 text-center">No pending arrivals.</p>
                )}
              </Card>
              <Card title={`Open alerts (${dash?.open_alerts_count || 0})`}>
                {dash?.open_alerts?.length ? (
                  <ul className="divide-y divide-slate-700">
                    {dash.open_alerts.map((a) => (
                      <li key={a.id} className="py-2.5 flex items-center justify-between">
                        <span className="text-slate-200">{a.type}</span>
                        <span className={`px-3 py-1 rounded-full text-xs font-bold border ${sevChip(a.severity)}`}>
                          {a.severity}
                        </span>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="text-slate-500 text-sm py-6 text-center">No open alerts. 🎉</p>
                )}
                <div className="mt-4">
                  <Link to="/admin/fraud" className="text-[#E5C07B] hover:text-[#FCD34D] text-sm font-semibold">
                    Open the fraud dashboard →
                  </Link>
                </div>
              </Card>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

const sevChip = (s) =>
  s === "high"
    ? "bg-red-500/20 text-red-300 border-red-500/30"
    : s === "med"
    ? "bg-yellow-500/20 text-yellow-300 border-yellow-500/30"
    : "bg-slate-500/20 text-slate-300 border-slate-500/30";

const Kpi = ({ icon, label, value, sub, highlight }) => (
  <div className={`rounded-2xl border p-4 ${highlight ? "bg-[#E5C07B]/10 border-[#E5C07B]/40" : "bg-slate-800/50 border-slate-700"}`}>
    <div className="flex items-center gap-2 text-slate-400 text-xs mb-2">
      <span className="text-[#E5C07B]">{icon}</span> {label}
    </div>
    <p className={`text-2xl font-bold ${highlight ? "text-[#FCD34D]" : "text-white"}`}>{value}</p>
    {sub ? <p className="text-slate-500 text-xs mt-1">{sub}</p> : null}
  </div>
);

const MiniStat = ({ label, value }) => (
  <div className="bg-slate-900/40 border border-slate-700 rounded-xl p-3">
    <p className="text-slate-400 text-xs mb-1">{label}</p>
    <p className="text-white font-semibold text-sm">{value}</p>
  </div>
);

const Card = ({ title, icon, children }) => (
  <div className="bg-gradient-to-r from-slate-800/50 to-slate-700/50 border border-slate-700 rounded-2xl shadow-xl p-6 backdrop-blur">
    <h2 className="text-lg font-bold mb-4 flex items-center gap-2">
      {icon ? <span className="text-[#E5C07B]">{icon}</span> : null} {title}
    </h2>
    {children}
  </div>
);
