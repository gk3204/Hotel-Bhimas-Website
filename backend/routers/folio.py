"""Folio / billing endpoints (prompt 07: per-stay charges, food posting, GST invoices).

Conventions (see docs/PROJECT_STATE.md, prompt 07):
- FolioCharge.amount is the authoritative SIGNED, GST-INCLUSIVE value
  (charges +, discounts/payments -). gst_percent records the slab for the
  invoice CGST/SGST split only; qty/unit_price are informational.
  v5i: for FOOD lines unit_price is the EX-GST menu price (menu prices exclude GST) and
  amount = qty x unit_price + GST; the invoice derives its Rate (excl. GST) / GST / Amount
  (incl. GST) columns from `amount` + `gst_percent` so every line type reads the same.
- Void never hard-deletes: the original line gets void=True + reason AND a
  reversing line (amount negated, reversal_of_id set, also void=True) is
  appended for the paper trail. All totals sum only void == False lines.
- Room charges for the WHOLE stay are posted at folio open (one line per
  night per booking item, split so the folio room total equals
  booking.total_amount to the paisa). Prompt 06 check-in reuses /folio/open.
- Creating an invoice does NOT settle the folio, but freezes it: further
  charge/void/discount return 409. Settlement = checkout (06) / payments (08).
- Advance: paid Payment rows post as type="payment" credit lines at open.
- Voids honour FOLIO_VOID_REQUIRES_ADMIN (default true -> admin JWT needed).
"""
import json
import logging
import os
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import text
from sqlalchemy.orm import Session, joinedload

from database import SessionLocal
from models import (Booking, BookingItem, Company, EInvoice, Folio, FolioCharge, Invoice,
                    Payment, PaymentAllocation, RoomType)
from schemas import (FolioBillToRequest, FolioChargeCreate, FolioDiscountRequest, FolioOpenRequest,
                     FolioVoidRequest, InvoiceBuyerRequest)
from services import company_service, folio_resolver, room_posting
from utils import gst
from utils import ordering
from utils import settings as app_settings
from utils.audit import write_audit, _resolve_user_id
from utils.auth_utils import get_current_user, require_admin, require_reception_or_admin
from utils.owner_otp import consume_otp

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/folio", tags=["Folio"])

# Booking statuses a folio may be opened against (in-house or confirmed stay).
FOLIO_BOOKING_STATUSES = ["confirmed", "checked_in"]


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("/health", dependencies=[Depends(require_reception_or_admin)])
def health():
    return {"status": "ok", "module": "folio"}


# ---------------------------------------------------------------- helpers

def require_void_permission(user=Depends(get_current_user)):
    """Void gate: FOLIO_VOID_REQUIRES_ADMIN (default true) -> admin only.
    Read at call time so the flag can be changed without a restart."""
    requires_admin = os.getenv("FOLIO_VOID_REQUIRES_ADMIN", "true").strip().lower() not in ("false", "0", "no")
    if requires_admin:
        if user.get("role") != "admin":
            raise HTTPException(status_code=403, detail="Void requires admin approval")
    elif user.get("role") not in ["admin", "reception"]:
        raise HTTPException(status_code=403, detail="Access denied")
    return user


def _fy_label(d: date) -> str:
    """Indian financial year label, e.g. 2026-07-11 -> '2026-27' (Apr-Mar)."""
    y = d.year if d.month >= 4 else d.year - 1
    return f"{y}-{(y + 1) % 100:02d}"


def _allocate_invoice_seq(db: Session, fy: str) -> int:
    """Atomically allocate the next per-FY invoice sequence (Postgres upsert)."""
    return db.execute(
        text("INSERT INTO invoice_counters (fy_label, last_seq) VALUES (:fy, 1) "
             "ON CONFLICT (fy_label) DO UPDATE SET last_seq = invoice_counters.last_seq + 1 "
             "RETURNING last_seq"),
        {"fy": fy},
    ).scalar()


def _active_charges(db: Session, folio_id: int):
    """Non-void lines of a folio, in POSTING order.

    The order_by is not cosmetic: this feeds invoice creation and `_invoice_payload`, so without it
    the printed GST invoice's line order came from whatever the executor returned and two prints of
    the same invoice could differ. The on-screen folio has always ordered by id (see the detail
    endpoint below), so screen and tax document could disagree as well.
    """
    return db.query(FolioCharge).filter(
        FolioCharge.folio_id == folio_id,
        FolioCharge.void == False,  # noqa: E712
    ).order_by(FolioCharge.id).all()


def _recompute(db: Session, folio: Folio):
    """Recompute folio total/balance from non-void lines (call after every mutation).
    SessionLocal has autoflush=False, so flush pending lines before summing.

    total/balance keep their ORIGINAL whole-folio meaning. The corporate mirror
    (company_total/company_balance, prompt 18 slice 7) is refreshed here too, so every existing
    mutation path stays correct without knowing anything about companies."""
    db.flush()
    lines = _active_charges(db, folio.id)
    folio.total = round(sum(float(c.amount) for c in lines if c.type != "payment"), 2)
    folio.balance = round(sum(float(c.amount) for c in lines), 2)
    company_service.recompute_company_totals(db, folio)


def _get_invoice(db: Session, folio_id: int):
    return db.query(Invoice).filter(Invoice.folio_id == folio_id).first()


def _ensure_editable(db: Session, folio: Folio):
    if folio.status != "open":
        raise HTTPException(status_code=409, detail="Folio is not open")
    if _get_invoice(db, folio.id):
        raise HTTPException(status_code=409, detail="Folio already invoiced — no further changes allowed")


def _ensure_chargeable(db: Session, folio: Folio):
    """`_ensure_editable`, plus the v4b6 complimentary gate.

    A `comp_mode='all'` stay takes no new charges at all — the owner said everything is free,
    so posting one and expecting somebody to notice is worse than refusing. `comp_mode='room'`
    charges extras normally: only the room is free."""
    _ensure_editable(db, folio)
    booking = db.query(Booking).filter(Booking.booking_id == folio.booking_id).first()
    if booking is not None and getattr(booking, "comp_mode", "none") == "all":
        raise HTTPException(
            status_code=422,
            detail="This stay is complimentary (all charges) — nothing can be posted to it. "
                   "Clear the complimentary status first if the guest should be billed.")


def _overstay_slice(db: Session, booking: Booking, cfg=None) -> dict:
    """Overstay flags for a folio-list row (v4b3). Best-effort — the stays list must not fail
    to load because the overstay read-model hiccuped."""
    try:
        from services.overstay_billing import overstay_state
        st = overstay_state(db, booking, cfg=cfg)
        return {"overdue": st["overdue"], "nights_overdue": st["nights_overdue"],
                "auto_charged_nights": st["auto_nights"],
                "auto_charged_amount": st["auto_amount"],
                "card_reencode_required": st["card_reencode_required"]}
    except Exception as e:                                    # pragma: no cover - defensive
        logger.warning(f"folio list overstay slice failed for {booking.booking_id}: {e}")
        return {}


def _void_charge_row(db: Session, charge: FolioCharge, reason: str, user) -> FolioCharge:
    """Mark a charge void + append its reversing mirror. Never hard-deletes.

    Extracted in v4b1 so the generic void endpoint, the overstay reversal (v4b3) and the
    retro-complimentary path (v4b6) share exactly ONE implementation of what voiding means.
    Does not commit and does not recompute — the caller owns the transaction.

    ⚠️ The reversal deliberately carries NO charge_date / booking_item_id / posting_reason.
    It is a bookkeeping mirror, not a night. Copying them would collide with the partial
    unique index uq_folio_room_night (which excludes reversals via reversal_of_id IS NULL),
    and would make the voided night look re-postable.
    """
    charge.void = True
    charge.void_reason = reason
    reversal = FolioCharge(
        folio_id=charge.folio_id,
        type=charge.type,
        description=f"REVERSAL of #{charge.id}: {charge.description}",
        qty=charge.qty,
        unit_price=charge.unit_price,
        amount=-float(charge.amount),
        gst_percent=charge.gst_percent,
        posted_by=_resolve_user_id(db, user),
        void=True,
        void_reason=f"reversal of #{charge.id}: {reason}",
        reversal_of_id=charge.id,
    )
    db.add(reversal)
    return reversal


