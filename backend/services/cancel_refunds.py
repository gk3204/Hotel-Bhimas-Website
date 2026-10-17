"""v7t (owner, 17 Oct): "for bookings which are cancelled with refund, raise a refund receipt for the
refunded amount with guest details etc, and it should show up in the cashier summary".

The admin-cancel used to refund ONLY the booking's first paid payment, and only through Razorpay -
an advance taken at the desk (cash / UPI / card) could not be refunded on cancellation at all.
Now the refund is spread over every paid payment that has not been refunded:

  * an online payment (Razorpay / PhonePe) is refunded at the gateway, as before;
  * a desk payment is recorded as a desk payout - how the money went back (cash / UPI / bank) and
    its reference come from the admin (owner decision, 17 Oct).

Each refunded payment carries its refund voucher RFND-<payment id> (routers/payments.receipt_payload),
dated when the refund was paid; the cashier summary lists it as a Paid-out on that day, and the
voucher is sent to the guest (WhatsApp + the cancellation email).
"""
import logging
import uuid
from decimal import Decimal

from fastapi import HTTPException

from models import FolioCharge, Payment
from services import folio_resolver, payment_split
from utils import clock
from utils.audit import _resolve_user_id

logger = logging.getLogger(__name__)

ONLINE = ("razorpay", "phonepe")
DESK_MODES = ("cash", "upi", "bank")


def refundable_payments(db, booking_id):
    """Paid payments of the booking not yet refunded - online ones first (they go back automatically)."""
    pays = (db.query(Payment).filter(Payment.booking_id == booking_id, Payment.status == "paid")
            .order_by(Payment.payment_id).all())
    pays = [p for p in pays if not p.refund_status or p.refund_status == "failed"]
    return sorted(pays, key=lambda p: (0 if (p.gateway or "") in ONLINE else 1, p.payment_id))


def refund_summary(db, booking_id):
    """What the cancel dialog shows: how much can be refunded and whether a desk refund mode is needed."""
    pays = refundable_payments(db, booking_id)
    return {
        "booking_id": booking_id,
        "refundable": round(sum(float(p.amount or 0) for p in pays), 2),
        "online": round(sum(float(p.amount or 0) for p in pays if (p.gateway or "") in ONLINE), 2),
        "desk": round(sum(float(p.amount or 0) for p in pays if (p.gateway or "") not in ONLINE), 2),
        "payments": [{"payment_id": p.payment_id, "receipt_no": f"RCPT-{p.payment_id}",
                      "amount": round(float(p.amount or 0), 2), "gateway": p.gateway, "method": p.method,
                      "online": (p.gateway or "") in ONLINE,
                      "paid_at": clock.to_local(p.created_at).strftime("%d/%m/%y %H:%M") if p.created_at else ""}
                     for p in pays],
    }


def plan(db, booking_id, amount):
    """[(payment, part)] - the refund spread over the payments, online first. Raises on a bad amount."""
    amount = round(float(amount or 0), 2)
    pays = refundable_payments(db, booking_id)
    total = round(sum(float(p.amount or 0) for p in pays), 2)
    if amount <= 0:
        return []
    if amount > total + 0.005:
        raise HTTPException(status_code=400,
                            detail=f"Refund Rs {amount:,.2f} is more than the Rs {total:,.2f} paid on this booking")
    out, left = [], amount
    for p in pays:
        if left <= 0.005:
            break
        part = round(min(left, float(p.amount or 0)), 2)
        out.append((p, part))
        left = round(left - part, 2)
    return out


