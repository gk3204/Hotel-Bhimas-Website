"""Reception desk endpoints.
Prompt 05: desk booking + availability (reuses the online engine's pricing/promotions,
CONFIRMED reservation, no convenience fee; room-type-level).
Prompt 06: check-in (assign physical rooms + KYC + open folio + card-encode payload),
check-out (settle folio, invalidate cards, room -> cleaning), desk board, registration slip.
Anti-fraud rule enforced here + in routers/cards.py: a key card is only issued against a
booking WITH a recorded payment (see routers/payments.py total_paid).
"""
import logging
import os
from datetime import date, datetime, time, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from database import SessionLocal
from models import Room, RoomType, Booking, Guest, RoomTypeAvailability, BookingItem, \
    Folio, CardIssuance
from schemas import DeskBookingCreate, CheckinRequest, CheckoutRequest, FolioOpenRequest
from utils.auth_utils import require_reception_or_admin
from utils.audit import write_audit
from utils.pdf_generator import generate_registration_slip_pdf
from routers.promotions import get_active_promotions, best_promotion_for_item
from routers.payments import total_paid
from routers.folio import open_folio
from scripts.expire_booking_jobs import expire_pending_bookings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/reception", tags=["Reception"])

# Statuses that reserve room capacity (mirror the online booking + availability logic).
RESERVED_STATUSES = ["confirmed", "pending_payment", "payment_pending"]


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("/health", dependencies=[Depends(require_reception_or_admin)])
def health():
    return {"status": "ok", "module": "reception"}


def _booked_qty(db, room_type_id, check_in, check_out, lock=False):
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


def _inactive_count(db, room_type_id):
    return db.query(func.count(Room.room_id)).filter(
        Room.room_type_id == room_type_id,
        Room.is_active == False,  # noqa: E712 (SQLAlchemy needs ==)
    ).scalar() or 0


def _blocked(db, room_type_id, check_in, check_out):
    return db.query(RoomTypeAvailability).filter(
        RoomTypeAvailability.room_type_id == room_type_id,
        RoomTypeAvailability.date >= check_in,
        RoomTypeAvailability.date < check_out,
        RoomTypeAvailability.is_available == False,  # noqa: E712
    ).first()


