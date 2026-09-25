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

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File, Form
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from database import SessionLocal
from models import Room, RoomType, Booking, Guest, RoomTypeAvailability, BookingItem, \
    Folio, FolioCharge, CardIssuance, TravelAgent, Company, BookingGuest, StayEvent
from utils import ordering
from utils import arrival_rules
from utils import secure_id_store
from utils import availability
from utils import settings as app_settings
from utils.settings import validate_category
from schemas import DeskBookingCreate, CheckinRequest, CheckoutRequest, ComplimentaryRequest, \
    ExtendStayRequest, FolioOpenRequest, ReverseOverstayRequest, RoomShiftRequest, EarlyCheckoutRequest, \
    CheckinQuoteRequest, ExtendHoursRequest
from utils.auth_utils import require_admin, require_reception_or_admin
from utils.audit import write_audit, _resolve_user_id
from utils.pdf_generator import generate_registration_slip_pdf
from utils.rate_engine import quote_stay
from utils.housekeeping import on_room_dirtied, set_hk_status, cleaning_card_state
from models import HousekeepingStatus
from routers.promotions import get_active_promotions, best_promotion_for_item
from routers.payments import total_paid, total_paid_including_prepaid, prepaid_slice
from routers.folio import (open_folio, _recompute, _get_invoice, _active_charges,
                           _ensure_editable as _ensure_editable_folio, _void_charge_row)
from routers.crm import check_guest_gate, match_guest, accrue_loyalty_on_checkout
from scripts.expire_booking_jobs import expire_pending_bookings
from services import company_service, ota_service, room_posting
from utils.owner_otp import consume_otp

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/reception", tags=["Reception"])

# Statuses that reserve room capacity + the capacity helpers now live in
# utils/availability.py so the desk and the public website share one implementation
# (backlog v2 FE-7). Re-exported here for callers that still import from this module.
RESERVED_STATUSES = availability.RESERVED_STATUSES


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("/health", dependencies=[Depends(require_reception_or_admin)])
def health():
    return {"status": "ok", "module": "reception"}


