"""Customer / Guest CRM (prompt 14).

Turns one-off guests into recognized customers:
  * repeat-guest recognition (match on phone / ID last-4) with history + VIP/blacklist,
  * rich guest profiles (CRUD) + VIP + blacklist/watchlist,
  * a loyalty ledger (accrue on checkout, redeem as a folio discount),
  * pre-arrival digital registration (tokenized public link that pre-fills a booking),
  * ID/guest photo upload (pluggable R2 / local storage) + optional ID OCR auto-fill.

Reuses the existing seams: write_audit / auth deps / app_settings config / the folio
discount path for redemption / MaintenanceTicket for complaints. Additive only.

The module-level helpers `check_guest_gate()` and `accrue_loyalty_on_checkout()` are
imported by routers/reception.py so booking/check-in/checkout stay the single source of
truth for those flows.
"""
import json
import logging
import secrets
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File, Request
from fastapi.responses import FileResponse
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from database import SessionLocal
from models import (Guest, GuestProfile, Booking, BookingItem, Payment, Folio,
                    Room, RoomType, MaintenanceTicket, LoyaltyLedger, PreArrivalRegistration)
from schemas import (GuestProfileUpdate, VipUpdate, BlacklistUpdate, LoyaltyRedeem,
                     PreArrivalCreate, PreArrivalSubmit, FolioDiscountRequest)
from utils.auth_utils import require_reception_or_admin, require_admin
from utils.audit import write_audit, _resolve_user_id
from utils.settings import get_crm_config, set_setting, get_setting
from utils import storage
from services import ocr_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/crm", tags=["Customer CRM"])

MAX_UPLOAD_BYTES = 8 * 1024 * 1024  # 8 MB per photo


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# =====================================================================
# helpers
# =====================================================================
def _mask_id(number: str | None) -> str | None:
    if not number:
        return None
    digits = "".join(ch for ch in number if ch.isalnum())
    return ("****" + digits[-4:]) if len(digits) >= 4 else "****"


def _get_profile(db: Session, guest_id: int) -> GuestProfile | None:
    return db.query(GuestProfile).filter(GuestProfile.guest_id == guest_id).first()


def _profile_dict(p: GuestProfile | None) -> dict:
    if not p:
        return {"vip": False, "blacklist": False, "blacklist_reason": None,
                "loyalty_points": 0, "marketing_optin": False, "id_type": None,
                "id_number_masked": None, "id_photo_url": None, "guest_photo_url": None,
                "address": None, "dob": None, "gstin": None, "notes": None,
                "nationality": None, "is_foreign_national": False,
                "passport_number_masked": None, "passport_place_of_issue": None,
                "passport_expiry": None, "visa_number_masked": None, "visa_type": None,
                "visa_expiry": None, "arrived_from": None, "next_destination": None}
    return {
        "vip": bool(p.vip), "blacklist": bool(p.blacklist),
        "blacklist_reason": p.blacklist_reason, "loyalty_points": int(p.loyalty_points or 0),
        "marketing_optin": bool(p.marketing_optin), "id_type": p.id_type,
        "id_number_masked": p.id_number_masked, "id_photo_url": p.id_photo_url,
        "guest_photo_url": p.guest_photo_url, "address": p.address,
        "dob": p.dob.isoformat() if p.dob else None, "gstin": p.gstin, "notes": p.notes,
        "nationality": p.nationality, "is_foreign_national": bool(p.is_foreign_national),
        "passport_number_masked": p.passport_number_masked,
        "passport_place_of_issue": p.passport_place_of_issue,
        "passport_expiry": p.passport_expiry.isoformat() if p.passport_expiry else None,
        "visa_number_masked": p.visa_number_masked, "visa_type": p.visa_type,
        "visa_expiry": p.visa_expiry.isoformat() if p.visa_expiry else None,
        "arrived_from": p.arrived_from, "next_destination": p.next_destination,
    }


