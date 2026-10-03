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
        # b5: unconfirmed OTA drafts hold their rooms (not this booking's own draft).
        holds, _u = availability.ota_draft_holds(db, new_ci, new_co,
                                                  exclude_ota_booking_id=b.ota_booking_id)
        free = (rt.total_rooms if rt else 0) - booked - inactive - int(holds.get(rt_id, 0))
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


# =================================================================================================
# v6m — admin edits a booking (web admin -> Bookings -> Edit). The desk reads bookings from the
# server (board, folio, history), so an edit here is what the desk shows on its next refresh.
# =================================================================================================

class BookingDetailsEdit(BaseModel):
    guest_name: Optional[str] = Field(None, min_length=2, max_length=100)
    phone: Optional[str] = Field(None, min_length=10, max_length=15)
    email: Optional[str] = Field(None, max_length=120)
    adults: Optional[int] = Field(None, ge=1, le=120)
    children: Optional[int] = Field(None, ge=0, le=40)
    check_in_time: Optional[time] = None
    admin_notes: Optional[str] = Field(None, max_length=500)
    reason: str = Field(..., min_length=3, max_length=200)


class BookingRoomLineEdit(BaseModel):
    booking_item_id: Optional[int] = Field(None, gt=0)    # an existing line, or None = a new one
    room_type_id: int = Field(..., gt=0)
    quantity: int = Field(1, ge=1, le=40)
    # The nightly rate BEFORE GST, per room. None = price from the rate card for the booking's
    # channel and dates (what the desk would have charged).
    price_per_night: Optional[float] = Field(None, ge=0, le=1000000)


class BookingRoomsEdit(BaseModel):
    lines: list[BookingRoomLineEdit] = Field(..., min_length=1, max_length=20)
    reason: str = Field(..., min_length=3, max_length=200)
    dry_run: bool = False


def _money(v) -> float:
    return round(float(v or 0), 2)


@router.patch("/{booking_id}/details")
def edit_booking_details(booking_id: int, data: BookingDetailsEdit, db: Session = Depends(get_db),
                         user=Depends(require_admin)):
    """Guest name / phone / email / party size / arrival time. Any live booking."""
    from models import Guest
    from utils.phone import is_valid_mobile
    b = db.query(Booking).filter(Booking.booking_id == booking_id).with_for_update().first()
    if not b:
        raise HTTPException(status_code=404, detail="Booking not found")
    if b.status == "cancelled":
        raise HTTPException(status_code=409, detail="A cancelled booking cannot be edited")
    g = db.query(Guest).filter(Guest.guest_id == b.guest_id).first()

    def snap():
        return {"guest_name": b.guest_name, "phone": g.phone if g else None,
                "email": g.email if g else None, "adults": b.adults, "children": b.children,
                "check_in_time": str(b.check_in_time) if b.check_in_time else None,
                "admin_notes": b.admin_notes}

    before = snap()
    if data.guest_name is not None:
        b.guest_name = data.guest_name.strip()
    if data.phone is not None and g is not None:
        ph = data.phone.strip()
        if not is_valid_mobile(ph):
            raise HTTPException(status_code=422, detail="That is not a valid mobile number")
        g.phone = ph
    if data.email is not None and g is not None:
        g.email = data.email.strip() or None
    if data.adults is not None:
        b.adults = data.adults
    if data.children is not None:
        b.children = data.children
    if data.check_in_time is not None:
        b.check_in_time = data.check_in_time
        if b.status == "confirmed" and not ota_service.is_ota_source(b.booking_source, db):
            b.expected_arrival_at = datetime.combine(b.check_in, data.check_in_time)
    if data.admin_notes is not None:
        b.admin_notes = data.admin_notes.strip() or None
    after = {**snap(), "reason": data.reason}
    write_audit(db, user, "booking.details_edited", "booking", b.booking_id,
                before=before, after=after, client="web", commit=False)
    db.commit()
    return {"booking_id": b.booking_id, **after}