_booked_qty = availability.booked_qty
_inactive_count = availability.out_of_service_count


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
            # `inactive` kept for the existing desk DTO; it now also covers rooms in
            # maintenance/blocked status, hence the clearer alias alongside it (FE-7).
            "inactive": inactive,
            "out_of_service": inactive,
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

        # Booking sources are an admin-editable list (v3 item 2). Validated here rather than
        # by a Pydantic regex so adding an OTA no longer needs a redeploy.
        booking_source = validate_category(db, "booking_source", data.booking_source)

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

            # F-08: serialise this room type's capacity decision before reading it. Without this the
            # row lock below has nothing to lock (the competing bookings do not exist yet) and
            # concurrent requests all read the same free count — five simultaneous requests for the
            # last five rooms were all accepted. Released on commit/rollback.
            availability.lock_room_type(db, item.room_type_id)
            booked = _booked_qty(db, item.room_type_id, data.check_in, data.check_out, lock=True)
            inactive = _inactive_count(db, item.room_type_id)
            available = rt.total_rooms - int(booked) - int(inactive)
            if item.quantity > available:
                raise HTTPException(status_code=409,
                                    detail=f"Not enough {rt.name}s available. Available: {max(0, available)}, Requested: {item.quantity}")
            rt_map[item.room_type_id] = rt

        # Occupancy (per booking): total adults must fit the combined max-occupancy (= max adults)
        # of the rooms booked. Children are separate and not counted here.
        capacity = sum(item.quantity * int(rt_map[item.room_type_id].max_occupancy or 0)
                       for item in data.rooms)
        if data.adults > capacity:
            raise HTTPException(
                status_code=409,
                detail=f"{data.adults} adults exceed the selected rooms' capacity "
                       f"({capacity} adult{'s' if capacity != 1 else ''}). Add a room or reduce adults.")
        # v5r: and a floor — a booking for three rooms with "1 adult" tells the desk nothing true, and
        # at check-in it will be asked for one identified guest per room anyway.
        _total_rooms = sum(int(item.quantity or 1) for item in data.rooms) or 1
        if data.adults < _total_rooms:
            raise HTTPException(
                status_code=409,
                detail=f"{_total_rooms} rooms need at least {_total_rooms} adults (one per room) — "
                       f"{data.adults} entered.")

        # Guest — reuse an existing record for this number (repeat-guest recognition, prompt 14) so
        # history + loyalty attach to one customer; else create. v5r: matched on normalised digits, so
        # +91 / 0 / bare spellings of one mobile are one guest.
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

        # Pricing (NO convenience fee). The per-night base comes from the shared rate engine
        # (rack / agent rate / seasonal-weekend plan by channel+date), then GST applies.
        # Promotions are deliberately NOT applied here: they are a WEBSITE-only offer (owner
        # decision). Walk-in / agent / OTA-confirmed desk bookings bill at the rate-engine price;
        # a desk giveaway is a deliberate folio discount, not an automatic promo.
        nights = (data.check_out - data.check_in).days
        total_base = total_gst = total_discount = total_room = 0.0
        items_data = []
        for item in data.rooms:
            rt = rt_map[item.room_type_id]
            base = quote_stay(db, rt, data.check_in, data.check_out,
                              channel=channel, agent_id=data.agent_id,
                              quantity=item.quantity)["base"]
            disc = 0.0
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
            adults=data.adults,
            children=data.children,
            booking_source=booking_source,
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
            # v5m: the moment the arrival rules measure early/late against. OTA stays are
            # contracted 12:00 whatever the channel mail said.
            expected_arrival_at=datetime.combine(
                data.check_in,
                time(hour=12) if ota_service.is_ota_source(booking_source, db)
                else (data.check_in_time or time(hour=12))),
        )
        db.add(booking)
        db.flush()
        # OTA tracking snapshot (prompt 17): stamp OTA booking id + commission + net payout when the
        # source is an OTA channel. Commission % = the desk override else the per-OTA config default.
        # v5 HARD GATE (applies to every OTA channel via is_ota_source — MMT/Goibibo/Booking.com/
        # Agoda/Yatra/other_ota + any admin-added source): an OTA id must be legitimate before the
        # booking is treated as prepaid.
        ota_verified_source = None
        if ota_service.is_ota_source(booking_source, db):
            # 1) No two live bookings may share an OTA id.
            dup = ota_service.duplicate_ota_booking(db, data.ota_booking_id)
            if dup is not None:
                raise HTTPException(
                    status_code=409,
                    detail=f"This OTA booking ID is already used by booking #{dup.booking_id}")
            # 2) Verified = a matching OTA email already imported; otherwise the owner must approve.
            if ota_service.ota_email_exists(db, booking_source, data.ota_booking_id):
                ota_verified_source = "email"
            elif app_settings.get_fraud_config(db).get("ota_unverified_otp_required"):
                consume_otp(db, data.owner_otp_id, data.owner_otp_code, "ota_unverified", user)
                ota_verified_source = "owner_otp"
            else:
                ota_verified_source = None  # gate off: record it, but leave it unverified/uncredited
        ota_service.apply_ota_fields(db, booking, booking_source,
                                     ota_booking_id=data.ota_booking_id,
                                     commission_percent_override=data.ota_commission_percent,
                                     commission_amount=data.ota_commission_amount,
                                     net_payout=data.ota_net_payout,
                                     verified=ota_verified_source is not None,
                                     verified_source=ota_verified_source)

        # Complimentary at booking (desk only) — reuses the v4b6 machinery. Setting all/room needs
        # a reason + the owner OTP; the v4b1 post_room_nights short-circuit then suppresses the room
        # charges so the folio opens empty at check-in. grand_total is left intact (what it was worth).
        if data.comp_mode != "none":
            if not data.comp_reason or len(data.comp_reason.strip()) < 5:
                raise HTTPException(status_code=400, detail="A complimentary reason (5+ characters) is required")
            if app_settings.get_fraud_config(db).get("comp_otp_required"):
                consume_otp(db, data.owner_otp_id, data.owner_otp_code, "booking_complimentary", user)
            booking.comp_mode = data.comp_mode
            booking.comp_reason = data.comp_reason.strip()
            booking.comp_by = _resolve_user_id(db, user)
            booking.comp_at = datetime.now()
            booking.comp_otp_id = data.owner_otp_id

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
        # The system already renders a confirmation PDF for the online flow; the desk sends the
        # same document, falling back to the plain text template when it cannot.
        _conf_pdf = None
        try:
            from routers.payments import booking_pdf_payload
            from utils.pdf_generator import generate_booking_pdf
            _conf_pdf = generate_booking_pdf(booking_pdf_payload(booking),
                                             {"gateway": "desk", "status": booking.status})
        except Exception as e:
            logger.warning(f"booking confirmation PDF failed (sending text instead): {e}")
        from services import notify as notify_service
        notify_service.notify_guest_document(
            db, guest,
            doc_template="booking_confirmation_doc", text_template="booking_confirmation",
            doc_params={"guest_name": guest.name, "booking_ref": str(booking.booking_id)},
            text_params={"guest_name": guest.name, "check_in": str(data.check_in),
                         "check_out": str(data.check_out), "booking_ref": str(booking.booking_id)},
            pdf_path=_conf_pdf,
            filename=f"booking-{booking.booking_id}.pdf",
            caption=f"Booking {booking.booking_id} confirmed \u2014 Hotel Bhimas",
            setting_key="wa_confirmation_enabled",
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
    # OTA stays are contracted NOON-to-NOON: checkout is always the checkout date @ 12:00, never
    # derived from the actual (often late) check-in moment — even under the property's 24h policy.
    # This one anchor feeds card expiry, overstay auto-billing and the reminders, so all of them
    # become fixed 12→12 for an OTA booking together.
    if ota_service.is_ota_source(booking.booking_source):
        return datetime.combine(booking.check_out, time(hour=12))
    # v5m: an hourly extension (checkout_extended_until) is an explicit checkout moment that beats
    # every derived one — card window, overstay sweep and reminders all follow it.
    if getattr(booking, "checkout_extended_until", None):
        return booking.checkout_extended_until
    if _checkout_mode() == "fixed":
        return datetime.combine(booking.check_out, time(hour=_checkout_hour()))
    # v5m: the 24h clock runs from stay_started_at (set at check-in by the arrival rules — the
    # expected time for an early or badly-late guest, the actual moment otherwise), falling back
    # to checked_in_at for stays that started before the rules existed.
    anchor = getattr(booking, "stay_started_at", None) or booking.checked_in_at or ref
    return anchor + timedelta(days=nights)


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
    """Best-effort transactional guest message, gated by an app_settings toggle.
    WhatsApp first, EMAIL as fallback (backlog v2 FE-11 generalised this helper into
    services/notify.py). Never raises — a messaging failure must not break check-in /
    booking / payment."""
    from services import notify as notify_service
    return notify_service.notify_guest(db, guest, template=template, params=params,
                                       setting_key=setting_key, booking_id=booking_id,
                                       client_ref=client_ref)


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
    """Everything the desktop needs to encode one guest card for one room.

    F-12: a card whose window has already closed is refused here rather than handed to the encoder.
    No correct flow ever wants one — the lock only knows the expiry written on the card, so the desk
    would burn a blank, hand it over, and the guest would find it dead at the door. This is the
    backstop for the lapse rule above: if any future path lets an expired stay through, it fails here
    with something a receptionist can act on instead of at the lock.
    """
    if valid_to and valid_from and valid_to <= valid_from:
        raise HTTPException(
            status_code=409,
            detail=(f"Room {room.room_number}: this stay's key window has already closed "
                    f"({valid_to:%d-%m-%Y %H:%M}). Extend the stay before cutting a card."))
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


# ---------------------------------------------------------------------------------------------
# v5m — arrival rules at check-in
# ---------------------------------------------------------------------------------------------

def expected_arrival(booking: Booking, db=None) -> datetime:
    """The moment the guest was expected: booked date + expected time; OTA stays are contracted
    12:00. Stored on the booking at creation (expected_arrival_at); derived for older rows."""
    stored = getattr(booking, "expected_arrival_at", None)
    if stored:
        return stored
    if ota_service.is_ota_source(booking.booking_source, db):
        return datetime.combine(booking.check_in, time(hour=12))
    return datetime.combine(booking.check_in, booking.check_in_time or time(hour=12))


def _fee_selection(db, booking: Booking, room_ids=None, room_to_item=None) -> list:
    """The ROOMS a fee applies to, as [(booking_item, room_id or None), ...] — one entry per room.

    v5n: a fee belongs to a room, not to a booking ("only one of the three rooms arrived early, so
    charge that room"). The selector is a physical room id rather than a booking-item id because one
    item can be several rooms (quantity 3), and an item id cannot say "two of these three".

    Resolution, in order: the item the room is already assigned to (exact — this is the state after
    check-in splits a quantity>1 item into one row per room); else the assignment map the caller was
    handed on the request (exact, and the only thing available before the split); else the room's TYPE
    (rooms of one type share a rate, so this prices correctly even on a mixed-type stay).
    `room_ids` empty / None = every room of the stay, expanded by quantity, so a fee is never
    silently zero.
    """
    items = list(booking.booking_items)
    if not room_ids:
        out = []
        for it in items:
            for _ in range(max(1, int(it.quantity or 1))):
                out.append((it, it.room_id))
        return out

    by_id = {it.booking_item_id: it for it in items}
    wanted, out, used = [int(r) for r in room_ids], [], {}
    for rid in wanted:
        item = next((it for it in items if it.room_id == rid), None)
        if item is None and room_to_item:
            item = by_id.get(room_to_item.get(rid))
        if item is None:
            room = db.query(Room).filter(Room.room_id == rid).first()
            if room is not None:
                for it in items:
                    if it.room_type_id in (room.room_type_id, room.alt_room_type_id) \
                            and used.get(it.booking_item_id, 0) < max(1, int(it.quantity or 1)):
                        item = it
                        break
        if item is None:
            continue
        used[item.booking_item_id] = used.get(item.booking_item_id, 0) + 1
        out.append((item, rid))
    if not out:   # nothing resolved (a stale id): fall back to the whole stay rather than charging 0
        return _fee_selection(db, booking)
    return out


def _night_rate(db, booking: Booking, selection=None) -> float:
    """One night of the selected ROOMS through the rate engine, GST-inclusive — the base a
    percent-mode fee is taken from, so a percent fee scales with how many rooms are charged.
    `selection` is what _fee_selection returned; None = the whole stay. Falls back to the booked
    per-night average when the engine has no rate (e.g. a room type with no rate plan)."""
    if selection is None:
        selection = _fee_selection(db, booking)
    counts = {}
    for item, _rid in selection:
        counts[item.booking_item_id] = counts.get(item.booking_item_id, 0) + 1
    items = {i.booking_item_id: i for i in booking.booking_items}
    try:
        quotes = room_posting._quote_nights(db, booking, list(items.values()), [booking.check_in])
        rate = 0.0
        for iid, n in counts.items():
            item = items.get(iid)
            qty = max(1, int(item.quantity or 1)) if item else 1
            per_item = sum(x["rate"] for x in quotes.get(iid, []))
            rate += per_item / qty * n       # the item's nightly rate covers `qty` rooms
        rate = round(rate, 2)
        if rate > 0:
            return rate
    except Exception as e:  # pragma: no cover - defensive
        logger.warning(f"night rate quote failed for booking {booking.booking_id}: {e}")
    nights = max(1, (booking.check_out - booking.check_in).days)
    total_rooms = sum(max(1, int(i.quantity or 1)) for i in booking.booking_items) or 1
    return round(float(booking.total_amount or 0) / nights * (len(selection) / total_rooms), 2)


def _item_gst_percent(db, item) -> float | None:
    """The GST slab of one booking item's room type. Per item, not per booking: a stay mixing a
    Suite with a Double must tax each room's fee at its own room type's rate."""
    rt = db.query(RoomType).filter(RoomType.room_type_id == item.room_type_id).first() if item else None
    return float(rt.gst_percent) if rt and rt.gst_percent is not None else None


def _room_gst_percent(db, booking: Booking) -> float | None:
    return _item_gst_percent(db, next(iter(booking.booking_items), None))


def _fmt_dev(minutes: int) -> str:
    h, m = divmod(abs(int(minutes)), 60)
    return f"{h} h {m:02d} m" if h else f"{m} min"


def arrival_evaluation(db, booking: Booking, now: datetime | None = None,
                       applied: float | None = None, rules: dict | None = None,
                       room_ids=None, room_to_item=None) -> dict:
    """Evaluate the arrival rules for a check-in happening `now` (pure; no writes).

    Returns everything the wizard shows and check_in acts on: kind (early / late / on_time),
    deviation, quoted fee + basis, whether a whole extra night is needed (day-early arrivals are
    always a full night — the room is slept in tonight), the stay-clock anchor, the checkout
    moment that follows, and whether `applied` needs an approval."""
    now = now or datetime.now()
    rules = rules or arrival_rules.load_rules(db)
    is_ota = ota_service.is_ota_source(booking.booking_source, db)
    expected = expected_arrival(booking, db)
    comp = room_posting._is_comped(booking)
    src = booking.booking_source or "direct"
    today = now.date()
    day_early = today < booking.check_in
    days_early = (booking.check_in - today).days if day_early else 0
    nights = max(1, (booking.check_out - booking.check_in).days)

    out = {"kind": "on_time", "expected_at": expected, "now": now, "is_ota": is_ota,
           "deviation_minutes": 0, "quoted": 0.0, "applied": 0.0, "basis": "on_time",
           "full_night": False, "days_early": days_early, "rule": None, "approval": "never",
           "needs_approval": False, "stay_start": now, "night_rate": 0.0, "comp": comp,
           "text": "", "lapsed": None, "extra_nights": 0}

    # Lapsed window: the stay is over, so there is nothing left to check into.
    #
    # F-02/F-12: both branches now compare the same MOMENT. The non-OTA branch used to compare DATES
    # (`today > check_out`), so on the check-out date itself a stay never counted as lapsed however
    # long ago its checkout moment had passed — for a 14:00 checkout that is ten hours a day in which
    # the desk could check a guest into a stay that had already ended. Reproduced: a 24-09 08:00 ->
    # 25-09 booking accepted at 11:47 on the 25th, in-house with `expected_check_out` 3 h 47 m in the
    # PAST, and the card cut for it ran valid_from 11:47 -> valid_to 09:00 — dead before it was
    # written. The booked checkout moment is the booked arrival plus the nights (or the fixed hour
    # when that mode is on), which is what `checkout_at` below computes from the ACTUAL arrival.
    if is_ota:
        booked_checkout = datetime.combine(booking.check_out, time(hour=12))
    elif _checkout_mode() == "fixed":
        booked_checkout = datetime.combine(booking.check_out, time(hour=_checkout_hour()))
    else:
        booked_checkout = expected + timedelta(days=nights)
    if now >= booked_checkout:
        out["lapsed"] = (f"OTA stay window closed at 12:00 on {booking.check_out:%d-%m-%Y}"
                         if is_ota else
                         f"Stay window lapsed at {booked_checkout:%H:%M} on {booking.check_out:%d-%m-%Y}")

    # v5n: which rooms this arrival is for. The desk ticks the rooms whose guests actually turned
    # up early; everything below (percent base, fixed multiplier, extra nights, fee lines) follows
    # that selection rather than the whole booking.
    selection = _fee_selection(db, booking, room_ids, room_to_item)
    rooms_charged = len(selection)
    total_rooms = sum(max(1, int(i.quantity or 1)) for i in booking.booking_items) or 1
    out_rooms = {"rooms_charged": rooms_charged, "total_rooms": total_rooms,
                 "charged_room_ids": [rid for _i, rid in selection if rid]}

    if now < expected:
        rate = _night_rate(db, booking, selection)
        ev = arrival_rules.evaluate_early(rules, src, expected, now, rate, comp, rooms=rooms_charged)
        out.update(kind="early", deviation_minutes=ev["deviation_minutes"], rule=ev["rule"],
                   quoted=ev["charge"], basis=ev["basis"], full_night=ev["full_night"],
                   approval=ev["approval"], night_rate=rate)
        if day_early and not ev["exempt"]:
            # The room is occupied tonight, so a day-early arrival is always a night (per day),
            # whatever the fee band says.
            out.update(full_night=True, quoted=round(rate * days_early, 2), basis="full_night")
        if ev["exempt"]:
            out.update(full_night=False, quoted=0.0)
        # Nights the booking grows by: every day early is one, and a same-day arrival beyond
        # the full-night bound is one too. The paid stay still runs from the expected time; with
        # extra nights the clock starts that many days earlier so the checkout moment is unchanged.
        out["extra_nights"] = max(days_early, 1 if out["full_night"] else 0)
        out["stay_start"] = expected - timedelta(days=out["extra_nights"])
    elif now > expected:
        ev = arrival_rules.evaluate_late_arrival(rules, src, expected, now)
        out.update(kind="late", deviation_minutes=ev["deviation_minutes"], rule=ev["rule"],
                   basis="late", stay_start=ev["stay_start"])
        if ev["deviation_minutes"] == 0:
            out.update(kind="on_time", basis="on_time", stay_start=now)

    # Applied amount + approval (only a charged early arrival can be edited).
    quoted = float(out["quoted"])
    if out["kind"] == "early" and out["basis"] not in ("exempt_comp", "free", "on_time"):
        applied_amt = quoted if applied is None else round(float(applied), 2)
        out["applied"] = applied_amt
        out["needs_approval"] = arrival_rules.needs_approval(out["rule"], quoted, applied)
    else:
        out["applied"] = 0.0

    # Checkout moment that follows from this arrival.
    if is_ota:
        out["checkout_at"] = datetime.combine(booking.check_out, time(hour=12))
    elif _checkout_mode() == "fixed":
        out["checkout_at"] = datetime.combine(booking.check_out, time(hour=_checkout_hour()))
    else:
        out["checkout_at"] = out["stay_start"] + timedelta(days=nights + out["extra_nights"])

    out.update(out_rooms)
    dev = _fmt_dev(out["deviation_minutes"])
    co = out["checkout_at"]
    if out["kind"] == "early":
        if out["basis"] == "exempt_comp":
            out["text"] = f"Early by {dev} — complimentary stay, no fee"
        elif out["basis"] in ("free", "on_time"):
            out["text"] = f"Early by {dev} — within the free allowance"
        elif out["full_night"]:
            what = "an extra night" if days_early <= 1 else f"{days_early} extra nights"
            out["text"] = f"Early by {dev} — charged as {what} (₹{quoted:,.0f})"
        else:
            out["text"] = f"Early by {dev} — early check-in fee ₹{quoted:,.0f}"
        out["text"] += f" · stay runs from {expected:%H:%M} → checkout {co:%d %b %H:%M}"
    elif out["kind"] == "late":
        if out["stay_start"] == expected:
            out["text"] = (f"Late by {dev} — stay counted from the booked {expected:%d %b %H:%M}"
                           f" → checkout {co:%d %b %H:%M}")
        else:
            out["text"] = f"Late by {dev} — 24 h clock starts now → checkout {co:%d %b %H:%M}"
    else:
        out["text"] = f"On time → checkout {co:%d %b %H:%M}"
    if is_ota:
        out["text"] = f"OTA stay: room until 12:00 on {booking.check_out:%d %b}" + (
            f" · {out['text'].split(' · ')[0]}" if out["kind"] == "early" else
            (f" · late by {dev}" if out["kind"] == "late" else ""))
    return out


def _arrival_public(ev: dict) -> dict:
    """JSON-safe slice of arrival_evaluation for the desk."""
    return {
        "kind": ev["kind"], "text": ev["text"], "deviation_minutes": ev["deviation_minutes"],
        "expected_at": ev["expected_at"].isoformat() if ev.get("expected_at") else None,
        "quoted": ev["quoted"], "applied": ev["applied"], "basis": ev["basis"],
        "full_night": ev["full_night"], "days_early": ev["days_early"], "extra_nights": ev["extra_nights"],
        "rooms_charged": ev.get("rooms_charged", 1), "total_rooms": ev.get("total_rooms", 1),
        "charged_room_ids": ev.get("charged_room_ids", []),
        "approval": ev["approval"], "needs_approval": ev["needs_approval"],
        "editable": ev["kind"] == "early" and ev["basis"] not in ("exempt_comp", "free", "on_time"),
        "stay_start": ev["stay_start"].isoformat() if ev.get("stay_start") else None,
        "checkout_at": ev["checkout_at"].isoformat() if ev.get("checkout_at") else None,
        "is_ota": ev["is_ota"], "lapsed": ev["lapsed"], "night_rate": ev["night_rate"],
        "comp": ev["comp"],
        "rule": {k: v for k, v in (ev["rule"] or {}).items() if k != "sources"} if ev.get("rule") else None,
    }


def _assert_early_capacity(db, booking: Booking, date_from: date, date_to: date):
    """A day-early arrival sleeps in a room tonight: the room TYPE must have capacity for the
    extra night(s), row-locked like /extend so a concurrent check-in cannot oversell."""
    need = {}
    for item in booking.booking_items:
        need[item.room_type_id] = need.get(item.room_type_id, 0) + int(item.quantity or 1)
    for rt_id, qty in need.items():
        rt = db.query(RoomType).filter(RoomType.room_type_id == rt_id).first()
        name = rt.name if rt else f"type {rt_id}"
        booked = int(_booked_qty(db, rt_id, date_from, date_to, lock=True))
        inactive = int(_inactive_count(db, rt_id))
        free = (rt.total_rooms if rt else 0) - booked - inactive
        if free < qty:
            raise HTTPException(
                status_code=409,
                detail=f"No {name} room is available for an early arrival tonight "
                       f"({date_from:%d-%m-%Y}) — a reservation needs it")


@router.post("/checkin/quote")
def checkin_quote(data: CheckinQuoteRequest, db: Session = Depends(get_db),
                  user=Depends(require_reception_or_admin)):
    """v5m dry-run: what the arrival rules will do if this booking is checked in right now."""
    booking = db.query(Booking).options(joinedload(Booking.booking_items)).filter(
        Booking.booking_id == data.booking_id).first()
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")
    ev = arrival_evaluation(db, booking, applied=data.arrival_fee_applied,
                            room_ids=data.early_room_ids)
    out = _arrival_public(ev)
    out["booking_id"] = booking.booking_id
    out["is_admin"] = user.get("role") == "admin"
    return out


def _checkin_response(db: Session, booking: Booking, folio: Folio | None, already: bool):
    valid_from, valid_to = _card_window(booking)
    cards = []
    for item in booking.booking_items:
        if item.room_id:
            room = db.query(Room).filter(Room.room_id == item.room_id).first()
            # Key-lock rooms have a physical metal key — nothing to encode, so they produce no
            # card task and the desk wizard skips straight past the "cards" step for an all-key stay.
            if room and room.lock_type != "key":
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

        # ---- v5m: arrival rules — early / late / lapsed, evaluated once, right now ----
        # A late guest may still check in on the check-out date (no re-dating at the desk);
        # an early guest pays by rule; a day-early guest is an extra night IF a room is free.
        now = datetime.now()
        today = now.date()
        # The assignment payload is the only map from room -> booking item before the quantity>1 split
        # further down, so hand it to the evaluation: that is what lets "room 83 only" price room 83's
        # own rate rather than an average of the stay.
        _room_to_item = {rid: a.booking_item_id for a in data.assignments for rid in a.room_ids}
        arrival = arrival_evaluation(db, booking, now, applied=data.arrival_fee_applied,
                                     room_ids=data.early_room_ids, room_to_item=_room_to_item)
        if arrival["lapsed"]:
            raise HTTPException(status_code=400, detail=arrival["lapsed"])
        if arrival["days_early"] > 1:
            raise HTTPException(
                status_code=400,
                detail=f"Booking starts on {booking.check_in:%d-%m-%Y} — more than a day early. "
                       f"Ask an admin to re-date the booking.")
        if arrival["days_early"] == 1:
            _assert_early_capacity(db, booking, today, booking.check_in)
        fee_quoted = float(arrival["quoted"])
        fee_applied = float(arrival["applied"])
        fee_changed = arrival["kind"] == "early" and data.arrival_fee_applied is not None \
            and round(fee_applied, 2) != round(fee_quoted, 2)
        if fee_changed and not (data.arrival_fee_reason or "").strip():
            raise HTTPException(status_code=400,
                                detail="Charging something other than the rule's early check-in fee needs a reason")
        arrival_approval = "none"
        if arrival["needs_approval"]:
            if user.get("role") == "admin":
                arrival_approval = "admin"
            else:
                consume_otp(db, data.arrival_otp_id, data.arrival_otp_code, "arrival_fee", user)
                arrival_approval = "owner_otp"

        # Anti-fraud gate: no recorded payment => no check-in (and no card).
        # v4b1: "paid" includes a prepayment collected by the channel that sold the stay.
        # An OTA booking has no Payment row (MakeMyTrip took the money), so this gate used to
        # refuse every OTA guest at check-in.
        # v5i: a complimentary stay ('all' = everything free, 'room' = room free, extras billed
        # later) has nothing to collect at check-in, so — like a prepaid channel booking — it
        # is not asked for money. The owner's OTP already approved the comp itself.
        is_comp = (getattr(booking, "comp_mode", "none") or "none") in ("all", "room")
        if not is_comp and total_paid_including_prepaid(db, booking.booking_id) <= 0:
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
        alt_sales = []          # v4b7: (item, room) pairs sold as the room's ALTERNATE type
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
                    # v4b7: a room may be sold as its configured ALTERNATE type — but only
                    # when the booked type has genuinely run out. See _assert_booked_type_sold_out.
                    if room.alt_room_type_id == item.room_type_id:
                        _assert_booked_type_sold_out(db, item, room)
                        alt_sales.append((item, room))
                    else:
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

        # ---- v4b7: ONE owner code for the whole check-in, not one per room ----
        # Every alt-type sale in this check-in is approved together. Three codes for a
        # three-room family is unusable, and staff would route around it — which is worse
        # than one approval that names all three.
        # Consumed AFTER all validation and BEFORE any mutation: consume_otp only flushes, so
        # a bad code costs nothing and a good one is spent only if the check-in commits.
        if alt_sales and app_settings.get_fraud_config(db).get("alt_room_type_otp_required"):
            consume_otp(db, data.owner_otp_id, data.owner_otp_code, "room_assignment", user)

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

        # v4b7: record which stays were sold as a room's ALTERNATE type. `room_type_id` is
        # left alone deliberately — it is what was booked and priced, and availability groups
        # on it. This column is what makes the practice reportable afterwards.
        #
        # v5n: match on the ROOM, after the split above. `alt_sales` captured the PARENT item, but a
        # quantity>1 item has by now become one row per room — so tagging the captured object marked
        # whichever room ended up on the parent row (usually a correctly-typed one) and left the
        # actual alternate room untagged. The owner's alternate-sale report named the wrong room.
        db.flush()
        _rows_by_room = {i.room_id: i for i in booking.booking_items if i.room_id}
        for _item, _room in alt_sales:
            target = _rows_by_room.get(_room.room_id, _item)
            target.sold_as_room_type_id = _room.room_type_id

        # ---- KYC (masked; the raw ID number is never stored) ----
        # ID types are an admin-editable list (v3 item 2). Every occupant's type is checked,
        # not just the lead's — a companion row is a government ID record too.
        lead_id_type = validate_category(db, "id_type", data.id_type)
        booking.guest.id_type = lead_id_type
        booking.guest.id_number_masked = "****" + data.id_number[-4:]

        # Per-occupant KYC roster (FE-3): rebuild booking_guests (lead + companions). The desk sends
        # the full roster in `additional_guests` (each row incl. the primary); an empty list keeps the
        # old single-guest behaviour. ID numbers masked here; scans were uploaded encrypted beforehand.
        db.query(BookingGuest).filter(BookingGuest.booking_id == booking.booking_id).delete()
        roster = list(data.additional_guests or [])

        # ---- v5n: HOW MANY guests must be identified is a property policy, not a constant ----
        # `checkin_id_scope`:
        #   lead       — the guest the booking is in, nobody else;
        #   per_room    — one responsible guest for EACH room being assigned (the default here:
        #                 a three-room group hands over three IDs, one per room key);
        #   all_adults  — one per adult the booking was made for (the pre-v5n rule).
        # Everyone beyond the required count is optional: name only, no ID, no scan — which is what
        # makes a 2 a.m. family of six checkable-in at the speed the desk actually works.
        # `per_room` deliberately ignores booking.adults: the website collects ONE occupancy number
        # per booking, so a three-room reservation routinely arrives saying adults = 1.
        fd_cfg = app_settings.get_frontdesk_config(db)
        id_scope = fd_cfg["id_scope"]
        scans_required = fd_cfg["scans_required"]
        room_ids_being_assigned = list(rooms_by_id.keys())
        if id_scope == "per_room":
            required_ids = len(room_ids_being_assigned)
        elif id_scope == "all_adults":
            required_ids = int(booking.adults or 1)
        else:
            required_ids = 1

        identified = [g for g in roster if not g.is_minor and g.id_number]
        # The lead's ID always arrives on the request itself (schema-mandatory), so an empty roster
        # still means exactly one identified guest.
        identified_count = len(identified) if roster else 1

        # With `per_room` the IDs must be spread ACROSS the rooms, and naming the rooms still short of
        # one is the only message the desk can act on — so this runs BEFORE the bare count check
        # (which for per_room would only be able to say "1 of 3"). Three IDs all filed against room
        # 301 satisfies a count while leaving two rooms with nobody accountable for them.
        if id_scope == "per_room" and roster:
            rooms_with_id = {g.room_id for g in identified if g.room_id}
            missing = [rid for rid in room_ids_being_assigned if rid not in rooms_with_id]
            if missing:
                labels = ", ".join(sorted(rooms_by_id[rid].room_number for rid in missing))
                raise HTTPException(
                    status_code=400,
                    detail=f"No guest ID captured for room(s) {labels} — each room needs one "
                           f"identified guest.")

        # ---- v5r: the guest's OWN number, and one per room -------------------------------------
        # Two rules the owner asked for, both about the same failure: a stay whose only contact number
        # belongs to somebody else.
        #
        #   (a) An OTA booking arrives carrying the CHANNEL's number — either the synthetic placeholder
        #       stamped from the booking id (Go-MMT and friends mask the guest entirely) or the OTA's own
        #       call centre, scraped out of the voucher body. Every WhatsApp the PMS sends would go
        #       there, and because guests are matched on phone, two OTA guests on one number merge into
        #       a single profile — one blacklist then blocks them all.
        #   (b) On a multi-room booking each room needs its own reachable number. The booking's number
        #       counts for at most ONE room: "all three rooms are on the payer's mobile" leaves two
        #       rooms with no way to be told their food is coming.
        from utils import phone as _ph

        is_ota_booking = ota_service.is_ota_source(booking.booking_source, db)
        lead_phone = (data.phone or "").strip()
        stored_phone = (booking.guest.phone if booking.guest else None)

        def _refuse_channel_number(value, where):
            """Common checks for any number offered as a guest contact."""
            if _ph.is_blocked(db, value):
                raise HTTPException(
                    status_code=400,
                    detail=f"{where} is on the blocked list (an OTA / call-centre number) — "
                           f"ask the guest for their own mobile.")
            if not _ph.is_valid_mobile(value):
                raise HTTPException(
                    status_code=400,
                    detail=f"{where} ({_ph.describe(value)}) is not a mobile number — "
                           f"enter a 10-digit mobile.")

        if is_ota_booking:
            # The desk must replace what the channel sent; nothing else identifies the guest.
            if not lead_phone:
                raise HTTPException(
                    status_code=400,
                    detail="This booking came from an OTA, which hides the guest's number — "
                           "capture the guest's own mobile before checking in.")
            # Order matters: the blocked list and the plain "that is not a mobile" reasons are more
            # specific than "that is a placeholder", and `is_ota_placeholder` would otherwise swallow
            # both (it treats anything that is not a valid mobile as a placeholder). The desk being told
            # the actual reason is the difference between fixing it and arguing with the screen.
            if _ph.is_blocked(db, lead_phone):
                raise HTTPException(
                    status_code=400,
                    detail="That number is on the blocked list (an OTA / call-centre number) — "
                           "ask the guest for their own mobile.")
            if booking.ota_booking_id and _ph.same_number(
                    lead_phone, _ph.ota_placeholder(booking.ota_booking_id)):
                raise HTTPException(
                    status_code=400,
                    detail="That is the placeholder the OTA booking id generated, not a phone number — "
                           "ask the guest for their own mobile.")
            if stored_phone and _ph.same_number(lead_phone, stored_phone):
                raise HTTPException(
                    status_code=400,
                    detail="That is the number the OTA sent with the booking, which reaches the channel "
                           "and not the guest — capture the guest's own mobile.")
            _refuse_channel_number(lead_phone, "The guest phone")
        elif lead_phone:
            _refuse_channel_number(lead_phone, "The guest phone")

        # Per-room numbers. Enforced whenever the roster is room-mapped (i.e. the `per_room` rule, which
        # is what makes "one responsible guest per room" meaningful) and there is more than one room.
        if id_scope == "per_room" and roster and len(room_ids_being_assigned) > 1:
            effective_lead = lead_phone or stored_phone
            seen: dict[str, int] = {}          # normalised number -> the room that first used it
            missing_phone = []
            for rid in room_ids_being_assigned:
                row = next((g for g in identified if g.room_id == rid and (g.phone or "").strip()), None)
                if row is None:
                    missing_phone.append(rid)
                    continue
                _refuse_channel_number(row.phone, f"Room {rooms_by_id[rid].room_number}'s phone")
                key = _ph.normalize(row.phone)
                if key in seen:
                    both = ", ".join(sorted({rooms_by_id[seen[key]].room_number,
                                             rooms_by_id[rid].room_number}))
                    raise HTTPException(
                        status_code=400,
                        detail=f"Rooms {both} were given the same number — each room needs its own "
                               f"contact so the right guest can be reached.")
                seen[key] = rid
            if missing_phone:
                labels = ", ".join(sorted(rooms_by_id[rid].room_number for rid in missing_phone))
                raise HTTPException(
                    status_code=400,
                    detail=f"No phone captured for room(s) {labels} — on a {len(room_ids_being_assigned)}-room "
                           f"booking each room needs its own contact number.")
            # The booking's own number may stand in for exactly one room.
            if effective_lead:
                lead_key = _ph.normalize(effective_lead)
                on_lead = [rid for key, rid in seen.items() if key == lead_key]
                if len(on_lead) > 1:
                    raise HTTPException(
                        status_code=400,
                        detail="The booking's own number can cover only one room — the other rooms need "
                               "their own guests' numbers.")

        if identified_count < required_ids:
            if id_scope == "per_room":
                detail = (f"This booking is {required_ids} room(s) — capture one guest's ID per room "
                          f"(only {identified_count} on file).")
            elif id_scope == "all_adults":
                detail = (f"This booking is for {booking.adults} adult(s) — capture an ID for every adult "
                          f"(only {identified_count} on file). Children can be added without an ID.")
            else:
                detail = "Capture the lead guest's ID before check-in."
            raise HTTPException(status_code=400, detail=detail)

        # The scanned document must be on file for whoever had to show an ID (the number alone is not
        # a record). Guests who were never required to identify themselves are not asked for scans.
        if roster and scans_required:
            with_scans = sum(1 for g in identified if g.id_scan_ref and g.id_scan_back_ref)
            if with_scans < required_ids:
                raise HTTPException(
                    status_code=400,
                    detail=f"Scan the front AND back of the ID for every guest who must show one — "
                           f"{required_ids} needed, {with_scans} with both scans on file.")

        # Every scan ref must belong to THIS booking, and no two occupants may share one.
        # The refs come back to us on this payload, and nothing tied them to the booking they were
        # uploaded against: putting one guest's ref on the whole roster made three occupants read
        # "ID on file" from a single scan, and made every later `id.view` audit the wrong guest.
        # Both are integrity holes in the KYC record, which is the point of collecting it at all.
        seen_refs: dict[str, str] = {}
        for g in roster:
            for ref in (g.id_scan_ref, g.id_scan_back_ref):
                if not ref:
                    continue
                if not secure_id_store.ref_belongs_to(ref, booking.booking_id):
                    raise HTTPException(
                        status_code=400,
                        detail=f"The ID scan attached to {g.name or 'this guest'} was not uploaded "
                               f"for this booking. Scan the ID again from this check-in.")
                if ref in seen_refs:
                    raise HTTPException(
                        status_code=400,
                        detail=f"The same ID scan is attached to {seen_refs[ref]} and "
                               f"{g.name or 'another guest'}. Every occupant needs their own ID.")
                seen_refs[ref] = g.name or "another guest"

        if roster:
            for g in roster:
                # v5n: room_id ties the occupant to the room they are actually in (a booking can be
                # several rooms), and phone is that room's own contact for its portal link. A room id
                # the desk did not just assign is dropped rather than trusted.
                g_room = g.room_id if g.room_id in rooms_by_id else None
                db.add(BookingGuest(
                    booking_id=booking.booking_id,
                    name=(g.name or "").strip() or (booking.guest.name if booking.guest else ""),
                    id_type=validate_category(db, "id_type", g.id_type) if g.id_type else None,
                    id_number_masked=("****" + g.id_number[-4:]) if g.id_number else None,
                    id_scan_ref=g.id_scan_ref,
                    id_scan_mime=g.id_scan_mime,
                    id_scan_back_ref=g.id_scan_back_ref,
                    id_scan_back_mime=g.id_scan_back_mime,
                    is_primary=g.is_primary,
                    is_minor=g.is_minor,
                    room_id=g_room,
                    phone=(g.phone or "").strip() or None,
                ))
        else:
            db.add(BookingGuest(
                booking_id=booking.booking_id,
                name=booking.guest.name if booking.guest else "",
                id_type=lead_id_type,
                id_number_masked=booking.guest.id_number_masked,
                is_primary=True,
                room_id=next(iter(rooms_by_id), None),
            ))

        # OTA bookings arrive with a masked/placeholder phone and no email — capture the real
        # contact the desk collected at arrival, onto the guest record.
        if booking.guest is not None:
            if data.phone and data.phone.strip():
                booking.guest.phone = data.phone.strip()
            if data.email:
                booking.guest.email = str(data.email).strip()

        booking.status = "checked_in"
        booking.checked_in_at = now
        # v5m: the 24h stay clock anchor per the arrival rules (expected time for an early or
        # badly-late guest, the actual moment otherwise). OTA stays ignore it (always 12→12).
        booking.stay_started_at = arrival["stay_start"]
        if not getattr(booking, "expected_arrival_at", None):
            booking.expected_arrival_at = arrival["expected_at"]
        for room in rooms_by_id.values():
            room.status = "occupied"
            room.status_changed_at = datetime.utcnow()  # prompt 11: cleaning-too-long detection
        db.commit()

        # FE-10: raise a "turn on AC" maintenance ticket for each assigned AC-type room.
        # Best-effort — never blocks a check-in. Idempotent per booking+room.
        try:
            from routers.maintenance import raise_ac_on_ticket
            ac_type_ids = {rt_id for rt_id in (r.room_type_id for r in rooms_by_id.values())
                           if db.query(RoomType).filter(RoomType.room_type_id == rt_id,
                                                        RoomType.is_ac == True).first()}  # noqa: E712
            for room in rooms_by_id.values():
                if room.room_type_id in ac_type_ids:
                    raise_ac_on_ticket(db, booking.booking_id, room)
            db.commit()
        except Exception as e:
            logger.error(f"AC-on ticket at check-in failed for booking {booking.booking_id}: {e}")
            db.rollback()

        # Folio (idempotent; posts room charges + advance payment credits, own commit).
        open_folio(FolioOpenRequest(booking_id=booking.booking_id), db, user)
        folio = db.query(Folio).filter(Folio.booking_id == booking.booking_id).first()

        # ---- v5m/v5n: early / late arrival — fee or extra night PER ROOM, one event per room ----
        # v5n: a fee is a room's fee, not a booking's. Only the rooms whose guests actually arrived
        # early are charged (the desk ticks them), each line carries its OWN room type's GST slab, and
        # each room gets its own stay_event — so the arrival-exceptions report names the room and
        # voiding one room's fee leaves the others standing.
        arrival_charge_id = None
        if folio and arrival["kind"] in ("early", "late"):
            try:
                exp = arrival["expected_at"]
                # Re-resolve AFTER the split: every charged room now has its own booking-item row, so
                # each fee line can carry that room's id and its own room type's GST slab.
                selection = _fee_selection(db, booking, data.early_room_ids)
                charged_items = [item for item, _rid in selection]
                sel_rooms = [rooms_by_id.get(rid) if rid else None for _item, rid in selection]
                basis = "override" if fee_changed else arrival["basis"]
                events: list[tuple] = []          # (room, charge_id, amount)

                if arrival["kind"] == "early" and arrival["extra_nights"] >= 1:
                    # Whole extra night(s) for the rooms that came early: priced through the rate
                    # engine (spread to the applied amount when the desk changed it; nothing posted
                    # for a comp stay). The booking's check-in only moves back when EVERY room came
                    # early — otherwise the booked dates stand and the early night is an explicit
                    # line, because the other rooms genuinely were not occupied that night.
                    d_from = booking.check_in - timedelta(days=arrival["extra_nights"])
                    room_posting.assert_nights_identifiable(db, folio.id)
                    posted = room_posting.post_room_nights(
                        db, booking, folio, d_from, booking.check_in, user=user,
                        posting_reason="early", price_mode=room_posting.PRICE_QUOTE,
                        override_total=fee_applied if fee_changed else None, recompute=False,
                        only_items=sorted({i.booking_item_id for i in charged_items}))
                    if posted["posted"]:
                        arrival_charge_id = posted["posted"][0]
                        for c in db.query(FolioCharge).filter(FolioCharge.id.in_(posted["posted"])).all():
                            room = rooms_by_id.get(
                                next((rid for item, rid in selection
                                      if item.booking_item_id == c.booking_item_id), None))
                            c.description = f"Early check-in — {c.description}"
                            c.room_id = room.room_id if room else None
                            events.append((room, c.id, float(c.amount or 0)))
                    if not booking.original_check_in:
                        booking.original_check_in = booking.check_in
                    if len(selection) == sum(max(1, int(i.quantity or 1))
                                             for i in booking.booking_items):
                        booking.check_in = d_from
                elif arrival["kind"] == "early" and fee_applied > 0:
                    # Split the applied fee across the charged rooms, the last room absorbing the
                    # rounding remainder — the same paisa-exact rule the folio uses everywhere else.
                    n = max(1, len(selection))
                    per = round(fee_applied / n, 2)
                    amounts = [per] * (n - 1) + [round(fee_applied - per * (n - 1), 2)]
                    for (item, _rid), room, amount in zip(selection, sel_rooms, amounts):
                        label = f" — Room {room.room_number}" if room else ""
                        charge = FolioCharge(
                            folio_id=folio.id, type="room",
                            description=(f"Early check-in fee{label} — "
                                         f"{_fmt_dev(arrival['deviation_minutes'])} before {exp:%H:%M}"),
                            qty=1, unit_price=amount, amount=amount,
                            gst_percent=_item_gst_percent(db, item),
                            posted_by=_resolve_user_id(db, user), charge_date=today,
                            posting_reason="early", room_id=room.room_id if room else None)
                        db.add(charge)
                        db.flush()
                        arrival_charge_id = arrival_charge_id or charge.id
                        events.append((room, charge.id, amount))

                rule_snapshot = arrival_rules.rule_snapshot(
                    {**(arrival["rule"] or {}), "quoted": fee_quoted,
                     "reason": data.arrival_fee_reason,
                     "rooms_charged": arrival.get("rooms_charged"),
                     "total_rooms": arrival.get("total_rooms"),
                     "stay_start": arrival["stay_start"].isoformat()})
                kind = "early_checkin" if arrival["kind"] == "early" else "late_arrival"
                uid = _resolve_user_id(db, user)
                if not events:
                    # Free / exempt / late arrival: no money, but the deviation is still the record.
                    events = [(next((r for r in sel_rooms if r), None), None, 0.0)]
                for room, charge_id, amount in events:
                    db.add(StayEvent(
                        booking_id=booking.booking_id, kind=kind,
                        room_id=room.room_id if room else None,
                        expected_at=exp, actual_at=now,
                        deviation_minutes=arrival["deviation_minutes"],
                        charge_amount=amount if arrival["kind"] == "early" else 0,
                        # A late arrival is never charged, so its basis is simply "free".
                        charge_basis=basis if arrival["kind"] == "early" else "free",
                        rule_json=rule_snapshot,
                        approval=arrival_approval,
                        approved_by=uid if arrival_approval != "none" else None,
                        folio_charge_id=charge_id,
                        created_by=uid))
                _recompute(db, folio)
                db.commit()
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"arrival fee posting failed for booking {booking.booking_id}: {e}", exc_info=True)
                db.rollback()

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
                           # v4b7: which rooms went out as their alternate type, and under
                           # which approval. This is the record the owner reviews later.
                           "alt_type_sales": [
                               {"room_number": _r.room_number,
                                "booked_type_id": _i.room_type_id,
                                "sold_as_type_id": _r.room_type_id}
                               for _i, _r in alt_sales],
                           "alt_type_otp_id": data.owner_otp_id if alt_sales else None,
                           # v5m: how the arrival rules were applied (the report reads stay_events).
                           "kyc": {"id_scope": id_scope, "required_ids": required_ids,
                                   "identified": identified_count, "scans_required": scans_required},
                           "arrival": {"kind": arrival["kind"], "deviation_minutes": arrival["deviation_minutes"],
                                       "quoted": fee_quoted, "applied": fee_applied,
                                       "basis": arrival["basis"], "approval": arrival_approval,
                                       "reason": data.arrival_fee_reason,
                                       "stay_start": arrival["stay_start"].isoformat()},
                           "client_ref": data.client_ref},
                    client="desktop", commit=True)

        # Best-effort WhatsApp welcome / room-ready (prompt 15).
        _room_nums = ", ".join(r.room_number for r in rooms_by_id.values())
        _wa_notify(db, "wa_room_ready_enabled", booking.guest, "room_ready",
                   {"guest_name": booking.guest.name, "room_label": _room_nums},
                   booking_id=booking.booking_id, client_ref=f"room_ready:{booking.booking_id}")

        # Auto-send the in-room portal link to the guest on check-in (self-service: room service,
        # wifi, checkout…). Gated by wa_portal_link_enabled so the owner can switch it off for cost.
        # Best-effort — a portal/WhatsApp failure must never break the check-in.
        try:
            if booking.guest and booking.guest.phone \
                    and app_settings.get_whatsapp_config(db).get("portal_link_enabled", True):
                from routers.portal import _get_or_create_session, _portal_url, _room_number
                from utils import whatsapp_service as _wa
                # v5n: one message to the number the booking was made with, naming every room it
                # covers. Each room has its OWN portal session and QR card (printed at the desk from
                # the Portal QR overlay) — WhatsApp can only reach a phone, and a group books on one.
                _sess = _get_or_create_session(db, booking, user)
                _wa.send_template(
                    db, booking.guest.phone, "portal_link",
                    {"guest_name": booking.guest.name or "Guest",
                     "room_number": (_room_nums or _room_number(db, _sess.room_id) or "-"),
                     "link": _portal_url(_sess.token)},
                    guest_id=booking.guest_id, booking_id=booking.booking_id,
                    client_ref=f"portal_link:{_sess.token}", respect_optout=False)
                db.commit()
        except Exception as e:
            logger.warning(f"check-in portal link auto-send failed for booking {booking.booking_id}: {e}")
            try:
                db.rollback()
            except Exception:
                pass

        db.refresh(booking)
        resp = _checkin_response(db, booking, folio, already=False)
        resp["vip"] = checkin_gate["vip"]
        resp["blacklist_warning"] = (checkin_gate["reason"] or "Guest is on the watchlist") \
            if checkin_gate["blacklist"] else None
        resp["company_routing"] = company_routing
        resp["arrival"] = _arrival_public(arrival)
        resp["folio_balance"] = float(folio.balance or 0) if folio else None
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

        # v4b4: the guest's key card must come back and be wiped on the encoder.
        # The desk reads + erases each card and sends the UIDs; if none came back and the gate
        # is armed, the owner has to approve the checkout.
        # ⚠️ Deliberately gated on a SETTING that ships OFF (see get_fraud_config). Making the
        # busiest moment of the day depend on a working encoder is a real operational risk —
        # an unplugged reader at 11 am with six departures queued means six calls to the owner,
        # and the predictable workaround is a standing code shared with the desk, which defeats
        # the whole mechanism. So: record from day one, arm only once the owner has seen how
        # often cards actually come back.
        erased_uids = [u.strip() for u in (data.cards_erased or []) if u and u.strip()]
        # A booking made up entirely of key-lock rooms never had a card to hand back, so the
        # "no card returned" approval must not fire for it. Only gate when at least one room is a card lock.
        has_card_room = any(
            (r.lock_type != "key")
            for r in (db.query(Room).filter(Room.room_id == it.room_id).first()
                      for it in booking.booking_items if it.room_id)
            if r is not None)
        if has_card_room and not erased_uids \
                and app_settings.get_fraud_config(db).get("checkout_no_card_otp_required"):
            consume_otp(db, data.owner_otp_id, data.owner_otp_code, "checkout_no_card", user)

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
        # v4b4: a card the desk physically WIPED on the encoder is recorded as `erased` with
        # who did it and when — a stronger fact than `checked_out`, which only ever meant "the
        # database says this stay is over". Match on the UID read back at encode time
        # (CardIssuance.card_uid), falling back to "everything on this booking" when the desk
        # erased cards but the UIDs don't line up (an older desk build, or a card issued before
        # UIDs were captured) — the physical wipe happened either way.
        uid_set = {u.lower() for u in erased_uids}
        matched = [c for c in cards if (c.card_uid or "").lower() in uid_set] if uid_set else []
        if erased_uids and not matched:
            matched = cards
        now_ts = datetime.now()
        uid_by_id = {c.id: c for c in matched}
        for c in cards:
            if c.id in uid_by_id:
                c.status = "erased"
                c.erased_at = now_ts
                c.erased_by = _resolve_user_id(db, user)
            else:
                c.status = "checked_out"
        cards_erased_count = len(matched)

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
                           # v4b4: recorded whether or not the gate is armed, so "how often does
                           # a card actually come back?" is answerable from the audit log.
                           "cards_erased": cards_erased_count,
                           "cards_erased_uids": erased_uids,
                           "encoder_unavailable": data.encoder_unavailable,
                           "no_card_otp_id": data.owner_otp_id,
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

        # ...and send it to the guest. The invoice has always been created here and never sent;
        # the desk had to email it by hand. Best-effort and idempotent per invoice number — a
        # messaging failure must never block a checkout.
        if invoice_no:
            try:
                from routers.folio import _invoice_payload, _get_invoice
                from utils.pdf_generator import generate_folio_invoice_pdf
                from services import notify as notify_service
                _inv = _get_invoice(db, folio.id)
                _guest = booking.guest
                _inv_pdf = None
                try:
                    _inv_pdf = generate_folio_invoice_pdf(_invoice_payload(db, folio, _inv))
                except Exception as e:
                    logger.warning(f"invoice PDF for WhatsApp failed (sending text instead): {e}")
                notify_service.notify_guest_document(
                    db, _guest,
                    doc_template="invoice_doc", text_template="invoice_doc",
                    doc_params={"guest_name": _guest.name if _guest else "Guest",
                                "invoice_no": invoice_no},
                    text_params={"guest_name": _guest.name if _guest else "Guest",
                                 "invoice_no": invoice_no},
                    pdf_path=_inv_pdf,
                    filename=invoice_no.replace("/", "-") + ".pdf",
                    caption=f"Tax invoice {invoice_no} \u2014 Hotel Bhimas",
                    setting_key="wa_invoice_enabled",
                    booking_id=booking.booking_id,
                    client_ref=f"invoice:{invoice_no}")
            except Exception as e:
                logger.error(f"invoice WhatsApp at checkout failed for booking {booking.booking_id}: {e}")

        # Linen (FE-9): send each room's default launderable set clean → dirty. Best-effort —
        # a failure here must never break a checkout (own commit/rollback inside the service).
        try:
            from services import linen_service
            linen_service.on_checkout(db, booking)
        except Exception as e:
            logger.error(f"linen on_checkout hook failed for booking {booking.booking_id}: {e}")

        # FE-10 follow-up: raise a "turn off AC" task for each vacated AC room so an empty
        # room isn't cooled all day. Best-effort — never blocks a checkout.
        try:
            from routers.maintenance import raise_ac_off_ticket
            for room in rooms:
                rt = db.query(RoomType).filter(RoomType.room_type_id == room.room_type_id,
                                               RoomType.is_ac == True).first()  # noqa: E712
                if rt:
                    raise_ac_off_ticket(db, booking.booking_id, room)
            db.commit()
        except Exception as e:
            logger.error(f"AC-off ticket at checkout failed for booking {booking.booking_id}: {e}")
            db.rollback()

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
    # v4b4: `erased` means the card was physically wiped on the encoder; `checked_out` only
    # means the database closed the stay. Both count as invalidated for the desk's toast.
    cards_out = db.query(func.count(CardIssuance.id)).filter(
        CardIssuance.booking_id == booking.booking_id,
        CardIssuance.status.in_(["checked_out", "erased"]),
    ).scalar() or 0
    cards_erased = db.query(func.count(CardIssuance.id)).filter(
        CardIssuance.booking_id == booking.booking_id,
        CardIssuance.status == "erased",
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
        "cards_erased": cards_erased,
        "rooms": rooms,
    }