def _aggregates(db: Session, guest_id: int) -> dict:
    """Computed stay_count / total_spend / last_stay for a guest (not stored)."""
    bookings = db.query(Booking).filter(Booking.guest_id == guest_id).all()
    stay_ids = [b.booking_id for b in bookings
                if b.status in ("checked_in", "checked_out")]
    stay_count = len(stay_ids)
    last_stay = None
    stays = [b for b in bookings if b.status in ("checked_in", "checked_out")]
    if stays:
        last_stay = max(b.check_in for b in stays).isoformat()
    # spend = net paid (paid payments minus refunds) across the guest's bookings
    total_spend = 0.0
    if bookings:
        b_ids = [b.booking_id for b in bookings]
        paid = db.query(func.coalesce(func.sum(Payment.amount), 0)).filter(
            Payment.booking_id.in_(b_ids), Payment.status == "paid").scalar() or 0
        refunded = db.query(func.coalesce(func.sum(Payment.refund_amount), 0)).filter(
            Payment.booking_id.in_(b_ids),
            Payment.refund_status == "completed").scalar() or 0
        total_spend = round(float(paid) - float(refunded), 2)
    return {"stay_count": stay_count, "total_spend": total_spend, "last_stay": last_stay}


def _guest_dict(db: Session, guest: Guest, with_aggregates: bool = True) -> dict:
    p = _get_profile(db, guest.guest_id)
    d = {
        "guest_id": guest.guest_id, "name": guest.name, "phone": guest.phone,
        "email": guest.email, "created_at": guest.created_at.isoformat() if guest.created_at else None,
        "profile": _profile_dict(p),
    }
    if with_aggregates:
        d.update(_aggregates(db, guest.guest_id))
    return d


def _complaints_for_guest(db: Session, guest_id: int) -> list[dict]:
    """Guest complaints = maintenance tickets on the guest's bookings (esp. source='guest')."""
    b_ids = [b.booking_id for b in
             db.query(Booking.booking_id).filter(Booking.guest_id == guest_id).all()]
    if not b_ids:
        return []
    tickets = db.query(MaintenanceTicket).filter(
        MaintenanceTicket.booking_id.in_([b[0] if isinstance(b, tuple) else b for b in b_ids])
    ).order_by(MaintenanceTicket.created_at.desc()).all()
    open_states = ("open", "assigned", "in_progress", "awaiting_parts")
    out = []
    for t in tickets:
        out.append({
            "id": t.id, "issue": t.issue, "category": t.category, "status": t.status,
            "source": t.source, "priority": t.priority, "booking_id": t.booking_id,
            "is_open": t.status in open_states,
            "created_at": t.created_at.isoformat() if t.created_at else None,
        })
    return out


# --- exported for routers/reception.py -------------------------------------------------
def check_guest_gate(db: Session, guest: Guest | None) -> dict:
    """VIP/blacklist gate info for a guest. `enforcement` is 'warn' or 'block'.
    Returns benign defaults for a guest with no profile (no behaviour change)."""
    cfg = get_crm_config(db)
    p = _get_profile(db, guest.guest_id) if guest else None
    return {
        "vip": bool(p.vip) if p else False,
        "blacklist": bool(p.blacklist) if p else False,
        "reason": p.blacklist_reason if p else None,
        "enforcement": cfg["blacklist_enforcement"],
    }


def match_guest(db: Session, phone: str | None) -> Guest | None:
    """Most-recent existing guest with this exact phone (repeat-guest recognition)."""
    if not phone:
        return None
    return db.query(Guest).filter(Guest.phone == phone.strip()).order_by(
        Guest.guest_id.desc()).first()


def guest_has_open_complaint(db: Session, guest_id: int) -> bool:
    """True if the guest has any unresolved complaint ticket. Single source of truth for
    prompt-15 review-request suppression — never ask an unhappy guest for a public review
    (uses the same open-ticket states as the guest-history view)."""
    if not guest_id:
        return False
    return any(c["is_open"] for c in _complaints_for_guest(db, guest_id))


