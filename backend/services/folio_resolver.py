"""Which folio? (v6e, Batch D)

Until now the answer was always "the booking's one folio", written out 39 times as
`db.query(Folio).filter(Folio.booking_id == X).first()`. A booking can now bill **per room**, so
that question has three different answers depending on what the caller actually meant, and writing
it out a fortieth time is how a charge ends up on the wrong guest's bill.

The three meanings, and the function for each:

* **"the bill this room's charge belongs to"** — `folio_for_item`. A room night, a room-service
  delivery, an overstay charge, a late-checkout fee: these belong to one room and nothing else.
* **"every bill on this stay"** — `folios_for_booking`. Reports, the night audit, "has this booking
  been billed at all", anything that aggregates.
* **"the bill to put something stay-level on"** — `primary_folio`. A payment with no room named, a
  booking-wide discount. In group mode it is the one folio; in per-room mode it is the oldest OPEN
  room folio, and callers that can do better (a payment the desk attributed to a room) should say
  so rather than fall back to it.

The shape of a booking is one of exactly two, enforced by the partial unique indexes in migration
049: **one folio with `booking_item_id IS NULL`** (group), or **one folio per booking item** (room).
`is_per_room` reads the booking's own `billing_mode` rather than counting folios, so the intent is
known before any folio exists.
"""
from sqlalchemy.orm import Session

from models import Booking, BookingItem, Folio

GROUP = "group"
PER_ROOM = "room"


def is_per_room(booking: Booking) -> bool:
    """Does this booking bill one room at a time?"""
    return (getattr(booking, "billing_mode", GROUP) or GROUP) == PER_ROOM


def folios_for_booking(db: Session, booking_id: int, *, open_only: bool = False) -> list[Folio]:
    """Every folio on the stay, oldest first. One in group mode, one per room otherwise."""
    q = db.query(Folio).filter(Folio.booking_id == booking_id)
    if open_only:
        q = q.filter(Folio.status == "open")
    return q.order_by(Folio.id).all()


def folio_for_item(db: Session, booking, item) -> Folio | None:
    """The folio a charge against `item` belongs on.

    In group mode every room's charges go on the one folio, which is what "one bill for the group"
    means. In per-room mode the charge follows the room. A caller with no item at all gets the
    primary folio, because a charge has to land somewhere and refusing would lose it.
    """
    if item is None or not is_per_room(booking):
        return primary_folio(db, booking)
    item_id = item.booking_item_id if hasattr(item, "booking_item_id") else int(item)
    folio = db.query(Folio).filter(Folio.booking_id == booking.booking_id,
                                   Folio.booking_item_id == item_id).first()
    # A room added to the stay after check-in has no folio yet. Falling back to the primary would
    # silently bill it to another room, so the caller is told there is none and can open one.
    return folio


def primary_folio(db: Session, booking) -> Folio | None:
    """The folio for something that belongs to the stay rather than to one room.

    Group mode: the booking's folio. Per-room mode: the oldest folio still OPEN, because a settled
    room has left and posting to it would reopen a closed bill; if every room has settled, the
    oldest folio is returned so a late charge still lands somewhere findable rather than vanishing.
    """
    booking_id = booking.booking_id if hasattr(booking, "booking_id") else int(booking)
    rows = folios_for_booking(db, booking_id)
    if not rows:
        return None
    group = next((f for f in rows if f.booking_item_id is None), None)
    if group is not None:
        # A booking-level folio exists, so this stay is billed as a group whatever the flag says.
        # Reading the folios rather than the flag matters for a booking passed in as a bare id, and
        # for one whose mode was changed after the folio was opened.
        return group
    return next((f for f in rows if f.status == "open"), rows[0])


def folio_by_room(db: Session, booking) -> dict[int, Folio]:
    """`{room_id: folio}` for the stay — what the desk needs to show a bill per room."""
    out: dict[int, Folio] = {}
    items = db.query(BookingItem).filter(BookingItem.booking_id == booking.booking_id).all()
    by_item = {f.booking_item_id: f for f in folios_for_booking(db, booking.booking_id)}
    group = by_item.get(None)
    for it in items:
        if it.room_id:
            out[it.room_id] = by_item.get(it.booking_item_id) or group
    return out


def open_balance(db: Session, booking_id: int) -> float:
    """What the whole stay still owes, across however many bills it has."""
    return round(sum(float(f.balance or 0)
                     for f in folios_for_booking(db, booking_id)), 2)
