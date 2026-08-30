// Installable-app affordances for the staff + admin shells (v4b10, R17).
//
// Three things the hand-rolled PWA never had, mounted as one small component so both layouts
// get all three or none:
//   1. INSTALL — `beforeinstallprompt` did not appear anywhere in src/, so the app was
//      installable only via the browser's own buried "Add to home screen".
//   2. UPDATE — the old service worker called skipWaiting() unconditionally, swapping the app
//      under whoever was using it. Now the new version waits until they say yes.
//   3. OFFLINE — every staff write (mark a room clean, raise a ticket) hard-failed offline with
//      no explanation, because nothing anywhere read navigator.onLine.
//
// Deliberately NOT here: an offline write queue. Writes go straight to the network; a folio or a
// room status that silently syncs later is a worse problem than a visible failure.
import React, { useEffect, useState } from "react";
import { useRegisterSW } from "virtual:pwa-register/react";

const BAR =
  "fixed inset-x-0 bottom-0 z-[60] px-4 py-3 text-sm font-medium flex items-center justify-center gap-3 shadow-2xl";

export default function PwaPrompts() {
  const [online, setOnline] = useState(() => navigator.onLine);
  const [installEvt, setInstallEvt] = useState(null);
  const [installDismissed, setInstallDismissed] = useState(
    () => localStorage.getItem("pwaInstallDismissed") === "1",
  );

  const {
    needRefresh: [needRefresh, setNeedRefresh],
    updateServiceWorker,
  } = useRegisterSW({
    onRegisterError(err) {
      // A failed registration must never break the page — the app works fine uninstalled.
      console.warn("Service worker registration failed", err);
    },
  });

  useEffect(() => {
    const up = () => setOnline(true);
    const down = () => setOnline(false);
    window.addEventListener("online", up);
    window.addEventListener("offline", down);
    return () => {
      window.removeEventListener("online", up);
      window.removeEventListener("offline", down);
    };
  }, []);

  useEffect(() => {
    const onPrompt = (e) => {
      e.preventDefault();          // keep it, so we can offer it at a sensible moment
      setInstallEvt(e);
    };
    const onInstalled = () => setInstallEvt(null);
    window.addEventListener("beforeinstallprompt", onPrompt);
    window.addEventListener("appinstalled", onInstalled);
    return () => {
      window.removeEventListener("beforeinstallprompt", onPrompt);
      window.removeEventListener("appinstalled", onInstalled);
    };
  }, []);

  const install = async () => {
    if (!installEvt) return;
    installEvt.prompt();
    await installEvt.userChoice;   // resolved either way; the event is single-use
    setInstallEvt(null);
  };

  const dismissInstall = () => {
    localStorage.setItem("pwaInstallDismissed", "1");
    setInstallDismissed(true);
  };

  // Offline is the most urgent of the three: it explains why the next tap will fail.
  if (!online) {
    return (
      <div className={`${BAR} bg-amber-500 text-slate-900`} role="status">
        <span>⚠️ No connection — you can read what is on screen, but nothing can be saved.</span>
      </div>
    );
  }

  if (needRefresh) {
    return (
      <div className={`${BAR} bg-[#E5C07B] text-slate-900`} role="status">
        <span>A new version is ready.</span>
        <button
          onClick={() => updateServiceWorker(true)}
          className="bg-slate-900 text-[#E5C07B] px-4 py-1.5 rounded-lg font-semibold"
        >
          Reload
        </button>
        <button onClick={() => setNeedRefresh(false)} className="underline">
          Later
        </button>
      </div>
    );
  }

  if (installEvt && !installDismissed) {
    return (
      <div className={`${BAR} bg-slate-800 text-white border-t border-[#E5C07B]/40`} role="status">
        <span>Install this as an app for full-screen use and a home-screen icon.</span>
        <button
          onClick={install}
          className="bg-[#E5C07B] text-slate-900 px-4 py-1.5 rounded-lg font-semibold"
        >
          Install
        </button>
        <button onClick={dismissInstall} className="underline text-slate-300">
          Not now
        </button>
      </div>
    );
  }

  return null;
}