def accrue_loyalty_on_checkout(db: Session, booking: Booking, user=None) -> int:
    """Award loyalty points for a completed stay. Idempotent: at most one accrual row
    per booking. Uses the folio total when present (actual charges), else the booking
    grand total. Joins the caller's transaction (no commit here). Returns points awarded."""
    if not booking or not booking.guest_id:
        return 0
    existing = db.query(LoyaltyLedger).filter(
        LoyaltyLedger.booking_id == booking.booking_id,
        LoyaltyLedger.delta > 0).first()
    if existing:
        return 0
    cfg = get_crm_config(db)
    rate = cfg["loyalty_points_per_rupee"]
    if rate <= 0:
        return 0
    folio = db.query(Folio).filter(Folio.booking_id == booking.booking_id).first()
    spend = float(folio.total) if folio and folio.total is not None else float(booking.grand_total or 0)
    points = int(round(spend * rate))
    if points <= 0:
        return 0
    profile = _get_or_create_profile(db, booking.guest_id, user)
    profile.loyalty_points = int(profile.loyalty_points or 0) + points
    db.add(LoyaltyLedger(
        guest_id=booking.guest_id, booking_id=booking.booking_id, delta=points,
        reason=f"Stay accrual (₹{spend:,.0f} @ {rate} pts/₹)",
        points_after=profile.loyalty_points, created_by=_resolve_user_id(db, user)))
    return points


def _get_or_create_profile(db: Session, guest_id: int, user=None) -> GuestProfile:
    p = _get_profile(db, guest_id)
    if not p:
        p = GuestProfile(guest_id=guest_id, updated_by=_resolve_user_id(db, user),
                         updated_at=datetime.utcnow())
        db.add(p)
        db.flush()
    return p


# =====================================================================
# match / search / directory
# =====================================================================
@router.get("/guests/match", dependencies=[Depends(require_reception_or_admin)])
def match_guest_endpoint(phone: str | None = Query(None), id_number: str | None = Query(None),
                         db: Session = Depends(get_db)):
    """Repeat-guest recognition: given a phone (and/or ID), return the existing guest with
    profile + VIP/blacklist + aggregates + recent history, or {matched: false}."""
    guest = match_guest(db, phone)
    if not guest and id_number:
        masked = _mask_id(id_number)
        prof = db.query(GuestProfile).filter(GuestProfile.id_number_masked == masked).first()
        if prof:
            guest = db.query(Guest).filter(Guest.guest_id == prof.guest_id).first()
    if not guest:
        return {"matched": False}
    data = _guest_dict(db, guest)
    data["matched"] = True
    data["history"] = _history(db, guest.guest_id, limit=5)
    return data


@router.get("/guests", dependencies=[Depends(require_reception_or_admin)])
def list_guests(q: str | None = Query(None), vip: bool | None = Query(None),
                blacklist: bool | None = Query(None), limit: int = Query(50, le=200),
                db: Session = Depends(get_db)):
    """Guest directory search (name/phone/email) with VIP/blacklist segment filters."""
    query = db.query(Guest).outerjoin(GuestProfile, GuestProfile.guest_id == Guest.guest_id)
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(or_(Guest.name.ilike(like), Guest.phone.ilike(like),
                                 Guest.email.ilike(like)))
    if vip is True:
        query = query.filter(GuestProfile.vip == True)  # noqa: E712
    if blacklist is True:
        query = query.filter(GuestProfile.blacklist == True)  # noqa: E712
    guests = query.order_by(Guest.guest_id.desc()).limit(limit).all()
    return {"total": len(guests),
            "data": [_guest_dict(db, g) for g in guests]}


@router.get("/guests/{guest_id}")
def get_guest(guest_id: int, request: Request, db: Session = Depends(get_db),
              user=Depends(require_reception_or_admin)):
    guest = db.query(Guest).filter(Guest.guest_id == guest_id).first()
    if not guest:
        raise HTTPException(status_code=404, detail="Guest not found")
    data = _guest_dict(db, guest)
    data["gate"] = check_guest_gate(db, guest)
    # ID-access audit (prompt 18, slice 5): opening a guest record exposes their masked ID /
    # ID document — record who viewed it, when, from where. Best-effort; never blocks the read.
    profile = _get_profile(db, guest_id)
    has_docs = bool((guest.id_number_masked) or (profile and (profile.id_photo_url or
                    profile.id_number_masked or profile.passport_number_masked)))
    if has_docs:
        client_ip = request.client.host if request and request.client else None
        write_audit(db, user, "id.view", "guest", guest_id,
                    after={"id_type": guest.id_type}, ip=client_ip, client="web", commit=True)
    return data


