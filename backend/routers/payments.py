# backend/routers/payment.py
from fastapi import APIRouter, Depends, HTTPException, Request, BackgroundTasks
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func
from decimal import Decimal
import os
import uuid
import logging
from datetime import datetime
import hmac
import hashlib
from database import SessionLocal
from models import Booking, Payment, BookingItem, RoomType, WebhookEvent, Folio, FolioCharge, CashShift, User
from schemas import DeskPaymentRecord, PaymentRefundRequest
from utils.pdf_generator import generate_booking_pdf, generate_payment_receipt_pdf
from utils.email_service import send_booking_email
from utils.auth_utils import require_reception_or_admin, get_current_user
from utils.audit import write_audit, _resolve_user_id
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
@router.get("/{payment_id}")
def get_payment(payment_id: int, db: Session = Depends(get_db)):
    """Get specific payment details"""
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
    Razorpay webhook endpoint for payment status updates
    Handles: payment.authorized, payment.failed
    Implements idempotency to prevent duplicate processing
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
        
        # Get payment data from webhook
        payment_data = data.get("payload", {}).get("payment", {})
        razorpay_order_id = payment_data.get("order_id")
        razorpay_payment_id = payment_data.get("id")
        
        if not razorpay_order_id:
            logger.warning(f"⚠️ Webhook missing order_id: {event_id}")
            return {"status": "ignored", "reason": "no_order_id"}
        
        # Find payment record
        payment = db.query(Payment).filter(
            Payment.order_id == razorpay_order_id
        ).first()
        
        if not payment:
            logger.warning(f"⚠️ Payment order not found: {razorpay_order_id}")
            # Store event anyway to prevent re-processing
            webhook_event = WebhookEvent(
                event_id=event_id,
                event_type=event,
                payment_id=razorpay_payment_id,
                status="payment_not_found",
                raw_data=str(data)
            )
            db.add(webhook_event)
            db.commit()
            return {"status": "payment_not_found"}
        
        booking = db.query(Booking).filter(
            Booking.booking_id == payment.booking_id
        ).first()
        
        # 🎯 Handle payment.authorized event
        if event == "payment.authorized":
            logger.info(f"✅ Payment authorized: {razorpay_payment_id} for booking {booking.booking_id}")
            
            payment.payment_id_gateway = razorpay_payment_id
            payment.status = "paid"
            
            # Check booking hasn't expired
            if booking.status == "cancelled":
                logger.warning(f"❌ Cannot confirm expired booking {booking.booking_id}")
                payment.status = "failed"
                webhook_event_status = "booking_expired"
            else:
                booking.status = "confirmed"
                webhook_event_status = "processed"
            
            db.commit()
        
        # 🎯 Handle payment.failed event
        elif event == "payment.failed":
            logger.warning(f"❌ Payment failed: {razorpay_payment_id} for booking {booking.booking_id}")
            
            payment.status = "failed"
            booking.status = "payment_pending"  # Keep reservation for retry
            
            db.commit()
            webhook_event_status = "processed"
        
        else:
            # Ignore other events
            logger.info(f"⏭️ Ignoring event type: {event}")
            webhook_event_status = "ignored"
        
        # 🔒 Store webhook event to prevent duplicate processing
        webhook_event = WebhookEvent(
            event_id=event_id,
            event_type=event,
            booking_id=booking.booking_id if booking else None,
            payment_id=razorpay_payment_id,
            status=webhook_event_status,
            raw_data=str(data)
        )
        db.add(webhook_event)
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
    """Refund gate: REFUND_REQUIRES_ADMIN (default true) -> admin only.
    Read at call time so the flag can be changed without a restart.
    Interim approval mechanism — prompt 11 replaces this with owner OTP."""
    requires_admin = os.getenv("REFUND_REQUIRES_ADMIN", "true").strip().lower() not in ("false", "0", "no")
    if requires_admin:
        if user.get("role") != "admin":
            raise HTTPException(status_code=403, detail="Refund requires admin approval")
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
            shift = _open_shift(db)
            payment.shift_id = shift.id if shift else None
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

    folio = db.query(Folio).filter(Folio.booking_id == payment.booking_id).first()
    folio_open = bool(folio and folio.status == "open")

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
        if folio_open:
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