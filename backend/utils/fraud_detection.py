"""Anti-fraud detection sweeps + owner digest (prompt 11, core).

Pure DB functions (no request context) mirroring utils/booking_cleanup.py, so they can run
either inline on an admin request (POST /fraud/reconcile, or lazily when the dashboard loads)
or from scripts/fraud_jobs.py under a future cron / Fly scheduled machine.

run_reconciliation(db) scans already-populated data and writes new fraud_alerts for:
  - card_without_booking / card_without_payment : an ACTIVE card whose booking/payment no
        longer validates (the offline re-validation hook — a booking cancelled or a payment
        removed after the card was cut). cards.py flags this at issue time; the sweep catches
        drift afterwards.
  - cleaning_too_long : a room parked in 'cleaning' beyond CLEANING_MAX_HOURS with no in-house
        booking (the owner's #1 fear: checked out, held in cleaning, re-let off-book for cash).
  - off_hours_issuance / off_station_issuance : a card cut outside ALLOWED_ISSUE_HOURS or from a
        station not in ALLOWED_STATIONS.
  - same_id_two_rooms : one guest ID on two concurrent in-house rooms.
  - repeated_refunds : the same payout reference refunded REPEAT_REFUND_THRESHOLD+ times.

Every alert carries a dedupe_key in its detail JSON so a repeated sweep never double-raises an
already-open alert (matches the FraudAlert insert shape used by routers/cards.py).
"""
import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func

from models import (Booking, BookingItem, CardIssuance, FraudAlert, Guest,
                    Payment, Room)

logger = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))  # single India property; no DST


# ---------------------------------------------------------------------------
# config (read at call time — the codebase idiom, e.g. payments.py:627)
# ---------------------------------------------------------------------------

def get_config(db) -> dict:
    """Current detector thresholds + OTP gates. These moved from env-only into
    admin-editable settings (backlog v2 FE-12) — `utils.settings.get_fraud_config` is now
    the single source, with the old env vars as its defaults. Every enforcement site reads
    the same helper, so the Settings screen can never show a gate the code isn't applying."""
    from utils import settings as app_settings
    return app_settings.get_fraud_config(db)


# ---------------------------------------------------------------------------
# dedupe: never re-raise an already-open alert for the same subject
# ---------------------------------------------------------------------------

def _existing_open_keys(db) -> set:
    keys = set()
    for a in db.query(FraudAlert).filter(FraudAlert.status == "open").all():
        if a.detail:
            try:
                dk = json.loads(a.detail).get("dedupe_key")
                if dk:
                    keys.add(dk)
            except (ValueError, TypeError):
                pass
        # derive anchor keys so alerts raised elsewhere (cards.py) also de-dupe the sweep
        if a.card_id:
            keys.add(f"{a.type}:card:{a.card_id}")
        if a.room_id and a.type == "cleaning_too_long":
            keys.add(f"{a.type}:room:{a.room_id}")
    return keys


def _make_alert(new_alerts, keys, *, type, severity, dedupe_key, detail,
                booking_id=None, room_id=None, card_id=None):
    if dedupe_key in keys:
        return None
    detail = dict(detail or {})
    detail["dedupe_key"] = dedupe_key
    alert = FraudAlert(
        type=type, severity=severity,
        booking_id=booking_id, room_id=room_id, card_id=card_id,
        detail=json.dumps(detail, default=str), status="open",
    )
    keys.add(dedupe_key)
    new_alerts.append(alert)
    return alert


# ---------------------------------------------------------------------------
# detectors
# ---------------------------------------------------------------------------

def _detect_card_anomalies(db, new_alerts, keys):
    # lazy: avoid an import cycle at module load.
    # v4b1: counts an OTA/website prepayment, otherwise this detector raised a
    # `card_without_payment` alert on EVERY OTA guest — noise that trains staff to ignore it.
    from routers.payments import total_paid_including_prepaid
    cards = db.query(CardIssuance).filter(CardIssuance.status == "active").all()
    for c in cards:
        booking = db.query(Booking).filter(Booking.booking_id == c.booking_id).first() if c.booking_id else None
        reason = None
        if not booking or booking.status != "checked_in":
            reason = "card_without_booking"
        elif not c.room_id or not db.query(BookingItem).filter(
                BookingItem.booking_id == booking.booking_id,
                BookingItem.room_id == c.room_id).first():
            reason = "card_without_booking"  # room not part of this stay
        elif total_paid_including_prepaid(db, booking.booking_id) <= 0:
            reason = "card_without_payment"
        if reason:
            _make_alert(new_alerts, keys, type=reason, severity="high",
                        dedupe_key=f"{reason}:card:{c.id}",
                        booking_id=c.booking_id, room_id=c.room_id, card_id=c.id,
                        detail={"card_uid": c.card_uid, "issue_type": c.issue_type,
                                "station_id": c.station_id,
                                "issued_at": str(c.issued_at) if c.issued_at else None,
                                "note": "active card no longer matches a paid, in-house booking"})