@router.put("/guests/{guest_id}")
def update_guest(guest_id: int, data: GuestProfileUpdate, db: Session = Depends(get_db),
                 user=Depends(require_reception_or_admin)):
    """Upsert a guest's CRM profile (and the thin Guest name/phone/email when given).
    The raw ID number is masked; never stored."""
    guest = db.query(Guest).filter(Guest.guest_id == guest_id).first()
    if not guest:
        raise HTTPException(status_code=404, detail="Guest not found")
    try:
        if data.name is not None:
            guest.name = data.name.strip()
        if data.phone is not None:
            guest.phone = data.phone.strip()
        if data.email is not None:
            guest.email = data.email
        p = _get_or_create_profile(db, guest_id, user)
        if data.id_type is not None:
            p.id_type = data.id_type
        if data.id_number is not None:
            p.id_number_masked = _mask_id(data.id_number)
        if data.id_photo_url is not None:
            p.id_photo_url = data.id_photo_url
        if data.guest_photo_url is not None:
            p.guest_photo_url = data.guest_photo_url
        if data.address is not None:
            p.address = data.address
        if data.dob is not None:
            p.dob = data.dob
        if data.gstin is not None:
            p.gstin = data.gstin
        if data.marketing_optin is not None:
            p.marketing_optin = data.marketing_optin
        if data.notes is not None:
            p.notes = data.notes
        # --- Form C / FRRO fields (prompt 18; passport/visa numbers masked at rest) ---
        if data.nationality is not None:
            p.nationality = data.nationality.strip() or None
        if data.is_foreign_national is not None:
            p.is_foreign_national = bool(data.is_foreign_national)
        if data.passport_number is not None:
            p.passport_number_masked = _mask_id(data.passport_number)
        if data.passport_place_of_issue is not None:
            p.passport_place_of_issue = data.passport_place_of_issue
        if data.passport_expiry is not None:
            p.passport_expiry = data.passport_expiry
        if data.visa_number is not None:
            p.visa_number_masked = _mask_id(data.visa_number)
        if data.visa_type is not None:
            p.visa_type = data.visa_type
        if data.visa_expiry is not None:
            p.visa_expiry = data.visa_expiry
        if data.arrived_from is not None:
            p.arrived_from = data.arrived_from
        if data.next_destination is not None:
            p.next_destination = data.next_destination
        p.updated_at = datetime.utcnow()
        p.updated_by = _resolve_user_id(db, user)
        db.commit()
        write_audit(db, user, "crm.profile_update", "guest", guest_id,
                    after={"vip": p.vip, "blacklist": p.blacklist,
                           "id_type": p.id_type, "has_photo": bool(p.id_photo_url)},
                    client="web", commit=True)
        return _guest_dict(db, guest)
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        logger.error(f"update_guest failed: {e}", exc_info=True)
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to update guest")


@router.post("/guests/{guest_id}/vip")
def set_vip(guest_id: int, data: VipUpdate, db: Session = Depends(get_db),
            user=Depends(require_admin)):
    guest = db.query(Guest).filter(Guest.guest_id == guest_id).first()
    if not guest:
        raise HTTPException(status_code=404, detail="Guest not found")
    p = _get_or_create_profile(db, guest_id, user)
    p.vip = bool(data.vip)
    p.updated_at = datetime.utcnow()
    p.updated_by = _resolve_user_id(db, user)
    db.commit()
    write_audit(db, user, "crm.vip", "guest", guest_id, after={"vip": p.vip},
                client="web", commit=True)
    return _guest_dict(db, guest)


