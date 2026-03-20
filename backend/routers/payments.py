# backend/routers/payment.py
from fastapi import APIRouter, Depends, HTTPException, Request, BackgroundTasks
from sqlalchemy.orm import Session
from decimal import Decimal
import razorpay
import os
import logging
from datetime import datetime
import hmac
import hashlib
from slowapi import Limiter
from slowapi.util import get_remote_address
from database import SessionLocal
from models import Booking, Payment
from utils.pdf_generator import generate_booking_pdf
from utils.email_service import send_booking_email

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/payments",
    tags=["Payments"]
)

# Rate limiting
limiter = Limiter(key_func=get_remote_address)

# DB dependency
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Initialize Razorpay client
razorpay_client = razorpay.Client(
    auth=(os.getenv("RAZORPAY_KEY_ID"), os.getenv("RAZORPAY_KEY_SECRET"))
)


@router.post("/create-order/{booking_id}")
@limiter.limit("10/minute")
def create_payment_order(request: Request, booking_id: int, db: Session = Depends(get_db)):
    """Create payment order with rate limiting (10 per minute)"""
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
@limiter.limit("10/minute")
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

    booking = db.query(Booking).filter(
        Booking.booking_id == payment.booking_id
    ).first()

    # ❌ If payment failed
    if payment_status != "success":
        payment.status = "failed"
        booking.status = "payment_failed"   # ✅ FIXED
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

    async def process_confirmation(booking, payment):
        pdf_path = generate_booking_pdf(booking, payment)
        await send_booking_email(booking, pdf_path)

        import os
        if os.path.exists(pdf_path):
            os.remove(pdf_path)

    background_tasks.add_task(process_confirmation, booking, payment)

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
    booking = db.query(Booking).filter(
        Booking.booking_id == booking_id
    ).first()

    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")

    if booking.status not in ["payment_failed", "pending_payment"]:
        raise HTTPException(status_code=400, detail="Retry not allowed")

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

    booking.status = "pending_payment"

    db.add(payment)
    db.commit()
    db.refresh(payment)

    return {
        "order_id": order["id"],
        "amount": amount_paise,
        "currency": "INR",
        "key": os.getenv("RAZORPAY_KEY_ID")
    }
