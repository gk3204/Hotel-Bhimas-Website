"""Website payments that reach us late, or never through the browser (v6m).

Booking 181 (production, 23 Sep): the guest paid Rs 3,763.83 on Razorpay, but the payment row stayed
`created` and 55 minutes later the 15-minute hold cancelled the booking as "Payment timeout". Two
gaps lined up:

  1. The ONLY thing that recorded a website payment was the guest's browser calling /payments/verify
     after checkout. Production's webhook_events table is empty: Razorpay's server-to-server webhook
     has never reached us. Close the tab, lose signal, switch apps - the money is taken and lost.
  2. A payment that arrived after the hold had expired was marked FAILED, by both /verify and the
     webhook. The guest's money, recorded as a failure.

This module is the one place a confirmed Razorpay payment for a website booking is settled, from any of
the three ways it can reach us - the browser, the webhook, or `reconcile_razorpay` asking Razorpay
directly. A payment that beat its hold confirms the booking as before. One that arrived after it:
  * the rooms are still free  -> the booking is REINSTATED (confirmed again), audited;
  * the rooms have been sold   -> the money is still recorded as paid, the booking stays cancelled, and a
                                  HIGH fraud-dashboard alert tells the owner to refund or rebook.
Never "failed": the guest paid.
"""
import json
import logging
from datetime import datetime, timedelta

from sqlalchemy import text

from models import Booking, BookingItem, FraudAlert, Payment, RoomType
from utils import availability
from utils.audit import write_audit

logger = logging.getLogger(__name__)

# The two reasons utils/booking_cleanup.expire_pending_bookings writes. A booking cancelled for any
# OTHER reason (the guest cancelled, an admin did) is never brought back by a payment.
TIMEOUT_REASONS = ("Payment timeout", "Payment retry timeout")
_RECONCILE_LOCK_KEY = 0x0052A20C


def _rooms_still_free(db, booking) -> bool:
    """Every room type on the booking still has capacity for its dates (this booking excluded:
    it is cancelled, so it is not counted)."""
    items = db.query(BookingItem).filter(BookingItem.booking_id == booking.booking_id).all()
    if not items:
        return False
    need = {}
    for it in items:
        need[it.room_type_id] = need.get(it.room_type_id, 0) + int(it.quantity or 1)
    holds, _unmapped = availability.ota_draft_holds(db, booking.check_in, booking.check_out)
    for rt_id, qty in need.items():
        availability.lock_room_type(db, rt_id)
        rt = db.query(RoomType).filter(RoomType.room_type_id == rt_id).first()
        if rt is None:
            return False
        free = (int(rt.total_rooms or 0)
                - int(availability.booked_qty(db, rt_id, booking.check_in, booking.check_out, lock=True))
                - int(availability.out_of_service_count(db, rt_id))
                - int(holds.get(rt_id, 0)))
        if free < qty:
            return False
    return True


def settle_website_payment(db, payment: Payment, booking, gateway_payment_id, source: str) -> dict:
    """Record a CONFIRMED Razorpay payment for a website booking. Does not commit.

    Returns {"outcome": ...}:
      already       the payment was already recorded as paid
      confirmed     the hold was still open; the booking is confirmed (the normal case)
      reinstated    the hold had expired, the rooms were still free, the booking is confirmed again
      paid_no_room  the hold had expired and the rooms are gone: money recorded, owner alerted
      recorded      paid, booking in some other state (already confirmed, etc.)
    """
    if payment.status == "paid":
        return {"outcome": "already", "booking_id": payment.booking_id}
    if gateway_payment_id:
        payment.payment_id_gateway = gateway_payment_id
    payment.status = "paid"
    if booking is None:
        return {"outcome": "recorded", "booking_id": payment.booking_id}

    if booking.status in ("pending_payment", "payment_pending"):
        booking.status = "confirmed"
        return {"outcome": "confirmed", "booking_id": booking.booking_id}

    if booking.status == "cancelled" and (booking.cancel_reason or "") in TIMEOUT_REASONS:
        was = {"status": booking.status, "cancel_reason": booking.cancel_reason,
               "cancelled_at": str(booking.cancelled_at) if booking.cancelled_at else None}
        if _rooms_still_free(db, booking):
            booking.status = "confirmed"
            booking.cancelled_at = None
            booking.cancel_reason = None
            write_audit(db, None, "payment.late_payment_reinstated", "booking", booking.booking_id,
                        before=was, after={"status": "confirmed", "payment_id": payment.payment_id,
                                           "gateway_payment_id": gateway_payment_id, "via": source},
                        client="system")
            logger.warning(f"Late payment {payment.payment_id} reinstated booking {booking.booking_id} ({source})")
            return {"outcome": "reinstated", "booking_id": booking.booking_id}
        db.add(FraudAlert(
            type="paid_after_expiry", severity="high", booking_id=booking.booking_id,
            detail=json.dumps({
                "payment_id": payment.payment_id, "gateway_payment_id": gateway_payment_id,
                "amount": float(payment.amount or 0), "via": source,
                "note": ("The guest PAID after the 15-minute hold had cancelled this booking, and its "
                         "rooms have since been sold. Refund the guest from Razorpay, or rebook them."),
            })))
        write_audit(db, None, "payment.late_payment_no_room", "booking", booking.booking_id,
                    before=was, after={"payment_id": payment.payment_id, "amount": float(payment.amount or 0),
                                       "gateway_payment_id": gateway_payment_id, "via": source},
                    client="system")
        logger.error(f"Late payment {payment.payment_id} for booking {booking.booking_id}: rooms gone — refund needed")
        return {"outcome": "paid_no_room", "booking_id": booking.booking_id}

    return {"outcome": "recorded", "booking_id": booking.booking_id}