def _get_folio(db: Session, folio_id: int) -> Folio:
    folio = db.query(Folio).filter(Folio.id == folio_id).first()
    if not folio:
        raise HTTPException(status_code=404, detail="Folio not found")
    return folio


def _invoice_totals(charges):
    """GST math over NON-VOID lines. Amounts are GST-inclusive; CGST = SGST = slab GST / 2.

    v6d (F-11): a discount now reduces the **taxable value**, apportioned across the slabs, rather
    than being knocked off after tax — the treatment the owner's accountant confirmed. The
    arithmetic lives in `utils/gst.split` because the GST report, the Tally export and the
    corporate bill must produce the same figures as this invoice; see that module for why.
    """
    charge_lines = [c for c in charges if c.type not in ("payment", "discount")]
    discount_lines = [c for c in charges if c.type == "discount"]
    payment_lines = [c for c in charges if c.type == "payment"]

    charges_total = round(sum(float(c.amount) for c in charge_lines), 2)
    discount_total = round(sum(float(c.amount) for c in discount_lines), 2)   # negative
    payments_total = round(sum(float(c.amount) for c in payment_lines), 2)    # negative

    split = gst.split(((c.gst_percent, c.amount) for c in charge_lines),
                      discount=discount_total)

    # The grand total is taken from the split rather than `charges_total + discount_total` so that
    # the per-slab rows printed on the invoice always add up to the amount demanded, to the paisa.
    grand_total = split["net_total"]
    balance_due = round(grand_total + payments_total, 2)
    return {
        "gst_rows": split["rows"],
        "charges_total": charges_total,
        "discount_total": discount_total,
        "taxable_total": split["taxable_total"],
        "cgst_total": split["cgst_total"],
        "sgst_total": split["sgst_total"],
        "grand_total": grand_total,
        "payments_total": payments_total,
        "balance_due": balance_due,
    }


def _room_numbers(booking) -> list:
    """Physical room numbers on a booking, in ROOM ORDER. A booking always reaches its rooms via
    BookingItem.room_id (there is no Booking->Room relationship), and a room is only assigned
    at check-in — so a confirmed-but-not-arrived stay legitimately has none.

    v5s: "in order" used to mean booking-item order, so a two-room stay could read "10, 9". Every
    room list on the folio screen and the folio detail comes through here, so ordering it once here
    orders all of them.
    """
    if not booking:
        return []
    return ordering.room_labels(bi.room.room_number for bi in booking.booking_items if bi.room)


def _charge_room(charge: FolioCharge, rooms_by_item: dict, rooms_by_id: dict) -> dict:
    """The room a folio line belongs to, as `{room_id, room_number}` (both None when it has none).

    F-14. Two sources, and both already existed — neither was ever returned. `folio_charges.room_id`
    is set directly by room service (v5n: "which room ate it"), and room nights carry
    `booking_item_id`, which after check-in IS one room. Prefer the explicit column; fall back to the
    item. Lines that genuinely belong to no room — a folio-level discount, a payment — return None,
    and the UI should show those against the stay rather than inventing a room for them.
    """
    room = rooms_by_id.get(getattr(charge, "room_id", None)) \
        or rooms_by_item.get(getattr(charge, "booking_item_id", None))
    return {"room_id": room.room_id if room else None,
            "room_number": room.room_number if room else None}


def _room_subtotals(charges, rooms_by_item, rooms_by_id) -> list:
    """What each room on this stay owes, and whether it is clear (F-14 / F-15).

    Completes F-14: Batch A put the room on every line, this adds the arithmetic on top. It is what
    lets ONE room settle and leave while the others stay open, which is the whole point of a
    per-room stay - the desk can answer "what does room 12 owe?" without reading the bill sideways.

    A line with no room - a folio-level discount, an advance payment against the stay - is reported
    under `room_number: None` as the stay's own share, rather than being smeared across rooms it may
    have nothing to do with. Deliberate: guessing would make a per-room settlement wrong in a way
    nobody could see.
    """
    buckets = {}
    for c in charges:
        if c.void:
            continue
        rm = _charge_room(c, rooms_by_item, rooms_by_id)
        key = rm["room_id"]
        b = buckets.setdefault(key, {"room_id": key, "room_number": rm["room_number"],
                                     "charges": 0.0, "payments": 0.0})
        if c.type == "payment":
            b["payments"] = round(b["payments"] + float(c.amount), 2)     # negative
        else:
            b["charges"] = round(b["charges"] + float(c.amount), 2)
    out = []
    for b in buckets.values():
        b["balance"] = round(b["charges"] + b["payments"], 2)
        b["settled"] = abs(b["balance"]) < 0.01
        out.append(b)
    # Rooms first, in room order; the stay's own unattributed share last.
    out.sort(key=lambda r: (r["room_number"] is None,
                            ordering.room_number_sort_key(r["room_number"] or "")))
    return out


def _folio_detail(db: Session, folio: Folio):
    """Shared response builder: folio + booking/guest summary + all lines + GST totals."""
    from routers.payments import prepaid_slice      # local: payments imports this module
    booking = db.query(Booking).options(
        joinedload(Booking.guest),
        joinedload(Booking.booking_items).joinedload(BookingItem.room),
    ).filter(Booking.booking_id == folio.booking_id).first()
    charges = db.query(FolioCharge).filter(
        FolioCharge.folio_id == folio.id
    ).order_by(FolioCharge.id).all()
    totals = _invoice_totals([c for c in charges if not c.void])
    invoice = _get_invoice(db, folio.id)
    company = company_service.get_company(db, folio.company_id)
    company_balance = float(folio.company_balance or 0)
    # F-14: booking_item_id -> the room it is assigned to. Built once here rather than per charge,
    # from the items already eager-loaded above, so naming the room on every line costs no queries.
    _items = booking.booking_items if booking else []
    rooms_by_item = {i.booking_item_id: i.room for i in _items if i.room is not None}
    rooms_by_id = {i.room.room_id: i.room for i in _items if i.room is not None}
    return {
        "folio_id": folio.id,
        "booking_id": folio.booking_id,
        "status": folio.status,
        "total": float(folio.total or 0),
        "balance": float(folio.balance or 0),
        # --- corporate split (prompt 18 slice 7). guest_balance is DERIVED, never stored. ---
        "company_id": folio.company_id,
        "company_name": company.name if company else None,
        "company_total": float(folio.company_total or 0),
        "company_balance": company_balance,
        "guest_balance": round(float(folio.balance or 0) - company_balance, 2),
        "opened_at": str(folio.opened_at) if folio.opened_at else None,
        "guest_name": booking.display_guest_name if booking else None,
        "phone": booking.guest.phone if booking and booking.guest else None,
        "email": booking.guest.email if booking and booking.guest else None,
        # v3 item 7: the room number is the first thing the desk looks for when a guest
        # queries their bill. List + pre-joined label (a stay can hold several rooms).
        "room_numbers": _room_numbers(booking),
        "room_number": ", ".join(_room_numbers(booking)) or None,
        "check_in": str(booking.check_in) if booking else None,
        "check_out": str(booking.check_out) if booking else None,
        # Expected arrival vs what actually happened (FE-1) — the folio header is where the
        # desk looks when a guest queries their bill, so it needs the real times.
        "check_in_time": str(booking.check_in_time) if booking and booking.check_in_time else None,
        "checked_in_at": booking.checked_in_at.isoformat() if booking and booking.checked_in_at else None,
        "checked_out_at": booking.checked_out_at.isoformat() if booking and booking.checked_out_at else None,
        "booking_status": booking.status if booking else None,
        # v4b6: so the bill header can say "Complimentary" before anyone asks for money.
        "comp_mode": (getattr(booking, "comp_mode", "none") or "none") if booking else "none",
        "comp_reason": getattr(booking, "comp_reason", None) if booking else None,
        # v4b9 (R9): a prepaid stay shows a small balance because the ROOM is already paid for.
        # Without saying so on the header, ₹0 reads as "nothing to collect, ever" and a real
        # extras balance reads as the whole bill.
        "booking_source": booking.booking_source if booking else None,
        **prepaid_slice(db, booking),
        "invoice_no": invoice.invoice_no if invoice else None,
        "invoice_date": str(invoice.invoice_date) if invoice else None,
        "charges": [{
            "charge_id": c.id,
            "type": c.type,
            "description": c.description,
            "qty": float(c.qty or 0),
            "unit_price": float(c.unit_price or 0),
            "amount": float(c.amount),
            "gst_percent": float(c.gst_percent) if c.gst_percent is not None else None,
            "posted_at": str(c.posted_at) if c.posted_at else None,
            "void": bool(c.void),
            "void_reason": c.void_reason,
            "reversal_of_id": c.reversal_of_id,
            "bill_to": c.bill_to or "guest",
            # F-14: WHICH ROOM this line belongs to. On a multi-room stay the folio showed two
            # identical "Room Non AC Standard x1 — 25 Sep" lines and a room-service line that had
            # forgotten the room it was delivered to, so nothing downstream could attribute a charge,
            # split a bill, or settle one room. The information was never missing — room nights store
            # `folio_charges.booking_item_id` and after check-in each item is exactly one room — it
            # was simply never returned.
            **_charge_room(c, rooms_by_item, rooms_by_id),
        } for c in charges],
        **{k: v for k, v in totals.items() if k != "gst_rows"},
        "gst_rows": totals["gst_rows"],
        # F-14: what each room owes. The desk settles and releases one room at a time from this.
        "room_subtotals": _room_subtotals(charges, rooms_by_item, rooms_by_id),
        # v6e: which bill this is, and the others on the same stay. A desk looking at room 62's
        # bill needs to see at a glance that rooms 61 and 63 have their own, and what they owe -
        # otherwise a guest asking "is everything settled?" gets an answer about one room.
        "billing_mode": (booking.billing_mode if booking else "group"),
        "booking_item_id": folio.booking_item_id,
        "room_number": next((i.room.room_number for i in _items
                             if i.booking_item_id == folio.booking_item_id and i.room is not None),
                            None),
        "bills": [{"folio_id": f2.id,
                   "booking_item_id": f2.booking_item_id,
                   "room_number": next((i.room.room_number for i in _items
                                        if i.booking_item_id == f2.booking_item_id
                                        and i.room is not None), None),
                   "status": f2.status,
                   "total": float(f2.total or 0),
                   "balance": float(f2.balance or 0),
                   "is_this_one": f2.id == folio.id}
                  for f2 in folio_resolver.folios_for_booking(db, folio.booking_id)],
    }


