import React, { useEffect, useState } from "react";
import { FaBroom, FaCheck, FaWrench, FaWineBottle, FaTimes } from "react-icons/fa";
import { getRooms, startTask, completeTask, minibarRestock, inspectRoom } from "../../api/housekeeping";
import RaiseTicketModal from "../../components/RaiseTicketModal";
import { useCategoryList, prettyCategory } from "../../utils/useCategoryList";

const inputCls =
  "w-full px-4 py-2 bg-slate-900/50 border border-slate-600 rounded-lg text-white focus:outline-none focus:border-[#E5C07B] focus:ring-2 focus:ring-[#E5C07B]/20 transition";

function statusChip(s) {
  const map = {
    dirty: "bg-amber-500/20 text-amber-300 border-amber-500/30",
    cleaning: "bg-yellow-500/20 text-yellow-300 border-yellow-500/30",
    clean: "bg-cyan-500/20 text-cyan-300 border-cyan-500/30",
    inspected: "bg-green-500/20 text-green-300 border-green-500/30",
    maintenance: "bg-red-500/20 text-red-300 border-red-500/30",
    dnd: "bg-purple-500/20 text-purple-300 border-purple-500/30",
  };
  return map[s] || "bg-slate-600/30 text-slate-300 border-slate-600/40";
}