# ---- Availability for the desk room grid ----
@router.get("/availability", dependencies=[Depends(require_reception_or_admin)])
def desk_availability(
    check_in: str = Query(..., description="YYYY-MM-DD"),
    check_out: str = Query(..., description="YYYY-MM-DD"),
    db: Session = Depends(get_db),
):
    expire_pending_bookings(db)
    try:
        ci = datetime.strptime(check_in, "%Y-%m-%d").date()
        co = datetime.strptime(check_out, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD")
    if ci >= co:
        raise HTTPException(status_code=400, detail="Check-out must be after check-in")

    rows = []
    for rt in db.query(RoomType).filter(RoomType.is_active == True).order_by(RoomType.room_type_id).all():  # noqa: E712
        blk = _blocked(db, rt.room_type_id, ci, co)
        booked = int(_booked_qty(db, rt.room_type_id, ci, co))
        inactive = int(_inactive_count(db, rt.room_type_id))
        available = 0 if blk else max(0, rt.total_rooms - booked - inactive)
        rows.append({
            "room_type_id": rt.room_type_id,
            "room_type_name": rt.name,
            "price_per_night": float(rt.price_per_night),
            "gst_percent": float(rt.gst_percent),
            "total_rooms": rt.total_rooms,
            "booked": booked,
            "inactive": inactive,
            "available": available,
            "blocked": bool(blk),
            "block_reason": blk.reason if blk else None,
        })
    return {"check_in": str(ci), "check_out": str(co), "room_types": rows}


# ---- Create a desk booking (walk-in or advance; single or group) ----
@router.post("/bookings", dependencies=[Depends(require_reception_or_admin)])
def create_desk_booking(data: DeskBookingCreate, db: Session = Depends(get_db)):
    try:
        expire_pending_bookings(db)

        today = date.today()
        if data.check_in < today:
            raise HTTPException(status_code=400, detail="Check-in date cannot be in the past")
        if data.check_out <= data.check_in:
            raise HTTPException(status_code=400, detail="Check-out must be after check-in")

        # Validate + availability (row-locked to prevent concurrent overbooking)
        rt_map = {}
        for item in data.rooms:
            rt = db.query(RoomType).filter(
                RoomType.room_type_id == item.room_type_id,
                RoomType.is_active == True,  # noqa: E712
            ).first()
            if not rt:
                raise HTTPException(status_code=400, detail=f"Room type {item.room_type_id} not available")

            blk = _blocked(db, item.room_type_id, data.check_in, data.check_out)
            if blk:
                raise HTTPException(status_code=409,
                                    detail=f"{rt.name} not available for selected dates. Reason: {blk.reason or 'blocked'}")

            booked = _booked_qty(db, item.room_type_id, data.check_in, data.check_out, lock=True)
            inactive = _inactive_count(db, item.room_type_id)
            available = rt.total_rooms - int(booked) - int(inactive)
            if item.quantity > available:
                raise HTTPException(status_code=409,
                                    detail=f"Not enough {rt.name}s available. Available: {max(0, available)}, Requested: {item.quantity}")
            rt_map[item.room_type_id] = rt

        # Guest
        guest = Guest(name=data.guest_name, phone=data.phone, email=data.email)
        db.add(guest)
        db.flush()

        # Pricing (same math as online, auto-promotions, but NO convenience fee)
        nights = (data.check_out - data.check_in).days
        promos = get_active_promotions(db)
        total_base = total_gst = total_discount = total_room = 0.0
        items_data = []
        for item in data.rooms:
            rt = rt_map[item.room_type_id]
            base = round(nights * float(rt.price_per_night) * item.quantity, 2)
            _, disc = best_promotion_for_item(promos, item.room_type_id, base, data.check_in)
            dbase = round(base - disc, 2)
            gst = round(dbase * float(rt.gst_percent) / 100, 2)
            itotal = round(dbase + gst, 2)
            total_base += base
            total_gst += gst
            total_discount += disc
            total_room += itotal
            items_data.append({
                "room_type_id": item.room_type_id, "quantity": item.quantity,
                "base_amount": base, "gst_amount": gst, "discount_amount": disc, "total_amount": itotal,
            })

        booking = Booking(
            guest_id=guest.guest_id,
            check_in=data.check_in,
            check_in_time=data.check_in_time,
            check_out=data.check_out,
            booking_source=data.booking_source,
            status="confirmed",
            base_amount=round(total_base, 2),
            gst_amount=round(total_gst, 2),
            discount_amount=round(total_discount, 2),
            total_amount=round(total_room, 2),
            convenience_fee=0,
            convenience_gst=0,
            grand_total=round(total_room, 2),  # desk booking: no convenience fee
        )
        db.add(booking)
        db.flush()
        for d in items_data:
            db.add(BookingItem(booking_id=booking.booking_id, **d))
        db.commit()
        db.refresh(booking)

        write_audit(db, None, "booking.desk_create", "booking", booking.booking_id,
                    after={"guest": data.guest_name, "check_in": str(data.check_in),
                           "check_out": str(data.check_out), "source": data.booking_source,
                           "rooms": len(data.rooms), "grand_total": round(total_room, 2)},
                    client="desktop", commit=True)

        return {
            "booking_id": booking.booking_id,
            "guest_name": guest.name,
            "status": booking.status,
            "booking_source": booking.booking_source,
            "check_in": str(booking.check_in),
            "check_out": str(booking.check_out),
            "nights": nights,
            "base_amount": float(booking.base_amount),
            "gst_amount": float(booking.gst_amount),
            "discount_amount": float(booking.discount_amount),
            "grand_total": float(booking.grand_total),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"create_desk_booking failed: {e}", exc_info=True)
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to create booking")


# =====================================================================
# CHECK-IN / CHECK-OUT (prompt 06)
# =====================================================================

def _checkout_hour() -> int:
    """Hotel checkout hour (24h). Read at call time so it can change without restart."""
    try:
        return max(0, min(23, int(os.getenv("CHECKOUT_HOUR", "12"))))
    except ValueError:
        return 12


def _grace_minutes() -> int:
    """Extra minutes a card stays valid past the checkout hour (lock-clock drift buffer)."""
    try:
        return max(0, int(os.getenv("CARD_GRACE_MINUTES", "60")))
    except ValueError:
        return 60


def _checkout_override_requires_admin() -> bool:
    return os.getenv("CHECKOUT_OVERRIDE_REQUIRES_ADMIN", "true").strip().lower() not in ("false", "0", "no")


def _card_window(booking: Booking):
    """Card validity: from now until the stay's checkout hour + grace."""
    valid_from = datetime.now()
    valid_to = datetime.combine(booking.check_out, time(hour=_checkout_hour())) + \
        timedelta(minutes=_grace_minutes())
    return valid_from, valid_to


def _room_code(room: Room) -> str:
    """BBFFRRAA code written to the card (area fixed at 99 for this property)."""
    try:
        rr = int(room.room_number)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=400,
            detail=f"Room '{room.room_number}' has a non-numeric number — cannot build a card room code")
    if not 0 <= rr <= 99:
        raise HTTPException(
            status_code=400,
            detail=f"Room number {rr} out of card range (00-99)")
    return f"{room.building:02d}{room.floor:02d}{rr:02d}99"