# ---------------------------------------------------------------- endpoints

@router.get("/")
def list_folios(db: Session = Depends(get_db), user=Depends(require_reception_or_admin),
                scope: str = Query("inhouse", pattern="^(inhouse|history|all)$"),
                limit: int = Query(300, ge=1, le=1000)):
    """Desk stays list.

    v4b1 (R3): the default used to be `confirmed OR folio open`, so a bill appeared on the
    Folio screen before the guest had arrived and lingered after they left. The owner asked
    for a folio to be there only between check-in and check-out — which is also the only
    window in which a receptionist can act on it, since a confirmed booking has no room
    assigned yet and a checked-out one is settled AND invoiced (frozen by _ensure_editable).

    ⚠️ `scope` exists because the **Reprint** screen calls this same endpoint, not just the
    same DTO. Narrowing the default without it would have made every settled bill and invoice
    unreachable for reprinting — the one thing that screen is for.
      inhouse  (default) currently checked-in stays          -> Folio screen
      history            checked-out stays, most recent first -> Reprint screen
      all                both                                 -> anything that wants the lot
    """
    q = (
        db.query(Booking, Folio)
        .outerjoin(Folio, Folio.booking_id == Booking.booking_id)
        .options(joinedload(Booking.guest),
                 joinedload(Booking.booking_items).joinedload(BookingItem.room_type),
                 joinedload(Booking.booking_items).joinedload(BookingItem.room))
    )
    if scope == "inhouse":
        rows = (q.filter(Booking.status == "checked_in")
                 .order_by(Booking.check_in, Booking.booking_id).all())
        # v5s, owner's explicit choice: the in-house list reads by ROOM NUMBER, not arrival time.
        # Sorted here rather than in SQL because the rooms arrive through booking_items (already
        # joinedload-ed above) and a stay may hold several — it sorts on its lowest room. A stay
        # with no room yet assigned sorts last.
        rows.sort(key=lambda r: ordering.room_number_sort_key(
            (_room_numbers(r[0]) or [""])[0]))
    elif scope == "history":
        rows = (q.filter(Booking.status == "checked_out")
                 .order_by(Booking.check_out.desc(), Booking.booking_id.desc())
                 .limit(limit).all())
    else:
        rows = (q.filter(Booking.status.in_(["checked_in", "checked_out"]))
                 .order_by(Booking.check_out.desc(), Booking.booking_id.desc())
                 .limit(limit).all())
    from routers.payments import prepaid_slice      # local: payments imports this module
    ov_cfg = app_settings.get_overstay_config(db)   # read once for the whole list
    data = []
    for booking, folio in rows:
        invoice = _get_invoice(db, folio.id) if folio else None
        room_types = ", ".join(
            f"{bi.room_type.name} x{bi.quantity}" for bi in booking.booking_items if bi.room_type
        )
        rooms = _room_numbers(booking)
        data.append({
            "booking_id": booking.booking_id,
            "guest_name": booking.display_guest_name,
            "phone": booking.guest.phone if booking.guest else None,
            "check_in": str(booking.check_in),
            "check_out": str(booking.check_out),
            "booking_status": booking.status,
            "comp_mode": getattr(booking, "comp_mode", "none") or "none",
            # v4b9 R9/R13 — the stays list is scanned before the header is opened.
            "booking_source": booking.booking_source,
            **prepaid_slice(db, booking),
            "room_types": room_types,
            # v3 item 7 — room NUMBER, distinct from room_types above (which is the room-type
            # name). The desk sorts and searches its stays list by this.
            "room_numbers": rooms,
            "room_number": ", ".join(rooms) or None,
            "grand_total": float(booking.grand_total or 0),
            "folio_id": folio.id if folio else None,
            "folio_status": folio.status if folio else None,
            "folio_total": float(folio.total or 0) if folio else None,
            "balance": float(folio.balance or 0) if folio else None,
            "invoice_no": invoice.invoice_no if invoice else None,
            # v4b3: a receptionist opening the Folio screen should see an overstay there too,
            # not only on the board.
            **_overstay_slice(db, booking, ov_cfg),
        })
    return {"total": len(data), "data": data}


def _split_amount(total: float, n: int) -> list:
    """Split a rupee figure n ways, the last part absorbing the remainder."""
    total = round(float(total or 0), 2)
    if n <= 1:
        return [total]
    per = round(total / n, 2)
    return [per] * (n - 1) + [round(total - per * (n - 1), 2)]


def _item_shares(items, total: float) -> dict:
    """Apportion `total` across booking items by what each room is worth.

    Used for the money that belongs to the stay rather than to one room — the online convenience
    fee, an advance already taken, an OTA prepayment. Splitting it evenly would credit a Rs 900
    single the same as a Rs 3,000 suite; splitting it by value is the only division that leaves
    each room's balance meaning what it says. The last room absorbs the paisa.
    """
    total = round(float(total or 0), 2)
    if not items or total == 0:
        return {i.booking_item_id: 0.0 for i in items}
    weights = [float(i.total_amount or 0) for i in items]
    grand = round(sum(weights), 2)
    if grand <= 0:
        parts = _split_amount(total, len(items))
        return {i.booking_item_id: parts[n] for n, i in enumerate(items)}
    out, running = {}, 0.0
    for n, i in enumerate(items):
        if n == len(items) - 1:
            out[i.booking_item_id] = round(total - running, 2)
        else:
            part = round(total * weights[n] / grand, 2)
            out[i.booking_item_id] = part
            running = round(running + part, 2)
    return out