def reconcile_razorpay(db, days: int = 14, dry_run: bool = True) -> dict:
    """Ask Razorpay about every recent website order we still think is unpaid.

    For each `created` / `failed` website payment in the last `days`, fetch the order's payments; any
    `captured` / `authorized` one is money the guest really paid. dry_run lists them; otherwise each is
    settled through `settle_website_payment` and committed. Safe to run any number of times."""
    from routers.payments import get_razorpay_client
    client = get_razorpay_client()
    if client is None:
        return {"configured": False, "checked": 0, "found": []}
    since = datetime.utcnow() - timedelta(days=max(1, int(days)))
    rows = (db.query(Payment)
            .filter(Payment.gateway == "razorpay", Payment.status.in_(("created", "failed")),
                    Payment.order_id.isnot(None), Payment.collect_method.is_(None),
                    Payment.created_at >= since)
            .order_by(Payment.payment_id).all())
    found, errors = [], 0
    for p in rows:
        try:
            items = (client.order.payments(p.order_id) or {}).get("items") or []
        except Exception as e:                       # one bad order must not stop the sweep
            errors += 1
            logger.warning(f"Razorpay reconcile: order {p.order_id} lookup failed: {e}")
            continue
        paid = [x for x in items if x.get("status") in ("captured", "authorized")]
        if not paid:
            continue
        gw = paid[0]
        booking = db.query(Booking).filter(Booking.booking_id == p.booking_id).first()
        entry = {"payment_id": p.payment_id, "booking_id": p.booking_id, "order_id": p.order_id,
                 "gateway_payment_id": gw.get("id"), "gateway_status": gw.get("status"),
                 "amount": float(p.amount or 0),
                 "guest_name": booking.display_guest_name if booking else None,
                 "booking_status": booking.status if booking else None,
                 "check_in": str(booking.check_in) if booking else None,
                 "check_out": str(booking.check_out) if booking else None,
                 "outcome": "would_settle"}
        if not dry_run:
            locked = db.query(Payment).filter(Payment.payment_id == p.payment_id).with_for_update().first()
            b = db.query(Booking).filter(Booking.booking_id == locked.booking_id).with_for_update().first()
            res = settle_website_payment(db, locked, b, gw.get("id"), source="razorpay_reconcile")
            write_audit(db, None, "payment.razorpay_reconciled", "payment", locked.payment_id,
                        after={"order_id": locked.order_id, "gateway_payment_id": gw.get("id"),
                               "outcome": res["outcome"]}, client="system")
            db.commit()
            entry["outcome"] = res["outcome"]
            if res["outcome"] in ("confirmed", "reinstated"):
                confirm_in_background(res["booking_id"])
        found.append(entry)
    return {"configured": True, "checked": len(rows), "found": found, "errors": errors, "dry_run": dry_run}


def confirm_in_background(booking_id: int):
    """v6m.7: send the confirmation (e-mail + WhatsApp) off the request / sweep thread."""
    import threading
    from services.booking_confirmation import send_website_confirmation
    threading.Thread(target=send_website_confirmation, args=(booking_id,), daemon=True).start()


def reconcile_razorpay_locked(db, days: int = 2) -> dict | None:
    """The scheduled sweep: one worker at a time (uvicorn runs several), never raises."""
    lock_conn = None
    try:
        # v6m.6: the lock on its OWN connection (closing it releases it). On the ORM session, a commit
        # inside the sweep returns that connection to the pool and the unlock runs elsewhere.
        lock_conn = db.get_bind().connect()
        got = lock_conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": _RECONCILE_LOCK_KEY}).scalar()
        if not got:
            return None
        return reconcile_razorpay(db, days=days, dry_run=False)
    except Exception as e:
        logger.error(f"Razorpay reconcile sweep failed: {e}")
        try:
            db.rollback()
        except Exception:
            pass
        return None
    finally:
        if lock_conn is not None:
            try:
                lock_conn.close()
            except Exception:
                pass
