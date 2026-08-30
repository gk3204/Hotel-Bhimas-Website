"""The one place that writes type='room' folio charges (v4b1).

Before this module `routers/folio.open_folio` was the ONLY writer of room charges and it
posted the whole stay in a single loop at check-in. v4 adds two more writers — extend-stay
(v4b2) and the automatic overstay charge (v4b3) — plus a reversal path, and all of them must
be able to answer "is this night already billed?" without charging a guest twice.

The invariant every caller upholds:

    Non-void type='room' lines exist for exactly the nights in
    [booking.check_in, booking.check_out). Every writer that posts a night advances
    check_out in the SAME transaction; every writer that reverses one moves it back.

That is what makes the overstay sweep idempotent across restarts without a job-run table:
after it bills, booking_checkout_moment() moves forward a day, so the next tick no longer
sees the booking as overdue. The state change IS the idempotency.

This module owns the posting half only. It deliberately does NOT commit and does NOT move
booking.check_out — the caller owns both, in one transaction.
"""
import logging
from datetime import date, timedelta

from fastapi import HTTPException
from sqlalchemy.orm import Session

from models import Booking, FolioCharge, RoomType
from utils.audit import _resolve_user_id
from utils.rate_engine import quote_stay

logger = logging.getLogger(__name__)

PRICE_BOOKING_SPLIT = "booking_split"
PRICE_QUOTE = "quote"

REASON_CHECKIN = "checkin"
REASON_EXTEND = "extend"
REASON_OVERSTAY = "overstay"


def booking_channel(booking: Booking) -> str:
    """The pricing channel a stay was sold on.

    Extracted from create_desk_booking (reception.py:143 — `channel = "agent" if agent else
    "walk_in"`) so an extension is priced on the SAME rate card the stay was sold on. Without
    it a walk-in extension could silently pick up an agent rate.
    """
    return "agent" if getattr(booking, "agent_id", None) else "walk_in"


def _days(date_from: date, date_to: date):
    d = date_from
    while d < date_to:
        yield d
        d += timedelta(days=1)


def _is_comped(booking: Booking) -> bool:
    """v4b6 adds Booking.comp_mode; getattr keeps this batch migration-independent."""
    return getattr(booking, "comp_mode", "none") in ("all", "room")


def assert_nights_identifiable(db: Session, folio_id: int):
    """Refuse to post against a folio whose existing room lines predate migration 028.

    Legacy room lines have charge_date IS NULL, and Postgres treats NULLs as distinct — so
    they never trip the unique index, but they also cannot be recognised as "already
    posted". Extending such a stay would silently double-charge the guest for nights they
    have already been billed for. A blocked extension is strictly better than that.
    """
    stale = db.query(FolioCharge.id).filter(
        FolioCharge.folio_id == folio_id,
        FolioCharge.type == "room",
        FolioCharge.reversal_of_id.is_(None),
        FolioCharge.charge_date.is_(None),
    ).first()
    if stale:
        raise HTTPException(
            status_code=409,
            detail=("This stay's room charges predate the per-night bookkeeping and cannot "
                    "be safely added to. Run backend/scripts/backfill_room_charge_dates.py "
                    "(dry-run first), then retry."))


def _quote_nights(db: Session, booking: Booking, items, nights) -> dict:
    """Price each night fresh through the rate engine, per booking item.

    Returns {booking_item_id: [{date, rate, source}]} where `rate` is GST-INCLUSIVE, to match
    the folio's convention that amounts already contain tax.

    ⚠️ Promotions are deliberately NOT re-applied. A promotion is a booking-window incentive;
    granting it again on a night the guest never originally booked is a giveaway with no
    approval trail behind it.
    """
    channel = booking_channel(booking)
    agent_id = getattr(booking, "agent_id", None)
    out = {}
    for item in items:
        rt = db.query(RoomType).filter(RoomType.room_type_id == item.room_type_id).first()
        gst = float(rt.gst_percent or 0) if rt else 0.0
        per_night = []
        for night in nights:
            q = quote_stay(db, rt, night, night + timedelta(days=1),
                           channel=channel, agent_id=agent_id, quantity=item.quantity)
            gross = round(float(q["base"]) * (1 + gst / 100), 2)
            per_night.append({"date": str(night), "rate": gross,
                              "source": (q.get("nightly") or [{}])[0].get("source")})
        out[item.booking_item_id] = per_night
    return out


def _split_booking_total(booking: Booking, item, nights) -> list:
    """The historical open_folio maths, byte-for-byte: split the item's GST-inclusive total
    across the stay's nights, the last night absorbing the remainder so the folio room total
    equals booking.total_amount to the paisa.

    Only meaningful over the WHOLE stay — it divides by the stay length, so a partial range
    would mis-price every line. Hence the guard.
    """
    span = max(1, (booking.check_out - booking.check_in).days)
    if len(nights) != span:
        raise ValueError(
            "price_mode='booking_split' prices the whole stay and must be called with "
            f"[check_in, check_out) — got {len(nights)} night(s), stay is {span}")
    item_total = float(item.total_amount or 0)
    per_night = round(item_total / span, 2)
    return [per_night if n < span - 1 else round(item_total - per_night * (span - 1), 2)
            for n in range(span)]


def _apply_override(amounts: list, override_total, quoted_grand: float) -> list:
    """Spread an agreed total across this item's nights, in proportion to what the item was
    quoted — so a multi-room extension does not dump the whole discount on the first room.
    Last night absorbs the remainder, so the folio still reconciles exactly."""
    if override_total is None or not amounts:
        return amounts
    if quoted_grand <= 0:
        return [0.0] * len(amounts)
    item_share = round(float(override_total) * sum(amounts) / quoted_grand, 2)
    each = round(item_share / len(amounts), 2)
    return [each] * (len(amounts) - 1) + [round(item_share - each * (len(amounts) - 1), 2)]


