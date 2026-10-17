"""v6m.13 (owner list 11 Oct, item 4): the receipt for money a guest paid BEFORE arriving.

"For prepaid like OTA or website, we need a receipt automatically generated showing the amount as
prepaid to bank for OTA and website the razorpay reference and should come in cashier summary,
shift summary etc." Decision (owner, 11 Oct): issued AT CHECK-IN, dated that moment.

It is a DOCUMENT, never a Payment:
  * an OTA / agent stay deliberately has no Payment row - `bookings.prepaid_amount` carries the money
    (see payments.total_paid_including_prepaid) - so a Payment would credit the guest twice;
  * a website stay already has its Razorpay Payment, listed on the day it was paid.
So the cash reports show it as a memo ("not collected at the desk") and the drawer never moves.
"""
import logging

from sqlalchemy.exc import IntegrityError

from models import Booking, Payment, PrepaidReceipt, User
from utils import clock
from utils.booking_source import channel_label, source_label

logger = logging.getLogger(__name__)

ONLINE_GATEWAYS = ("razorpay", "phonepe")


def receipt_no(pr) -> str:
    return f"PRE-{pr.id}"


def _website_payments(db, booking):
    """The guest's own online payments for the stay: a gateway payment nobody at the desk took
    (desk QR / link collections carry the collector and a collect_method)."""
    return (db.query(Payment)
            .filter(Payment.booking_id == booking.booking_id, Payment.status == "paid",
                    Payment.gateway.in_(ONLINE_GATEWAYS), Payment.collected_by.is_(None),
                    Payment.collect_method.is_(None))
            .order_by(Payment.payment_id).all())


def _what_was_prepaid(db, booking):
    """(kind, channel, amount, net_to_hotel, reference, payment_ids), or None when nothing was paid
    before arrival."""
    prepaid = float(getattr(booking, "prepaid_amount", 0) or 0)
    if prepaid > 0:
        kind = (getattr(booking, "prepaid_source", None) or "ota").lower()
        if kind == "agent":
            channel = source_label(db, booking)
        else:
            kind = "ota"
            channel = channel_label(booking)
        net = getattr(booking, "ota_net_payout", None)
        return (kind, channel[:60], round(prepaid, 2),
                (round(float(net), 2) if net is not None and kind == "ota" else None),
                (getattr(booking, "ota_booking_id", None) or None), None)
    # v7t (owner, 17 Oct): a website stay's money already has its ADVANCE receipt (RCPT-n), raised -
    # and listed in the cashier summary - on the day it was paid. Check-in sends THAT receipt to the
    # guest (send_website_advance_receipts) instead of issuing a second, PRE, document.
    return None
    pays = _website_payments(db, booking)
    amount = round(sum(float(p.amount or 0) for p in pays), 2)
    if amount > 0:
        gw = (pays[0].gateway or "razorpay").title()
        refs = ", ".join(p.payment_id_gateway or p.order_id or f"#{p.payment_id}" for p in pays)
        return ("website", f"Website ({gw})", amount, None, refs[:200],
                ",".join(str(p.payment_id) for p in pays)[:200])
    return None


def issue_prepaid_receipt(db, booking, user_id=None):
    """Issue the booking's prepaid receipt once (idempotent: every later room's check-in and every
    outbox replay returns the same one). Returns the PrepaidReceipt, or None when nothing was
    prepaid. Commits its own row."""
    if booking is None:
        return None
    existing = db.query(PrepaidReceipt).filter(PrepaidReceipt.booking_id == booking.booking_id).first()
    if existing is not None:
        return existing
    what = _what_was_prepaid(db, booking)
    if what is None:
        return None
    kind, channel, amount, net, ref, pay_ids = what
    pr = PrepaidReceipt(booking_id=booking.booking_id, kind=kind, channel=channel, amount=amount,
                        net_to_hotel=net, reference=ref, payment_ids=pay_ids,
                        issued_at=clock.now_utc(), issued_by=user_id)
    db.add(pr)
    try:
        db.commit()
    except IntegrityError:          # two desks checked the same booking in at once
        db.rollback()
        return db.query(PrepaidReceipt).filter(PrepaidReceipt.booking_id == booking.booking_id).first()
    db.refresh(pr)
    return pr