def _early_checkout_new_co(booking: Booking, now: datetime) -> date:
    """The check-out DATE a guest leaving at `now` is billed through.

    A guest who lingers past TODAY's check-out deadline (check-out time + grace) has used
    tonight's room, so the unused-night cut must start TOMORROW, not today. Before the
    deadline they owe only the nights already slept, so the cut starts today.
    The daily deadline mirrors `booking_checkout_moment`:
      OTA   -> today @ 12:00      (contracted noon-to-noon)
      fixed -> today @ CHECKOUT_HOUR
      24h   -> today @ the actual check-in clock-time (10pm in -> 10pm out)."""
    today = now.date()
    if ota_service.is_ota_source(booking.booking_source):
        deadline_t = time(hour=12)
    elif _checkout_mode() == "fixed":
        deadline_t = time(hour=_checkout_hour())
    else:
        base = booking.checked_in_at or now
        deadline_t = base.time().replace(microsecond=0)
    deadline = datetime.combine(today, deadline_t) + timedelta(minutes=_grace_minutes())
    return today if now <= deadline else today + timedelta(days=1)


@router.post("/checkout/early")
def early_checkout(data: EarlyCheckoutRequest, db: Session = Depends(get_db),
                   user=Depends(require_reception_or_admin)):
    """Shorten an in-house stay when the guest leaves early (v4).

    Voids the room-night charges for the UNUSED future nights (`charge_date >= new_check_out`),
    moves `check_out` back and recomputes the folio, so the guest is billed only for nights stayed.
    A prepaid / already-paid guest then shows an OVERPAYMENT, which the normal /checkout
    'return excess / refund' flow settles. `dry_run` previews the credit without mutating.
    Mirrors /overstay/reverse's night-void mechanics; idempotent (a re-void is a no-op)."""
    try:
        booking = (db.query(Booking).filter(Booking.booking_id == data.booking_id)
                     .with_for_update().first())
        if not booking:
            raise HTTPException(status_code=404, detail="Booking not found")
        if booking.status != "checked_in":
            raise HTTPException(
                status_code=409,
                detail=f"Only an in-house stay can be checked out early (this one is '{booking.status}')")
        folio = db.query(Folio).filter(Folio.booking_id == booking.booking_id).first()
        if not folio:
            raise HTTPException(status_code=409, detail="No folio for this stay")
        _ensure_editable_folio(db, folio)

        # Default (desk sends none): bill through the day the guest actually leaves. If they've
        # already passed today's check-out deadline (time + grace), tonight is kept — the cut
        # starts tomorrow — so a late-afternoon departure never wrongly refunds tonight's rent.
        new_co = data.new_check_out or _early_checkout_new_co(booking, datetime.now())
        if new_co <= booking.check_in:
            raise HTTPException(status_code=400,
                                detail="Early check-out must leave at least one night stayed")
        if new_co >= booking.check_out:
            raise HTTPException(
                status_code=409,
                detail="That date is not before the booked check-out — nothing to shorten")

        nights_dropped = (booking.check_out - new_co).days
        # Unused booked room-nights: non-voided room lines dated on/after the new check-out.
        lines = (db.query(FolioCharge)
                   .filter(FolioCharge.folio_id == folio.id,
                           FolioCharge.type == "room",
                           FolioCharge.charge_date >= new_co,
                           FolioCharge.reversal_of_id.is_(None),
                           FolioCharge.void == False)  # noqa: E712
                   .all())
        credit = round(sum(float(l.amount or 0) for l in lines), 2)
        bal_before = float(folio.balance or 0)

        if data.dry_run:
            return {"booking_id": booking.booking_id, "dry_run": True,
                    "new_check_out": str(new_co), "nights_dropped": nights_dropped,
                    "credit": credit,
                    "folio_balance_before": bal_before,
                    "folio_balance_after": round(bal_before - credit, 2)}

        before = {"check_out": str(booking.check_out), "grand_total": float(booking.grand_total or 0),
                  "folio_balance": bal_before}
        for line in lines:
            _void_charge_row(db, line, "Early check-out — unused night dropped", user)
            item = db.query(BookingItem).filter(
                BookingItem.booking_item_id == line.booking_item_id).first()
            if item:
                amt = float(line.amount or 0)
                gstp = float(db.query(RoomType.gst_percent).filter(
                    RoomType.room_type_id == item.room_type_id).scalar() or 0)
                base = round(amt / (1 + gstp / 100), 2)
                item.base_amount = round(float(item.base_amount or 0) - base, 2)
                item.gst_amount = round(float(item.gst_amount or 0) - (amt - base), 2)
                item.total_amount = round(float(item.total_amount or 0) - amt, 2)

        booking.check_out = new_co
        booking.total_amount = round(float(booking.total_amount or 0) - credit, 2)
        booking.grand_total = round(float(booking.grand_total or 0) - credit, 2)
        # The stay window just got shorter — the card now outlives the stay, so it must be re-cut.
        booking.card_reencode_required = True

        _recompute(db, folio)
        db.commit()
        db.refresh(folio)

        write_audit(db, user, "reception.early_checkout", "booking", booking.booking_id,
                    before=before,
                    after={"check_out": str(new_co), "nights_dropped": nights_dropped,
                           "credit": credit, "grand_total": float(booking.grand_total or 0),
                           "folio_balance": float(folio.balance or 0)},
                    client="desktop", commit=True)

        return {"booking_id": booking.booking_id, "dry_run": False,
                "new_check_out": str(new_co), "nights_dropped": nights_dropped,
                "credit": credit, "folio_balance": float(folio.balance or 0),
                "grand_total": float(booking.grand_total or 0),
                "card_reencode_required": True}
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        logger.error(f"early_checkout failed: {e}", exc_info=True)
        db.rollback()
        raise HTTPException(status_code=500, detail="Early check-out failed")


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
        # v4b7: three cases now, not two.
        #   same type      -> no gate, no adjustment (unchanged)
        #   ALTERNATE sale -> the room can be let as the booked type; guest keeps their price
        #   cross type     -> a real re-type, priced as before (unchanged)
        is_alt_sale = cross_type and new_room.alt_room_type_id == item.room_type_id

        # Cross-type shifts consume the new type's capacity for the remaining dates —
        # don't strand a future confirmed reservation of that type.
        # An alternate sale does NOT: the reservation stays in its booked bucket.
        if cross_type and not is_alt_sale and today < booking.check_out:
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
        # ⚠️ An ALTERNATE sale is priced at ZERO difference on purpose. The guest booked a type
        # at a price; putting them in a physically different room is the hotel's operational
        # convenience, not a re-sale — so they neither pay more nor get a refund. That is also
        # precisely why it needs an owner code: the fraud vector is a receptionist putting a
        # guest into a ₹4,000 room booked at ₹2,000, or pocketing the difference in cash.
        suggested = 0.0 if is_alt_sale else round((new_rate - old_rate) * remaining_nights, 2)
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
            # v4b7: told up front so the desk can show the approval panel BEFORE a rejected
            # submit, and explain that an alternate sale costs the guest nothing extra.
            "is_alt_room_type_sale": is_alt_sale,
            "requires_owner_otp": bool(
                (bool(old_type.is_ac) and not bool(new_type.is_ac)
                 and app_settings.get_fraud_config(db)["ac_downgrade_otp_required"])
                or (is_alt_sale
                    and app_settings.get_fraud_config(db)["alt_room_type_otp_required"])),
            # Key-lock target room => no card to cut; the desk skips encoding on a null card.
            "card": (None if new_room.lock_type == "key"
                     else _encode_payload(db, item, new_room, valid_from, valid_to)),
        }

        if data.dry_run:
            db.rollback()  # release the row locks; nothing was mutated
            return response

        # ---- v4b7: ONE approval per user action, with a `reasons[]` list ----
        # Two different things can be wrong with a room move and BOTH can be true at once:
        #   ac_downgrade  — the guest gets less comfort than they paid for (service risk)
        #   alt_room_type — this room is being sold as a type it is not (revenue leak)
        # Asking for two codes for one button press is unusable, so they are reasons under a
        # single `room_assignment` action. `ac_downgrade` survives as an action string only for
        # older desk builds; new requests should send `room_assignment`.
        _fc = app_settings.get_fraud_config(db)
        reasons = []
        if bool(old_type.is_ac) and not bool(new_type.is_ac) and _fc["ac_downgrade_otp_required"]:
            reasons.append("ac_downgrade")
        if new_room.alt_room_type_id == item.room_type_id and _fc["alt_room_type_otp_required"]:
            # Same rule as check-in: an alternate sale is only for when the booked type is gone.
            _assert_booked_type_sold_out(db, item, new_room)
            reasons.append("alt_room_type")
        if reasons:
            consume_otp(db, data.owner_otp_id, data.owner_otp_code, "room_assignment", user)

        # ---- mutate (single transaction) ----
        before = {"room_id": old_room.room_id, "room_number": old_room.room_number,
                  "room_type_id": item.room_type_id, "room_type": old_type.name,
                  "folio_balance": float(folio.balance or 0)}

        item.room_id = new_room.room_id
        if is_alt_sale:
            # v4b7: an ALTERNATE-type sale is not a re-sale. The guest keeps the type (and the
            # price) they booked; only the physical room differs, which is the hotel's
            # convenience. So room_type_id is left alone — changing it would move a live
            # reservation between inventory buckets — and the physical type is recorded here.
            item.sold_as_room_type_id = new_room.room_type_id
        elif cross_type:
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
                           "alt_room_type_sale": is_alt_sale,
                           "sold_as_room_type_id": (new_room.room_type_id if is_alt_sale else None),
                           "approval_reasons": reasons,
                           "folio_balance": float(folio.balance or 0),
                           "client_ref": data.client_ref},
                    client="desktop", commit=True)

        # FE-10: shifting INTO an AC room raises the "turn on AC" task (best-effort).
        if new_type.is_ac:
            try:
                from routers.maintenance import raise_ac_on_ticket
                raise_ac_on_ticket(db, booking.booking_id, new_room, commit=True)
            except Exception as e:
                logger.error(f"AC-on ticket on shift failed for booking {booking.booking_id}: {e}")
                db.rollback()

        response["superseded_card_ids"] = superseded_ids
        response["folio_balance"] = float(folio.balance or 0)
        # active_cards on the new room may have changed after the supersede/commit.
        # Key-lock target room => no card to cut.
        response["card"] = (None if new_room.lock_type == "key"
                            else _encode_payload(db, item, new_room, valid_from, valid_to))
        return response
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        logger.error(f"shift_room failed: {e}", exc_info=True)
        db.rollback()
        raise HTTPException(status_code=500, detail="Room shift failed")


