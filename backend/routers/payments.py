# backend/routers/payment.py
from fastapi import APIRouter, Depends, HTTPException, Request, BackgroundTasks
from fastapi.responses import FileResponse, Response
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func
from decimal import Decimal
import os
import uuid
import logging
from datetime import datetime, timedelta
import hmac
import hashlib
from database import SessionLocal
from models import Booking, Payment, BookingItem, RoomType, WebhookEvent, Folio, FolioCharge, CashShift, User
from schemas import DeskPaymentRecord, PaymentRefundRequest, ExcessReturnRequest, DeskCollectRequest
from utils.pdf_generator import generate_booking_pdf, generate_payment_receipt_pdf
from utils.email_service import send_booking_email
from utils.auth_utils import require_reception_or_admin, get_current_user
from utils.audit import write_audit, _resolve_user_id
from utils.owner_otp import consume_otp
from utils.settings import get_desk_pay_config
from routers.folio import _recompute as _folio_recompute

# Try to import razorpay, but allow app to run without it
try:
    import razorpay
    RAZORPAY_AVAILABLE = True
except ImportError:
    RAZORPAY_AVAILABLE = False
    logging.warning("⚠️ Razorpay not available - payment features may be limited")

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/payments",
    tags=["Payments"]
)

# DB dependency
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_razorpay_client():
    if not RAZORPAY_AVAILABLE:
        return None

    key = os.getenv("RAZORPAY_KEY_ID")
    secret = os.getenv("RAZORPAY_KEY_SECRET")

    if not key or not secret:
        logger.error("❌ Razorpay keys missing")
        return None

    try:
        return razorpay.Client(auth=(key, secret))
    except Exception as e:
        logger.error(f"❌ Razorpay init failed: {e}")
        return None


def process_razorpay_refund(db: Session, payment_id: int, refund_amount: float, reason: str):
    """
    Process a refund through Razorpay API
    Returns: (success: bool, refund_id: str, message: str)
    """
    try:
        # Fetch payment from database
        payment = db.query(Payment).filter(Payment.payment_id == payment_id).first()
        if not payment:
            return False, None, "Payment not found"
        
        # Check if payment was successful
        if payment.status != "paid":
            return False, None, f"Cannot refund payment with status: {payment.status}"
        
        # Check if already refunded
        if payment.refund_status and payment.refund_status != "failed":
            return False, payment.refund_id, "Payment already refunded"
        
        # Get Razorpay client
        razorpay_client = get_razorpay_client()
        if not razorpay_client:
            return False, None, "Razorpay service unavailable"
        
        # Call Razorpay refund API
        refund_response = razorpay_client.refund.create(
            data={
                "payment_id": payment.payment_id_gateway,  # Razorpay payment ID
                "amount": int(refund_amount * 100),  # Razorpay expects amount in paise
                "notes": {
                    "reason": reason,
                    "refund_type": "admin_initiated"
                }
            }
        )
        
        # Update payment record with refund details
        payment.refund_id = refund_response.get("id")
        payment.refund_amount = Decimal(str(refund_amount))
        payment.refund_status = "completed"
        payment.refund_reason = reason
        db.commit()
        
        logger.info(f"✅ Refund processed: Payment {payment_id}, Refund ID: {payment.refund_id}, Amount: ₹{refund_amount}")
        return True, payment.refund_id, "Refund processed successfully"
        
    except Exception as e:
        logger.error(f"❌ Refund failed for payment {payment_id}: {str(e)}")
        return False, None, f"Refund failed: {str(e)}"


# 📊 GET all payments (admin only)
@router.get("/", dependencies=[Depends(require_reception_or_admin)])
def get_all_payments(db: Session = Depends(get_db)):
    """Get all payments"""
    try:
        payments = db.query(Payment).order_by(Payment.created_at.desc()).all()
        return payments
    except Exception as e:
        logger.error(f"Error fetching payments: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to fetch payments")


# 📊 GET payment details
@router.get("/{payment_id}", dependencies=[Depends(require_reception_or_admin)])
def get_payment(payment_id: int, db: Session = Depends(get_db)):
    """Get specific payment details (reception/admin only — financial + refund data)."""
    payment = db.query(Payment).filter(
        Payment.payment_id == payment_id
    ).first()

    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")

    return payment


@router.post("/create-order/{booking_id}")
def create_payment_order(booking_id: int, db: Session = Depends(get_db)):
    razorpay_client = get_razorpay_client()

    if not razorpay_client:
        logger.error("Razorpay is not available")
        raise HTTPException(status_code=503, detail="Payment service unavailable")
    try:
        booking = db.query(Booking).filter(
            Booking.booking_id == booking_id,
            Booking.status == "pending_payment"  # booking must be pending
        ).first()

        if not booking:
            logger.warning(f"Payment order creation failed for booking {booking_id}: Not found or already confirmed")
            raise HTTPException(status_code=404, detail="Booking not found or already confirmed")

        # Amount in paise (Razorpay expects integer paise)
        amount_paise = int(Decimal(booking.grand_total) * 100)

        # Create Razorpay order
        order = razorpay_client.order.create({
            "amount": amount_paise,
            "currency": "INR",
            "payment_capture": 1  # auto capture
        })

        # 🔒 Check if payment order already exists (prevent duplicate orders)
        existing_payment = db.query(Payment).filter(
            Payment.booking_id == booking.booking_id,
            Payment.status.in_(["created", "paid"])
        ).first()
        
        if existing_payment:
            logger.warning(f"Duplicate payment order attempt for booking {booking_id}")
            raise HTTPException(
                status_code=409, 
                detail="Payment already in progress for this booking. Please complete or cancel it first."
            )
        
        # Save payment record
        payment = Payment(
            booking_id=booking.booking_id,
            gateway="razorpay",
            order_id=order["id"],
            amount=booking.grand_total,
            currency="INR",
            status="created",
            created_at=datetime.utcnow()
        )
        db.add(payment)
        db.commit()
        db.refresh(payment)

        logger.info(f"Payment order {order['id']} created for booking {booking_id}")
        
        return {
            "order_id": order["id"],
            "amount": amount_paise,
            "currency": "INR",
            "key": os.getenv("RAZORPAY_KEY_ID")
        }
    except Exception as e:
        logger.error(f"Error creating payment order for booking {booking_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to create payment order")


