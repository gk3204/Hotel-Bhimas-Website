from datetime import datetime
from sqlalchemy import Column, Integer, String, Numeric, Date, ForeignKey, DateTime, TIMESTAMP, Boolean, Float, Index
from sqlalchemy.sql import func
from database import Base
from sqlalchemy.orm import relationship

class User(Base):
    __tablename__ = "users"
    user_id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, nullable=False, index=True)
    password_hash = Column(String, nullable=False)
    role = Column(String(20), nullable=False, index=True)
    created_at = Column(TIMESTAMP, server_default=func.now())

class RoomType(Base):
    __tablename__ = "room_types"
    room_type_id = Column(Integer, primary_key=True, index=True)
    name = Column(String(50), nullable=False)
    price_per_night = Column(Numeric(10, 2), nullable=False)
    gst_percent = Column(Float, nullable=False)
    max_occupancy = Column(Integer, nullable=False)
    total_rooms = Column(Integer, nullable=False, default=1)  # Total count of rooms available
    is_active = Column(Boolean, default=True, index=True)
    created_at = Column(TIMESTAMP, server_default=func.now())

class Room(Base):
    __tablename__ = "rooms"
    room_id = Column(Integer, primary_key=True, index=True)
    room_number = Column(String(10), unique=True, nullable=False, index=True)
    room_type_id = Column(Integer, ForeignKey("room_types.room_type_id"), index=True)
    status = Column(String(20), nullable=False, default="vacant", index=True)

class Guest(Base):
    __tablename__ = "guests"
    guest_id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    phone = Column(String(15), nullable=False, index=True)
    email = Column(String(100), index=True)
    created_at = Column(TIMESTAMP, server_default=func.now())

class Booking(Base):
    __tablename__ = "bookings"
    booking_id = Column(Integer, primary_key=True, index=True)
    guest_id = Column(Integer, ForeignKey("guests.guest_id"), index=True)
    check_in = Column(Date, nullable=False, index=True)
    check_out = Column(Date, nullable=False, index=True)
    booking_source = Column(String(20), default="online")
    status = Column(String(30), nullable=False, index=True)
    base_amount = Column(Numeric(10, 2))
    gst_amount = Column(Numeric(10, 2))
    total_amount = Column(Numeric(10, 2), nullable=False)
    convenience_fee = Column(Numeric(10, 2))
    convenience_gst = Column(Numeric(10, 2))
    grand_total = Column(Numeric(10, 2))
    cancelled_at = Column(DateTime)
    cancel_reason = Column(String(100))
    created_at = Column(TIMESTAMP, server_default=func.now(), index=True)
    updated_at = Column(TIMESTAMP, server_default=func.now(), onupdate=func.now())

    guest = relationship("Guest")
    booking_items = relationship("BookingItem", cascade="all, delete-orphan", back_populates="booking")

    # Composite index for date range queries
    __table_args__ = (
        Index('idx_booking_date_range', 'check_in', 'check_out'),
        Index('idx_booking_status_date', 'status', 'check_in'),
    )


class BookingItem(Base):
    __tablename__ = "booking_items"
    booking_item_id = Column(Integer, primary_key=True, index=True)
    booking_id = Column(Integer, ForeignKey("bookings.booking_id"), nullable=False, index=True)
    room_type_id = Column(Integer, ForeignKey("room_types.room_type_id"), nullable=False, index=True)
    room_id = Column(Integer, ForeignKey("rooms.room_id"), nullable=True, index=True)
    quantity = Column(Integer, default=1, nullable=False)
    base_amount = Column(Numeric(10, 2))
    gst_amount = Column(Numeric(10, 2))
    total_amount = Column(Numeric(10, 2), nullable=False)
    created_at = Column(TIMESTAMP, server_default=func.now())

    room_type = relationship("RoomType")
    room = relationship("Room")
    booking = relationship("Booking", back_populates="booking_items")

class Payment(Base):
    __tablename__ = "payments"
    payment_id = Column(Integer, primary_key=True)
    booking_id = Column(Integer, ForeignKey("bookings.booking_id"), index=True)
    gateway = Column(String, index=True)   # razorpay
    order_id = Column(String, index=True)
    payment_id_gateway = Column(String, index=True)
    amount = Column(Numeric(10, 2))
    currency = Column(String, default="INR")
    status = Column(String, index=True)    # created | paid | failed
    created_at = Column(DateTime, server_default=func.now(), index=True)


class RoomTypeAvailability(Base):
    __tablename__ = "room_type_availability"
    id = Column(Integer, primary_key=True, index=True)
    room_type_id = Column(Integer, ForeignKey("room_types.room_type_id"), nullable=False, index=True)
    date = Column(Date, nullable=False, index=True)
    is_available = Column(Boolean, default=True, index=True)
    reason = Column(String(100))  # optional (maintenance, full, admin blocked)
    created_at = Column(TIMESTAMP, server_default=func.now())

    # Composite index for date availability queries
    __table_args__ = (
        Index('idx_availability_room_date', 'room_type_id', 'date'),
    )


class AuditLog(Base):
    __tablename__ = "audit_logs"
    log_id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.user_id"), index=True)
    action = Column(String, nullable=False)
    created_at = Column(TIMESTAMP, server_default=func.now(), index=True)


class ContactEnquiry(Base):
    __tablename__ = "contact_enquiries"
    enquiry_id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False, index=True)
    email = Column(String(100), nullable=False, index=True)
    phone = Column(String(15), nullable=False, index=True)
    message = Column(String(1000), nullable=False)
    status = Column(String(20), default="pending", index=True)  # pending, replied, spam
    created_at = Column(TIMESTAMP, server_default=func.now(), index=True)


class WebhookEvent(Base):
    __tablename__ = "webhook_events"
    webhook_event_id = Column(Integer, primary_key=True, index=True)
    event_id = Column(String(100), nullable=False, unique=True, index=True)  # Unique ID from Razorpay
    event_type = Column(String(50), nullable=False, index=True)  # payment.authorized, payment.failed, etc
    booking_id = Column(Integer, ForeignKey("bookings.booking_id"), nullable=True, index=True)
    payment_id = Column(String(100), nullable=True, index=True)  # Razorpay payment ID
    status = Column(String(20), default="processed", index=True)  # processed, failed, pending
    raw_data = Column(String, nullable=True)  # Store raw webhook payload for debugging
    created_at = Column(TIMESTAMP, server_default=func.now(), index=True)