def _active_cards(db: Session, room_id: int) -> int:
    return db.query(func.count(CardIssuance.id)).filter(
        CardIssuance.room_id == room_id,
        CardIssuance.status == "active",
    ).scalar() or 0


def _encode_payload(db: Session, item: BookingItem, room: Room, valid_from, valid_to):
    """Everything the desktop needs to encode one guest card for one room."""
    return {
        "booking_item_id": item.booking_item_id,
        "room_id": room.room_id,
        "room_number": room.room_number,
        "building": room.building,
        "floor": room.floor,
        "room": int(room.room_number),
        "area": 99,
        "room_code": _room_code(room),
        "valid_from": valid_from.isoformat(),
        "valid_to": valid_to.isoformat(),
        "max_cards": room.max_cards,
        "active_cards": _active_cards(db, room.room_id),
    }


def _checkin_response(db: Session, booking: Booking, folio: Folio | None, already: bool):
    valid_from, valid_to = _card_window(booking)
    cards = []
    for item in booking.booking_items:
        if item.room_id:
            room = db.query(Room).filter(Room.room_id == item.room_id).first()
            if room:
                cards.append(_encode_payload(db, item, room, valid_from, valid_to))
    return {
        "booking_id": booking.booking_id,
        "status": booking.status,
        "already_checked_in": already,
        "guest_name": booking.guest.name if booking.guest else None,
        "checked_in_at": booking.checked_in_at.isoformat() if booking.checked_in_at else None,
        "folio_id": folio.id if folio else None,
        "folio_balance": float(folio.balance or 0) if folio else None,
        "cards": cards,
    }


