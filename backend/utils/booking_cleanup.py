from datetime import datetime, timedelta, timezone
from models import Booking

def expire_pending_bookings(db):
    expiry_time = datetime.now(timezone.utc) - timedelta(minutes=15)

    # 🔒 Expire pending_payment bookings (initial 15 min window)
    expired_pending = db.query(Booking).filter(
        Booking.status == "pending_payment",
        Booking.created_at < expiry_time
    ).update({
        Booking.status: "cancelled",
        Booking.cancelled_at: datetime.now(timezone.utc),
        Booking.cancel_reason: "Payment timeout"
    }, synchronize_session=False)

    # 🔒 Expire payment_pending bookings (5 min retry window - shorter since user had chance to retry)
    payment_pending_expiry = datetime.now(timezone.utc) - timedelta(minutes=5)
    expired_retry = db.query(Booking).filter(
        Booking.status == "payment_pending",
        Booking.updated_at < payment_pending_expiry
    ).update({
        Booking.status: "cancelled",
        Booking.cancelled_at: datetime.now(timezone.utc),
        Booking.cancel_reason: "Payment retry timeout"
    }, synchronize_session=False)

    db.commit()
    
    total_expired = expired_pending + expired_retry
    if total_expired > 0:
        print(f"🔄 Auto-expired {total_expired} bookings: {expired_pending} pending, {expired_retry} retry")
    
    return total_expired
