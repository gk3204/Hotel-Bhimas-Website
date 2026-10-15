"""v7m (owner, 15 Oct): "all receipts - advance / refund / food bill / prepaid / invoice - should contain
the room number". One place decides which room a document names, so the receipt, the refund voucher,
the prepaid receipt and the invoice can never disagree.

  - a payment that landed on a ROOM's bill (per-room stays, v6e) names that room;
  - otherwise the stay's rooms (numerically: 9 before 10);
  - before check-in, when no room is assigned yet, "Not assigned yet".
"""
from models import BookingItem, Folio, FolioCharge, Room
from utils import ordering

NOT_ASSIGNED = "Not assigned yet"


def _labels(db, room_ids) -> str:
    ids = {r for r in room_ids if r}
    if not ids:
        return ""
    nums = [n for (n,) in db.query(Room.room_number).filter(Room.room_id.in_(ids)).all()]
    return ", ".join(sorted(nums, key=ordering.room_number_sort_key))


def booking_rooms_label(db, booking_id) -> str:
    """Every room assigned to the stay, or NOT_ASSIGNED."""
    ids = [r for (r,) in db.query(BookingItem.room_id).filter(BookingItem.booking_id == booking_id).all()]
    return _labels(db, ids) or NOT_ASSIGNED


def folio_rooms_label(db, folio) -> str:
    """A room's own bill names its room; the stay's bill names every room."""
    if folio is None:
        return NOT_ASSIGNED
    if folio.booking_item_id:
        rid = db.query(BookingItem.room_id).filter(
            BookingItem.booking_item_id == folio.booking_item_id).scalar()
        lab = _labels(db, [rid])
        if lab:
            return lab
    return booking_rooms_label(db, folio.booking_id)


def payment_rooms_label(db, payment) -> str:
    """The room(s) whose bill this payment (or its refund) was posted to - else the stay's rooms."""
    ids = []
    for room_id, item_id in (db.query(FolioCharge.room_id, Folio.booking_item_id)
                             .join(Folio, Folio.id == FolioCharge.folio_id)
                             .filter(FolioCharge.payment_id == payment.payment_id).all()):
        if room_id:
            ids.append(room_id)
        elif item_id:
            ids.append(db.query(BookingItem.room_id).filter(BookingItem.booking_item_id == item_id).scalar())
    # a payment spread over the group bill (no room on its line) is for the whole stay
    lab = _labels(db, ids) if ids and all(ids) else ""
    return lab or booking_rooms_label(db, payment.booking_id)
