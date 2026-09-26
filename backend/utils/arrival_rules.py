"""Arrival & departure rules (v5m) — the owner's policy for early check-in, late arrival and hourly
(late-checkout) extensions, keyed by booking source and editable in the admin Settings page.

One JSON setting, `arrival_rules`:

    {"exempt_comp": true,
     "early_checkin":     [ {sources, threshold_minutes, full_night_after_minutes, charge, approval}, ... ],
     "late_arrival":      [ {sources, threshold_minutes}, ... ],
     "hourly_extension":  [ {sources, threshold_minutes, full_night_after_minutes, max_hours, overflow,
                             charge, approval}, ... ]}

* `sources` — booking sources the row applies to; `"*"` is the default row. First match wins.
* `threshold_minutes` — deviation up to this is free / ignored (a guest 2 minutes early is not "early").
* `full_night_after_minutes` — beyond this the deviation is treated as a whole night ("too early" ->
  an extra night is posted; "too late" extension -> refused or a full night, per `overflow`).
* `charge` — {"mode": "free" | "fixed" (amount, Rs) | "percent" (percent of one night's GST-inclusive rate)}.
* `approval` — "always" | "on_change" (only when the desk edits the computed amount / makes it free) |
  "never"; satisfied by an admin login or an owner OTP (action `arrival_fee` / `extend_hours`).
* `exempt_comp` — a fully complimentary stay is never charged (the event is still recorded).

Evaluation is pure (no DB writes) so the desk can dry-run it ("Early by 2 h 10 m -> Rs 300").
"""
import json
import logging
from copy import deepcopy
from datetime import datetime, timedelta
from utils import clock          # F-03: one clock - see utils/clock.py

logger = logging.getLogger(__name__)

RULES_KEY = "arrival_rules"
OTA_SOURCES_DEFAULT = ("makemytrip", "goibibo", "booking_com", "agoda", "yatra", "other_ota")

DEFAULT_RULES = {
    "exempt_comp": True,
    "early_checkin": [
        {"sources": list(OTA_SOURCES_DEFAULT) + ["website"],
         "threshold_minutes": 30, "full_night_after_minutes": 360,
         "charge": {"mode": "fixed", "amount": 300}, "approval": "on_change"},
        {"sources": ["*"], "threshold_minutes": 120, "full_night_after_minutes": 480,
         "charge": {"mode": "percent", "percent": 25}, "approval": "on_change"},
    ],
    "late_arrival": [{"sources": ["*"], "threshold_minutes": 120}],
    "hourly_extension": [
        {"sources": ["*"], "threshold_minutes": 30, "full_night_after_minutes": 360,
         "max_hours": 8, "overflow": "refuse",
         "charge": {"mode": "percent", "percent": 25}, "approval": "on_change"},
    ],
}

CHARGE_MODES = ("free", "fixed", "percent")
APPROVALS = ("always", "on_change", "never")
OVERFLOWS = ("refuse", "full_night")


# ------------------------------------------------------------------ load / validate

def load_rules(db) -> dict:
    """The active rule set (setting -> defaults), normalised so every consumer sees full rows."""
    from utils.settings import get_setting
    raw = get_setting(db, RULES_KEY)
    rules = None
    if raw:
        try:
            rules = json.loads(raw)
        except (ValueError, TypeError):
            logger.warning("arrival_rules setting is not valid JSON — using defaults")
    return normalise(rules or {})


def normalise(rules: dict) -> dict:
    """Fill gaps with defaults and coerce types; never raises on odd input."""
    out = deepcopy(DEFAULT_RULES)
    if not isinstance(rules, dict):
        return out
    out["exempt_comp"] = bool(rules.get("exempt_comp", True))
    for kind in ("early_checkin", "late_arrival", "hourly_extension"):
        rows = rules.get(kind)
        if isinstance(rows, list) and rows:
            out[kind] = [_norm_row(kind, r) for r in rows if isinstance(r, dict)]
            if not any("*" in r["sources"] for r in out[kind]):
                out[kind].append(_norm_row(kind, DEFAULT_RULES[kind][-1]))
    return out