@router.post("/checkin")
def check_in(data: CheckinRequest, db: Session = Depends(get_db),
             user=Depends(require_reception_or_admin)):
    """Check a guest in: validate booking + recorded payment (anti-fraud gate), assign
    specific active vacant rooms, store masked KYC, open the folio (idempotent), set
    rooms occupied, and return the card-encode payload per room. Idempotent."""
    try:
        expire_pending_bookings(db)

        booking = db.query(Booking).options(
            joinedload(Booking.guest), joinedload(Booking.booking_items),
        ).filter(Booking.booking_id == data.booking_id).first()
        if not booking:
            raise HTTPException(status_code=404, detail="Booking not found")

        # Idempotent: an outbox re-flush (or double-click) just re-returns the payload
        # and repairs a missing folio.
        if booking.status == "checked_in":
            open_folio(FolioOpenRequest(booking_id=booking.booking_id), db, user)
            folio = db.query(Folio).filter(Folio.booking_id == booking.booking_id).first()
            return _checkin_response(db, booking, folio, already=True)
        if booking.status != "confirmed":
            raise HTTPException(status_code=409,
                                detail=f"Cannot check in a '{booking.status}' booking")

        today = date.today()
        if today < booking.check_in:
            raise HTTPException(status_code=400,
                                detail=f"Booking starts on {booking.check_in:%d-%m-%Y} — too early to check in")
        if today >= booking.check_out:
            raise HTTPException(status_code=400,
                                detail=f"Stay window lapsed on {booking.check_out:%d-%m-%Y}")

        # Anti-fraud gate: no recorded payment => no check-in (and no card).
        if total_paid(db, booking.booking_id) <= 0:
            raise HTTPException(status_code=409,
                                detail="No payment recorded for this booking — record a desk payment first")

        # ---- validate the room assignments ----
        items = {i.booking_item_id: i for i in booking.booking_items}
        assigned_ids = [a.booking_item_id for a in data.assignments]
        if sorted(assigned_ids) != sorted(items.keys()):
            raise HTTPException(status_code=400,
                                detail="Assignments must cover every booking item exactly once")
        all_room_ids = [rid for a in data.assignments for rid in a.room_ids]
        if len(all_room_ids) != len(set(all_room_ids)):
            raise HTTPException(status_code=400, detail="The same room was assigned twice")

        rooms_by_id = {}
        for a in data.assignments:
            item = items[a.booking_item_id]
            if len(a.room_ids) != (item.quantity or 1):
                raise HTTPException(
                    status_code=400,
                    detail=f"Booking item {a.booking_item_id} needs {item.quantity} room(s), got {len(a.room_ids)}")
            for rid in a.room_ids:
                room = db.query(Room).filter(Room.room_id == rid).with_for_update().first()
                if not room:
                    raise HTTPException(status_code=404, detail=f"Room id {rid} not found")
                if not room.is_active:
                    raise HTTPException(status_code=409,
                                        detail=f"Room {room.room_number} is out of service")
                if room.status != "vacant":
                    raise HTTPException(status_code=409,
                                        detail=f"Room {room.room_number} is not vacant (status: {room.status})")
                if room.room_type_id != item.room_type_id:
                    raise HTTPException(status_code=409,
                                        detail=f"Room {room.room_number} is not a {item.room_type.name if item.room_type else 'matching'} room")
                held = db.query(BookingItem).join(Booking).filter(
                    BookingItem.room_id == rid,
                    Booking.status == "checked_in",
                ).first()
                if held:
                    raise HTTPException(status_code=409,
                                        detail=f"Room {room.room_number} is already held by booking {held.booking_id}")
                _room_code(room)  # fail early on a non-numeric room number
                rooms_by_id[rid] = room

        # ---- assign rooms (split multi-quantity items into one row per room) ----
        for a in data.assignments:
            item = items[a.booking_item_id]
            qty = item.quantity or 1
            if qty == 1:
                item.room_id = a.room_ids[0]
            else:
                # Split amounts per room, last row absorbs the rounding remainder
                # (same paisa-exact approach as folio room lines).
                def _split(total):
                    total = float(total or 0)
                    per = round(total / qty, 2)
                    return [per] * (qty - 1) + [round(total - per * (qty - 1), 2)]
                bases, gsts = _split(item.base_amount), _split(item.gst_amount)
                discs, totals = _split(item.discount_amount), _split(item.total_amount)
                item.quantity = 1
                item.room_id = a.room_ids[0]
                item.base_amount, item.gst_amount = bases[0], gsts[0]
                item.discount_amount, item.total_amount = discs[0], totals[0]
                for n, rid in enumerate(a.room_ids[1:], start=1):
                    db.add(BookingItem(
                        booking_id=booking.booking_id,
                        room_type_id=item.room_type_id,
                        room_id=rid,
                        quantity=1,
                        base_amount=bases[n], gst_amount=gsts[n],
                        discount_amount=discs[n], total_amount=totals[n],
                    ))

        # ---- KYC (masked; the raw ID number is never stored) ----
        booking.guest.id_type = data.id_type
        booking.guest.id_number_masked = "****" + data.id_number[-4:]

        booking.status = "checked_in"
        booking.checked_in_at = datetime.now()
        for room in rooms_by_id.values():
            room.status = "occupied"
        db.commit()

        # Folio (idempotent; posts room charges + advance payment credits, own commit).
        open_folio(FolioOpenRequest(booking_id=booking.booking_id), db, user)
        folio = db.query(Folio).filter(Folio.booking_id == booking.booking_id).first()

        write_audit(db, user, "reception.checkin", "booking", booking.booking_id,
                    after={"rooms": [r.room_number for r in rooms_by_id.values()],
                           "id_type": data.id_type,
                           "id_number_masked": booking.guest.id_number_masked,
                           "paid_total": total_paid(db, booking.booking_id),
                           "client_ref": data.client_ref},
                    client="desktop", commit=True)

        db.refresh(booking)
        return _checkin_response(db, booking, folio, already=False)
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        logger.error(f"check_in failed: {e}", exc_info=True)
        db.rollback()
        raise HTTPException(status_code=500, detail="Check-in failed")


