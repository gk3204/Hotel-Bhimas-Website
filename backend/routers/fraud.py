"""Anti-fraud / internal-controls endpoints (prompt 11, core).

- Fraud-alerts dashboard: list / filter / detail / acknowledge-or-dismiss.
- Reconciliation sweep (manual POST or lazy on dashboard load) + a card-vs-booking-vs-payment report.
- Owner daily digest (viewable; WhatsApp delivery lands in prompt 15).
- Owner-approval OTP: request / list (Approvals inbox, shows the code = interim on-screen delivery) / verify.

Admin-only except the two OTP endpoints the reception desk itself calls.
"""
import json
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from database import SessionLocal
from models import (Booking, BookingItem, CardIssuance, Folio, FraudAlert,
                    OwnerOtp, Payment, Room, RoomType)
from schemas import AlertReview, FraudConfigUpdate, OtpRequest, OtpVerify
from utils import settings as app_settings
from utils.audit import write_audit, _resolve_user_id
from utils.auth_utils import require_admin, require_reception_or_admin
from utils.fraud_detection import run_reconciliation, compute_daily_digest, get_config
from utils.owner_otp import (OTP_ACTIONS, build_context, create_otp, consume_otp,
                             deliver_otp, safe_extras)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/fraud", tags=["Anti-Fraud"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("/health", dependencies=[Depends(require_admin)])
def health():
    return {"status": "ok", "module": "fraud"}


# ---------------------------------------------------------------------------
# alerts dashboard
# ---------------------------------------------------------------------------

def _parse_detail(raw):
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return {"raw": raw}


def _alert_dict(db, a: FraudAlert, room_numbers=None):
    room_number = None
    if a.room_id:
        room_number = (room_numbers or {}).get(a.room_id)
        if room_number is None:
            r = db.query(Room).filter(Room.room_id == a.room_id).first()
            room_number = r.room_number if r else None
    return {
        "id": a.id, "type": a.type, "severity": a.severity, "status": a.status,
        "booking_id": a.booking_id, "room_id": a.room_id, "room_number": room_number,
        "card_id": a.card_id,
        "detail": _parse_detail(a.detail),
        "detected_at": str(a.detected_at) if a.detected_at else None,
        "reviewed_by": a.reviewed_by,
        "reviewed_at": str(a.reviewed_at) if a.reviewed_at else None,
        "review_note": a.review_note,
    }


@router.get("/alerts", dependencies=[Depends(require_admin)])
def list_alerts(db: Session = Depends(get_db),
                status: str = Query(None), type: str = Query(None),
                severity: str = Query(None), reconcile: bool = Query(False),
                date_from: str = Query(None, alias="from"),
                date_to: str = Query(None, alias="to"),
                limit: int = Query(200, ge=1, le=1000)):
    """List fraud alerts (newest first) with optional filters. `reconcile=true` runs the
    detection sweep first (the desktop dashboard loads with this on)."""
    if reconcile:
        try:
            run_reconciliation(db)
        except Exception as e:
            logger.error(f"inline reconciliation failed: {e}", exc_info=True)

    q = db.query(FraudAlert)
    if status:
        q = q.filter(FraudAlert.status == status)
    if type:
        q = q.filter(FraudAlert.type == type)
    if severity:
        q = q.filter(FraudAlert.severity == severity)
    if date_from:
        q = q.filter(FraudAlert.detected_at >= date_from)
    if date_to:
        q = q.filter(FraudAlert.detected_at <= date_to + " 23:59:59")
    rows = q.order_by(FraudAlert.detected_at.desc(), FraudAlert.id.desc()).limit(limit).all()

    room_numbers = {r.room_id: r.room_number for r in db.query(Room).filter(
        Room.room_id.in_({a.room_id for a in rows if a.room_id})).all()} if rows else {}

    # summary counts (unfiltered — for the dashboard stat tiles)
    open_total = db.query(func.count(FraudAlert.id)).filter(FraudAlert.status == "open").scalar() or 0
    high_open = db.query(func.count(FraudAlert.id)).filter(
        FraudAlert.status == "open", FraudAlert.severity == "high").scalar() or 0
    cleaning_open = db.query(func.count(FraudAlert.id)).filter(
        FraudAlert.status == "open", FraudAlert.type == "cleaning_too_long").scalar() or 0
    pending_otp = db.query(func.count(OwnerOtp.id)).filter(
        OwnerOtp.used == False, OwnerOtp.expires_at > datetime.utcnow()).scalar() or 0

    return {
        "total": len(rows),
        "summary": {"open": int(open_total), "high_severity": int(high_open),
                    "cleaning_too_long": int(cleaning_open), "pending_otp": int(pending_otp)},
        "data": [_alert_dict(db, a, room_numbers) for a in rows],
    }