def post_room_nights(db: Session, booking: Booking, folio, date_from: date, date_to: date, *,
                     user=None,
                     posting_reason: str = REASON_CHECKIN,
                     price_mode: str = PRICE_QUOTE,
                     override_total: float | None = None,
                     recompute: bool = True,
                     enforce_backfilled: bool = True) -> dict:
    """Post one type='room' FolioCharge per (BookingItem, night) over [date_from, date_to).

    Idempotent per (booking_item_id, charge_date): a night already posted — **even a VOIDED
    one** — is skipped, never duplicated. A voided night keeps its slot deliberately, so the
    overstay sweep can never re-bill a night the owner has just reversed.

    price_mode:
      "booking_split"  the historical maths, used ONLY by open_folio: split each item's
                       GST-inclusive total across the nights so the folio room total equals
                       booking.total_amount to the paisa, last night absorbing the remainder.
      "quote"          price each night fresh through the rate engine (extend / overstay).
                       ⚠️ Promotions are NOT re-applied. A promo is a booking-window
                       incentive; re-applying it to a night the guest never originally
                       booked is a giveaway with no approval trail.

    override_total spreads an agreed amount (e.g. a discounted extension) across the new
    nights with the same last-night-absorbs-the-remainder rule, so the folio still
    reconciles exactly.

    Does NOT commit, and does NOT move booking.check_out — the caller owns both.
    """
    if price_mode not in (PRICE_BOOKING_SPLIT, PRICE_QUOTE):
        raise ValueError(f"unknown price_mode {price_mode!r}")
    if date_to <= date_from:
        return {"posted": [], "skipped": [], "amount": 0.0, "per_item": [], "comped": False}

    nights = list(_days(date_from, date_to))

    # Complimentary short-circuit (v4b6). The owner's decision was SUPPRESSION, not a 100%
    # discount line: posting a taxable line and discounting it away would create an output-tax
    # liability on a supply with no consideration. So no line, no discount row, no zero row.
    if _is_comped(booking):
        return {"posted": [], "skipped": [str(d) for d in nights], "amount": 0.0,
                "per_item": [], "comped": True}

    if enforce_backfilled:
        assert_nights_identifiable(db, folio.id)

    uid = _resolve_user_id(db, user) if user is not None else None

    # Which (item, night) slots are already taken. Voided rows are INCLUDED on purpose.
    taken = {
        (c.booking_item_id, c.charge_date)
        for c in db.query(FolioCharge.booking_item_id, FolioCharge.charge_date).filter(
            FolioCharge.folio_id == folio.id,
            FolioCharge.type == "room",
            FolioCharge.reversal_of_id.is_(None),
        ).all()
    }

    items = list(booking.booking_items)
    posted, skipped, per_item = [], [], []
    total_posted = 0.0

    quotes = _quote_nights(db, booking, items, nights) if price_mode == PRICE_QUOTE else {}
    quoted_grand = round(sum(n["rate"] for pn in quotes.values() for n in pn), 2)

    for item in items:
        rt = db.query(RoomType).filter(RoomType.room_type_id == item.room_type_id).first()
        label = rt.name if rt else item.room_type_id
        item_nightly, item_amount, item_posted = [], 0.0, []

        if price_mode == PRICE_BOOKING_SPLIT:
            amounts = _split_booking_total(booking, item, nights)
        else:
            amounts = _apply_override(
                [n["rate"] for n in quotes.get(item.booking_item_id, [])],
                override_total, quoted_grand)

        # --- post them -----------------------------------------------------------------
        for night, line_amount in zip(nights, amounts):
            key = (item.booking_item_id, night)
            if key in taken:
                skipped.append(str(night))
                continue
            qty = float(item.quantity or 1)
            charge = FolioCharge(
                folio_id=folio.id,
                type="room",
                description=f"Room {label} x{item.quantity} — {night:%d %b %Y}",
                qty=item.quantity,
                unit_price=round(line_amount / qty, 2) if qty else line_amount,
                amount=line_amount,
                gst_percent=float(rt.gst_percent) if rt else None,
                posted_by=uid,
                charge_date=night,
                booking_item_id=item.booking_item_id,
                posting_reason=posting_reason,
            )
            db.add(charge)
            item_posted.append(charge)
            item_amount = round(item_amount + line_amount, 2)
            total_posted = round(total_posted + line_amount, 2)
            src = None
            if price_mode == PRICE_QUOTE:
                src = next((n["source"] for n in quotes.get(item.booking_item_id, [])
                            if n["date"] == str(night)), None)
            item_nightly.append({"date": str(night), "rate": line_amount, "source": src})

        if item_nightly:
            per_item.append({
                "booking_item_id": item.booking_item_id,
                "room_id": item.room_id,
                "room_type_name": rt.name if rt else None,
                "nightly": item_nightly,
                "amount": item_amount,
            })
        posted.extend(item_posted)

    db.flush()   # so the caller sees ids, and so the unique index fires here not at commit
    if recompute:
        from routers.folio import _recompute      # local: routers.folio imports services
        _recompute(db, folio)

    return {
        "posted": [c.id for c in posted],
        "skipped": sorted(set(skipped)),
        "amount": round(total_posted, 2),
        "quoted_amount": round(quoted_grand, 2) if price_mode == PRICE_QUOTE else None,
        "per_item": per_item,
        "comped": False,
    }
