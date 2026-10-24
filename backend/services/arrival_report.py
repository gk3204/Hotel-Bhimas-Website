"""The arrival report: everyone due to arrive on one date, unconfirmed included (owner, 21 Oct).

The owner: "an arrival report selectable by date, which will show guest name, room type, booking
source, expected check-in time, check-out date, suggested room ... even unconfirmed bookings should
be shown and should be marked as unconfirmed".

ONE ROW PER ROOM, so a two-room stay gets two suggested rooms:
* a room that already arrived shows the room it is in (status `arrived`, no suggestion);
* a room still to come gets a suggestion from `services.room_suggest` (advisory, nothing reserved);
* a line released as "never arrived" (v8e) is closed and is not listed.

Unconfirmed, marked as such:
* `ota_draft`: an OTA confirmation email nobody has confirmed yet (`pending` / `flagged` - the same
  statuses that HOLD rooms, `availability.HOLDING_DRAFT_STATUSES`). A draft whose OTA id already has
  a live booking is listed through that booking only (`availability.live_ota_booking_ids`, shared
  with the room holds).
* `payment_pending`: a website booking waiting on its payment.

Nothing here re-derives a rule the board already has: money is `total_paid` + `prepaid_slice`, the
phone is `guest_display_phone`, the expected time is `expected_arrival`, late / early is the
board's own `arrival_evaluation`, and the source label is `utils.booking_source`.
"""

import logging
from datetime import datetime, time
from types import SimpleNamespace

from sqlalchemy.orm import joinedload

from models import Booking, BookingItem, Company, OtaDraftBooking, RoomType, TravelAgent
from services import ota_service
from services.room_suggest import suggest_rooms
from utils import arrival_rules, availability, booking_source, clock

logger = logging.getLogger(__name__)

# What a booking on its arrival day can be. `checked_in` / `checked_out` are there so a partly
# arrived stay, and any past date, still lists every room it arrived with.
REPORT_STATUSES = ("confirmed", "pending_payment", "payment_pending", "checked_in", "checked_out")
_PAYMENT_PENDING = ("pending_payment", "payment_pending")

STATUS_LABELS = {"confirmed": "Confirmed", "arrived": "Arrived", "unconfirmed": "UNCONFIRMED"}
UNCONFIRMED_LABELS = {"ota_draft": "OTA email not confirmed yet",
                      "ota_draft_flagged": "OTA email flagged for review",
                      "payment_pending": "Website booking - payment pending"}


def _hhmm(dt):
    return dt.strftime("%H:%M") if dt else None


def _booking_rows(db, b, today, now, rules, agents, companies):
    """Rows for one PMS booking: one per room, money on the first row only."""
    from routers.reception import (expected_arrival, guest_display_phone, arrival_evaluation,
                                   _arrival_public, total_rooms, pending_rooms)
    from routers.payments import total_paid, prepaid_slice

    paid = total_paid(db, b.booking_id)
    pre = prepaid_slice(db, b, paid=paid)
    grand = float(b.grand_total or 0)
    exp = expected_arrival(b, db)
    preview = None
    if b.check_in == today and b.status == "confirmed":
        try:
            preview = _arrival_public(arrival_evaluation(db, b, now, rules=rules))
        except Exception as e:  # never let a rules problem hide an arrival
            logger.warning(f"arrival report: preview failed for booking {b.booking_id}: {e}")
    unconfirmed = "payment_pending" if b.status in _PAYMENT_PENDING else None
    is_ota = ota_service.is_ota_source(b.booking_source, db)

    base = {
        "kind": "booking",
        "booking_id": b.booking_id,
        "draft_id": None,
        "guest_name": b.display_guest_name or None,
        "phone": guest_display_phone(b),
        "booking_source": b.booking_source,
        "source_label": booking_source.channel_label(b),
        "agent_name": agents.get(b.agent_id),
        "ota_booking_id": b.ota_booking_id,
        "expected_at": exp.isoformat() if exp else None,
        "expected_time": _hhmm(exp),
        "expected_note": "OTA 12:00" if is_ota else None,
        "check_in": str(b.check_in),
        "check_out": str(b.check_out),
        "nights": max(1, (b.check_out - b.check_in).days) if b.check_out and b.check_in else None,
        "adults": int(b.adults or 1),
        "children": int(b.children or 0),
        "rooms_total": total_rooms(b),
        "rooms_pending": pending_rooms(b),
        "is_late": bool(preview and preview["kind"] == "late"),
        "is_early": bool(preview and preview["kind"] == "early"),
        "lapsed": bool((preview or {}).get("lapsed")),
        "bill_to": b.bill_to or "guest",
        "company_name": companies.get(b.company_id),
    }
    money = {
        "grand_total": grand,
        "paid_total": round(paid, 2),
        "prepaid_amount": pre["prepaid_amount"],
        "prepaid_source": pre["prepaid_source"],
        "is_prepaid": pre["is_prepaid"],
        # what the desk still has to collect: the same "covered" the desk's HasBalanceDue uses
        "balance_due": round(max(0.0, grand - (paid + pre["prepaid_amount"])), 2),
    }

    rows = []
    for it in sorted(b.booking_items or [], key=lambda i: i.booking_item_id):
        rt_name = it.room_type.name if it.room_type else None
        if it.checked_in_at is not None:
            rows.append({**base, "status": "arrived", "unconfirmed_reason": None,
                         "room_type_id": it.room_type_id, "room_type_name": rt_name,
                         "room_number": it.room.room_number if it.room else None,
                         "arrived_at": it.checked_in_at.isoformat()})
        elif it.checked_out_at is None:          # still to come (a released line is closed)
            for _ in range(max(1, int(it.quantity or 1))):
                rows.append({**base, "status": "unconfirmed" if unconfirmed else "confirmed",
                             "unconfirmed_reason": unconfirmed,
                             "room_type_id": it.room_type_id, "room_type_name": rt_name,
                             "room_number": None, "arrived_at": None})
    for n, r in enumerate(rows, 1):
        r["room_index"] = n
        r.update(money if n == 1 else {k: None for k in money})
        r["money_row"] = n == 1
    return rows