def send_website_advance_receipts(db, booking):
    """v7t (owner): at check-in, the guest gets the advance receipt(s) of what they paid on the
    website (RCPT-n, dated the day they paid) - WhatsApp with the PDF, else the text / email fallback.
    Idempotent per receipt. Returns [{payment_id, receipt_no, amount}]. Never raises."""
    out = []
    try:
        pays = _website_payments(db, booking)
    except Exception as e:
        logger.warning(f"website advance receipts for booking {getattr(booking, 'booking_id', '?')}: {e}")
        return out
    if not pays:
        return out
    from routers.payments import receipt_payload
    from utils.pdf_generator import generate_payment_receipt_pdf
    from services import notify as notify_service
    guest = booking.guest
    name = booking.display_guest_name or (guest.name if guest else "Guest")
    for p in pays:
        amt = f"{float(p.amount or 0):.2f}"
        out.append({"payment_id": p.payment_id, "receipt_no": f"RCPT-{p.payment_id}", "amount": float(p.amount or 0)})
        try:
            path = None
            try:
                path = generate_payment_receipt_pdf(receipt_payload(db, p, "payment"))
            except Exception as e:
                logger.warning(f"advance receipt PDF for payment {p.payment_id} failed: {e}")
            notify_service.notify_guest_document(
                db, guest, doc_template="payment_receipt_doc", text_template="payment_receipt",
                doc_params={"guest_name": name, "amount": amt},
                text_params={"guest_name": name, "amount": amt, "method": "website",
                             "booking_ref": str(booking.booking_id)},
                pdf_path=path, filename=f"advance-receipt-RCPT-{p.payment_id}.pdf",
                caption=f"Advance receipt for Rs. {amt} - Hotel Bhimas", booking_id=booking.booking_id,
                client_ref=f"advance_receipt:{p.payment_id}")
        except Exception as e:
            logger.warning(f"advance receipt send for payment {p.payment_id} failed: {e}")
    return out


def mode_label(pr) -> str:
    if pr.kind in ("ota", "agent"):
        return f"Prepaid - {pr.channel} (bank)"
    return f"Prepaid - {pr.channel}"


def prepaid_receipt_payload(db, pr) -> dict:
    from utils.doc_rooms import booking_rooms_label
    """The dict generate_payment_receipt_pdf() expects, kind='prepaid'."""
    booking = db.query(Booking).filter(Booking.booking_id == pr.booking_id).first()
    guest = booking.guest if booking else None
    issuer = db.query(User).filter(User.user_id == pr.issued_by).first() if pr.issued_by else None
    return {
        "kind": "prepaid",
        "payment_id": f"pre{pr.id}",
        "receipt_no": receipt_no(pr),
        "booking_id": pr.booking_id,
        "guest_name": (booking.display_guest_name if booking else None) or (guest.name if guest else "—"),
        "guest_phone": guest.phone if guest else None,
        "date": clock.to_local(pr.issued_at),
        "amount": float(pr.amount or 0),
        "net_to_hotel": float(pr.net_to_hotel) if pr.net_to_hotel is not None else None,
        "method": mode_label(pr),
        "reference": pr.reference,
        "gateway": pr.kind,
        "channel": pr.channel,
        "collected_by_name": (issuer.full_name or issuer.username) if issuer else None,
        "source": source_label(db, booking),
        "rooms": booking_rooms_label(db, pr.booking_id),   # v7m (owner): the room(s)
    }


def prepaid_receipt_summary(pr) -> dict | None:
    """The short form the desk / folio carry."""
    if pr is None:
        return None
    return {"receipt_no": receipt_no(pr), "id": pr.id, "kind": pr.kind, "channel": pr.channel,
            "amount": float(pr.amount or 0), "reference": pr.reference,
            "issued_at": clock.to_local(pr.issued_at).isoformat() if pr.issued_at else None}
