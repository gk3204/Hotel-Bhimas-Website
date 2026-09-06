"""Housekeeping helpers (prompt 13).

Shared logic so the checkout / room-move flows in routers/reception.py and the
housekeeper endpoints in routers/housekeeping.py stay consistent:

- `set_hk_status(db, room_id, status, user)` upserts the single HousekeepingStatus
  row for a room (one row per room). It does NOT touch Room.status — the coarse
  Room.status vocab (what availability/check-in read) is managed by the caller so
  the anti-fraud re-sale gate stays explicit.
- `on_room_dirtied(db, room, booking)` is called wherever a stay vacates a room
  (checkout, room-move): it marks the room dirty and creates a pooled
  `checkout_clean` task. Idempotent — never stacks duplicate open tasks.

The caller is responsible for the DB commit (these join the caller's transaction).
"""
from datetime import datetime

from models import HousekeepingStatus, HousekeepingTask, CardIssuance
from utils.audit import _resolve_user_id


# ---------------------------------------------------------------- cleaning card
# The reception desk can hand the housekeeper a time-limited "cleaning card" while a room is
# dirty. It may be encoded ONCE per cleaning cycle and only for a card-lock room. The cycle
# boundary is Room.status_changed_at (bumped only when the room ENTERS cleaning — at checkout /
# room-move — and not by the cleaning-card start/finish below), so "issued this cycle" is simply
# "issued at or after the room went dirty".

def open_checkout_clean_task(db, room_id: int):
    """The current cleaning cycle's checkout_clean task (open first, else the most recent),
    used to link the cleaning card + drive auto start/finish."""
    q = db.query(HousekeepingTask).filter(
        HousekeepingTask.room_id == room_id,
        HousekeepingTask.type == "checkout_clean",
    )
    return (q.filter(HousekeepingTask.status.in_(["pending", "in_progress"]))
             .order_by(HousekeepingTask.created_at.desc()).first()
            or q.order_by(HousekeepingTask.created_at.desc()).first())


def active_cleaning_card(db, room_id: int):
    """The room's outstanding (un-returned) cleaning card, if any."""
    return db.query(CardIssuance).filter(
        CardIssuance.room_id == room_id,
        CardIssuance.card_type == "cleaning",
        CardIssuance.status == "active",
    ).order_by(CardIssuance.id.desc()).first()


def cleaning_card_state(db, room) -> str:
    """'none' | 'active' | 'used' for the room's CURRENT cleaning cycle.
    active = an outstanding cleaning card exists (offer Return); used = one was issued this cycle
    and already returned (encode blocked — once only); none = none issued yet (encode allowed for
    a dirty card-lock room). Key-lock rooms are always 'none' (they never get a card)."""
    if getattr(room, "lock_type", "card") == "key":
        return "none"
    since = room.status_changed_at or datetime.min
    cards = db.query(CardIssuance).filter(
        CardIssuance.room_id == room.room_id,
        CardIssuance.card_type == "cleaning",
        CardIssuance.issued_at >= since,
    ).all()
    if not cards:
        return "none"
    return "active" if any(c.status == "active" for c in cards) else "used"


def set_hk_status(db, room_id: int, status: str, user=None, photo_url=None):
    """Upsert the current housekeeping status for a room. Returns the row."""
    row = db.query(HousekeepingStatus).filter(HousekeepingStatus.room_id == room_id).first()
    if row is None:
        row = HousekeepingStatus(room_id=room_id)
        db.add(row)
    row.status = status
    row.updated_by = _resolve_user_id(db, user)
    row.updated_at = datetime.utcnow()
    if photo_url is not None:
        row.photo_url = photo_url
    return row


def on_room_dirtied(db, room, booking=None):
    """A stay left `room` -> mark it dirty + ensure a pending cleaning task exists.
    Idempotent: if the room already has an open (pending/in_progress) checkout_clean
    task, don't create a second one."""
    set_hk_status(db, room.room_id, "dirty", user=None)

    existing = db.query(HousekeepingTask).filter(
        HousekeepingTask.room_id == room.room_id,
        HousekeepingTask.type == "checkout_clean",
        HousekeepingTask.status.in_(["pending", "in_progress"]),
    ).first()
    if existing:
        return existing

    task = HousekeepingTask(
        room_id=room.room_id,
        type="checkout_clean",
        status="pending",
        booking_id=booking.booking_id if booking is not None else None,
    )
    db.add(task)
    return task