def ensure_folios(db: Session, booking: Booking, user) -> list:
    """Open the stay's bill, or bills. Idempotent — returns what is already there.

    v6e (Batch D): a booking with `billing_mode='room'` gets ONE FOLIO PER ROOM, each carrying only
    that room's nights and only its share of the stay-level money. `billing_mode='group'` — the
    default and what every existing booking is — gets the single folio it always had, with every
    room's charges on it.

    The room lines still go through `services/room_posting.post_room_nights`, one call per folio
    with `only_items` naming that room, so the per-night maths and the idempotency key are the same
    in both modes. That is deliberate: two ways of pricing a night is how a per-room bill would
    come to disagree with the group bill for the same stay.
    """
    per_room = folio_resolver.is_per_room(booking)
    existing = folio_resolver.folios_for_booking(db, booking.booking_id)
    if existing and not per_room:
        return existing
    items = sorted(booking.booking_items, key=lambda i: i.booking_item_id)
    if per_room and existing and len(existing) >= len(items):
        return existing
    if booking.status not in FOLIO_BOOKING_STATUSES:
        raise HTTPException(status_code=400,
                            detail=f"Cannot open a folio for a '{booking.status}' booking")

    uid = _resolve_user_id(db, user)

    # The money that belongs to the stay rather than to a room, apportioned in per-room mode.
    conv = round(float(booking.convenience_fee or 0) + float(booking.convenience_gst or 0), 2)
    paid = db.query(Payment).filter(
        Payment.booking_id == booking.booking_id,
        Payment.status == "paid",
    ).all()
    prepaid = round(float(getattr(booking, "prepaid_amount", 0) or 0), 2)

    targets = [(i, [i.booking_item_id]) for i in items] if per_room else [(None, None)]
    conv_share = _item_shares(items, conv) if per_room else {}
    prepaid_share = _item_shares(items, prepaid) if per_room else {}
    pay_shares = {p.payment_id: _item_shares(items, float(p.amount or 0)) for p in paid} \
        if per_room else {}

    have = {f.booking_item_id: f for f in existing}
    out = []
    for item, only_items in targets:
        key = item.booking_item_id if item is not None else None
        folio = have.get(key)
        if folio is None:
            folio = Folio(booking_id=booking.booking_id, booking_item_id=key, status="open")
            db.add(folio)
            db.flush()
        out.append(folio)

        # Room charges. The per-night split lives in services/room_posting (v4b1) so that
        # check-in, extend-stay and the automatic overstay charge all post nights through
        # ONE implementation with one idempotency key. price_mode="booking_split" is the
        # historical maths, unchanged: the folio room total still equals booking.total_amount
        # to the paisa. A complimentary stay (v4b6) posts nothing at all.
        room_posting.post_room_nights(
            db, booking, folio, booking.check_in, booking.check_out,
            user=user,
            posting_reason=room_posting.REASON_CHECKIN,
            price_mode=room_posting.PRICE_BOOKING_SPLIT,
            recompute=False,
            # A brand-new folio has no lines, so there is nothing to be un-backfilled.
            enforce_backfilled=False,
            only_items=only_items,
        )

        # Online (website) bookings add a convenience fee on top of the room total, and the guest
        # paid the WHOLE grand_total to the gateway. The room lines above only cover the room total,
        # so without posting the fee the folio would credit more than it charged and read as an
        # overpayment the hotel must refund. Post it as a charge so the folio equals what was paid.
        # (Desk / OTA bookings carry no convenience fee, so this is a no-op for them.)
        _conv = conv_share.get(key, 0.0) if per_room else conv
        if _conv > 0:
            db.add(FolioCharge(
                folio_id=folio.id,
                type="misc",
                description="Convenience fee (online booking)",
                qty=1,
                unit_price=_conv,
                amount=_conv,
                # v5i: the fee is convenience_fee + 18% GST on it, so it belongs in the 18% slab —
                # leaving gst_percent NULL parked it in a spurious "0%" row on the GST invoice.
                gst_percent=18,
                posted_by=uid,
                booking_item_id=key,
            ))

        # Advance already paid (online gateway) -> credit lines.
        for p in paid:
            amt = pay_shares[p.payment_id].get(key, 0.0) if per_room else float(p.amount or 0)
            if not amt:
                continue
            ref = p.payment_id_gateway or p.order_id or p.payment_id
            db.add(FolioCharge(
                folio_id=folio.id,
                type="payment",
                description=f"Advance paid — {p.gateway or 'gateway'} {ref}",
                qty=1,
                unit_price=amt,
                amount=-amt,
                posted_by=uid,
                booking_item_id=key,
            ))
            if per_room:
                db.add(PaymentAllocation(payment_id=p.payment_id, folio_id=folio.id, amount=amt))

        # Prepaid elsewhere (OTA channel / website) -> credit line (v4b1, R9).
        # An OTA booking records NO Payment row — the channel took the money, not the hotel —
        # so without this the folio showed the full room amount due and the desk collected it
        # a SECOND time. Not modelled as a Payment on purpose: that would put money the hotel
        # never touched into collections_summary, the cash-drawer gate and the shift variance.
        _prepaid = prepaid_share.get(key, 0.0) if per_room else prepaid
        if _prepaid > 0:
            # Prefer the channel NAME ("makemytrip") over the generic source ("ota") — the
            # receptionist reads this line and needs to know who is holding the money.
            src = (booking.booking_source or booking.prepaid_source or "channel")
            src = src.replace("_", " ").title()
            ref = booking.ota_booking_id or f"booking {booking.booking_id}"
            db.add(FolioCharge(
                folio_id=folio.id,
                type="payment",
                description=f"Prepaid to {src} — {ref}",
                qty=1,
                unit_price=_prepaid,
                amount=-_prepaid,
                posted_by=uid,
                booking_item_id=key,
            ))

        _recompute(db, folio)
    return out


@router.post("/open")
def open_folio(data: FolioOpenRequest, db: Session = Depends(get_db),
               user=Depends(require_reception_or_admin)):
    """Open a folio for a booking (idempotent). Posts the WHOLE stay's room
    charges (one line per night per booking item, matching the booking's stored
    pricing exactly) plus credit lines for already-paid payments (advance).

    v6e: a booking that bills per room gets one folio per room; the reply is the first of them,
    and `booking_folios` on the detail lists them all.
    """
    try:
        booking = db.query(Booking).options(
            joinedload(Booking.booking_items),
        ).filter(Booking.booking_id == data.booking_id).first()
        if not booking:
            raise HTTPException(status_code=404, detail="Booking not found")

        before = {f.id for f in folio_resolver.folios_for_booking(db, booking.booking_id)}
        folios = ensure_folios(db, booking, user)
        if not folios:
            raise HTTPException(status_code=400, detail="Nothing to bill on this booking")
        nights = max(1, (booking.check_out - booking.check_in).days)
        db.commit()
        for f in folios:
            db.refresh(f)

        for f in folios:
            if f.id not in before:
                write_audit(db, user, "folio.open", "folio", f.id,
                            after={"booking_id": booking.booking_id, "total": float(f.total),
                                   "balance": float(f.balance), "nights": nights,
                                   "booking_item_id": f.booking_item_id,
                                   "billing_mode": booking.billing_mode},
                            client="desktop", commit=True)
        return _folio_detail(db, folios[0])
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"open_folio failed: {e}", exc_info=True)
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to open folio")


@router.get("/{folio_id}")
def get_folio(folio_id: int, db: Session = Depends(get_db),
              user=Depends(require_reception_or_admin)):
    return _folio_detail(db, _get_folio(db, folio_id))


@router.post("/{folio_id}/charge")
def post_charge(folio_id: int, data: FolioChargeCreate, db: Session = Depends(get_db),
                user=Depends(require_reception_or_admin)):
    """Post an incidental charge (unit_price GST-inclusive). The charge type comes from the
    admin-editable `charge_type` list (v3 item 2); the reserved system types room / payment /
    discount are refused here as well as at the settings layer, because the reports split
    revenue on them and a hand-posted "room" line would corrupt every revenue figure."""
    try:
        folio = _get_folio(db, folio_id)
        # v4b6: also refuses a fully-complimentary stay.
        _ensure_chargeable(db, folio)

        if data.type in app_settings.RESERVED_CHARGE_TYPES:
            raise HTTPException(
                status_code=422,
                detail=f"'{data.type}' is posted by the system and cannot be charged by hand.")
        charge_type = app_settings.validate_category(db, "charge_type", data.type)

        amount = round(data.qty * data.unit_price, 2)
        charge = FolioCharge(
            folio_id=folio.id,
            type=charge_type,
            description=data.description,
            qty=data.qty,
            unit_price=data.unit_price,
            amount=amount,
            gst_percent=data.gst_percent,
            posted_by=_resolve_user_id(db, user),
        )
        db.add(charge)
        _recompute(db, folio)
        db.commit()

        write_audit(db, user, "folio.charge_post", "folio", folio.id,
                    after={"charge_id": charge.id, "type": data.type,
                           "description": data.description, "qty": data.qty,
                           "unit_price": data.unit_price, "amount": amount,
                           "gst_percent": data.gst_percent},
                    client="desktop", commit=True)
        return _folio_detail(db, folio)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"post_charge failed: {e}", exc_info=True)
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to post charge")