def _draft_rows(db, d, rt_names):
    """Rows for one unconfirmed OTA draft: one per room it would take."""
    lines = ota_service.draft_room_type_lines(db, d)
    total = sum(int(n) for _, n in lines) or 1
    exp = datetime.combine(d.check_in, time(hour=12))       # OTA stays are contracted 12:00
    base = {
        "kind": "draft",
        "booking_id": None,
        "draft_id": d.id,
        "status": "unconfirmed",
        "unconfirmed_reason": "ota_draft_flagged" if d.status == "flagged" else "ota_draft",
        "guest_name": d.guest_name or None,
        "phone": d.phone or None,
        "booking_source": d.channel_code,
        "source_label": booking_source.channel_label(SimpleNamespace(booking_source=d.channel_code)),
        "agent_name": None,
        "ota_booking_id": d.ota_booking_id,
        "expected_at": exp.isoformat(),
        "expected_time": "12:00",
        "expected_note": "OTA 12:00",
        "check_in": str(d.check_in),
        "check_out": str(d.check_out) if d.check_out else None,
        "nights": max(1, (d.check_out - d.check_in).days) if d.check_out else None,
        "adults": int(d.adults or 1),
        "children": int(d.children or 0),
        "rooms_total": total,
        "rooms_pending": total,
        "is_late": False, "is_early": False, "lapsed": False,
        "bill_to": "guest", "company_name": None,
        "room_number": None, "arrived_at": None,
    }
    rows = []
    for rt_id, n in lines:
        for _ in range(max(1, int(n))):
            rows.append({**base, "room_type_id": rt_id,
                         # unmapped: show what the OTA called it, never a guessed type
                         "room_type_name": rt_names.get(rt_id) if rt_id else (d.room_type_hint or "Unknown type")})
    for n, r in enumerate(rows, 1):
        r["room_index"] = n
        r["money_row"] = n == 1
        # the voucher's gross; what the guest prepaid the OTA is not known until it is confirmed
        r.update({"grand_total": float(d.amount) if (n == 1 and d.amount is not None) else None,
                  "paid_total": None, "prepaid_amount": None, "prepaid_source": None,
                  "is_prepaid": None, "balance_due": None})
    return rows


