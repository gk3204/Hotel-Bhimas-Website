from datetime import datetime
from sqlalchemy import Column, Integer, String, Numeric, Date, Time, ForeignKey, DateTime, TIMESTAMP, Boolean, Float, Index
from sqlalchemy.sql import func
from database import Base
from sqlalchemy.orm import relationship

class User(Base):
    __tablename__ = "users"
    user_id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, nullable=False, index=True)
    password_hash = Column(String, nullable=False)
    role = Column(String(20), nullable=False, index=True)  # admin | reception | housekeeper | maintenance | user
    created_at = Column(TIMESTAMP, server_default=func.now())
    # --- PMS foundation (additive) ---
    full_name = Column(String(100), nullable=True)      # staff display name
    is_active = Column(Boolean, default=True, index=True)  # deactivated staff can't log in
    pin = Column(String(10), nullable=True)             # optional quick desk-switch PIN (hashed later)
    last_login = Column(DateTime, nullable=True)

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
    room_number = Column(String(10), unique=True, nullable=False, index=True)  # label; must match the lock
    room_type_id = Column(Integer, ForeignKey("room_types.room_type_id"), index=True)
    status = Column(String(20), nullable=False, default="vacant", index=True)  # vacant|occupied|cleaning|inspected|maintenance|blocked
    # --- PMS: card-lock room identity (prompt 04) ---
    building = Column(Integer, nullable=False, default=1)   # card code BB
    floor = Column(Integer, nullable=False, default=1)      # card code FF
    max_cards = Column(Integer, nullable=False, default=4)  # per-room key limit
    lock_no = Column(String(20), nullable=True)            # optional physical lock id
    is_active = Column(Boolean, nullable=False, default=True, index=True)  # False = out of service (repair) → not bookable

class Guest(Base):
    __tablename__ = "guests"
    guest_id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    phone = Column(String(15), nullable=False, index=True)
    email = Column(String(100), index=True)
    id_type = Column(String(20), nullable=True)          # aadhaar|passport|driving_licence|voter_id|other (check-in KYC)
    id_number_masked = Column(String(30), nullable=True)  # masked, e.g. "****1234" — raw number is never stored
    created_at = Column(TIMESTAMP, server_default=func.now())

class Booking(Base):
    __tablename__ = "bookings"
    booking_id = Column(Integer, primary_key=True, index=True)
    guest_id = Column(Integer, ForeignKey("guests.guest_id"), index=True)
    check_in = Column(Date, nullable=False, index=True)
    check_in_time = Column(Time, nullable=True)  # Guest's expected arrival time
    check_out = Column(Date, nullable=False, index=True)
    booking_source = Column(String(20), default="online")
    status = Column(String(30), nullable=False, index=True)
    base_amount = Column(Numeric(10, 2))
    gst_amount = Column(Numeric(10, 2))
    discount_amount = Column(Numeric(10, 2), default=0)  # Total promotion discount applied
    total_amount = Column(Numeric(10, 2), nullable=False)
    convenience_fee = Column(Numeric(10, 2))
    convenience_gst = Column(Numeric(10, 2))
    grand_total = Column(Numeric(10, 2))
    cancelled_at = Column(DateTime)
    cancel_reason = Column(String(100))
    admin_cancelled = Column(Boolean, default=False, index=True)  # Flag for admin-initiated cancellation
    admin_cancelled_reason = Column(String(100))  # "Double booking error", "Guest request", "Technical issue", "Other"
    admin_notes = Column(String(500))  # Optional admin notes
    admin_cancelled_by = Column(String(50))  # Admin username/ID
    admin_cancelled_at = Column(DateTime)  # When admin cancelled
    checked_in_at = Column(DateTime, nullable=True)   # set by POST /reception/checkin (status -> "checked_in")
    checked_out_at = Column(DateTime, nullable=True)  # set by POST /reception/checkout (status -> "checked_out")
    created_at = Column(TIMESTAMP, server_default=func.now(), index=True)
    updated_at = Column(TIMESTAMP, server_default=func.now(), onupdate=func.now(), nullable=True)

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
    discount_amount = Column(Numeric(10, 2), default=0)  # Promotion discount applied to this line item
    total_amount = Column(Numeric(10, 2), nullable=False)
    created_at = Column(TIMESTAMP, server_default=func.now())

    room_type = relationship("RoomType")
    room = relationship("Room")
    booking = relationship("Booking", back_populates="booking_items")

