"""Key-card issuance records (card_issuances) — the anti-fraud backbone (prompt 06).

The desktop records every card in its local outbox BEFORE encoding, then posts here
(immediately when online, on sync when offline). The server re-validates every row:
  - valid                     -> record the issuance (status=active)
  - invalid but encoded=True  -> a live card already exists, so STILL record it AND
                                 raise a fraud_alert (owner review, prompt 11 dashboard)
  - invalid and encoded=False -> plain 409, nothing recorded (pre-check / encode never ran)
"""
import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from database import SessionLocal
from models import Booking, BookingItem, CardIssuance, FraudAlert, Room
from schemas import CardIssueRequest
from utils.audit import write_audit, _resolve_user_id
from utils.auth_utils import require_reception_or_admin
from routers.payments import total_paid

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/cards", tags=["Cards"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("/health", dependencies=[Depends(require_reception_or_admin)])
def health():
    return {"status": "ok", "module": "cards"}


def _active_count(db: Session, room_id: int) -> int:
    return db.query(func.count(CardIssuance.id)).filter(
        CardIssuance.room_id == room_id,
        CardIssuance.status == "active",
    ).scalar() or 0


def _issue_response(db: Session, card: CardIssuance, duplicate=False, flagged=False,
                    fraud_alert_id=None, reasons=None):
    room = db.query(Room).filter(Room.room_id == card.room_id).first() if card.room_id else None
    return {
        "card_id": card.id,
        "booking_id": card.booking_id,
        "room_id": card.room_id,
        "status": card.status,
        "issue_type": card.issue_type,
        "duplicate": duplicate,
        "flagged": flagged,
        "fraud_alert_id": fraud_alert_id,
        "reasons": reasons or [],
        "active_cards": _active_count(db, card.room_id) if card.room_id else None,
        "max_cards": room.max_cards if room else None,
    }


