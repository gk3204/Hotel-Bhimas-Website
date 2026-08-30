"""Automatic overstay billing (v4b3).

A guest still in the room past their checkout time picks up the next day's rent
automatically, and their key card is killed. Removing that charge needs the owner's approval
(POST /reception/overstay/{id}/reverse).

⚠️ THE GRACE PERIOD DELAYS THE ACTION; IT NEVER MOVES THE BILLED DAY.
The owner was explicit about this. The charged night runs from the booking's own checkout
moment — `booking_checkout_moment()`, i.e. the actual check-out time — so the guest is billed
`check_out + 1 day`, NOT `grace expiry + 1 day`. The grace is only a courtesy window before
the hotel acts. Mixing the two would quietly shift every overstay night by an hour and push
the card window with it.

    trigger  : now > checkout_moment + grace_minutes
    billed   : one night, and check_out advances by exactly 1 day from checkout_moment

THE INVARIANT EVERYTHING RESTS ON
    Non-void type='room' lines exist for exactly the nights in
    [booking.check_in, booking.check_out). Every writer that posts a night advances check_out
    in the SAME transaction; every writer that reverses one moves it back.

That is what makes this job idempotent across restarts without a job-run table: once it bills,
booking_checkout_moment() moves forward a full day, so the next tick no longer sees the booking
as overdue. **The state change IS the idempotency.** It survives redeploys and clock jumps
because it is derived from persisted state, not from "when did I last run".
"""
import json
import logging
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from models import Booking, CardIssuance, Folio, FraudAlert
from services import room_posting
from utils import settings as app_settings
from utils.audit import write_audit

logger = logging.getLogger(__name__)

# Arbitrary but fixed: the advisory-lock key for the whole sweep.
_SWEEP_LOCK_KEY = 0x00570A11


def _checkout_moment(booking: Booking) -> datetime:
    # Imported lazily: routers.reception imports services, so a module-level import here
    # would close the cycle.
    from routers.reception import booking_checkout_moment
    return booking_checkout_moment(booking)


