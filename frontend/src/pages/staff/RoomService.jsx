// Room service on a tablet (backlog v2 TBC-4).
//
// Two halves: take an order, and work the live board. The board shows orders guests placed
// from the in-room QR alongside ones staff typed here — they are the same record, so nothing
// gets missed because it came in through a different door.
//
// Printing goes through the tablet's own browser (see components/ThermalPrint.jsx) because
// the backend is in the cloud and can't reach a printer in the hotel.
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  FaConciergeBell, FaPrint, FaSyncAlt, FaCheck, FaTimes, FaPlus, FaMinus, FaBell, FaBellSlash,
} from "react-icons/fa";
import * as api from "../../api/roomService";
import { requestOtp } from "../../api/fraud";
import { KotSheet, BillSheet } from "../../components/ThermalPrint";
import { prettyCategory } from "../../utils/useCategoryList";
import { playChime, unlockAudio, isAudioBlocked } from "../../utils/alertSound";
import usePoll from "../../utils/usePoll";

const money = (v) => `₹${Number(v || 0).toFixed(2)}`;

const POLL_MS = 20000;  // how soon a guest order shows up (and chimes)
// Re-chime while an order still has no kitchen docket. At 1s this is a continuous alarm
// rather than a periodic reminder — the chime itself is ~0.5s, so there is about half a
// second of silence between repeats. Printing the KOT or muting stops it.
const REPEAT_MS = 1000;
const MUTE_KEY = "rs_alert_muted";

const statusChip = (s) =>
  ({
    requested: "bg-yellow-500/20 text-yellow-300 border-yellow-500/30",
    acknowledged: "bg-blue-500/20 text-blue-300 border-blue-500/30",
    completed: "bg-green-500/20 text-green-300 border-green-500/30",
    dismissed: "bg-slate-600/30 text-slate-300 border-slate-600/40",
  }[s] || "bg-slate-600/30 text-slate-300 border-slate-600/40");

