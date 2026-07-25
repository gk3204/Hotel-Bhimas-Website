import React, { useEffect, useState } from "react";
import { FaSyncAlt, FaCheckDouble, FaSave } from "react-icons/fa";
import { getRooms, getConfig, updateConfig, inspectRoom } from "../../api/housekeeping";
import { PageShell } from "../../components/admin/BackofficeUI";

const hkChip = (s) => {
  const map = {
    dirty: "bg-amber-500/20 text-amber-300 border-amber-500/30",
    cleaning: "bg-yellow-500/20 text-yellow-300 border-yellow-500/30",
    clean: "bg-cyan-500/20 text-cyan-300 border-cyan-500/30",
    inspected: "bg-green-500/20 text-green-300 border-green-500/30",
    maintenance: "bg-red-500/20 text-red-300 border-red-500/30",
    dnd: "bg-purple-500/20 text-purple-300 border-purple-500/30",
  };
  return map[s] || "bg-slate-600/30 text-slate-300 border-slate-600/40";
};

export default function Housekeeping() {
  const [rooms, setRooms] = useState([]);
  const [config, setConfig] = useState({ auto_inspect: false, cleaning_max_hours: 6 });
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [busyId, setBusyId] = useState(null);
  const [toast, setToast] = useState(null);

  const showToast = (message, type = "success") => {
    setToast({ message, type });
    setTimeout(() => setToast(null), 3500);
  };

  const load = async () => {
    setLoading(true);
    try {
      const data = await getRooms(false);
      setRooms(data.rooms || []);
      const cfg = await getConfig();
      setConfig(cfg);
    } catch (e) {
      showToast(e.message, "error");
    }
    setLoading(false);
  };

  useEffect(() => {
    load();
  }, []); // eslint-disable-line

  const saveConfig = async () => {
    setSaving(true);
    try {
      const cfg = await updateConfig({ auto_inspect: config.auto_inspect });
      setConfig(cfg);
      showToast("Housekeeping settings saved");
    } catch (e) {
      showToast(e.message, "error");
    }
    setSaving(false);
  };

  const doInspect = async (room) => {
    setBusyId(room.room_id);
    try {
      await inspectRoom(room.room_id, {});
      showToast(`Room ${room.room_number} inspected — now re-sellable`);
      await load();
    } catch (e) {
      showToast(e.message, "error");
    } finally {
      setBusyId(null);
    }
  };

  // A room awaiting the inspected gate = cleaned but Room.status still 'cleaning'.
  const needsInspection = (r) =>
    r.room_status === "cleaning" || r.housekeeping_status === "clean" || r.housekeeping_status === "dirty";

  return (
    <PageShell
      icon="🧹"
      title="Housekeeping"
      subtitle="Housekeepers set cleaning status; a supervisor marks a room inspected before it is re-sellable."
    >
        {/* Config */}
        <div className="bg-gradient-to-r from-slate-800/50 to-slate-700/50 border border-slate-700 p-6 rounded-2xl shadow-xl mb-6 backdrop-blur">
          <h2 className="text-xl font-bold text-[#E5C07B] mb-5">Settings</h2>
          <div className="flex flex-wrap items-center gap-6">
            <label className="flex items-center gap-3 px-4 py-2.5 bg-slate-900/50 border border-slate-600 rounded-lg cursor-pointer">
              <input
                type="checkbox"
                checked={!!config.auto_inspect}
                onChange={(e) => setConfig({ ...config, auto_inspect: e.target.checked })}
                className="w-4 h-4 accent-[#E5C07B]"
              />
              <span className="text-slate-300 text-sm">Auto-inspect (mark-clean also inspects — skips the supervisor gate)</span>
            </label>
            <div className="text-slate-400 text-sm">
              Cleaning-too-long alert after <b className="text-slate-200">{config.cleaning_max_hours}h</b>
            </div>
            <button
              onClick={saveConfig}
              disabled={saving}
              className="bg-gradient-to-r from-[#E5C07B] to-[#D4AF37] text-slate-900 font-bold px-6 py-2.5 rounded-lg transition-all hover:scale-105 disabled:opacity-50 flex items-center gap-2"
            >
              <FaSave size={13} /> {saving ? "Saving…" : "Save"}
            </button>
            <button onClick={load} className="bg-slate-700 hover:bg-slate-600 font-semibold px-6 py-2.5 rounded-lg transition flex items-center gap-2">
              <FaSyncAlt size={13} /> Refresh
            </button>
          </div>
        </div>

        {/* Rooms board */}
        <div className="bg-gradient-to-r from-slate-800/50 to-slate-700/50 border border-slate-700 rounded-2xl shadow-xl overflow-hidden backdrop-blur">
          <div className="px-6 py-4 border-b border-slate-700">
            <h2 className="text-lg font-bold text-white">Room status</h2>
          </div>
          {loading ? (
            <div className="p-12 flex items-center justify-center">
              <div className="animate-spin"><div className="h-12 w-12 border-4 border-[#E5C07B] border-t-[#D4AF37] rounded-full"></div></div>
            </div>
          ) : rooms.length === 0 ? (
            <div className="p-12 text-center text-slate-400"><div className="text-5xl mb-4">🧹</div><p>No rooms.</p></div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left">
                <thead className="bg-slate-900/80 border-b border-slate-700">
                  <tr>
                    <th className="px-5 py-4 font-semibold text-sm">Room</th>
                    <th className="px-5 py-4 font-semibold text-sm">Room status</th>
                    <th className="px-5 py-4 font-semibold text-sm">Housekeeping</th>
                    <th className="px-5 py-4 font-semibold text-sm">Cleaning task</th>
                    <th className="px-5 py-4 font-semibold text-sm">Updated</th>
                    <th className="px-5 py-4 font-semibold text-sm text-right">Action</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-700">
                  {rooms.map((r) => (
                    <tr key={r.room_id} className="hover:bg-slate-700/30 transition">
                      <td className="px-5 py-4 font-medium">{r.room_number}</td>
                      <td className="px-5 py-4 text-slate-300 text-sm">{r.room_status}</td>
                      <td className="px-5 py-4">
                        <span className={`px-3 py-1 rounded-full text-xs font-bold border ${hkChip(r.housekeeping_status)}`}>
                          {r.housekeeping_status || "—"}
                        </span>
                      </td>
                      <td className="px-5 py-4 text-slate-400 text-sm">{r.open_task ? r.open_task.status : "—"}</td>
                      <td className="px-5 py-4 text-slate-400 text-sm whitespace-nowrap">{(r.updated_at || "").slice(0, 16).replace("T", " ") || "—"}</td>
                      <td className="px-5 py-4 text-right">
                        {r.room_status !== "occupied" && needsInspection(r) && (
                          <button
                            disabled={busyId === r.room_id}
                            onClick={() => doInspect(r)}
                            className="bg-green-700 hover:bg-green-800 px-4 py-1.5 rounded-lg text-sm font-semibold flex items-center gap-2 ml-auto disabled:opacity-50"
                          >
                            <FaCheckDouble /> Inspect
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {toast && (
          <div className="fixed top-6 right-6 z-50">
            <div className={`px-6 py-4 rounded-xl shadow-2xl font-medium backdrop-blur ${toast.type === "error" ? "bg-red-600/90 text-white" : "bg-green-600/90 text-white"}`}>
              {toast.type === "error" ? "❌" : "✅"} {toast.message}
            </div>
          </div>
        )}
    </PageShell>
  );
}
