import React, { useEffect, useState, useCallback } from "react";
import { useSearchParams } from "react-router-dom";
import {
  listGuests,
  getGuest,
  getGuestHistory,
  updateGuest,
  setVip,
  setBlacklist,
} from "../../api/crm";
import { FaSearch, FaStar, FaBan, FaEdit, FaGift } from "react-icons/fa";
import { usePaged, Paginator } from "../../components/admin/Paginator";
import { PageShell } from "../../components/admin/BackofficeUI";

const SEGMENTS = [
  { key: "all", label: "All guests" },
  { key: "vip", label: "VIP" },
  { key: "blacklist", label: "Blacklist" },
];

const GuestDirectory = () => {
  const [searchParams] = useSearchParams();
  const [guests, setGuests] = useState([]);
  const [loading, setLoading] = useState(false);
  const [q, setQ] = useState(searchParams.get("q") || "");
  const [segment, setSegment] = useState("all");
  const [selected, setSelected] = useState(null); // {guest, history}
  const [toast, setToast] = useState(null);
  const paged = usePaged(guests, 25);

  const showToast = (message, type = "success") => {
    setToast({ message, type });
    setTimeout(() => setToast(null), 3000);
  };

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await listGuests({
        q,
        vip: segment === "vip",
        blacklist: segment === "blacklist",
      });
      setGuests(data.data || []);
    } catch (err) {
      showToast(err.message || "Failed to load guests", "error");
    } finally {
      setLoading(false);
    }
  }, [q, segment]);

  useEffect(() => {
    load();
  }, [segment]); // eslint-disable-line react-hooks/exhaustive-deps

  const openGuest = async (id) => {
    try {
      const [guest, history] = await Promise.all([getGuest(id), getGuestHistory(id)]);
      setSelected({ guest, history, form: toForm(guest) });
    } catch (err) {
      showToast(err.message || "Failed to open guest", "error");
    }
  };

  const toForm = (g) => ({
    name: g.name || "",
    phone: g.phone || "",
    email: g.email || "",
    address: g.profile?.address || "",
    dob: g.profile?.dob || "",
    gstin: g.profile?.gstin || "",
    notes: g.profile?.notes || "",
    marketing_optin: !!g.profile?.marketing_optin,
  });

  const refreshSelected = async (id) => {
    const [guest, history] = await Promise.all([getGuest(id), getGuestHistory(id)]);
    setSelected((s) => ({ ...s, guest, history }));
  };

  const saveProfile = async () => {
    const id = selected.guest.guest_id;
    try {
      await updateGuest(id, selected.form);
      await refreshSelected(id);
      load();
      showToast("Profile saved");
    } catch (err) {
      showToast(err.message || "Save failed", "error");
    }
  };

  const toggleVip = async () => {
    const id = selected.guest.guest_id;
    try {
      await setVip(id, !selected.guest.profile.vip);
      await refreshSelected(id);
      load();
    } catch (err) {
      showToast(err.message || "VIP update failed", "error");
    }
  };

  const toggleBlacklist = async () => {
    const id = selected.guest.guest_id;
    const currently = selected.guest.profile.blacklist;
    let reason = null;
    if (!currently) {
      reason = window.prompt("Reason for blacklisting this guest:");
      if (!reason || !reason.trim()) return;
    }
    try {
      await setBlacklist(id, !currently, reason);
      await refreshSelected(id);
      load();
    } catch (err) {
      showToast(err.message || "Blacklist update failed", "error");
    }
  };

  return (
    <PageShell icon="👤" title="Guest Directory" subtitle="Recognized customers — profiles, VIP, blacklist and loyalty">

        {/* Controls */}
        <div className="bg-gradient-to-r from-slate-800/50 to-slate-700/50 border border-slate-700 rounded-2xl p-5 mb-6 backdrop-blur">
          <div className="flex flex-wrap items-center gap-3">
            <div className="flex items-center bg-slate-900/50 border border-slate-600 rounded-lg px-3 flex-1 min-w-[240px]">
              <FaSearch className="text-slate-500" />
              <input
                value={q}
                onChange={(e) => setQ(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && load()}
                placeholder="Search name, phone or email…"
                className="px-3 py-2 bg-transparent text-white focus:outline-none flex-1"
              />
            </div>
            <button
              onClick={load}
              className="bg-gradient-to-r from-[#E5C07B] to-[#D4AF37] text-slate-900 px-5 py-2 rounded-lg font-bold hover:scale-105 transition"
            >
              Search
            </button>
            <div className="flex gap-2">
              {SEGMENTS.map((s) => (
                <button
                  key={s.key}
                  onClick={() => setSegment(s.key)}
                  className={`px-4 py-2 rounded-lg text-sm font-semibold border transition ${
                    segment === s.key
                      ? "bg-[#E5C07B]/20 text-[#FCD34D] border-[#E5C07B]/40"
                      : "bg-slate-900/40 text-slate-300 border-slate-600 hover:border-slate-500"
                  }`}
                >
                  {s.label}
                </button>
              ))}
            </div>
          </div>
        </div>

        {/* List */}
        <div className="bg-gradient-to-r from-slate-800/50 to-slate-700/50 border border-slate-700 rounded-2xl shadow-xl overflow-hidden backdrop-blur">
          {loading ? (
            <div className="p-12 flex items-center justify-center">
              <div className="animate-spin h-12 w-12 border-4 border-[#E5C07B] border-t-[#D4AF37] rounded-full"></div>
            </div>
          ) : guests.length === 0 ? (
            <div className="p-12 text-center text-slate-400">
              <div className="text-5xl mb-4">📭</div>
              <p>No guests match this search.</p>
            </div>
          ) : (
            <>
            <div className="overflow-x-auto max-h-[600px] overflow-y-auto">
              <table className="w-full text-left">
                <thead className="bg-slate-900/80 sticky top-0 border-b border-slate-700">
                  <tr>
                    <th className="px-6 py-4 font-semibold text-sm">Name</th>
                    <th className="px-6 py-4 font-semibold text-sm">Phone</th>
                    <th className="px-6 py-4 font-semibold text-sm">Stays</th>
                    <th className="px-6 py-4 font-semibold text-sm">Total Spend</th>
                    <th className="px-6 py-4 font-semibold text-sm">Last Stay</th>
                    <th className="px-6 py-4 font-semibold text-sm">Flags</th>
                    <th className="px-6 py-4 font-semibold text-sm">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-700">
                  {paged.pageItems.map((g) => (
                    <tr
                      key={g.guest_id}
                      className={`hover:bg-slate-700/30 transition ${g.profile.blacklist ? "opacity-70" : ""}`}
                    >
                      <td className="px-6 py-4 font-medium">{g.name}</td>
                      <td className="px-6 py-4 text-slate-300">{g.phone}</td>
                      <td className="px-6 py-4 text-slate-300">{g.stay_count}</td>
                      <td className="px-6 py-4 text-[#E5C07B] font-bold">₹{(g.total_spend || 0).toLocaleString("en-IN")}</td>
                      <td className="px-6 py-4 text-slate-300">{g.last_stay || "—"}</td>
                      <td className="px-6 py-4 space-x-1">
                        {g.profile.vip && (
                          <span className="px-2 py-1 rounded-full text-xs font-bold border bg-amber-500/20 text-amber-300 border-amber-500/30">★ VIP</span>
                        )}
                        {g.profile.blacklist && (
                          <span className="px-2 py-1 rounded-full text-xs font-bold border bg-red-500/20 text-red-300 border-red-500/30">⛔ Blacklist</span>
                        )}
                        {g.profile.loyalty_points > 0 && (
                          <span className="px-2 py-1 rounded-full text-xs font-bold border bg-emerald-500/20 text-emerald-300 border-emerald-500/30">{g.profile.loyalty_points} pts</span>
                        )}
                      </td>
                      <td className="px-6 py-4">
                        <button
                          onClick={() => openGuest(g.guest_id)}
                          className="bg-blue-600 hover:bg-blue-700 px-3 py-2 rounded-lg text-white font-medium transition inline-flex items-center gap-1"
                        >
                          <FaEdit size={14} /> View
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <Paginator {...paged} />
            </>
          )}
        </div>

        {/* Profile modal */}
        {selected && (
          <div className="fixed inset-0 bg-black/70 backdrop-blur-sm flex items-center justify-center z-50 p-4">
            <div className="bg-gradient-to-br from-slate-800 to-slate-900 rounded-2xl w-full max-w-3xl text-white border border-slate-700 shadow-2xl max-h-[90vh] overflow-y-auto">
              <div className="flex items-center justify-between p-6 border-b border-slate-700">
                <div>
                  <h2 className="text-2xl font-bold text-[#E5C07B]">{selected.guest.name}</h2>
                  <p className="text-slate-400 text-sm">{selected.guest.phone} · {selected.guest.email || "no email"}</p>
                </div>
                <div className="space-x-2">
                  {selected.guest.profile.vip && (
                    <span className="px-2 py-1 rounded-full text-xs font-bold border bg-amber-500/20 text-amber-300 border-amber-500/30">★ VIP</span>
                  )}
                  {selected.guest.profile.blacklist && (
                    <span className="px-2 py-1 rounded-full text-xs font-bold border bg-red-500/20 text-red-300 border-red-500/30">⛔ Blacklist</span>
                  )}
                </div>
              </div>

              {/* stats */}
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 p-6">
                <Stat label="Stays" value={selected.history.stay_count} />
                <Stat label="Total spend" value={`₹${(selected.history.total_spend || 0).toLocaleString("en-IN")}`} />
                <Stat label="Loyalty" value={`${selected.guest.profile.loyalty_points || 0} pts`} icon={<FaGift />} />
                <Stat label="Last stay" value={selected.history.last_stay || "—"} />
              </div>

              {selected.guest.profile.blacklist && (
                <div className="mx-6 mb-4 p-3 rounded-lg bg-red-500/10 border border-red-500/30 text-red-300 text-sm">
                  <b>Blacklisted:</b> {selected.guest.profile.blacklist_reason || "no reason on file"}
                </div>
              )}

              {/* edit form */}
              <div className="px-6 pb-2 grid grid-cols-1 sm:grid-cols-2 gap-4">
                <Field label="Name" value={selected.form.name} onChange={(v) => setForm(setSelected, "name", v)} />
                <Field label="Phone" value={selected.form.phone} onChange={(v) => setForm(setSelected, "phone", v)} />
                <Field label="Email" value={selected.form.email} onChange={(v) => setForm(setSelected, "email", v)} />
                <Field label="Date of birth" type="date" value={selected.form.dob} onChange={(v) => setForm(setSelected, "dob", v)} />
                <Field label="Address" value={selected.form.address} onChange={(v) => setForm(setSelected, "address", v)} />
                <Field label="GSTIN" value={selected.form.gstin} onChange={(v) => setForm(setSelected, "gstin", v)} />
                <div className="sm:col-span-2">
                  <label className="mb-2 text-sm font-semibold text-slate-300 block">Notes</label>
                  <textarea
                    value={selected.form.notes}
                    onChange={(e) => setForm(setSelected, "notes", e.target.value)}
                    rows={2}
                    className="w-full px-4 py-2 bg-slate-900/50 border border-slate-600 rounded-lg text-white focus:outline-none focus:border-[#E5C07B] focus:ring-2 focus:ring-[#E5C07B]/20 transition"
                  />
                </div>
                <label className="flex items-center gap-2 text-slate-300 text-sm">
                  <input
                    type="checkbox"
                    checked={selected.form.marketing_optin}
                    onChange={(e) => setForm(setSelected, "marketing_optin", e.target.checked)}
                  />
                  Marketing opt-in
                </label>
              </div>

              {/* history + complaints */}
              <div className="px-6 py-4">
                <h3 className="text-lg font-bold text-[#E5C07B] mb-2">Stay history</h3>
                {selected.history.bookings.length === 0 ? (
                  <p className="text-slate-400 text-sm">No bookings yet.</p>
                ) : (
                  <div className="space-y-1 max-h-40 overflow-y-auto">
                    {selected.history.bookings.map((b) => (
                      <div key={b.booking_id} className="flex justify-between text-sm bg-slate-900/40 rounded px-3 py-2">
                        <span>#{b.booking_id} · {b.check_in} → {b.check_out} · {b.rooms.join(", ") || "—"}</span>
                        <span className="text-slate-400">{b.status} · ₹{(b.grand_total || 0).toLocaleString("en-IN")}</span>
                      </div>
                    ))}
                  </div>
                )}
                {selected.history.complaints.length > 0 && (
                  <>
                    <h3 className="text-lg font-bold text-[#E5C07B] mt-4 mb-2">
                      Complaints {selected.history.has_open_complaint && <span className="text-red-400 text-sm">(open)</span>}
                    </h3>
                    <div className="space-y-1 max-h-32 overflow-y-auto">
                      {selected.history.complaints.map((c) => (
                        <div key={c.id} className="flex justify-between text-sm bg-slate-900/40 rounded px-3 py-2">
                          <span>{c.issue} <span className="text-slate-500">({c.category})</span></span>
                          <span className={c.is_open ? "text-red-400" : "text-slate-400"}>{c.status}</span>
                        </div>
                      ))}
                    </div>
                  </>
                )}
              </div>

              {/* actions */}
              <div className="flex flex-wrap justify-between gap-3 p-6 border-t border-slate-700">
                <div className="space-x-2">
                  <button
                    onClick={toggleVip}
                    className={`px-4 py-2 rounded-lg font-semibold inline-flex items-center gap-2 transition ${
                      selected.guest.profile.vip
                        ? "bg-amber-600 hover:bg-amber-700 text-white"
                        : "bg-slate-700 hover:bg-slate-600 text-white"
                    }`}
                  >
                    <FaStar size={14} /> {selected.guest.profile.vip ? "Remove VIP" : "Mark VIP"}
                  </button>
                  <button
                    onClick={toggleBlacklist}
                    className={`px-4 py-2 rounded-lg font-semibold inline-flex items-center gap-2 transition ${
                      selected.guest.profile.blacklist
                        ? "bg-slate-700 hover:bg-slate-600 text-white"
                        : "bg-red-600 hover:bg-red-700 text-white"
                    }`}
                  >
                    <FaBan size={14} /> {selected.guest.profile.blacklist ? "Un-blacklist" : "Blacklist"}
                  </button>
                </div>
                <div className="space-x-3">
                  <button
                    onClick={() => setSelected(null)}
                    className="bg-slate-700 hover:bg-slate-600 px-6 py-2 rounded-lg font-medium transition"
                  >
                    Close
                  </button>
                  <button
                    onClick={saveProfile}
                    className="bg-gradient-to-r from-[#E5C07B] to-[#D4AF37] hover:shadow-lg text-slate-900 px-6 py-2 rounded-lg font-bold transition-all hover:scale-105"
                  >
                    Save Changes
                  </button>
                </div>
              </div>
            </div>
          </div>
        )}

        {/* Toast */}
        {toast && (
          <div className="fixed top-6 right-6 z-50">
            <div
              className={`px-6 py-4 rounded-xl shadow-2xl font-medium backdrop-blur ${
                toast.type === "error" ? "bg-red-600/90 text-white" : "bg-green-600/90 text-white"
              }`}
            >
              {toast.type === "error" ? "❌" : "✅"} {toast.message}
            </div>
          </div>
        )}
    </PageShell>
  );
};

function setForm(setSelected, key, value) {
  setSelected((s) => ({ ...s, form: { ...s.form, [key]: value } }));
}

const Stat = ({ label, value, icon }) => (
  <div className="bg-slate-900/40 border border-slate-700 rounded-xl px-4 py-3">
    <div className="text-slate-400 text-xs flex items-center gap-1">{icon} {label}</div>
    <div className="text-lg font-bold text-white">{value}</div>
  </div>
);

const Field = ({ label, value, onChange, type = "text" }) => (
  <div className="flex flex-col">
    <label className="mb-2 text-sm font-semibold text-slate-300">{label}</label>
    <input
      type={type}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="px-4 py-2 bg-slate-900/50 border border-slate-600 rounded-lg text-white focus:outline-none focus:border-[#E5C07B] focus:ring-2 focus:ring-[#E5C07B]/20 transition"
    />
  </div>
);

export default GuestDirectory;