@router.post("/guests/{guest_id}/blacklist")
def set_blacklist(guest_id: int, data: BlacklistUpdate, db: Session = Depends(get_db),
                  user=Depends(require_admin)):
    guest = db.query(Guest).filter(Guest.guest_id == guest_id).first()
    if not guest:
        raise HTTPException(status_code=404, detail="Guest not found")
    p = _get_or_create_profile(db, guest_id, user)
    p.blacklist = bool(data.blacklist)
    p.blacklist_reason = data.reason.strip() if (data.blacklist and data.reason) else None
    p.updated_at = datetime.utcnow()
    p.updated_by = _resolve_user_id(db, user)
    db.commit()
    write_audit(db, user, "crm.blacklist", "guest", guest_id,
                after={"blacklist": p.blacklist, "reason": p.blacklist_reason},
                client="web", commit=True)
    return _guest_dict(db, guest)


# =====================================================================
# history
# =====================================================================
def _history(db: Session, guest_id: int, limit: int = 50) -> list[dict]:
    bookings = db.query(Booking).filter(Booking.guest_id == guest_id).order_by(
        Booking.check_in.desc()).limit(limit).all()
    out = []
    for b in bookings:
        rooms = []
        for it in db.query(BookingItem).filter(BookingItem.booking_id == b.booking_id).all():
            rt = db.query(RoomType).filter(RoomType.room_type_id == it.room_type_id).first()
            label = rt.name if rt else f"type {it.room_type_id}"
            if it.room_id:
                room = db.query(Room).filter(Room.room_id == it.room_id).first()
                if room:
                    label = f"{label} · {room.room_number}"
            rooms.append(label)
        paid = db.query(func.coalesce(func.sum(Payment.amount), 0)).filter(
            Payment.booking_id == b.booking_id, Payment.status == "paid").scalar() or 0
        out.append({
            "booking_id": b.booking_id, "check_in": b.check_in.isoformat(),
            "check_out": b.check_out.isoformat(), "status": b.status,
            "source": b.booking_source, "rooms": rooms,
            "grand_total": float(b.grand_total or 0), "paid": float(paid),
        })
    return out


@router.get("/guests/{guest_id}/history", dependencies=[Depends(require_reception_or_admin)])
def guest_history(guest_id: int, db: Session = Depends(get_db)):
    guest = db.query(Guest).filter(Guest.guest_id == guest_id).first()
    if not guest:
        raise HTTPException(status_code=404, detail="Guest not found")
    complaints = _complaints_for_guest(db, guest_id)
    return {
        "guest_id": guest_id,
        **_aggregates(db, guest_id),
        "bookings": _history(db, guest_id),
        "complaints": complaints,
        # prompt-15 hook: an open complaint suppresses review-request/offer messages.
        "has_open_complaint": any(c["is_open"] for c in complaints),
    }


# =====================================================================
# loyalty
# =====================================================================
@router.get("/guests/{guest_id}/loyalty", dependencies=[Depends(require_reception_or_admin)])
def guest_loyalty(guest_id: int, db: Session = Depends(get_db)):
    guest = db.query(Guest).filter(Guest.guest_id == guest_id).first()
    if not guest:
        raise HTTPException(status_code=404, detail="Guest not found")
    p = _get_profile(db, guest_id)
    rows = db.query(LoyaltyLedger).filter(LoyaltyLedger.guest_id == guest_id).order_by(
        LoyaltyLedger.created_at.desc()).all()
    cfg = get_crm_config(db)
    return {
        "guest_id": guest_id,
        "balance": int(p.loyalty_points) if p else 0,
        "rupee_per_point": cfg["loyalty_rupee_per_point"],
        "ledger": [{
            "id": r.id, "delta": r.delta, "reason": r.reason, "points_after": r.points_after,
            "booking_id": r.booking_id,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        } for r in rows],
    }