def _norm_row(kind: str, r: dict) -> dict:
    srcs = r.get("sources") or ["*"]
    if isinstance(srcs, str):
        srcs = [srcs]
    row = {"sources": [str(s).strip().lower() for s in srcs if str(s).strip()] or ["*"],
           "threshold_minutes": max(0, int(_num(r.get("threshold_minutes"), 0)))}
    if kind == "late_arrival":
        return row
    row["full_night_after_minutes"] = max(row["threshold_minutes"],
                                          int(_num(r.get("full_night_after_minutes"), 360)))
    ch = r.get("charge") if isinstance(r.get("charge"), dict) else {}
    mode = str(ch.get("mode", "free")).lower()
    row["charge"] = {"mode": mode if mode in CHARGE_MODES else "free",
                     "amount": round(max(0.0, _num(ch.get("amount"), 0.0)), 2),
                     "percent": round(min(100.0, max(0.0, _num(ch.get("percent"), 0.0))), 2)}
    ap = str(r.get("approval", "on_change")).lower()
    row["approval"] = ap if ap in APPROVALS else "on_change"
    if kind == "hourly_extension":
        row["max_hours"] = max(1, int(_num(r.get("max_hours"), 8)))
        ov = str(r.get("overflow", "refuse")).lower()
        row["overflow"] = ov if ov in OVERFLOWS else "refuse"
    return row


def validate(rules: dict) -> list[str]:
    """Human-readable problems for the admin editor (empty list = OK)."""
    errs = []
    if not isinstance(rules, dict):
        return ["rules must be an object"]
    for kind in ("early_checkin", "late_arrival", "hourly_extension"):
        rows = rules.get(kind)
        if not isinstance(rows, list) or not rows:
            errs.append(f"{kind}: at least one rule row is required")
            continue
        if not any("*" in (r.get("sources") or []) for r in rows if isinstance(r, dict)):
            errs.append(f"{kind}: one row must apply to all sources (\"*\")")
        for i, r in enumerate(rows, 1):
            if not isinstance(r, dict):
                errs.append(f"{kind} row {i}: not an object"); continue
            t = _num(r.get("threshold_minutes"), 0)
            if t < 0:
                errs.append(f"{kind} row {i}: threshold must be >= 0")
            if kind != "late_arrival":
                b = _num(r.get("full_night_after_minutes"), 0)
                if b < t:
                    errs.append(f"{kind} row {i}: full-night bound must be >= threshold")
                ch = r.get("charge") or {}
                if str(ch.get("mode", "free")).lower() not in CHARGE_MODES:
                    errs.append(f"{kind} row {i}: charge mode must be free / fixed / percent")
                if str(ch.get("mode")) == "percent" and not (0 <= _num(ch.get("percent"), -1) <= 100):
                    errs.append(f"{kind} row {i}: percent must be 0-100")
                if str(ch.get("mode")) == "fixed" and _num(ch.get("amount"), -1) < 0:
                    errs.append(f"{kind} row {i}: amount must be >= 0")
                if str(r.get("approval", "on_change")).lower() not in APPROVALS:
                    errs.append(f"{kind} row {i}: approval must be always / on_change / never")
            if kind == "hourly_extension" and _num(r.get("max_hours"), 1) < 1:
                errs.append(f"{kind} row {i}: max hours must be >= 1")
    return errs


def rule_for(rules: dict, kind: str, source: str | None) -> dict:
    src = (source or "direct").strip().lower()
    rows = rules.get(kind) or []
    for r in rows:
        if src in r["sources"]:
            return r
    for r in rows:
        if "*" in r["sources"]:
            return r
    return _norm_row(kind, DEFAULT_RULES[kind][-1])


# ------------------------------------------------------------------ evaluation

def charge_for(rule: dict, night_rate: float, units: float = 1.0) -> tuple[float, str]:
    """(amount, basis) for one application of a rule.

    `night_rate` is one night of the rooms BEING CHARGED, GST-inclusive — so a percent rule already
    scales with how many rooms are involved. `units` is how many rooms a FIXED amount applies to:
    a ₹300 early-check-in fee on a three-room group that all arrived early is ₹900, and ₹300 when
    only one of the three came early (v5n — the owner's rule: charge the room that actually did it).
    """
    ch = rule.get("charge") or {}
    mode = ch.get("mode", "free")
    if mode == "fixed":
        return round(float(ch.get("amount", 0)) * units, 2), "fixed"
    if mode == "percent":
        return round(float(night_rate) * float(ch.get("percent", 0)) / 100.0, 2), "percent"
    return 0.0, "free"


