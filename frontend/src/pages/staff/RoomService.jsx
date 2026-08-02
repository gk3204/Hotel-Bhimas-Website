// Room service on a tablet (backlog v2 TBC-4).
//
// Two halves: take an order, and work the live board. The board shows orders guests placed
// from the in-room QR alongside ones staff typed here — they are the same record, so nothing
// gets missed because it came in through a different door.
//
// Printing goes through the tablet's own browser (see components/ThermalPrint.jsx) because
// the backend is in the cloud and can't reach a printer in the hotel.
import React, { useCallback, useEffect, useMemo, useState } from "react";
import { FaConciergeBell, FaPrint, FaSyncAlt, FaCheck, FaTimes, FaPlus, FaMinus } from "react-icons/fa";
import * as api from "../../api/roomService";
import { KotSheet, BillSheet } from "../../components/ThermalPrint";
import { prettyCategory } from "../../utils/useCategoryList";

const money = (v) => `₹${Number(v || 0).toFixed(2)}`;

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

  // What is currently being sent to the printer (one at a time).
  const [printKot, setPrintKot] = useState(null);
  const [printBill, setPrintBill] = useState(null);
  // Orders whose KOT we've already auto-printed this session — a poll must not reprint.
  const [autoPrinted] = useState(() => new Set());

  const showToast = (message, type = "success") => {
    setToast({ message, type });
    setTimeout(() => setToast(null), 3500);
  };

  const loadBoard = useCallback(async (auto = true) => {
    try {
      const d = await api.getOrders(true);
      const list = d.orders || [];
      setOrders(list);

      // A guest order arriving from the QR portal needs to reach the kitchen. Browsers won't
      // print unattended without a gesture, so we surface it loudly and let one tap send it —
      // the desk app is the one that can print these silently.
      if (auto) {
        const fresh = list.find(
          (o) => o.source === "portal" && !o.printed_kot_at && !autoPrinted.has(o.id),
        );
        if (fresh) showToast(`New guest order for room ${fresh.room_number} — print the KOT`, "info");
      }
    } catch (e) {
      showToast(e.message, "error");
    }
  }, [autoPrinted]);

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

  // Keep the board live without hammering the API.
  useEffect(() => {
    const id = window.setInterval(() => loadBoard(true), 30000);
    return () => window.clearInterval(id);
  }, [loadBoard]);

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

  const place = async () => {
    if (!bookingId) { showToast("Pick a room first", "error"); return; }
    if (cartCount === 0) { showToast("Add at least one item", "error"); return; }
    setPlacing(true);
    try {
      const order = await api.placeOrder({
        booking_id: Number(bookingId),
        items: Object.entries(cart).map(([id, qty]) => ({ menu_item_id: Number(id), qty })),
        note: note || null,
        client_ref: `rs-desk-${bookingId}-${Date.now()}`,
      });
      setCart({});
      setNote("");
      showToast(`Order placed — ${order.kot_no}`);
      // Straight to the kitchen.
      const kot = await api.getKot(order.id, true);
      setPrintKot(kot);
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

  const cancel = async (o) => {
    if (!window.confirm(`Cancel order ${o.kot_no || `#${o.id}`}? The kitchen may already be cooking.`)) return;
    setBusyId(o.id);
    try {
      await api.cancelOrder(o.id);
      showToast("Order cancelled");
      await loadBoard(false);
    } catch (e) {
      showToast(e.message, "error");
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
        Take an order, send it to the kitchen, then deliver — the charges land on the guest's room bill.
      </p>

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
        <button onClick={() => loadBoard(false)}
                className="ml-auto px-4 py-2.5 rounded-lg bg-slate-700 hover:bg-slate-600 text-sm flex items-center gap-2">
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
              {placing ? "Placing…" : `Place order · ${cartCount} item(s) · ${money(cartTotal)}`}
            </button>
            <p className="text-slate-500 text-xs text-center mt-2">
              The kitchen docket prints straight after. Nothing is charged until you deliver.
            </p>
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
