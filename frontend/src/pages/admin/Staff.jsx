// Duty roster, attendance and staff performance (prompt 18, slices 12 & 9).
import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  FaCopy, FaFileCsv, FaFilePdf, FaIdCard, FaPlus, FaSearch, FaSync,
} from "react-icons/fa";
import * as api from "../../api/backoffice";
import { useConfirm } from "../../components/ConfirmDialog";
import {
  Card, Chip, DataTable, Field, GhostButton, Modal, PageShell, PrimaryButton, SelectField,
  Spinner, Stat, Tabs, daysAgo, fmtDate, inputCls, money, today, useToast,
} from "../../components/admin/BackofficeUI";

const TABS = [
  ["roster", "Duty roster"],
  ["attendance", "Attendance"],
  ["performance", "Performance"],
  ["cards", "Staff cards"],
];

const SHIFT_TYPES = ["morning", "evening", "night", "general"];
const SHIFT_TONE = { morning: "gold", evening: "info", night: "neutral", general: "ok" };
const DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

/** Monday of the week containing `d` — rosters here run Mon–Sun. */
function weekStartOf(d) {
  const date = new Date(d);
  const day = (date.getDay() + 6) % 7; // Mon = 0
  date.setDate(date.getDate() - day);
  return date.toISOString().slice(0, 10);
}
const addDays = (iso, n) =>
  new Date(new Date(iso).getTime() + n * 86400000).toISOString().slice(0, 10);

export default function Staff() {
  const [tab, setTab] = useState("roster");
  const [toast, showToast] = useToast();
  const [staff, setStaff] = useState([]);

  const loadStaff = useCallback(async () => {
    try {
      const res = await api.listStaff();
      setStaff(res.data || []);
    } catch (e) {
      showToast(e.message || "Failed to load staff");
    }
  }, [showToast]);

  useEffect(() => { loadStaff(); /* eslint-disable-next-line */ }, []);

  return (
    <PageShell
      icon="👥"
      title="Staff, Roster & Attendance"
      subtitle="Plan who is on duty, see who actually turned up, and compare it against what each person sold."
      toast={toast}
    >
      <Tabs tabs={TABS} active={tab} onChange={setTab} />
      {tab === "roster" && <RosterTab staff={staff} showToast={showToast} />}
      {tab === "attendance" && <AttendanceTab staff={staff} showToast={showToast} />}
      {tab === "performance" && <PerformanceTab showToast={showToast} />}
      {tab === "cards" && <CardsTab staff={staff} reload={loadStaff} showToast={showToast} />}
    </PageShell>
  );
}

/* ------------------------------------------------------------------ roster */