@router.post("/{folio_id}/charges/{charge_id}/void")
def void_charge(folio_id: int, charge_id: int, data: FolioVoidRequest,
                db: Session = Depends(get_db), user=Depends(require_void_permission)):
    """Void a charge: mark it void + append a reversing entry. Never hard-deletes."""
    try:
        folio = _get_folio(db, folio_id)
        _ensure_editable(db, folio)

        charge = db.query(FolioCharge).filter(
            FolioCharge.id == charge_id, FolioCharge.folio_id == folio.id
        ).first()
        if not charge:
            raise HTTPException(status_code=404, detail="Charge not found on this folio")
        if charge.void:
            raise HTTPException(status_code=409, detail="Charge is already void")
        if charge.type == "payment":
            raise HTTPException(status_code=400, detail="Payments cannot be voided here (refunds = prompt 08)")
        # v4b1: room nights are no longer voidable through the generic endpoint. Reversing a
        # night is not a bookkeeping-only act — it must also move booking.check_out back and
        # decrement grand_total, or the folio silently decouples from the booking and the
        # contiguous-nights invariant breaks. That belongs to the dedicated reversal route.
        # v5m: early-check-in / hourly-extension FEES are type='room' (room GST slab) but not
        # nights — no booking_item_id, no check-out to move — so they void like any other line.
        if charge.type == "room" and charge.booking_item_id is not None:
            raise HTTPException(
                status_code=400,
                detail=("Room nights cannot be voided here — reversing a night also moves the "
                        "stay's check-out date. Use the overstay reversal (admin + owner "
                        "approval) for an auto-charged night."))

        # Owner-approval OTP: a void erases a charge, so it needs the owner's code when armed
        # (VOID_OTP_REQUIRED, default on). Consumed here so the code is only spent on a real void.
        if app_settings.get_fraud_config(db)["void_otp_required"]:
            consume_otp(db, data.owner_otp_id, data.owner_otp_code, "void", user)

        before = {"charge_id": charge.id, "type": charge.type,
                  "description": charge.description, "amount": float(charge.amount)}

        reversal = _void_charge_row(db, charge, data.reason, user)
        # v5m: a voided arrival / extension fee is reflected in the arrival-exceptions report.
        from models import StayEvent
        for ev in db.query(StayEvent).filter(StayEvent.folio_charge_id == charge.id).all():
            ev.charge_basis = "voided"
            ev.charge_amount = 0
        _recompute(db, folio)
        db.commit()

        write_audit(db, user, "folio.charge_void", "folio", folio.id,
                    before=before,
                    after={"reason": data.reason, "reversal_charge_id": reversal.id,
                           "folio_total": float(folio.total)},
                    client="desktop", commit=True)
        return _folio_detail(db, folio)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"void_charge failed: {e}", exc_info=True)
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to void charge")


@router.post("/{folio_id}/discount")
def apply_discount(folio_id: int, data: FolioDiscountRequest, db: Session = Depends(get_db),
                   user=Depends(require_reception_or_admin)):
    """Apply a folio-level discount (reason mandatory).
    Below-floor discounts get admin OTP approval in prompt 11.

    v6d (F-11): this is no longer a "non-taxable adjustment" knocked off after tax. A discount
    shown on the invoice reduces the transaction value, so it reduces the TAXABLE value and the
    tax with it, apportioned across the slabs by `utils/gst.split`. The hotel was paying GST on
    money it never received."""
    try:
        folio = _get_folio(db, folio_id)
        _ensure_editable(db, folio)
        if data.amount > float(folio.total or 0):
            raise HTTPException(status_code=400, detail="Discount cannot exceed the folio total")

        # Owner-approval OTP: when the discount gate is on (ships armed), ANY discount amount > 0
        # needs the owner's code — the owner asked for approval on every discount, not just deep
        # ones. Consumed here so the code is only spent if the discount commits.
        if app_settings.get_fraud_config(db)["discount_otp_required"] and data.amount > 0:
            consume_otp(db, data.owner_otp_id, data.owner_otp_code, "discount_below_floor", user)

        charge = FolioCharge(
            folio_id=folio.id,
            type="discount",
            description=f"Discount — {data.reason}",
            qty=1,
            unit_price=data.amount,
            amount=-data.amount,
            posted_by=_resolve_user_id(db, user),
        )
        db.add(charge)
        _recompute(db, folio)
        db.commit()

        write_audit(db, user, "folio.discount", "folio", folio.id,
                    after={"charge_id": charge.id, "amount": data.amount, "reason": data.reason,
                           "folio_total": float(folio.total)},
                    client="desktop", commit=True)
        return _folio_detail(db, folio)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"apply_discount failed: {e}", exc_info=True)
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to apply discount")


@router.post("/{folio_id}/bill-to")
def set_bill_to(folio_id: int, data: FolioBillToRequest, db: Session = Depends(get_db),
                user=Depends(require_reception_or_admin)):
    """Route this folio (or just the listed charge ids) to a company, or back to the guest.

    This is the split folio: one folio, each line flagged with who pays it. Passing no
    `charge_ids` routes the whole stay; passing some routes only those lines, which is how the
    desk bills the room to the employer and leaves the bar tab with the guest.

    Credit control runs before anything moves. A breach is refused with 409 when
    `company_credit_block` is on, unless an admin supplies `override_reason` (audited)."""
    try:
        folio = _get_folio(db, folio_id)
        _ensure_editable(db, folio)

        bill_to = (data.bill_to or "company").strip().lower()
        if bill_to not in company_service.BILL_TO_VALUES:
            raise HTTPException(status_code=400, detail="bill_to must be 'guest' or 'company'")

        company = None
        credit = None
        if bill_to == company_service.BILL_TO_COMPANY:
            if not data.company_id:
                raise HTTPException(status_code=400, detail="company_id is required to bill a company")
            company = company_service.get_company(db, data.company_id, active_only=True)
            if not company:
                raise HTTPException(status_code=404, detail="Company not found or inactive")

            additional = company_service.projected_company_amount(db, folio, data.charge_ids)
            credit = company_service.check_credit(db, company, additional)
            if credit["blocked"]:
                if user.get("role") != "admin" or not (data.override_reason or "").strip():
                    raise HTTPException(
                        status_code=409,
                        detail=(f"{company.name} would exceed its credit limit "
                                f"(outstanding ₹{credit['outstanding']:.2f} + ₹{additional:.2f} "
                                f"> limit ₹{credit['credit_limit']:.2f}). "
                                f"An admin can override with a reason."),
                    )
                write_audit(db, user, "company.credit_override", "company", company.id,
                            after={"folio_id": folio.id, "reason": data.override_reason.strip(),
                                   **credit},
                            client="desktop")

        result = company_service.route_charges(db, folio, company,
                                               charge_ids=data.charge_ids, bill_to=bill_to)

        # Keep the booking's own flag in step so arrivals/reports show the right bill-to.
        booking = db.query(Booking).filter(Booking.booking_id == folio.booking_id).first()
        if booking is not None:
            if bill_to == company_service.BILL_TO_COMPANY:
                booking.company_id = company.id
                booking.bill_to = "company" if not data.charge_ids else "split"
            elif not company_service.company_lines(db, folio.id):
                booking.company_id = None
                booking.bill_to = "guest"

        db.commit()
        write_audit(db, user, "folio.bill_to", "folio", folio.id,
                    after={"bill_to": bill_to, "company_id": company.id if company else None,
                           "charge_ids": data.charge_ids, **result},
                    client="desktop", commit=True)

        detail = _folio_detail(db, folio)
        detail["credit"] = credit
        detail["routed"] = result
        return detail
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"set_bill_to failed: {e}", exc_info=True)
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to set bill-to")