def _extend_waive_requires_admin() -> bool:
    return os.getenv("EXTEND_WAIVE_REQUIRES_ADMIN", "true").strip().lower() not in ("false", "0", "no")


EXTEND_MAX_DAYS = 30


@router.post("/extend")
def extend_stay(data: ExtendStayRequest, db: Session = Depends(get_db),
                user=Depends(require_reception_or_admin)):
    """Extend an in-house guest's stay (v4b2).

    Deliberately a sibling of /reception/shift: dry-run-then-commit, the SERVER prices it and
    the desktop never computes money, and the response carries the same CardEncodeInfo shape
    so the desk reuses EncodeAndRecordAsync untouched.

    Why `checked_in` ONLY:
      * `confirmed` — no room is assigned yet (item.room_id is NULL until check-in), so there
        is no card to re-cut. Changing the dates of a stay that has not started is a booking
        amendment and should re-price through the booking path, not append nights.
      * `checked_out` — the folio is settled AND an invoice has been issued. You cannot append
        to a sequential GST document. There is deliberately no folio-reopen path; the right
        answer to a post-checkout correction is a credit note, which is separate work.
    """
    try:
        # ⚠️ No joinedload here. `FOR UPDATE` + an outer join makes Postgres raise
        # "FOR UPDATE cannot be applied to the nullable side of an outer join". Lock the
        # booking row on its own; guest / booking_items lazy-load afterwards.
        booking = (db.query(Booking)
                     .filter(Booking.booking_id == data.booking_id)
                     .with_for_update().first())
        if not booking:
            raise HTTPException(status_code=404, detail="Booking not found")
        if booking.status != "checked_in":
            raise HTTPException(
                status_code=409,
                detail=f"Only an in-house stay can be extended (this one is '{booking.status}')")

        old_co = booking.check_out
        new_co = data.new_check_out

        folio = db.query(Folio).filter(Folio.booking_id == booking.booking_id).first()
        if not folio:
            raise HTTPException(status_code=409, detail="No folio for this stay — open it first")

        # Replay guard. `new_check_out` is absolute, so a re-flushed desk outbox row lands
        # here instead of extending a second time.
        if new_co <= old_co:
            return {"booking_id": booking.booking_id, "dry_run": data.dry_run, "already": True,
                    "from_check_out": str(old_co), "to_check_out": str(old_co), "extra_nights": 0,
                    "items": [], "quoted_amount": 0.0, "applied_amount": 0.0,
                    "requires_reason": False, "requires_admin": _extend_waive_requires_admin(),
                    "folio_id": folio.id, "folio_balance_before": float(folio.balance or 0),
                    "folio_balance_after": float(folio.balance or 0),
                    "grand_total_before": float(booking.grand_total or 0),
                    "grand_total_after": float(booking.grand_total or 0),
                    "posted_charge_ids": [], "superseded_card_ids": [],
                    "card_reencode_required": bool(booking.card_reencode_required),
                    "cards": []}
        if (new_co - old_co).days > EXTEND_MAX_DAYS:
            raise HTTPException(status_code=400,
                                detail=f"An extension is capped at {EXTEND_MAX_DAYS} nights "
                                       f"— create a new booking for a longer stay")

        # An invoice already raised mid-stay freezes the folio. Say WHICH one, so the desk
        # knows what to do instead of reading the generic "already invoiced".
        inv = _get_invoice(db, folio.id)
        if folio.status != "open" or inv:
            raise HTTPException(
                status_code=409,
                detail=(f"Invoice {inv.invoice_no} has already been raised for this stay — an "
                        f"extension cannot be added to an issued tax invoice. Settle and "
                        f"re-book, or have an admin void the invoice first."
                        if inv else "Folio is not open — an extension cannot be posted"))

        # ---- capacity for the EXTRA nights only, per distinct room type ----------------
        # Rooms are assigned at check-in, so a future reservation holds no room_id: type-level
        # capacity is the right check. The row locks close the race with a check-in happening
        # right now. `booked_qty` counts this booking's own items, but only for ranges that
        # overlap [old_co, new_co) — which this stay currently does not.
        need = {}
        for item in booking.booking_items:
            need[item.room_type_id] = need.get(item.room_type_id, 0) + int(item.quantity or 1)
        for rt_id, qty in need.items():
            rt = db.query(RoomType).filter(RoomType.room_type_id == rt_id).first()
            name = rt.name if rt else f"type {rt_id}"
            if _blocked(db, rt_id, old_co, new_co):
                raise HTTPException(status_code=409,
                                    detail=f"{name} is blocked for the extra nights")
            booked = int(availability.booked_qty(db, rt_id, old_co, new_co, lock=True))
            inactive = int(availability.out_of_service_count(db, rt_id))
            free = (rt.total_rooms if rt else 0) - booked - inactive
            if free < qty:
                raise HTTPException(
                    status_code=409,
                    detail=f"No {name} capacity for {old_co:%d-%m-%Y} to {new_co:%d-%m-%Y} "
                           f"— a future reservation needs it")
        for item in booking.booking_items:
            if item.room_id:
                db.query(Room).filter(Room.room_id == item.room_id).with_for_update().first()

        # ---- price the extra nights (server-side, always) ------------------------------
        room_posting.assert_nights_identifiable(db, folio.id)
        preview = room_posting.post_room_nights(
            db, booking, folio, old_co, new_co, user=user,
            posting_reason=room_posting.REASON_EXTEND,
            price_mode=room_posting.PRICE_QUOTE, recompute=False)
        quoted = float(preview["amount"])
        comped = bool(preview.get("comped"))
        applied = quoted if data.applied_amount is None else round(float(data.applied_amount), 2)

        if applied > quoted:
            raise HTTPException(
                status_code=400,
                detail=f"An extension cannot be charged above the quoted ₹{quoted:,.2f} "
                       f"— extra charges belong on the folio")
        if applied != quoted and not data.reason:
            raise HTTPException(status_code=400,
                                detail="Charging something other than the quoted amount needs a reason")
        if applied < quoted and _extend_waive_requires_admin() and user.get("role") != "admin":
            raise HTTPException(status_code=403,
                                detail="Charging less than the quoted amount requires an admin login")
        # Giving the extra nights away entirely is a comp in all but name.
        needs_owner_otp = (applied == 0 and quoted > 0 and data.applied_amount is not None
                           and not comped
                           and app_settings.get_fraud_config(db).get("comp_otp_required"))

        balance_before = float(folio.balance or 0)
        grand_before = float(booking.grand_total or 0)

        if data.dry_run:
            resp = {
                "booking_id": booking.booking_id, "dry_run": True, "already": False,
                "from_check_out": str(old_co), "to_check_out": str(new_co),
                "extra_nights": (new_co - old_co).days,
                "items": preview["per_item"],
                "quoted_amount": quoted, "applied_amount": None,
                "comped": comped,
                "requires_reason": applied != quoted,
                "requires_admin": _extend_waive_requires_admin(),
                # Told to the desk up front so it can show the approval panel BEFORE the
                # receptionist presses Extend, rather than after a rejected submit. (The
                # room-shift flow lacks this and the desk has to fail first to find out.)
                "requires_owner_otp": bool(needs_owner_otp),
                "folio_id": folio.id,
                "folio_balance_before": balance_before,
                "folio_balance_after": round(balance_before + applied, 2),
                "grand_total_before": grand_before,
                "grand_total_after": round(grand_before + applied, 2),
                "posted_charge_ids": [], "superseded_card_ids": [],
                "card_reencode_required": True, "cards": [],
            }
            db.rollback()          # releases the row locks; nothing was mutated
            return resp

        # ---- commit (one transaction) ---------------------------------------------------
        db.rollback()   # drop the preview's uncommitted rows, then post for real
        # Re-lock after the rollback (see the note above on FOR UPDATE + outer joins).
        booking = (db.query(Booking)
                     .filter(Booking.booking_id == data.booking_id).with_for_update().first())
        folio = db.query(Folio).filter(Folio.booking_id == booking.booking_id).first()

        # ⚠️ Consume the owner code AFTER that rollback, never before it. consume_otp only
        # flushes — it joins the caller's transaction so the code is spent only if the action
        # commits — so consuming it during the pricing pass would have been undone by the very
        # rollback that discards the preview rows, leaving a "used" code still reusable.
        # Here it is inside the transaction that actually commits: a bad code still costs
        # nothing, and a good one is spent exactly once.
        if needs_owner_otp:
            consume_otp(db, data.owner_otp_id, data.owner_otp_code, "extend_waive", user)

        result = room_posting.post_room_nights(
            db, booking, folio, old_co, new_co, user=user,
            posting_reason=room_posting.REASON_EXTEND,
            price_mode=room_posting.PRICE_QUOTE,
            override_total=(None if data.applied_amount is None else applied),
            recompute=False)

        # Keep booking + item money in step with the folio. `grand_total` is read by the desk
        # board, the folio list, loyalty accrual, the agent-commission report and the OTA
        # payout snapshot — letting it drift from the folio breaks all of them quietly.
        if applied and not comped:
            per_item_amt = {p["booking_item_id"]: p["amount"] for p in result["per_item"]}
            added_base = 0.0
            for item in booking.booking_items:
                add = float(per_item_amt.get(item.booking_item_id, 0) or 0)
                if not add:
                    continue
                gstp = float(db.query(RoomType.gst_percent).filter(
                    RoomType.room_type_id == item.room_type_id).scalar() or 0)
                base = round(add / (1 + gstp / 100), 2)
                added_base = round(added_base + base, 2)
                item.base_amount = round(float(item.base_amount or 0) + base, 2)
                item.gst_amount = round(float(item.gst_amount or 0) + (add - base), 2)
                item.total_amount = round(float(item.total_amount or 0) + add, 2)
            booking.base_amount = round(float(booking.base_amount or 0) + added_base, 2)
            booking.gst_amount = round(float(booking.gst_amount or 0) + (applied - added_base), 2)
            booking.total_amount = round(float(booking.total_amount or 0) + applied, 2)
            booking.grand_total = round(float(booking.grand_total or 0) + applied, 2)

        # `original_check_out` records what the guest BOOKED. Set once, never overwritten —
        # check_out itself keeps moving, and v4b3's runaway guard reads this.
        if booking.original_check_out is None:
            booking.original_check_out = old_co
        booking.check_out = new_co
        booking.card_reencode_required = True
        # v5m: a night extension supersedes any hourly one — the clock runs from the stay anchor
        # again over the new number of nights.
        booking.checkout_extended_until = None

        cards = db.query(CardIssuance).filter(
            CardIssuance.booking_id == booking.booking_id,
            CardIssuance.status == "active").all()
        for c in cards:
            c.status = "superseded"      # frees the max_cards slot for the re-cut
        superseded_ids = [c.id for c in cards]

        _recompute(db, folio)
        db.commit()

        write_audit(db, user, "reception.extend", "booking", booking.booking_id,
                    before={"check_out": str(old_co), "grand_total": grand_before,
                            "folio_balance": balance_before},
                    after={"check_out": str(new_co), "extra_nights": (new_co - old_co).days,
                           "quoted_amount": quoted, "applied_amount": applied,
                           "comped": comped, "reason": data.reason,
                           "posted_charge_ids": result["posted"],
                           "superseded_card_ids": superseded_ids,
                           "grand_total": float(booking.grand_total or 0),
                           "folio_balance": float(folio.balance or 0),
                           "client_ref": data.client_ref},
                    client="desktop", commit=True)

        # Card payloads are built AFTER check_out moved, so valid_to is the new window.
        # Best-effort by design: the extension is ALREADY COMMITTED at this point, so a room
        # the encoder cannot address (a non-numeric or out-of-range room number) must not turn
        # a successful extension into a 500. The stay stays flagged card_reencode_required, and
        # staff cut the key from the Cards screen instead.
        valid_from, valid_to = _card_window(booking)
        card_payloads = []
        for item in booking.booking_items:
            if not item.room_id:
                continue
            room = db.query(Room).filter(Room.room_id == item.room_id).first()
            # v5n: a key-lock room has a metal key and nothing to encode — skip it, exactly as
            # check-in (_checkin_response) and /extend-hours already do. Without this a mixed
            # card/key multi-room stay showed the desk a card task it could never satisfy.
            if not room or room.lock_type == "key":
                continue
            try:
                card_payloads.append(_encode_payload(db, item, room, valid_from, valid_to))
            except HTTPException as e:
                logger.error(f"extend: no card payload for room {room.room_number} "
                             f"(booking {booking.booking_id}): {e.detail}")

        return {
            "booking_id": booking.booking_id, "dry_run": False, "already": False,
            "from_check_out": str(old_co), "to_check_out": str(new_co),
            "extra_nights": (new_co - old_co).days,
            "items": result["per_item"],
            "quoted_amount": quoted, "applied_amount": applied, "comped": comped,
            "requires_reason": applied != quoted,
            "requires_admin": _extend_waive_requires_admin(),
            "requires_owner_otp": bool(needs_owner_otp),
            "folio_id": folio.id,
            "folio_balance_before": balance_before,
            "folio_balance_after": float(folio.balance or 0),
            "grand_total_before": grand_before,
            "grand_total_after": float(booking.grand_total or 0),
            "posted_charge_ids": result["posted"],
            "superseded_card_ids": superseded_ids,
            "card_reencode_required": True,
            "cards": card_payloads,
        }
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        logger.error(f"extend_stay failed: {e}", exc_info=True)
        db.rollback()
        raise HTTPException(status_code=500, detail="Extend stay failed")


