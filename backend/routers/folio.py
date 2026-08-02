"""Folio / billing endpoints (prompt 07: per-stay charges, food posting, GST invoices).

Conventions (see docs/PROJECT_STATE.md, prompt 07):
- FolioCharge.amount is the authoritative SIGNED, GST-INCLUSIVE value
  (charges +, discounts/payments -). gst_percent records the slab for the
  invoice CGST/SGST split only; qty/unit_price are informational.
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

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import text
from sqlalchemy.orm import Session, joinedload

from database import SessionLocal
from models import Booking, BookingItem, Company, EInvoice, Folio, FolioCharge, Invoice, Payment, RoomType
from schemas import (FolioBillToRequest, FolioChargeCreate, FolioDiscountRequest, FolioOpenRequest,
                     FolioVoidRequest)
from services import company_service
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
    return db.query(FolioCharge).filter(
        FolioCharge.folio_id == folio_id,
        FolioCharge.void == False,  # noqa: E712
    ).all()


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


def _get_folio(db: Session, folio_id: int) -> Folio:
    folio = db.query(Folio).filter(Folio.id == folio_id).first()
    if not folio:
        raise HTTPException(status_code=404, detail="Folio not found")
    return folio


def _invoice_totals(charges):
    """GST math over NON-VOID lines. Amounts are GST-inclusive; CGST = SGST = slab GST / 2.
    Discounts are a non-taxable adjustment (promo discounts are already inside room lines)."""
    charge_lines = [c for c in charges if c.type not in ("payment", "discount")]
    discount_lines = [c for c in charges if c.type == "discount"]
    payment_lines = [c for c in charges if c.type == "payment"]

    slabs = {}
    for c in charge_lines:
        g = float(c.gst_percent or 0)
        slabs[g] = round(slabs.get(g, 0) + float(c.amount), 2)

    gst_rows = []
    taxable_total = cgst_total = sgst_total = 0.0
    for g in sorted(slabs):
        gross = slabs[g]
        taxable = round(gross / (1 + g / 100), 2)
        gst = round(gross - taxable, 2)
        sgst = round(gst / 2, 2)
        cgst = round(gst - sgst, 2)   # absorbs the odd paisa so cgst+sgst == gst exactly
        gst_rows.append({"gst_percent": g, "taxable": taxable, "cgst": cgst, "sgst": sgst, "total": gross})
        taxable_total = round(taxable_total + taxable, 2)
        cgst_total = round(cgst_total + cgst, 2)
        sgst_total = round(sgst_total + sgst, 2)

    charges_total = round(sum(float(c.amount) for c in charge_lines), 2)
    discount_total = round(sum(float(c.amount) for c in discount_lines), 2)   # negative
    payments_total = round(sum(float(c.amount) for c in payment_lines), 2)    # negative
    grand_total = round(charges_total + discount_total, 2)
    balance_due = round(grand_total + payments_total, 2)
    return {
        "gst_rows": gst_rows,
        "charges_total": charges_total,
        "discount_total": discount_total,
        "taxable_total": taxable_total,
        "cgst_total": cgst_total,
        "sgst_total": sgst_total,
        "grand_total": grand_total,
        "payments_total": payments_total,
        "balance_due": balance_due,
    }


def _folio_detail(db: Session, folio: Folio):
    """Shared response builder: folio + booking/guest summary + all lines + GST totals."""
    booking = db.query(Booking).options(
        joinedload(Booking.guest),
        joinedload(Booking.booking_items),
    ).filter(Booking.booking_id == folio.booking_id).first()
    charges = db.query(FolioCharge).filter(
        FolioCharge.folio_id == folio.id
    ).order_by(FolioCharge.id).all()
    totals = _invoice_totals([c for c in charges if not c.void])
    invoice = _get_invoice(db, folio.id)
    company = company_service.get_company(db, folio.company_id)
    company_balance = float(folio.company_balance or 0)
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
        "guest_name": booking.guest.name if booking and booking.guest else None,
        "phone": booking.guest.phone if booking and booking.guest else None,
        "email": booking.guest.email if booking and booking.guest else None,
        "check_in": str(booking.check_in) if booking else None,
        "check_out": str(booking.check_out) if booking else None,
        "booking_status": booking.status if booking else None,
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
        } for c in charges],
        **{k: v for k, v in totals.items() if k != "gst_rows"},
        "gst_rows": totals["gst_rows"],
    }


# ---------------------------------------------------------------- endpoints

@router.get("/")
def list_folios(db: Session = Depends(get_db), user=Depends(require_reception_or_admin)):
    """Desk list: confirmed / in-house bookings with their folio (if opened)."""
    rows = (
        db.query(Booking, Folio)
        .outerjoin(Folio, Folio.booking_id == Booking.booking_id)
        .options(joinedload(Booking.guest),
                 joinedload(Booking.booking_items).joinedload(BookingItem.room_type))
        .filter((Booking.status.in_(FOLIO_BOOKING_STATUSES)) | (Folio.status == "open"))
        .order_by(Booking.check_in, Booking.booking_id)
        .all()
    )
    data = []
    for booking, folio in rows:
        invoice = _get_invoice(db, folio.id) if folio else None
        room_types = ", ".join(
            f"{bi.room_type.name} x{bi.quantity}" for bi in booking.booking_items if bi.room_type
        )
        data.append({
            "booking_id": booking.booking_id,
            "guest_name": booking.guest.name if booking.guest else None,
            "phone": booking.guest.phone if booking.guest else None,
            "check_in": str(booking.check_in),
            "check_out": str(booking.check_out),
            "booking_status": booking.status,
            "room_types": room_types,
            "grand_total": float(booking.grand_total or 0),
            "folio_id": folio.id if folio else None,
            "folio_status": folio.status if folio else None,
            "folio_total": float(folio.total or 0) if folio else None,
            "balance": float(folio.balance or 0) if folio else None,
            "invoice_no": invoice.invoice_no if invoice else None,
        })
    return {"total": len(data), "data": data}


@router.post("/open")
def open_folio(data: FolioOpenRequest, db: Session = Depends(get_db),
               user=Depends(require_reception_or_admin)):
    """Open a folio for a booking (idempotent). Posts the WHOLE stay's room
    charges (one line per night per booking item, matching the booking's stored
    pricing exactly) plus credit lines for already-paid payments (advance)."""
    try:
        existing = db.query(Folio).filter(Folio.booking_id == data.booking_id).first()
        if existing:
            return _folio_detail(db, existing)

        booking = db.query(Booking).options(
            joinedload(Booking.booking_items),
        ).filter(Booking.booking_id == data.booking_id).first()
        if not booking:
            raise HTTPException(status_code=404, detail="Booking not found")
        if booking.status not in FOLIO_BOOKING_STATUSES:
            raise HTTPException(status_code=400,
                                detail=f"Cannot open a folio for a '{booking.status}' booking")

        uid = _resolve_user_id(db, user)
        folio = Folio(booking_id=booking.booking_id, status="open")
        db.add(folio)
        db.flush()

        # Room charges: split each booking item's GST-inclusive total across the
        # nights so the folio room total equals booking.total_amount to the paisa.
        nights = max(1, (booking.check_out - booking.check_in).days)
        for item in booking.booking_items:
            rt = db.query(RoomType).filter(RoomType.room_type_id == item.room_type_id).first()
            item_total = float(item.total_amount or 0)
            per_night = round(item_total / nights, 2)
            for n in range(nights):
                night = booking.check_in + timedelta(days=n)
                line_amount = per_night if n < nights - 1 else round(item_total - per_night * (nights - 1), 2)
                db.add(FolioCharge(
                    folio_id=folio.id,
                    type="room",
                    description=f"Room {rt.name if rt else item.room_type_id} x{item.quantity} — {night:%d %b %Y}",
                    qty=item.quantity,
                    unit_price=round(line_amount / item.quantity, 2) if item.quantity else line_amount,
                    amount=line_amount,
                    gst_percent=float(rt.gst_percent) if rt else None,
                    posted_by=uid,
                ))

        # Advance already paid (online gateway) -> credit lines.
        paid = db.query(Payment).filter(
            Payment.booking_id == booking.booking_id,
            Payment.status == "paid",
        ).all()
        for p in paid:
            ref = p.payment_id_gateway or p.order_id or p.payment_id
            db.add(FolioCharge(
                folio_id=folio.id,
                type="payment",
                description=f"Advance paid — {p.gateway or 'gateway'} {ref}",
                qty=1,
                unit_price=float(p.amount or 0),
                amount=-float(p.amount or 0),
                posted_by=uid,
            ))

        _recompute(db, folio)
        db.commit()
        db.refresh(folio)

        write_audit(db, user, "folio.open", "folio", folio.id,
                    after={"booking_id": booking.booking_id, "total": float(folio.total),
                           "balance": float(folio.balance), "nights": nights},
                    client="desktop", commit=True)
        return _folio_detail(db, folio)
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
    """Post a food/misc/minibar/laundry/extra-bed charge (unit_price GST-inclusive)."""
    try:
        folio = _get_folio(db, folio_id)
        _ensure_editable(db, folio)

        amount = round(data.qty * data.unit_price, 2)
        charge = FolioCharge(
            folio_id=folio.id,
            type=data.type,
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

        before = {"charge_id": charge.id, "type": charge.type,
                  "description": charge.description, "amount": float(charge.amount)}

        charge.void = True
        charge.void_reason = data.reason
        reversal = FolioCharge(
            folio_id=folio.id,
            type=charge.type,
            description=f"REVERSAL of #{charge.id}: {charge.description}",
            qty=charge.qty,
            unit_price=charge.unit_price,
            amount=-float(charge.amount),
            gst_percent=charge.gst_percent,
            posted_by=_resolve_user_id(db, user),
            void=True,
            void_reason=f"reversal of #{charge.id}: {data.reason}",
            reversal_of_id=charge.id,
        )
        db.add(reversal)
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
    """Apply a folio-level discount (non-taxable adjustment; reason mandatory).
    Below-floor discounts get admin OTP approval in prompt 11."""
    try:
        folio = _get_folio(db, folio_id)
        _ensure_editable(db, folio)
        if data.amount > float(folio.total or 0):
            raise HTTPException(status_code=400, detail="Discount cannot exceed the folio total")

        # Owner-approval OTP (prompt 11): a discount deeper than DISCOUNT_FLOOR_PERCENT of the
        # folio total is "below floor" and needs the owner's code when DISCOUNT_OTP_REQUIRED is on.
        # Opt-in (default off) so prompt-07 behaviour is unchanged. Consumed here so the code is
        # only spent if the discount commits.
        if os.getenv("DISCOUNT_OTP_REQUIRED", "false").strip().lower() not in ("false", "0", "no"):
            try:
                floor_pct = float(os.getenv("DISCOUNT_FLOOR_PERCENT", "20"))
            except (TypeError, ValueError):
                floor_pct = 20.0
            total = float(folio.total or 0)
            below_floor = total <= 0 or (data.amount / total) * 100.0 > floor_pct
            if below_floor:
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


def ensure_invoice(db: Session, folio: Folio, user, client: str = "desktop"):
    """Idempotently allocate a sequential GST invoice for a folio and snapshot its totals.
    Returns (invoice, created). Returns (None, False) when the folio has nothing billable.
    Commits its own work. Shared by the desk endpoint AND the auto-invoice at checkout (ALT-3)."""
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
    db.add(invoice)
    db.commit()
    db.refresh(invoice)

    write_audit(db, user, "folio.invoice_create", "folio", folio.id,
                after={"invoice_no": invoice.invoice_no,
                       "grand_total": totals["grand_total"],
                       "balance_due": totals["balance_due"]},
                client=client, commit=True)
    return invoice, True


@router.post("/{folio_id}/invoice")
def create_invoice(folio_id: int, db: Session = Depends(get_db),
                   user=Depends(require_reception_or_admin)):
    """Allocate a sequential GST invoice number and snapshot the totals (idempotent).
    Does NOT settle the folio, but freezes further charges/voids/discounts."""
    try:
        folio = _get_folio(db, folio_id)
        invoice, _created = ensure_invoice(db, folio, user)
        if invoice is None:
            raise HTTPException(status_code=400, detail="Folio has no charges to invoice")
        return _invoice_summary(invoice)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"create_invoice failed: {e}", exc_info=True)
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to create invoice")


def _invoice_summary(invoice: Invoice):
    return {
        "invoice_no": invoice.invoice_no,
        "invoice_date": str(invoice.invoice_date),
        "folio_id": invoice.folio_id,
        "booking_id": invoice.booking_id,
        "taxable_total": float(invoice.taxable_total or 0),
        "cgst_total": float(invoice.cgst_total or 0),
        "sgst_total": float(invoice.sgst_total or 0),
        "grand_total": float(invoice.grand_total or 0),
        "balance_due": float(invoice.balance_due or 0),
    }


def _invoice_payload(db: Session, folio: Folio, invoice: Invoice):
    """Everything the PDF/email needs. Charges are frozen once invoiced, so
    recomputing line groups here always matches the stored snapshot."""
    booking = db.query(Booking).options(joinedload(Booking.guest)).filter(
        Booking.booking_id == folio.booking_id).first()
    guest = booking.guest if booking else None
    charges = _active_charges(db, folio.id)
    totals = _invoice_totals(charges)

    def _line(c):
        return {
            "description": c.description,
            "qty": float(c.qty or 0),
            "unit_price": float(c.unit_price or 0),
            "gst_percent": float(c.gst_percent) if c.gst_percent is not None else None,
            "amount": float(c.amount),
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
            "name": guest.name if guest else "N/A",
            "phone": guest.phone if guest else "N/A",
            "email": guest.email if guest else None,
        },
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
        "guest_name": guest.name if guest else "",
        "seller_gstin": os.getenv("GST_EINVOICE_GSTIN", "37AAACK9397F1Z3"),
        "buyer": {"gstin": (profile.gstin if profile and profile.gstin else "URP"),
                  "name": guest.name if guest else ""},
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