@router.post("/{folio_id}/transfer-to-company")
def transfer_to_company(folio_id: int, db: Session = Depends(get_db),
                        user=Depends(require_reception_or_admin)):
    """Move this folio's company-side balance onto the company's city ledger.

    Runs automatically at checkout; exposed separately so the desk can settle the corporate side
    early. Idempotent — a repeat call reports the existing transfer instead of double-posting."""
    try:
        folio = _get_folio(db, folio_id)
        if not folio.company_id:
            raise HTTPException(status_code=400, detail="This folio is not billed to a company")

        result = company_service.transfer_folio_to_company(db, folio, user=user)
        if result is None:
            raise HTTPException(status_code=400, detail="Nothing outstanding on the company side")
        _recompute(db, folio)
        db.commit()

        detail = _folio_detail(db, folio)
        detail["transfer"] = result
        return detail
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"transfer_to_company failed: {e}", exc_info=True)
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to transfer to the company account")


def _profile_buyer(db: Session, folio: Folio) -> dict:
    """v5m: the guest profile's GST identity as an invoice buyer block ({} when B2C)."""
    from models import GuestProfile
    booking = db.query(Booking).options(joinedload(Booking.guest)).filter(
        Booking.booking_id == folio.booking_id).first()
    guest = booking.guest if booking else None
    if not guest:
        return {}
    p = db.query(GuestProfile).filter(GuestProfile.guest_id == guest.guest_id).first()
    if not p or not p.gstin:
        return {}
    # v6f: the GST BILLING address, falling back to the residential one for profiles saved
    # before `gst_address` existed - so nothing on a past invoice moves.
    return {"buyer_gstin": p.gstin, "buyer_name": p.gst_legal_name or guest.name,
            "buyer_address": p.gst_address or p.address,
            "buyer_state_code": p.gst_state_code or p.gstin[:2]}


def apply_buyer(invoice: Invoice, buyer: dict | None):
    """Stamp (or clear) the B2B buyer snapshot on an invoice. Amounts never change."""
    buyer = buyer or {}
    gstin = (buyer.get("buyer_gstin") or "").strip().upper() or None
    invoice.buyer_gstin = gstin
    invoice.buyer_name = ((buyer.get("buyer_name") or "").strip() or None) if gstin else None
    invoice.buyer_address = ((buyer.get("buyer_address") or "").strip() or None) if gstin else None
    invoice.buyer_state_code = ((buyer.get("buyer_state_code") or "").strip() or (gstin[:2] if gstin else None)) if gstin else None


def invoice_allowed(db: Session, folio: Folio, user, *, at_checkout: bool = False) -> None:
    """v5m §I — an invoice is a settlement document: raised at check-out once the folio is
    settled. Raises 409 otherwise. The checkout transaction passes at_checkout=True (its own
    zero-balance / override guard already ran); an admin may raise one early only when the
    balance is zero (a prepaid stay leaving without extras)."""
    if at_checkout:
        return
    balance = round(float(folio.balance or 0), 2)
    booking = db.query(Booking).filter(Booking.booking_id == folio.booking_id).first()
    status = booking.status if booking else None
    is_admin = (user or {}).get("role") == "admin"
    if balance != 0:
        raise HTTPException(status_code=409,
                            detail=f"Settle the folio first — balance ₹{balance:,.2f}. "
                                   f"The invoice is issued at check-out.")
    if status != "checked_out" and not is_admin:
        raise HTTPException(status_code=409,
                            detail="The invoice is issued at check-out. An admin can raise it early "
                                   "for a fully settled folio.")


def ensure_invoice(db: Session, folio: Folio, user, client: str = "desktop", buyer: dict | None = None):
    """Idempotently allocate a sequential GST invoice for a folio and snapshot its totals.
    Returns (invoice, created). Returns (None, False) when the folio has nothing billable.
    Commits its own work. Shared by the desk endpoint AND the auto-invoice at checkout (ALT-3).
    v5m: `buyer` (explicit B2B block) else the guest profile's GST identity, snapshotted on the
    invoice so a later profile edit never rewrites an issued document."""
    existing = _get_invoice(db, folio.id)
    if existing:
        return existing, False

    charges = _active_charges(db, folio.id)
    if not any(c.type not in ("payment",) for c in charges):
        return None, False

    totals = _invoice_totals(charges)
    today = date.today()
    fy = _fy_label(today)
    seq = _allocate_invoice_seq(db, fy)
    invoice = Invoice(
        folio_id=folio.id,
        booking_id=folio.booking_id,
        invoice_no=f"INV/{fy}/{seq:05d}",
        fy_label=fy,
        seq=seq,
        invoice_date=today,
        taxable_total=totals["taxable_total"],
        cgst_total=totals["cgst_total"],
        sgst_total=totals["sgst_total"],
        grand_total=totals["grand_total"],
        balance_due=totals["balance_due"],
        gst_breakup=json.dumps(totals["gst_rows"]),
        company_id=folio.company_id,   # picked up by the consolidated company bill (slice 7)
        created_by=_resolve_user_id(db, user),
    )
    apply_buyer(invoice, buyer if buyer is not None else _profile_buyer(db, folio))
    db.add(invoice)
    db.commit()
    db.refresh(invoice)

    write_audit(db, user, "folio.invoice_create", "folio", folio.id,
                after={"invoice_no": invoice.invoice_no,
                       "grand_total": totals["grand_total"],
                       "balance_due": totals["balance_due"],
                       "buyer_gstin": invoice.buyer_gstin},
                client=client, commit=True)
    return invoice, True


@router.post("/{folio_id}/invoice")
def create_invoice(folio_id: int, data: InvoiceBuyerRequest | None = None,
                   db: Session = Depends(get_db), user=Depends(require_reception_or_admin)):
    """Allocate a sequential GST invoice number and snapshot the totals (idempotent).
    Does NOT settle the folio, but freezes further charges/voids/discounts.
    v5m: only once the folio is settled (see invoice_allowed); optional B2B buyer body."""
    try:
        folio = _get_folio(db, folio_id)
        if not _get_invoice(db, folio.id):
            invoice_allowed(db, folio, user)
        buyer = None
        if data is not None and data.buyer_gstin is not None:
            from routers.crm import normalise_gstin
            gstin = normalise_gstin(data.buyer_gstin)
            buyer = {"buyer_gstin": gstin, "buyer_name": data.buyer_name,
                     "buyer_address": data.buyer_address, "buyer_state_code": data.buyer_state_code}
            # v6f: `gstin` may be None here, which is the guest saying "no GST invoice" - and
            # that has to reach the profile too, or the next stay re-applies the old one.
            if data.save_to_profile:
                _save_gst_to_profile(db, folio, buyer)
        invoice, _created = ensure_invoice(db, folio, user, buyer=buyer)
        if invoice is None:
            raise HTTPException(status_code=400, detail="Folio has no charges to invoice")
        # v6f: `ensure_invoice` is idempotent and returns an ALREADY-EXISTING invoice untouched and
        # uncommitted. Two things were therefore lost whenever the invoice had already been raised
        # - which is the common case, because checkout can raise it automatically:
        #   * GST details sent with this call were silently ignored (the desk saw "invoice
        #     created", with no buyer on it);
        #   * the profile write above was only flushed, never committed, so the guest's GSTIN was
        #     not remembered either.
        # Stamping the buyer on an existing invoice is not a new liberty - PATCH .../invoice/buyer
        # has always allowed exactly that - and the amounts never move.
        if buyer is not None and not _created:
            apply_buyer(invoice, buyer)
        db.commit()
        db.refresh(invoice)
        return _invoice_summary(invoice)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"create_invoice failed: {e}", exc_info=True)
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to create invoice")


