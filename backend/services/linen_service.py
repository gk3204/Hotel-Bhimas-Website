"""Linen / laundry stage-count engine (FE-9).

A hotel-wide pool per item type with three launderable stages — clean -> dirty -> at_laundry ->
clean — plus 'out' for issued/consumed pieces (not a stored column). `record_move` applies a
stage delta to a LinenItem and appends an append-only LinenMovement row. `on_checkout` moves a
checked-out room's default set from clean -> dirty (best-effort; never breaks checkout).
"""
import logging
from datetime import datetime

from models import LinenItem, LinenSet, LinenMovement

logger = logging.getLogger(__name__)

# Stages that are stored as columns on LinenItem. 'out' (issued/consumed) is intentionally not
# a column — moving something to 'out' just decrements its source stage.
_COLUMN_STAGES = ("clean", "dirty", "at_laundry")


def record_move(db, item: LinenItem, from_stage, to_stage, qty, *,
                room_id=None, booking_id=None, reason=None, user=None):
    """Apply a stage move to `item` and log it. Decrements `from_stage` (floored at 0), increments
    `to_stage`; a None stage means an external in/out flow (replenish / consume). Does NOT commit —
    the caller owns the transaction. `qty` must be positive."""
    from utils.audit import _resolve_user_id
    qty = float(qty or 0)
    if qty <= 0:
        return
    if from_stage in _COLUMN_STAGES:
        setattr(item, from_stage, max(0.0, float(getattr(item, from_stage) or 0) - qty))
    if to_stage in _COLUMN_STAGES:
        setattr(item, to_stage, float(getattr(item, to_stage) or 0) + qty)
    uid = _resolve_user_id(db, user) if user is not None else None
    item.updated_at = datetime.utcnow()
    if uid is not None:
        item.updated_by = uid
    db.add(LinenMovement(
        item_id=item.id, from_stage=from_stage, to_stage=to_stage, qty=qty,
        reason=reason, room_id=room_id, booking_id=booking_id, created_by=uid,
    ))


def on_checkout(db, booking):
    """Send every checked-out room's default LAUNDERABLE linen set from clean -> dirty (FE-9).
    Best-effort: a failure here must never break a checkout. Commits its own work."""
    try:
        sets_by_type: dict = {}
        for bi in booking.booking_items:
            if not bi.room_id:
                continue
            rt = bi.room_type_id
            if rt not in sets_by_type:
                pairs = []
                for s in db.query(LinenSet).filter(LinenSet.room_type_id == rt).all():
                    it = db.query(LinenItem).filter(
                        LinenItem.id == s.item_id, LinenItem.is_active == True).first()  # noqa: E712
                    if it and it.launderable and float(s.qty or 0) > 0:
                        pairs.append((it, float(s.qty)))
                sets_by_type[rt] = pairs
            for it, qty in sets_by_type[rt]:
                record_move(db, it, "clean", "dirty", qty,
                            room_id=bi.room_id, booking_id=booking.booking_id, reason="checkout")
        db.commit()
    except Exception as e:
        logger.error(f"linen on_checkout failed for booking {getattr(booking, 'booking_id', '?')}: {e}",
                     exc_info=True)
        db.rollback()