class Payment(Base):
    __tablename__ = "payments"
    payment_id = Column(Integer, primary_key=True)
    booking_id = Column(Integer, ForeignKey("bookings.booking_id"), index=True)
    gateway = Column(String, index=True)   # razorpay | desk
    order_id = Column(String, index=True)
    payment_id_gateway = Column(String, index=True)
    amount = Column(Numeric(10, 2))
    currency = Column(String, default="INR")
    status = Column(String, index=True)    # created | paid | failed
    method = Column(String(20), nullable=True)  # desk payments: cash|card|upi|bank
    client_ref = Column(String(64), nullable=True, unique=True, index=True)  # desktop-generated uuid (offline outbox dedupe)
    refund_id = Column(String, nullable=True, index=True)  # Razorpay refund ID (desk refunds: DESK-xxxxxxxx)
    refund_amount = Column(Numeric(10, 2), nullable=True)  # Amount refunded
    refund_status = Column(String(50), nullable=True, index=True)  # "pending" | "completed" | "failed"
    refund_reason = Column(String(100), nullable=True)  # Admin's reason for refund
    shift_id = Column(Integer, ForeignKey("cash_shifts.id"), nullable=True, index=True)  # open cash shift a CASH payment was taken in (prompt 12 reconciles)
    refund_shift_id = Column(Integer, ForeignKey("cash_shifts.id"), nullable=True)  # open cash shift a CASH refund was paid out of
    collected_by = Column(Integer, ForeignKey("users.user_id"), nullable=True)  # desk user who took the money (from JWT)
    refund_mode = Column(String(20), nullable=True)  # desk refunds: cash|upi|bank (razorpay refunds leave NULL)
    refund_reference = Column(String(100), nullable=True)  # UPI / bank reference of the refund payout
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
    """Append-only audit trail. Write via utils/audit.py:write_audit(). Never UPDATE/DELETE rows here."""
    __tablename__ = "audit_logs"
    log_id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.user_id"), index=True)  # actor (nullable for system events)
    action = Column(String, nullable=False)          # e.g. "booking.cancel", "card.issue", "shift.close"
    entity_type = Column(String(50), nullable=True, index=True)  # "booking" | "card" | "folio" | ...
    entity_id = Column(String(50), nullable=True, index=True)    # id of the affected entity (string for flexibility)
    before = Column(String, nullable=True)           # JSON snapshot before the change (text)
    after = Column(String, nullable=True)            # JSON snapshot after the change (text)
    ip = Column(String(50), nullable=True)
    client = Column(String(20), nullable=True)       # "desktop" | "web" | "housekeeper" | "system"
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


class Promotion(Base):
    __tablename__ = "promotions"
    promotion_id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    discount_type = Column(String(10), nullable=False)          # "percent" | "flat"
    discount_value = Column(Numeric(10, 2), nullable=False)
    room_type_id = Column(Integer, ForeignKey("room_types.room_type_id"), nullable=True, index=True)  # null = all room types
    is_active = Column(Boolean, default=False, index=True)
    valid_from = Column(Date, nullable=True)                     # null = no start bound
    valid_to = Column(Date, nullable=True)                       # null = no end bound
    created_at = Column(TIMESTAMP, server_default=func.now())

    room_type = relationship("RoomType")


# =====================================================================
# PMS FOUNDATION TABLES (Milestone 0, prompt 01) — additive, new tables.
# Feature logic (endpoints) is added by later prompts; these define the schema.
# =====================================================================