def refund_for_cancellation(db, booking, amount, reason, user, mode=None, reference=None):
    """Refund `amount` of the booking's payments. Returns the refunded Payment rows (committed).

    Validates everything before any money moves. A gateway refund commits on its own (the money has
    moved); should a LATER one fail, the ones already done stay recorded and a 502 says so - the
    booking is then not cancelled, so the admin can retry for the rest."""
    from routers.payments import process_gateway_refund, _open_shift
    from routers.folio import _recompute as _folio_recompute
    steps = plan(db, booking.booking_id, amount)
    if not steps:
        return []
    needs_mode = any((p.gateway or "") not in ONLINE for p, _ in steps)
    if needs_mode and mode not in DESK_MODES:
        raise HTTPException(status_code=422,
                            detail="This booking was paid at the desk - choose how the money goes back "
                                   "(cash / UPI / bank) for the desk part of the refund")
    why = f"Booking cancelled - {reason}"[:100]
    by = _resolve_user_id(db, user)
    done = []
    for p, part in steps:
        if (p.gateway or "") in ONLINE:
            ok, refund_id, msg = process_gateway_refund(db, p.payment_id, part, why)
            if not ok:
                if not done:
                    raise HTTPException(status_code=400, detail=f"Refund failed: {msg}")
                raise HTTPException(status_code=502, detail=(
                    f"Refund failed for RCPT-{p.payment_id}: {msg}. Already refunded: "
                    + ", ".join(f"RFND-{d.payment_id} Rs {float(d.refund_amount):,.2f}" for d in done)
                    + ". The booking is NOT cancelled yet - retry for the rest."))
            p = db.query(Payment).filter(Payment.payment_id == p.payment_id).first()
            p.refunded_by = by
        else:
            p.refund_id = f"DESK-{uuid.uuid4().hex[:8].upper()}"
            p.refund_amount = Decimal(str(part))
            p.refund_status = "completed"
            p.refund_reason = why
            p.refund_mode = mode
            p.refund_reference = (reference or None)
            p.refunded_at = clock.now_utc()
            p.refunded_by = by
            shift = _open_shift(db)
            p.refund_shift_id = shift.id if shift else None
        # the stay's bill, when one was opened, shows the refund too (as the desk refund does)
        if folio_resolver.primary_folio(db, booking.booking_id) is not None:
            for _f, _part in payment_split.refund_targets(db, p, part):
                db.add(FolioCharge(folio_id=_f.id, type="payment", description=f"Refund - {why} ({p.refund_id})",
                                   qty=1, unit_price=_part, amount=_part, gst_percent=0, posted_by=by,
                                   booking_item_id=_f.booking_item_id, payment_id=p.payment_id))
                _folio_recompute(db, _f)
        db.commit()
        done.append(p)
    return done


def send_refund_vouchers(db, booking, payments):
    """WhatsApp each refund voucher to the guest (document, else the text / email fallback). Returns
    the PDF paths, which the cancellation email attaches. Never raises."""
    from routers.payments import receipt_payload
    from utils.pdf_generator import generate_payment_receipt_pdf
    from services import notify as notify_service
    paths = []
    guest = booking.guest
    for p in payments:
        try:
            path = generate_payment_receipt_pdf(receipt_payload(db, p, "refund"))
            paths.append(path)
        except Exception as e:
            logger.warning(f"refund voucher PDF for payment {p.payment_id} failed: {e}")
            path = None
        try:
            amt = f"{float(p.refund_amount or 0):.2f}"
            notify_service.notify_guest_document(
                db, guest, doc_template="refund_voucher_doc", text_template="refund_voucher",
                doc_params={"guest_name": booking.display_guest_name or (guest.name if guest else "Guest"),
                            "amount": amt},
                text_params={"guest_name": booking.display_guest_name or (guest.name if guest else "Guest"),
                             "amount": amt, "booking_ref": str(booking.booking_id)},
                pdf_path=path, filename=f"refund-RFND-{p.payment_id}.pdf",
                caption=f"Refund of Rs. {amt} - Hotel Bhimas", booking_id=booking.booking_id,
                client_ref=f"refund_voucher:{p.payment_id}",
                email_fallback=False)        # the cancellation email carries the voucher itself
        except Exception as e:
            logger.warning(f"refund voucher WhatsApp for payment {p.payment_id} failed: {e}")
    return paths
