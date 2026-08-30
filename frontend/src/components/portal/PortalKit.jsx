// Shared chrome for the in-room guest portal (v3 item 3).
//
// The portal deliberately does NOT use components/admin/BackofficeUI: that kit is the dark
// gold-on-slate staff brand, and this is a LIGHT, self-contained guest-facing page (same
// idiom as pages/public/PreArrival.jsx). Extracted here so the seven portal pages share one
// copy of the brand instead of pasting the same hex into each.
// The labels/formatters live in ./portalTokens.js — this file exports components only.
import React from "react";
import { Link } from "react-router-dom";
import { STATUS_LABEL } from "./portalTokens";

/** Page frame: brand header, optional back arrow, footer note. */
export function Shell({ subtitle, backTo, children }) {
  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-50 via-amber-50 to-slate-100 py-8 px-4">
      <div className="max-w-lg mx-auto">
        <div className="relative text-center mb-5">
          {backTo && (
            <Link
              to={backTo}
              aria-label="Back"
              className="absolute left-0 top-1 w-9 h-9 flex items-center justify-center rounded-full bg-white border border-slate-200 text-slate-600 shadow-sm hover:text-[#B8860B] transition"
            >
              ←
            </Link>
          )}
          <h1 className="text-3xl font-bold text-transparent bg-clip-text bg-gradient-to-r from-[#B8860B] to-[#D4AF37]">
            Hotel Bhimas
          </h1>
          {subtitle && <p className="text-slate-600 mt-1 text-sm">{subtitle}</p>}
        </div>
        {children}
        <p className="text-center text-xs text-slate-400 mt-5">
          Requests reach our front desk instantly. For anything urgent, dial 0 from your room phone.
        </p>
      </div>
    </div>
  );
}

export function Card({ icon, title, children }) {
  return (
    <div className="bg-white rounded-2xl shadow-md border border-slate-200 p-5">
      {title && (
        <h2 className="font-bold text-slate-800 mb-3 flex items-center gap-2">
          <span className="text-xl">{icon}</span> {title}
        </h2>
      )}
      {children}
    </div>
  );
}

export function Btn({ children, onClick, disabled, kind = "primary" }) {
  const cls =
    kind === "primary"
      ? "bg-gradient-to-r from-[#D4AF37] to-[#B8860B] text-white"
      : "bg-slate-100 text-slate-700 border border-slate-300";
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className={`${cls} font-semibold px-4 py-2.5 rounded-lg transition disabled:opacity-60 w-full`}
    >
      {children}
    </button>
  );
}

/** A status pill for a guest request. */
export function StatusPill({ status }) {
  const tone =
    status === "completed"
      ? "bg-emerald-100 text-emerald-700"
      : status === "dismissed"
        ? "bg-slate-100 text-slate-500"
        : "bg-amber-100 text-amber-700";
  return (
    <span className={`text-xs font-semibold px-2 py-1 rounded-full ${tone}`}>
      {STATUS_LABEL[status] || status}
    </span>
  );
}

/** Big tap target on the home hub. */
export function Tile({ to, icon, title, subtitle }) {
  return (
    <Link
      to={to}
      className="bg-white rounded-2xl shadow-md border border-slate-200 p-5 flex flex-col items-center text-center gap-1 hover:border-[#D4AF37] hover:shadow-lg transition active:scale-[0.98]"
    >
      <span className="text-3xl">{icon}</span>
      <span className="font-bold text-slate-800 text-sm mt-1">{title}</span>
      {subtitle && <span className="text-xs text-slate-500">{subtitle}</span>}
    </Link>
  );
}