@router.post("/verify")
async def verify_payment(
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    data = await request.json()

    razorpay_order_id = data.get("razorpay_order_id")
    razorpay_payment_id = data.get("razorpay_payment_id")
    razorpay_signature = data.get("razorpay_signature")
    payment_status = data.get("status")

    if not razorpay_order_id:
        raise HTTPException(status_code=400, detail="Invalid payload")

    payment = db.query(Payment).filter(
        Payment.order_id == razorpay_order_id
    ).with_for_update().first()  # 🔒 Lock to prevent double-verify

    if not payment:
        raise HTTPException(status_code=404, detail="Payment record not found")
    
    # 🔒 Prevent double verification
    if payment.status in ["paid", "failed"]:
        logger.warning(f"Attempt to re-verify already processed payment: {razorpay_order_id}")
        raise HTTPException(
            status_code=400,
            detail="Payment has already been processed"
        )

    # 🔒 Lock booking WITHOUT joinedload (PostgreSQL limitation with FOR UPDATE + outer joins)
    booking = db.query(Booking).filter(
        Booking.booking_id == payment.booking_id
    ).with_for_update().first()  # 🔒 Lock to prevent concurrent modification

    # Guard: a payment with no matching booking would NPE on the status writes below.
    if not booking:
        logger.error(f"Payment {payment.payment_id} references missing booking {payment.booking_id}")
        raise HTTPException(status_code=404, detail="Booking for this payment not found")

    # ❌ If payment failed
    if payment_status != "success":
        payment.status = "failed"
        booking.status = "payment_pending"   # 🔒 Keep booking reserved for retry, don't free rooms
        db.commit()
        return {"message": "Payment failed"}

    # ✅ Verify signature
    generated_signature = hmac.new(
        key=os.getenv("RAZORPAY_KEY_SECRET").encode(),
        msg=f"{razorpay_order_id}|{razorpay_payment_id}".encode(),
        digestmod=hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(generated_signature, razorpay_signature):
        payment.status = "failed"
        booking.status = "cancelled"   # 🔒 Cancel to free rooms
        booking.cancelled_at = datetime.utcnow()
        booking.cancel_reason = "Invalid payment signature"
        db.commit()
        raise HTTPException(status_code=400, detail="Invalid signature")

    # ✅ Payment success
    payment.payment_id_gateway = razorpay_payment_id
    payment.status = "paid"
    
    # 🔒 Check booking hasn't expired before confirming
    if booking.status == "cancelled":
        logger.warning(f"Cannot confirm expired booking {booking.booking_id}")
        payment.status = "failed"
        db.commit()
        raise HTTPException(
            status_code=400,
            detail="Booking has expired. Please create a new booking."
        )
    
    booking.status = "confirmed"

    db.commit()

    logger.info(f"✅ Payment verified for booking {booking.booking_id}. Starting background confirmation task...")

    # Extract booking data BEFORE session closes (prevent detached instance issues)
    booking_data = {
        "booking_id": booking.booking_id,
        "guest_name": booking.guest.name,
        "guest_email": booking.guest.email,
        "guest_phone": booking.guest.phone,
        "check_in": booking.check_in,
        "check_in_time": str(booking.check_in_time) if booking.check_in_time else None,
        "check_out": booking.check_out,
        "status": booking.status,
        "base_amount": float(booking.base_amount),
        "gst_amount": float(booking.gst_amount),
        "discount_amount": float(booking.discount_amount or 0),
        "total_amount": float(booking.total_amount),
        "convenience_fee": float(booking.convenience_fee),
        "convenience_gst": float(booking.convenience_gst),
        "grand_total": float(booking.grand_total),
        "booking_items": [
            {
                "room_type_name": item.room_type.name,
                "room_type_id": item.room_type.room_type_id,
                "quantity": item.quantity,
                "base_amount": float(item.base_amount),
                "gst_amount": float(item.gst_amount),
                "total_amount": float(item.total_amount),
                "price_per_night": float(item.room_type.price_per_night)
            }
            for item in booking.booking_items
        ]
    }

    # Extract payment data BEFORE session closes (detached instance error prevention)
    payment_data = {
        "payment_id_gateway": payment.payment_id_gateway,
        "order_id": payment.order_id,
        "gateway": payment.gateway,
        "status": payment.status
    }

    def process_confirmation(booking_data, payment_data):
        """Synchronous confirmation task - PDF generation and email sending"""
        try:
            logger.info(f"🔄 Background task started for booking {booking_data['booking_id']}")
            logger.info(f"Starting confirmation process for booking {booking_data['booking_id']}")

            # Generate PDF
            try:
                pdf_path = generate_booking_pdf(booking_data, payment_data)
                logger.info(f"PDF generated successfully: {pdf_path}")
            except Exception as e:
                logger.error(f"PDF generation failed for booking {booking_data['booking_id']}: {str(e)}", exc_info=True)
                return

            # Send email (now synchronous)
            try:
                send_booking_email(booking_data, pdf_path)
                logger.info(f"Email sent successfully for booking {booking_data['booking_id']}")
            except Exception as e:
                logger.error(f"Email sending failed for booking {booking_data['booking_id']}: {str(e)}", exc_info=True)
                return

            # Cleanup PDF
            try:
                if os.path.exists(pdf_path):
                    os.remove(pdf_path)
                    logger.info(f"PDF cleaned up: {pdf_path}")
            except Exception as e:
                logger.warning(f"Failed to cleanup PDF {pdf_path}: {str(e)}")

        except Exception as e:
            logger.error(f"Unexpected error in confirmation process for booking {booking_data['booking_id']}: {str(e)}", exc_info=True)

    logger.info(f"📋 Registering background task for booking {booking.booking_id}")
    background_tasks.add_task(process_confirmation, booking_data, payment_data)
    logger.info(f"✨ Background task added to queue for booking {booking.booking_id}")

    return {"message": "Payment successful"}

@router.post("/mark-failed/{booking_id}")
def mark_failed(booking_id: int, db: Session = Depends(get_db)):
    booking = db.query(Booking).filter(
        Booking.booking_id == booking_id
    ).first()

    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")

    booking.status = "payment_pending"
    db.commit()

    return {"message": "Booking marked as payment pending"}

@router.post("/retry/{booking_id}")
def retry_payment(booking_id: int, db: Session = Depends(get_db)):
    razorpay_client = get_razorpay_client()
    booking = db.query(Booking).options(
        joinedload(Booking.booking_items)
    ).filter(
        Booking.booking_id == booking_id
    ).first()

    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")

    # Allow retry for payment_pending or pending_payment bookings
    if booking.status not in ["payment_pending", "pending_payment"]:
        raise HTTPException(status_code=400, detail="Retry not allowed for this booking status")

    # 🔒 Re-validate availability before allowing retry (prevent overbooking)
    for item in booking.booking_items:
        # Check if this room type is still available for the dates
        booked = db.query(func.sum(BookingItem.quantity)).filter(
            BookingItem.room_type_id == item.room_type_id,
            BookingItem.booking_id.in_(
                db.query(Booking.booking_id).filter(
                    Booking.status.in_(["confirmed", "pending_payment"]),
                    Booking.booking_id != booking_id,  # Exclude current booking
                    Booking.check_in < booking.check_out,
                    Booking.check_out > booking.check_in
                )
            )
        ).scalar() or 0
        
        # Check room type availability  
        room_type = db.query(RoomType).filter(
            RoomType.room_type_id == item.room_type_id
        ).first()
        
        if booked + item.quantity > room_type.total_rooms:
            raise HTTPException(
                status_code=409, 
                detail=f"Rooms no longer available for selected dates. Please create a new booking."
            )

    amount_paise = int(Decimal(booking.grand_total) * 100)

    order = razorpay_client.order.create({
        "amount": amount_paise,
        "currency": "INR",
        "payment_capture": 1
    })

    payment = Payment(
        booking_id=booking.booking_id,
        gateway="razorpay",
        order_id=order["id"],
        amount=booking.grand_total,
        currency="INR",
        status="created",
        created_at=datetime.utcnow()
    )

    # Reactivate the booking for retry
    booking.status = "pending_payment"

    db.add(payment)
    db.commit()
    db.refresh(payment)

    logger.info(f"Payment retry initiated for booking {booking_id} with new order {order['id']}")

    return {
        "order_id": order["id"],
        "amount": amount_paise,
        "currency": "INR",
        "key": os.getenv("RAZORPAY_KEY_ID")
    }

# 🔒 WEBHOOK ENDPOINT FOR RAZORPAY PAYMENT UPDATES
@router.post("/webhook")
async def payment_webhook(request: Request, db: Session = Depends(get_db)):
    """
    Razorpay webhook endpoint for payment status updates.
    Handles: payment.authorized / payment.failed (website checkout) and
    payment.captured / qr_code.credited / payment_link.paid (desk collect, prompt 19).
    Implements idempotency (WebhookEvent.event_id) to prevent duplicate processing.
    Requires RAZORPAY_WEBHOOK_SECRET in the environment.
    """
    
    try:
        # Get raw body for signature verification
        body = await request.body()
        data = await request.json()
        
        # 🔒 Verify webhook signature (CRITICAL for security)
        razorpay_signature = request.headers.get("X-Razorpay-Signature")
        webhook_secret = os.getenv("RAZORPAY_WEBHOOK_SECRET")
        
        if not webhook_secret:
            logger.error("❌ RAZORPAY_WEBHOOK_SECRET not configured")
            raise HTTPException(status_code=500, detail="Webhook secret not configured")
        
        # Recreate signature to verify it's from Razorpay
        generated_signature = hmac.new(
            key=webhook_secret.encode(),
            msg=body,
            digestmod=hashlib.sha256
        ).hexdigest()
        
        if not hmac.compare_digest(generated_signature, razorpay_signature):
            logger.warning(f"🚫 Invalid webhook signature from {request.client.host}")
            raise HTTPException(status_code=400, detail="Invalid signature")
        
        # Extract event details
        event = data.get("event")
        event_id = data.get("id")  # Unique webhook event ID from Razorpay
        created = data.get("created_at")
        
        logger.info(f"📥 Webhook received: {event} (ID: {event_id})")
        
        # 🔒 Idempotency check - prevent duplicate processing
        existing_event = db.query(WebhookEvent).filter(
            WebhookEvent.event_id == event_id
        ).first()
        
        if existing_event:
            logger.info(f"⚠️ Webhook {event_id} already processed - skipping")
            return {"status": "already_processed", "event_id": event_id}
        
        # Extract entities (Razorpay nests the real object under payload.<kind>.entity).
        payload = data.get("payload", {}) or {}
        payment_entity = (payload.get("payment", {}) or {}).get("entity", {}) or {}
        razorpay_order_id = payment_entity.get("order_id")
        razorpay_payment_id = payment_entity.get("id")

        # Locate our Payment row per event type. Website checkout matches by order_id;
        # desk-collect (prompt 19) has no order_id — a UPI QR matches by qr_code_id and a
        # payment link by payment_link_id.
        payment = None
        if event in ("payment.authorized", "payment.failed", "payment.captured"):
            if razorpay_order_id:
                payment = db.query(Payment).filter(Payment.order_id == razorpay_order_id).first()
        elif event == "qr_code.credited":
            qr_id = (payload.get("qr_code", {}) or {}).get("entity", {}).get("id")
            if qr_id:
                payment = db.query(Payment).filter(Payment.qr_code_id == qr_id).first()
        elif event == "payment_link.paid":
            pl_id = (payload.get("payment_link", {}) or {}).get("entity", {}).get("id")
            if pl_id:
                payment = db.query(Payment).filter(Payment.payment_link_id == pl_id).first()

        if not payment:
            # Unknown/irrelevant event or a payment we don't track (e.g. a duplicate
            # payment.captured for a QR we match via qr_code.credited). Record it so it is
            # never reprocessed, and ack.
            reason = "payment_not_found" if event in (
                "payment.authorized", "payment.failed", "payment.captured",
                "qr_code.credited", "payment_link.paid") else "ignored"
            db.add(WebhookEvent(event_id=event_id, event_type=event,
                                payment_id=razorpay_payment_id, status=reason, raw_data=str(data)))
            db.commit()
            return {"status": reason, "event_id": event_id}

        booking = db.query(Booking).filter(Booking.booking_id == payment.booking_id).first()

        # 🎯 Success events
        if event == "payment.authorized":
            # Website checkout flow (unchanged): mark paid + confirm the booking.
            if booking and booking.status == "cancelled":
                logger.warning(f"❌ Cannot confirm expired booking {booking.booking_id}")
                payment.status = "failed"
                webhook_event_status = "booking_expired"
            else:
                _mark_payment_paid(db, payment, razorpay_payment_id)
                if booking:
                    booking.status = "confirmed"
                webhook_event_status = "processed"
            db.commit()

        elif event in ("payment.captured", "qr_code.credited", "payment_link.paid"):
            # qr_code.credited / payment_link.paid are always desk-collect. payment.captured also
            # fires for WEBSITE orders (which payment.authorized already settled) — only a payment
            # raised at the desk carries a collect_method, so gate the desk-collect handling on it.
            is_desk = payment.collect_method in ("upi_qr", "link")
            if is_desk:
                # Desk-collect (prompt 19): the folio is marked paid ONLY here, by the gateway.
                logger.info(f"✅ Desk collection captured: payment {payment.payment_id} via {event}")
                _mark_payment_paid(db, payment, razorpay_payment_id)
                webhook_event_status = "processed"
                db.commit()
                write_audit(db, None, "payment.desk_collect_captured", "payment", payment.payment_id,
                            after={"event": event, "gateway_payment_id": razorpay_payment_id,
                                   "booking_id": payment.booking_id, "amount": float(payment.amount or 0)},
                            client="system", commit=True)
            else:
                # Website capture: payment.authorized already marked this paid + confirmed the
                # booking. Settle idempotently; do NOT mislabel it as a desk collection in the audit.
                logger.info(f"↩️ Website capture for payment {payment.payment_id} via {event} "
                            f"(already settled by payment.authorized)")
                _mark_payment_paid(db, payment, razorpay_payment_id)
                webhook_event_status = "processed"
                db.commit()

        # 🎯 Handle payment.failed event
        elif event == "payment.failed":
            logger.warning(f"❌ Payment failed: {razorpay_payment_id} for booking {payment.booking_id}")
            payment.status = "failed"
            if booking and booking.status not in ("checked_in", "checked_out"):
                booking.status = "payment_pending"  # keep the reservation for retry (website flow)
            db.commit()
            webhook_event_status = "processed"

        else:
            logger.info(f"⏭️ Ignoring event type: {event}")
            webhook_event_status = "ignored"

        # 🔒 Store webhook event to prevent duplicate processing
        db.add(WebhookEvent(
            event_id=event_id,
            event_type=event,
            booking_id=booking.booking_id if booking else None,
            payment_id=razorpay_payment_id,
            status=webhook_event_status,
            raw_data=str(data)
        ))
        db.commit()

        logger.info(f"✅ Webhook {event_id} processed successfully")

        return {
            "status": "received",
            "event_id": event_id,
            "event": event,
            "booking_id": booking.booking_id if booking else None
        }
    
    except Exception as e:
        logger.error(f"❌ Webhook processing error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Webhook processing failed")


# =====================================================================
# DESK PAYMENTS & REFUNDS (prompts 06 + 08).
# Records payments taken at the front desk (cash / card-machine / UPI / bank),
# issues receipt PDFs, processes refunds, and links cash to the open cash shift.
# Conventions (prompt 08 — LOCKED):
#   - Refunds are per-PAYMENT and single-shot: one refund per Payment row,
#     retryable only after refund_status == 'failed'. Amount <= payment amount.
#   - Refund approval: REFUND_REQUIRES_ADMIN env (default true, read at call
#     time) -> the caller's own JWT must be role 'admin'. Prompt 11 swaps this
#     interim gate for owner WhatsApp OTP approval.
#   - Folio: payments post NEGATIVE type='payment' lines; refunds post POSITIVE
#     type='payment' lines (keeps folio.total = charges-only). Allowed while
#     folio.status == 'open' (invoicing does NOT freeze payments); a settled
#     folio is skipped silently for refunds (post-checkout refunds are legal)
#     but rejects new payments.
#   - Cash-shift linkage is LINK-ONLY: payments.shift_id / refund_shift_id
#     point at the most recent open cash_shifts row (or NULL if none). Prompt 12
#     computes drawer totals from these links at shift close — nothing here
#     mutates CashShift counters.
# =====================================================================


def _open_shift(db: Session):
    """Most recent open cash shift, or None. Station-agnostic (single property);
    prompt 12 adds open/close endpoints and per-station scoping if needed."""
    return (db.query(CashShift).filter(CashShift.status == "open")
            .order_by(CashShift.opened_at.desc()).first())


def require_refund_permission(user=Depends(get_current_user)):
    """Refund gate. Two approval modes, read at call time so flags change without a restart:
      - REFUND_REQUIRES_OWNER_OTP (default false, prompt 11): owner OTP IS the approval, so
        reception+admin pass the gate and the endpoint consumes the OTP in its body.
      - else REFUND_REQUIRES_ADMIN (default true, prompt 08 interim): admin JWT required.
    """
    if os.getenv("REFUND_REQUIRES_OWNER_OTP", "false").strip().lower() not in ("false", "0", "no"):
        if user.get("role") not in ["admin", "reception"]:
            raise HTTPException(status_code=403, detail="Access denied")
        return user
    requires_admin = os.getenv("REFUND_REQUIRES_ADMIN", "true").strip().lower() not in ("false", "0", "no")
    if requires_admin:
        if user.get("role") != "admin":
            raise HTTPException(status_code=403, detail="Refund requires admin approval")
    elif user.get("role") not in ["admin", "reception"]:
        raise HTTPException(status_code=403, detail="Access denied")
    return user


def require_excess_return_permission(user=Depends(get_current_user)):
    """Excess-return gate: EXCESS_RETURN_REQUIRES_ADMIN (default FALSE) -> reception may
    return an overpaid deposit. Unlike an arbitrary refund, the amount is computed
    server-side and capped at the folio's genuine credit, so it is structurally safe."""
    requires_admin = os.getenv("EXCESS_RETURN_REQUIRES_ADMIN", "false").strip().lower() not in ("false", "0", "no")
    if requires_admin:
        if user.get("role") != "admin":
            raise HTTPException(status_code=403, detail="Excess return requires admin approval")
    elif user.get("role") not in ["admin", "reception"]:
        raise HTTPException(status_code=403, detail="Access denied")
    return user

def total_paid(db: Session, booking_id: int) -> float:
    """Sum of recorded payments for a booking (the anti-fraud 'recorded payment' gate
    used by check-in and card issue). Recorded = Payment.status == 'paid'."""
    total = (
        db.query(func.coalesce(func.sum(Payment.amount), 0))
        .filter(Payment.booking_id == booking_id, Payment.status == "paid")
        .scalar()
    )
    return float(total or 0)


# =====================================================================
# INTEGRATED DESK PAYMENTS — UPI QR + Razorpay link (prompt 19).
# A gateway-confirmed collection raised at the desk: the guest scans a
# dynamic UPI QR or opens a payment link; Razorpay's WEBHOOK (not staff)
# marks the Payment paid and posts the folio credit. Cash falls back to
# the proven recorded-cash path (record_desk_payment). Anti-fraud: the
# folio is settled only by the gateway event, never by a staff assertion.
# Fee policy is config-driven (get_desk_pay_config): UPI QR ~0% (fee off),
# cards/links may pass ~2% + GST (fee on). Reuses Payment / WebhookEvent /
# _open_shift / total_paid / _folio_recompute — no new subsystems.
# =====================================================================


def _compute_desk_fee(cfg: dict, method: str, base: float, apply_fee_override=None) -> float:
    """Convenience fee (GST-inclusive) the guest pays on top of `base` for a non-cash desk
    collection, per config. UPI QR is ~0% MDR so its fee is off by default; cards/links can
    pass the ~2% MDR + GST-on-fee. Cash is always 0. `apply_fee_override` (from the request)
    forces the per-method toggle on/off when not None."""
    if method == "cash":
        return 0.0
    enabled = {"upi_qr": cfg["fee_on_upi"], "link": cfg["fee_on_link"]}.get(method, False)
    if apply_fee_override is not None:
        enabled = bool(apply_fee_override)
    if not enabled or cfg["fee_card_percent"] <= 0:
        return 0.0
    fee = base * cfg["fee_card_percent"] / 100.0
    fee_with_gst = fee * (1.0 + cfg["fee_gst_percent"] / 100.0)
    return round(fee_with_gst, 2)


def _mark_payment_paid(db: Session, payment: Payment, gateway_payment_id, method=None):
    """Shared 'a gateway payment succeeded' routine for the desk-collect webhook events
    (qr_code.credited / payment_link.paid / payment.captured) and the live-reconcile fallback.
    Idempotent: a Payment already 'paid' is left untouched. If the booking's folio is open it
    posts the convenience-fee charge (if any) + the NEGATIVE 'payment' credit line and recomputes
    the balance — mirroring record_desk_payment. Does NOT commit (the caller owns the txn)."""
    if payment.status == "paid":
        return
    if gateway_payment_id:
        payment.payment_id_gateway = gateway_payment_id
    payment.status = "paid"
    if method:                       # desk QR/link already carry method='upi'; website path passes None
        payment.method = method
    folio = db.query(Folio).filter(Folio.booking_id == payment.booking_id).first()
    if folio and folio.status == "open":
        fee = float(payment.convenience_fee_amount or 0)
        if fee > 0:
            db.add(FolioCharge(
                folio_id=folio.id, type="misc",
                description=f"Convenience fee ({payment.collect_method or payment.method})",
                qty=1, unit_price=fee, amount=fee, gst_percent=0,
                posted_by=payment.collected_by))
        amt = round(float(payment.amount or 0), 2)
        ref = f" ({gateway_payment_id})" if gateway_payment_id else ""
        db.add(FolioCharge(
            folio_id=folio.id, type="payment",
            description=f"Desk {payment.collect_method or 'payment'} — {payment.method}{ref}",
            qty=1, unit_price=-amt, amount=-amt, gst_percent=0,
            posted_by=payment.collected_by))
        _folio_recompute(db, folio)


def _try_live_reconcile(db: Session, payment: Payment):
    """Best-effort belt-and-suspenders to the webhook: ask Razorpay directly whether a still-
    pending desk-collect payment has been captured (or has expired). The webhook is primary; this
    covers webhook delay/misconfiguration when the desktop polls GET /{id}/status. Never raises."""
    try:
        client = get_razorpay_client()
        if not client:
            return
        captured_gw_id = None
        expired = False
        if payment.qr_code_id:
            pays = client.qrcode.fetch_all_payments(payment.qr_code_id)
            for item in (pays.get("items") or []):
                if item.get("status") == "captured":
                    captured_gw_id = item.get("id")
                    break
            if not captured_gw_id:
                qr = client.qrcode.fetch(payment.qr_code_id)
                if qr.get("status") == "closed":   # single-use QR auto-closes on pay; else expired
                    expired = True
        elif payment.payment_link_id:
            pl = client.payment_link.fetch(payment.payment_link_id)
            if pl.get("status") == "paid":
                pmts = pl.get("payments") or []
                captured_gw_id = (pmts[0].get("payment_id") if pmts else None) or payment.payment_id_gateway or "paid"
            elif pl.get("status") in ("expired", "cancelled"):
                expired = True
        if captured_gw_id:
            _mark_payment_paid(db, payment, captured_gw_id, method="upi")
            db.commit()
            write_audit(db, None, "payment.desk_collect_reconciled", "payment", payment.payment_id,
                        after={"gateway_payment_id": captured_gw_id, "source": "poll"},
                        client="system", commit=True)
        elif expired and payment.status == "created":
            payment.status = "expired"
            db.commit()
    except Exception as e:
        db.rollback()
        logger.warning(f"live reconcile failed for payment {getattr(payment, 'payment_id', '?')}: {e}")


@router.post("/record")
def record_desk_payment(data: DeskPaymentRecord, db: Session = Depends(get_db),
                        user=Depends(require_reception_or_admin)):
    """Record a desk payment against a booking. Idempotent on client_ref (offline
    outbox re-flushes return the original row). If the booking has an open folio,
    a matching negative 'payment' line is posted so the balance updates."""
    try:
        # Idempotency: an outbox re-flush with the same client_ref returns the original.
        if data.client_ref:
            existing = db.query(Payment).filter(Payment.client_ref == data.client_ref).first()
            if existing:
                return _desk_payment_response(db, existing, duplicate=True)

        booking = db.query(Booking).filter(Booking.booking_id == data.booking_id).first()
        if not booking:
            raise HTTPException(status_code=404, detail="Booking not found")
        if booking.status not in ("confirmed", "checked_in"):
            raise HTTPException(
                status_code=409,
                detail=f"Cannot record a payment against a '{booking.status}' booking")

        payment = Payment(
            booking_id=booking.booking_id,
            gateway="desk",
            method=data.method,
            payment_id_gateway=data.reference,
            amount=Decimal(str(round(data.amount, 2))),
            currency="INR",
            status="paid",
            client_ref=data.client_ref,
            collected_by=_resolve_user_id(db, user),
        )
        if data.method == "cash":
            # Hard cash gate (prompt 12): cash must land in an open drawer so it is
            # attributable to a shift. Non-cash (card/upi/bank) is unaffected.
            shift = _open_shift(db)
            if not shift:
                raise HTTPException(
                    status_code=409,
                    detail="No cash shift is open — open a cash shift before recording cash.")
            payment.shift_id = shift.id
        db.add(payment)
        db.flush()

        # Post a credit line on the open folio (payments stay allowed after invoicing;
        # a settled folio takes no more money).
        folio = db.query(Folio).filter(Folio.booking_id == booking.booking_id).first()
        if folio:
            if folio.status != "open":
                raise HTTPException(status_code=409, detail="Folio is settled — no further payments")
            ref = f" ({data.reference})" if data.reference else ""
            db.add(FolioCharge(
                folio_id=folio.id,
                type="payment",
                description=f"Desk payment — {data.method}{ref}",
                qty=1,
                unit_price=-round(data.amount, 2),
                amount=-round(data.amount, 2),
                gst_percent=0,
                posted_by=_resolve_user_id(db, user),
            ))
            _folio_recompute(db, folio)

        db.commit()
        write_audit(db, user, "payment.desk_record", "payment", payment.payment_id,
                    after={"booking_id": booking.booking_id, "amount": float(payment.amount),
                           "method": data.method, "reference": data.reference,
                           "client_ref": data.client_ref, "shift_id": payment.shift_id},
                    client="desktop", commit=True)
        logger.info(f"✅ Desk payment ₹{payment.amount} ({data.method}) recorded for booking {booking.booking_id}")
        # Best-effort WhatsApp payment receipt (prompt 15) — must not break the payment.
        try:
            from utils.settings import get_setting
            guest = booking.guest
            if guest and guest.phone and \
               str(get_setting(db, "wa_receipt_enabled", "true")).strip().lower() not in ("false", "0", "no", ""):
                from utils import whatsapp_service
                whatsapp_service.send_template(
                    db, guest.phone, "payment_receipt",
                    {"guest_name": guest.name, "amount": f"{float(payment.amount):.2f}",
                     "method": data.method, "booking_ref": str(booking.booking_id)},
                    guest_id=guest.guest_id, booking_id=booking.booking_id,
                    client_ref=f"receipt:{payment.payment_id}")
        except Exception as e:
            logger.warning(f"payment receipt WhatsApp failed: {e}")
        return _desk_payment_response(db, payment)

    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        logger.error(f"❌ Desk payment failed: {e}", exc_info=True)
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to record payment")


def _desk_payment_response(db: Session, payment: Payment, duplicate: bool = False):
    folio = db.query(Folio).filter(Folio.booking_id == payment.booking_id).first()
    return {
        "payment_id": payment.payment_id,
        "booking_id": payment.booking_id,
        "amount": float(payment.amount),
        "method": payment.method,
        "status": payment.status,
        "duplicate": duplicate,
        "shift_id": payment.shift_id,
        "total_paid": total_paid(db, payment.booking_id),
        "folio_id": folio.id if folio else None,
        "folio_balance": float(folio.balance or 0) if folio else None,
    }


# --------------------------------------------------- desk collect (UPI QR / link)

def _desk_collect_response(db: Session, payment: Payment, short_url=None, expires_at=None):
    folio = db.query(Folio).filter(Folio.booking_id == payment.booking_id).first()
    return {
        "payment_id": payment.payment_id,
        "booking_id": payment.booking_id,
        "collect_method": payment.collect_method,
        "method": payment.method,
        "status": payment.status,
        "amount": float(payment.amount or 0),
        "fee": float(payment.convenience_fee_amount or 0),
        "qr_code_id": payment.qr_code_id,
        "payment_link_id": payment.payment_link_id,
        "short_url": short_url,
        "expires_at": expires_at,
        "total_paid": total_paid(db, payment.booking_id),
        "folio_id": folio.id if folio else None,
        "folio_balance": float(folio.balance or 0) if folio else None,
    }


@router.post("/desk/collect")
def desk_collect(data: DeskCollectRequest, db: Session = Depends(get_db),
                 user=Depends(require_reception_or_admin)):
    """Raise a gateway-confirmed desk collection for the exact amount (prompt 19).
      - upi_qr -> a Razorpay dynamic UPI QR (near-0% MDR; preferred). Poll GET /{id}/status.
      - link   -> a Razorpay payment link (send via WhatsApp/SMS). Confirmed by the same webhook.
      - cash   -> falls back to the proven recorded-cash path (shift gate + folio credit + receipt).
    A convenience fee is added per config for card-like methods (off on UPI by default) and shown
    transparently. For upi_qr/link the folio is marked paid ONLY by the webhook, never here."""
    cfg = get_desk_pay_config(db)

    # Idempotency: an outbox re-flush / double-click with the same client_ref returns the original.
    if data.client_ref:
        existing = db.query(Payment).filter(Payment.client_ref == data.client_ref).first()
        if existing:
            return _desk_collect_response(db, existing)

    booking = db.query(Booking).filter(Booking.booking_id == data.booking_id).first()
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")
    if booking.status not in ("confirmed", "checked_in"):
        raise HTTPException(status_code=409,
                            detail=f"Cannot collect against a '{booking.status}' booking")

    base = round(data.amount, 2)

    # ---- cash fallback: reuse the recorded-cash path unchanged (shift gate + folio + receipt) ----
    if data.method == "cash":
        if not cfg["cash_enabled"]:
            raise HTTPException(status_code=409, detail="Cash collection is disabled")
        rec = record_desk_payment(
            DeskPaymentRecord(booking_id=data.booking_id, amount=base, method="cash",
                              reference=data.reference, client_ref=data.client_ref),
            db=db, user=user)
        rec["collect_method"] = "cash"
        return rec

    client = get_razorpay_client()
    if not client:
        raise HTTPException(status_code=503, detail="Online payments are unavailable (Razorpay not configured)")

    fee = _compute_desk_fee(cfg, data.method, base, data.apply_fee)
    charge = round(base + fee, 2)
    guest = booking.guest

    # ---- UPI dynamic QR ----
    if data.method == "upi_qr":
        if not cfg["upi_qr_enabled"]:
            raise HTTPException(status_code=409, detail="UPI QR collection is disabled")
        close_by = int((datetime.utcnow() + timedelta(minutes=cfg["qr_expiry_minutes"])).timestamp())
        try:
            qr = client.qrcode.create({
                "type": "upi_qr",
                "name": ((guest.name if guest else "") or "Hotel Bhimas guest")[:40],
                "usage": "single_use",
                "fixed_amount": True,
                "payment_amount": int(round(charge * 100)),
                "description": f"Booking {booking.booking_id} — Hotel Bhimas",
                "close_by": close_by,
                "notes": {"booking_id": str(booking.booking_id), "desk": "reception"},
            })
        except Exception as e:
            logger.error(f"❌ Razorpay QR create failed for booking {booking.booking_id}: {e}", exc_info=True)
            raise HTTPException(status_code=502, detail="Failed to create the UPI QR")
        payment = Payment(
            booking_id=booking.booking_id, gateway="razorpay", method="upi",
            collect_method="upi_qr", amount=Decimal(str(charge)),
            convenience_fee_amount=Decimal(str(fee)), currency="INR", status="created",
            client_ref=data.client_ref, collected_by=_resolve_user_id(db, user),
            qr_code_id=qr.get("id"), qr_image_url=qr.get("image_url"))
        db.add(payment)
        db.commit()
        write_audit(db, user, "payment.desk_collect", "payment", payment.payment_id,
                    after={"booking_id": booking.booking_id, "method": "upi_qr",
                           "amount": float(charge), "fee": float(fee), "qr_code_id": payment.qr_code_id},
                    client="desktop", commit=True)
        logger.info(f"🧾 Desk UPI QR ₹{charge} raised for booking {booking.booking_id} (qr {payment.qr_code_id})")
        return _desk_collect_response(db, payment, expires_at=close_by)

    # ---- Razorpay payment link ----
    if not cfg["link_enabled"]:
        raise HTTPException(status_code=409, detail="Payment link collection is disabled")
    try:
        plink = client.payment_link.create({
            "amount": int(round(charge * 100)), "currency": "INR",
            "description": f"Booking {booking.booking_id} — Hotel Bhimas",
            "customer": {"name": (guest.name if guest else "") or "",
                         "contact": (guest.phone if guest else "") or "",
                         "email": (guest.email if guest else "") or ""},
            "notify": {"sms": False, "email": False},
            "reminder_enable": False,
            "notes": {"booking_id": str(booking.booking_id), "desk": "reception"},
        })
    except Exception as e:
        logger.error(f"❌ Razorpay payment link create failed for booking {booking.booking_id}: {e}", exc_info=True)
        raise HTTPException(status_code=502, detail="Failed to create the payment link")
    payment = Payment(
        booking_id=booking.booking_id, gateway="razorpay", method="upi",
        collect_method="link", amount=Decimal(str(charge)),
        convenience_fee_amount=Decimal(str(fee)), currency="INR", status="created",
        client_ref=data.client_ref, collected_by=_resolve_user_id(db, user),
        payment_link_id=plink.get("id"))
    db.add(payment)
    db.commit()
    write_audit(db, user, "payment.desk_collect", "payment", payment.payment_id,
                after={"booking_id": booking.booking_id, "method": "link",
                       "amount": float(charge), "fee": float(fee), "payment_link_id": payment.payment_link_id},
                client="desktop", commit=True)
    logger.info(f"🔗 Desk payment link ₹{charge} raised for booking {booking.booking_id} (plink {payment.payment_link_id})")
    return _desk_collect_response(db, payment, short_url=plink.get("short_url"))


@router.post("/desk/link")
def desk_link(data: DeskCollectRequest, db: Session = Depends(get_db),
              user=Depends(require_reception_or_admin)):
    """Create a Razorpay payment link for the exact amount (prompt 19). Thin alias of
    /desk/collect with method='link' so the desktop can call a clear URL; returns short_url."""
    data.method = "link"
    return desk_collect(data, db=db, user=user)


@router.get("/{payment_id}/status")
def payment_status(payment_id: int, db: Session = Depends(get_db),
                   user=Depends(require_reception_or_admin)):
    """Poll a desk-collect payment until it is paid/failed/expired (prompt 19). While still
    'created' it also does a best-effort live Razorpay reconcile as a fallback to the webhook."""
    payment = db.query(Payment).filter(Payment.payment_id == payment_id).first()
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")
    if payment.status == "created" and (payment.qr_code_id or payment.payment_link_id):
        _try_live_reconcile(db, payment)
        payment = db.query(Payment).filter(Payment.payment_id == payment_id).first()
    folio = db.query(Folio).filter(Folio.booking_id == payment.booking_id).first()
    return {
        "payment_id": payment.payment_id,
        "booking_id": payment.booking_id,
        "collect_method": payment.collect_method,
        "method": payment.method,
        "status": payment.status,          # created | paid | failed | expired
        "amount": float(payment.amount or 0),
        "fee": float(payment.convenience_fee_amount or 0),
        "total_paid": total_paid(db, payment.booking_id),
        "folio_id": folio.id if folio else None,
        "folio_balance": float(folio.balance or 0) if folio else None,
    }


@router.get("/{payment_id}/qr.png")
def payment_qr_png(payment_id: int, db: Session = Depends(get_db),
                   user=Depends(require_reception_or_admin)):
    """Serve the QR image bytes for a UPI-QR desk collection (prompt 19). Proxies Razorpay's
    hosted QR image so the desktop can reuse its authed byte-download channel (no direct CDN hit)."""
    payment = db.query(Payment).filter(Payment.payment_id == payment_id).first()
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")
    if not payment.qr_image_url:
        raise HTTPException(status_code=404, detail="No QR image for this payment")
    try:
        import requests
        r = requests.get(payment.qr_image_url, timeout=10)
        r.raise_for_status()
        content = r.content
        ctype = r.headers.get("Content-Type", "image/png")
    except Exception as e:
        logger.error(f"❌ QR image fetch failed for payment {payment_id}: {e}", exc_info=True)
        raise HTTPException(status_code=502, detail="Failed to fetch the QR image")
    return Response(content=content, media_type=ctype)


@router.post("/{payment_id}/send-link")
def send_payment_link(payment_id: int, db: Session = Depends(get_db),
                      user=Depends(require_reception_or_admin)):
    """WhatsApp the Razorpay payment link to the guest (prompt 19 + 15). Best-effort/stub-off
    when WhatsApp env is unset (a message row is still logged). Idempotent on the payment."""
    payment = db.query(Payment).filter(Payment.payment_id == payment_id).first()
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")
    if payment.collect_method != "link" or not payment.payment_link_id:
        raise HTTPException(status_code=409, detail="This payment is not a payment link")
    booking = db.query(Booking).filter(Booking.booking_id == payment.booking_id).first()
    guest = booking.guest if booking else None
    if not guest or not guest.phone:
        raise HTTPException(status_code=400, detail="The guest has no phone number on file")
    short_url = None
    try:
        client = get_razorpay_client()
        if client:
            pl = client.payment_link.fetch(payment.payment_link_id)
            short_url = pl.get("short_url")
    except Exception as e:
        logger.warning(f"payment link fetch failed for send-link {payment_id}: {e}")
    if not short_url:
        raise HTTPException(status_code=502, detail="Could not fetch the payment link URL")
    try:
        from utils import whatsapp_service
        whatsapp_service.send_template(
            db, guest.phone, "payment_link",
            {"guest_name": guest.name, "amount": f"{float(payment.amount):.2f}", "link": short_url},
            guest_id=guest.guest_id, booking_id=booking.booking_id,
            client_ref=f"paylink:{payment.payment_id}")
    except Exception as e:
        logger.warning(f"payment link WhatsApp send failed for {payment_id}: {e}")
        raise HTTPException(status_code=502, detail="Failed to send the WhatsApp message")
    return {"sent": True, "short_url": short_url, "phone": guest.phone}


# ------------------------------------------------------------------ refunds

@router.post("/refund")
def refund_payment(data: PaymentRefundRequest, db: Session = Depends(get_db),
                   user=Depends(require_refund_permission)):
    """Refund a recorded payment (prompt 08). Razorpay originals go through the
    existing gateway refund; desk originals are recorded as a desk payout
    (mode cash|upi|bank). Posts a POSITIVE 'payment' line on the open folio.
    Online-only from the desktop (no offline outbox)."""
    payment = db.query(Payment).filter(Payment.payment_id == data.payment_id).first()
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")
    if payment.status != "paid":
        raise HTTPException(status_code=409,
                            detail=f"Cannot refund a payment with status '{payment.status}'")
    if payment.refund_status and payment.refund_status != "failed":
        raise HTTPException(status_code=409, detail="Payment already refunded")
    amount = round(data.amount, 2)
    if amount > float(payment.amount):
        raise HTTPException(status_code=400, detail="Refund exceeds payment amount")

    # Owner-approval OTP (prompt 11): opt-in via REFUND_REQUIRES_OWNER_OTP. Consumed here so
    # the code is only spent if the refund itself commits. 403 if missing/invalid/expired.
    if os.getenv("REFUND_REQUIRES_OWNER_OTP", "false").strip().lower() not in ("false", "0", "no"):
        consume_otp(db, data.owner_otp_id, data.owner_otp_code, "refund", user)

    folio = db.query(Folio).filter(Folio.booking_id == payment.booking_id).first()
    # Backlog v2 TBC-1: post the credit line to the folio even once the folio is
    # SETTLED. The common refund happens after checkout, and the old open-folio
    # guard meant those refunds never touched the folio at all — the ledger and the
    # folio disagreed forever. The folio is append-only in spirit, and the invoice's
    # GST snapshot stays frozen, so a late credit line is safe: `_invoice_payload`
    # lists anything posted after the invoice under its own heading.
    post_to_folio = bool(folio)

    if payment.gateway == "razorpay":
        # Gateway path: helper validates again, calls Razorpay, sets refund_* and COMMITS.
        ok, refund_id, message = process_razorpay_refund(db, payment.payment_id, amount, data.reason)
        if not ok:
            raise HTTPException(status_code=502, detail=message)
    else:
        # Desk path: record the payout (no gateway). All in one transaction below.
        refund_id = f"DESK-{uuid.uuid4().hex[:8].upper()}"
        payment.refund_id = refund_id
        payment.refund_amount = Decimal(str(amount))
        payment.refund_status = "completed"
        payment.refund_reason = data.reason
        payment.refund_mode = data.mode
        payment.refund_reference = data.reference
        if data.mode == "cash":
            shift = _open_shift(db)
            payment.refund_shift_id = shift.id if shift else None

    folio_posting_failed = False
    try:
        if post_to_folio:
            db.add(FolioCharge(
                folio_id=folio.id,
                type="payment",
                description=f"Refund — {data.reason} ({refund_id})",
                qty=1,
                unit_price=amount,
                amount=amount,
                gst_percent=0,
                posted_by=_resolve_user_id(db, user),
            ))
            _folio_recompute(db, folio)
        db.commit()
        write_audit(db, user, "payment.refund", "payment", payment.payment_id,
                    before={"refund_status": None},
                    after={"booking_id": payment.booking_id, "refund_id": refund_id,
                           "refund_amount": amount, "refund_status": payment.refund_status,
                           "reason": data.reason, "mode": data.mode if payment.gateway != "razorpay" else None,
                           "reference": data.reference, "gateway": payment.gateway,
                           "refund_shift_id": payment.refund_shift_id},
                    client="desktop", commit=True)
    except Exception as e:
        db.rollback()
        if payment.gateway == "razorpay":
            # Money already moved at the gateway (helper committed) — don't 500 and
            # mislead the operator; surface the bookkeeping failure instead.
            logger.critical(f"❌ Refund folio/audit posting failed AFTER gateway refund "
                            f"(payment {payment.payment_id}, refund {refund_id}): {e}", exc_info=True)
            folio_posting_failed = True
        else:
            logger.error(f"❌ Desk refund failed for payment {payment.payment_id}: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail="Failed to record refund")

    db.expire_all()
    payment = db.query(Payment).filter(Payment.payment_id == data.payment_id).first()
    folio = db.query(Folio).filter(Folio.booking_id == payment.booking_id).first()
    logger.info(f"✅ Refund ₹{amount} recorded for payment {payment.payment_id} "
                f"(booking {payment.booking_id}, {refund_id})")
    return {
        "payment_id": payment.payment_id,
        "booking_id": payment.booking_id,
        "refund_id": refund_id,
        "refund_amount": float(payment.refund_amount or amount),
        "refund_status": payment.refund_status,
        "gateway": payment.gateway,
        "folio_posting_failed": folio_posting_failed,
        "total_paid": total_paid(db, payment.booking_id),
        "folio_id": folio.id if folio else None,
        "folio_balance": float(folio.balance or 0) if folio else None,
    }


@router.post("/return-excess")
def return_excess(data: ExcessReturnRequest, db: Session = Depends(get_db),
                  user=Depends(require_excess_return_permission)):
    """Return an overpaid advance/deposit (prompt 08b). The amount is computed
    server-side (= the open folio's credit balance) and auto-allocated across the
    booking's refundable DESK payments newest-first, stamping the normal refund_*
    fields on each. Posts ONE positive 'payment' folio line so the balance lands
    on 0 and the standard checkout gate passes. Gateway (Razorpay) credits are
    deliberately not auto-returned — use POST /payments/refund per payment."""
    booking = db.query(Booking).filter(Booking.booking_id == data.booking_id).first()
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")
    folio = db.query(Folio).filter(Folio.booking_id == data.booking_id).first()
    if not folio or folio.status != "open":
        raise HTTPException(status_code=409, detail="No open folio for this booking")

    balance = round(float(folio.balance or 0), 2)
    excess = round(-balance, 2)
    if excess <= 0:
        raise HTTPException(status_code=409,
                            detail=f"No excess to return — folio balance is ₹{balance:,.2f}")

    # Plan the allocation first (no side effects until it fully covers the excess).
    candidates = (db.query(Payment)
                  .filter(Payment.booking_id == data.booking_id,
                          Payment.gateway == "desk",
                          Payment.status == "paid")
                  .filter((Payment.refund_status.is_(None)) | (Payment.refund_status == "failed"))
                  .order_by(Payment.created_at.desc(), Payment.payment_id.desc())
                  .all())
    allocations = []
    remaining = excess
    for p in candidates:
        if remaining <= 0:
            break
        take = round(min(remaining, float(p.amount or 0)), 2)
        if take <= 0:
            continue
        allocations.append((p, take))
        remaining = round(remaining - take, 2)
    if remaining > 0:
        raise HTTPException(
            status_code=409,
            detail=f"Excess ₹{excess:,.2f} exceeds refundable desk payments — "
                   f"refund the online payment via Refund (admin) instead")

    try:
        shift = _open_shift(db) if data.mode == "cash" else None
        alloc_out = []
        for p, take in allocations:
            p.refund_id = f"DESK-{uuid.uuid4().hex[:8].upper()}"
            p.refund_amount = Decimal(str(take))
            p.refund_status = "completed"
            p.refund_reason = "Excess deposit return"
            p.refund_mode = data.mode
            p.refund_reference = data.reference
            if data.mode == "cash":
                p.refund_shift_id = shift.id if shift else None
            alloc_out.append({"payment_id": p.payment_id, "amount": take, "refund_id": p.refund_id})

        db.add(FolioCharge(
            folio_id=folio.id,
            type="payment",
            description=f"Deposit return — {data.mode}",
            qty=1,
            unit_price=excess,
            amount=excess,
            gst_percent=0,
            posted_by=_resolve_user_id(db, user),
        ))
        _folio_recompute(db, folio)
        db.commit()
        write_audit(db, user, "payment.return_excess", "booking", booking.booking_id,
                    after={"excess": excess, "mode": data.mode, "reference": data.reference,
                           "allocations": alloc_out},
                    client="desktop", commit=True)
    except Exception as e:
        db.rollback()
        logger.error(f"❌ Excess return failed for booking {data.booking_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to record the excess return")

    logger.info(f"✅ Excess ₹{excess} returned ({data.mode}) for booking {booking.booking_id} "
                f"across {len(alloc_out)} payment(s)")
    return {
        "booking_id": booking.booking_id,
        "returned": excess,
        "mode": data.mode,
        "allocations": alloc_out,
        "folio_id": folio.id,
        "folio_balance": float(folio.balance or 0),
        "total_paid": total_paid(db, booking.booking_id),
    }


# ------------------------------------------------------- history & receipts

@router.get("/by-booking/{booking_id}")
def payments_by_booking(booking_id: int, db: Session = Depends(get_db),
                        user=Depends(require_reception_or_admin)):
    """Payment history for a booking (desk 'Payments' overlay). Includes all
    statuses (created/failed rows shown for context) plus refund details."""
    booking = db.query(Booking).filter(Booking.booking_id == booking_id).first()
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")

    payments = (db.query(Payment).filter(Payment.booking_id == booking_id)
                .order_by(Payment.created_at.desc()).all())
    total_refunded = round(sum(
        float(p.refund_amount or 0) for p in payments if p.refund_status == "completed"), 2)
    return {
        "booking_id": booking_id,
        "total_paid": total_paid(db, booking_id),
        "total_refunded": total_refunded,
        "payments": [{
            "payment_id": p.payment_id,
            "gateway": p.gateway,
            "method": p.method,
            "amount": float(p.amount or 0),
            "status": p.status,
            "reference": p.payment_id_gateway,
            "created_at": p.created_at.isoformat() if p.created_at else None,
            "shift_id": p.shift_id,
            "collected_by": p.collected_by,
            "refund_id": p.refund_id,
            "refund_amount": float(p.refund_amount) if p.refund_amount is not None else None,
            "refund_status": p.refund_status,
            "refund_reason": p.refund_reason,
            "refund_mode": p.refund_mode,
            "receipt_available": p.status == "paid",
        } for p in payments],
    }


@router.get("/{payment_id}/receipt/pdf")
def payment_receipt_pdf(payment_id: int, kind: str = "payment", db: Session = Depends(get_db),
                        user=Depends(require_reception_or_admin)):
    """Receipt PDF for a payment (kind=payment) or refund voucher (kind=refund).
    Regenerated on demand into the temp dir, same as the folio invoice PDF."""
    if kind not in ("payment", "refund"):
        raise HTTPException(status_code=400, detail="kind must be 'payment' or 'refund'")
    payment = db.query(Payment).filter(Payment.payment_id == payment_id).first()
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")
    if payment.status != "paid":
        raise HTTPException(status_code=400, detail="No receipt for an unpaid payment")
    if kind == "refund" and payment.refund_status != "completed":
        raise HTTPException(status_code=400, detail="Payment has no completed refund")

    booking = db.query(Booking).filter(Booking.booking_id == payment.booking_id).first()
    guest = booking.guest if booking else None
    collector = (db.query(User).filter(User.user_id == payment.collected_by).first()
                 if payment.collected_by else None)

    receipt_data = {
        "kind": kind,
        "payment_id": payment.payment_id,
        "receipt_no": (f"RCPT-{payment.payment_id}" if kind == "payment"
                       else f"RFND-{payment.payment_id}"),
        "booking_id": payment.booking_id,
        "guest_name": guest.name if guest else "—",
        "guest_phone": guest.phone if guest else None,
        "date": payment.created_at,
        "amount": float(payment.amount or 0),
        "method": payment.method or payment.gateway,
        "reference": payment.payment_id_gateway,
        "gateway": payment.gateway,
        "collected_by_name": (collector.full_name or collector.username) if collector else None,
        "refund_id": payment.refund_id,
        "refund_amount": float(payment.refund_amount) if payment.refund_amount is not None else None,
        "refund_reason": payment.refund_reason,
        "refund_mode": payment.refund_mode,
        "refund_reference": payment.refund_reference,
    }
    try:
        pdf_path = generate_payment_receipt_pdf(receipt_data)
    except Exception as e:
        logger.error(f"❌ Receipt PDF generation failed for payment {payment_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to generate receipt PDF")
    filename = f"{'receipt' if kind == 'payment' else 'refund-voucher'}-{payment_id}.pdf"
    return FileResponse(pdf_path, media_type="application/pdf", filename=filename)