def _detect_cleaning_too_long(db, new_alerts, keys):
    from utils.housekeeping import active_cleaning_card
    max_hours = get_config(db)["cleaning_max_hours"]
    if max_hours <= 0:
        return  # 0 disables the cleaning-too-long window (documented contract)
    cutoff = datetime.utcnow() - timedelta(hours=max_hours)
    rooms = db.query(Room).filter(Room.status == "cleaning").all()
    for r in rooms:
        if not r.status_changed_at or r.status_changed_at > cutoff:
            continue  # unknown entry time, or not long enough yet
        # legitimately in-house? (a checked-in booking assigned to this room)
        in_house = db.query(BookingItem).join(
            Booking, Booking.booking_id == BookingItem.booking_id).filter(
            BookingItem.room_id == r.room_id,
            Booking.status == "checked_in").first()
        if in_house:
            continue
        hours = round((datetime.utcnow() - r.status_changed_at).total_seconds() / 3600, 1)
        # A cleaning card still in the housekeeper's hands past the window is the stronger signal.
        outstanding = active_cleaning_card(db, r.room_id) is not None
        _make_alert(new_alerts, keys, type="cleaning_too_long", severity="high",
                    dedupe_key=f"cleaning_too_long:room:{r.room_id}", room_id=r.room_id,
                    detail={"room_number": r.room_number, "hours_in_cleaning": hours,
                            "threshold_hours": max_hours,
                            "since": str(r.status_changed_at),
                            "cleaning_card_outstanding": outstanding,
                            "note": "room held in cleaning far beyond threshold with no in-house booking"})


def _detect_cleaning_too_fast(db, new_alerts, keys):
    """A checkout-clean finished suspiciously fast (probably not really cleaned). Duration is the
    task's started_at -> done_at, which for a card-lock room is the cleaning card's encode -> return
    and for a key-lock room is the manual start -> mark-clean. `cleaning_min_minutes == 0` disables it."""
    from models import HousekeepingTask
    min_minutes = get_config(db)["cleaning_min_minutes"]
    if min_minutes <= 0:
        return
    # Only look back a couple of days so a config change doesn't re-scan ancient history.
    lookback = datetime.utcnow() - timedelta(days=2)
    tasks = (db.query(HousekeepingTask)
             .filter(HousekeepingTask.type == "checkout_clean",
                     HousekeepingTask.status == "done",
                     HousekeepingTask.started_at.isnot(None),
                     HousekeepingTask.done_at.isnot(None),
                     HousekeepingTask.done_at >= lookback).all())
    for t in tasks:
        minutes = round((t.done_at - t.started_at).total_seconds() / 60, 1)
        if minutes >= min_minutes:
            continue
        r = db.query(Room).filter(Room.room_id == t.room_id).first()
        _make_alert(new_alerts, keys, type="cleaning_too_fast", severity="med",
                    dedupe_key=f"cleaning_too_fast:task:{t.id}", room_id=t.room_id,
                    detail={"room_number": r.room_number if r else None,
                            "minutes_taken": minutes, "threshold_minutes": min_minutes,
                            "cleaned_by": t.cleaned_by_name,
                            "since": str(t.started_at),
                            "note": "room marked clean far faster than the minimum — likely not cleaned"})


def _detect_inspection_overdue(db, new_alerts, keys):
    """A room CLEANED (housekeeping task done) but not yet INSPECTED beyond the threshold — the
    room can't go back into service until someone signs off on it."""
    from models import HousekeepingTask
    max_hours = get_config(db)["inspection_max_hours"]
    cutoff = datetime.utcnow() - timedelta(hours=max_hours)
    tasks = (db.query(HousekeepingTask)
             .filter(HousekeepingTask.status == "done",
                     HousekeepingTask.inspected_at.is_(None),
                     HousekeepingTask.done_at.isnot(None),
                     HousekeepingTask.done_at < cutoff).all())
    for t in tasks:
        r = db.query(Room).filter(Room.room_id == t.room_id).first()
        hours = round((datetime.utcnow() - t.done_at).total_seconds() / 3600, 1)
        _make_alert(new_alerts, keys, type="inspection_overdue", severity="high",
                    dedupe_key=f"inspection_overdue:task:{t.id}", room_id=t.room_id,
                    detail={"room_number": r.room_number if r else None,
                            "hours_since_cleaned": hours, "threshold_hours": max_hours,
                            "since": str(t.done_at),
                            "note": "room cleaned but not inspected beyond threshold"})


