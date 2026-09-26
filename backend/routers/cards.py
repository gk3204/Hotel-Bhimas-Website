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
import os

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from database import SessionLocal
from models import Booking, BookingItem, CardIssuance, FolioCharge, FraudAlert, Room
from schemas import CardIssueRequest
from utils import settings as app_settings
from services import folio_resolver
from utils.audit import write_audit, _resolve_user_id
from utils.auth_utils import require_reception_or_admin
from utils.owner_otp import consume_otp
from routers.payments import total_paid, total_paid_including_prepaid

# Anti-fraud (ALT-1): reissuing a reported-lost card or cutting an EXTRA card beyond the
# check-in set are the two high-risk desk actions. When the card-issue gate is on they need
# an owner approval code — opt-in (default off) so existing behaviour is unchanged. The
# toggle is now admin-editable in Settings, defaulting to the CARD_ISSUE_OTP_REQUIRED env
# var (backlog v2 FE-12).
_OTP_GATED_ISSUE_TYPES = ("extra", "lost_reissue")


def _card_issue_otp_required(db) -> bool:
    return app_settings.get_fraud_config(db)["card_issue_otp_required"]

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
                    fraud_alert_id=None, reasons=None, fee_amount=0.0, fee_skipped=None):
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
        # v6f: what the guest was charged for losing the card, so the desk can say so out loud.
        # `fee_skipped` names the reason when nothing was posted — a zero fee configured, a
        # complimentary stay, a waiver, no open bill — because silence there reads as a bug.
        "fee_amount": fee_amount,
        "fee_charge_id": card.fee_charge_id,
        "fee_skipped": fee_skipped,
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
        # v4b1: counts a prepayment collected by the channel that sold the stay. Without it
        # every OTA guest was refused a key card as "unpaid" — they had paid, just not to us.
        # v5i: a complimentary stay owes nothing at check-in, so a key card for it is not fraud.
        is_comp = bool(booking) and (getattr(booking, "comp_mode", "none") or "none") in ("all", "room")
        if booking and not is_comp and total_paid_including_prepaid(db, booking.booking_id) <= 0:
            reasons.append("card_without_payment")
        # v6f: initialised, because the lost-card fee below asks which ROOM of the stay this is
        # and the branch that sets it does not always run.
        assigned = None
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

        # Owner-approval gate for reissue / extra cards (ALT-1). A FAILED/absent approval is
        # treated like any other validation reason: it hard-rejects a pre-check (encoded=False,
        # nothing cut yet) but a card that is ALREADY encoded is still recorded and flagged —
        # a live card is never silently dropped. Consumed here so the code is only spent if the
        # issuance commits. Opt-in via CARD_ISSUE_OTP_REQUIRED (default off).
        if data.issue_type in _OTP_GATED_ISSUE_TYPES and _card_issue_otp_required(db):
            try:
                consume_otp(db, data.owner_otp_id, data.owner_otp_code, "card_issue", user)
            except HTTPException:
                reasons.append("card_issue_without_approval")

        # A key-lock room has a physical metal key — a guest key card should never be cut for it.
        if room and getattr(room, "lock_type", "card") == "key":
            reasons.append("card_on_keylock_room")

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

        # ---- v6f: charge for the lost card ----------------------------------------------------
        # The registration slip has promised "a lost/unreturned card is chargeable" since v4b1 and
        # nothing ever billed it. Posted here, INSIDE the transaction that records the card, so a
        # card and its fee can never exist apart.
        #
        # Idempotent on the LOST CARD, not on the request: /cards/issue legitimately runs twice for
        # one reissue (Card Management pre-checks with encoded=false, and a valid pre-check records
        # a row), and `client_ref` only dedupes an outbox re-flush. One fee per lost card, ever.
        fee_amount, fee_skipped = 0.0, None
        if data.issue_type == "lost_reissue" and old_card is not None:
            card.replaces_card_id = old_card.id
            cfg = app_settings.get_frontdesk_config(db)
            price = round(float(cfg.get("lost_card_fee_amount") or 0), 2)
            already = db.query(CardIssuance).filter(
                CardIssuance.replaces_card_id == old_card.id,
                CardIssuance.fee_charge_id.isnot(None),
                CardIssuance.id != card.id).first()
            if data.fee_waived:
                if not (data.fee_waive_reason or "").strip():
                    raise HTTPException(status_code=400,
                                        detail="Waiving the lost-card fee needs a reason")
                fee_skipped = "waived: " + data.fee_waive_reason.strip()
            elif price <= 0:
                fee_skipped = "no lost-card fee is configured"
            elif is_comp:
                fee_skipped = "complimentary stay"
            elif already is not None:
                fee_skipped = f"already charged on card {already.id}"
            elif booking is None:
                fee_skipped = "no booking to bill"
            else:
                _folio = folio_resolver.folio_for_item(db, booking, assigned) \
                    if assigned is not None else folio_resolver.primary_folio(db, booking)
                if _folio is None:
                    fee_skipped = "no folio for this stay"
                elif _folio.status != "open":
                    # Same rule as the minibar charge: a settled bill takes no more money. The loss
                    # is still recorded; the desk collects at the counter.
                    fee_skipped = "the bill is already settled"
                else:
                    _label = f" — room {room.room_number}" if room else ""
                    _fee = FolioCharge(
                        folio_id=_folio.id,
                        type="misc",
                        description=f"Lost key card{_label}",
                        qty=1,
                        unit_price=price,
                        amount=price,
                        gst_percent=round(float(cfg.get("lost_card_fee_gst_percent") or 0), 2),
                        posted_by=_resolve_user_id(db, user),
                        posting_reason="lost_card",
                        room_id=room.room_id if room else None,
                    )
                    db.add(_fee)
                    db.flush()
                    card.fee_charge_id = _fee.id
                    fee_amount = price
                    from routers.folio import _recompute
                    _recompute(db, _folio)

        # v4b2: a stay whose dates moved (a manual extension, or v4b3's automatic overstay
        # charge) is flagged as needing its card re-cut. Recording a card for that booking is
        # what clears the flag — so the desk board stops nagging the moment the guest has a
        # working key again. Only a genuinely valid issue clears it: a flagged card means the
        # stay still has a problem, so leave the flag up for someone to deal with.
        if booking is not None and not reasons and getattr(booking, "card_reencode_required", False):
            booking.card_reencode_required = False

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
                           "fee_amount": fee_amount, "fee_charge_id": card.fee_charge_id,
                           "fee_skipped": fee_skipped,
                           "client_ref": data.client_ref},
                    client="desktop", commit=True)
        if reasons:
            logger.warning(f"⚠️ Flagged card issuance {card.id} (booking {data.booking_id}): {reasons}")

        return _issue_response(db, card, flagged=bool(reasons),
                               fraud_alert_id=fraud_alert_id, reasons=reasons,
                               fee_amount=fee_amount, fee_skipped=fee_skipped)
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