@router.post("/checkout")
def check_out(data: CheckoutRequest, db: Session = Depends(get_db),
              user=Depends(require_reception_or_admin)):
    """Check a guest out: folio balance must be 0 (or an authorised override with a
    reason), folio -> settled, rooms -> cleaning, active cards -> checked_out. Idempotent."""
    try:
        booking = db.query(Booking).options(
            joinedload(Booking.booking_items),
        ).filter(Booking.booking_id == data.booking_id).first()
        if not booking:
            raise HTTPException(status_code=404, detail="Booking not found")

        folio = db.query(Folio).filter(Folio.booking_id == booking.booking_id).first()

        if booking.status == "checked_out":
            return _checkout_response(db, booking, folio, override=False, already=True)
        if booking.status != "checked_in":
            raise HTTPException(status_code=409,
                                detail=f"Cannot check out a '{booking.status}' booking")
        if not folio:
            raise HTTPException(status_code=409, detail="No folio for this booking — open it first")

        balance = round(float(folio.balance or 0), 2)
        override_used = False
        if balance != 0:
            if not data.override:
                raise HTTPException(
                    status_code=409,
                    detail=f"Folio balance is ₹{balance:,.2f} — settle it before checkout (or use an admin override)")
            if not data.override_reason:
                raise HTTPException(status_code=400, detail="Override needs a reason")
            if _checkout_override_requires_admin() and user.get("role") != "admin":
                raise HTTPException(status_code=403,
                                    detail="Checkout override requires an admin login")
            override_used = True

        before = {"booking_status": booking.status, "folio_balance": balance}

        folio.status = "settled"
        folio.settled_at = datetime.now()
        booking.status = "checked_out"
        booking.checked_out_at = datetime.now()

        rooms = []
        for item in booking.booking_items:
            if item.room_id:
                room = db.query(Room).filter(Room.room_id == item.room_id).first()
                if room and room.status == "occupied":
                    room.status = "cleaning"
                if room:
                    rooms.append(room)

        cards = db.query(CardIssuance).filter(
            CardIssuance.booking_id == booking.booking_id,
            CardIssuance.status == "active",
        ).all()
        for c in cards:
            c.status = "checked_out"

        db.commit()

        write_audit(db, user, "reception.checkout", "booking", booking.booking_id,
                    before=before,
                    after={"override": override_used, "override_reason": data.override_reason,
                           "cards_invalidated": len(cards),
                           "rooms": [r.room_number for r in rooms],
                           "client_ref": data.client_ref},
                    client="desktop", commit=True)

        return _checkout_response(db, booking, folio, override=override_used, already=False)
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        logger.error(f"check_out failed: {e}", exc_info=True)
        db.rollback()
        raise HTTPException(status_code=500, detail="Checkout failed")


def _checkout_response(db: Session, booking: Booking, folio: Folio | None, override: bool, already: bool):
    rooms = []
    for item in booking.booking_items:
        if item.room_id:
            room = db.query(Room).filter(Room.room_id == item.room_id).first()
            if room:
                rooms.append({"room_id": room.room_id, "room_number": room.room_number,
                              "status": room.status})
    cards_out = db.query(func.count(CardIssuance.id)).filter(
        CardIssuance.booking_id == booking.booking_id,
        CardIssuance.status == "checked_out",
    ).scalar() or 0
    return {
        "booking_id": booking.booking_id,
        "status": booking.status,
        "already_checked_out": already,
        "checked_out_at": booking.checked_out_at.isoformat() if booking.checked_out_at else None,
        "folio_id": folio.id if folio else None,
        "folio_status": folio.status if folio else None,
        "balance": float(folio.balance or 0) if folio else None,
        "override": override,
        "cards_invalidated": cards_out,
        "rooms": rooms,
    }


