// Shared gold-on-dark building blocks for the back-office admin screens
// (Companies / Vendors / Staff — prompt 18 slices 7/13/12/9).
//
// Same brand and spacing as pages/admin/Compliance.jsx and RoomTypes.jsx — slate-900 surfaces,
// #E5C07B gold accent, rounded-2xl cards. Extracted rather than pasted three times; nothing here
// introduces a second look, it just names the pieces those pages already use.
import React, { useEffect, useMemo, useState } from "react";

export const GOLD = "#E5C07B";

export const inputCls =
  "px-4 py-2 bg-slate-900/50 border border-slate-600 rounded-lg text-white w-full " +
  "focus:outline-none focus:border-[#E5C07B] focus:ring-2 focus:ring-[#E5C07B]/20 transition " +
  "disabled:opacity-50";

export const money = (v) =>
  `₹ ${Number(v || 0).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

export const today = () => new Date().toISOString().slice(0, 10);
export const daysAgo = (n) => new Date(Date.now() - n * 86400000).toISOString().slice(0, 10);
/** DD-MM-YYYY, the convention used everywhere in this product. */
export const fmtDate = (iso) => {
  if (!iso) return "—";
  const d = String(iso).slice(0, 10).split("-");
  return d.length === 3 ? `${d[2]}-${d[1]}-${d[0]}` : String(iso);
};

export function Field({ label, hint, children, ...props }) {
  return (
    <div className="flex flex-col">
      <label className="mb-2 text-sm font-semibold text-slate-300">{label}</label>
      {children || <input {...props} className={inputCls} />}
      {hint && <span className="mt-1 text-xs text-slate-500">{hint}</span>}
    </div>
  );
}

export function SelectField({ label, options, hint, ...props }) {
  return (
    <Field label={label} hint={hint}>
      <select {...props} className={inputCls}>
        {options.map((o) =>
          typeof o === "string" ? (
            <option key={o} value={o}>{o}</option>
          ) : (
            <option key={o.value} value={o.value}>{o.label}</option>
          )
        )}
      </select>
    </Field>
  );
}

export function PrimaryButton({ children, className = "", ...props }) {
  return (
    <button
      {...props}
      className={`bg-gradient-to-r from-[#E5C07B] to-[#D4AF37] text-slate-900 font-bold px-6 py-2.5
        rounded-lg transition-all hover:scale-105 disabled:opacity-50 disabled:hover:scale-100
        flex items-center justify-center gap-2 ${className}`}
    >
      {children}
    </button>
  );
}

export function GhostButton({ children, className = "", ...props }) {
  return (
    <button
      {...props}
      className={`bg-slate-700 hover:bg-slate-600 text-white font-semibold px-4 py-2.5 rounded-lg
        transition disabled:opacity-50 flex items-center justify-center gap-2 ${className}`}
    >
      {children}
    </button>
  );
}

export function DangerButton({ children, className = "", ...props }) {
  return (
    <button
      {...props}
      className={`bg-red-900/60 hover:bg-red-800/70 border border-red-700/60 text-red-100
        font-semibold px-4 py-2.5 rounded-lg transition disabled:opacity-50
        flex items-center justify-center gap-2 ${className}`}
    >
      {children}
    </button>
  );
}

export function Card({ title, right, children, className = "" }) {
  return (
    <div
      className={`bg-gradient-to-r from-slate-800/50 to-slate-700/50 border border-slate-700
        rounded-2xl shadow-xl backdrop-blur overflow-hidden ${className}`}
    >
      {(title || right) && (
        <div className="p-5 border-b border-slate-700 flex items-center justify-between gap-4">
          <h2 className="text-lg font-bold text-[#E5C07B]">{title}</h2>
          {right}
        </div>
      )}
      {children}
    </div>
  );
}

export function Spinner({ className = "" }) {
  return (
    <div className={`flex items-center justify-center ${className}`}>
      <div className="animate-spin">
        <div className="h-10 w-10 border-4 border-[#E5C07B] border-t-[#D4AF37] rounded-full" />
      </div>
    </div>
  );
}

const CHIP_TONES = {
  ok: "bg-emerald-900/50 border-emerald-600/50 text-emerald-200",
  warn: "bg-amber-900/50 border-amber-600/50 text-amber-200",
  danger: "bg-red-900/50 border-red-600/50 text-red-200",
  info: "bg-sky-900/50 border-sky-600/50 text-sky-200",
  gold: "bg-[#E5C07B]/15 border-[#E5C07B]/50 text-[#E5C07B]",
  neutral: "bg-slate-700/60 border-slate-600 text-slate-300",
};

export function Chip({ tone = "neutral", children }) {
  return (
    <span
      className={`inline-block px-2.5 py-1 rounded-full border text-xs font-semibold whitespace-nowrap
        ${CHIP_TONES[tone] || CHIP_TONES.neutral}`}
    >
      {children}
    </span>
  );
}

export function Stat({ label, value, tone = "" }) {
  return (
    <div className="bg-slate-900/50 border border-slate-700 rounded-xl p-4">
      <div className="text-slate-400 text-xs mb-1">{label}</div>
      <div className={`text-xl font-bold ${tone || "text-white"}`}>{value}</div>
    </div>
  );
}

/** Table with the loading / error / empty states the guidelines require (never optional). */
// Generic comparator: numbers numerically, everything else as natural strings; nulls sort last.
const cmpValues = (a, b) => {
  if (a == null && b == null) return 0;
  if (a == null) return 1;
  if (b == null) return -1;
  if (typeof a === "number" && typeof b === "number") return a - b;
  return String(a).localeCompare(String(b), undefined, { numeric: true, sensitivity: "base" });
};

// Paginates client-side (rows are already fully loaded on these screens). Defaults to 25 rows/page;
// tables shorter than that show no footer. Pass `pageSize={0}` to disable and render every row.
// A column may be a string (not sortable) or an object { label, sort } where `sort` is a row-key
// string or an accessor (row) => comparableValue; clicking a sortable header cycles asc → desc → off.
export function DataTable({ columns, rows, renderRow, loading, error, empty = "No records.", pageSize = 25, onRetry }) {
  const cols = columns.map((c) => (typeof c === "string" ? { label: c } : c));
  const [sort, setSort] = useState({ idx: null, dir: "asc" });
  const [page, setPage] = useState(0);

  const sorted = useMemo(() => {
    const base = rows || [];
    const col = sort.idx == null ? null : cols[sort.idx];
    if (!col?.sort) return base;
    const acc = typeof col.sort === "function" ? col.sort : (r) => r[col.sort];
    const factor = sort.dir === "asc" ? 1 : -1;
    return [...base].sort((a, b) => cmpValues(acc(a), acc(b)) * factor);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rows, sort]);

  const total = sorted.length;
  const paginated = pageSize && total > pageSize;
  const pageCount = paginated ? Math.ceil(total / pageSize) : 1;

  // Keep the page in range when the row set shrinks (e.g. a filter/search narrows results).
  useEffect(() => {
    if (page > pageCount - 1) setPage(0);
  }, [page, pageCount]);

  const view = useMemo(() => {
    if (!paginated) return sorted;
    const start = page * pageSize;
    return sorted.slice(start, start + pageSize);
  }, [sorted, paginated, page, pageSize]);

  const toggleSort = (idx) =>
    setSort((s) => {
      if (s.idx !== idx) return { idx, dir: "asc" };
      if (s.dir === "asc") return { idx, dir: "desc" };
      return { idx: null, dir: "asc" };
    });

  if (loading)
    return (
      <div className="overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead className="bg-slate-900/80 border-b border-slate-700">
            <tr>
              {cols.map((c, ci) => (
                <th key={ci} className="px-4 py-3 font-semibold whitespace-nowrap text-slate-500">
                  {typeof c.label === "string" ? c.label : ""}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-700">
            {Array.from({ length: 6 }).map((_, r) => (
              <tr key={r}>
                {cols.map((_c, ci) => (
                  <td key={ci} className="px-4 py-3.5">
                    <div className="h-3.5 rounded bg-slate-700/50 animate-pulse"
                      style={{ width: `${45 + ((ci * 17 + r * 11) % 45)}%` }} />
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  if (error)
    return (
      <div className="p-10 text-center">
        <div className="text-4xl mb-3">⚠️</div>
        <p className="text-red-300 mb-4 break-words">{error}</p>
        {onRetry && (
          <button onClick={onRetry}
            className="px-4 py-2 rounded-lg bg-slate-700 hover:bg-slate-600 text-white text-sm font-semibold transition">
            Retry
          </button>
        )}
      </div>
    );
  if (total === 0)
    return (
      <div className="p-12 text-center text-slate-400">
        <div className="text-5xl mb-4">📭</div>
        <p>{empty}</p>
      </div>
    );

  const from = paginated ? page * pageSize + 1 : 1;
  const to = paginated ? Math.min(total, (page + 1) * pageSize) : total;

  return (
    <>
      <div className="overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead className="bg-slate-900/80 border-b border-slate-700">
            <tr>
              {cols.map((c, ci) => {
                const active = sort.idx === ci;
                return (
                  <th key={ci} className="px-4 py-3 font-semibold whitespace-nowrap">
                    {c.sort ? (
                      <button
                        onClick={() => toggleSort(ci)}
                        className={`inline-flex items-center gap-1 hover:text-[#E5C07B] transition ${active ? "text-[#E5C07B]" : ""}`}
                      >
                        {c.label}
                        <span className="text-[10px] opacity-70">{active ? (sort.dir === "asc" ? "▲" : "▼") : "↕"}</span>
                      </button>
                    ) : (
                      c.label
                    )}
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-700">
            {view.map((r, i) => (
              <tr key={r.id ?? r.user_id ?? (page * (pageSize || 0) + i)} className="hover:bg-slate-700/30 transition">
                {renderRow(r, page * (pageSize || 0) + i).map((cell, j) => (
                  <td key={j} className="px-4 py-2.5 text-slate-200 whitespace-nowrap">
                    {cell === null || cell === undefined || cell === "" ? "—" : cell}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {paginated && (
        <div className="flex items-center justify-between gap-3 px-4 py-3 border-t border-slate-700 text-sm text-slate-400">
          <span>Showing <b className="text-slate-200">{from}–{to}</b> of <b className="text-slate-200">{total}</b></span>
          <div className="flex items-center gap-2">
            <button
              onClick={() => setPage((p) => Math.max(0, p - 1))}
              disabled={page === 0}
              className="px-3 py-1.5 rounded-lg bg-slate-700 hover:bg-slate-600 text-white disabled:opacity-40 disabled:cursor-not-allowed transition"
            >
              Prev
            </button>
            <span className="text-slate-300">Page {page + 1} / {pageCount}</span>
            <button
              onClick={() => setPage((p) => Math.min(pageCount - 1, p + 1))}
              disabled={page >= pageCount - 1}
              className="px-3 py-1.5 rounded-lg bg-slate-700 hover:bg-slate-600 text-white disabled:opacity-40 disabled:cursor-not-allowed transition"
            >
              Next
            </button>
          </div>
        </div>
      )}
    </>
  );
}

export function Modal({ title, onClose, children, wide = false }) {
  return (
    <div
      className="fixed inset-0 z-50 bg-black/70 backdrop-blur-sm flex items-start justify-center p-4 overflow-y-auto"
      onClick={onClose}
    >
      <div
        className={`bg-slate-800 border border-slate-700 rounded-2xl shadow-2xl w-full my-8
          ${wide ? "max-w-4xl" : "max-w-2xl"}`}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="p-5 border-b border-slate-700 flex items-center justify-between">
          <h3 className="text-lg font-bold text-[#E5C07B]">{title}</h3>
          <button onClick={onClose} className="text-slate-400 hover:text-white text-2xl leading-none px-2">
            ×
          </button>
        </div>
        <div className="p-6">{children}</div>
      </div>
    </div>
  );
}

export function Toast({ message }) {
  if (!message) return null;
  return (
    <div className="fixed top-6 right-6 z-[60] bg-slate-800 border border-[#E5C07B]/40 text-white px-5 py-3 rounded-xl shadow-2xl max-w-md">
      {message}
    </div>
  );
}

export function Tabs({ tabs, active, onChange }) {
  return (
    <div className="flex flex-wrap gap-2 mb-6">
      {tabs.map(([key, label]) => (
        <button
          key={key}
          onClick={() => onChange(key)}
          className={`px-4 py-2 rounded-lg font-semibold transition text-sm ${
            active === key
              ? "bg-gradient-to-r from-[#E5C07B] to-[#D4AF37] text-slate-900"
              : "bg-slate-800/60 border border-slate-700 text-slate-300 hover:bg-slate-700/60"
          }`}
        >
          {label}
        </button>
      ))}
    </div>
  );
}

export function PageShell({ icon, title, subtitle, toast, right, children }) {
  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-900 via-slate-800 to-slate-900 text-white p-6">
      <div className="max-w-7xl mx-auto">
        <Toast message={toast} />
        <div className="mb-6 flex items-start justify-between gap-4">
          <div>
            <h1 className="text-4xl font-bold mb-2 bg-gradient-to-r from-[#E5C07B] to-[#FCD34D] bg-clip-text text-transparent">
              {icon} {title}
            </h1>
            <p className="text-slate-400">{subtitle}</p>
          </div>
          {right && <div className="shrink-0">{right}</div>}
        </div>
        {children}
      </div>
    </div>
  );
}

/** Small hook: a toast that clears itself, used by every back-office page. */
export function useToast(ms = 3500) {
  const [toast, setToast] = React.useState("");
  const show = React.useCallback(
    (m) => {
      setToast(m);
      setTimeout(() => setToast(""), ms);
    },
    [ms]
  );
  return [toast, show];
}
