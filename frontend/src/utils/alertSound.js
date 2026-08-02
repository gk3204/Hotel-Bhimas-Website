// Audible alert for staff screens (room-service orders from the in-room QR).
//
// The chime is SYNTHESISED with the Web Audio API rather than played from an audio file:
// it works offline in the installable PWA, ships no binary, and sidesteps the media-autoplay
// rules that apply to <audio> elements.
//
// The one thing that genuinely bites here: browsers refuse to start an AudioContext until the
// user has interacted with the page. If that is ignored the alert fails SILENTLY and staff
// wrongly trust it — so `unlockAudio()` is called from the first tap and `isAudioBlocked()`
// lets the UI say so out loud.

let ctx = null;

function supported() {
  return typeof window !== "undefined" && (window.AudioContext || window.webkitAudioContext);
}

/** Create (or resume) the shared AudioContext. Safe to call repeatedly; call it from a
 *  user gesture — a click, tap or keypress — or the browser will keep it suspended. */
export function unlockAudio() {
  if (!supported()) return false;
  try {
    if (!ctx) {
      const Ctor = window.AudioContext || window.webkitAudioContext;
      ctx = new Ctor();
    }
    if (ctx.state === "suspended") ctx.resume().catch(() => {});
    return ctx.state === "running";
  } catch {
    return false;
  }
}

/** True when we have no usable audio — the caller should show a "tap to enable sound" hint
 *  instead of pretending the alert works. */
export function isAudioBlocked() {
  if (!supported()) return true;
  return !ctx || ctx.state !== "running";
}

/** One short two-tone chime. Never throws — a failed alert must not break the screen. */
export function playChime() {
  if (!supported()) return;
  try {
    if (!ctx) unlockAudio();
    if (!ctx || ctx.state !== "running") return;

    // Two notes, second a little higher, so it reads as an "attention" chime rather than a beep.
    const notes = [
      { freq: 880, at: 0, dur: 0.18 },
      { freq: 1174.7, at: 0.16, dur: 0.28 },
    ];
    const now = ctx.currentTime;

    for (const n of notes) {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = "sine";
      osc.frequency.value = n.freq;

      // Ramp the gain instead of switching it — an abrupt start/stop clicks audibly.
      const t0 = now + n.at;
      gain.gain.setValueAtTime(0.0001, t0);
      gain.gain.exponentialRampToValueAtTime(0.35, t0 + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, t0 + n.dur);

      osc.connect(gain).connect(ctx.destination);
      osc.start(t0);
      osc.stop(t0 + n.dur + 0.02);
    }
  } catch {
    /* alerting is best-effort — never let it break the board */
  }
}