class CardIssuance(Base):
    """Every physical key card cut by the reception desktop app (anti-fraud backbone)."""
    __tablename__ = "card_issuances"
    id = Column(Integer, primary_key=True, index=True)
    booking_id = Column(Integer, ForeignKey("bookings.booking_id"), nullable=True, index=True)
    room_id = Column(Integer, ForeignKey("rooms.room_id"), nullable=True, index=True)
    card_uid = Column(String(64), nullable=True, index=True)     # card serial / ReadCard id
    card_type = Column(String(20), nullable=False, default="guest", index=True)  # guest|checkout|master|floor|building|emergency|erase
    room_code = Column(String(16), nullable=True)               # BBFFRRAA written to the card
    valid_from = Column(DateTime, nullable=True)
    valid_to = Column(DateTime, nullable=True)                  # = expiry (checkout + grace)
    issued_by = Column(Integer, ForeignKey("users.user_id"), nullable=True, index=True)
    station_id = Column(String(50), nullable=True)             # which front-desk PC issued it
    issued_at = Column(TIMESTAMP, server_default=func.now(), index=True)
    status = Column(String(20), nullable=False, default="active", index=True)  # active|checked_out|erased|lost|superseded
    issue_type = Column(String(20), nullable=False, default="checkin")  # checkin|extra|lost_reissue|shift
    client_ref = Column(String(64), nullable=True, unique=True, index=True)  # desktop-generated uuid (offline outbox dedupe)


class Folio(Base):
    """Per-stay bill. One open folio per in-house booking."""
    __tablename__ = "folios"
    id = Column(Integer, primary_key=True, index=True)
    booking_id = Column(Integer, ForeignKey("bookings.booking_id"), unique=True, nullable=False, index=True)
    status = Column(String(20), nullable=False, default="open", index=True)  # open|settled
    total = Column(Numeric(10, 2), default=0)
    balance = Column(Numeric(10, 2), default=0)
    opened_at = Column(TIMESTAMP, server_default=func.now())
    settled_at = Column(DateTime, nullable=True)


class FolioCharge(Base):
    """A line on a folio: room charge, food, misc, tax, discount, or a payment applied."""
    __tablename__ = "folio_charges"
    id = Column(Integer, primary_key=True, index=True)
    folio_id = Column(Integer, ForeignKey("folios.id"), nullable=False, index=True)
    type = Column(String(20), nullable=False, index=True)      # room|food|minibar|laundry|extra_bed|misc|tax|discount|payment
    description = Column(String(200), nullable=True)
    qty = Column(Numeric(10, 2), default=1)
    unit_price = Column(Numeric(10, 2), default=0)
    amount = Column(Numeric(10, 2), nullable=False, default=0) # signed, GST-inclusive: charges +, payments/discounts -
    gst_percent = Column(Float, nullable=True)
    posted_by = Column(Integer, ForeignKey("users.user_id"), nullable=True, index=True)
    posted_at = Column(TIMESTAMP, server_default=func.now(), index=True)
    void = Column(Boolean, default=False, index=True)          # never hard-delete; mark void + reversing entry
    void_reason = Column(String(200), nullable=True)
    reversal_of_id = Column(Integer, ForeignKey("folio_charges.id"), nullable=True, index=True)  # set on the reversing entry


