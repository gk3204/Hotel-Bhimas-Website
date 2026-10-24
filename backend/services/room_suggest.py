"""Which room to suggest for an arrival: rotation, longest since last checkout (owner, 21 Oct).

The owner: "system should suggest room which has not been booked for a long time (some kind of round
robin)". Rooms are only chosen at check-in, so this is ADVISORY - it is printed on the arrival report
and reserves nothing. Read-only: no row is written.

The rule, per room type:
* A room can be suggested when it is active, not maintenance / blocked, and not occupied past the
  day by a guest who is checked in.
* The room that was RELEASED longest ago comes first; a room never released at all comes before
  every other. A room whose in-house guest leaves on or before the day counts as released on that
  departure date, so it naturally falls behind rooms that are already empty.
* For TODAY a clean / inspected room wins a tie, and a room whose guest is still in (due out) goes
  after every empty one.
* One room is never suggested twice on one report: demands are served in the order given (the
  caller puts confirmed stays before unconfirmed drafts, then by expected arrival).
* None of the booked type left: rooms whose ALTERNATE type is the booked type, labelled so
  (selling one still needs the owner's code at check-in).

⚠️ `checked_out_at` (booking and booking_item) is a naive-UTC instant; it is only compared with other
UTC instants here and turned into a business date through `clock.business_date_of` for display.
"""

from datetime import datetime, time

from sqlalchemy import func

from models import Booking, BookingItem, HousekeepingStatus, Room
from utils import clock, ordering
from utils.availability import OUT_OF_SERVICE_STATUSES
from utils.housekeeping import effective_hk_status

_READY_HK = ("clean", "inspected", "vacant")


def _last_release(db) -> dict:
    """{room_id: last checkout instant (UTC)} over every departed room. Per-room stamps (048) where
    they exist, else the booking's own (rows from before the split)."""
    out = {}
    q = (db.query(BookingItem.room_id,
                  func.max(func.coalesce(BookingItem.checked_out_at, Booking.checked_out_at)))
         .join(Booking, Booking.booking_id == BookingItem.booking_id)
         .filter(BookingItem.room_id.isnot(None),
                 (BookingItem.checked_out_at.isnot(None)) | (Booking.status == "checked_out"))
         .group_by(BookingItem.room_id))
    for room_id, at in q.all():
        if at is not None:
            out[room_id] = at
    return out


def _in_house(db) -> dict:
    """{room_id: departure date} for every room a checked-in guest is in right now."""
    out = {}
    rows = (db.query(BookingItem, Booking)
            .join(Booking, Booking.booking_id == BookingItem.booking_id)
            .filter(BookingItem.room_id.isnot(None),
                    BookingItem.checked_in_at.isnot(None),
                    BookingItem.checked_out_at.is_(None),
                    Booking.status == "checked_in").all())
    for it, b in rows:
        dep = it.check_out or b.check_out
        if dep is not None:
            out[it.room_id] = max(dep, out.get(it.room_id, dep))
    return out


def candidate_rooms(db, day) -> list:
    """Every room that could take a guest arriving on `day`, best first, as dicts:
    {room, room_type_id, alt_room_type_id, released_at, due_out, reason}."""
    today = clock.business_today()
    released = _last_release(db)
    in_house = _in_house(db)
    hk = {r.room_id: r.status for r in db.query(HousekeepingStatus).all()}

    out = []
    for room in db.query(Room).filter(Room.is_active.is_(True)).all():
        if (room.status or "") in OUT_OF_SERVICE_STATUSES:
            continue
        due_out = None
        at = released.get(room.room_id)
        if room.room_id in in_house:
            dep = in_house[room.room_id]
            if dep > day:
                continue                      # occupied through the arrival night
            due_out = dep
            at = clock.to_utc(datetime.combine(dep, time(hour=12)))   # released on departure
        elif day == today and room.status == "occupied":
            continue                          # occupied with no stay we can see: never suggest it
        hk_status = (effective_hk_status(room, hk.get(room.room_id)) or "").strip().lower()
        if due_out is not None:
            reason = f"guest due out {due_out:%d %b}"
        elif at is None:
            reason = "never used"
        else:
            reason = f"last out {clock.business_date_of(at):%d %b}"
        out.append({
            "room": room,
            "room_type_id": room.room_type_id,
            "alt_room_type_id": room.alt_room_type_id,
            "released_at": at,
            "due_out": due_out,
            "hk_status": hk_status or None,
            "reason": reason,
            "_key": (
                1 if (day == today and due_out is not None) else 0,   # today: empty rooms first
                0 if at is None else 1,                               # never released first
                at or datetime.min,                                   # then longest since checkout
                0 if (day != today or hk_status in _READY_HK) else 1,   # today: ready room wins a tie
                ordering.room_number_sort_key(room.room_number),
            ),
        })
    out.sort(key=lambda c: c["_key"])
    return out


def suggest_rooms(db, day, demands) -> dict:
    """Allocate one suggested room per demand.

    `demands` is an ordered list of (key, room_type_id); earlier demands get the better rooms.
    Returns {key: {room_id, room_number, reason, alt} | None}; None = nothing of that type (or its
    alternates) is free that day, or the room type is unknown.
    """
    pool = candidate_rooms(db, day)
    used, out = set(), {}
    for key, rt_id in demands:
        pick, alt = None, False
        if rt_id:
            pick = next((c for c in pool if c["room_type_id"] == rt_id
                         and c["room"].room_id not in used), None)
            if pick is None:
                pick = next((c for c in pool if c["alt_room_type_id"] == rt_id
                             and c["room"].room_id not in used), None)
                alt = pick is not None
        if pick is None:
            out[key] = None
            continue
        used.add(pick["room"].room_id)
        out[key] = {"room_id": pick["room"].room_id,
                    "room_number": pick["room"].room_number,
                    "reason": pick["reason"] + (" (alt type)" if alt else ""),
                    "alt": alt}
    return out
