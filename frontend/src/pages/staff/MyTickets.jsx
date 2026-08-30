import React, { useEffect, useState } from "react";
import { FaClipboardList, FaPlus } from "react-icons/fa";
import { getTickets, getTicket, updateTicketStatus, addTicketItem, claimTicket } from "../../api/maintenance";

const inputCls =
  "w-full px-3 py-2 bg-slate-900/50 border border-slate-600 rounded-lg text-white focus:outline-none focus:border-[#E5C07B] focus:ring-2 focus:ring-[#E5C07B]/20 transition";

function statusChip(s) {
  const map = {
    open: "bg-slate-600/30 text-slate-300 border-slate-600/40",
    assigned: "bg-blue-500/20 text-blue-300 border-blue-500/30",
    in_progress: "bg-yellow-500/20 text-yellow-300 border-yellow-500/30",
    awaiting_parts: "bg-amber-500/20 text-amber-300 border-amber-500/30",
    work_done: "bg-cyan-500/20 text-cyan-300 border-cyan-500/30",
    resolved: "bg-green-500/20 text-green-300 border-green-500/30",
    closed: "bg-green-500/20 text-green-300 border-green-500/30",
  };
  return map[s] || "bg-slate-600/30 text-slate-300 border-slate-600/40";
}

export default function MyTickets() {
  const [tickets, setTickets] = useState([]);
  const [loading, setLoading] = useState(true);
  const [detail, setDetail] = useState({}); // id -> full ticket (with items)
  const [busyId, setBusyId] = useState(null);
  const [toast, setToast] = useState(null);

  const showToast = (message, type = "success") => {
    setToast({ message, type });
    setTimeout(() => setToast(null), 3500);
  };

  const load = async () => {
    setLoading(true);
    try {
      const data = await getTickets();
      setTickets(data.tickets || []);
    } catch (e) {
      showToast(e.message, "error");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, []);

  const openDetail = async (id) => {
    if (detail[id]) {
      setDetail((d) => ({ ...d, [id]: null }));
      return;
    }
    try {
      const t = await getTicket(id);
      setDetail((d) => ({ ...d, [id]: t }));
    } catch (e) {
      showToast(e.message, "error");
    }
  };

  const claim = async (t) => {
    setBusyId(t.id);
    try {
      await claimTicket(t.id);
      showToast(`Ticket #${t.id} is yours — start work when ready`);
      await load();
    } catch (e) {
      showToast(e.message, "error");
    } finally {
      setBusyId(null);
    }
  };

  const advance = async (t, status) => {
    setBusyId(t.id);
    try {
      let note;
      if (status === "work_done") {
        note = window.prompt("What did you do? (optional)") || undefined;
      }
      await updateTicketStatus(t.id, { status, note });
      showToast(`Ticket #${t.id} → ${status.replace(/_/g, " ")}`);
      setDetail((d) => ({ ...d, [t.id]: null }));
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
        <FaClipboardList /> My Tickets
      </h1>
      <p className="text-slate-400 mb-5 text-sm">Jobs assigned to you, plus open ones you can claim. Housekeeping signs off your work before a ticket is resolved.</p>

      {loading ? (
        <div className="p-10 text-center text-slate-400">Loading…</div>
      ) : tickets.length === 0 ? (
        <div className="p-10 text-center text-slate-400">Nothing to do right now.</div>
      ) : (
        <div className="space-y-3">
          {tickets.map((t) => {
            const busy = busyId === t.id;
            const d = detail[t.id];
            // v4b8: the technician is done at `work_done` — the sign-off is somebody else's.
            const terminal = ["work_done", "resolved", "closed"].includes(t.status);
            return (
              <div key={t.id} className="bg-gradient-to-r from-slate-800/60 to-slate-700/60 border border-slate-700 rounded-2xl p-4">
                <div className="flex items-center justify-between">
                  <div>
                    <div className="font-bold">
                      #{t.id} · {t.category}
                      {t.room_number ? ` · Room ${t.room_number}` : t.area ? ` · ${t.area}` : ""}
                    </div>
                    <div className="text-slate-300 text-sm mt-1">{t.issue}</div>
                  </div>
                  <span className={`px-3 py-1 rounded-full text-xs font-bold border ${statusChip(t.status)}`}>{t.status === "work_done" ? "work done — awaiting sign-off" : t.status}</span>
                </div>

                {!terminal && (
                  <div className="flex flex-wrap gap-2 mt-3">
                    {/* An unassigned open job can be picked up directly (ALT-8) — previously
                        you had to wait for a supervisor to assign it before you could start. */}
                    {t.status === "open" && !t.assignee && (
                      <button disabled={busy} onClick={() => claim(t)} className="bg-[#E5C07B] hover:bg-[#D4AF37] text-slate-900 px-3 py-1.5 rounded-lg text-sm font-bold disabled:opacity-50">
                        Claim this job
                      </button>
                    )}
                    {t.status === "assigned" && (
                      <button disabled={busy} onClick={() => advance(t, "in_progress")} className="bg-yellow-600 hover:bg-yellow-700 px-3 py-1.5 rounded-lg text-sm font-semibold disabled:opacity-50">
                        Start work
                      </button>
                    )}
                    {(t.status === "in_progress" || t.status === "awaiting_parts") && (
                      <>
                        {t.status !== "awaiting_parts" && (
                          <button disabled={busy} onClick={() => advance(t, "awaiting_parts")} className="bg-amber-600 hover:bg-amber-700 px-3 py-1.5 rounded-lg text-sm font-semibold disabled:opacity-50">
                            Awaiting parts
                          </button>
                        )}
                        <button disabled={busy} onClick={() => advance(t, "work_done")} className="bg-green-700 hover:bg-green-800 px-3 py-1.5 rounded-lg text-sm font-semibold disabled:opacity-50">
                          Mark work done
                        </button>
                      </>
                    )}
                    <button onClick={() => openDetail(t.id)} className="bg-slate-700 hover:bg-slate-600 px-3 py-1.5 rounded-lg text-sm font-semibold">
                      {d ? "Hide items" : "Items"}
                    </button>
                  </div>
                )}

                {d && (
                  <ItemsPanel ticket={d} onChanged={() => openDetail(t.id).then(() => openDetail(t.id))} showToast={showToast} />
                )}
              </div>
            );
          })}
        </div>
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

function ItemsPanel({ ticket, showToast }) {
  const [items, setItems] = useState(ticket.items || []);
  const [item, setItem] = useState("");
  const [qty, setQty] = useState(1);
  const [cost, setCost] = useState("");
  const [saving, setSaving] = useState(false);

  const add = async () => {
    if (!item.trim()) {
      showToast("Enter an item name.", "error");
      return;
    }
    setSaving(true);
    try {
      const created = await addTicketItem(ticket.id, {
        item: item.trim(),
        qty: Number(qty),
        est_cost: cost ? Number(cost) : null,
      });
      setItems((xs) => [...xs, created]);
      setItem("");
      setCost("");
      setQty(1);
      showToast("Item added");
    } catch (e) {
      showToast(e.message, "error");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="mt-3 border-t border-slate-700 pt-3">
      <div className="text-sm font-semibold text-[#E5C07B] mb-2">Required parts</div>
      {items.length === 0 ? (
        <div className="text-slate-400 text-sm mb-2">None yet.</div>
      ) : (
        <ul className="text-sm text-slate-300 mb-2 space-y-1">
          {items.map((it) => (
            <li key={it.id} className="flex justify-between">
              <span>
                {it.item} × {it.qty}
              </span>
              <span className="text-slate-400">{it.status}</span>
            </li>
          ))}
        </ul>
      )}
      <div className="grid grid-cols-3 gap-2">
        <input className={inputCls} placeholder="Part" value={item} onChange={(e) => setItem(e.target.value)} />
        <input className={inputCls} type="number" min="1" placeholder="Qty" value={qty} onChange={(e) => setQty(e.target.value)} />
        <input className={inputCls} type="number" min="0" placeholder="Est ₹" value={cost} onChange={(e) => setCost(e.target.value)} />
      </div>
      <button onClick={add} disabled={saving} className="mt-2 bg-slate-700 hover:bg-slate-600 px-3 py-1.5 rounded-lg text-sm font-semibold flex items-center gap-2 disabled:opacity-50">
        <FaPlus /> Add part
      </button>
    </div>
  );
}
