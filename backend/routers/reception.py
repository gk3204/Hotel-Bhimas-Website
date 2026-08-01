"""Reception desk endpoints.
Prompt 05: desk booking + availability (reuses the online engine's pricing/promotions,
CONFIRMED reservation, no convenience fee; room-type-level).
Prompt 06: check-in (assign physical rooms + KYC + open folio + card-encode payload),
check-out (settle folio, invalidate cards, room -> cleaning), desk board, registration slip.
Prompt 09: room shift (move an in-house guest to another room mid-stay: re-point the
booking item, folio follows the booking, rate difference posted explicitly, old cards
superseded, new-room card-encode payload returned).
Anti-fraud rule enforced here + in routers/cards.py: a key card is only issued against a
booking WITH a recorded payment (see routers/payments.py total_paid).
"""
import io
import logging
import os
from datetime import date, datetime, time, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from database import SessionLocal
from models import Room, RoomType, Booking, Guest, RoomTypeAvailability, BookingItem, \
    Folio, FolioCharge, CardIssuance, TravelAgent, Company, BookingGuest
from utils import secure_id_store
from schemas import DeskBookingCreate, CheckinRequest, CheckoutRequest, FolioOpenRequest, \
    RoomShiftRequest
from utils.auth_utils import require_reception_or_admin
from utils.audit import write_audit, _resolve_user_id
from utils.pdf_generator import generate_registration_slip_pdf
from utils.rate_engine import quote_stay
from utils.housekeeping import on_room_dirtied, set_hk_status
from models import HousekeepingStatus
from routers.promotions import get_active_promotions, best_promotion_for_item
from routers.payments import total_paid
from routers.folio import open_folio, _recompute, _get_invoice
from routers.crm import check_guest_gate, match_guest, accrue_loyalty_on_checkout
from scripts.expire_booking_jobs import expire_pending_bookings
from services import company_service, ota_service

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
@router.post("/bookings")
def create_desk_booking(data: DeskBookingCreate, db: Session = Depends(get_db),
                        user=Depends(require_reception_or_admin)):
    try:
        expire_pending_bookings(db)

        today = date.today()
        if data.check_in < today:
            raise HTTPException(status_code=400, detail="Check-in date cannot be in the past")
        if data.check_out <= data.check_in:
            raise HTTPException(status_code=400, detail="Check-out must be after check-in")

        # Resolve the pricing channel/agent (prompt 10). An agent booking prices from
        # that agent's negotiated rate/plans; commission is accrued on the booking.
        agent = None
        if data.agent_id:
            agent = db.query(TravelAgent).filter(
                TravelAgent.id == data.agent_id,
                TravelAgent.is_active == True,  # noqa: E712
            ).first()
            if not agent:
                raise HTTPException(status_code=400, detail="Travel agent not found or inactive")
        channel = "agent" if agent else "walk_in"

        # Corporate bill-to (prompt 18 slice 7). Validated here so a walk-in that the desk knows
        # is a company guest is flagged from the start; the folio is actually routed at check-in
        # (routers/folio.py POST /folio/{id}/bill-to), once the charges exist.
        company = None
        if data.company_id:
            company = company_service.get_company(db, data.company_id, active_only=True)
            if not company:
                raise HTTPException(status_code=400, detail="Company not found or inactive")

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

        # Guest — reuse an existing record on an exact phone match (repeat-guest
        # recognition, prompt 14) so history + loyalty attach to one customer; else create.
        guest = match_guest(db, data.phone)
        blacklist_warning = None
        if guest:
            gate = check_guest_gate(db, guest)
            if gate["blacklist"]:
                # 'block' stops a non-admin at booking; admin may proceed (audited). 'warn'
                # always proceeds and surfaces a banner in the response.
                if gate["enforcement"] == "block" and user.get("role") != "admin":
                    raise HTTPException(
                        status_code=409,
                        detail=f"Guest is blacklisted: {gate['reason'] or 'no reason on file'}. "
                               f"An admin login is required to override and book.")
                blacklist_warning = gate["reason"] or "Guest is on the watchlist"
            # keep the thin record fresh if the caller corrected name/email
            if data.guest_name:
                guest.name = data.guest_name
            if data.email:
                guest.email = data.email
        else:
            guest = Guest(name=data.guest_name, phone=data.phone, email=data.email)
            db.add(guest)
        db.flush()

        # Pricing (auto-promotions, NO convenience fee). The per-night base comes from
        # the shared rate engine (rack / agent rate / seasonal-weekend plan by channel+date),
        # then promotions + GST apply exactly as before.
        nights = (data.check_out - data.check_in).days
        promos = get_active_promotions(db)
        total_base = total_gst = total_discount = total_room = 0.0
        items_data = []
        for item in data.rooms:
            rt = rt_map[item.room_type_id]
            base = quote_stay(db, rt, data.check_in, data.check_out,
                              channel=channel, agent_id=data.agent_id,
                              quantity=item.quantity)["base"]
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

        # Commission accrues on agent bookings: % of the discounted (pre-GST) room value.
        commission_percent = float(agent.commission_percent or 0) if agent else None
        commission_amount = None
        if agent:
            commission_amount = round((round(total_base, 2) - round(total_discount, 2)) * commission_percent / 100, 2)

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
            agent_id=agent.id if agent else None,
            commission_percent=commission_percent,
            commission_amount=commission_amount,
            company_id=company.id if company else None,
            bill_to=(data.bill_to or "guest") if company else "guest",
        )
        db.add(booking)
        db.flush()
        # OTA tracking snapshot (prompt 17): stamp OTA booking id + commission + net payout when the
        # source is an OTA channel. Commission % = the desk override else the per-OTA config default.
        ota_service.apply_ota_fields(db, booking, data.booking_source,
                                     ota_booking_id=data.ota_booking_id,
                                     commission_percent_override=data.ota_commission_percent)
        for d in items_data:
            db.add(BookingItem(booking_id=booking.booking_id, **d))
        db.commit()
        db.refresh(booking)

        # `user`, not None: the audit trail must record WHO took the booking. Anonymous desk
        # bookings were an audit gap; the staff-performance report (prompt 18 slice 9) reads this.
        write_audit(db, user, "booking.desk_create", "booking", booking.booking_id,
                    after={"guest": data.guest_name, "check_in": str(data.check_in),
                           "check_out": str(data.check_out), "source": data.booking_source,
                           "rooms": len(data.rooms), "grand_total": round(total_room, 2),
                           "agent_id": agent.id if agent else None,
                           "commission_amount": commission_amount,
                           "company_id": booking.company_id, "bill_to": booking.bill_to},
                    client="desktop", commit=True)

        # Best-effort WhatsApp booking confirmation (prompt 15) — must not break booking.
        _wa_notify(db, "wa_confirmation_enabled", guest, "booking_confirmation",
                   {"guest_name": guest.name, "check_in": str(data.check_in),
                    "check_out": str(data.check_out), "booking_ref": str(booking.booking_id)},
                   booking_id=booking.booking_id,
                   client_ref=f"confirmation:{booking.booking_id}")

        return {
            "booking_id": booking.booking_id,
            "guest_name": guest.name,
            "status": booking.status,
            "booking_source": booking.booking_source,
            "agent_id": booking.agent_id,
            "check_in": str(booking.check_in),
            "check_out": str(booking.check_out),
            "nights": nights,
            "base_amount": float(booking.base_amount),
            "gst_amount": float(booking.gst_amount),
            "discount_amount": float(booking.discount_amount),
            "grand_total": float(booking.grand_total),
            "commission_percent": commission_percent,
            "commission_amount": commission_amount,
            "ota_booking_id": booking.ota_booking_id,
            "ota_commission_percent": float(booking.ota_commission_percent) if booking.ota_commission_percent is not None else None,
            "ota_net_payout": float(booking.ota_net_payout) if booking.ota_net_payout is not None else None,
            "guest_id": guest.guest_id,
            "blacklist_warning": blacklist_warning,   # prompt 14: non-null when a watchlisted guest was booked
            "company_id": booking.company_id,
            "company_name": company.name if company else None,
            "bill_to": booking.bill_to,
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

def _checkout_mode() -> str:
    """'24h' (default): checkout is N x 24h after the ACTUAL check-in moment (Hotel Bhimas
    policy — check in 10pm, check out 10pm). 'fixed': checkout at CHECKOUT_HOUR on the
    checkout date. Read at call time so it can change without restart."""
    return "fixed" if os.getenv("CHECKOUT_MODE", "24h").strip().lower() == "fixed" else "24h"


def _checkout_hour() -> int:
    """Fixed-mode checkout hour (used only when CHECKOUT_MODE=fixed)."""
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


def booking_checkout_moment(booking: Booking, ref=None) -> datetime:
    """The real checkout moment for a booking (the shared anchor for BOTH card expiry and
    the '2h before checkout' WhatsApp reminder — prompt 15). This is the checkout time
    BEFORE the grace buffer.
    24h mode (default): the ACTUAL check-in moment + nights x 24h.
    Fixed mode: checkout date @ CHECKOUT_HOUR.
    `ref` stands in for the check-in moment before the guest is checked in."""
    ref = ref or datetime.now()
    nights = max(1, (booking.check_out - booking.check_in).days)
    if _checkout_mode() == "fixed":
        return datetime.combine(booking.check_out, time(hour=_checkout_hour()))
    return (booking.checked_in_at or ref) + timedelta(days=nights)


def _card_window(booking: Booking):
    """Card validity window.
    24h mode (default): expiry = the ACTUAL check-in moment + nights x 24h + grace
    (guest in at 10pm -> out at 10pm). Before check-in, 'now' stands in for the
    check-in moment (the wizard checks in and encodes within the same minute).
    Fixed mode: expiry = checkout date @ CHECKOUT_HOUR + grace."""
    valid_from = datetime.now()
    base = booking_checkout_moment(booking, ref=valid_from)
    return valid_from, base + timedelta(minutes=_grace_minutes())


def _wa_notify(db, setting_key, guest, template, params, booking_id=None, client_ref=None):
    """Best-effort transactional WhatsApp send (prompt 15), gated by an app_settings toggle.
    Never raises — a messaging failure must not break check-in / booking / payment."""
    try:
        if not guest or not getattr(guest, "phone", None):
            return
        from utils.settings import get_setting
        if str(get_setting(db, setting_key, "true")).strip().lower() in ("false", "0", "no", ""):
            return
        from utils import whatsapp_service
        whatsapp_service.send_template(db, guest.phone, template, params,
                                       guest_id=getattr(guest, "guest_id", None),
                                       booking_id=booking_id, client_ref=client_ref)
    except Exception as e:
        logger.warning(f"transactional WhatsApp ({template}) failed: {e}")


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

        # Blacklist/watchlist gate (prompt 14): 'block' stops a non-admin at check-in;
        # 'warn' surfaces a banner (returned as blacklist_warning) but proceeds.
        checkin_gate = check_guest_gate(db, booking.guest)
        if checkin_gate["blacklist"]:
            if checkin_gate["enforcement"] == "block" and user.get("role") != "admin":
                raise HTTPException(
                    status_code=409,
                    detail=f"Guest is blacklisted: {checkin_gate['reason'] or 'no reason on file'}. "
                           f"An admin login is required to override and check in.")

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

        # Per-occupant KYC roster (FE-3): rebuild booking_guests (lead + companions). The desk sends
        # the full roster in `additional_guests` (each row incl. the primary); an empty list keeps the
        # old single-guest behaviour. ID numbers masked here; scans were uploaded encrypted beforehand.
        db.query(BookingGuest).filter(BookingGuest.booking_id == booking.booking_id).delete()
        roster = list(data.additional_guests or [])
        if roster:
            for g in roster:
                db.add(BookingGuest(
                    booking_id=booking.booking_id,
                    name=(g.name or "").strip() or (booking.guest.name if booking.guest else ""),
                    id_type=g.id_type,
                    id_number_masked=("****" + g.id_number[-4:]) if g.id_number else None,
                    id_scan_ref=g.id_scan_ref,
                    id_scan_mime=g.id_scan_mime,
                    is_primary=g.is_primary,
                ))
        else:
            db.add(BookingGuest(
                booking_id=booking.booking_id,
                name=booking.guest.name if booking.guest else "",
                id_type=data.id_type,
                id_number_masked=booking.guest.id_number_masked,
                is_primary=True,
            ))

        booking.status = "checked_in"
        booking.checked_in_at = datetime.now()
        for room in rooms_by_id.values():
            room.status = "occupied"
            room.status_changed_at = datetime.utcnow()  # prompt 11: cleaning-too-long detection
        db.commit()

        # Folio (idempotent; posts room charges + advance payment credits, own commit).
        open_folio(FolioOpenRequest(booking_id=booking.booking_id), db, user)
        folio = db.query(Folio).filter(Folio.booking_id == booking.booking_id).first()

        # Corporate bill-to (prompt 18 slice 7): the booking was flagged for a company, so route
        # the freshly-posted ROOM lines to it now. `bill_to='company'` routes everything the desk
        # posts later too (via the folio screen); `split` routes only the room, leaving the guest
        # to settle incidentals. Best-effort: a credit problem must not block a check-in — the
        # desk sees the unrouted folio and can resolve it with an admin.
        company_routing = None
        if folio and booking.company_id and (booking.bill_to or "guest") != "guest":
            try:
                company = company_service.get_company(db, booking.company_id, active_only=True)
                if company:
                    room_ids = None
                    if booking.bill_to == "split":
                        room_ids = [c.id for c in company_service.routable_charges(db, folio.id)
                                    if c.type == "room"]
                    folio.company_id = company.id
                    company_routing = company_service.route_charges(
                        db, folio, company, charge_ids=room_ids)
                    _recompute(db, folio)
                    db.commit()
                    company_routing["company_name"] = company.name
                    company_routing["credit"] = company_service.check_credit(db, company)
            except Exception as e:
                logger.error(f"company routing failed at check-in for booking "
                             f"{booking.booking_id}: {e}", exc_info=True)
                db.rollback()
                company_routing = None

        write_audit(db, user, "reception.checkin", "booking", booking.booking_id,
                    after={"rooms": [r.room_number for r in rooms_by_id.values()],
                           "id_type": data.id_type,
                           "id_number_masked": booking.guest.id_number_masked,
                           "paid_total": total_paid(db, booking.booking_id),
                           "company_routing": company_routing,
                           "client_ref": data.client_ref},
                    client="desktop", commit=True)

        # Best-effort WhatsApp welcome / room-ready (prompt 15).
        _room_nums = ", ".join(r.room_number for r in rooms_by_id.values())
        _wa_notify(db, "wa_room_ready_enabled", booking.guest, "room_ready",
                   {"guest_name": booking.guest.name, "room_label": _room_nums},
                   booking_id=booking.booking_id, client_ref=f"room_ready:{booking.booking_id}")

        db.refresh(booking)
        resp = _checkin_response(db, booking, folio, already=False)
        resp["vip"] = checkin_gate["vip"]
        resp["blacklist_warning"] = (checkin_gate["reason"] or "Guest is on the watchlist") \
            if checkin_gate["blacklist"] else None
        resp["company_routing"] = company_routing
        return resp
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

        # Corporate bill-to (prompt 18 slice 7): move the company-routed balance onto the
        # company's city ledger FIRST, so the balance the guest must settle below is only ever
        # the guest's own. Idempotent — a repeated checkout will not double-post. No cash is
        # asserted here: the money moves by ledger entry, which is the anti-fraud invariant.
        company_transfer = None
        if folio.company_id:
            try:
                company_transfer = company_service.transfer_folio_to_company(db, folio, user=user)
                if company_transfer:
                    _recompute(db, folio)
            except Exception as e:
                logger.error(f"company transfer failed for folio {folio.id}: {e}", exc_info=True)
                raise HTTPException(status_code=500,
                                    detail="Failed to transfer the company balance — checkout aborted")

        balance = round(float(folio.balance or 0), 2)
        override_used = False
        if balance != 0:
            if not data.override:
                if balance < 0:
                    raise HTTPException(
                        status_code=409,
                        detail=f"Guest has overpaid ₹{-balance:,.2f} — return the excess before checkout")
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
                    room.status_changed_at = datetime.utcnow()  # prompt 11: cleaning-too-long detection
                    on_room_dirtied(db, room, booking)          # prompt 13: dirty + auto cleaning task
                if room:
                    rooms.append(room)

        cards = db.query(CardIssuance).filter(
            CardIssuance.booking_id == booking.booking_id,
            CardIssuance.status == "active",
        ).all()
        for c in cards:
            c.status = "checked_out"

        # Loyalty accrual (prompt 14): award points for the completed stay. Idempotent
        # (one accrual per booking); joins this transaction. Never blocks checkout.
        try:
            points_awarded = accrue_loyalty_on_checkout(db, booking, user)
        except Exception as e:
            logger.error(f"loyalty accrual failed for booking {booking.booking_id}: {e}")
            points_awarded = 0

        db.commit()

        write_audit(db, user, "reception.checkout", "booking", booking.booking_id,
                    before=before,
                    after={"override": override_used, "override_reason": data.override_reason,
                           "cards_invalidated": len(cards),
                           "rooms": [r.room_number for r in rooms],
                           "loyalty_awarded": points_awarded,
                           "company_transfer": company_transfer,
                           "client_ref": data.client_ref},
                    client="desktop", commit=True)

        # Final GST invoice at checkout (ALT-3): idempotent, best-effort — a stray failure
        # here must never block a checkout (the desk can still invoice from the folio).
        invoice_no = None
        if folio is not None:
            try:
                from routers.folio import ensure_invoice
                inv, _created = ensure_invoice(db, folio, user)
                invoice_no = inv.invoice_no if inv else None
            except Exception as e:
                logger.error(f"auto-invoice at checkout failed for booking {booking.booking_id}: {e}")

        resp = _checkout_response(db, booking, folio, override=override_used, already=False)
        resp["loyalty_awarded"] = points_awarded
        resp["company_transfer"] = company_transfer
        resp["invoice_no"] = invoice_no
        return resp
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


# =====================================================================
# ROOM SHIFT (prompt 09)
# =====================================================================

def _shift_waive_requires_admin() -> bool:
    return os.getenv("SHIFT_WAIVE_REQUIRES_ADMIN", "true").strip().lower() not in ("false", "0", "no")


def _incl_rate(rt: RoomType) -> float:
    """Rack rate per night, GST-inclusive (FolioCharge amounts are GST-inclusive)."""
    return round(float(rt.price_per_night) * (1 + float(rt.gst_percent or 0) / 100), 2)


@router.post("/shift")
def shift_room(data: RoomShiftRequest, db: Session = Depends(get_db),
               user=Depends(require_reception_or_admin)):
    """Move an in-house guest to a different room mid-stay. The folio is keyed on the
    booking, so it follows automatically; only the rate DIFFERENCE is posted (one signed
    GST-inclusive 'misc' line). Suggested difference = remaining CALENDAR nights x rack-rate
    difference (deliberately simpler than the 24h card window, which stays anchored to the
    actual check-in moment and is unchanged by a shift). Charging less than the computed
    difference needs a reason + admin (SHIFT_WAIVE_REQUIRES_ADMIN, default true, read at
    call time). All active cards on the old room are marked 'superseded'; the response
    carries the encode payload for the new room's card. dry_run=True prices + validates
    without mutating (used by the desktop as the target-room preview). Online-only."""
    try:
        booking = db.query(Booking).options(
            joinedload(Booking.guest), joinedload(Booking.booking_items),
        ).filter(Booking.booking_id == data.booking_id).first()
        if not booking:
            raise HTTPException(status_code=404, detail="Booking not found")
        if booking.status != "checked_in":
            raise HTTPException(status_code=409,
                                detail=f"Cannot shift a '{booking.status}' booking — guest must be in-house")

        # Which room is the guest leaving? (group bookings hold several rooms)
        item = next((i for i in booking.booking_items if i.room_id == data.from_room_id), None)
        if not item:
            raise HTTPException(status_code=409,
                                detail="Guest is not in that room — pick the room being vacated")
        if data.to_room_id == data.from_room_id:
            raise HTTPException(status_code=400, detail="Guest is already in that room")
        if any(i.room_id == data.to_room_id for i in booking.booking_items):
            raise HTTPException(status_code=400,
                                detail="That room is already part of this booking")

        old_room = db.query(Room).filter(Room.room_id == data.from_room_id).with_for_update().first()
        old_type = db.query(RoomType).filter(RoomType.room_type_id == item.room_type_id).first()
        if not old_room or not old_type:
            raise HTTPException(status_code=404, detail="Current room not found")

        # ---- validate the target room (mirrors the check-in assignment rules) ----
        new_room = db.query(Room).filter(Room.room_id == data.to_room_id).with_for_update().first()
        if not new_room:
            raise HTTPException(status_code=404, detail=f"Room id {data.to_room_id} not found")
        if not new_room.is_active:
            raise HTTPException(status_code=409,
                                detail=f"Room {new_room.room_number} is out of service")
        if new_room.status != "vacant":
            raise HTTPException(status_code=409,
                                detail=f"Room {new_room.room_number} is not vacant (status: {new_room.status})")
        held = db.query(BookingItem).join(Booking).filter(
            BookingItem.room_id == new_room.room_id,
            Booking.status == "checked_in",
        ).first()
        if held:
            raise HTTPException(status_code=409,
                                detail=f"Room {new_room.room_number} is already held by booking {held.booking_id}")
        _room_code(new_room)  # fail early on a non-numeric room number
        new_type = db.query(RoomType).filter(RoomType.room_type_id == new_room.room_type_id).first()
        if not new_type:
            raise HTTPException(status_code=404, detail="Target room's type not found")

        today = date.today()
        cross_type = new_room.room_type_id != item.room_type_id

        # Cross-type shifts consume the new type's capacity for the remaining dates —
        # don't strand a future confirmed reservation of that type.
        if cross_type and today < booking.check_out:
            if _blocked(db, new_type.room_type_id, today, booking.check_out):
                raise HTTPException(status_code=409,
                                    detail=f"{new_type.name} is blocked for the remaining dates")
            booked = int(_booked_qty(db, new_type.room_type_id, today, booking.check_out, lock=True))
            inactive = int(_inactive_count(db, new_type.room_type_id))
            if new_type.total_rooms - booked - inactive <= 0:
                raise HTTPException(status_code=409,
                                    detail=f"No {new_type.name} capacity left for the remaining dates "
                                           f"— a future reservation needs it")

        # ---- rate difference (remaining calendar nights x GST-inclusive rack rates) ----
        remaining_nights = max(0, (booking.check_out - today).days)
        old_rate, new_rate = _incl_rate(old_type), _incl_rate(new_type)
        suggested = round((new_rate - old_rate) * remaining_nights, 2)
        applied = suggested if data.applied_adjustment is None else round(float(data.applied_adjustment), 2)

        lo, hi = min(0.0, suggested), max(0.0, suggested)
        if not (lo <= applied <= hi):
            raise HTTPException(status_code=400,
                                detail=f"Adjustment must be between ₹{lo:,.2f} and ₹{hi:,.2f} "
                                       f"(the computed difference) — extra charges belong on the folio")
        if applied != suggested and not data.reason:
            raise HTTPException(status_code=400,
                                detail="Changing the computed rate difference needs a reason")
        if applied < suggested and _shift_waive_requires_admin() and user.get("role") != "admin":
            raise HTTPException(status_code=403,
                                detail="Charging less than the computed difference requires an admin login")

        folio = db.query(Folio).filter(Folio.booking_id == booking.booking_id).first()
        if not folio:
            raise HTTPException(status_code=409, detail="No folio for this booking — open it first")
        if applied != 0 and (folio.status != "open" or _get_invoice(db, folio.id)):
            raise HTTPException(status_code=409,
                                detail="Folio already invoiced — the rate difference cannot be posted; "
                                       "shift with a zero adjustment or void the invoice first")

        valid_from, valid_to = _card_window(booking)
        response = {
            "booking_id": booking.booking_id,
            "dry_run": data.dry_run,
            "from_room": {"room_id": old_room.room_id, "room_number": old_room.room_number,
                          "room_type_name": old_type.name},
            "to_room": {"room_id": new_room.room_id, "room_number": new_room.room_number,
                        "room_type_name": new_type.name},
            "remaining_nights": remaining_nights,
            "old_rate_per_night": old_rate,
            "new_rate_per_night": new_rate,
            "suggested_adjustment": suggested,
            "applied_adjustment": None if data.dry_run else applied,
            "requires_reason": applied != suggested,
            "requires_admin": _shift_waive_requires_admin(),
            "folio_id": folio.id,
            "folio_balance": float(folio.balance or 0),
            "superseded_card_ids": [],
            "card": _encode_payload(db, item, new_room, valid_from, valid_to),
        }

        if data.dry_run:
            db.rollback()  # release the row locks; nothing was mutated
            return response

        # ---- mutate (single transaction) ----
        before = {"room_id": old_room.room_id, "room_number": old_room.room_number,
                  "room_type_id": item.room_type_id, "room_type": old_type.name,
                  "folio_balance": float(folio.balance or 0)}

        item.room_id = new_room.room_id
        if cross_type:
            item.room_type_id = new_room.room_type_id
        if old_room.status == "occupied":
            old_room.status = "cleaning"
            old_room.status_changed_at = datetime.utcnow()  # prompt 11: cleaning-too-long detection
            on_room_dirtied(db, old_room, booking)          # prompt 13: dirty + auto cleaning task
        new_room.status = "occupied"
        new_room.status_changed_at = datetime.utcnow()
        set_hk_status(db, new_room.room_id, "occupied", user=user)  # prompt 13: reflect occupancy

        old_cards = db.query(CardIssuance).filter(
            CardIssuance.booking_id == booking.booking_id,
            CardIssuance.room_id == old_room.room_id,
            CardIssuance.status == "active",
        ).all()
        for c in old_cards:
            c.status = "superseded"
        superseded_ids = [c.id for c in old_cards]

        if applied != 0:
            desc = (f"Room shift {old_room.room_number}→{new_room.room_number} "
                    f"rate difference ({remaining_nights} nights)")
            if data.reason:
                desc += f" — {data.reason}"
            db.add(FolioCharge(
                folio_id=folio.id,
                type="misc",
                description=desc,
                qty=1,
                unit_price=applied,
                amount=applied,
                gst_percent=float(new_type.gst_percent) if applied > 0 else float(old_type.gst_percent),
                posted_by=_resolve_user_id(db, user),
            ))
            _recompute(db, folio)
        db.commit()

        write_audit(db, user, "reception.shift", "booking", booking.booking_id,
                    before=before,
                    after={"room_id": new_room.room_id, "room_number": new_room.room_number,
                           "room_type_id": new_room.room_type_id, "room_type": new_type.name,
                           "remaining_nights": remaining_nights,
                           "suggested_adjustment": suggested, "applied_adjustment": applied,
                           "reason": data.reason, "superseded_card_ids": superseded_ids,
                           "folio_balance": float(folio.balance or 0),
                           "client_ref": data.client_ref},
                    client="desktop", commit=True)

        response["superseded_card_ids"] = superseded_ids
        response["folio_balance"] = float(folio.balance or 0)
        # active_cards on the new room may have changed after the supersede/commit
        response["card"] = _encode_payload(db, item, new_room, valid_from, valid_to)
        return response
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        logger.error(f"shift_room failed: {e}", exc_info=True)
        db.rollback()
        raise HTTPException(status_code=500, detail="Room shift failed")


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

    # Corporate bill-to (prompt 18 slice 7) — cached per board build, not per row.
    _company_names = {c.id: c.name for c in db.query(Company).all()}

    def _bill_to(b):
        return {"bill_to": b.bill_to or "guest",
                "company_id": b.company_id,
                "company_name": _company_names.get(b.company_id)}

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
            **_bill_to(b),
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
            **_bill_to(b),
            "rooms": rooms,
        })

    vacant_rooms = [{
        "room_id": r.room_id,
        "room_number": r.room_number,
        "room_type_id": r.room_type_id,
        "room_type_name": rt.name if rt else None,          # shift target picker (prompt 09)
        "price_per_night": float(rt.price_per_night) if rt else None,
        "building": r.building,
        "floor": r.floor,
        "max_cards": r.max_cards,
    } for r, rt in db.query(Room, RoomType).outerjoin(
        RoomType, Room.room_type_id == RoomType.room_type_id,
    ).filter(
        Room.is_active == True,  # noqa: E712
        Room.status == "vacant",
    ).order_by(Room.room_number).all()]

    # prompt 13: every active room + its housekeeping status, for the desktop's
    # READ-ONLY housekeeping panel (cleaning/dirty rooms are in neither vacant_rooms
    # nor inhouse, so they need their own list). Reception cannot edit these.
    rooms_hk = []
    for r, hk in db.query(Room, HousekeepingStatus).outerjoin(
        HousekeepingStatus, Room.room_id == HousekeepingStatus.room_id,
    ).filter(Room.is_active == True).order_by(Room.room_number).all():  # noqa: E712
        rooms_hk.append({
            "room_id": r.room_id,
            "room_number": r.room_number,
            "room_status": r.status,
            "housekeeping_status": hk.status if hk else None,
            "updated_at": hk.updated_at.isoformat() if hk and hk.updated_at else None,
        })

    return {"date": str(today), "arrivals": arrivals, "inhouse": inhouse,
            "vacant_rooms": vacant_rooms, "rooms": rooms_hk}