@router.post("/guests/{guest_id}/loyalty/redeem")
def redeem_loyalty(guest_id: int, data: LoyaltyRedeem, db: Session = Depends(get_db),
                   user=Depends(require_reception_or_admin)):
    """Redeem points as a non-taxable folio discount on the guest's open folio.
    Points -> ₹ via loyalty_rupee_per_point. Debits points + writes a ledger row."""
    from routers.folio import apply_discount  # local import avoids a circular module load
    guest = db.query(Guest).filter(Guest.guest_id == guest_id).first()
    if not guest:
        raise HTTPException(status_code=404, detail="Guest not found")
    p = _get_profile(db, guest_id)
    balance = int(p.loyalty_points) if p else 0
    if data.points > balance:
        raise HTTPException(status_code=400,
                            detail=f"Only {balance} points available")
    cfg = get_crm_config(db)
    rupees = round(data.points * cfg["loyalty_rupee_per_point"], 2)
    if rupees <= 0:
        raise HTTPException(status_code=400, detail="These points have no redeemable value")

    # Locate the target open folio (named booking, else the guest's newest in-house folio).
    folio_q = db.query(Folio).join(Booking, Booking.booking_id == Folio.booking_id).filter(
        Booking.guest_id == guest_id, Folio.status == "open")
    if data.booking_id:
        folio_q = folio_q.filter(Folio.booking_id == data.booking_id)
    folio = folio_q.order_by(Folio.id.desc()).first()
    if not folio:
        raise HTTPException(status_code=409,
                            detail="No open folio to apply the discount to — check the guest in first")

    # Reuse the folio discount path (handles totals + audit + freeze rules).
    apply_discount(folio.id, FolioDiscountRequest(
        amount=rupees, reason=f"Loyalty redemption ({data.points} pts)"), db, user)

    # Debit the points + ledger row (own commit).
    p.loyalty_points = balance - data.points
    p.updated_at = datetime.utcnow()
    last_charge = db.query(Folio).filter(Folio.id == folio.id).first()  # noqa: F841 (kept for clarity)
    ledger = LoyaltyLedger(
        guest_id=guest_id, booking_id=folio.booking_id, delta=-data.points,
        reason=f"Redeemed for ₹{rupees:,.2f} discount", points_after=p.loyalty_points,
        created_by=_resolve_user_id(db, user))
    db.add(ledger)
    db.commit()
    write_audit(db, user, "crm.loyalty_redeem", "guest", guest_id,
                after={"points": data.points, "rupees": rupees, "folio_id": folio.id},
                client="desktop", commit=True)
    return {"guest_id": guest_id, "redeemed_points": data.points, "discount": rupees,
            "balance": p.loyalty_points, "folio_id": folio.id}


# =====================================================================
# uploads / OCR
# =====================================================================
async def _read_upload(file: UploadFile) -> bytes:
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty file")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File too large (max 8 MB)")
    return data


@router.post("/upload")
async def upload_file(file: UploadFile = File(...), kind: str = Query("crm"),
                      user=Depends(require_reception_or_admin)):
    """Desk/admin upload of an ID or guest photo. Returns a storage URL (R2 or local)."""
    data = await _read_upload(file)
    url = storage.save_bytes(data, file.filename, file.content_type, kind=kind)
    return {"url": url, "storage": "r2" if storage.is_r2_configured() else "local"}


@router.get("/files/{name}")
def get_file(name: str):
    """Serve a locally-stored upload (fallback storage). No-op for R2 (absolute URLs)."""
    path = storage.local_path(name)
    if not path:
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(path)


@router.post("/ocr")
async def ocr_id(file: UploadFile = File(...), id_type: str | None = Query(None),
                 user=Depends(require_reception_or_admin)):
    """OCR an ID document to pre-fill name/number/DOB (staff confirms). Returns
    {configured: false} when no OCR provider is set — the desk types the fields."""
    data = await _read_upload(file)
    return ocr_service.extract_id(data, id_type)


# =====================================================================
# pre-arrival digital registration
# =====================================================================
def _public_base_url() -> str:
    import os
    return os.getenv("PUBLIC_SITE_URL", "https://hotelbhimas.in").rstrip("/")


