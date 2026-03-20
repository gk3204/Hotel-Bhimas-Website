from datetime import datetime, timedelta, timezone
from models import Booking

def expire_pending_bookings(db):
    expiry_time = datetime.now(timezone.utc) - timedelta(minutes=15)

    db.query(Booking).filter(
        Booking.status == "pending_payment",
        Booking.created_at < expiry_time
    ).update({
        Booking.status: "cancelled",
        Booking.cancelled_at: datetime.now(timezone.utc),
        Booking.cancel_reason: "Payment timeout"
    }, synchronize_session=False)

    db.commit()