# ==========================================================================
# Per-occupant guest KYC (FE-3): all guests' ID + an ENCRYPTED ID scan.
# Scans are encrypted at rest under booking_<id>/ and only ever streamed back
# (decrypted) to an authed reception/admin caller — never a public URL.
# ==========================================================================

@router.get("/bookings/{booking_id}/guests")
def list_booking_guests(booking_id: int, db: Session = Depends(get_db),
                        user=Depends(require_reception_or_admin)):
    """The occupant roster for a booking (masked IDs + whether an ID scan is on file)."""
    rows = (db.query(BookingGuest).filter(BookingGuest.booking_id == booking_id)
            .order_by(BookingGuest.is_primary.desc(), BookingGuest.id).all())
    return {"booking_id": booking_id, "guests": [{
        "id": g.id, "name": g.name, "id_type": g.id_type,
        "id_number_masked": g.id_number_masked, "is_primary": g.is_primary,
        "has_scan": bool(g.id_scan_ref),
    } for g in rows]}


@router.post("/bookings/{booking_id}/guests/scan")
async def upload_guest_scan(booking_id: int, file: UploadFile = File(...),
                            db: Session = Depends(get_db),
                            user=Depends(require_reception_or_admin)):
    """Upload a guest ID scan for a booking. Stored ENCRYPTED under booking_<id>/; returns the
    ref (kept on the check-in payload). 503 when encryption isn't configured — no plaintext write."""
    if not db.query(Booking).filter(Booking.booking_id == booking_id).first():
        raise HTTPException(status_code=404, detail="Booking not found")
    if not secure_id_store.allowed_mime(file.content_type):
        raise HTTPException(status_code=400, detail="Unsupported file type (use JPG/PNG/WEBP/PDF)")
    data = await file.read()
    try:
        ref = secure_id_store.save_scan(booking_id, data, file.content_type)
    except RuntimeError:
        raise HTTPException(status_code=503,
                            detail="ID-scan encryption is not configured on the server")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    write_audit(db, user, "reception.guest_scan_upload", "booking", booking_id,
                after={"mime": file.content_type}, client="desktop", commit=True)
    return {"ref": ref, "mime": file.content_type}


