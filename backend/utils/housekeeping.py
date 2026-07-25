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

from models import HousekeepingStatus, HousekeepingTask
from utils.audit import _resolve_user_id


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