def _detect_issuance_fencing(db, new_alerts, keys):
    from utils import settings as app_settings
    stations = get_config(db)["allowed_stations"]
    start, end = app_settings.allowed_issue_hours_window(db)
    since = datetime.utcnow() - timedelta(days=7)  # only recent issuances
    cards = db.query(CardIssuance).filter(CardIssuance.issued_at >= since).all()
    for c in cards:
        if not c.issued_at:
            continue
        issued = c.issued_at
        if issued.tzinfo is None:
            issued = issued.replace(tzinfo=timezone.utc)
        hour = issued.astimezone(IST).hour
        if hour < start or hour >= end:
            _make_alert(new_alerts, keys, type="off_hours_issuance", severity="med",
                        dedupe_key=f"off_hours_issuance:card:{c.id}",
                        booking_id=c.booking_id, room_id=c.room_id, card_id=c.id,
                        detail={"issued_at": str(c.issued_at), "issued_hour_ist": hour,
                                "allowed_hours": f"{start:02d}-{end:02d}",
                                "station_id": c.station_id})
        if stations and (c.station_id or "") not in stations:
            _make_alert(new_alerts, keys, type="off_station_issuance", severity="med",
                        dedupe_key=f"off_station_issuance:card:{c.id}",
                        booking_id=c.booking_id, room_id=c.room_id, card_id=c.id,
                        detail={"station_id": c.station_id, "allowed_stations": stations,
                                "issued_at": str(c.issued_at)})


def _detect_same_id_two_rooms(db, new_alerts, keys):
    # guests currently in-house, grouped by masked ID across distinct rooms
    rows = (db.query(Guest.id_number_masked, Booking.booking_id, BookingItem.room_id)
            .join(Booking, Booking.guest_id == Guest.guest_id)
            .join(BookingItem, BookingItem.booking_id == Booking.booking_id)
            .filter(Booking.status == "checked_in",
                    Guest.id_number_masked.isnot(None),
                    BookingItem.room_id.isnot(None)).all())
    by_id = {}
    for masked, booking_id, room_id in rows:
        by_id.setdefault(masked, {"bookings": set(), "rooms": set()})
        by_id[masked]["bookings"].add(booking_id)
        by_id[masked]["rooms"].add(room_id)
    for masked, g in by_id.items():
        if len(g["rooms"]) >= 2:
            anchor = min(g["bookings"])
            _make_alert(new_alerts, keys, type="same_id_two_rooms", severity="med",
                        dedupe_key=f"same_id_two_rooms:id:{masked}", booking_id=anchor,
                        detail={"id_number_masked": masked,
                                "booking_ids": sorted(g["bookings"]),
                                "room_ids": sorted(g["rooms"]),
                                "note": "same guest ID checked into multiple rooms"})


def _detect_repeated_refunds(db, new_alerts, keys):
    threshold = get_config(db)["repeat_refund_threshold"]
    rows = (db.query(Payment.refund_reference, func.count(Payment.payment_id))
            .filter(Payment.refund_reference.isnot(None),
                    Payment.refund_reference != "",
                    Payment.refund_status.in_(["completed", "pending"]))
            .group_by(Payment.refund_reference)
            .having(func.count(Payment.payment_id) >= threshold).all())
    for reference, count in rows:
        _make_alert(new_alerts, keys, type="repeated_refunds", severity="med",
                    dedupe_key=f"repeated_refunds:ref:{reference}",
                    detail={"refund_reference": reference, "refund_count": count,
                            "threshold": threshold,
                            "note": "same payout reference refunded repeatedly"})