@router.get("/alerts/{alert_id}", dependencies=[Depends(require_admin)])
def get_alert(alert_id: int, db: Session = Depends(get_db)):
    a = db.query(FraudAlert).filter(FraudAlert.id == alert_id).first()
    if not a:
        raise HTTPException(status_code=404, detail="Alert not found")
    return _alert_dict(db, a)


@router.post("/alerts/{alert_id}/review")
def review_alert(alert_id: int, data: AlertReview, db: Session = Depends(get_db),
                 user=Depends(require_admin)):
    """Acknowledge (reviewed) or dismiss an alert with an optional note."""
    a = db.query(FraudAlert).filter(FraudAlert.id == alert_id).first()
    if not a:
        raise HTTPException(status_code=404, detail="Alert not found")
    before = {"status": a.status, "review_note": a.review_note}
    a.status = data.status
    a.review_note = data.note
    a.reviewed_by = _resolve_user_id(db, user)
    a.reviewed_at = datetime.utcnow()
    db.commit()
    write_audit(db, user, "fraud.alert_review", "fraud_alert", a.id,
                before=before, after={"status": a.status, "review_note": a.review_note},
                client="web", commit=True)
    return _alert_dict(db, a)


# ---------------------------------------------------------------------------
# reconciliation + report + digest + config
# ---------------------------------------------------------------------------

@router.post("/reconcile")
def reconcile(db: Session = Depends(get_db), user=Depends(require_admin)):
    """Run the detection sweep now and report how many new alerts were raised."""
    result = run_reconciliation(db)
    write_audit(db, user, "fraud.reconcile", "fraud_alert", None,
                after=result, client="web", commit=True)
    return result


@router.get("/reconciliation-report", dependencies=[Depends(require_admin)])
def reconciliation_report(db: Session = Depends(get_db)):
    """Every ACTIVE key card with its live card-vs-booking-vs-payment validation state."""
    # v4b1: prepaid-inclusive, so an OTA stay is not reported as an unpaid card.
    from routers.payments import total_paid_including_prepaid
    cards = db.query(CardIssuance).filter(CardIssuance.status == "active").order_by(
        CardIssuance.id.desc()).all()
    room_numbers = {r.room_id: r.room_number for r in db.query(Room).filter(
        Room.room_id.in_({c.room_id for c in cards if c.room_id})).all()} if cards else {}
    out = []
    for c in cards:
        booking = db.query(Booking).filter(Booking.booking_id == c.booking_id).first() if c.booking_id else None
        has_booking = bool(booking and booking.status == "checked_in")
        on_booking = bool(booking and c.room_id and db.query(BookingItem).filter(
            BookingItem.booking_id == booking.booking_id, BookingItem.room_id == c.room_id).first())
        paid = bool(booking and total_paid_including_prepaid(db, booking.booking_id) > 0)
        out.append({
            "card_id": c.id, "booking_id": c.booking_id, "room_id": c.room_id,
            "room_number": room_numbers.get(c.room_id), "card_uid": c.card_uid,
            "issue_type": c.issue_type, "station_id": c.station_id,
            "issued_at": str(c.issued_at) if c.issued_at else None,
            "has_checked_in_booking": has_booking, "room_on_booking": on_booking,
            "has_payment": paid,
            "matched": has_booking and on_booking and paid,
        })
    return {"total": len(out), "unmatched": sum(1 for r in out if not r["matched"]), "data": out}


@router.get("/digest", dependencies=[Depends(require_admin)])
def digest(db: Session = Depends(get_db), day: str = Query(None)):
    """Owner daily digest (occupancy / revenue / expected cash / open alerts).
    Viewable now; WhatsApp delivery is added in prompt 15."""
    d = None
    if day:
        try:
            d = datetime.strptime(day, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status_code=400, detail="day must be YYYY-MM-DD")
    return compute_daily_digest(db, d)


@router.get("/config", dependencies=[Depends(require_admin)])
def config(db: Session = Depends(get_db)):
    """Current detector thresholds + OTP toggles."""
    return get_config(db)


