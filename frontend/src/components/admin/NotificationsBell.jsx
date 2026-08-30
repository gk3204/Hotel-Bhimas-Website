import React, { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { FaBell, FaEnvelopeOpenText, FaShieldAlt, FaBullhorn, FaBoxOpen, FaStar } from "react-icons/fa";
import { getAllEnquiries } from "../../api/enquiry";
import { getAlerts } from "../../api/fraud";
import { listComplaints } from "../../api/complaints";
import { getLowStock } from "../../api/stock";
import { listReviews } from "../../api/reviews";
import usePoll from "../../utils/usePoll";

// Count helper — tolerant of the different response shapes across the APIs.
const len = (x) =>
  Array.isArray(x) ? x.length : (x?.data?.length ?? x?.items?.length ?? x?.count ?? 0);

// Each actionable source: how to count it, and where clicking takes you.
const SOURCES = [
  {
    key: "enquiries", label: "New enquiries", icon: FaEnvelopeOpenText, to: "/admin/enquiries",
    load: async () => (await getAllEnquiries()).filter((e) => (e.status || "pending") === "pending").length,
  },
  {
    key: "fraud", label: "Open fraud alerts", icon: FaShieldAlt, to: "/admin/fraud",
    load: async () => len(await getAlerts({ status: "open" })),
  },
  {
    key: "complaints", label: "Overdue complaints", icon: FaBullhorn, to: "/admin/complaints",
    load: async () => len(await listComplaints({ breached: true })),
  },
  {
    key: "lowstock", label: "Low-stock items", icon: FaBoxOpen, to: "/admin/inventory",
    load: async () => len(await getLowStock()),
  },
  {
    key: "reviews", label: "Reviews to approve", icon: FaStar, to: "/admin/reviews",
    load: async () => len(await listReviews({ pending_approval: true })),
  },
];

const POLL_MS = 90000;

export default function NotificationsBell() {
  const [counts, setCounts] = useState({});
  const [open, setOpen] = useState(false);
  const navigate = useNavigate();
  const ref = useRef(null);

  const load = useCallback(async () => {
    const results = await Promise.allSettled(SOURCES.map((s) => s.load()));
    const next = {};
    results.forEach((r, i) => {
      next[SOURCES[i].key] = r.status === "fulfilled" ? (Number(r.value) || 0) : 0;
    });
    setCounts(next);
  }, []);

  useEffect(() => { load(); }, [load]);
  // Shared poller (v3 item 5): skips hidden tabs, refreshes the moment one is focused again.
  usePoll(load, POLL_MS);

  // Close on outside click.
  useEffect(() => {
    if (!open) return undefined;
    const onDown = (e) => {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  const items = SOURCES.filter((s) => (counts[s.key] || 0) > 0);
  const total = items.reduce((n, s) => n + counts[s.key], 0);

  const go = (to) => {
    setOpen(false);
    navigate(to);
  };

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen((o) => !o)}
        className="relative p-2 rounded-lg text-slate-300 hover:text-white hover:bg-slate-800/70 transition"
        aria-label={`Notifications${total ? ` (${total})` : ""}`}
        title="Notifications"
      >
        <FaBell size={16} />
        {total > 0 && (
          <span className="absolute -top-0.5 -right-0.5 min-w-[16px] h-4 px-1 rounded-full bg-red-500 text-white text-[10px] font-bold flex items-center justify-center">
            {total > 99 ? "99+" : total}
          </span>
        )}
      </button>

      {open && (
        <div className="absolute right-0 mt-2 w-64 bg-slate-800 border border-slate-700 rounded-xl shadow-2xl z-50 overflow-hidden">
          <div className="px-4 py-2.5 border-b border-slate-700 text-sm font-semibold text-[#E5C07B]">
            Needs attention
          </div>
          {items.length === 0 ? (
            <div className="px-4 py-6 text-center text-slate-400 text-sm">
              <div className="text-2xl mb-1">✅</div>
              You're all caught up.
            </div>
          ) : (
            <ul className="py-1">
              {items.map((s) => {
                const Icon = s.icon;
                return (
                  <li key={s.key}>
                    <button
                      onClick={() => go(s.to)}
                      className="w-full flex items-center gap-3 px-4 py-2.5 text-sm text-slate-200 hover:bg-slate-700/60 transition text-left"
                    >
                      <Icon className="text-slate-400 shrink-0" size={14} />
                      <span className="flex-1 truncate">{s.label}</span>
                      <span className="min-w-[20px] h-5 px-1.5 rounded-full bg-[#E5C07B]/20 text-[#FCD34D] text-xs font-bold flex items-center justify-center">
                        {counts[s.key]}
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