@router.post("/pre-arrival")
def create_prearrival(data: PreArrivalCreate, db: Session = Depends(get_db),
                      user=Depends(require_reception_or_admin)):
    """Create + send a pre-arrival registration link for a booking. Emails the guest
    (WhatsApp send is a logger stub -> prompt 15). Returns the link + token."""
    booking = db.query(Booking).filter(Booking.booking_id == data.booking_id).first()
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")
    guest = db.query(Guest).filter(Guest.guest_id == booking.guest_id).first()
    cfg = get_crm_config(db)

    token = secrets.token_urlsafe(24)
    reg = PreArrivalRegistration(
        token=token, booking_id=booking.booking_id, guest_id=booking.guest_id,
        status="sent", expires_at=datetime.utcnow() + timedelta(hours=cfg["registration_link_ttl_hours"]),
        created_by=_resolve_user_id(db, user))
    db.add(reg)
    db.commit()

    link = f"{_public_base_url()}/pre-arrival/{token}"
    delivery = {"email": False, "whatsapp": False}
    if data.channel in ("email",) and guest and guest.email:
        try:
            from utils.email_service import send_prearrival_link_email
            send_prearrival_link_email(guest.email, guest.name, link)
            delivery["email"] = True
        except Exception as e:
            logger.error(f"pre-arrival email failed: {e}")
    if data.channel == "whatsapp":
        # Real WhatsApp delivery (prompt 15) — stub/log until WHATSAPP_* env is set.
        try:
            from utils import whatsapp_service
            if guest and guest.phone:
                row = whatsapp_service.send_template(
                    db, guest.phone, "prearrival_link",
                    {"guest_name": guest.name, "link": link},
                    guest_id=guest.guest_id, booking_id=booking.booking_id)
                delivery["whatsapp"] = bool(row and row.status in ("sent", "delivered", "read"))
            else:
                delivery["whatsapp"] = False
        except Exception as e:
            logger.error(f"pre-arrival WhatsApp failed: {e}")

    write_audit(db, user, "crm.prearrival_create", "booking", booking.booking_id,
                after={"token": token[:8] + "…", "channel": data.channel}, client="web", commit=True)
    return {"token": token, "link": link, "booking_id": booking.booking_id, "delivery": delivery}


def _booking_summary(db: Session, booking: Booking) -> dict:
    guest = db.query(Guest).filter(Guest.guest_id == booking.guest_id).first()
    rooms = []
    for it in db.query(BookingItem).filter(BookingItem.booking_id == booking.booking_id).all():
        rt = db.query(RoomType).filter(RoomType.room_type_id == it.room_type_id).first()
        rooms.append({"room_type": rt.name if rt else None, "quantity": it.quantity})
    return {
        "booking_id": booking.booking_id, "check_in": booking.check_in.isoformat(),
        "check_out": booking.check_out.isoformat(),
        "guest_name": guest.name if guest else None,
        "phone": guest.phone if guest else None,
        "email": guest.email if guest else None,
        "rooms": rooms,
    }


@router.get("/pre-arrival/{token}")
def get_prearrival(token: str, db: Session = Depends(get_db)):
    """PUBLIC — the guest's pre-arrival form loads this: booking basics + any saved payload."""
    reg = db.query(PreArrivalRegistration).filter(PreArrivalRegistration.token == token).first()
    if not reg:
        raise HTTPException(status_code=404, detail="This link is not valid.")
    if reg.expires_at and reg.expires_at < datetime.utcnow():
        raise HTTPException(status_code=410, detail="This link has expired. Please contact the hotel.")
    booking = db.query(Booking).filter(Booking.booking_id == reg.booking_id).first()
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")
    if reg.status == "sent":
        reg.status = "opened"
        db.commit()
    payload = json.loads(reg.payload) if reg.payload else None
    return {"status": reg.status, "booking": _booking_summary(db, booking),
            "submitted": payload}