@router.put("/config")
def update_config(data: FraudConfigUpdate, db: Session = Depends(get_db),
                  user=Depends(require_admin)):
    """Edit the detector thresholds + approval gates (backlog v2 FE-12).

    These used to be env-only and read-only on screen, so arming the refund/discount/card
    approval gates meant a redeploy. Every enforcement site now reads the same settings
    helper, so what this screen shows is what the code applies. Audited."""
    before = get_config(db)
    changes = data.model_dump(exclude_unset=True)
    if not changes:
        return get_config(db)

    key_map = {
        "cleaning_max_hours": app_settings.FRAUD_CLEANING_MAX_HOURS_KEY,
        "cleaning_min_minutes": app_settings.FRAUD_CLEANING_MIN_MINUTES_KEY,
        "inspection_max_hours": app_settings.FRAUD_INSPECTION_MAX_HOURS_KEY,
        "allowed_issue_hours": app_settings.FRAUD_ALLOWED_ISSUE_HOURS_KEY,
        "allowed_stations": app_settings.FRAUD_ALLOWED_STATIONS_KEY,
        "repeat_refund_threshold": app_settings.FRAUD_REPEAT_REFUND_THRESHOLD_KEY,
        "refund_requires_owner_otp": app_settings.FRAUD_REFUND_OTP_KEY,
        "discount_otp_required": app_settings.FRAUD_DISCOUNT_OTP_KEY,
        "void_otp_required": app_settings.FRAUD_VOID_OTP_KEY,
        "card_issue_otp_required": app_settings.FRAUD_CARD_ISSUE_OTP_KEY,
        "owner_otp_ttl_minutes": app_settings.FRAUD_OTP_TTL_MINUTES_KEY,
        # v4b0
        "ac_downgrade_otp_required": app_settings.FRAUD_AC_DOWNGRADE_OTP_KEY,
        "alt_room_type_otp_required": app_settings.FRAUD_ALT_ROOM_TYPE_OTP_KEY,
        "comp_otp_required": app_settings.FRAUD_COMP_OTP_KEY,
        "overstay_reverse_otp_required": app_settings.FRAUD_OVERSTAY_REVERSE_OTP_KEY,
        "rs_cancel_otp_required": app_settings.FRAUD_RS_CANCEL_OTP_KEY,
        "checkout_no_card_otp_required": app_settings.FRAUD_CHECKOUT_NO_CARD_OTP_KEY,
        "ota_unverified_otp_required": app_settings.FRAUD_OTA_UNVERIFIED_OTP_KEY,
    }
    for field, value in changes.items():
        key = key_map.get(field)
        if not key:
            continue
        if isinstance(value, bool):
            stored = "true" if value else "false"
        elif isinstance(value, list):
            stored = ",".join(str(v).strip() for v in value if str(v).strip())
        else:
            stored = str(value)
        app_settings.set_setting(db, key, stored, user=user, commit=False)
    db.commit()

    after = get_config(db)
    write_audit(db, user, "fraud.config_update", "app_settings", None,
                before=before, after=after, client="web", commit=True)
    return after


# ---------------------------------------------------------------------------
# owner-approval OTP
# ---------------------------------------------------------------------------

@router.post("/otp/request")
def otp_request(data: OtpRequest, db: Session = Depends(get_db),
                user=Depends(require_reception_or_admin)):
    """Create an owner-approval code for a sensitive action. The code is NOT returned here —
    the owner reads it from the admin Approvals inbox (GET /fraud/otp) / WhatsApp, then the
    desk re-submits the action with owner_otp_id + code.

    v4b0: the action is validated against `owner_otp.OTP_ACTIONS` (a typo'd action used to
    sail through and mint a code nothing could ever consume), and the approval context is
    built SERVER-side from the ids the desk sends, so the owner reads the hotel's data
    rather than the client's."""
    if data.action not in OTP_ACTIONS:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown approval action '{data.action}'. Valid actions: "
                   + ", ".join(sorted(OTP_ACTIONS)))

    # Back-compat with the SHIPPED desk, which predates the typed fields and puts everything
    # in `context`: card_issue sends {booking_id, card_id, issue_type, room}, ac_downgrade
    # sends {booking_id, to_room_id}, refund sends {payment_id, amount}. Lifting those means
    # an un-updated desk build produces a fully named summary today, rather than
    # "guest / no booking" until each screen is migrated in a later batch.
    ctx_in = data.context or {}
    booking = rooms = folio = None
    booking_id = data.booking_id or ctx_in.get("booking_id")
    if not booking_id and ctx_in.get("payment_id"):
        booking_id = (db.query(Payment.booking_id)
                        .filter(Payment.payment_id == ctx_in["payment_id"]).scalar())

    room_ids = list(data.room_ids or [])
    for key in ("room_id", "to_room_id", "from_room_id"):
        rid = ctx_in.get(key)
        if isinstance(rid, int) and rid not in room_ids:
            room_ids.append(rid)

    if booking_id:
        booking = (db.query(Booking)
                     .options(joinedload(Booking.guest))
                     .filter(Booking.booking_id == booking_id).first())
    if not room_ids and booking is not None:
        # "Which room?" is the first thing the owner asks, so fall back to the rooms actually
        # assigned to the stay. Several shipped desk payloads name no room id at all (refund
        # sends only payment_id; card_issue sends `room` as a display STRING), and without
        # this they would read "no room assigned" for a guest who plainly has one.
        room_ids = [r for (r,) in db.query(BookingItem.room_id)
                                     .filter(BookingItem.booking_id == booking.booking_id,
                                             BookingItem.room_id.isnot(None)).all()]
    if room_ids:
        # Pass the ORM rows straight through — build_context resolves the type names itself
        # (`Room` has no room_type relationship, only BookingItem does).
        rooms = db.query(Room).filter(Room.room_id.in_(room_ids)).all()
    if data.folio_id:
        folio = db.query(Folio).filter(Folio.id == data.folio_id).first()
    elif booking is not None:
        folio = db.query(Folio).filter(Folio.booking_id == booking.booking_id).first()

    # safe_extras() drops the server-owned keys. Without it a client posting
    # {"booking": ...} in `context` would collide with build_context's own keyword
    # parameter and 500 the request.
    context = build_context(db, data.action, booking=booking, rooms=rooms, folio=folio,
                            amount=data.amount, user=user,
                            reasons=(data.context or {}).get("reasons"),
                            **safe_extras(data.context))

    otp, delivery = create_otp(db, data.action, context, user, return_delivery=True)
    write_audit(db, user, "fraud.otp_request", "owner_otp", otp.id,
                after={"action": data.action, "delivery": delivery.get("channel")},
                client="desktop", commit=True)
    return {"otp_id": otp.id, "action": otp.action,
            "expires_at": otp.expires_at.isoformat(),
            # Backlog v2 F-C: tell the desk which channel actually carried the code, so
            # the receptionist knows whether to expect a WhatsApp or to phone the owner.
            "delivery": delivery,
            "message": delivery.get("detail")
                       or "Owner approval requested. Ask the owner for the code from the admin dashboard."}


