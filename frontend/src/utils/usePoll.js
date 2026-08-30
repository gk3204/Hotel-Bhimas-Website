// Shared polling hook (v3 item 5) — screens stay current without anyone pressing Refresh.
//
// Replaces the hand-rolled setInterval copies in staff/RoomService.jsx and
// components/admin/NotificationsBell.jsx, which each solved half the problem.
//
// Three behaviours that a bare setInterval gets wrong, and why they matter here:
//
//  1. A HIDDEN TAB IS NOT POLLED. Browsers throttle timers in background tabs anyway, so the
//     requests arrive in unpredictable bursts rather than on schedule — and a tablet left on
//     the room-service board overnight would otherwise hammer the API for nothing. The tick
//     is skipped while hidden and fires IMMEDIATELY on becoming visible again, which is the
//     moment the staff member is actually looking at stale data.
//
//  2. SLOW RESPONSES DO NOT STACK. If the callback takes longer than the interval (a phone on
//     hotel wifi), a plain interval queues a second, third, fourth call. This one skips a tick
//     while the previous is still running.
//
//  3. IT CAN BE PAUSED. Pass `enabled: false` while a modal is open or a form is dirty — a
//     background refresh that clobbers half-typed input is worse than stale data.
//
// NOTE: polling deliberately does NOT count as user activity. AdminLayout's idle-logout timer
// resets on real input only, so a page can poll all night and the session still expires on
// schedule. Do not "fix" that by calling resetTimer from here.
import { useEffect, useRef } from "react";

/**
 * @param {() => (void|Promise<void>)} fn      what to run on each tick
 * @param {number} intervalMs                  how often; <= 0 disables polling
 * @param {{enabled?: boolean, runOnVisible?: boolean}} [opts]
 *        enabled       — false pauses polling without unmounting (default true)
 *        runOnVisible  — refresh the moment the tab regains focus (default true)
 */
export default function usePoll(fn, intervalMs, opts = {}) {
  const { enabled = true, runOnVisible = true } = opts;
  // Held in a ref so a caller passing an inline arrow doesn't restart the timer every render.
  const saved = useRef(fn);
  const running = useRef(false);

  useEffect(() => { saved.current = fn; }, [fn]);

  useEffect(() => {
    if (!enabled || !intervalMs || intervalMs <= 0) return undefined;

    let cancelled = false;

    const tick = async () => {
      if (cancelled || running.current) return;
      if (typeof document !== "undefined" && document.visibilityState !== "visible") return;
      running.current = true;
      try {
        await saved.current();
      } catch {
        // A poll is best-effort: the screen keeps its last good data and the next tick
        // retries. Surfacing an error banner every 20s on a flaky connection is noise.
      } finally {
        running.current = false;
      }
    };

    const id = window.setInterval(tick, intervalMs);

    const onVisible = () => {
      if (document.visibilityState === "visible") tick();
    };
    if (runOnVisible) document.addEventListener("visibilitychange", onVisible);

    return () => {
      cancelled = true;
      window.clearInterval(id);
      if (runOnVisible) document.removeEventListener("visibilitychange", onVisible);
    };
  }, [enabled, intervalMs, runOnVisible]);
}
