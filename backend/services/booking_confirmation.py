"""v6m.7: the confirmation a WEBSITE booking's guest receives once it is paid - e-mail (guest + hotel)
AND WhatsApp with the confirmation PDF attached (owner: "for website bookings also send booking
confirmation with doc in whatsapp"). Desk bookings have sent the WhatsApp document since v5; website
bookings only ever got the e-mail, and only from the browser's /verify call - a payment settled by
the Razorpay webhook or the 15-minute Razorpay check sent nothing at all.

One function for every path, so they cannot drift. Opens its own session (it runs as a background
task after the response). WhatsApp is idempotent on `confirmation:<booking_id>`; never raises.
"""
import logging
import os

logger = logging.getLogger(__name__)


def send_website_confirmation(booking_id: int, *, email: bool = True, whatsapp: bool = True) -> dict:
    """Returns {"email": {"ok", "error"} | None, "whatsapp": notify-result | None}."""
    from database import SessionLocal
    from models import Booking, Payment
    out = {"email": None, "whatsapp": None}
    db = SessionLocal()
    pdf_path = None
    try:
        booking = db.query(Booking).filter(Booking.booking_id == booking_id).first()
        if booking is None:
            return out
        payment = (db.query(Payment).filter(Payment.booking_id == booking_id, Payment.status == "paid")
                   .order_by(Payment.payment_id.desc()).first())
        from routers.payments import booking_pdf_payload
        booking_data = booking_pdf_payload(booking)
        payment_data = {"payment_id_gateway": getattr(payment, "payment_id_gateway", None),
                        "order_id": getattr(payment, "order_id", None),
                        "gateway": getattr(payment, "gateway", None) or "razorpay",
                        "status": getattr(payment, "status", None) or "paid"}
        try:
            from utils.pdf_generator import generate_booking_pdf
            pdf_path = generate_booking_pdf(booking_data, payment_data)
        except Exception as e:
            logger.error(f"confirmation PDF failed for booking {booking_id}: {e}", exc_info=True)

        if email:
            try:
                from utils.email_service import send_booking_email
                if send_booking_email(booking_data, pdf_path) is False:   # SAFE_MODE
                    out["email"] = {"ok": False, "error": "SAFE_MODE is on - e-mail is switched off on this server"}
                else:
                    out["email"] = {"ok": True, "error": None}
            except Exception as e:
                logger.error(f"confirmation e-mail failed for booking {booking_id}: {e}")
                out["email"] = {"ok": False, "error": str(e) or "Email sending failed"}

        if whatsapp and booking.guest is not None:
            from services import notify as notify_service
            g = booking.guest
            out["whatsapp"] = notify_service.notify_guest_document(
                db, g,
                doc_template="booking_confirmation_doc", text_template="booking_confirmation",
                doc_params={"guest_name": g.name, "booking_ref": str(booking.booking_id)},
                text_params={"guest_name": g.name, "check_in": str(booking.check_in),
                             "check_out": str(booking.check_out), "booking_ref": str(booking.booking_id)},
                pdf_path=pdf_path,
                filename=f"booking-{booking.booking_id}.pdf",
                caption=f"Booking {booking.booking_id} confirmed \u2014 Hotel Bhimas",
                setting_key="wa_confirmation_enabled",
                booking_id=booking.booking_id,
                client_ref=f"confirmation:{booking.booking_id}",
                # the e-mail above already carries the confirmation - never a second one from here
                email_fallback=False)
        return out
    except Exception as e:
        logger.error(f"send_website_confirmation({booking_id}) failed: {e}", exc_info=True)
        return out
    finally:
        try:
            if pdf_path and os.path.exists(pdf_path):
                os.remove(pdf_path)
        except Exception:
            pass
        db.close()