function RosterTab({ staff, showToast }) {
  const [week, setWeek] = useState(weekStartOf(today()));
  const [data, setData] = useState(null);
  const [coverage, setCoverage] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [adding, setAdding] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [roster, cov] = await Promise.all([
        api.getRoster(week),
        api.getCoverage(week).catch(() => null),   // fail-soft: coverage must not break the grid
      ]);
      setData(roster);
      setCoverage(cov);
    } catch (e) {
      setError(e.message || "Failed to load the roster");
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [week]);

  useEffect(() => { load(); /* eslint-disable-next-line */ }, [week]);

  /** shifts keyed by `${user_id}|${date}` so the grid is a straight lookup. */
  const grid = useMemo(() => {
    const map = {};
    (data?.data || []).forEach((s) => {
      (map[`${s.user_id}|${s.shift_date}`] ||= []).push(s);
    });
    return map;
  }, [data]);

  const rosteredStaff = useMemo(() => {
    const ids = new Set((data?.data || []).map((s) => s.user_id));
    // Everyone on duty this week first, then the rest so they can be assigned.
    return [...staff].sort((a, b) => (ids.has(b.user_id) ? 1 : 0) - (ids.has(a.user_id) ? 1 : 0));
  }, [staff, data]);

  const { confirm } = useConfirm();

  const copyForward = async () => {
    if (!(await confirm({
      title: "Copy this week's duties forward?",
      message: `Into the week of ${fmtDate(addDays(week, 7))}.`,
      confirmText: "Copy",
    }))) return;
    try {
      const res = await api.copyRosterWeek({
        source_week_start: week, target_week_start: addDays(week, 7), overwrite: false,
      });
      showToast(`${res.created} duty(ies) copied, ${res.skipped} already existed`);
      setWeek(addDays(week, 7));
    } catch (e) {
      showToast(e.message || "Copy failed");
    }
  };

  const removeShift = async (s) => {
    if (!(await confirm({
      title: "Remove duty?",
      message: `${s.staff_name}'s ${s.shift_type} duty on ${fmtDate(s.shift_date)}.`,
      confirmText: "Remove", tone: "danger",
    }))) return;
    try {
      await api.deleteShift(s.id);
      showToast("Duty removed");
      load();
    } catch (e) {
      showToast(e.message || "Could not remove the duty");
    }
  };

  return (
    <>
      <Card className="mb-6">
        <div className="p-5 grid grid-cols-1 md:grid-cols-5 gap-4 items-end">
          <Field label="Week of" type="date" value={week}
            onChange={(e) => setWeek(weekStartOf(e.target.value))} />
          <GhostButton onClick={() => setWeek(addDays(week, -7))}>← Previous</GhostButton>
          <GhostButton onClick={() => setWeek(addDays(week, 7))}>Next →</GhostButton>
          <GhostButton onClick={copyForward}><FaCopy size={14} /> Copy to next week</GhostButton>
          <PrimaryButton onClick={() => setAdding({ shift_date: week })}>
            <FaPlus size={14} /> Add duty
          </PrimaryButton>
        </div>
      </Card>

      <Card
        title={`Week of ${fmtDate(week)}`}
        right={data && <span className="text-slate-400 text-sm">{data.total} duty(ies)</span>}
        className="mb-6"
      >
        {loading ? (
          <Spinner className="p-12" />
        ) : error ? (
          <div className="p-8 text-center text-red-300">{error}</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="bg-slate-900/80 border-b border-slate-700">
                <tr>
                  <th className="px-4 py-3 font-semibold">Staff</th>
                  {(data?.dates || []).map((d, i) => (
                    <th key={d} className="px-3 py-3 font-semibold whitespace-nowrap">
                      {DAY_NAMES[i]} <span className="text-slate-500 font-normal">{d.slice(8)}</span>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-700">
                {rosteredStaff.map((u) => (
                  <tr key={u.user_id} className="hover:bg-slate-700/20 transition">
                    <td className="px-4 py-2.5">
                      <div className="font-semibold text-slate-100">{u.full_name || u.username}</div>
                      <div className="text-xs text-slate-500">{u.role}</div>
                    </td>
                    {(data?.dates || []).map((d) => {
                      const cell = grid[`${u.user_id}|${d}`] || [];
                      return (
                        <td key={d} className="px-3 py-2 align-top">
                          <div className="flex flex-col gap-1">
                            {cell.map((s) => (
                              <button key={s.id} onClick={() => removeShift(s)} title="Click to remove"
                                className="text-left">
                                <Chip tone={s.status === "absent" ? "danger" : SHIFT_TONE[s.shift_type]}>
                                  {s.shift_type}{s.status === "absent" ? " · absent" : ""}
                                </Chip>
                              </button>
                            ))}
                            {cell.length === 0 && (
                              <button
                                onClick={() => setAdding({ user_id: u.user_id, shift_date: d })}
                                className="text-slate-600 hover:text-[#E5C07B] text-xs transition">
                                + add
                              </button>
                            )}
                          </div>
                        </td>
                      );
                    })}
                  </tr>
                ))}
                {rosteredStaff.length === 0 && (
                  <tr><td colSpan={8} className="p-12 text-center text-slate-400">
                    No staff accounts yet — create them in User Management first.
                  </td></tr>
                )}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {coverage?.data?.length > 0 && (
        <Card title="Planned vs actually worked">
          <DataTable
            columns={["Staff", "Planned", "Absent", "Sessions worked", "Hours", "Drawers opened", "No-shows", "Unplanned"]}
            rows={coverage.data}
            empty="Nothing rostered or worked this week."
            renderRow={(r) => [
              r.staff_name, r.planned, r.absent, r.worked_sessions, r.hours, r.drawers_opened,
              r.no_shows > 0 ? <Chip key="n" tone="danger">{r.no_shows}</Chip> : "0",
              r.unplanned > 0 ? <Chip key="u" tone="warn">{r.unplanned}</Chip> : "0",
            ]}
          />
          <p className="px-5 py-4 text-slate-500 text-xs border-t border-slate-700">
            ⓘ A planned duty with no attendance is a no-show; attendance with no duty is unplanned
            overtime. "Drawers opened" links the cash shift to the duty that was planned.
          </p>
        </Card>
      )}

      {adding && (
        <ShiftForm value={adding} staff={staff} onClose={() => setAdding(null)}
          showToast={showToast} onSaved={() => { setAdding(null); load(); }} />
      )}
    </>
  );
}

function ShiftForm({ value, staff, onClose, onSaved, showToast }) {
  const [form, setForm] = useState({
    user_id: value.user_id || staff[0]?.user_id || "",
    shift_date: value.shift_date || today(),
    shift_type: "general", start_time: "", end_time: "", role_label: "", notes: "",
  });
  const [saving, setSaving] = useState(false);

  const save = async () => {
    if (!form.user_id) { showToast("Pick a staff member"); return; }
    setSaving(true);
    try {
      const payload = {
        user_id: Number(form.user_id),
        shift_date: form.shift_date,
        shift_type: form.shift_type,
      };
      if (form.start_time) payload.start_time = form.start_time;
      if (form.end_time) payload.end_time = form.end_time;
      if (form.role_label) payload.role_label = form.role_label;
      if (form.notes) payload.notes = form.notes;
      await api.createShift(payload);
      showToast("Duty added");
      onSaved();
    } catch (e) {
      showToast(e.message || "Could not add the duty");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal title="Add a duty" onClose={onClose}>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <SelectField label="Staff member *" value={form.user_id}
          onChange={(e) => setForm({ ...form, user_id: e.target.value })}
          options={staff.map((u) => ({ value: u.user_id, label: `${u.full_name || u.username} (${u.role})` }))} />
        <Field label="Date *" type="date" value={form.shift_date}
          onChange={(e) => setForm({ ...form, shift_date: e.target.value })} />
        <SelectField label="Shift" value={form.shift_type}
          onChange={(e) => setForm({ ...form, shift_type: e.target.value })} options={SHIFT_TYPES} />
        <Field label="Role / area" value={form.role_label} hint="e.g. Front desk, Housekeeping floor 2"
          onChange={(e) => setForm({ ...form, role_label: e.target.value })} />
        <Field label="Start time" type="time" value={form.start_time}
          onChange={(e) => setForm({ ...form, start_time: e.target.value })} />
        <Field label="End time" type="time" value={form.end_time}
          onChange={(e) => setForm({ ...form, end_time: e.target.value })} />
      </div>
      <div className="flex gap-3 mt-6">
        <PrimaryButton onClick={save} disabled={saving}>{saving ? "Saving…" : "Add duty"}</PrimaryButton>
        <GhostButton onClick={onClose}>Cancel</GhostButton>
      </div>
    </Modal>
  );
}

/* ------------------------------------------------------------------ attendance */

function AttendanceTab({ staff, showToast }) {
  const [range, setRange] = useState({ from: daysAgo(29), to: today() });
  const [userId, setUserId] = useState("");
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [adding, setAdding] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      setData(await api.listAttendance({ ...range, ...(userId ? { user_id: userId } : {}) }));
    } catch (e) {
      setError(e.message || "Failed to load attendance");
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [range, userId]);

  useEffect(() => { load(); /* eslint-disable-next-line */ }, []);

  return (
    <>
      <Card className="mb-6">
        <div className="p-5 grid grid-cols-1 md:grid-cols-5 gap-4 items-end">
          <Field label="From" type="date" value={range.from}
            onChange={(e) => setRange({ ...range, from: e.target.value })} />
          <Field label="To" type="date" value={range.to}
            onChange={(e) => setRange({ ...range, to: e.target.value })} />
          <SelectField label="Staff member" value={userId} onChange={(e) => setUserId(e.target.value)}
            options={[{ value: "", label: "Everyone" },
              ...staff.map((u) => ({ value: u.user_id, label: u.full_name || u.username }))]} />
          <PrimaryButton onClick={load}><FaSearch size={14} /> Apply</PrimaryButton>
          <PrimaryButton onClick={() => setAdding(true)}><FaPlus size={14} /> Record a session</PrimaryButton>
        </div>
        <p className="px-5 pb-5 text-slate-500 text-xs">
          ⓘ Staff clock in and out at the front desk with their PIN (or a staff card once card
          attendance is enabled). Use "Record a session" only to fix a missed punch — corrections are audited.
        </p>
      </Card>

      {data && (
        <div className="grid grid-cols-2 md:grid-cols-3 gap-4 mb-6">
          <Stat label="Sessions in period" value={data.total} />
          <Stat label="On duty right now" value={data.on_duty_now}
            tone={data.on_duty_now > 0 ? "text-emerald-300" : "text-slate-400"} />
          <Stat label="Staff with hours" value={data.by_staff?.length || 0} />
        </div>
      )}

      {data?.by_staff?.length > 0 && (
        <Card title="Hours by staff member" className="mb-6">
          <DataTable
            columns={["Staff", "Sessions", "Hours"]}
            rows={data.by_staff}
            renderRow={(r) => [r.staff_name, r.sessions, r.hours]}
          />
        </Card>
      )}

      <Card title="Sessions">
        <DataTable
          columns={["Staff", "Clock in", "Clock out", "Hours", "Source", "Station", "Duty", "Note"]}
          rows={data?.data} loading={loading} error={error}
          empty="No attendance recorded in this period."
          renderRow={(r) => [
            r.staff_name,
            r.clock_in ? `${fmtDate(r.clock_in)} ${r.clock_in.slice(11, 16)}` : "—",
            r.clock_out ? `${fmtDate(r.clock_out)} ${r.clock_out.slice(11, 16)}` : (
              <Chip key="o" tone="ok">on duty</Chip>
            ),
            r.hours_worked || "—",
            <Chip key="s" tone={r.source === "card" ? "gold" : r.source === "admin" ? "info" : "neutral"}>
              {r.source}
            </Chip>,
            r.station_id,
            r.roster_shift_id ? <Chip key="d" tone="ok">planned</Chip> : <Chip key="d" tone="warn">unplanned</Chip>,
            r.note,
          ]}
        />
      </Card>

      {adding && (
        <ManualAttendanceForm staff={staff} onClose={() => setAdding(false)} showToast={showToast}
          onSaved={() => { setAdding(false); load(); }} />
      )}
    </>
  );
}

function ManualAttendanceForm({ staff, onClose, onSaved, showToast }) {
  const [form, setForm] = useState({
    user_id: staff[0]?.user_id || "", clock_in: "", clock_out: "", note: "",
  });
  const [saving, setSaving] = useState(false);

  const save = async () => {
    if (!form.user_id || !form.clock_in) { showToast("Staff member and clock-in time are required"); return; }
    if (form.clock_out && form.clock_out <= form.clock_in) {
      showToast("Clock-out must be after clock-in");
      return;
    }
    setSaving(true);
    try {
      await api.recordAttendance({
        user_id: Number(form.user_id),
        clock_in: form.clock_in,
        clock_out: form.clock_out || null,
        note: form.note || null,
      });
      showToast("Session recorded");
      onSaved();
    } catch (e) {
      showToast(e.message || "Could not record the session");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal title="Record a worked session" onClose={onClose}>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <SelectField label="Staff member *" value={form.user_id}
          onChange={(e) => setForm({ ...form, user_id: e.target.value })}
          options={staff.map((u) => ({ value: u.user_id, label: u.full_name || u.username }))} />
        <Field label="Note" value={form.note} hint="e.g. missed punch"
          onChange={(e) => setForm({ ...form, note: e.target.value })} />
        <Field label="Clock in *" type="datetime-local" value={form.clock_in}
          onChange={(e) => setForm({ ...form, clock_in: e.target.value })} />
        <Field label="Clock out" type="datetime-local" value={form.clock_out}
          hint="Leave blank to leave the session open."
          onChange={(e) => setForm({ ...form, clock_out: e.target.value })} />
      </div>
      <div className="flex gap-3 mt-6">
        <PrimaryButton onClick={save} disabled={saving}>{saving ? "Saving…" : "Record session"}</PrimaryButton>
        <GhostButton onClick={onClose}>Cancel</GhostButton>
      </div>
    </Modal>
  );
}

/* ------------------------------------------------------------------ performance */

function PerformanceTab({ showToast }) {
  const [range, setRange] = useState({ from: daysAgo(29), to: today() });
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      setData(await api.getStaffPerformance(range));
    } catch (e) {
      setError(e.message || "Failed to load the report");
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [range]);

  useEffect(() => { load(); /* eslint-disable-next-line */ }, []);

  const doExport = async (format) => {
    try {
      await api.exportStaffPerformance(range, format);
    } catch (e) {
      showToast(e.message || "Export failed");
    }
  };

  const t = data?.totals;

  return (
    <>
      <Card className="mb-6">
        <div className="p-5 grid grid-cols-1 md:grid-cols-5 gap-4 items-end">
          <Field label="From" type="date" value={range.from}
            onChange={(e) => setRange({ ...range, from: e.target.value })} />
          <Field label="To" type="date" value={range.to}
            onChange={(e) => setRange({ ...range, to: e.target.value })} />
          <PrimaryButton onClick={load}><FaSync size={14} /> Apply</PrimaryButton>
          <GhostButton onClick={() => doExport("csv")}><FaFileCsv size={14} /> CSV</GhostButton>
          <GhostButton onClick={() => doExport("pdf")}><FaFilePdf size={14} /> PDF</GhostButton>
        </div>
        <p className="px-5 pb-5 text-slate-500 text-xs">
          ⓘ Every figure here is a by-product of normal work the system already records — nobody fills
          in a timesheet. Read discounts, voids and cash variance next to revenue.
        </p>
      </Card>

      {t && (
        <div className="grid grid-cols-2 md:grid-cols-5 gap-4 mb-6">
          <Stat label="Revenue posted" value={money(t.charge_revenue)} />
          <Stat label="Upsells" value={money(t.upsell_revenue)} tone="text-emerald-300" />
          <Stat label="Discounts given" value={money(t.discount_value)} tone="text-amber-300" />
          <Stat label="Voids" value={money(t.void_value)} tone="text-red-300" />
          <Stat label="Hours worked" value={t.hours_worked} />
        </div>
      )}

      <Card title="Per staff member">
        <DataTable
          columns={["Staff", "Role", "Bookings", "Room nights", "Revenue", "Upsells", "Discounts",
            "Voids", "Cards", "Shifts", "Cash variance", "Complaints", "Hours", "Planned", "No-shows"]}
          rows={data?.rows} loading={loading} error={error}
          empty="No staff activity in this period."
          renderRow={(r) => [
            <span key="n" className="font-semibold text-slate-100">{r.staff_name}</span>,
            r.role,
            r.bookings_created,
            r.room_nights,
            money(r.charge_revenue),
            <span key="u" className="text-emerald-300">{money(r.upsell_revenue)}</span>,
            r.discount_value > 0 ? <span key="d" className="text-amber-300">{money(r.discount_value)}</span> : "—",
            r.void_value > 0 ? <span key="v" className="text-red-300">{money(r.void_value)}</span> : "—",
            r.cards_issued,
            r.shifts_closed,
            r.cash_variance !== 0
              ? <Chip key="cv" tone={Math.abs(r.cash_variance) > 100 ? "danger" : "warn"}>
                  {money(r.cash_variance)}
                </Chip>
              : "—",
            r.complaints_handled,
            r.hours_worked,
            r.planned_duties,
            r.no_shows > 0 ? <Chip key="ns" tone="danger">{r.no_shows}</Chip> : "0",
          ]}
        />
      </Card>
    </>
  );
}

/* ------------------------------------------------------------------ staff cards */

function CardsTab({ staff, reload, showToast }) {
  const [saving, setSaving] = useState(null);
  const [drafts, setDrafts] = useState({});

  const assign = async (u) => {
    setSaving(u.user_id);
    try {
      await api.assignStaffCard(u.user_id, (drafts[u.user_id] || "").trim());
      showToast(drafts[u.user_id] ? `Card bound to ${u.full_name || u.username}` : "Card unbound");
      setDrafts((d) => ({ ...d, [u.user_id]: "" }));
      reload();
    } catch (e) {
      showToast(e.message || "Could not assign the card");
    } finally {
      setSaving(null);
    }
  };

  return (
    <>
      <Card className="mb-6">
        <div className="p-5">
          <h3 className="text-[#E5C07B] font-bold mb-2 flex items-center gap-2">
            <FaIdCard /> Staff key-card attendance
          </h3>
          <p className="text-slate-400 text-sm">
            Bind a card UID to a staff member and they can clock in and out with a tap instead of a PIN.
          </p>
          <p className="text-amber-300/90 text-sm mt-3">
            ⚠️ <b>One hardware check is still outstanding.</b> The card endpoint works and the encoder
            can already read a card. What nobody has confirmed on real hardware is whether a staff
            card's payload is <b>stable and unique per card</b> — it may encode the role or floor
            rather than a serial. To confirm: bind a card's UID below, read the <i>same</i> card again
            (it should match) and a <i>different</i> staff card (it should differ). Until then the
            desk has no card-punch button and <b>PIN clock-in is the supported path</b>.
          </p>
        </div>
      </Card>

      <Card title="Staff">
        <DataTable
          columns={["Staff", "Role", "PIN set", "Card bound", "Assign / change card UID", ""]}
          rows={staff}
          empty="No staff accounts yet."
          renderRow={(u) => [
            <span key="n" className="font-semibold text-slate-100">{u.full_name || u.username}</span>,
            u.role,
            u.has_pin ? <Chip key="p" tone="ok">yes</Chip> : <Chip key="p" tone="warn">no PIN</Chip>,
            u.has_staff_card ? <Chip key="c" tone="gold">bound</Chip> : <Chip key="c" tone="neutral">—</Chip>,
            <input key="i" className={inputCls} placeholder="Card UID (blank to unbind)"
              value={drafts[u.user_id] ?? ""}
              onChange={(e) => setDrafts({ ...drafts, [u.user_id]: e.target.value })} />,
            <GhostButton key="b" onClick={() => assign(u)} disabled={saving === u.user_id}>
              {saving === u.user_id ? "Saving…" : "Save"}
            </GhostButton>,
          ]}
        />
      </Card>
    </>
  );
}
