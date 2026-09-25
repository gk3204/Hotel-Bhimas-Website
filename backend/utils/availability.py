"""Room-type capacity arithmetic — the ONE place that decides what counts as sold.

Backlog v2 FE-7. Before this module the desk (`routers/reception.py`) and the public
website (`routers/room_type_availability.py`) each carried their own copy of the
"which booking statuses reserve a room" list, and neither counted a guest who had
already **checked in**. Check-in flips `Booking.status` to `checked_in`, so the stay
dropped straight out of the booked count and the occupied room was offered for sale
again — an overbooking hole on both surfaces.

Both routers now call these helpers, so the two can never drift again.
"""

from sqlalchemy import func

from models import Booking, BookingItem, Room

# Booking statuses that hold a room out of inventory for their date range.
# `checked_in` belongs here: the guest is physically in the room.
# `checked_out` / `cancelled` / `expired` deliberately do NOT — those rooms are resellable.
RESERVED_STATUSES = ["confirmed", "pending_payment", "payment_pending", "checked_in"]

# Physical rooms that cannot be sold right now regardless of bookings.
# `occupied` is absent on purpose — an occupied room is already counted through its
# `checked_in` booking above, and counting it here too would subtract it twice.
# `cleaning` is absent too: it is transient and a same-day resell is legitimate.
OUT_OF_SERVICE_STATUSES = ("maintenance", "blocked")


# Namespace for the per-room-type advisory lock. pg_advisory_xact_lock takes two int4s; the first
# keeps our keys from colliding with any other advisory lock in the database (the night audit holds
# one of its own, utils/night_audit.py).
_CAPACITY_LOCK_NS = 0x524F4F4D  # "ROOM"


def lock_room_type(db, room_type_id) -> None:
    """Serialise capacity decisions for ONE room type, for the rest of this transaction.

    F-08 — the overbooking guard did not hold. `booked_qty(lock=True)` takes `SELECT ... FOR UPDATE`
    on the bookings that overlap the dates, but a row lock can only lock rows that ALREADY EXIST:
    when the competing bookings have not been inserted yet the subquery matches nothing, so there is
    nothing to lock. Every concurrent request read `booked = 0`, every one passed the check, and
    every one inserted. Measured before this existed: five simultaneous requests for the last five
    rooms were ALL accepted — 25 rooms sold against 5 — and `available` then read 0, so the oversell
    was invisible until the guests arrived. `READ COMMITTED` (database.py) makes that phantom the
    expected behaviour, not bad luck.

    An advisory lock has no such problem: it locks a NUMBER, which exists whether or not any row
    does. Taken on the room type, so it serialises only bookings competing for the same inventory,
    and `_xact_` releases it on commit or rollback — nothing to leak. Precedent in this codebase:
    `utils/night_audit.py`.

    Call this BEFORE reading capacity, inside the booking transaction.
    """
    from sqlalchemy import text
    db.execute(text("SELECT pg_advisory_xact_lock(:ns, :k)"),
               {"ns": _CAPACITY_LOCK_NS, "k": int(room_type_id)})


def booked_qty(db, room_type_id, check_in, check_out, lock=False):
    """Rooms of this type consumed by bookings overlapping [check_in, check_out).

    `lock=True` adds a row lock on the overlapping bookings, which is worth having but is NOT what
    makes the capacity check safe — see `lock_room_type` above, which the callers take first.
    """
    inner = db.query(Booking.booking_id).filter(
        Booking.status.in_(RESERVED_STATUSES),
        Booking.check_in < check_out,
        Booking.check_out > check_in,
    )
    if lock:
        inner = inner.with_for_update()
    return db.query(func.sum(BookingItem.quantity)).filter(
        BookingItem.room_type_id == room_type_id,
        BookingItem.booking_id.in_(inner),
    ).scalar() or 0


def out_of_service_count(db, room_type_id):
    """Physical rooms of this type that are deactivated or out of service."""
    return db.query(func.count(Room.room_id)).filter(
        Room.room_type_id == room_type_id,
        (Room.is_active == False) | (Room.status.in_(OUT_OF_SERVICE_STATUSES)),  # noqa: E712
    ).scalar() or 0