def _save_gst_to_profile(db: Session, folio: Folio, buyer: dict):
    """Remember the guest's GST identity for next time (the desk asked at checkout)."""
    from models import GuestProfile
    booking = db.query(Booking).filter(Booking.booking_id == folio.booking_id).first()
    if not booking:
        return
    p = db.query(GuestProfile).filter(GuestProfile.guest_id == booking.guest_id).first()
    if p is None:
        p = GuestProfile(guest_id=booking.guest_id)
        db.add(p)
    p.gstin = buyer.get("buyer_gstin")
    if buyer.get("buyer_name"):
        p.gst_legal_name = buyer["buyer_name"]
    if buyer.get("buyer_address"):
        # ⚠️ v6f: `gst_address`, NOT `address`. This used to write the invoice's billing address
        # into the column KYC fills and the POLICE REGISTER prints - so a guest who gave their
        # company's address to get a GST invoice had their home address in the statutory register
        # replaced by their office, silently, at checkout.
        p.gst_address = buyer["buyer_address"]
    p.gst_state_code = buyer.get("buyer_state_code") or (p.gstin[:2] if p.gstin else None)
    if not p.gstin:
        # v6f: an explicitly emptied GSTIN means "this guest is not a business any more". The old
        # code only ever wrote a GSTIN, never cleared one, so making an invoice B2C left the
        # profile intact and the NEXT stay silently put the old GSTIN back on the bill.
        p.gst_legal_name = None
        p.gst_state_code = None
        p.gst_address = None
    db.flush()


@router.patch("/{folio_id}/invoice/buyer")
def set_invoice_buyer(folio_id: int, data: InvoiceBuyerRequest, db: Session = Depends(get_db),
                      user=Depends(require_reception_or_admin)):
    """v5m: add / change / clear the B2B buyer block on an issued invoice. Amounts and the
    invoice number never change (the GST return needs the buyer, not a new document). Audited."""
    try:
        folio = _get_folio(db, folio_id)
        invoice = _get_invoice(db, folio.id)
        if not invoice:
            raise HTTPException(status_code=400, detail="Create the invoice first")
        from routers.crm import normalise_gstin
        gstin = normalise_gstin(data.buyer_gstin)
        before = {"buyer_gstin": invoice.buyer_gstin, "buyer_name": invoice.buyer_name,
                  "buyer_address": invoice.buyer_address, "buyer_state_code": invoice.buyer_state_code}
        buyer = {"buyer_gstin": gstin, "buyer_name": data.buyer_name,
                 "buyer_address": data.buyer_address, "buyer_state_code": data.buyer_state_code}
        apply_buyer(invoice, buyer)
        # v6f: an emptied GSTIN clears the profile as well - see _save_gst_to_profile.
        if data.save_to_profile:
            _save_gst_to_profile(db, folio, buyer)
        db.commit()
        write_audit(db, user, "folio.invoice_buyer_update", "folio", folio.id,
                    before=before,
                    after={"invoice_no": invoice.invoice_no, "buyer_gstin": invoice.buyer_gstin,
                           "buyer_name": invoice.buyer_name, "buyer_address": invoice.buyer_address,
                           "buyer_state_code": invoice.buyer_state_code, "client_ref": data.client_ref},
                    client="desktop", commit=True)
        return _invoice_summary(invoice)
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        logger.error(f"set_invoice_buyer failed: {e}", exc_info=True)
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to update the invoice buyer")


@router.get("/{folio_id}/invoice/buyer-defaults")
def invoice_buyer_defaults(folio_id: int, db: Session = Depends(get_db),
                           user=Depends(require_reception_or_admin)):
    """v5m: what the GST-details prompt pre-fills — the issued invoice's buyer, else the profile."""
    folio = _get_folio(db, folio_id)
    invoice = _get_invoice(db, folio.id)
    if invoice and invoice.buyer_gstin:
        return {"source": "invoice", "buyer_gstin": invoice.buyer_gstin, "buyer_name": invoice.buyer_name,
                "buyer_address": invoice.buyer_address, "buyer_state_code": invoice.buyer_state_code}
    prof = _profile_buyer(db, folio)
    return {"source": "profile" if prof else "none", **{k: prof.get(k) for k in
            ("buyer_gstin", "buyer_name", "buyer_address", "buyer_state_code")}}


def _invoice_summary(invoice: Invoice):
    return {
        "invoice_no": invoice.invoice_no,
        "invoice_date": str(invoice.invoice_date),
        "folio_id": invoice.folio_id,
        "booking_id": invoice.booking_id,
        "buyer_gstin": invoice.buyer_gstin,
        "buyer_name": invoice.buyer_name,
        "buyer_address": invoice.buyer_address,
        "buyer_state_code": invoice.buyer_state_code,
        "taxable_total": float(invoice.taxable_total or 0),
        "cgst_total": float(invoice.cgst_total or 0),
        "sgst_total": float(invoice.sgst_total or 0),
        "grand_total": float(invoice.grand_total or 0),
        "balance_due": float(invoice.balance_due or 0),
    }


def _frozen_totals(invoice: Invoice, charges) -> dict:
    """The totals to PRINT for an issued invoice: the ones it was issued with.

    v6d (F-11): an invoice is a filed document. Its taxable value and tax were written to
    `invoices` (and its per-slab breakup to `gst_breakup`) at the moment it was raised, and a
    reprint must show those figures even if the way we compute tax has changed since — which it
    just did. Recomputing meant every reprint of every past invoice would silently restate the
    tax the hotel had already filed, and nobody would see it happen.

    Only the tax block is frozen. The lines, the payments and what is still owed are recomputed,
    because money genuinely does move after an invoice is raised and the guest should see it;
    `_invoice_payload` already lists anything settled after issue separately.
    """
    live = _invoice_totals(charges)
    try:
        rows = json.loads(invoice.gst_breakup) if invoice.gst_breakup else []
    except (ValueError, TypeError):
        rows = []
    if not rows and not invoice.taxable_total:
        # Nothing was stored (an invoice from before the snapshot existed) — the live figures are
        # the only ones there are.
        return live
    frozen_grand = float(invoice.grand_total or 0)
    live.update({
        "gst_rows": rows or live["gst_rows"],
        "taxable_total": float(invoice.taxable_total or 0),
        "cgst_total": float(invoice.cgst_total or 0),
        "sgst_total": float(invoice.sgst_total or 0),
        "grand_total": frozen_grand,
        # Still owed against the frozen total, using today's payments.
        "balance_due": round(frozen_grand + live["payments_total"], 2),
    })
    return live


def _invoice_payload(db: Session, folio: Folio, invoice: Invoice):
    """Everything the PDF/email needs. Charges are frozen once invoiced, so
    recomputing line groups here always matches the stored snapshot."""
    booking = db.query(Booking).options(joinedload(Booking.guest)).filter(
        Booking.booking_id == folio.booking_id).first()
    guest = booking.guest if booking else None
    charges = _active_charges(db, folio.id)
    totals = _frozen_totals(invoice, charges)

    def _line(c):
        # v5i: the invoice shows Rate WITHOUT GST and Amount WITH it. Both derive from the
        # authoritative GST-inclusive `amount` (not unit_price, whose meaning differs by line
        # type: room/misc carry an inclusive unit price, food an ex-GST menu price).
        amount = float(c.amount)
        qty = float(c.qty or 0) or 1.0
        g = float(c.gst_percent or 0)
        taxable = round(amount / (1 + g / 100), 2)
        return {
            "description": c.description,
            "qty": float(c.qty or 0),
            "unit_price": float(c.unit_price or 0),
            "gst_percent": float(c.gst_percent) if c.gst_percent is not None else None,
            "rate_ex_gst": round(taxable / qty, 2),
            "gst_amount": round(amount - taxable, 2),
            "amount": amount,
            "posted_at": c.posted_at,
        }

    # --- Payments & refunds, itemised (backlog v2 TBC-1) -------------------
    # Payments post NEGATIVE type='payment' lines, refunds POSITIVE ones. The
    # invoice's GST snapshot is frozen at issue and must never move, so anything
    # settled AFTER the invoice was raised is listed separately and plainly
    # labelled rather than folded back into the taxable totals.
    issued_at = invoice.created_at
    settlement_lines, post_invoice_lines = [], []
    for c in charges:
        if c.type != "payment":
            continue
        row = _line(c)
        row["kind"] = "refund" if float(c.amount) > 0 else "payment"
        after = bool(issued_at and c.posted_at and c.posted_at > issued_at)
        (post_invoice_lines if after else settlement_lines).append(row)

    nights = (booking.check_out - booking.check_in).days if booking else 0
    einvoice = db.query(EInvoice).filter(EInvoice.invoice_id == invoice.id).first()
    return {
        "invoice_no": invoice.invoice_no,
        "invoice_date": invoice.invoice_date,
        "folio_id": folio.id,
        "einvoice": ({"irn": einvoice.irn, "signed_qr": einvoice.signed_qr,
                      "ack_no": einvoice.ack_no, "ack_date": einvoice.ack_date,
                      "status": einvoice.status}
                     if einvoice and einvoice.status in ("generated", "stub") else None),
        "guest": {
            # F-01: the name THIS stay was booked in. Reading the live guest record meant an
            # invoice already issued could reprint under a different person's name.
            "name": (booking.display_guest_name if booking else "") or "N/A",
            "phone": guest.phone if guest else "N/A",
            "email": guest.email if guest else None,
        },
        # v5m B2B: printed as a "Bill to" block when a GSTIN is on the invoice.
        "buyer": ({"gstin": invoice.buyer_gstin, "name": invoice.buyer_name,
                   "address": invoice.buyer_address, "state_code": invoice.buyer_state_code}
                  if invoice.buyer_gstin else None),
        "booking": {
            "booking_id": folio.booking_id,
            "check_in": booking.check_in if booking else None,
            "check_out": booking.check_out if booking else None,
            "nights": nights,
        },
        "lines": [_line(c) for c in charges if c.type not in ("payment", "discount")],
        "discount_lines": [_line(c) for c in charges if c.type == "discount"],
        "payment_lines": settlement_lines,
        "post_invoice_lines": post_invoice_lines,
        **totals,
    }