export default function RoomService() {
  const [tab, setTab] = useState("board");
  const [menu, setMenu] = useState([]);
  const [rooms, setRooms] = useState([]);
  const [orders, setOrders] = useState([]);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState(null);
  const [toast, setToast] = useState(null);

  // Order form
  const [bookingId, setBookingId] = useState("");
  const [cart, setCart] = useState({});           // menuItemId -> qty
  const [note, setNote] = useState("");
  const [placing, setPlacing] = useState(false);

  // Cancel dialog (v4b5): a reason is mandatory, and the owner may have to approve.
  const [cancelTarget, setCancelTarget] = useState(null);
  const [cancelReason, setCancelReason] = useState("");
  const [cancelOtpRequired, setCancelOtpRequired] = useState(false);
  const [cancelOtpId, setCancelOtpId] = useState(null);
  const [cancelOtpCode, setCancelOtpCode] = useState("");

  // What is currently being sent to the printer (one at a time).
  const [printKot, setPrintKot] = useState(null);
  const [printBill, setPrintBill] = useState(null);
  // Orders whose KOT we've already auto-printed this session — a poll must not reprint.
  const [autoPrinted] = useState(() => new Set());

  // ---- Audible alert for guest QR orders ----------------------------------
  // Ids already on the board when this screen opened. They are seeded WITHOUT chiming, so
  // opening or reloading the page never alarms for orders staff already know about.
  const seenIds = useRef(null);
  const [muted, setMuted] = useState(() => localStorage.getItem(MUTE_KEY) === "1");
  const [audioBlocked, setAudioBlocked] = useState(true);
  const mutedRef = useRef(muted);
  useEffect(() => { mutedRef.current = muted; }, [muted]);

  const showToast = (message, type = "success") => {
    setToast({ message, type });
    setTimeout(() => setToast(null), 3500);
  };

  // Browsers keep audio suspended until the user interacts. Unlock on the first tap/keypress
  // anywhere on the screen, and reflect the real state so the UI can prompt if it's still off.
  useEffect(() => {
    const unlock = () => {
      unlockAudio();
      setAudioBlocked(isAudioBlocked());
    };
    unlock();
    window.addEventListener("pointerdown", unlock);
    window.addEventListener("keydown", unlock);
    return () => {
      window.removeEventListener("pointerdown", unlock);
      window.removeEventListener("keydown", unlock);
    };
  }, []);

  const loadBoard = useCallback(async (auto = true) => {
    try {
      const d = await api.getOrders(true);
      const list = d.orders || [];
      setOrders(list);

      // First load only seeds the baseline — no alert for what was already there.
      if (seenIds.current === null) {
        seenIds.current = new Set(list.map((o) => o.id));
        return;
      }

      // A guest order arriving from the QR portal needs to reach the kitchen. Browsers won't
      // print unattended without a gesture, so we chime and let one tap send it — the desk app
      // is the one that can print these silently.
      const fresh = list.filter((o) => o.source === "portal" && !seenIds.current.has(o.id));
      list.forEach((o) => seenIds.current.add(o.id));
      if (auto && fresh.length > 0) {
        if (!mutedRef.current) playChime();
        const first = fresh[0];
        showToast(
          fresh.length === 1
            ? `New guest order — room ${first.room_number}`
            : `${fresh.length} new guest orders`,
          "info",
        );
      }
    } catch (e) {
      showToast(e.message, "error");
    }
  }, []);

  useEffect(() => {
    (async () => {
      setLoading(true);
      try {
        const [m, r] = await Promise.all([api.getMenu(), api.getRooms()]);
        setMenu(m.items || []);
        setRooms(r.rooms || []);
        await loadBoard(false);
      } catch (e) {
        showToast(e.message, "error");
      } finally {
        setLoading(false);
      }
    })();
  }, [loadBoard]);

  // Keep the board live without hammering the API. The shared poller (v3 item 5) also
  // refreshes the instant the tablet is woken, which is exactly when a missed order matters.
  usePoll(useCallback(() => loadBoard(true), [loadBoard]), POLL_MS);

  // Orders the kitchen hasn't been told about yet. Drives both the banner and the re-chime.
  const awaitingKot = useMemo(
    () => orders.filter((o) => !o.printed_kot_at && o.status !== "completed"),
    [orders],
  );

  // Keep chiming while anything is still waiting for the kitchen — a single ding is easy to
  // miss at a busy desk. It stops the moment the docket is printed.
  useEffect(() => {
    if (muted || awaitingKot.length === 0) return undefined;
    const id = window.setInterval(() => {
      if (!mutedRef.current) playChime();
    }, REPEAT_MS);
    return () => window.clearInterval(id);
  }, [muted, awaitingKot.length]);

  // A sleeping tablet is the likeliest way an order gets missed, and browsers throttle timers
  // in hidden tabs. Hold a screen wake lock while this screen is open, and re-take it when the
  // tablet comes back to the foreground (the lock is dropped automatically on hide).
  useEffect(() => {
    let lock = null;
    let cancelled = false;
    const acquire = async () => {
      try {
        if (document.visibilityState !== "visible" || !navigator.wakeLock) return;
        lock = await navigator.wakeLock.request("screen");
      } catch {
        /* unsupported or refused — the screen just sleeps as usual */
      }
    };
    const onVisible = () => { if (document.visibilityState === "visible" && !cancelled) acquire(); };
    acquire();
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      cancelled = true;
      document.removeEventListener("visibilitychange", onVisible);
      try { lock?.release(); } catch { /* already gone */ }
    };
  }, []);

  const toggleMute = () => {
    const next = !muted;
    setMuted(next);
    localStorage.setItem(MUTE_KEY, next ? "1" : "0");
    if (!next) { unlockAudio(); setAudioBlocked(isAudioBlocked()); playChime(); }
  };

  const bump = (id, delta) =>
    setCart((c) => {
      const next = { ...c };
      const q = (next[id] || 0) + delta;
      if (q <= 0) delete next[id];
      else next[id] = q;
      return next;
    });

  const cartTotal = useMemo(
    () => menu.reduce((sum, m) => sum + (cart[m.id] || 0) * Number(m.price || 0), 0),
    [cart, menu],
  );
  const cartCount = useMemo(
    () => Object.values(cart).reduce((a, b) => a + b, 0),
    [cart],
  );

  const byCategory = useMemo(() => {
    const g = {};
    for (const m of menu) (g[m.category] ||= []).push(m);
    return g;
  }, [menu]);

  // v4b5 (R1): ONE press — order, kitchen docket, charge and guest bill.
  // Staff are standing at the counter with the guest, so the old two-step "place now, come
  // back and deliver later" dance was pure friction for an order they take themselves. Both
  // documents come back in the SAME response, so this prints twice with no extra round trip.
  // (A guest's own QR order keeps its separate Deliver — see the board below.)
  const place = async () => {
    if (!bookingId) { showToast("Pick a room first", "error"); return; }
    if (cartCount === 0) { showToast("Add at least one item", "error"); return; }
    setPlacing(true);
    try {
      const order = await api.placeOrder({
        booking_id: Number(bookingId),
        items: Object.entries(cart).map(([id, qty]) => ({ menu_item_id: Number(id), qty })),
        note: note || null,
        deliver_now: true,
        client_ref: `rs-desk-${bookingId}-${Date.now()}`,
      });
      setCart({});
      setNote("");
      showToast(`${order.kot_no} — ${money(order.bill?.total)} charged to room ${order.room_number}`);
      if (order.kot) setPrintKot(order.kot);
      if (order.bill) setPrintBill(order.bill);
      await loadBoard(false);
      setTab("board");
    } catch (e) {
      showToast(e.message, "error");
    } finally {
      setPlacing(false);
    }
  };

  const printKotFor = async (o) => {
    setBusyId(o.id);
    try {
      const kot = await api.getKot(o.id, true);
      autoPrinted.add(o.id);
      setPrintKot(kot);
      await loadBoard(false);
    } catch (e) {
      showToast(e.message, "error");
    } finally {
      setBusyId(null);
    }
  };

  const deliver = async (o) => {
    setBusyId(o.id);
    try {
      const res = await api.deliverOrder(o.id);
      if (res.duplicate) showToast("That order was already delivered", "info");
      else showToast(`Delivered — ${money(res.bill?.total)} charged to room ${res.room_number}`);
      setPrintBill(res.bill);
      await loadBoard(false);
    } catch (e) {
      showToast(e.message, "error");
    } finally {
      setBusyId(null);
    }
  };

  // v4b5 (R5): cancelling needs a reason, and an owner code when the gate is armed. Before
  // this, any tablet login could silently dismiss an order that already had a KOT number
  // against it — no reason, no approval, no trace beyond the status flip.
  const cancel = (o) => {
    setCancelTarget(o);
    setCancelReason("");
    setCancelOtpRequired(false);
    setCancelOtpId(null);
    setCancelOtpCode("");
  };

  const requestCancelApproval = async () => {
    if (!cancelTarget) return;
    try {
      const res = await requestOtp({
        action: "room_service_cancel",
        booking_id: cancelTarget.booking_id,
        amount: cancelTarget.amount,
        context: { kot_no: cancelTarget.kot_no },
      });
      setCancelOtpId(res.otp_id);
      showToast("Approval requested — ask the owner for the code", "info");
    } catch (e) {
      showToast(e.message, "error");
    }
  };

  const confirmCancel = async () => {
    const o = cancelTarget;
    if (!o) return;
    if (cancelReason.trim().length < 3) {
      showToast("A reason is required", "error");
      return;
    }
    setBusyId(o.id);
    try {
      await api.cancelOrder(o.id, {
        reason: cancelReason.trim(),
        owner_otp_id: cancelOtpRequired ? cancelOtpId : null,
        owner_otp_code: cancelOtpRequired ? cancelOtpCode.trim() : null,
      });
      showToast("Order cancelled");
      setCancelTarget(null);
      setCancelReason("");
      setCancelOtpRequired(false);
      setCancelOtpId(null);
      setCancelOtpCode("");
      await loadBoard(false);
    } catch (e) {
      // Same handshake as the desk: the server refuses, we reveal the approval panel and
      // keep the dialog open so the typed reason survives.
      if (String(e.message || "").toLowerCase().includes("owner_otp")) {
        setCancelOtpRequired(true);
        showToast("Owner approval needed to cancel this order", "info");
      } else {
        showToast(e.message, "error");
      }
    } finally {
      setBusyId(null);
    }
  };

  return (
    <div>
      <h1 className="text-2xl font-bold mb-1 bg-gradient-to-r from-[#E5C07B] to-[#FCD34D] bg-clip-text text-transparent flex items-center gap-2">
        <FaConciergeBell /> Room Service
      </h1>
      <p className="text-slate-400 mb-4 text-sm">
        Take an order in one press — kitchen docket, guest bill and the charge on the room, together.
        Orders guests place themselves still need a Deliver.
      </p>

      {/* Waiting-for-kitchen banner. The chime is the prompt; this is the action, and it still
          works when sound is muted or the browser has blocked audio. */}
      {awaitingKot.length > 0 && (
        <button
          onClick={() => { setTab("board"); printKotFor(awaitingKot[0]); }}
          className="w-full mb-4 px-4 py-3 rounded-xl bg-[#E5C07B] text-slate-900 font-bold flex items-center justify-between gap-3 shadow-lg animate-pulse text-left"
        >
          <span className="flex items-center gap-2">
            <FaBell />
            {awaitingKot.length === 1
              ? `Room ${awaitingKot[0].room_number} — order waiting for the kitchen`
              : `${awaitingKot.length} orders waiting for the kitchen`}
          </span>
          <span className="text-sm whitespace-nowrap">Print KOT →</span>
        </button>
      )}

      {/* Sound is useless if it's silently blocked, so say so rather than pretend. */}
      {!muted && audioBlocked && (
        <button
          onClick={() => { unlockAudio(); setAudioBlocked(isAudioBlocked()); }}
          className="w-full mb-4 px-4 py-2.5 rounded-lg bg-slate-700 hover:bg-slate-600 text-sm text-left"
        >
          🔇 Sound is blocked by the browser — tap here to enable order alerts.
        </button>
      )}

      <div className="flex gap-2 mb-5">
        {[["board", `Orders${orders.length ? ` (${orders.length})` : ""}`], ["order", "Take an order"]].map(([k, label]) => (
          <button
            key={k}
            onClick={() => setTab(k)}
            className={`px-4 py-2.5 rounded-lg font-semibold text-sm transition ${
              tab === k ? "bg-[#E5C07B] text-slate-900" : "bg-slate-700 hover:bg-slate-600"
            }`}
          >
            {label}
          </button>
        ))}
        <button onClick={toggleMute}
                title={muted ? "Order alerts are muted" : "Order alerts are on"}
                aria-label={muted ? "Unmute order alerts" : "Mute order alerts"}
                className={`ml-auto px-4 py-2.5 rounded-lg text-sm flex items-center gap-2 transition ${
                  muted ? "bg-slate-700 hover:bg-slate-600 text-slate-400" : "bg-slate-700 hover:bg-slate-600"
                }`}>
          {muted ? <FaBellSlash size={12} /> : <FaBell size={12} />}
          {muted ? "Muted" : "Alerts on"}
        </button>
        <button onClick={() => loadBoard(false)}
                className="px-4 py-2.5 rounded-lg bg-slate-700 hover:bg-slate-600 text-sm flex items-center gap-2">
          <FaSyncAlt size={12} /> Refresh
        </button>
      </div>

      {loading && <div className="p-10 text-center text-slate-400">Loading…</div>}

      {/* ------------------------------------------------------------ board */}
      {!loading && tab === "board" && (
        orders.length === 0 ? (
          <div className="p-10 text-center text-slate-400">No open orders.</div>
        ) : (
          <div className="space-y-3">
            {orders.map((o) => {
              const busy = busyId === o.id;
              const items = o.payload?.items || [];
              return (
                <div key={o.id} className="bg-gradient-to-r from-slate-800/60 to-slate-700/60 border border-slate-700 rounded-2xl p-4">
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <div className="font-bold text-lg">
                        Room {o.room_number || "—"}
                        <span className="text-slate-400 text-sm font-normal ml-2">{o.kot_no || `#${o.id}`}</span>
                      </div>
                      <div className="text-slate-300 text-sm mt-1">
                        {items.map((it) => `${it.qty}× ${it.name}`).join(", ") || "—"}
                      </div>
                      {o.payload?.note && (
                        <div className="text-amber-300 text-sm mt-1">Note: {o.payload.note}</div>
                      )}
                      <div className="text-slate-500 text-xs mt-1">
                        {o.source === "portal" ? "Guest ordered from the room QR" : "Taken by staff"}
                        {o.printed_kot_at ? " · KOT printed" : " · KOT not printed"}
                      </div>
                    </div>
                    <div className="text-right shrink-0">
                      <div className="font-bold text-[#E5C07B]">{money(o.amount)}</div>
                      <span className={`inline-block mt-1 px-2.5 py-1 rounded-full text-xs font-bold border ${statusChip(o.status)}`}>
                        {o.status}
                      </span>
                    </div>
                  </div>

                  <div className="flex flex-wrap gap-2 mt-3">
                    <button disabled={busy} onClick={() => printKotFor(o)}
                            className={`px-3 py-2 rounded-lg text-sm font-semibold disabled:opacity-50 flex items-center gap-1.5 ${
                              o.printed_kot_at ? "bg-slate-700 hover:bg-slate-600" : "bg-[#E5C07B] text-slate-900"
                            }`}>
                      <FaPrint size={12} /> {o.printed_kot_at ? "Reprint KOT" : "Print KOT"}
                    </button>
                    <button disabled={busy} onClick={() => deliver(o)}
                            className="bg-green-700 hover:bg-green-800 px-3 py-2 rounded-lg text-sm font-semibold disabled:opacity-50 flex items-center gap-1.5">
                      <FaCheck size={12} /> Deliver &amp; print bill
                    </button>
                    <button disabled={busy} onClick={() => cancel(o)}
                            className="bg-slate-700 hover:bg-red-700 px-3 py-2 rounded-lg text-sm font-semibold disabled:opacity-50 flex items-center gap-1.5">
                      <FaTimes size={12} /> Cancel
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        )
      )}

      {/* ------------------------------------------------------------ order */}
      {!loading && tab === "order" && (
        <div className="space-y-4">
          <div className="bg-slate-800/60 border border-slate-700 rounded-2xl p-4">
            <label htmlFor="rs-room" className="block text-sm font-semibold text-slate-300 mb-2">Room</label>
            <select id="rs-room" value={bookingId} onChange={(e) => setBookingId(e.target.value)}
                    className="w-full px-3 py-3 bg-slate-900/50 border border-slate-600 rounded-lg text-white text-lg">
              <option value="">Pick a room…</option>
              {rooms.map((r) => (
                <option key={r.booking_id} value={r.booking_id} disabled={!r.folio_open}>
                  {r.room_number} — {r.guest_name || "Guest"}{r.folio_open ? "" : " (bill settled)"}
                </option>
              ))}
            </select>
            {rooms.length === 0 && (
              <p className="text-slate-500 text-xs mt-2">Nobody is checked in right now.</p>
            )}
          </div>

          {Object.entries(byCategory).map(([cat, items]) => (
            <div key={cat}>
              <h2 className="text-[#E5C07B] font-bold mb-2">{prettyCategory(cat)}</h2>
              <div className="space-y-2">
                {items.map((m) => (
                  <div key={m.id} className="bg-slate-800/60 border border-slate-700 rounded-xl p-3 flex items-center gap-3">
                    <div className="flex-1 min-w-0">
                      <div className="font-semibold">{m.name}</div>
                      {m.description && <div className="text-slate-400 text-xs">{m.description}</div>}
                      <div className="text-[#E5C07B] text-sm">{money(m.price)}</div>
                    </div>
                    <div className="flex items-center gap-2 shrink-0">
                      <button onClick={() => bump(m.id, -1)} disabled={!cart[m.id]}
                              aria-label={`Remove one ${m.name}`}
                              className="w-11 h-11 rounded-lg bg-slate-700 hover:bg-slate-600 disabled:opacity-30 flex items-center justify-center">
                        <FaMinus size={12} />
                      </button>
                      <span className="w-8 text-center font-bold text-lg">{cart[m.id] || 0}</span>
                      <button onClick={() => bump(m.id, 1)}
                              aria-label={`Add one ${m.name}`}
                              className="w-11 h-11 rounded-lg bg-[#E5C07B] text-slate-900 hover:bg-[#D4AF37] flex items-center justify-center">
                        <FaPlus size={12} />
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          ))}

          {menu.length === 0 && (
            <div className="p-8 text-center text-slate-400">
              The menu is empty. An admin adds dishes under Guest Portal → Room-service menu.
            </div>
          )}

          <div className="bg-slate-800/60 border border-slate-700 rounded-2xl p-4">
            <label htmlFor="rs-note" className="block text-sm font-semibold text-slate-300 mb-2">
              Note for the kitchen
            </label>
            <input id="rs-note" value={note} onChange={(e) => setNote(e.target.value)}
                   placeholder="e.g. no onion, extra spicy"
                   className="w-full px-3 py-3 bg-slate-900/50 border border-slate-600 rounded-lg text-white" />
          </div>

          <div className="sticky bottom-3">
            <button onClick={place} disabled={placing || cartCount === 0 || !bookingId}
                    className="w-full bg-gradient-to-r from-[#E5C07B] to-[#D4AF37] text-slate-900 font-bold py-4 rounded-xl text-lg disabled:opacity-50 shadow-xl">
              {placing
                ? "Sending…"
                : `Order · KOT · Bill — ${cartCount} item(s) · ${money(cartTotal)}`}
            </button>
            <p className="text-slate-500 text-xs text-center mt-2">
              One press: the kitchen docket and the guest's bill both print, and the amount goes
              on the room.
            </p>
          </div>
        </div>
      )}

      {/* Cancel an order (v4b5): reason always, owner code when the gate is armed. */}
      {cancelTarget && (
        <div className="fixed inset-0 z-50 bg-black/70 flex items-end sm:items-center justify-center p-4">
          <div className="w-full max-w-md rounded-2xl bg-slate-800 border border-slate-700 p-5">
            <h3 className="text-lg font-bold text-white">
              Cancel {cancelTarget.kot_no || `#${cancelTarget.id}`}?
            </h3>
            <p className="text-slate-400 text-sm mt-1">
              Room {cancelTarget.room_number} · {money(cancelTarget.amount)}
              {cancelTarget.printed_kot_at
                ? " · the kitchen already has the docket, so the food may exist"
                : " · the docket has not printed yet"}
            </p>

            <label className="block mt-4 text-sm font-semibold text-slate-300" htmlFor="rs-cancel-reason">
              Reason (required)
            </label>
            <input
              id="rs-cancel-reason"
              value={cancelReason}
              onChange={(e) => setCancelReason(e.target.value)}
              placeholder="e.g. guest changed their mind, out of stock"
              className="mt-1 w-full px-4 py-2 bg-slate-900/60 border border-slate-600 rounded-lg text-white focus:outline-none focus:border-[#E5C07B]"
            />

            {cancelOtpRequired && (
              <div className="mt-4 rounded-xl border border-amber-500/40 bg-amber-500/10 p-3">
                <p className="text-amber-200 text-sm">Cancelling this order needs owner approval.</p>
                <div className="flex gap-2 mt-2">
                  <button
                    onClick={requestCancelApproval}
                    className="px-3 py-2 rounded-lg bg-slate-700 hover:bg-slate-600 text-sm whitespace-nowrap"
                  >
                    Request approval
                  </button>
                  <input
                    value={cancelOtpCode}
                    onChange={(e) => setCancelOtpCode(e.target.value)}
                    placeholder="6-digit code"
                    inputMode="numeric"
                    className="flex-1 min-w-0 px-3 py-2 bg-slate-900/60 border border-slate-600 rounded-lg text-white focus:outline-none focus:border-[#E5C07B]"
                  />
                </div>
              </div>
            )}

            <div className="flex gap-2 mt-5">
              <button
                onClick={confirmCancel}
                disabled={busyId === cancelTarget.id}
                className="flex-1 px-4 py-3 rounded-xl bg-red-900/70 hover:bg-red-900 text-white font-semibold disabled:opacity-50"
              >
                Cancel order
              </button>
              <button
                onClick={() => setCancelTarget(null)}
                className="px-4 py-3 rounded-xl bg-slate-700 hover:bg-slate-600 text-white"
              >
                Keep it
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Print sheets — rendered only while printing, then cleared. */}
      {printKot && <KotSheet kot={printKot} onDone={() => setPrintKot(null)} />}
      {printBill && <BillSheet bill={printBill} onDone={() => setPrintBill(null)} />}

      {toast && (
        <div className={`fixed bottom-4 left-4 right-4 z-50 px-5 py-3 rounded-xl shadow-2xl font-semibold text-center ${
          toast.type === "error" ? "bg-red-600/90" : toast.type === "info" ? "bg-blue-600/90" : "bg-green-600/90"
        }`}>
          {toast.message}
        </div>
      )}
    </div>
  );
}
