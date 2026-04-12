# backend/routers/payment.py
from fastapi import APIRouter, Depends, HTTPException, Request, BackgroundTasks
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func
from decimal import Decimal
import os
import logging
from datetime import datetime
import hmac
import hashlib
from database import SessionLocal
from models import Booking, Payment, BookingItem, RoomType
from utils.pdf_generator import generate_booking_pdf
from utils.email_service import send_booking_email
from utils.auth_utils import require_reception_or_admin

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
    ).first()

    if not payment:
        raise HTTPException(status_code=404, detail="Payment record not found")

    booking = db.query(Booking).options(
        joinedload(Booking.guest),
        joinedload(Booking.booking_items).joinedload(BookingItem.room_type)
    ).filter(
        Booking.booking_id == payment.booking_id
    ).first()

    # ❌ If payment failed
    if payment_status != "success":
        payment.status = "failed"
        booking.status = "cancelled"   # 🔒 Cancel booking to free up rooms for other users
        booking.cancel_reason = "Payment failed - user can retry with new booking"
        booking.cancelled_at = datetime.utcnow()
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
        booking.status = "payment_failed"   # ✅ FIXED
        db.commit()
        raise HTTPException(status_code=400, detail="Invalid signature")

    # ✅ Payment success
    payment.payment_id_gateway = razorpay_payment_id
    payment.status = "paid"
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
        "check_out": booking.check_out,
        "status": booking.status,
        "base_amount": float(booking.base_amount),
        "gst_amount": float(booking.gst_amount),
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

    booking.status = "payment_failed"
    db.commit()

    return {"message": "Booking marked as failed"}

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

    # Allow retry for cancelled, payment_failed, or pending_payment bookings
    if booking.status not in ["payment_failed", "pending_payment", "cancelled"]:
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
    booking.cancelled_at = None  # Clear cancellation if it was cancelled
    booking.cancel_reason = None

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
