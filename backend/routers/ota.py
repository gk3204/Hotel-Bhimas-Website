"""OTA tracking endpoints (prompt 17, Phase A).

Makes OTA (MakeMyTrip / Goibibo / Booking.com / Agoda) business visible + reconcilable:
  * per-OTA config (commission %, active, mailbox toggle, cancellation rules)   [admin]
  * OTA bookings list (gross / commission / net payout / OTA id)                 [reception+admin]
  * payout reconciliation + actual-settlement entry                             [admin]
  * email-parsed draft inbox: confirm (one-click → real booking) / dismiss       [reception+admin]
  * manual IMAP poll trigger                                                     [admin]

Config/reconciliation/settlements are admin (owner cockpit — screens live in the web admin, which
the front-desk PC can reach). The OTA-bookings list + draft confirm/dismiss are reception-facing so
the desk can action them. Phase B (channel-manager two-way sync) is a later prompt.
"""
import logging
from datetime import date, datetime, time, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from database import SessionLocal
from models import Booking, OtaSettlement, OtaDraftBooking
from schemas import OtaChannelUpdate, OtaSettlementCreate, OtaDraftConfirm, DeskBookingCreate, BookingItemCreate
from utils.auth_utils import require_admin, require_reception_or_admin
from utils.audit import write_audit, _resolve_user_id
from services import ota_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ota", tags=["OTA"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _parse_date(s, field):
    if not s:
        return None
    try:
        return date.fromisoformat(s)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid {field}. Use YYYY-MM-DD")


def _range(from_, to, default_days=30):
    """Inclusive (dfrom, dto). Missing both -> last `default_days`. dfrom > dto -> 400."""
    dfrom = _parse_date(from_, "from")
    dto = _parse_date(to, "to")
    if dto is None:
        dto = date.today()
    if dfrom is None:
        dfrom = dto - timedelta(days=default_days - 1)
    if dfrom > dto:
        raise HTTPException(status_code=400, detail="'from' must be on or before 'to'")
    return dfrom, dto


# ============================================================
# Per-OTA config
# ============================================================
@router.get("/channels")
def list_channels(db: Session = Depends(get_db), user=Depends(require_admin)):
    return {
        "channels": [ota_service.serialize_channel(c) for c in ota_service.get_channels(db)],
        "poller": ota_service.poller_status(db),
    }


@router.put("/channels/{code}")
def update_channel(code: str, data: OtaChannelUpdate,
                   db: Session = Depends(get_db), user=Depends(require_admin)):
    if code not in ota_service.OTA_SOURCES:
        raise HTTPException(status_code=404, detail=f"Unknown OTA channel: {code}")
    c = ota_service.upsert_channel(
        db, code,
        display_name=data.display_name,
        commission_percent=data.commission_percent,
        active=data.active,
        mailbox_parsing_enabled=data.mailbox_parsing_enabled,
        cancellation_policy=data.cancellation_policy,
    )
    write_audit(db, user, "ota.channel_update", "ota_channel", c.id,
                after=ota_service.serialize_channel(c), commit=True)
    return ota_service.serialize_channel(c)


@router.get("/status")
def poller_status(db: Session = Depends(get_db), user=Depends(require_admin)):
    return ota_service.poller_status(db)


@router.get("/rates")
def channel_rates(db: Session = Depends(get_db), user=Depends(require_reception_or_admin)):
    """Reception-readable channel commission defaults (no config/secrets) — the desktop uses these
    to pre-fill the commission % when the desk enters an OTA booking."""
    return {"channels": [
        {"code": c.code, "display_name": c.display_name,
         "commission_percent": float(c.commission_percent or 0), "active": bool(c.active)}
        for c in ota_service.get_channels(db)
    ]}


# ============================================================
# OTA bookings list
# ============================================================
@router.get("/bookings")
def list_ota_bookings(from_: str = Query(None, alias="from"), to: str = Query(None),
                      source: str = Query(None),
                      db: Session = Depends(get_db), user=Depends(require_reception_or_admin)):
    dfrom, dto = _range(from_, to)
    q = (db.query(Booking)
         .filter(Booking.booking_source.in_(ota_service.OTA_SOURCES),
                 Booking.check_in >= dfrom, Booking.check_in <= dto))
    if source:
        if source not in ota_service.OTA_SOURCES:
            raise HTTPException(status_code=400, detail=f"Unknown OTA source: {source}")
        q = q.filter(Booking.booking_source == source)
    bookings = q.order_by(Booking.check_in.desc(), Booking.booking_id.desc()).all()
    rows = [ota_service.serialize_ota_booking(db, b) for b in bookings]
    totals = {
        "bookings": len(rows),
        "gross": round(sum(r["gross"] for r in rows), 2),
        "commission_amount": round(sum(r["commission_amount"] or 0 for r in rows), 2),
        "net_payout": round(sum(r["net_payout"] or 0 for r in rows), 2),
    }
    return {"from": str(dfrom), "to": str(dto), "rows": rows, "totals": totals}


# ============================================================
# Payout reconciliation + settlements
# ============================================================
@router.get("/reconciliation")
def reconciliation(from_: str = Query(None, alias="from"), to: str = Query(None),
                   channel: str = Query(None),
                   db: Session = Depends(get_db), user=Depends(require_admin)):
    dfrom, dto = _range(from_, to)
    if channel and channel not in ota_service.OTA_SOURCES:
        raise HTTPException(status_code=400, detail=f"Unknown OTA channel: {channel}")
    return ota_service.reconcile_payouts(db, channel=channel, dfrom=dfrom, dto=dto)