def build_arrival_report(db, day) -> dict:
    from routers.reception import expire_pending_bookings

    expire_pending_bookings(db)      # a lapsed website hold is not an arrival (same as the board)
    today = clock.business_today()
    now = clock.now_local()
    rules = arrival_rules.load_rules(db)
    agents = {a.id: a.name for a in db.query(TravelAgent).all()}
    companies = {c.id: c.name for c in db.query(Company).all()}
    rt_names = {t.room_type_id: t.name for t in db.query(RoomType).all()}

    bookings = (db.query(Booking)
                .options(joinedload(Booking.guest),
                         joinedload(Booking.booking_items).joinedload(BookingItem.room_type),
                         joinedload(Booking.booking_items).joinedload(BookingItem.room))
                .filter(Booking.check_in == day, Booking.status.in_(REPORT_STATUSES))
                .order_by(Booking.booking_id).all())
    rows = []
    for b in bookings:
        rows.extend(_booking_rows(db, b, today, now, rules, agents, companies))

    drafts = (db.query(OtaDraftBooking)
              .filter(OtaDraftBooking.kind == "confirmation",
                      OtaDraftBooking.status.in_(availability.HOLDING_DRAFT_STATUSES),
                      OtaDraftBooking.check_in == day)
              .order_by(OtaDraftBooking.id).all())
    live = availability.live_ota_booking_ids(db, {d.ota_booking_id for d in drafts})
    for d in drafts:
        if d.ota_booking_id and d.ota_booking_id in live:
            continue                               # listed through its booking
        rows.extend(_draft_rows(db, d, rt_names))

    # Suggestions: only for rooms still to come, and never for a day already gone. Confirmed stays
    # are served first so an unconfirmed draft cannot take the room a confirmed guest would get.
    want = [r for r in rows if r["status"] != "arrived"]
    for r in rows:
        r["row_key"] = f"{r['kind'][0]}{r['booking_id'] or r['draft_id']}-{r['room_index']}"
    picks = {}
    if day >= today:
        want.sort(key=lambda r: (r["status"] == "unconfirmed", r["expected_at"] or "", r["row_key"]))
        picks = suggest_rooms(db, day, [(r["row_key"], r["room_type_id"]) for r in want])
    for r in rows:
        p = picks.get(r["row_key"])
        r["suggested_room"] = p["room_number"] if p else None
        r["suggested_room_id"] = p["room_id"] if p else None
        r["suggested_alt"] = bool(p and p["alt"])
        if r["status"] == "arrived":
            r["suggested_reason"] = None
        elif day < today:
            r["suggested_reason"] = "date has passed"
        elif not r["room_type_id"]:
            r["suggested_reason"] = "room type not identified"
        else:
            r["suggested_reason"] = p["reason"] if p else "none free"
        r["status_label"] = STATUS_LABELS[r["status"]]
        r["unconfirmed_label"] = UNCONFIRMED_LABELS.get(r["unconfirmed_reason"])

    rows.sort(key=lambda r: (r["expected_at"] or "", r["kind"], r["booking_id"] or r["draft_id"], r["room_index"]))
    return {"date": str(day), "is_today": day == today, "rows": rows, "summary": _summary(rows)}


def _summary(rows) -> dict:
    stays = {(r["kind"], r["booking_id"] or r["draft_id"]) for r in rows}
    unconf = {(r["kind"], r["booking_id"] or r["draft_id"]) for r in rows if r["status"] == "unconfirmed"}
    by_type = {}
    for r in rows:
        t = by_type.setdefault(r["room_type_name"] or "Unknown type",
                               {"room_type": r["room_type_name"] or "Unknown type",
                                "rooms": 0, "to_arrive": 0, "unconfirmed": 0, "no_room_free": 0})
        t["rooms"] += 1
        if r["status"] != "arrived":
            t["to_arrive"] += 1
            if r["status"] == "unconfirmed":
                t["unconfirmed"] += 1
            if r["suggested_reason"] == "none free":
                t["no_room_free"] += 1
    return {
        "bookings": len(stays),
        "rooms": len(rows),
        "rooms_to_arrive": sum(1 for r in rows if r["status"] != "arrived"),
        "rooms_arrived": sum(1 for r in rows if r["status"] == "arrived"),
        "unconfirmed_bookings": len(unconf),
        "unconfirmed_rooms": sum(1 for r in rows if r["status"] == "unconfirmed"),
        "adults": sum(r["adults"] for r in rows if r["money_row"]),
        "children": sum(r["children"] for r in rows if r["money_row"]),
        "balance_due": round(sum(r["balance_due"] or 0 for r in rows if r["money_row"]), 2),
        "by_type": sorted(by_type.values(), key=lambda t: t["room_type"]),
    }