@router.post("/extend-hours")
def extend_hours(data: ExtendHoursRequest, db: Session = Depends(get_db),
                 user=Depends(require_reception_or_admin)):
    """v5m — late checkout by the hour, priced by the admin's hourly-extension rule.

    Sibling of /extend (dry-run then commit, server prices, same card payload shape), but the
    unit is HOURS: `until = current checkout moment + hours`. The rule decides free / fee /
    refuse ("beyond N h counts as a night — extend by a night instead"). Can be requested any
    time during the stay, before or after the checkout moment, until an invoice exists.
    The fee is a folio `room` line at the room GST %, so it prints on the invoice; the event
    is recorded in stay_events for the arrival-exceptions report."""
    try:
        booking = (db.query(Booking)
                     .filter(Booking.booking_id == data.booking_id)
                     .with_for_update().first())
        if not booking:
            raise HTTPException(status_code=404, detail="Booking not found")
        if booking.status != "checked_in":
            raise HTTPException(
                status_code=409,
                detail=f"Only an in-house stay can be extended (this one is '{booking.status}')")
        folio = db.query(Folio).filter(Folio.booking_id == booking.booking_id).first()
        if not folio:
            raise HTTPException(status_code=409, detail="No folio for this stay — open it first")
        inv = _get_invoice(db, folio.id)
        if folio.status != "open" or inv:
            raise HTTPException(
                status_code=409,
                detail=(f"Invoice {inv.invoice_no} has already been raised for this stay — "
                        f"an extension cannot be added to an issued tax invoice."
                        if inv else "Folio is not open — an extension cannot be posted"))

        current = booking_checkout_moment(booking)
        # Replay guard: the desk sends the `until` it previewed; if the stay already ends there
        # (or later) this is a re-flush, not a second extension.
        if data.until and booking.checkout_extended_until and booking.checkout_extended_until >= data.until:
            return {"booking_id": booking.booking_id, "dry_run": data.dry_run, "already": True,
                    "from": current.isoformat(), "until": booking.checkout_extended_until.isoformat(),
                    "hours": 0, "quoted_amount": 0.0, "applied_amount": 0.0, "refused": None,
                    "folio_id": folio.id, "folio_balance_before": float(folio.balance or 0),
                    "folio_balance_after": float(folio.balance or 0), "posted_charge_ids": [],
                    "superseded_card_ids": [], "card_reencode_required": bool(booking.card_reencode_required),
                    "cards": [], "requires_owner_otp": False, "requires_reason": False}

        rules = arrival_rules.load_rules(db)
        comp = room_posting._is_comped(booking)
        # v5n: only the rooms staying late are charged (the desk ticks them); the percent base and the
        # fixed multiplier both follow that selection.
        selection = _fee_selection(db, booking, data.room_ids)
        charged_items = [item for item, _rid in selection]
        rooms_charged = len(selection)
        total_rooms = sum(max(1, int(i.quantity or 1)) for i in booking.booking_items) or 1
        rate = _night_rate(db, booking, selection)
        ev = arrival_rules.evaluate_extension(rules, booking.booking_source or "direct", current,
                                              data.hours, rate, comp, rooms=rooms_charged)
        if ev["refused"]:
            raise HTTPException(status_code=400, detail=f"Cannot extend by {data.hours} h: {ev['refused']}")
        until = ev["until"]
        quoted = float(ev["charge"])
        applied = quoted if data.applied_amount is None else round(float(data.applied_amount), 2)
        changed = data.applied_amount is not None and applied != quoted
        if changed and not data.dry_run and not (data.reason or "").strip():
            raise HTTPException(status_code=400,
                                detail="Charging something other than the rule's fee needs a reason")
        needs_approval = (not ev["exempt"]) and arrival_rules.needs_approval(
            ev["rule"], quoted, data.applied_amount)
        is_admin = user.get("role") == "admin"
        balance_before = float(folio.balance or 0)
        rooms_note = ("" if rooms_charged >= total_rooms
                      else f" · {rooms_charged} of {total_rooms} rooms")
        text = (f"Late checkout +{data.hours} h (until {until:%H:%M %d %b}){rooms_note}"
                + (" — complimentary" if ev["exempt"] else
                   (" — free" if quoted == 0 else f" — ₹{quoted:,.0f}")))

        if data.dry_run:
            db.rollback()
            return {"booking_id": booking.booking_id, "dry_run": True, "already": False,
                    "from": current.isoformat(), "until": until.isoformat(), "hours": data.hours,
                    "quoted_amount": quoted, "applied_amount": None, "basis": ev["basis"],
                    "exempt": ev["exempt"], "text": text, "refused": None,
                    "rooms_charged": rooms_charged, "total_rooms": total_rooms,
                    "charged_room_ids": [rid for _i, rid in selection if rid],
                    "rule": {k: v for k, v in ev["rule"].items() if k != "sources"},
                    "requires_reason": changed, "requires_owner_otp": bool(needs_approval and not is_admin),
                    "requires_admin": False,
                    "folio_id": folio.id, "folio_balance_before": balance_before,
                    "folio_balance_after": round(balance_before + applied, 2),
                    "posted_charge_ids": [], "superseded_card_ids": [],
                    "card_reencode_required": True, "cards": []}

        approval = "none"
        if needs_approval:
            if is_admin:
                approval = "admin"
            else:
                consume_otp(db, data.owner_otp_id, data.owner_otp_code, "extend_hours", user)
                approval = "owner_otp"

        # v5n: one fee line and one event PER ROOM staying late, each at that room's own GST slab.
        uid = _resolve_user_id(db, user)
        sel_rooms = [db.query(Room).filter(Room.room_id == rid).first() if rid else None
                     for _item, rid in selection]
        charge_id = None
        events = []
        if applied > 0 and not ev["exempt"]:
            n = max(1, len(selection))
            per = round(applied / n, 2)
            amounts = [per] * (n - 1) + [round(applied - per * (n - 1), 2)]
            for (item, _rid), room, amount in zip(selection, sel_rooms, amounts):
                label = f" — Room {room.room_number}" if room else ""
                charge = FolioCharge(
                    folio_id=folio.id, type="room",
                    description=f"Late checkout +{data.hours} h{label} (until {until:%H:%M %d %b})",
                    qty=1, unit_price=amount, amount=amount,
                    gst_percent=_item_gst_percent(db, item),
                    posted_by=uid, charge_date=current.date(),
                    posting_reason="extend_hours", room_id=room.room_id if room else None)
                db.add(charge)
                db.flush()
                charge_id = charge_id or charge.id
                events.append((room, charge.id, amount))
            booking.grand_total = round(float(booking.grand_total or 0) + applied, 2)
            booking.total_amount = round(float(booking.total_amount or 0) + applied, 2)
        if not events:
            events = [(next((r for r in sel_rooms if r), None), None, 0.0)]
        snapshot = arrival_rules.rule_snapshot({**ev["rule"], "quoted": quoted, "reason": data.reason,
                                                "rooms_charged": rooms_charged,
                                                "total_rooms": total_rooms})
        for room, cid, amount in events:
            db.add(StayEvent(
                booking_id=booking.booking_id, kind="hourly_extension",
                room_id=room.room_id if room else None,
                expected_at=current, actual_at=until, deviation_minutes=data.hours * 60,
                hours=data.hours,
                charge_amount=amount if not ev["exempt"] else 0,
                charge_basis="override" if changed else ev["basis"],
                rule_json=snapshot, approval=approval,
                approved_by=uid if approval != "none" else None,
                folio_charge_id=cid, created_by=uid))

        booking.checkout_extended_until = until
        booking.card_reencode_required = True
        cards = db.query(CardIssuance).filter(
            CardIssuance.booking_id == booking.booking_id,
            CardIssuance.status == "active").all()
        for c in cards:
            c.status = "superseded"
        superseded_ids = [c.id for c in cards]
        _recompute(db, folio)
        db.commit()

        write_audit(db, user, "reception.extend_hours", "booking", booking.booking_id,
                    before={"checkout_at": current.isoformat(), "folio_balance": balance_before},
                    after={"until": until.isoformat(), "hours": data.hours, "quoted_amount": quoted,
                           "applied_amount": applied, "basis": ev["basis"], "approval": approval,
                           "rooms_charged": rooms_charged, "total_rooms": total_rooms,
                           "reason": data.reason, "folio_charge_id": charge_id,
                           "superseded_card_ids": superseded_ids,
                           "folio_balance": float(folio.balance or 0), "client_ref": data.client_ref},
                    client="desktop", commit=True)

        valid_from, valid_to = _card_window(booking)
        card_payloads = []
        for item in booking.booking_items:
            if not item.room_id:
                continue
            room = db.query(Room).filter(Room.room_id == item.room_id).first()
            if room and room.lock_type != "key":
                try:
                    card_payloads.append(_encode_payload(db, item, room, valid_from, valid_to))
                except HTTPException as e:
                    logger.error(f"extend-hours: no card payload for room {room.room_number}: {e.detail}")

        return {"booking_id": booking.booking_id, "dry_run": False, "already": False,
                "from": current.isoformat(), "until": until.isoformat(), "hours": data.hours,
                "quoted_amount": quoted, "applied_amount": applied, "basis": ev["basis"],
                "exempt": ev["exempt"], "text": text, "refused": None,
                "rooms_charged": rooms_charged, "total_rooms": total_rooms,
                "requires_reason": changed, "requires_owner_otp": False, "requires_admin": False,
                "folio_id": folio.id, "folio_balance_before": balance_before,
                "folio_balance_after": float(folio.balance or 0),
                "posted_charge_ids": [charge_id] if charge_id else [],
                "superseded_card_ids": superseded_ids,
                "card_reencode_required": True, "cards": card_payloads}
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        logger.error(f"extend_hours failed: {e}", exc_info=True)
        db.rollback()
        raise HTTPException(status_code=500, detail="Hourly extension failed")