@router.post("/pre-arrival/{token}")
def submit_prearrival(token: str, data: PreArrivalSubmit, db: Session = Depends(get_db)):
    """PUBLIC — the guest submits their details. Upserts the booking's guest + profile so
    check-in is pre-filled; the desk verifies on arrival."""
    reg = db.query(PreArrivalRegistration).filter(PreArrivalRegistration.token == token).first()
    if not reg:
        raise HTTPException(status_code=404, detail="This link is not valid.")
    if reg.expires_at and reg.expires_at < datetime.utcnow():
        raise HTTPException(status_code=410, detail="This link has expired. Please contact the hotel.")
    if reg.status in ("submitted", "verified"):
        raise HTTPException(status_code=409, detail="This registration has already been submitted.")
    booking = db.query(Booking).filter(Booking.booking_id == reg.booking_id).first()
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")

    guest = db.query(Guest).filter(Guest.guest_id == booking.guest_id).first()
    if not guest:
        raise HTTPException(status_code=404, detail="Guest not found")

    # Update the thin guest (name/phone/email) if the guest corrected them.
    if data.name:
        guest.name = data.name.strip()
    if data.phone:
        guest.phone = data.phone.strip()
    if data.email:
        guest.email = data.email

    p = _get_or_create_profile(db, guest.guest_id, user=None)
    if data.id_type is not None:
        p.id_type = data.id_type
    if data.id_number is not None:
        p.id_number_masked = _mask_id(data.id_number)
    if data.id_photo_url is not None:
        p.id_photo_url = data.id_photo_url
    if data.address is not None:
        p.address = data.address
    if data.dob is not None:
        p.dob = data.dob
    if data.gstin is not None:
        p.gstin = data.gstin
    if data.marketing_optin is not None:
        p.marketing_optin = data.marketing_optin
    p.updated_at = datetime.utcnow()

    # Snapshot the submitted payload (masked) for the desk to verify.
    reg.payload = json.dumps({
        "name": guest.name, "phone": guest.phone, "email": guest.email,
        "id_type": p.id_type, "id_number_masked": p.id_number_masked,
        "id_photo_url": p.id_photo_url, "address": p.address,
        "dob": p.dob.isoformat() if p.dob else None, "gstin": p.gstin,
        "marketing_optin": p.marketing_optin,
    })
    reg.status = "submitted"
    reg.submitted_at = datetime.utcnow()
    db.commit()
    write_audit(db, None, "crm.prearrival_submit", "booking", booking.booking_id,
                after={"guest_id": guest.guest_id}, client="guest", commit=True)
    return {"status": "submitted", "booking_id": booking.booking_id}


@router.post("/pre-arrival/{token}/upload")
async def prearrival_upload(token: str, file: UploadFile = File(...),
                            db: Session = Depends(get_db)):
    """PUBLIC — guest uploads their ID photo from the pre-arrival page (token-scoped)."""
    reg = db.query(PreArrivalRegistration).filter(PreArrivalRegistration.token == token).first()
    if not reg:
        raise HTTPException(status_code=404, detail="This link is not valid.")
    if reg.expires_at and reg.expires_at < datetime.utcnow():
        raise HTTPException(status_code=410, detail="This link has expired.")
    data = await _read_upload(file)
    url = storage.save_bytes(data, file.filename, file.content_type, kind="prearrival")
    return {"url": url}


# =====================================================================
# config (admin-editable)
# =====================================================================
@router.get("/config", dependencies=[Depends(require_reception_or_admin)])
def get_config(db: Session = Depends(get_db)):
    cfg = get_crm_config(db)
    cfg["ocr_configured"] = ocr_service.is_configured()
    cfg["storage"] = "r2" if storage.is_r2_configured() else "local"
    return cfg


@router.put("/config")
def update_config(loyalty_points_per_rupee: float | None = None,
                  loyalty_rupee_per_point: float | None = None,
                  blacklist_enforcement: str | None = None,
                  db: Session = Depends(get_db), user=Depends(require_admin)):
    from utils.settings import (LOYALTY_POINTS_PER_RUPEE_KEY, LOYALTY_RUPEE_PER_POINT_KEY,
                                BLACKLIST_ENFORCEMENT_KEY)
    if loyalty_points_per_rupee is not None:
        set_setting(db, LOYALTY_POINTS_PER_RUPEE_KEY, max(0.0, loyalty_points_per_rupee), user)
    if loyalty_rupee_per_point is not None:
        set_setting(db, LOYALTY_RUPEE_PER_POINT_KEY, max(0.0, loyalty_rupee_per_point), user)
    if blacklist_enforcement is not None:
        if blacklist_enforcement not in ("warn", "block"):
            raise HTTPException(status_code=400, detail="enforcement must be 'warn' or 'block'")
        set_setting(db, BLACKLIST_ENFORCEMENT_KEY, blacklist_enforcement, user)
    db.commit()
    write_audit(db, user, "crm.config_update", "app_settings", None,
                after=get_crm_config(db), client="web", commit=True)
    return get_crm_config(db)