def overstay_state(db: Session, booking: Booking, *, now=None, cfg=None) -> dict:
    """The shared read-model for "is this stay overdue, and by how much?".

    Used by the sweep AND by the desk board, the folio list and the owner dashboard, so the
    board and the biller can never disagree about who is overdue. Before v4b3 the desk derived
    it client-side from dates while reports.py used `check_out < today` — which in 24h mode
    flags a 22:00 check-in a full 22 hours early.
    """
    now = now or datetime.now()
    cfg = cfg or app_settings.get_overstay_config(db)
    if booking.status != "checked_in":
        # ⚠️ Same KEYS as the live branch below, always. This used to omit
        # `card_reencode_required` and `original_check_out`, so every caller that read them
        # KeyError'd on a stay that was not in house. The callers catch and degrade, so it
        # showed up only as a log line and a folio-list row that silently lost ALL of its
        # overstay fields — on the Reprint screen, where every row is a checked-out stay.
        return {"overdue": False, "nights_overdue": 0, "due_at": None,
                "grace_until": None, "auto_nights": 0, "auto_amount": 0.0,
                "original_check_out": (str(booking.original_check_out)
                                       if booking.original_check_out else None),
                "card_reencode_required": bool(booking.card_reencode_required)}

    due_at = _checkout_moment(booking)
    grace_until = due_at + timedelta(minutes=cfg["grace_minutes"])
    overdue = now > grace_until

    # How many whole days past the checkout moment — measured from the checkout moment
    # itself, NOT from grace expiry (see the module docstring).
    nights_overdue = max(0, int((now - due_at).total_seconds() // 86400) + (1 if overdue else 0))

    auto = _auto_charges(db, booking)
    return {
        "overdue": overdue,
        "nights_overdue": nights_overdue if overdue else 0,
        "due_at": due_at.isoformat(),
        "grace_until": grace_until.isoformat(),
        "auto_nights": len(auto),
        "auto_amount": round(sum(float(c.amount or 0) for c in auto), 2),
        "original_check_out": (str(booking.original_check_out)
                               if booking.original_check_out else None),
        "card_reencode_required": bool(booking.card_reencode_required),
    }


def _auto_charges(db: Session, booking: Booking):
    """Non-void room lines this job posted for a booking (for display and for the reversal)."""
    from models import FolioCharge
    folio = db.query(Folio).filter(Folio.booking_id == booking.booking_id).first()
    if not folio:
        return []
    return (db.query(FolioCharge)
              .filter(FolioCharge.folio_id == folio.id,
                      FolioCharge.type == "room",
                      FolioCharge.posting_reason == room_posting.REASON_OVERSTAY,
                      FolioCharge.reversal_of_id.is_(None),
                      FolioCharge.void == False)  # noqa: E712
              .order_by(FolioCharge.charge_date)
              .all())


def _alert(db: Session, *, type_, severity, booking, detail):
    """Raise a fraud alert, deduped on the booking + the night it concerns."""
    key = f"{type_}:{booking.booking_id}:{booking.check_out}"
    detail = dict(detail or {})
    detail["dedupe_key"] = key
    exists = (db.query(FraudAlert.id)
                .filter(FraudAlert.type == type_,
                        FraudAlert.booking_id == booking.booking_id,
                        FraudAlert.status == "open")
                .first())
    if exists:
        return None
    db.add(FraudAlert(type=type_, severity=severity, booking_id=booking.booking_id,
                      detail=json.dumps(detail, default=str), status="open"))
    return key


def _bill_one(db: Session, booking_id: int, *, now, cfg) -> dict:
    """Bill ONE booking one night, in its own transaction. Never raises — a single bad stay
    must not roll back the other nineteen."""
    out = {"booking_id": booking_id, "billed": False, "reason": None, "amount": 0.0}
    try:
        # Serialises against a concurrent /reception/extend, /reception/checkout or a second
        # sweep. No joinedload — FOR UPDATE cannot be applied to the nullable side of an
        # outer join (the trap that bit v4b2).
        booking = (db.query(Booking).filter(Booking.booking_id == booking_id)
                     .with_for_update().first())
        if not booking:
            out["reason"] = "gone"
            return out

        # Re-check every guard: state may have moved since the candidate query.
        st = overstay_state(db, booking, now=now, cfg=cfg)
        if not st["overdue"]:
            out["reason"] = "not_overdue"
            db.rollback()
            return out

        old_co = booking.check_out
        new_co = old_co + timedelta(days=1)

        # Runaway guard. A forgotten checkout — guest left days ago, nobody processed it —
        # must not silently accrue 40 nights of rent and 40 audit rows.
        booked_co = booking.original_check_out or old_co
        if (new_co - booked_co).days > cfg["max_auto_days"]:
            _alert(db, type_="overstay_unattended", severity="high", booking=booking,
                   detail={"booked_check_out": str(booked_co), "current_check_out": str(old_co),
                           "max_auto_days": cfg["max_auto_days"],
                           "note": "Automatic billing stopped — this stay needs a human."})
            db.commit()
            out["reason"] = "runaway_guard"
            return out

        folio = db.query(Folio).filter(Folio.booking_id == booking.booking_id).first()
        from routers.folio import _get_invoice, _recompute
        invoice = _get_invoice(db, folio.id) if folio else None
        billable = bool(folio) and folio.status == "open" and invoice is None

        comped = getattr(booking, "comp_mode", "none") in ("all", "room")

        if not billable:
            # The MONEY problem needs a human. The PHYSICAL SECURITY problem does not wait for
            # one — the guest is past their window with a live card either way — so we still
            # kill the card below, and raise an alert rather than silently dropping this.
            _alert(db, type_="overstay_unbillable", severity="med", booking=booking,
                   detail={"folio_id": folio.id if folio else None,
                           "folio_status": folio.status if folio else None,
                           "invoice_no": invoice.invoice_no if invoice else None,
                           "nights_overdue": st["nights_overdue"],
                           "note": "Guest is past checkout but the folio cannot take a charge."})
            _supersede_cards(db, booking)
            booking.card_reencode_required = True
            db.commit()
            out["reason"] = "unbillable"
            return out

        posted = {"amount": 0.0, "posted": []}
        if not comped:
            posted = room_posting.post_room_nights(
                db, booking, folio, old_co, new_co, user=None,
                posting_reason=room_posting.REASON_OVERSTAY,
                price_mode=room_posting.PRICE_QUOTE, recompute=False)

            # ⚠️ Nothing posted means this night's slot is already taken — and because the
            # unique index ignores `void`, "taken" includes a night the OWNER HAS JUST
            # REVERSED. Advancing check_out anyway would silently undo their reversal and
            # hand the guest a free night. Bill nothing, move nothing.
            # (The card is already dead from the reversal, and the board still shows the
            # stay as overdue, so the desk can see it.)
            if not posted["posted"]:
                db.rollback()
                logger.info(f"overstay: night {old_co} for booking {booking_id} is already "
                            f"settled or was reversed — not re-billing, not advancing")
                out["reason"] = "night_already_settled"
                return out

        if booking.original_check_out is None:
            booking.original_check_out = old_co
        booking.check_out = new_co
        booking.card_reencode_required = True
        superseded = _supersede_cards(db, booking)

        _recompute(db, folio)
        db.commit()

        # user=None: a system actor. write_audit / _resolve_user_id already handle it.
        write_audit(db, None, "reception.overstay_charge", "booking", booking.booking_id,
                    before={"check_out": str(old_co)},
                    after={"check_out": str(new_co), "amount": posted["amount"],
                           "posted_charge_ids": posted.get("posted", []),
                           "superseded_card_ids": superseded, "comped": comped,
                           "nights_overdue": st["nights_overdue"],
                           "due_at": st["due_at"], "grace_minutes": cfg["grace_minutes"]},
                    client="system", commit=True)

        out.update(billed=True, amount=posted["amount"], reason="comped" if comped else None)
        return out

    except IntegrityError as e:
        # The partial unique index caught a double-post (a concurrent sweep beat us to it).
        db.rollback()
        logger.info(f"overstay: night already billed for booking {booking_id} ({e.orig})")
        out["reason"] = "already_billed"
        return out
    except Exception as e:
        db.rollback()
        logger.error(f"overstay: billing booking {booking_id} failed: {e}", exc_info=True)
        out["reason"] = "error"
        return out


def _supersede_cards(db: Session, booking: Booking) -> list:
    """Kill the guest's live cards. Reuses the EXISTING `superseded` status deliberately —
    these strings are switched on by cards._active_count, reception._active_cards (the
    max_cards gate), cards_by_booking, the desk Keys overlay and utils/fraud_detection, so a
    new value would have to be taught to all of them. `superseded` already means "no longer
    the valid card for this stay", and it frees the max_cards slot, which is right: the re-cut
    is a replacement, not an additional key."""
    cards = db.query(CardIssuance).filter(
        CardIssuance.booking_id == booking.booking_id,
        CardIssuance.status == "active").all()
    for c in cards:
        c.status = "superseded"
    return [c.id for c in cards]


def sweep_overstays(db: Session, *, now=None, dry_run=False, generated_by="scheduler") -> dict:
    """Find every in-house stay past its checkout moment + grace, and bill one night each.

    Charges ONE day per tick, not "everything owed". A guest three days over is billed once
    per day as each grace expires, so every charge ties to one identifiable night and one
    audit row — which is exactly what the reversal route needs to undo them individually.
    """
    now = now or datetime.now()
    cfg = app_settings.get_overstay_config(db)
    result = {"ran_at": now.isoformat(), "dry_run": dry_run, "enabled": cfg["enabled"],
              "candidates": [], "billed": 0, "amount": 0.0, "skipped": []}

    if not cfg["enabled"] and not dry_run:
        result["skipped"].append("disabled")
        return result

    # ⚠️ The Dockerfile runs uvicorn with --workers 4, so FOUR copies of this scheduler exist
    # in four processes. The other four schedulers survive that by being upsert-idempotent; a
    # biller is not. This advisory lock is the single most important guard in the batch, and
    # it also covers a Railway rolling redeploy overlapping old and new instances.
    locked = False
    if not dry_run:
        locked = bool(db.execute(text("SELECT pg_try_advisory_lock(:k)"),
                                 {"k": _SWEEP_LOCK_KEY}).scalar())
        if not locked:
            logger.info("overstay sweep: another instance holds the lock, skipping")
            result["skipped"].append("locked")
            return result

    try:
        # Cheap pre-filter, then the exact test per booking. `check_out <= today` cannot miss
        # anyone: the real moment is always on or after that date.
        candidates = (db.query(Booking)
                        .filter(Booking.status == "checked_in",
                                Booking.check_out <= now.date())
                        .all())
        for b in candidates:
            st = overstay_state(db, b, now=now, cfg=cfg)
            if not st["overdue"]:
                continue
            row = {"booking_id": b.booking_id, "check_out": str(b.check_out),
                   "due_at": st["due_at"], "nights_overdue": st["nights_overdue"]}
            if dry_run:
                # Price it without touching anything, so the owner can see what WOULD happen.
                row["would_charge"] = _quote_one_night(db, b)
                result["candidates"].append(row)
                continue
            outcome = _bill_one(db, b.booking_id, now=now, cfg=cfg)
            row.update(outcome)
            result["candidates"].append(row)
            if outcome["billed"]:
                result["billed"] += 1
                result["amount"] = round(result["amount"] + outcome["amount"], 2)
    finally:
        if locked:
            try:
                db.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": _SWEEP_LOCK_KEY})
                db.commit()
            except Exception as e:                              # pragma: no cover
                logger.warning(f"overstay sweep: unlock failed: {e}")

    if dry_run:
        result["amount"] = round(sum(c.get("would_charge") or 0 for c in result["candidates"]), 2)
    logger.info(f"overstay sweep ({generated_by}): {len(result['candidates'])} candidate(s), "
                f"{result['billed']} billed, ₹{result['amount']:.2f}"
                + (" [DRY RUN]" if dry_run else ""))
    return result


def _quote_one_night(db: Session, booking: Booking) -> float:
    """What the next night would cost — priced, then rolled back. Dry-run only."""
    try:
        folio = db.query(Folio).filter(Folio.booking_id == booking.booking_id).first()
        if not folio:
            return 0.0
        preview = room_posting.post_room_nights(
            db, booking, folio, booking.check_out, booking.check_out + timedelta(days=1),
            user=None, posting_reason=room_posting.REASON_OVERSTAY,
            price_mode=room_posting.PRICE_QUOTE, recompute=False)
        return float(preview["amount"])
    except Exception as e:
        logger.info(f"overstay dry-run quote failed for booking {booking.booking_id}: {e}")
        return 0.0
    finally:
        db.rollback()