@router.put("/{booking_id}/rooms")
def edit_booking_rooms(booking_id: int, data: BookingRoomsEdit, db: Session = Depends(get_db),
                       user=Depends(require_admin)):
    """Change a line's room type, add / remove lines, or set the nightly rate - BEFORE check-in.

    Once a guest is in, their nights are posted to the bill and rooms are assigned; changing rooms
    then is a room SHIFT at the desk (which moves the card and the bill line), not an edit here.
    Capacity is checked for the booking's own dates with its current hold excluded; the booking's
    amounts are rebuilt from the lines. dry_run returns the new figures without saving."""
    from utils.rate_engine import quote_stay
    from services.room_posting import booking_channel
    b = db.query(Booking).filter(Booking.booking_id == booking_id).with_for_update().first()
    if not b:
        raise HTTPException(status_code=404, detail="Booking not found")
    if b.status not in ("confirmed", "no_show", "pending_payment", "payment_pending"):
        raise HTTPException(status_code=409,
                            detail=f"Rooms can only be edited before check-in (this booking is "
                                   f"'{b.status}'). For a guest already in, use Room Shift at the desk.")
    items = {i.booking_item_id: i for i in b.booking_items}
    if any(i.room_id or i.checked_in_at for i in items.values()):
        raise HTTPException(status_code=409, detail="A room on this booking is already assigned or checked in")
    for ln in data.lines:
        if ln.booking_item_id is not None and ln.booking_item_id not in items:
            raise HTTPException(status_code=422, detail=f"Line {ln.booking_item_id} is not on this booking")

    nights = max(1, (b.check_out - b.check_in).days)
    channel = (b.booking_source if ota_service.is_ota_source(b.booking_source, db)
               else booking_channel(b))
    rt_ids = {ln.room_type_id for ln in data.lines}
    rts = {rt.room_type_id: rt for rt in db.query(RoomType).filter(RoomType.room_type_id.in_(rt_ids)).all()}
    missing = rt_ids - set(rts)
    if missing:
        raise HTTPException(status_code=422, detail=f"Unknown room type(s): {sorted(missing)}")

    # Capacity for the new mix, this booking's own current hold excluded.
    need, held = {}, {}
    for ln in data.lines:
        need[ln.room_type_id] = need.get(ln.room_type_id, 0) + ln.quantity
    for i in items.values():
        held[i.room_type_id] = held.get(i.room_type_id, 0) + int(i.quantity or 1)
    counts_self = b.status in availability.RESERVED_STATUSES
    holds, _u = availability.ota_draft_holds(db, b.check_in, b.check_out,
                                              exclude_ota_booking_id=b.ota_booking_id)
    for rt_id, qty in need.items():
        availability.lock_room_type(db, rt_id)
        booked = int(availability.booked_qty(db, rt_id, b.check_in, b.check_out, lock=True))
        if counts_self:
            booked -= held.get(rt_id, 0)
        free = (int(rts[rt_id].total_rooms or 0) - booked
                - int(availability.out_of_service_count(db, rt_id)) - int(holds.get(rt_id, 0)))
        if free < qty:
            raise HTTPException(status_code=409,
                                detail=f"Only {max(free, 0)} {rts[rt_id].name} free for "
                                       f"{b.check_in:%d-%m-%Y} to {b.check_out:%d-%m-%Y}; {qty} asked")

    priced = []
    for ln in data.lines:
        rt = rts[ln.room_type_id]
        if ln.price_per_night is not None:
            base = round(float(ln.price_per_night) * nights * ln.quantity, 2)
            basis = "rate set by admin"
        else:
            base = round(float(quote_stay(db, rt, b.check_in, b.check_out, channel=channel,
                                          agent_id=b.agent_id, quantity=ln.quantity)["base"]), 2)
            basis = "rate card"
        gst = round(base * float(rt.gst_percent or 0) / 100, 2)
        priced.append({"line": ln, "rt": rt, "base": base, "gst": gst, "total": round(base + gst, 2),
                       "basis": basis, "price_per_night": round(base / nights / ln.quantity, 2)})

    before = {"lines": [{"booking_item_id": i.booking_item_id, "room_type_id": i.room_type_id,
                         "quantity": i.quantity, "total": _money(i.total_amount)} for i in items.values()],
              "base_amount": _money(b.base_amount), "gst_amount": _money(b.gst_amount),
              "total_amount": _money(b.total_amount), "grand_total": _money(b.grand_total)}
    new_base = round(sum(p["base"] for p in priced), 2)
    new_gst = round(sum(p["gst"] for p in priced), 2)
    new_total = round(new_base + new_gst - _money(b.discount_amount), 2)
    new_grand = round(new_total + _money(b.convenience_fee) + _money(b.convenience_gst), 2)
    after = {"lines": [{"booking_item_id": p["line"].booking_item_id, "room_type_id": p["rt"].room_type_id,
                        "room_type": p["rt"].name, "quantity": p["line"].quantity,
                        "price_per_night": p["price_per_night"], "basis": p["basis"],
                        "base": p["base"], "gst": p["gst"], "total": p["total"]} for p in priced],
             "nights": nights, "base_amount": new_base, "gst_amount": new_gst,
             "total_amount": new_total, "grand_total": new_grand}
    if data.dry_run:
        db.rollback()
        return {"booking_id": b.booking_id, "dry_run": True, "before": before, "after": after}

    keep = {ln.booking_item_id for ln in data.lines if ln.booking_item_id}
    for iid, item in items.items():
        if iid not in keep:
            db.delete(item)
    for p in priced:
        ln = p["line"]
        item = items.get(ln.booking_item_id) if ln.booking_item_id else None
        if item is None:
            item = BookingItem(booking_id=b.booking_id)
            db.add(item)
        item.room_type_id = ln.room_type_id
        item.quantity = ln.quantity
        item.base_amount = p["base"]
        item.gst_amount = p["gst"]
        item.discount_amount = 0
        item.total_amount = p["total"]
    b.base_amount, b.gst_amount, b.total_amount, b.grand_total = new_base, new_gst, new_total, new_grand
    if b.agent_id and b.commission_percent is not None:
        b.commission_amount = round(new_base * float(b.commission_percent) / 100, 2)
    write_audit(db, user, "booking.rooms_edited", "booking", b.booking_id,
                before=before, after={**after, "reason": data.reason}, client="web", commit=False)
    db.commit()
    return {"booking_id": b.booking_id, "dry_run": False, "before": before, "after": after}