@router.post("/settlements")
def create_settlement(data: OtaSettlementCreate,
                      db: Session = Depends(get_db), user=Depends(require_admin)):
    s = ota_service.record_settlement(
        db, data.channel_code, data.amount,
        period_start=data.period_start, period_end=data.period_end,
        reference=data.reference, notes=data.notes, user_id=_resolve_user_id(db, user))
    write_audit(db, user, "ota.settlement_record", "ota_settlement", s.id,
                after=ota_service.serialize_settlement(s), commit=True)
    return ota_service.serialize_settlement(s)


@router.get("/settlements")
def list_settlements(from_: str = Query(None, alias="from"), to: str = Query(None),
                     channel: str = Query(None),
                     db: Session = Depends(get_db), user=Depends(require_admin)):
    dfrom, dto = _range(from_, to, default_days=90)
    q = db.query(OtaSettlement).filter(
        ota_service.func_date(OtaSettlement.created_at) >= dfrom,
        ota_service.func_date(OtaSettlement.created_at) <= dto)
    if channel:
        q = q.filter(OtaSettlement.channel_code == channel)
    rows = [ota_service.serialize_settlement(s)
            for s in q.order_by(OtaSettlement.created_at.desc()).all()]
    return {"from": str(dfrom), "to": str(dto), "rows": rows}


# ============================================================
# Email-parsed draft inbox
# ============================================================
@router.get("/drafts")
def list_drafts(status: str = Query(None),
                db: Session = Depends(get_db), user=Depends(require_reception_or_admin)):
    q = db.query(OtaDraftBooking)
    if status:
        q = q.filter(OtaDraftBooking.status == status)
    rows = [ota_service.serialize_draft(d)
            for d in q.order_by(OtaDraftBooking.created_at.desc()).limit(200).all()]
    return {"rows": rows}


@router.post("/drafts/{draft_id}/confirm")
def confirm_draft(draft_id: int, data: OtaDraftConfirm,
                  db: Session = Depends(get_db), user=Depends(require_reception_or_admin)):
    """Turn a pending email draft into a real desk booking (one click). Reuses the desk-booking
    engine (availability + pricing + guest reuse) so the OTA booking consumes shared-DB availability
    exactly like any other booking, and stamps source + OTA id + commission."""
    d = db.query(OtaDraftBooking).filter(OtaDraftBooking.id == draft_id).first()
    if not d:
        raise HTTPException(status_code=404, detail="Draft not found")
    if d.status == "confirmed":
        raise HTTPException(status_code=409, detail="Draft already confirmed")
    if d.kind == "cancellation":
        raise HTTPException(status_code=400, detail="Cancellation drafts can't be confirmed as a booking; dismiss it.")

    guest_name = data.guest_name or d.guest_name
    phone = data.phone or d.phone
    check_in = data.check_in or d.check_in
    check_out = data.check_out or d.check_out
    if not guest_name or not phone:
        raise HTTPException(status_code=400, detail="Guest name + phone are required (draft didn't parse them — supply in the request).")
    if not check_in or not check_out:
        raise HTTPException(status_code=400, detail="Check-in + check-out dates are required (draft didn't parse them — supply in the request).")

    commission_pct = data.ota_commission_percent
    if commission_pct is None and d.commission_percent is not None:
        commission_pct = float(d.commission_percent)

    booking_req = DeskBookingCreate(
        rooms=[BookingItemCreate(room_type_id=data.room_type_id, quantity=data.quantity)],
        guest_name=guest_name,
        phone=phone,
        email=(data.email or d.email or None),
        check_in=check_in,
        check_out=check_out,
        check_in_time=(data.check_in_time or time(12, 0)),
        booking_source=d.channel_code,
        ota_booking_id=d.ota_booking_id,
        ota_commission_percent=commission_pct,
    )

    # Call the desk-booking handler directly (plain function — Depends bypassed by explicit args).
    from routers.reception import create_desk_booking
    result = create_desk_booking(booking_req, db=db, user=user)

    d.status = "confirmed"
    d.linked_booking_id = result["booking_id"]
    write_audit(db, user, "ota.draft_confirm", "ota_draft_booking", d.id,
                after={"booking_id": result["booking_id"], "channel": d.channel_code,
                       "ota_booking_id": d.ota_booking_id}, commit=True)
    return {"draft_id": d.id, "booking": result}


@router.post("/drafts/{draft_id}/dismiss")
def dismiss_draft(draft_id: int,
                  db: Session = Depends(get_db), user=Depends(require_reception_or_admin)):
    d = db.query(OtaDraftBooking).filter(OtaDraftBooking.id == draft_id).first()
    if not d:
        raise HTTPException(status_code=404, detail="Draft not found")
    d.status = "dismissed"
    write_audit(db, user, "ota.draft_dismiss", "ota_draft_booking", d.id, commit=True)
    return {"draft_id": d.id, "status": d.status}


# ============================================================
# Manual IMAP poll
# ============================================================
@router.post("/poll")
def poll_now(db: Session = Depends(get_db), user=Depends(require_admin)):
    """Trigger the IMAP mailbox poll inline (mirrors /whatsapp/jobs/run). No-ops with
    configured=false when OTA_IMAP_* env is unset."""
    return ota_service.poll_mailbox(db)
