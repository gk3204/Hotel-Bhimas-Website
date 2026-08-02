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


def booked_qty(db, room_type_id, check_in, check_out, lock=False):
    """Rooms of this type consumed by bookings overlapping [check_in, check_out)."""
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