def needs_approval(rule: dict, quoted: float, applied: float | None) -> bool:
    ap = rule.get("approval", "on_change")
    if ap == "always":
        return True
    if ap == "never":
        return False
    return applied is not None and round(float(applied), 2) != round(float(quoted), 2)


def evaluate_early(rules: dict, source: str, expected_at: datetime, now: datetime,
                   night_rate: float, comp: bool, rooms: int = 1) -> dict:
    """Early check-in: how early, what it costs, whether an extra night is needed."""
    rule = rule_for(rules, "early_checkin", source)
    dev = int((expected_at - now).total_seconds() // 60) if expected_at else 0   # minutes early (+)
    out = {"kind": "early_checkin", "deviation_minutes": max(0, dev), "rule": rule,
           "charge": 0.0, "basis": "free", "full_night": False, "approval": rule["approval"],
           "exempt": False}
    if dev <= 0:
        out["basis"] = "on_time"
        return out
    if comp and rules.get("exempt_comp", True):
        out.update(basis="exempt_comp", exempt=True)
        return out
    if dev <= rule["threshold_minutes"]:
        return out                                            # within tolerance: free
    if dev > rule["full_night_after_minutes"]:
        out.update(full_night=True, charge=round(float(night_rate), 2), basis="full_night")
        return out
    amt, basis = charge_for(rule, night_rate, units=max(1, int(rooms)))
    out.update(charge=amt, basis=basis)
    return out


def evaluate_late_arrival(rules: dict, source: str, expected_at: datetime, now: datetime) -> dict:
    """Late arrival: how late, and where the stay clock starts (actual vs expected)."""
    rule = rule_for(rules, "late_arrival", source)
    dev = int((now - expected_at).total_seconds() // 60) if expected_at else 0      # minutes late (+)
    if dev <= 0:
        return {"kind": "late_arrival", "deviation_minutes": 0, "rule": rule, "stay_start": now,
                "from_expected": False}
    from_expected = dev > rule["threshold_minutes"]
    return {"kind": "late_arrival", "deviation_minutes": dev, "rule": rule,
            "stay_start": expected_at if from_expected else now, "from_expected": from_expected}


def evaluate_extension(rules: dict, source: str, current_checkout: datetime, hours: int,
                       night_rate: float, comp: bool, rooms: int = 1) -> dict:
    """Hourly extension: new checkout moment, cost, or a refusal beyond the bound."""
    rule = rule_for(rules, "hourly_extension", source)
    hours = int(hours)
    out = {"kind": "hourly_extension", "hours": hours, "rule": rule, "until": None,
           "charge": 0.0, "basis": "free", "approval": rule["approval"], "refused": None, "exempt": False,
           "full_night": False}
    if hours < 1:
        out["refused"] = "hours must be at least 1"
        return out
    if hours > rule["max_hours"]:
        out["refused"] = f"more than {rule['max_hours']} hours — extend the stay by a night instead"
        return out
    minutes = hours * 60
    until = current_checkout + timedelta(hours=hours)
    out["until"] = until
    if comp and rules.get("exempt_comp", True):
        out.update(basis="exempt_comp", exempt=True)
        return out
    if minutes <= rule["threshold_minutes"]:
        return out
    if minutes > rule["full_night_after_minutes"]:
        if rule.get("overflow") == "full_night":
            out.update(full_night=True, charge=round(float(night_rate), 2), basis="full_night")
            return out
        out["refused"] = (f"beyond {rule['full_night_after_minutes'] // 60} h counts as a night — "
                          "extend the stay by a night instead")
        return out
    # One fee per extension, not per hour — but per ROOM staying late (v5n).
    amt, basis = charge_for(rule, night_rate, units=max(1, int(rooms)))
    out.update(charge=amt, basis=basis)
    return out


def rule_snapshot(rule: dict) -> str:
    return json.dumps(rule, default=str)


# ------------------------------------------------------------------ reporting (v5m §H)

KIND_LABELS = {"early_checkin": "Early check-in", "late_arrival": "Late arrival",
               "hourly_extension": "Hourly extension"}
BASIS_LABELS = {"free": "Free", "fixed": "Fixed fee", "percent": "% of night", "full_night": "Full night",
                "exempt_comp": "Comp (exempt)", "override": "Desk override", "voided": "Voided",
                "on_time": "On time", "late": "Late"}


def stay_events_report(db, date_from, date_to, kind: str | None = None) -> dict:
    """Rows + totals for the arrival-exceptions report, from stay_events over [date_from, date_to]
    (by created_at). Also feeds the owner's daily report section and the night-audit summary."""
    from sqlalchemy import func
    from models import StayEvent, Booking, Guest, BookingItem, Room, User
    q = (db.query(StayEvent, Booking, Guest)
           .join(Booking, Booking.booking_id == StayEvent.booking_id)
           .outerjoin(Guest, Guest.guest_id == Booking.guest_id)
           .filter(clock.business_date_sql(StayEvent.created_at) >= date_from,
                   clock.business_date_sql(StayEvent.created_at) <= date_to))
    if kind:
        q = q.filter(StayEvent.kind == kind)
    rows, totals = [], {"early_checkin": {"count": 0, "charged": 0.0},
                        "late_arrival": {"count": 0, "charged": 0.0},
                        "hourly_extension": {"count": 0, "charged": 0.0}}
    users = {}
    for ev, b, g in q.order_by(StayEvent.created_at).all():
        # v5n: an event belongs to ONE room when the fee was charged per room, so name that room
        # rather than the whole stay — "which room did we charge?" is the question this report answers.
        if ev.room_id:
            one = db.query(Room.room_number).filter(Room.room_id == ev.room_id).scalar()
            rooms = [one] if one else []
        else:
            # A stay-level event (no single room): name every room on the booking.
            # ⚠️ `for r, in ...` was unpacking a one-column Row, and `.with_entities(Room)` returns
            # Room OBJECTS, not tuples - so this raised `cannot unpack non-iterable Room object`
            # the first time a booking-level event reached the report. It had never been hit
            # because v5n's per-room fees always set `room_id`; the v6c per-room work finally
            # produced an event without one, and the whole Arrival Exceptions page 500'd.
            rooms = [r.room_number for r in
                     db.query(Room).join(BookingItem, BookingItem.room_id == Room.room_id)
                     .filter(BookingItem.booking_id == b.booking_id).all()]
        approver = None
        if ev.approved_by:
            if ev.approved_by not in users:
                u = db.query(User).filter(User.user_id == ev.approved_by).first()
                users[ev.approved_by] = u.username if u else str(ev.approved_by)
            approver = users[ev.approved_by]
        try:
            rule = json.loads(ev.rule_json) if ev.rule_json else {}
        except (ValueError, TypeError):
            rule = {}
        amt = float(ev.charge_amount or 0)
        t = totals.setdefault(ev.kind, {"count": 0, "charged": 0.0})
        t["count"] += 1
        t["charged"] = round(t["charged"] + amt, 2)
        rows.append({
            "id": ev.id, "date": ev.created_at.strftime("%Y-%m-%d %H:%M") if ev.created_at else None,
            "booking_id": b.booking_id, "guest": g.name if g else None,
            "rooms": ", ".join(rooms) or "-", "source": b.booking_source,
            "kind": ev.kind, "kind_label": KIND_LABELS.get(ev.kind, ev.kind),
            "expected_at": ev.expected_at.strftime("%d-%m %H:%M") if ev.expected_at else None,
            "actual_at": ev.actual_at.strftime("%d-%m %H:%M") if ev.actual_at else None,
            "deviation_minutes": ev.deviation_minutes, "hours": ev.hours,
            "charge": amt, "quoted": rule.get("quoted"),
            "rooms_charged": rule.get("rooms_charged"), "total_rooms": rule.get("total_rooms"),
            "basis": ev.charge_basis, "basis_label": BASIS_LABELS.get(ev.charge_basis, ev.charge_basis),
            "approval": ev.approval, "approved_by": approver, "reason": rule.get("reason"),
            "voided": ev.charge_basis == "voided",
        })
    return {"from": str(date_from), "to": str(date_to), "kind": kind, "rows": rows, "totals": totals,
            "charged_total": round(sum(t["charged"] for t in totals.values()), 2)}


def _num(v, default):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default