@router.get("/{folio_id}/invoice/pdf")
def invoice_pdf(folio_id: int, db: Session = Depends(get_db),
                user=Depends(require_reception_or_admin)):
    folio = _get_folio(db, folio_id)
    invoice = _get_invoice(db, folio.id)
    if not invoice:
        raise HTTPException(status_code=400, detail="Create the invoice first")
    from utils.pdf_generator import generate_folio_invoice_pdf
    path = generate_folio_invoice_pdf(_invoice_payload(db, folio, invoice))
    return FileResponse(path, media_type="application/pdf",
                        filename=invoice.invoice_no.replace("/", "-") + ".pdf")


@router.post("/{folio_id}/invoice/email")
def email_invoice(folio_id: int, db: Session = Depends(get_db),
                  user=Depends(require_reception_or_admin)):
    folio = _get_folio(db, folio_id)
    invoice = _get_invoice(db, folio.id)
    if not invoice:
        raise HTTPException(status_code=400, detail="Create the invoice first")
    payload = _invoice_payload(db, folio, invoice)
    if not payload["guest"]["email"]:
        raise HTTPException(status_code=400, detail="Guest has no email address on file")
    from utils.pdf_generator import generate_folio_invoice_pdf
    from utils.email_service import send_invoice_email
    path = generate_folio_invoice_pdf(payload)
    try:
        send_invoice_email(payload, path)
    except Exception as e:
        logger.error(f"invoice email failed: {e}", exc_info=True)
        raise HTTPException(status_code=502, detail="Failed to send the invoice email")
    write_audit(db, user, "folio.invoice_email", "folio", folio.id,
                after={"invoice_no": invoice.invoice_no, "to": payload["guest"]["email"]},
                client="desktop", commit=True)
    return {"sent": True, "invoice_no": invoice.invoice_no, "to": payload["guest"]["email"]}


# --- GST e-invoicing / IRN (prompt 18, slice 2) ----------------------------------------------

def _einvoice_input(db: Session, folio: Folio, invoice: Invoice) -> dict:
    """Build the einvoice_service input from the frozen invoice snapshot."""
    try:
        gst_breakup = json.loads(invoice.gst_breakup) if invoice.gst_breakup else []
    except (ValueError, TypeError):
        gst_breakup = []
    booking = db.query(Booking).options(joinedload(Booking.guest)).filter(
        Booking.booking_id == invoice.booking_id).first()
    guest = booking.guest if booking else None
    profile = None
    if guest:
        from models import GuestProfile
        profile = db.query(GuestProfile).filter(GuestProfile.guest_id == guest.guest_id).first()
    return {
        "invoice_no": invoice.invoice_no,
        "invoice_date": str(invoice.invoice_date),
        "taxable_total": float(invoice.taxable_total or 0),
        "cgst_total": float(invoice.cgst_total or 0),
        "sgst_total": float(invoice.sgst_total or 0),
        "grand_total": float(invoice.grand_total or 0),
        "gst_breakup": gst_breakup,
        "guest_name": (booking.display_guest_name if booking else ""),
        "seller_gstin": os.getenv("GST_EINVOICE_GSTIN", "37AAACK9397F1Z3"),
        # v5m: the invoice's own buyer snapshot wins over the (possibly later-edited) profile.
        "buyer": ({"gstin": invoice.buyer_gstin, "name": invoice.buyer_name or (booking.display_guest_name if booking else ""),
                   "address": invoice.buyer_address, "state_code": invoice.buyer_state_code}
                  if invoice.buyer_gstin else
                  {"gstin": (profile.gstin if profile and profile.gstin else "URP"),
                   "name": (booking.display_guest_name if booking else ""),
                   "address": profile.address if profile else None,
                   "state_code": (profile.gst_state_code or profile.gstin[:2]) if profile and profile.gstin else None}),
    }


def _einvoice_summary(e: EInvoice) -> dict:
    return {
        "status": e.status,
        "irn": e.irn,
        "ack_no": e.ack_no,
        "ack_date": e.ack_date,
        "signed_qr": e.signed_qr,
        "error": e.error,
        "created_at": e.created_at.isoformat() if e.created_at else None,
    }


@router.get("/{folio_id}/einvoice")
def get_einvoice(folio_id: int, db: Session = Depends(get_db),
                 user=Depends(require_reception_or_admin)):
    folio = _get_folio(db, folio_id)
    invoice = _get_invoice(db, folio.id)
    if not invoice:
        return {"exists": False, "provider": einvoice_provider_status()}
    e = db.query(EInvoice).filter(EInvoice.invoice_id == invoice.id).first()
    return {"exists": bool(e), "provider": einvoice_provider_status(),
            **({"einvoice": _einvoice_summary(e)} if e else {})}


@router.post("/{folio_id}/einvoice")
def create_einvoice(folio_id: int, db: Session = Depends(get_db),
                    user=Depends(require_admin)):
    """Generate + persist the IRN for this folio's tax invoice (admin). Idempotent: a successful
    IRN is returned as-is; a prior failure can be retried. Real IRP call only when GST_EINVOICE_*
    is configured; otherwise a stub IRN is produced (status='stub')."""
    from services import einvoice_service
    folio = _get_folio(db, folio_id)
    invoice = _get_invoice(db, folio.id)
    if not invoice:
        raise HTTPException(status_code=400, detail="Create the invoice first")
    existing = db.query(EInvoice).filter(EInvoice.invoice_id == invoice.id).first()
    if existing and existing.status in ("generated", "stub"):
        return {"already": True, "einvoice": _einvoice_summary(existing)}

    result = einvoice_service.generate_irn(_einvoice_input(db, folio, invoice))
    row = existing or EInvoice(invoice_id=invoice.id)
    row.irn = result["irn"]
    row.ack_no = result["ack_no"]
    row.ack_date = result["ack_date"]
    row.signed_qr = result["signed_qr"]
    row.status = result["status"]
    row.request_payload = result["request_payload"]
    row.response_payload = result["response_payload"]
    row.error = result["error"]
    row.created_by = _resolve_user_id(db, user)
    if not existing:
        db.add(row)
    db.commit()
    write_audit(db, user, "folio.einvoice", "invoice", invoice.id,
                after={"invoice_no": invoice.invoice_no, "status": row.status, "irn": row.irn},
                client="web", commit=True)
    if row.status == "failed":
        raise HTTPException(status_code=502, detail=f"e-invoice generation failed: {row.error}")
    return {"already": False, "einvoice": _einvoice_summary(row)}


def einvoice_provider_status() -> dict:
    from services import einvoice_service
    return einvoice_service.provider_status()