COMP_VOID_PREFIX = "Complimentary"


def _comp_waivable(charge, mode: str) -> bool:
    """Which existing folio lines a comp waives. `room` waives the room nights only; `all`
    waives every charge. Payment credits are NEVER touched — an advance was really paid, and
    making a stay free does not un-take the guest's money."""
    if charge.type in ("payment", "discount"):
        return False
    return charge.type == "room" if mode == "room" else True


def _comp_apply(db, booking, folio, mode: str, reason: str, user) -> list:
    """Waive the charges already on the folio (a comp set AFTER check-in).

    ⚠️ This voids rather than deletes, so the folio shows the original lines struck through
    with their reversals — NOT an empty bill. There is no way to have both an audit trail and
    a literally empty folio for a retro-comp, and `folio.py` is built on "void never hard-
    deletes". The reversals are `void=True`, so they are excluded from `_active_charges`,
    `_invoice_totals` and every report; only the folio detail screen shows them.
    """
    waived = []
    for c in _active_charges(db, folio.id):
        if _comp_waivable(c, mode):
            _void_charge_row(db, c, f"{COMP_VOID_PREFIX} — {reason}", user)
            waived.append(c.id)
    return waived


def _comp_restore(db, booking, folio, user) -> list:
    """Un-waive what a comp waived (clearing a comp).

    ⚠️ Reinstates the ORIGINAL lines rather than posting new ones. The v4b1 unique index keeps
    a voided night's (booking_item, date) slot occupied on purpose — that is what stops the
    overstay sweep re-billing a reversed night — so a fresh post would collide. Un-voiding is
    also the truthful record: the charge always existed and was waived for a while.
    The reversal rows stay as history; they are `void=True` so they affect no total.
    """
    restored = []
    rows = (db.query(FolioCharge)
              .filter(FolioCharge.folio_id == folio.id,
                      FolioCharge.void == True,                      # noqa: E712
                      FolioCharge.reversal_of_id.is_(None),
                      FolioCharge.void_reason.ilike(f"{COMP_VOID_PREFIX}%"))
              .all())
    for c in rows:
        c.void = False
        c.void_reason = None
        restored.append(c.id)
    return restored


@router.post("/bookings/{booking_id}/complimentary")
def set_complimentary(booking_id: int, data: ComplimentaryRequest,
                      db: Session = Depends(get_db), user=Depends(require_reception_or_admin)):
    """Make a stay complimentary — or take that away again (v4b6).

    `all` = room rent AND every folio extra are free. `room` = room rent free, extras billed.
    Charges are **suppressed**, not discounted: posting a taxable line and discounting it 100%
    would create an output-tax liability on a supply with no consideration, so the GST reports
    would be wrong. The cost is that ADR/RevPAR understate — `comp_room_value` on the sales
    report is what answers "how much did we give away?".

    ⚠️ **Clearing needs the owner's code too**, not just setting. Un-gated clearing would let
    someone comp a stay, post charges against the free flag, then un-comp — laundering a
    discount through a route with no floor and no approval.
    """
    try:
        booking = (db.query(Booking).filter(Booking.booking_id == booking_id)
                     .with_for_update().first())
        if not booking:
            raise HTTPException(status_code=404, detail="Booking not found")
        if booking.status not in ("confirmed", "checked_in"):
            raise HTTPException(
                status_code=409,
                detail=f"A '{booking.status}' stay cannot be made complimentary — the bill is "
                       f"closed. Refund or credit it instead.")

        folio = db.query(Folio).filter(Folio.booking_id == booking.booking_id).first()
        invoice = _get_invoice(db, folio.id) if folio else None
        if invoice:
            raise HTTPException(
                status_code=409,
                detail=f"Invoice {invoice.invoice_no} has already been raised for this stay — "
                       f"a tax invoice cannot be retro-comped.")

        old_mode = getattr(booking, "comp_mode", "none") or "none"
        if data.mode == old_mode:
            raise HTTPException(status_code=409, detail=f"This stay is already '{old_mode}'")

        if app_settings.get_fraud_config(db).get("comp_otp_required"):
            consume_otp(db, data.owner_otp_id, data.owner_otp_code, "booking_complimentary", user)

        before = {"comp_mode": old_mode,
                  "folio_balance": float(folio.balance or 0) if folio else None}
        waived, restored = [], []

        if data.mode == "none":
            if folio:
                restored = _comp_restore(db, booking, folio, user)
            booking.comp_mode = "none"
            booking.comp_reason = None
            booking.comp_by = None
            booking.comp_at = None
            booking.comp_otp_id = None
            # A comp set BEFORE check-in suppressed the nights entirely, so there may be
            # nothing to restore — post whatever is missing. post_room_nights skips the slots
            # that already exist (including the ones just un-voided).
            if folio and booking.status == "checked_in":
                try:
                    room_posting.post_room_nights(
                        db, booking, folio, booking.check_in, booking.check_out, user=user,
                        posting_reason=room_posting.REASON_CHECKIN,
                        price_mode=room_posting.PRICE_BOOKING_SPLIT, recompute=False)
                except Exception as e:
                    logger.warning(f"comp clear: re-posting nights failed for {booking_id}: {e}")
        else:
            booking.comp_mode = data.mode
            booking.comp_reason = data.reason
            booking.comp_by = _resolve_user_id(db, user)
            booking.comp_at = datetime.now()
            booking.comp_otp_id = data.owner_otp_id
            if folio:
                waived = _comp_apply(db, booking, folio, data.mode, data.reason, user)

        advance_to_refund = 0.0
        if folio:
            _recompute(db, folio)
            # A comped stay that took an advance now has a NEGATIVE balance, and checkout
            # correctly refuses to settle an overpayment. Tell the desk now, while the guest
            # is still reachable, rather than letting it surface at checkout.
            bal = float(folio.balance or 0)
            advance_to_refund = round(-bal, 2) if bal < 0 else 0.0
        db.commit()

        write_audit(db, user, "reception.complimentary", "booking", booking.booking_id,
                    before=before,
                    after={"comp_mode": booking.comp_mode, "reason": data.reason,
                           "waived_charge_ids": waived, "restored_charge_ids": restored,
                           "otp_id": data.owner_otp_id,
                           "grand_total": float(booking.grand_total or 0),
                           "folio_balance": float(folio.balance or 0) if folio else None,
                           "advance_to_refund": advance_to_refund},
                    client="desktop", commit=True)

        return {"booking_id": booking.booking_id,
                "comp_mode": booking.comp_mode,
                "reason": booking.comp_reason,
                "waived_charges": len(waived),
                "restored_charges": len(restored),
                "folio_id": folio.id if folio else None,
                "folio_balance": float(folio.balance or 0) if folio else None,
                "grand_total": float(booking.grand_total or 0),
                "advance_to_refund": advance_to_refund}
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        logger.error(f"set_complimentary failed: {e}", exc_info=True)
        db.rollback()
        raise HTTPException(status_code=500, detail="Could not change the complimentary status")