@router.post("/otp/{otp_id}/resend", dependencies=[Depends(require_admin)])
def otp_resend(otp_id: int, db: Session = Depends(get_db)):
    """Re-deliver a still-live approval code to the owner's WhatsApp (backlog v2 F-C).
    Does not mint a new code — the same one is pushed again."""
    otp = db.query(OwnerOtp).filter(OwnerOtp.id == otp_id).first()
    if not otp:
        raise HTTPException(status_code=404, detail="OTP not found")
    if otp.used:
        raise HTTPException(status_code=409, detail="That approval code has already been used")
    if otp.expires_at < datetime.utcnow():
        raise HTTPException(status_code=409, detail="That approval code has expired")
    return {"otp_id": otp.id, "delivery": deliver_otp(db, otp)}


@router.get("/otp", dependencies=[Depends(require_admin)])
def otp_list(db: Session = Depends(get_db), status: str = Query("pending")):
    """Approvals inbox. For pending codes this returns the CODE itself — the interim on-screen
    delivery to the owner (prompt 15 replaces this with WhatsApp)."""
    q = db.query(OwnerOtp)
    now = datetime.utcnow()
    if status == "pending":
        q = q.filter(OwnerOtp.used == False, OwnerOtp.expires_at > now)
    elif status == "used":
        q = q.filter(OwnerOtp.used == True)
    elif status == "expired":
        q = q.filter(OwnerOtp.used == False, OwnerOtp.expires_at <= now)
    rows = q.order_by(OwnerOtp.created_at.desc()).limit(100).all()

    def _row(o):
        ctx = _parse_detail(o.context) or {}
        return {
            "otp_id": o.id, "action": o.action,
            "context": ctx,
            # v4b0: lifted out of the context so the inbox has a headline without digging.
            # Older rows predate build_context and have no summary — the UI falls back to
            # the action name rather than showing a blank row.
            "summary": ctx.get("summary") if isinstance(ctx, dict) else None,
            "code": o.code if (not o.used and o.expires_at > now) else None,  # only reveal live codes
            "expires_at": o.expires_at.isoformat(),
            "used": o.used, "used_at": str(o.used_at) if o.used_at else None,
            "created_at": str(o.created_at) if o.created_at else None,
        }

    return {"total": len(rows), "data": [_row(o) for o in rows]}


@router.post("/otp/verify")
def otp_verify(data: OtpVerify, db: Session = Depends(get_db),
               user=Depends(require_reception_or_admin)):
    """Standalone verify (also consumed inline by the gated refund/discount endpoints).
    Consumes the code if valid — single-use."""
    otp = db.query(OwnerOtp).filter(OwnerOtp.id == data.otp_id).first()
    if not otp:
        raise HTTPException(status_code=404, detail="OTP not found")
    consume_otp(db, data.otp_id, data.code, otp.action, user)
    db.commit()
    return {"ok": True, "otp_id": otp.id, "action": otp.action}