# ---- Desk board: arrivals + in-house + vacant rooms (also the desktop's offline cache) ----
@router.get("/board")
def desk_board(db: Session = Depends(get_db), user=Depends(require_reception_or_admin)):
    expire_pending_bookings(db)
    today = date.today()

    def _paid_and_folio(booking_id):
        folio = db.query(Folio).filter(Folio.booking_id == booking_id).first()
        return (total_paid(db, booking_id),
                folio.id if folio else None,
                float(folio.balance or 0) if folio else None)

    arrivals = []
    for b in db.query(Booking).options(
            joinedload(Booking.guest),
            joinedload(Booking.booking_items).joinedload(BookingItem.room_type),
    ).filter(
        Booking.status == "confirmed",
        Booking.check_in <= today,
        Booking.check_out > today,
    ).order_by(Booking.check_in_time, Booking.booking_id).all():
        paid, folio_id, folio_balance = _paid_and_folio(b.booking_id)
        arrivals.append({
            "booking_id": b.booking_id,
            "guest_name": b.guest.name if b.guest else None,
            "phone": b.guest.phone if b.guest else None,
            "check_in": str(b.check_in),
            "check_in_time": str(b.check_in_time) if b.check_in_time else None,
            "check_out": str(b.check_out),
            "booking_source": b.booking_source,
            "grand_total": float(b.grand_total or 0),
            "paid_total": paid,
            "folio_id": folio_id,
            "folio_balance": folio_balance,
            "items": [{
                "booking_item_id": i.booking_item_id,
                "room_type_id": i.room_type_id,
                "room_type_name": i.room_type.name if i.room_type else None,
                "quantity": i.quantity,
            } for i in b.booking_items],
        })

    inhouse = []
    for b in db.query(Booking).options(
            joinedload(Booking.guest),
            joinedload(Booking.booking_items).joinedload(BookingItem.room_type),
    ).filter(Booking.status == "checked_in").order_by(Booking.check_out, Booking.booking_id).all():
        paid, folio_id, folio_balance = _paid_and_folio(b.booking_id)
        valid_from, valid_to = _card_window(b)
        rooms = []
        for i in b.booking_items:
            if i.room_id:
                room = db.query(Room).filter(Room.room_id == i.room_id).first()
                if room:
                    rooms.append({
                        "room_id": room.room_id,
                        "room_number": room.room_number,
                        "building": room.building,
                        "floor": room.floor,
                        "max_cards": room.max_cards,
                        "active_cards": _active_cards(db, room.room_id),
                    })
        inhouse.append({
            "booking_id": b.booking_id,
            "guest_name": b.guest.name if b.guest else None,
            "phone": b.guest.phone if b.guest else None,
            "check_in": str(b.check_in),
            "check_out": str(b.check_out),
            "checked_in_at": b.checked_in_at.isoformat() if b.checked_in_at else None,
            "grand_total": float(b.grand_total or 0),
            "paid_total": paid,
            "folio_id": folio_id,
            "folio_balance": folio_balance,
            "valid_from": valid_from.isoformat(),
            "valid_to": valid_to.isoformat(),
            "rooms": rooms,
        })

    vacant_rooms = [{
        "room_id": r.room_id,
        "room_number": r.room_number,
        "room_type_id": r.room_type_id,
        "building": r.building,
        "floor": r.floor,
        "max_cards": r.max_cards,
    } for r in db.query(Room).filter(
        Room.is_active == True,  # noqa: E712
        Room.status == "vacant",
    ).order_by(Room.room_number).all()]

    return {"date": str(today), "arrivals": arrivals, "inhouse": inhouse,
            "vacant_rooms": vacant_rooms}


# ---- Registration slip (printed at check-in, guest signs it) ----
@router.get("/checkin/{booking_id}/slip")
def registration_slip(booking_id: int, db: Session = Depends(get_db),
                      user=Depends(require_reception_or_admin)):
    booking = db.query(Booking).options(
        joinedload(Booking.guest),
        joinedload(Booking.booking_items).joinedload(BookingItem.room_type),
    ).filter(Booking.booking_id == booking_id).first()
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")
    if booking.status not in ("checked_in", "checked_out"):
        raise HTTPException(status_code=409, detail="Registration slip is available after check-in")

    folio = db.query(Folio).filter(Folio.booking_id == booking_id).first()
    rooms = []
    for i in booking.booking_items:
        if i.room_id:
            room = db.query(Room).filter(Room.room_id == i.room_id).first()
            if room:
                rooms.append(room.room_number)

    slip_data = {
        "booking_id": booking.booking_id,
        "guest_name": booking.guest.name if booking.guest else "",
        "phone": booking.guest.phone if booking.guest else "",
        "email": booking.guest.email if booking.guest else None,
        "id_type": booking.guest.id_type if booking.guest else None,
        "id_number_masked": booking.guest.id_number_masked if booking.guest else None,
        "rooms": rooms,
        "check_in": booking.check_in,
        "check_out": booking.check_out,
        "checked_in_at": booking.checked_in_at,
        "booking_source": booking.booking_source,
        "grand_total": float(booking.grand_total or 0),
        "paid_total": total_paid(db, booking_id),
        "balance": float(folio.balance or 0) if folio else None,
    }
    try:
        pdf_path = generate_registration_slip_pdf(slip_data)
    except Exception as e:
        logger.error(f"registration slip PDF failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to generate registration slip")

    write_audit(db, user, "reception.slip_print", "booking", booking_id,
                after={"guest": slip_data["guest_name"]}, client="desktop", commit=True)
    return FileResponse(pdf_path, media_type="application/pdf",
                        filename=f"registration_slip_{booking_id}.pdf")