def _free_rooms_of_type(db, room_type_id: int, exclude_room_ids=()) -> list:
    """Physically vacant, active, unheld rooms of a given type — right now."""
    rows = (db.query(Room)
              .filter(Room.room_type_id == room_type_id,
                      Room.is_active == True,               # noqa: E712
                      Room.status == "vacant")
              .order_by(*ordering.room_number_key(Room.room_number)).all())
    out = []
    for r in rows:
        if r.room_id in exclude_room_ids:
            continue
        held = (db.query(BookingItem).join(Booking)
                  .filter(BookingItem.room_id == r.room_id,
                          Booking.status == "checked_in").first())
        if not held:
            out.append(r)
    return out


def _assert_booked_type_sold_out(db, item, room):
    """⛔ An alternate-type sale is only allowed when the BOOKED type has genuinely run out.

    The owner was explicit: Room 47 may be let as a Triple Non-A/C, but only when there is no
    actual Triple Non-A/C left. This is checked BEFORE any approval is requested, so an owner
    code is never even created for a swap made out of convenience — there is nothing to
    rubber-stamp, and no habit of asking forms.

    It also bounds the known availability drift: an alternate sale can now only happen on a
    date when that type was fully booked anyway.
    """
    free = _free_rooms_of_type(db, item.room_type_id)
    if free:
        names = ", ".join(r.room_number for r in free[:5])
        booked_name = item.room_type.name if item.room_type else f"type {item.room_type_id}"
        raise HTTPException(
            status_code=409,
            detail=(f"Room {room.room_number} is not a {booked_name}, and you do not need it — "
                    f"{booked_name} {'rooms' if len(free) > 1 else 'room'} {names} "
                    f"{'are' if len(free) > 1 else 'is'} free. Assign one of those. Selling a "
                    f"room as its alternate type is only for when the booked type has run out."))


def _alt_sale_context(db, booking, item, room) -> dict:
    """The action-specific half of the owner's approval text for an alternate-type sale.
    The point is that the giveaway is impossible to miss on a phone screen."""
    booked = item.room_type or db.query(RoomType).filter(
        RoomType.room_type_id == item.room_type_id).first()
    physical = db.query(RoomType).filter(
        RoomType.room_type_id == room.room_type_id).first()
    nights = max(1, (booking.check_out - booking.check_in).days)
    b_rate = float(booked.price_per_night or 0) if booked else 0.0
    p_rate = float(physical.price_per_night or 0) if physical else 0.0
    delta = round(p_rate - b_rate, 2)
    return {
        "booked_type": {"room_type_id": item.room_type_id,
                        "name": booked.name if booked else None, "rate_per_night": b_rate},
        "physical_type": {"room_type_id": room.room_type_id,
                          "name": physical.name if physical else None, "rate_per_night": p_rate},
        "delta_per_night": delta,
        "nights": nights,
        "total_delta": round(delta * nights, 2),
    }


def _overstay_fields(db, booking, cfg=None) -> dict:
    """The overstay slice of a board row. Best-effort: a board that fails to load because the
    overstay read-model hiccuped would take the whole front desk down with it."""
    try:
        from services.overstay_billing import overstay_state
        st = overstay_state(db, booking, cfg=cfg)
        return {
            "overdue": st["overdue"],
            "overdue_since": st["due_at"] if st["overdue"] else None,
            "nights_overdue": st["nights_overdue"],
            "auto_charged_nights": st["auto_nights"],
            "auto_charged_amount": st["auto_amount"],
            "card_reencode_required": st["card_reencode_required"],
            "original_check_out": st["original_check_out"],
        }
    except Exception as e:                                    # pragma: no cover - defensive
        logger.warning(f"overstay fields for booking {booking.booking_id} failed: {e}")
        return {}


@router.post("/overstay/{booking_id}/reverse")
def reverse_overstay(booking_id: int, data: ReverseOverstayRequest,
                     db: Session = Depends(get_db), user=Depends(require_reception_or_admin)):
    """Undo an automatically-charged overstay night (v4b3).

    Reception **or** admin, gated by owner approval: the whole point of the OTP is that the
    desk requests it and the OWNER authorises the reversal with the code, so a receptionist
    can run this without an admin login but never alone. Deliberately its own route rather than a gated folio void —
    reversing a night has to void the line, move `check_out` back, decrement `grand_total`
    and re-flag the card, none of which a generic void can express.
    """
    try:
        booking = (db.query(Booking).filter(Booking.booking_id == booking_id)
                     .with_for_update().first())
        if not booking:
            raise HTTPException(status_code=404, detail="Booking not found")
        if booking.status != "checked_in":
            raise HTTPException(status_code=409,
                                detail="Only an in-house stay's overstay charge can be reversed")

        folio = db.query(Folio).filter(Folio.booking_id == booking.booking_id).first()
        if not folio:
            raise HTTPException(status_code=409, detail="No folio for this stay")
        _ensure_editable_folio(db, folio)

        # Only the TRAILING night. Reversing a middle one would hole the contiguous-nights
        # invariant that makes the whole posting spine idempotent.
        last_night = booking.check_out - timedelta(days=1)
        if data.night_date != last_night:
            raise HTTPException(
                status_code=409,
                detail=f"Only the most recent auto-charged night can be reversed. "
                       f"Reverse {last_night:%d %b %Y} first.")

        lines = (db.query(FolioCharge)
                   .filter(FolioCharge.folio_id == folio.id,
                           FolioCharge.type == "room",
                           FolioCharge.charge_date == data.night_date,
                           FolioCharge.reversal_of_id.is_(None),
                           FolioCharge.void == False)  # noqa: E712
                   .all())
        if not lines:
            raise HTTPException(status_code=404,
                                detail=f"No room charge found for {data.night_date:%d %b %Y}")
        # This route never touches a night the guest actually booked.
        if any(l.posting_reason != room_posting.REASON_OVERSTAY for l in lines):
            raise HTTPException(
                status_code=409,
                detail=f"{data.night_date:%d %b %Y} is a booked night, not an automatic "
                       f"overstay charge — it cannot be reversed here.")

        if app_settings.get_fraud_config(db).get("overstay_reverse_otp_required"):
            consume_otp(db, data.owner_otp_id, data.owner_otp_code, "overstay_reverse", user)

        before = {"check_out": str(booking.check_out),
                  "grand_total": float(booking.grand_total or 0),
                  "folio_balance": float(folio.balance or 0)}

        reversed_amount, reversal_ids = 0.0, []
        for line in lines:
            rev = _void_charge_row(db, line, f"Overstay charge reversed — {data.reason}", user)
            reversed_amount = round(reversed_amount + float(line.amount or 0), 2)
            reversal_ids.append(rev)
            # keep the booking item in step with the folio
            item = db.query(BookingItem).filter(
                BookingItem.booking_item_id == line.booking_item_id).first()
            if item:
                amt = float(line.amount or 0)
                gstp = float(db.query(RoomType.gst_percent).filter(
                    RoomType.room_type_id == item.room_type_id).scalar() or 0)
                base = round(amt / (1 + gstp / 100), 2)
                item.base_amount = round(float(item.base_amount or 0) - base, 2)
                item.gst_amount = round(float(item.gst_amount or 0) - (amt - base), 2)
                item.total_amount = round(float(item.total_amount or 0) - amt, 2)

        booking.check_out = data.night_date
        booking.total_amount = round(float(booking.total_amount or 0) - reversed_amount, 2)
        booking.grand_total = round(float(booking.grand_total or 0) - reversed_amount, 2)
        # The window just got SHORTER, so the guest's card now outlives their stay. That is
        # the direction that actually matters for security.
        booking.card_reencode_required = True

        _recompute(db, folio)
        db.commit()
        db.refresh(folio)

        write_audit(db, user, "reception.overstay_reverse", "booking", booking.booking_id,
                    before=before,
                    after={"check_out": str(booking.check_out),
                           "night_date": str(data.night_date),
                           "amount_reversed": reversed_amount,
                           "charge_ids": [l.id for l in lines],
                           "reversal_ids": [r.id for r in reversal_ids],
                           "otp_id": data.owner_otp_id, "reason": data.reason,
                           "grand_total": float(booking.grand_total or 0),
                           "folio_balance": float(folio.balance or 0)},
                    client="desktop", commit=True)

        return {"booking_id": booking.booking_id,
                "night_date": str(data.night_date),
                "amount_reversed": reversed_amount,
                "check_out": str(booking.check_out),
                "folio_balance": float(folio.balance or 0),
                "grand_total": float(booking.grand_total or 0),
                "card_reencode_required": True}
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        logger.error(f"reverse_overstay failed: {e}", exc_info=True)
        db.rollback()
        raise HTTPException(status_code=500, detail="Reversing the overstay charge failed")


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
                "company_name": _company_names.get(b.company_id),
                # v4b6: so the desk can chip a free stay before someone asks for money.
                "comp_mode": getattr(b, "comp_mode", "none") or "none",
                "comp_reason": getattr(b, "comp_reason", None)}

    # v4b9 (R9): money the guest already paid the CHANNEL (v4b1). Deliberately not a `Payment`
    # row — the hotel never received it — so `paid_total` alone reads as "owes the whole room
    # rate" on every website/OTA arrival, which is how a guest gets asked to pay twice. `paid`
    # is passed in because the row already summed it; the rule itself lives in payments.py.

    # v5m: arrivals now include a late guest up to and including the check-out date (no re-dating
    # at the desk; the arrival rules decide the stay clock), each row carrying the server-side
    # evaluation the wizard shows. `upcoming` lists the next 7 days for early check-ins.
    _rules = arrival_rules.load_rules(db)
    _now = datetime.now()

    def _arrival_row(b):
        paid, folio_id, folio_balance = _paid_and_folio(b.booking_id)
        try:
            ev = arrival_evaluation(db, b, _now, rules=_rules)
            preview = _arrival_public(ev)
        except Exception as e:  # never let a rules problem hide an arrival
            logger.warning(f"arrival preview failed for booking {b.booking_id}: {e}")
            preview = None
        exp = expected_arrival(b, db)
        return {
            "booking_id": b.booking_id,
            "guest_id": b.guest_id,
            "guest_name": b.guest.name if b.guest else None,
            "phone": b.guest.phone if b.guest else None,
            # Website bookings carry a real email; surfaced so the desk pre-fills it at check-in.
            "email": b.guest.email if b.guest else None,
            "check_in": str(b.check_in),
            "check_in_time": str(b.check_in_time) if b.check_in_time else None,
            "check_out": str(b.check_out),
            "booking_source": b.booking_source,
            # v5r: the desk needs the channel reference to recognise (and refuse) the placeholder phone
            # that was stamped from it at check-in.
            "ota_booking_id": b.ota_booking_id,
            "grand_total": float(b.grand_total or 0),
            "paid_total": paid,
            "folio_id": folio_id,
            "folio_balance": folio_balance,
            "adults": int(b.adults or 1),
            "children": int(b.children or 0),
            "expected_arrival_at": exp.isoformat(),
            "is_late": bool(preview and preview["kind"] == "late"),
            "is_early": bool(preview and preview["kind"] == "early"),
            "lapsed": (preview or {}).get("lapsed"),
            "arrival_preview": preview,
            **prepaid_slice(db, b, paid=paid),
            **_bill_to(b),
            "items": [{
                "booking_item_id": i.booking_item_id,
                "room_type_id": i.room_type_id,
                "room_type_name": i.room_type.name if i.room_type else None,
                "quantity": i.quantity,
            } for i in b.booking_items],
        }

    _arrival_q = db.query(Booking).options(
        joinedload(Booking.guest),
        joinedload(Booking.booking_items).joinedload(BookingItem.room_type),
    ).filter(Booking.status == "confirmed")
    arrivals = [_arrival_row(b) for b in _arrival_q.filter(
        Booking.check_in <= today, Booking.check_out >= today,
    ).order_by(Booking.check_in, Booking.check_in_time, Booking.booking_id).all()]
    upcoming = [_arrival_row(b) for b in _arrival_q.filter(
        Booking.check_in > today, Booking.check_in <= today + timedelta(days=7),
    ).order_by(Booking.check_in, Booking.check_in_time, Booking.booking_id).all()]

    # F-16: the fourth bucket — a reservation nobody ever arrived for, whose window has now closed.
    # It was in NO bucket: `arrivals` requires check_out >= today, `upcoming` requires a future
    # check_in, `inhouse` requires checked_in. So it simply disappeared from the desk while staying
    # `confirmed` in the database and still consuming availability on its dates. The nightly no-show
    # sweep is the only thing that would ever touch it, and that ignores anything before
    # `no_show_from_date`, so a stay that lapses quietly stays confirmed for ever. For an OTA booking
    # that is money: no no-show, no commission dispute, no record.
    # Capped and newest-first — this is a to-do list, not an archive. The row carries the same shape
    # as an arrival so the desk can reuse the card, and both actions it needs (mark no-show, re-date)
    # already exist in routers/booking_lifecycle.py.
    missed = [_arrival_row(b) for b in _arrival_q.filter(
        Booking.check_out < today,
    ).order_by(Booking.check_out.desc(), Booking.booking_id.desc()).limit(50).all()]

    # Read the overstay config ONCE for the whole board rather than per row.
    ov_cfg = app_settings.get_overstay_config(db)
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
                        "lock_type": room.lock_type,   # key rooms skip card read/erase at checkout
                    })
        # v5s: a stay's own rooms read ascending ("9, 10", never "10, 9").
        rooms.sort(key=lambda r: ordering.room_number_sort_key(r["room_number"]))
        inhouse.append({
            "booking_id": b.booking_id,
            "guest_id": b.guest_id,
            "guest_name": b.guest.name if b.guest else None,
            "phone": b.guest.phone if b.guest else None,
            # Website bookings carry a real email; surfaced so the desk pre-fills it at check-in.
            "email": b.guest.email if b.guest else None,
            "check_in": str(b.check_in),
            "check_out": str(b.check_out),
            # v4b9 R13: the in-house row carried no source, so the desk could tell where an
            # ARRIVING guest came from but not an in-house one — and the two boards are read by
            # the same person minutes apart.
            "booking_source": b.booking_source,
            # Expected arrival vs what actually happened (FE-1) — both on the row so the
            # in-house board can show a late/early arrival at a glance.
            "check_in_time": str(b.check_in_time) if b.check_in_time else None,
            "checked_in_at": b.checked_in_at.isoformat() if b.checked_in_at else None,
            # The expected check-out MOMENT (not just the date) so the desk can show "out 12:00" beside
            # the "in HH:mm" arrival time. Same source-aware anchor as card expiry / overstay billing.
            "expected_check_out": booking_checkout_moment(b).isoformat(),
            # v5m: an hourly extension in force (the moment above already follows it).
            "checkout_extended_until": b.checkout_extended_until.isoformat() if b.checkout_extended_until else None,
            "stay_started_at": b.stay_started_at.isoformat() if b.stay_started_at else None,
            "grand_total": float(b.grand_total or 0),
            "paid_total": paid,
            "folio_id": folio_id,
            "folio_balance": folio_balance,
            "valid_from": valid_from.isoformat(),
            "valid_to": valid_to.isoformat(),
            # v4b3: overstay state from the SHARED read-model, so the board, the owner
            # dashboard and the automatic biller can never disagree about who is overdue.
            # Additive — the desk still derives its own overdue flag from the dates, and that
            # keeps working.
            **_overstay_fields(db, b, ov_cfg),
            **prepaid_slice(db, b, paid=paid),
            **_bill_to(b),
            "rooms": rooms,
        })

    _rt_names = dict(db.query(RoomType.room_type_id, RoomType.name).all())
    vacant_rooms = [{
        "room_id": r.room_id,
        "room_number": r.room_number,
        "room_type_id": r.room_type_id,
        "room_type_name": rt.name if rt else None,          # shift target picker (prompt 09)
        "price_per_night": float(rt.price_per_night) if rt else None,
        # v4b7: the second type this room may be sold as, so the picker can offer it for a
        # booking of THAT type — and say that doing so needs the owner's approval.
        "alt_room_type_id": r.alt_room_type_id,
        "alt_room_type_name": _rt_names.get(r.alt_room_type_id),
        "building": r.building,
        "floor": r.floor,
        "max_cards": r.max_cards,
        "lock_type": r.lock_type,
    } for r, rt in db.query(Room, RoomType).outerjoin(
        RoomType, Room.room_type_id == RoomType.room_type_id,
    ).filter(
        Room.is_active == True,  # noqa: E712
        Room.status == "vacant",
    ).order_by(*ordering.room_number_key(Room.room_number)).all()]

    # prompt 13: every active room + its housekeeping status, for the desktop's
    # READ-ONLY housekeeping panel (cleaning/dirty rooms are in neither vacant_rooms
    # nor inhouse, so they need their own list). Reception cannot edit these.
    rooms_hk = []
    for r, hk in db.query(Room, HousekeepingStatus).outerjoin(
        HousekeepingStatus, Room.room_id == HousekeepingStatus.room_id,
    ).filter(Room.is_active == True).order_by(  # noqa: E712
            *ordering.room_number_key(Room.room_number)).all():
        rooms_hk.append({
            "room_id": r.room_id,
            "room_number": r.room_number,
            "room_status": r.status,
            "housekeeping_status": hk.status if hk else None,
            "updated_at": hk.updated_at.isoformat() if hk and hk.updated_at else None,
            # cleaning-card controls on the desk housekeeping board (card-lock rooms only)
            "lock_type": r.lock_type,
            "building": r.building,
            "floor": r.floor,
            "cleaning_card": cleaning_card_state(db, r),
        })

    # v5s, owner's explicit choice: the In-house board reads by ROOM NUMBER (Arrivals and
    # Departures stay on time order — an arrivals board sorted by room is unusable at the desk, and
    # an arrival has no room assigned until the wizard gives it one).
    inhouse.sort(key=lambda r: ordering.room_number_sort_key(
        (r["rooms"][0]["room_number"] if r.get("rooms") else "")))
    return {"date": str(today), "arrivals": arrivals, "upcoming": upcoming, "inhouse": inhouse,
            "missed": missed,
            "vacant_rooms": vacant_rooms, "rooms": rooms_hk,
            # v5n: the front-desk policy the wizard must enforce (how many IDs, scans or not). Sent
            # with the board rather than as a separate call so the desk always has it, keeps it in the
            # offline board cache, and can never drift from what the server will accept.
            "policy": app_settings.get_frontdesk_config(db)}


