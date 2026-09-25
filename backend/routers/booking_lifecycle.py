"""v5m — booking lifecycle beyond check-in/out: no-shows and admin re-dating.

  POST /bookings/{id}/no-show     reception/admin — the guest never arrived (window lapsed or the
                                  desk knows they are not coming). Money is untouched: a prepaid /
                                  advance stays where it is; any refund is an admin decision.
  POST /bookings/{id}/reinstate   admin — undo a no-show (auto or manual) back to `confirmed`, e.g.
                                  the guest turns up late after all; the arrival rules then apply.
  POST /bookings/{id}/redate      admin — move a confirmed booking's dates (same length unless a new
                                  check-out is given), with the room-type availability check the
                                  desk never runs because the desk never re-dates.

The AUTOMATIC no-show lives in utils/night_audit.mark_no_shows: a `confirmed` booking whose
check-out date is over is a no-show — never earlier, so a badly-late guest can still be checked
in on any booked date (arrival rules decide the stay clock).
"""
import logging
from datetime import date, datetime, time, timedelta
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from database import SessionLocal
from models import Booking, BookingItem, RoomType
from services import ota_service
from utils import availability
from utils.audit import write_audit
from utils.auth_utils import require_admin, require_reception_or_admin
from utils import clock          # F-03: one clock - see utils/clock.py

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/bookings", tags=["Booking lifecycle"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class NoShowRequest(BaseModel):
    reason: Optional[str] = Field(None, max_length=200)
    client_ref: Optional[str] = Field(None, max_length=64)


class RedateRequest(BaseModel):
    check_in: date
    check_out: Optional[date] = None      # omitted = keep the same number of nights
    check_in_time: Optional[time] = None  # omitted = keep the booked time
    reason: str = Field(..., min_length=3, max_length=200)


def _expected(booking: Booking, db) -> datetime:
    from routers.reception import expected_arrival
    return expected_arrival(booking, db)


def _serialize(b: Booking) -> dict:
    return {
        "booking_id": b.booking_id, "status": b.status,
        "check_in": str(b.check_in), "check_out": str(b.check_out),
        "check_in_time": str(b.check_in_time) if b.check_in_time else None,
        "expected_arrival_at": b.expected_arrival_at.isoformat() if b.expected_arrival_at else None,
        "no_show_at": b.no_show_at.isoformat() if b.no_show_at else None,
        "original_check_in": str(b.original_check_in) if b.original_check_in else None,
        "original_check_out": str(b.original_check_out) if b.original_check_out else None,
    }


def mark_no_show(db, booking: Booking, user, reason=None, client="desktop", automatic=False):
    """Shared by the endpoint and the night audit. Caller commits."""
    booking.status = "no_show"
    booking.no_show_at = clock.now_utc()      # F-03: an instant, stored UTC
    write_audit(db, user, "booking.no_show", "booking", booking.booking_id,
                before={"status": "confirmed"},
                after={"status": "no_show", "reason": reason,
                       "automatic": automatic, "check_in": str(booking.check_in),
                       "check_out": str(booking.check_out),
                       "prepaid_amount": float(booking.prepaid_amount or 0)},
                client=client, commit=False)


@router.post("/{booking_id}/no-show")
def no_show(booking_id: int, data: NoShowRequest = Body(default=NoShowRequest()),
            db: Session = Depends(get_db), user=Depends(require_reception_or_admin)):
    b = db.query(Booking).filter(Booking.booking_id == booking_id).with_for_update().first()
    if not b:
        raise HTTPException(status_code=404, detail="Booking not found")
    if b.status == "no_show":
        return _serialize(b)
    if b.status != "confirmed":
        raise HTTPException(status_code=409, detail=f"Cannot mark a '{b.status}' booking as a no-show")
    if datetime.now() < _expected(b, db):
        raise HTTPException(status_code=409,
                            detail=f"The guest is expected at {_expected(b, db):%d-%m-%Y %H:%M} — "
                                   f"a no-show can only be marked after that")
    mark_no_show(db, b, user, reason=data.reason, client="desktop")
    db.commit()
    db.refresh(b)
    return _serialize(b)


