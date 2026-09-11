// Continuous staff alert (v5e): loop the shared chime while there is unattended work, stopping the
// moment it's taken up (or the user mutes). Extracted from staff/RoomService.jsx so the maintenance
// (MyTickets) and housekeeping (MyRooms) screens get the same behaviour.
//
// `pending` = how many items still need attention (0 = silence). `storageKey` persists the per-screen
// mute choice. Returns { muted, setMuted, audioBlocked } so the page can render a mute button + a
// "tap to enable sound" hint (browsers keep audio suspended until the first user gesture).
import { useEffect, useRef, useState } from "react";
import { playChime, unlockAudio, isAudioBlocked } from "./alertSound";

const REPEAT_MS = 1500;   // ~1s of silence between ~0.5s chimes — an alarm, not a periodic ping

export default function useContinuousAlert(pending, storageKey) {
  const [muted, setMuted] = useState(() => {
    try { return localStorage.getItem(storageKey) === "1"; } catch { return false; }
  });
  const [audioBlocked, setAudioBlocked] = useState(true);
  const mutedRef = useRef(muted);
  useEffect(() => {
    mutedRef.current = muted;
    try { localStorage.setItem(storageKey, muted ? "1" : "0"); } catch { /* private mode */ }
  }, [muted, storageKey]);

  // Unlock the AudioContext on the first tap/keypress; reflect the real state so the UI can prompt.
  useEffect(() => {
    const unlock = () => { unlockAudio(); setAudioBlocked(isAudioBlocked()); };
    unlock();
    window.addEventListener("pointerdown", unlock);
    window.addEventListener("keydown", unlock);
    return () => {
      window.removeEventListener("pointerdown", unlock);
      window.removeEventListener("keydown", unlock);
    };
  }, []);

  // Chime promptly when work appears, then repeat until it's cleared or muted.
  useEffect(() => {
    if (muted || !pending) return undefined;
    if (!mutedRef.current) playChime();
    const id = window.setInterval(() => { if (!mutedRef.current) playChime(); }, REPEAT_MS);
    return () => window.clearInterval(id);
  }, [muted, pending]);

  return { muted, setMuted, audioBlocked };
}