def _detect_ota_no_email(db, new_alerts, keys):
    """An OTA booking whose id was NOT backed by the OTA's own email — verified only by an owner
    OTP (or still unverified) — and whose stay has reached check-in with no confirming email yet.
    This is the review queue for the hard OTA-id gate: a fabricated 'OTA prepaid' id never gets a
    real email. 'legacy' (grandfathered) and 'email' verified bookings are exempt."""
    from models import OtaDraftBooking
    today = datetime.utcnow().date()
    rows = (db.query(Booking)
            .filter(Booking.ota_booking_id.isnot(None),
                    Booking.status.in_(("confirmed", "checked_in", "checked_out")),
                    Booking.check_in <= today,
                    func.coalesce(Booking.ota_verified_source, "") != "email",
                    func.coalesce(Booking.ota_verified_source, "") != "legacy").all())
    for b in rows:
        has_email = db.query(OtaDraftBooking).filter(
            OtaDraftBooking.channel_code == b.booking_source,
            OtaDraftBooking.ota_booking_id == b.ota_booking_id,
            OtaDraftBooking.kind == "confirmation").first() is not None
        if has_email:
            continue
        _make_alert(new_alerts, keys, type="ota_no_email", severity="med",
                    dedupe_key=f"ota_no_email:booking:{b.booking_id}", booking_id=b.booking_id,
                    detail={"ota_booking_id": b.ota_booking_id, "channel": b.booking_source,
                            "verified_source": b.ota_verified_source,
                            "check_in": str(b.check_in),
                            "note": "OTA booking reached check-in with no confirming OTA email — "
                                    "verify the booking id is genuine"})


def run_reconciliation(db) -> dict:
    """Run every detector, persist new (de-duplicated) alerts, return per-type counts."""
    keys = _existing_open_keys(db)
    new_alerts = []
    for detector in (_detect_card_anomalies, _detect_cleaning_too_long,
                     _detect_cleaning_too_fast, _detect_inspection_overdue,
                     _detect_issuance_fencing, _detect_same_id_two_rooms,
                     _detect_repeated_refunds, _detect_ota_no_email):
        try:
            detector(db, new_alerts, keys)
        except Exception as e:
            logger.error(f"fraud detector {detector.__name__} failed: {e}", exc_info=True)

    for a in new_alerts:
        db.add(a)
    db.commit()

    by_type = {}
    for a in new_alerts:
        by_type[a.type] = by_type.get(a.type, 0) + 1
    total_open = db.query(func.count(FraudAlert.id)).filter(FraudAlert.status == "open").scalar() or 0
    logger.info(f"reconciliation: {len(new_alerts)} new alert(s) {by_type}; {total_open} open total")
    return {"new_alerts": len(new_alerts), "by_type": by_type, "open_total": int(total_open)}


# ---------------------------------------------------------------------------
# owner daily digest (viewable now; WhatsApp delivery lands in prompt 15)
# ---------------------------------------------------------------------------

def compute_daily_digest(db, day=None) -> dict:
    """Occupancy, revenue, expected cash, arrivals/departures and open-alert counts.
    `day` (a date) defaults to today (server date)."""
    if day is None:
        day = datetime.utcnow().date()

    total_rooms = db.query(func.count(Room.room_id)).filter(Room.is_active == True).scalar() or 0
    occupied = db.query(func.count(Room.room_id)).filter(Room.status == "occupied").scalar() or 0
    cleaning = db.query(func.count(Room.room_id)).filter(Room.status == "cleaning").scalar() or 0
    occ_pct = round(100.0 * occupied / total_rooms, 1) if total_rooms else 0.0

    def _sum(*conds):
        return float(db.query(func.coalesce(func.sum(Payment.amount), 0)).filter(*conds).scalar() or 0)

    revenue = _sum(Payment.status == "paid", func.date(Payment.created_at) == day)
    cash_in = _sum(Payment.status == "paid", Payment.method == "cash",
                   func.date(Payment.created_at) == day)
    cash_refunds = float(db.query(func.coalesce(func.sum(Payment.refund_amount), 0)).filter(
        Payment.refund_mode == "cash", Payment.refund_status == "completed",
        func.date(Payment.created_at) == day).scalar() or 0)

    arrivals = db.query(func.count(Booking.booking_id)).filter(Booking.check_in == day).scalar() or 0
    departures = db.query(func.count(Booking.booking_id)).filter(Booking.check_out == day).scalar() or 0

    open_alerts = db.query(func.count(FraudAlert.id)).filter(FraudAlert.status == "open").scalar() or 0
    high_alerts = db.query(func.count(FraudAlert.id)).filter(
        FraudAlert.status == "open", FraudAlert.severity == "high").scalar() or 0

    return {
        "day": str(day),
        "occupancy": {"occupied": int(occupied), "cleaning": int(cleaning),
                      "total_rooms": int(total_rooms), "occupancy_pct": occ_pct},
        "revenue_today": round(revenue, 2),
        "expected_cash_today": round(cash_in - cash_refunds, 2),
        "arrivals": int(arrivals),
        "departures": int(departures),
        "open_alerts": int(open_alerts),
        "high_severity_alerts": int(high_alerts),
    }
