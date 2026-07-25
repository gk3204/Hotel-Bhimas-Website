import React, { createContext, useCallback, useContext, useRef, useState } from "react";
import { FaExclamationTriangle } from "react-icons/fa";

// App-wide themed replacement for window.confirm / window.alert.
//   const { confirm, notify } = useConfirm();
//   if (!(await confirm({ title, message, confirmText, tone: "danger" }))) return;
//   await notify({ title, message });   // single-button info dialog
const ConfirmContext = createContext(null);

export function useConfirm() {
  const ctx = useContext(ConfirmContext);
  if (!ctx) throw new Error("useConfirm must be used within <ConfirmProvider>");
  return ctx;
}

export function ConfirmProvider({ children }) {
  const [state, setState] = useState(null); // { opts, mode } | null
  const resolver = useRef(null);

  const open = useCallback((opts, mode) => {
    return new Promise((resolve) => {
      resolver.current = resolve;
      setState({ opts, mode });
    });
  }, []);

  const confirm = useCallback((opts) => open(opts, "confirm"), [open]);
  const notify = useCallback((opts) => open(opts, "notify"), [open]);

  const settle = (value) => {
    resolver.current?.(value);
    resolver.current = null;
    setState(null);
  };

  const opts = state?.opts || {};
  const isDanger = opts.tone === "danger";

  return (
    <ConfirmContext.Provider value={{ confirm, notify }}>
      {children}
      {state && (
        <div className="fixed inset-0 z-[100] flex items-center justify-center p-4">
          <div
            className="absolute inset-0 bg-black/60 backdrop-blur-sm"
            onClick={() => settle(state.mode === "notify" ? undefined : false)}
          />
          <div className="relative w-full max-w-md bg-slate-800 border border-slate-700 rounded-2xl shadow-2xl p-6">
            <div className="flex items-start gap-4">
              <div
                className={`shrink-0 h-11 w-11 rounded-full flex items-center justify-center ${
                  isDanger ? "bg-red-500/15 text-red-300" : "bg-[#E5C07B]/15 text-[#E5C07B]"
                }`}
              >
                <FaExclamationTriangle size={18} />
              </div>
              <div className="min-w-0">
                <h3 className="text-lg font-bold text-white">{opts.title || "Please confirm"}</h3>
                {opts.message && <p className="text-slate-300 text-sm mt-1 break-words">{opts.message}</p>}
              </div>
            </div>

            <div className="flex justify-end gap-3 mt-6">
              {state.mode === "confirm" && (
                <button
                  onClick={() => settle(false)}
                  className="px-4 py-2 rounded-lg text-sm font-semibold bg-slate-700 hover:bg-slate-600 text-white transition"
                >
                  {opts.cancelText || "Cancel"}
                </button>
              )}
              <button
                autoFocus
                onClick={() => settle(state.mode === "notify" ? undefined : true)}
                className={`px-4 py-2 rounded-lg text-sm font-bold transition ${
                  isDanger
                    ? "bg-red-600 hover:bg-red-500 text-white"
                    : "bg-gradient-to-r from-[#E5C07B] to-[#D4AF37] text-slate-900 hover:scale-105"
                }`}
              >
                {opts.confirmText || (state.mode === "notify" ? "OK" : "Confirm")}
              </button>
            </div>
          </div>
        </div>
      )}
    </ConfirmContext.Provider>
  );
}