@router.get("/notifications")
def desk_notifications(db: Session = Depends(get_db), user=Depends(require_reception_or_admin)):
    """Lightweight aggregate the desk polls app-wide to raise sound + toast alerts. For each
    category it returns a count + the newest id, so the desk fires a notification only when the id
    advances past what it last saw."""
    from models import MaintenanceTicket, GuestRequest, WhatsAppMessage, WhatsAppConversationRead

    _OPEN_TICKET = MaintenanceTicket.status.notin_(("resolved", "verified", "closed"))
    tickets_open = int(db.query(func.count(MaintenanceTicket.id))
                       .filter(MaintenanceTicket.source == "guest", _OPEN_TICKET).scalar() or 0)
    tickets_latest = int(db.query(func.coalesce(func.max(MaintenanceTicket.id), 0))
                         .filter(MaintenanceTicket.source == "guest", _OPEN_TICKET).scalar() or 0)

    _OPEN_REQ = GuestRequest.status.in_(("requested", "acknowledged"))
    rs_open = int(db.query(func.count(GuestRequest.id))
                  .filter(GuestRequest.type == "room_service", _OPEN_REQ).scalar() or 0)
    rs_latest = int(db.query(func.coalesce(func.max(GuestRequest.id), 0))
                    .filter(GuestRequest.type == "room_service", _OPEN_REQ).scalar() or 0)
    pr_open = int(db.query(func.count(GuestRequest.id))
                  .filter(GuestRequest.type != "room_service", _OPEN_REQ).scalar() or 0)
    pr_latest = int(db.query(func.coalesce(func.max(GuestRequest.id), 0))
                    .filter(GuestRequest.type != "room_service", _OPEN_REQ).scalar() or 0)

    # Room-service orders still awaiting the kitchen docket — the desk chimes continuously while this
    # is > 0 and stops the moment the KOT is printed (printed_kot_at set). Mirrors the web board's rule.
    rs_awaiting_kot = int(db.query(func.count(GuestRequest.id))
                          .filter(GuestRequest.type == "room_service",
                                  GuestRequest.printed_kot_at.is_(None),
                                  GuestRequest.status.notin_(("completed", "dismissed"))).scalar() or 0)

    # WhatsApp unread across conversations (same rule as /whatsapp/unread-count).
    wa_unread = int(db.query(func.count(WhatsAppMessage.id))
                    .outerjoin(WhatsAppConversationRead,
                               WhatsAppConversationRead.phone == WhatsAppMessage.to_number)
                    .filter(WhatsAppMessage.direction == "in",
                            WhatsAppMessage.id > func.coalesce(WhatsAppConversationRead.last_read_message_id, 0))
                    .scalar() or 0)

    # EXTEND requests (tagged inbound rows) in the last 48h.
    ext_cut = datetime.now() - timedelta(hours=48)
    ext_latest = int(db.query(func.coalesce(func.max(WhatsAppMessage.id), 0))
                     .filter(WhatsAppMessage.template == "extend_request",
                             WhatsAppMessage.created_at >= ext_cut).scalar() or 0)
    ext_count = int(db.query(func.count(WhatsAppMessage.id))
                    .filter(WhatsAppMessage.template == "extend_request",
                            WhatsAppMessage.created_at >= ext_cut).scalar() or 0)

    return {
        "guest_tickets": {"open": tickets_open, "latest_id": tickets_latest},
        "whatsapp_unread": wa_unread,
        "room_service": {"open": rs_open, "latest_id": rs_latest},
        "rs_awaiting_kot": rs_awaiting_kot,
        "portal_requests": {"open": pr_open, "latest_id": pr_latest},
        "extend": {"recent": ext_count, "latest_id": ext_latest},
    }


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
        "room_id": g.room_id, "phone": g.phone,          # v5n: which room, and its own contact
        "has_scan": bool(g.id_scan_ref),
        "has_scan_back": bool(g.id_scan_back_ref),
        # Archived = pulled into the offline weekly archive and deleted from object storage.
        # The UI shows "Archived (offline)" instead of a viewer button that would only 410.
        "scan_archived": bool(g.id_scan_archived_at),
        "scan_back_archived": bool(g.id_scan_back_archived_at),
        # A ref whose object never arrived: the desk captured it offline and died, or its queue
        # was wiped, before the upload synced. Distinct from "archived" (deliberately offline)
        # and from "no scan" (never captured). Only checked for refs that are not archived, so a
        # purged scan does not get double-flagged.
        "scan_missing": _scan_missing(g.id_scan_ref, g.id_scan_archived_at),
        "scan_back_missing": _scan_missing(g.id_scan_back_ref, g.id_scan_back_archived_at),
    } for g in rows]}


def _scan_missing(ref, archived_at) -> bool:
    if not ref or archived_at:
        return False
    try:
        return secure_id_store.stat_scan(ref) is None
    except Exception:
        return False        # storage unreachable is not "missing"; the viewer will say so


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
        # RuntimeError now covers two cases: the encryption key is unset, OR object storage is
        # unreachable. Both mean "we will not store this scan" and both fail closed — nothing is
        # ever written in plaintext. The specific cause is in the server log.
        raise HTTPException(status_code=503,
                            detail="ID scans cannot be stored right now — check the server log")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    write_audit(db, user, "reception.guest_scan_upload", "booking", booking_id,
                after={"mime": file.content_type}, client="desktop", commit=True)
    return {"ref": ref, "mime": file.content_type}


# Envelopes are ciphertext already, so the cap here is on the sealed size; the plaintext cap
# is enforced again inside save_scan. RSA header + GCM tag add ~430 bytes, so this is generous.
_ENVELOPE_MAX_BYTES = 9 * 1024 * 1024


@router.post("/bookings/{booking_id}/guests/scan/envelope")
async def upload_guest_scan_envelope(booking_id: int,
                                     file: UploadFile = File(...),
                                     ref: str = Form(...),
                                     mime: str = Form(...),
                                     db: Session = Depends(get_db),
                                     user=Depends(require_reception_or_admin)):
    """Ingest a scan the desk sealed while OFFLINE (utils/scan_envelope.py).

    The desk chose `ref` at capture time — it had to, because the check-in it queued behind
    this upload already carries that ref. We verify the ref belongs to this booking, open the
    envelope, and hand the plaintext to save_scan, which Fernet-wraps it BEFORE the first write.
    Plaintext exists only in this request's memory.

    Idempotent by ref: if the object is already stored, nothing is rewritten and `already` is
    true. A desk that lost the 2xx and retries gets the same answer, and the archived sha of an
    existing object is never invalidated (see save_scan)."""
    if not db.query(Booking).filter(Booking.booking_id == booking_id).first():
        raise HTTPException(status_code=404, detail="Booking not found")
    if not secure_id_store.ref_belongs_to(ref, booking_id):
        raise HTTPException(status_code=400, detail="ref does not belong to this booking")
    if not secure_id_store.allowed_mime(mime):
        raise HTTPException(status_code=400, detail="Unsupported file type (use JPG/PNG/WEBP/PDF)")

    sealed = await file.read()
    if not sealed:
        raise HTTPException(status_code=400, detail="empty envelope")
    if len(sealed) > _ENVELOPE_MAX_BYTES:
        raise HTTPException(status_code=400, detail="envelope too large")

    from utils import scan_envelope
    if not scan_envelope.is_configured():
        raise HTTPException(status_code=503,
                            detail="Offline scan capture is not configured on the server")
    try:
        plaintext = scan_envelope.open_envelope(sealed)
    except ValueError as e:
        # A 4xx on purpose: the desk treats it as permanent and cancels the dependent check-in
        # rather than retrying a corrupt or foreign-key envelope forever.
        raise HTTPException(status_code=400, detail=f"envelope rejected: {e}")

    already = secure_id_store.stat_scan(ref) is not None
    try:
        stored_ref = secure_id_store.save_scan(booking_id, plaintext, mime, ref=ref)
    except RuntimeError:
        raise HTTPException(status_code=503,
                            detail="ID scans cannot be stored right now — check the server log")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        del plaintext

    write_audit(db, user, "reception.guest_scan_upload", "booking", booking_id,
                after={"mime": mime, "offline": True, "ref": stored_ref, "already": already},
                client="desktop", commit=True)
    return {"ref": stored_ref, "mime": mime, "already": already}


@router.get("/bookings/{booking_id}/guests/{guest_id}/scan")
def get_guest_scan(booking_id: int, guest_id: int,
                   side: str = Query("front", pattern="^(front|back)$"),
                   db: Session = Depends(get_db),
                   user=Depends(require_reception_or_admin)):
    """Stream the DECRYPTED ID scan for one guest (authed reception/admin only).

    An ID has two sides and the desk flatbed has no duplex, so `side` selects which pass to
    return. `front` is the default, keeping every existing caller working unchanged."""
    # Normalise before anything reads it: this value is written into the audit row, and an
    # audit entry that records something other than "front"/"back" is worse than none.
    side = "back" if side == "back" else "front"
    g = (db.query(BookingGuest)
         .filter(BookingGuest.id == guest_id, BookingGuest.booking_id == booking_id).first())
    ref = (g.id_scan_back_ref if side == "back" else g.id_scan_ref) if g else None
    mime = (g.id_scan_back_mime if side == "back" else g.id_scan_mime) if g else None
    if not ref:
        raise HTTPException(status_code=404, detail=f"No {side} ID scan on file")
    # Archived check comes FIRST. Once a scan is pulled offline and deleted from object storage,
    # read_scan would raise FileNotFoundError and this would 404 — indistinguishable from "this
    # guest never had a scan". 410 Gone says the opposite: it existed, we moved it deliberately,
    # and here is the ref that locates it inside the archive.
    archived_at = g.id_scan_back_archived_at if side == "back" else g.id_scan_archived_at
    if archived_at:
        raise HTTPException(
            status_code=410,
            detail=(f"This {side} ID scan was archived offline on "
                    f"{archived_at.strftime('%Y-%m-%d')} and is no longer stored on the server. "
                    f"Retrieve it from the backup archive using ref {ref}."))
    try:
        data = secure_id_store.read_scan(ref)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="ID scan not found")
    except RuntimeError:
        raise HTTPException(status_code=503,
                            detail="ID scan cannot be retrieved right now (key or storage unavailable)")
    # Record WHO looked at a government ID. `routers/crm.py` already emits `id.view` when a
    # guest record is opened; this path decrypts an actual ID image and was not audited at
    # all — a gap worth closing now that the web admin surfaces it (FE-3).
    # The SIDE is part of the record: without it you cannot tell which image was disclosed.
    write_audit(db, user, "id.view", "booking_guest", g.id,
                after={"booking_id": booking_id, "guest_name": g.name, "side": side,
                       "id_type": g.id_type, "id_number_masked": g.id_number_masked},
                commit=True)
    # Clamp the stored mime to the allowlist and tell the browser not to sniff past it. The admin
    # reads this response into a blob: URL, which inherits the ADMIN origin — so the Content-Type
    # on an ID image is a security boundary, not a display hint. Rows written before the check-in
    # schema validated the mime can still hold anything, hence the clamp here too.
    media_type = secure_id_store.safe_media_type(mime)
    filename = f"id-scan-{g.id}-{side}.{secure_id_store.extension_for(media_type)}"
    return StreamingResponse(
        io.BytesIO(data), media_type=media_type,
        headers={"X-Content-Type-Options": "nosniff",
                 "Content-Disposition": f'inline; filename="{filename}"'})


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
    # v5n: each occupant's ROOM, so a group booking's slip records who slept where (the whole point
    # of the roster when three rooms are on one booking, and the first thing a police query asks).
    _room_nums = {r.room_id: r.room_number for r in
                  db.query(Room).filter(Room.room_id.in_([i.room_id for i in booking.booking_items
                                                          if i.room_id] or [0])).all()}
    guests = [{"name": g.name, "id_type": g.id_type, "id_number_masked": g.id_number_masked,
               "has_scan": bool(g.id_scan_ref), "has_scan_back": bool(g.id_scan_back_ref),
               "is_primary": g.is_primary,
               "room_number": _room_nums.get(g.room_id)} for g in guest_rows]
    if not guests and booking.guest:
        guests = [{"name": booking.guest.name, "id_type": booking.guest.id_type,
                   "id_number_masked": booking.guest.id_number_masked, "has_scan": False,
                   "has_scan_back": False, "is_primary": True,
                   "room_number": rooms[0] if rooms else None}]

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
        "expected_check_out": booking_checkout_moment(booking),
        "booking_source": booking.booking_source,
        "grand_total": float(booking.grand_total or 0),
        "paid_total": total_paid(db, booking_id),
        "balance": float(folio.balance or 0) if folio else None,
        # Admin-editable rules/terms printed on the slip (FE-2 / F-A settings backbone).
        "registration_rules": _reg_rules(db),
        # All occupants with masked IDs (FE-3).
        "guests": guests,
        "adults": int(booking.adults or 1),
        "children": int(booking.children or 0),
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