class Invoice(Base):
    """GST tax invoice snapshot for a folio (prompt 07). One invoice per folio;
    numbers are sequential per Indian financial year (INV/2026-27/00001)."""
    __tablename__ = "invoices"
    id = Column(Integer, primary_key=True, index=True)
    folio_id = Column(Integer, ForeignKey("folios.id"), unique=True, nullable=False, index=True)
    booking_id = Column(Integer, ForeignKey("bookings.booking_id"), nullable=False, index=True)
    invoice_no = Column(String(30), unique=True, nullable=False, index=True)   # INV/<FY>/<seq:05d>
    fy_label = Column(String(10), nullable=False, index=True)                  # e.g. "2026-27"
    seq = Column(Integer, nullable=False)
    invoice_date = Column(Date, nullable=False)
    taxable_total = Column(Numeric(10, 2), default=0)
    cgst_total = Column(Numeric(10, 2), default=0)
    sgst_total = Column(Numeric(10, 2), default=0)
    grand_total = Column(Numeric(10, 2), default=0)
    balance_due = Column(Numeric(10, 2), default=0)
    gst_breakup = Column(String, nullable=True)   # JSON: [{gst_percent, taxable, cgst, sgst, total}]
    created_by = Column(Integer, ForeignKey("users.user_id"), nullable=True)
    created_at = Column(TIMESTAMP, server_default=func.now())


class InvoiceCounter(Base):
    """Per-financial-year invoice sequence (allocated atomically via upsert)."""
    __tablename__ = "invoice_counters"
    fy_label = Column(String(10), primary_key=True)
    last_seq = Column(Integer, nullable=False, default=0)


class CashShift(Base):
    """A reception cash-drawer session (per-shift or per-day) for reconciliation."""
    __tablename__ = "cash_shifts"
    id = Column(Integer, primary_key=True, index=True)
    staff_id = Column(Integer, ForeignKey("users.user_id"), nullable=False, index=True)
    station_id = Column(String(50), nullable=True)
    period = Column(String(10), nullable=False, default="shift")  # shift|day
    opening_balance = Column(Numeric(10, 2), default=0)
    collections_cash = Column(Numeric(10, 2), default=0)
    expenses_total = Column(Numeric(10, 2), default=0)
    payouts_total = Column(Numeric(10, 2), default=0)
    expected_cash = Column(Numeric(10, 2), nullable=True)
    counted_cash = Column(Numeric(10, 2), nullable=True)
    variance = Column(Numeric(10, 2), nullable=True)           # counted - expected
    status = Column(String(10), nullable=False, default="open", index=True)  # open|closed
    opened_at = Column(TIMESTAMP, server_default=func.now(), index=True)
    closed_by = Column(Integer, ForeignKey("users.user_id"), nullable=True)
    closed_at = Column(DateTime, nullable=True)


class Expense(Base):
    """Petty-cash outflow logged against an open cash shift."""
    __tablename__ = "expenses"
    id = Column(Integer, primary_key=True, index=True)
    shift_id = Column(Integer, ForeignKey("cash_shifts.id"), nullable=True, index=True)
    category = Column(String(30), nullable=True)              # supplies|staff|vendor|misc
    description = Column(String(200), nullable=True)
    amount = Column(Numeric(10, 2), nullable=False, default=0)
    receipt_url = Column(String, nullable=True)
    created_by = Column(Integer, ForeignKey("users.user_id"), nullable=True, index=True)
    created_at = Column(TIMESTAMP, server_default=func.now(), index=True)


class FraudAlert(Base):
    """Anomaly/control-violation flagged for owner/admin review."""
    __tablename__ = "fraud_alerts"
    id = Column(Integer, primary_key=True, index=True)
    type = Column(String(50), nullable=False, index=True)     # card_without_booking|cleaning_too_long|cash_variance|...
    severity = Column(String(10), nullable=False, default="med", index=True)  # low|med|high
    booking_id = Column(Integer, ForeignKey("bookings.booking_id"), nullable=True, index=True)
    room_id = Column(Integer, ForeignKey("rooms.room_id"), nullable=True, index=True)
    card_id = Column(Integer, ForeignKey("card_issuances.id"), nullable=True, index=True)
    detail = Column(String, nullable=True)                    # JSON text with specifics
    detected_at = Column(TIMESTAMP, server_default=func.now(), index=True)
    status = Column(String(20), nullable=False, default="open", index=True)  # open|reviewed|dismissed
    reviewed_by = Column(Integer, ForeignKey("users.user_id"), nullable=True)
    reviewed_at = Column(DateTime, nullable=True)