@router.get("/bookings/{booking_id}/guests/{guest_id}/scan")
def get_guest_scan(booking_id: int, guest_id: int, db: Session = Depends(get_db),
                   user=Depends(require_reception_or_admin)):
    """Stream the DECRYPTED ID scan for one guest (authed reception/admin only)."""
    g = (db.query(BookingGuest)
         .filter(BookingGuest.id == guest_id, BookingGuest.booking_id == booking_id).first())
    if not g or not g.id_scan_ref:
        raise HTTPException(status_code=404, detail="No ID scan on file")
    try:
        data = secure_id_store.read_scan(g.id_scan_ref)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="ID scan not found")
    except RuntimeError:
        raise HTTPException(status_code=503, detail="ID scan cannot be decrypted (key not configured)")
    return StreamingResponse(io.BytesIO(data), media_type=g.id_scan_mime or "application/octet-stream")


# ---- Registration slip (printed at check-in, guest signs it) ----
def _reg_rules(db) -> str:
    """Admin-editable rules/terms text for the reg-slip (F-A). Defensive: never fails the slip."""
    try:
        from utils.settings import get_registration_rules
        return get_registration_rules(db)
    except Exception:
        return ""


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

    # All occupants (FE-3): the reg-slip lists every guest (name + masked ID + "ID on file"),
    # falling back to the lead Guest when no roster exists.
    guest_rows = (db.query(BookingGuest).filter(BookingGuest.booking_id == booking_id)
                  .order_by(BookingGuest.is_primary.desc(), BookingGuest.id).all())
    guests = [{"name": g.name, "id_type": g.id_type, "id_number_masked": g.id_number_masked,
               "has_scan": bool(g.id_scan_ref), "is_primary": g.is_primary} for g in guest_rows]
    if not guests and booking.guest:
        guests = [{"name": booking.guest.name, "id_type": booking.guest.id_type,
                   "id_number_masked": booking.guest.id_number_masked, "has_scan": False,
                   "is_primary": True}]

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
        # Admin-editable rules/terms printed on the slip (FE-2 / F-A settings backbone).
        "registration_rules": _reg_rules(db),
        # All occupants with masked IDs (FE-3).
        "guests": guests,
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