export default function MyRooms() {
  const [rooms, setRooms] = useState([]);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState(null);
  const [toast, setToast] = useState(null);
  const [raiseFor, setRaiseFor] = useState(null); // room object or null
  const [minibarFor, setMinibarFor] = useState(null);
  // v4b8 (R19): the cleaner's NAME, picked per room from the admin-editable list. Kept per
  // room_id because one housekeeper can be finishing several rooms on the same screen.
  // Both names are now recorded together at the inspection step (see doInspect).
  const [cleanedBy, setCleanedBy] = useState({});
  const [inspectedBy, setInspectedBy] = useState({});
  const cleanerOptions = useCategoryList("cleaned_by", ["housekeeping_team"]);
  const inspectorOptions = useCategoryList("inspected_by", ["housekeeping_team"]);

  const showToast = (message, type = "success") => {
    setToast({ message, type });
    setTimeout(() => setToast(null), 3500);
  };

  const load = async () => {
    setLoading(true);
    try {
      const data = await getRooms(true);
      setRooms(data.rooms || []);
    } catch (e) {
      showToast(e.message, "error");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, []);

  const doStart = async (room) => {
    setBusyId(room.room_id);
    try {
      await startTask(room.open_task.id);
      showToast(`Started cleaning ${room.room_number}`);
      await load();
    } catch (e) {
      showToast(e.message, "error");
    } finally {
      setBusyId(null);
    }
  };

  // Key-lock rooms only: no cleaning card drives them, so the housekeeper marks clean by hand.
  // The room then moves to the inspection step below (names are captured there, not here).
  const doFinish = async (room) => {
    setBusyId(room.room_id);
    try {
      await completeTask(room.open_task.id, {});
      showToast(`${room.room_number} marked clean — sign off below`);
      await load();
    } catch (e) {
      showToast(e.message, "error");
    } finally {
      setBusyId(null);
    }
  };

  // Inspection sign-off (all rooms): records who cleaned + who inspected and makes the room
  // re-sellable. For card-lock rooms this is the only step the housekeeper does on this screen —
  // the desk's cleaning card handled start + finish.
  const doInspect = async (room) => {
    const cleaned = cleanedBy[room.room_id];
    const inspected = inspectedBy[room.room_id];
    if (!cleaned || !inspected) {
      showToast("Pick who cleaned and who inspected before signing off.", "error");
      return;
    }
    setBusyId(room.room_id);
    try {
      await inspectRoom(room.room_id, { cleaned_by_name: cleaned, inspected_by_name: inspected });
      showToast(`${room.room_number} inspected — re-sellable`);
      await load();
    } catch (e) {
      showToast(e.message, "error");
    } finally {
      setBusyId(null);
    }
  };

  return (
    <div>
      <h1 className="text-2xl font-bold mb-1 bg-gradient-to-r from-[#E5C07B] to-[#FCD34D] bg-clip-text text-transparent flex items-center gap-2">
        <FaBroom /> My Rooms
      </h1>
      <p className="text-slate-400 mb-5 text-sm">Rooms to clean. You set the status — the room is inspected before re-sale.</p>

      {loading ? (
        <div className="p-10 text-center text-slate-400">Loading…</div>
      ) : rooms.length === 0 ? (
        <div className="p-10 text-center text-slate-400">🎉 No rooms need attention right now.</div>
      ) : (
        <div className="space-y-3">
          {rooms.map((room) => {
            const task = room.open_task;
            const busy = busyId === room.room_id;
            const isKey = room.lock_type === "key";
            // Cleaned but not yet inspected -> show the sign-off step (both card & key rooms).
            const awaitingInspection = room.housekeeping_status === "clean";
            // A card-lock room that's dirty/cleaning is driven by the desk's cleaning card, not here.
            const cardInProgress = !isKey && task && !awaitingInspection;
            return (
              <div key={room.room_id} className="bg-gradient-to-r from-slate-800/60 to-slate-700/60 border border-slate-700 rounded-2xl p-4">
                <div className="flex items-center justify-between mb-3">
                  <div className="text-xl font-bold flex items-center gap-2">
                    Room {room.room_number}
                    <span className={`px-2 py-0.5 rounded-full text-[10px] font-semibold border ${isKey ? "border-slate-500 text-slate-300" : "border-amber-500/40 text-amber-300"}`}>
                      {isKey ? "Key lock" : "Card lock"}
                    </span>
                  </div>
                  <span className={`px-3 py-1 rounded-full text-xs font-bold border ${statusChip(room.housekeeping_status)}`}>
                    {room.housekeeping_status || "—"}
                  </span>
                </div>
                <div className="flex flex-wrap gap-2 items-center">
                  {/* Key-lock rooms have no cleaning card, so cleaning is started/finished by hand. */}
                  {isKey && task && task.status === "pending" && (
                    <button disabled={busy} onClick={() => doStart(room)} className="bg-yellow-600 hover:bg-yellow-700 px-4 py-2 rounded-lg font-semibold disabled:opacity-50">
                      Start cleaning
                    </button>
                  )}
                  {isKey && task && task.status === "in_progress" && (
                    <button disabled={busy} onClick={() => doFinish(room)} className="bg-green-700 hover:bg-green-800 px-4 py-2 rounded-lg font-semibold flex items-center gap-2 disabled:opacity-50">
                      <FaCheck /> Mark clean
                    </button>
                  )}
                  {cardInProgress && (
                    <span className="text-sm text-slate-400 italic">
                      Cleaning card is issued at the front desk — encode it there to start, return it when done.
                    </span>
                  )}

                  {/* Inspection sign-off: who cleaned + who inspected -> room re-sellable. */}
                  {awaitingInspection && (
                    <>
                      <select
                        value={cleanedBy[room.room_id] || ""}
                        onChange={(e) => setCleanedBy({ ...cleanedBy, [room.room_id]: e.target.value })}
                        aria-label={`Cleaned by, room ${room.room_number}`}
                        className="px-3 py-2 bg-slate-900/50 border border-slate-600 rounded-lg text-white text-sm focus:outline-none focus:border-[#E5C07B]"
                      >
                        <option value="">Cleaned by…</option>
                        {cleanerOptions.map((c) => (
                          <option key={c} value={c}>{prettyCategory(c)}</option>
                        ))}
                      </select>
                      <select
                        value={inspectedBy[room.room_id] || ""}
                        onChange={(e) => setInspectedBy({ ...inspectedBy, [room.room_id]: e.target.value })}
                        aria-label={`Inspected by, room ${room.room_number}`}
                        className="px-3 py-2 bg-slate-900/50 border border-slate-600 rounded-lg text-white text-sm focus:outline-none focus:border-[#E5C07B]"
                      >
                        <option value="">Inspected by…</option>
                        {inspectorOptions.map((c) => (
                          <option key={c} value={c}>{prettyCategory(c)}</option>
                        ))}
                      </select>
                      <button disabled={busy} onClick={() => doInspect(room)} className="bg-green-700 hover:bg-green-800 px-4 py-2 rounded-lg font-semibold flex items-center gap-2 disabled:opacity-50">
                        <FaCheck /> Inspect &amp; make re-sellable
                      </button>
                    </>
                  )}

                  <button onClick={() => setMinibarFor(room)} className="bg-slate-700 hover:bg-slate-600 px-4 py-2 rounded-lg font-semibold flex items-center gap-2">
                    <FaWineBottle /> Minibar
                  </button>
                  <button onClick={() => setRaiseFor(room)} className="bg-slate-700 hover:bg-slate-600 px-4 py-2 rounded-lg font-semibold flex items-center gap-2">
                    <FaWrench /> Ticket
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}

      <RaiseTicketModal
        open={!!raiseFor}
        onClose={() => setRaiseFor(null)}
        onRaised={() => showToast("Ticket raised")}
        rooms={raiseFor ? [raiseFor] : []}
        defaultRoomId={raiseFor?.room_id ?? null}
      />

      {minibarFor && (
        <MinibarModal room={minibarFor} onClose={() => setMinibarFor(null)} onDone={(msg) => { setMinibarFor(null); showToast(msg); }} onError={(m) => showToast(m, "error")} />
      )}

      {toast && (
        <div className="fixed top-6 right-6 z-50">
          <div className={`px-6 py-4 rounded-xl shadow-2xl font-medium backdrop-blur ${toast.type === "error" ? "bg-red-600/90 text-white" : "bg-green-600/90 text-white"}`}>
            {toast.type === "error" ? "❌" : "✅"} {toast.message}
          </div>
        </div>
      )}
    </div>
  );
}

function MinibarModal({ room, onClose, onDone, onError }) {
  const [description, setDescription] = useState("");
  const [qty, setQty] = useState(1);
  const [unitPrice, setUnitPrice] = useState("");
  const [saving, setSaving] = useState(false);

  const submit = async () => {
    if (!description.trim() || !unitPrice) {
      onError("Enter item and price.");
      return;
    }
    setSaving(true);
    try {
      await minibarRestock({
        room_id: room.room_id,
        description: description.trim(),
        qty: Number(qty),
        unit_price: Number(unitPrice),
        gst_percent: 5,
      });
      onDone(`Minibar posted to ${room.room_number}`);
    } catch (e) {
      setSaving(false);
      onError(e.message);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/70 backdrop-blur-sm flex items-center justify-center z-50 p-4">
      <div className="bg-gradient-to-br from-slate-800 to-slate-900 p-6 rounded-2xl w-full max-w-sm border border-slate-700 shadow-2xl">
        <h2 className="text-xl font-bold mb-4 text-[#E5C07B] flex items-center gap-2">
          <FaWineBottle /> Minibar — Room {room.room_number}
        </h2>
        <label className="block mb-1 text-sm font-semibold text-slate-300">Item(s)</label>
        <input className={`${inputCls} mb-3`} value={description} placeholder="e.g. 2x Water" onChange={(e) => setDescription(e.target.value)} />
        <div className="grid grid-cols-2 gap-3 mb-4">
          <div>
            <label className="block mb-1 text-sm font-semibold text-slate-300">Qty</label>
            <input type="number" min="1" className={inputCls} value={qty} onChange={(e) => setQty(e.target.value)} />
          </div>
          <div>
            <label className="block mb-1 text-sm font-semibold text-slate-300">Unit ₹ (incl GST)</label>
            <input type="number" min="0" className={inputCls} value={unitPrice} onChange={(e) => setUnitPrice(e.target.value)} />
          </div>
        </div>
        <div className="flex justify-end gap-3">
          <button onClick={onClose} className="bg-slate-700 hover:bg-slate-600 px-5 py-2 rounded-lg flex items-center gap-2">
            <FaTimes /> Cancel
          </button>
          <button onClick={submit} disabled={saving} className="bg-gradient-to-r from-[#E5C07B] to-[#D4AF37] text-slate-900 font-bold px-5 py-2 rounded-lg hover:scale-105 transition-all disabled:opacity-50">
            {saving ? "Posting…" : "Post to folio"}
          </button>
        </div>
      </div>
    </div>
  );
}