@router.post("/{booking_id}/reinstate")
def reinstate(booking_id: int, reason: Optional[str] = Body(None, embed=True, max_length=200),
              db: Session = Depends(get_db), user=Depends(require_admin)):
    b = db.query(Booking).filter(Booking.booking_id == booking_id).with_for_update().first()
    if not b:
        raise HTTPException(status_code=404, detail="Booking not found")
    if b.status != "no_show":
        raise HTTPException(status_code=409, detail=f"Only a no-show can be reinstated (this one is '{b.status}')")
    b.status = "confirmed"
    b.no_show_at = None
    write_audit(db, user, "booking.reinstate", "booking", b.booking_id,
                before={"status": "no_show"}, after={"status": "confirmed", "reason": reason},
                client="web", commit=False)
    db.commit()
    db.refresh(b)
    return _serialize(b)


@router.post("/{booking_id}/redate")
def redate(booking_id: int, data: RedateRequest, db: Session = Depends(get_db),
           user=Depends(require_admin)):
    b = db.query(Booking).filter(Booking.booking_id == booking_id).with_for_update().first()
    if not b:
        raise HTTPException(status_code=404, detail="Booking not found")
    if b.status not in ("confirmed", "no_show"):
        raise HTTPException(status_code=409, detail=f"A '{b.status}' booking cannot be re-dated")
    nights = max(1, (b.check_out - b.check_in).days)
    new_ci = data.check_in
    new_co = data.check_out or (new_ci + timedelta(days=nights))
    if new_co <= new_ci:
        raise HTTPException(status_code=400, detail="Check-out must be after check-in")
    if new_ci < date.today():
        raise HTTPException(status_code=400, detail="The new check-in date cannot be in the past")
    if (new_ci, new_co) == (b.check_in, b.check_out) and not data.check_in_time:
        raise HTTPException(status_code=400, detail="Those are already the booked dates")

    # Room-type capacity for the NEW window, excluding this booking's own hold on the old one.
    need = {}
    for item in b.booking_items:
        need[item.room_type_id] = need.get(item.room_type_id, 0) + int(item.quantity or 1)
    for rt_id, qty in need.items():
        rt = db.query(RoomType).filter(RoomType.room_type_id == rt_id).first()
        booked = int(availability.booked_qty(db, rt_id, new_ci, new_co, lock=True))
        # booked_qty counts this booking when its old window overlaps the new one.
        if b.status == "confirmed" and b.check_in < new_co and b.check_out > new_ci:
            booked -= qty
        inactive = int(availability.out_of_service_count(db, rt_id))
        free = (rt.total_rooms if rt else 0) - booked - inactive
        if free < qty:
            raise HTTPException(
                status_code=409,
                detail=f"No {rt.name if rt else 'room'} capacity for {new_ci:%d-%m-%Y} → {new_co:%d-%m-%Y} "
                       f"({max(free, 0)} free, {qty} needed)")

    before = _serialize(b)
    if not b.original_check_in:
        b.original_check_in = b.check_in
    if not b.original_check_out:
        b.original_check_out = b.check_out
    b.check_in, b.check_out = new_ci, new_co
    if data.check_in_time:
        b.check_in_time = data.check_in_time
    is_ota = ota_service.is_ota_source(b.booking_source, db)
    b.expected_arrival_at = datetime.combine(
        new_ci, time(hour=12) if is_ota else (b.check_in_time or time(hour=12)))
    if b.status == "no_show":
        b.status = "confirmed"
        b.no_show_at = None
    if (new_co - new_ci).days != nights:
        logger.info(f"redate: booking {b.booking_id} length changed {nights} → {(new_co - new_ci).days} nights; "
                    f"price stays as booked — adjust on the folio")
    write_audit(db, user, "booking.redated", "booking", b.booking_id,
                before=before, after={**_serialize(b), "reason": data.reason}, client="web", commit=False)
    db.commit()
    db.refresh(b)
    out = _serialize(b)
    out["nights"] = (new_co - new_ci).days
    out["length_changed"] = (new_co - new_ci).days != nights
    return out