@router.post("/issue")
def issue_card(data: CardIssueRequest, db: Session = Depends(get_db),
               user=Depends(require_reception_or_admin)):
    try:
        # Idempotency: an outbox re-flush with the same client_ref returns the original.
        if data.client_ref:
            existing = db.query(CardIssuance).filter(
                CardIssuance.client_ref == data.client_ref).first()
            if existing:
                return _issue_response(db, existing, duplicate=True)

        booking = db.query(Booking).filter(Booking.booking_id == data.booking_id).first()
        room = db.query(Room).filter(Room.room_id == data.room_id).first()

        # ---- re-validate (collect every failure; decide afterwards) ----
        reasons = []
        if not booking:
            reasons.append("card_without_booking")
        elif booking.status != "checked_in":
            reasons.append("card_without_booking")  # not an in-house stay
        if booking and total_paid(db, booking.booking_id) <= 0:
            reasons.append("card_without_payment")
        if not room:
            reasons.append("card_without_booking")
        elif booking:
            assigned = db.query(BookingItem).filter(
                BookingItem.booking_id == booking.booking_id,
                BookingItem.room_id == room.room_id,
            ).first()
            if not assigned:
                reasons.append("card_without_booking")  # room not part of this stay

        # lost_reissue: retire the reported card first (its replacement overrides it
        # at the lock anyway), so it doesn't count against max_cards. A bad lost_card_id
        # only hard-rejects when no card was encoded — a live card must always be recorded.
        if data.issue_type == "lost_reissue":
            old_card = db.query(CardIssuance).filter(
                CardIssuance.id == data.lost_card_id).first() if data.lost_card_id else None
            if not old_card or old_card.booking_id != data.booking_id \
                    or old_card.room_id != data.room_id:
                if not data.encoded:
                    raise HTTPException(status_code=400,
                                        detail="lost_card_id does not match this booking/room")
                reasons.append("lost_reissue_mismatch")
            elif old_card.status == "active":
                old_card.status = "lost"

        if room and _active_count(db, room.room_id) >= (room.max_cards or 4):
            reasons.append("max_cards_exceeded")

        reasons = list(dict.fromkeys(reasons))  # dedupe, keep order

        # ---- invalid + no card encoded: plain reject, nothing recorded ----
        if reasons and not data.encoded:
            db.rollback()
            raise HTTPException(status_code=409, detail="; ".join(reasons))

        # ---- record the issuance (valid, or invalid-but-live-card) ----
        card = CardIssuance(
            booking_id=booking.booking_id if booking else None,
            room_id=room.room_id if room else None,
            card_uid=data.card_uid,
            card_type="guest",
            room_code=data.room_code,
            valid_from=data.valid_from,
            valid_to=data.valid_to,
            issued_by=_resolve_user_id(db, user),
            station_id=data.station_id,
            status="active",
            issue_type=data.issue_type,
            client_ref=data.client_ref,
        )
        db.add(card)
        db.flush()

        fraud_alert_id = None
        if reasons:
            # A live card exists that fails validation -> permanent record + owner alert.
            worst = "max_cards_exceeded" if reasons == ["max_cards_exceeded"] else reasons[0]
            alert = FraudAlert(
                type=worst,
                severity="med" if worst == "max_cards_exceeded" else "high",
                booking_id=booking.booking_id if booking else None,
                room_id=room.room_id if room else None,
                card_id=card.id,
                detail=json.dumps({
                    "reasons": reasons,
                    "station_id": data.station_id,
                    "offline": data.offline,
                    "client_issued_at": data.client_issued_at.isoformat() if data.client_issued_at else None,
                    "client_ref": data.client_ref,
                    "issue_type": data.issue_type,
                }),
                status="open",
            )
            db.add(alert)
            db.flush()
            fraud_alert_id = alert.id

        db.commit()

        action = "card.issue_flagged" if reasons else "card.issue"
        write_audit(db, user, action, "card", card.id,
                    after={"booking_id": data.booking_id, "room_id": data.room_id,
                           "issue_type": data.issue_type, "card_uid": data.card_uid,
                           "valid_to": data.valid_to.isoformat(),
                           "station_id": data.station_id, "offline": data.offline,
                           "reasons": reasons, "fraud_alert_id": fraud_alert_id,
                           "lost_card_id": data.lost_card_id,
                           "client_ref": data.client_ref},
                    client="desktop", commit=True)
        if reasons:
            logger.warning(f"⚠️ Flagged card issuance {card.id} (booking {data.booking_id}): {reasons}")

        return _issue_response(db, card, flagged=bool(reasons),
                               fraud_alert_id=fraud_alert_id, reasons=reasons)
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        logger.error(f"issue_card failed: {e}", exc_info=True)
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to record card issuance")


@router.get("/by-booking/{booking_id}")
def cards_by_booking(booking_id: int, db: Session = Depends(get_db),
                     user=Depends(require_reception_or_admin)):
    """All issuances for a booking (drives the desktop's Keys overlay + checkout)."""
    rows = db.query(CardIssuance).filter(
        CardIssuance.booking_id == booking_id,
    ).order_by(CardIssuance.id).all()
    room_numbers = {
        r.room_id: r.room_number
        for r in db.query(Room).filter(
            Room.room_id.in_({c.room_id for c in rows if c.room_id})).all()
    } if rows else {}
    return {"total": len(rows), "data": [{
        "card_id": c.id,
        "room_id": c.room_id,
        "room_number": room_numbers.get(c.room_id),
        "card_uid": c.card_uid,
        "issue_type": c.issue_type,
        "status": c.status,
        "room_code": c.room_code,
        "valid_from": c.valid_from.isoformat() if c.valid_from else None,
        "valid_to": c.valid_to.isoformat() if c.valid_to else None,
        "issued_at": str(c.issued_at) if c.issued_at else None,
        "station_id": c.station_id,
    } for c in rows]}
