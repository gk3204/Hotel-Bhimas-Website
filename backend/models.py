from datetime import datetime
from sqlalchemy import Column, Integer, String, Numeric, Date, Time, ForeignKey, DateTime, TIMESTAMP, Boolean, Float, Index, UniqueConstraint
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
    phone = Column(String(20), nullable=True)           # staff WhatsApp/phone (prompt 15 assignee notify)
    # --- admin 2FA / TOTP (prompt 18, additive) ---
    totp_secret = Column(String(64), nullable=True)     # base32 TOTP secret (same posture as `pin`; encrypt-at-rest is a follow-up)
    totp_enabled = Column(Boolean, default=False, index=True)  # True once the admin has confirmed a code
    totp_enrolled_at = Column(DateTime, nullable=True)
    # --- staff attendance (prompt 18 slice 9, additive) ---
    # UID of the staff key card that clocks this user in/out. The UID -> user resolution and
    # POST /roster/attendance/card are live; EncoderService.ReadCard() already works, so the desk
    # can read a card. Unverified on hardware: whether a staff card's payload is stable+unique
    # per card. Confirm that before relying on card attendance (PIN punching is the fallback).
    staff_card_uid = Column(String(40), nullable=True, index=True)

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
    status_changed_at = Column(DateTime, nullable=True)    # when `status` last changed (prompt 11 cleaning-too-long detection)

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
    # --- Rates & travel-agent pricing (prompt 10, additive) ---
    agent_id = Column(Integer, ForeignKey("travel_agents.id"), nullable=True, index=True)  # set when booked "as agent X"
    commission_percent = Column(Numeric(5, 2), nullable=True)   # snapshot of the agent's commission % at booking time
    commission_amount = Column(Numeric(10, 2), nullable=True)   # accrued commission on this booking (snapshot)
    # --- OTA tracking (prompt 17 Phase A, additive) — set when booking_source is an OTA channel ---
    ota_booking_id = Column(String(80), nullable=True, index=True)    # the OTA's own reference (MMT/Goibibo/Booking.com/Agoda)
    ota_commission_percent = Column(Numeric(5, 2), nullable=True)     # commission % (from per-OTA config, overridable at entry)
    ota_net_payout = Column(Numeric(10, 2), nullable=True)           # expected payout = gross - commission (snapshot)
    # --- Corporate bill-to-company (prompt 18 slice 7, additive) ---
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=True, index=True)  # the bill-to company
    bill_to = Column(String(10), nullable=False, default="guest", index=True)  # guest | company | split
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


class BookingGuest(Base):
    """Per-occupant KYC for a booking (FE-3). One row per guest staying in the room — the lead
    (is_primary=True) plus each companion. ID numbers are stored MASKED only (raw never persisted,
    like Guest.id_number_masked); the ID scan image is stored ENCRYPTED via utils.secure_id_store
    and referenced by `id_scan_ref` (never a public URL — served by an authed decrypt-on-read route)."""
    __tablename__ = "booking_guests"
    id = Column(Integer, primary_key=True, index=True)
    booking_id = Column(Integer, ForeignKey("bookings.booking_id"), nullable=False, index=True)
    name = Column(String(100), nullable=False)
    id_type = Column(String(20), nullable=True)            # aadhaar|passport|driving_licence|voter_id|other
    id_number_masked = Column(String(30), nullable=True)   # masked "****1234" — raw never stored
    id_scan_ref = Column(String(120), nullable=True)       # encrypted-file ref: booking_<id>/<uuid>.enc
    id_scan_mime = Column(String(40), nullable=True)       # content type, for the decrypt-on-read stream
    is_primary = Column(Boolean, nullable=False, default=False, index=True)
    created_at = Column(TIMESTAMP, server_default=func.now(), index=True)


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
    # Integrated desk collection (prompt 19): a Razorpay UPI-QR or payment-link raised at the desk.
    # The webhook matches the Payment on these when there is no order_id (QR / link events carry none).
    collect_method = Column(String(20), nullable=True, index=True)  # upi_qr | link | cash (desk collect flow)
    qr_code_id = Column(String(50), nullable=True, index=True)      # Razorpay qr_code id (qr_code.credited match)
    qr_image_url = Column(String(300), nullable=True)               # Razorpay-hosted QR image (proxied by /qr.png)
    payment_link_id = Column(String(50), nullable=True, index=True) # Razorpay payment_link id (payment_link.paid match)
    convenience_fee_amount = Column(Numeric(10, 2), nullable=True)  # GST-incl. fee the guest paid on top of base (posted to folio on capture)
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
    # --- Corporate split billing (prompt 18 slice 7, additive) ---
    # `total`/`balance` keep their ORIGINAL whole-folio meaning so reports, the night audit and the
    # desktop are untouched. These mirror the `bill_to='company'` subset of the same lines; the
    # guest-side figure is derived on read: guest_balance = balance - company_balance.
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=True, index=True)
    company_total = Column(Numeric(10, 2), default=0)
    company_balance = Column(Numeric(10, 2), default=0)


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
    # --- Corporate split billing (prompt 18 slice 7, additive) ---
    # THIS is the split folio: one folio, each line routed to whoever pays it.
    bill_to = Column(String(10), nullable=False, default="guest", index=True)  # guest | company


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
    # --- Corporate billing (prompt 18 slice 7, additive) ---
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=True, index=True)
    company_invoice_id = Column(Integer, ForeignKey("company_invoices.id"), nullable=True, index=True)  # rolled into this consolidated bill


class InvoiceCounter(Base):
    """Per-financial-year invoice sequence (allocated atomically via upsert)."""
    __tablename__ = "invoice_counters"
    fy_label = Column(String(10), primary_key=True)
    last_seq = Column(Integer, nullable=False, default=0)


class EInvoice(Base):
    """GST e-invoice (IRN) for a tax Invoice (prompt 18, slice 2). One row per Invoice.
    The real IRP/GSP call runs only when GST_EINVOICE_* env is set; otherwise a deterministic
    stub IRN + signed-QR payload is generated (status='stub') — the data flow is identical so
    go-live is just adding credentials. QR is rendered onto the invoice PDF."""
    __tablename__ = "e_invoices"
    id = Column(Integer, primary_key=True, index=True)
    invoice_id = Column(Integer, ForeignKey("invoices.id"), unique=True, nullable=False, index=True)
    irn = Column(String(80), nullable=True, index=True)     # 64-char Invoice Reference Number
    ack_no = Column(String(40), nullable=True)              # acknowledgement number from the IRP
    ack_date = Column(String(30), nullable=True)           # acknowledgement date (as returned by IRP)
    signed_qr = Column(String, nullable=True)              # signed QR payload string (drawn on the PDF)
    status = Column(String(12), nullable=False, default="stub", index=True)  # generated|stub|failed|cancelled
    request_payload = Column(String, nullable=True)        # JSON text sent to the IRP (audit)
    response_payload = Column(String, nullable=True)       # JSON text returned by the IRP (audit)
    error = Column(String(300), nullable=True)             # failure reason when status='failed'
    created_by = Column(Integer, ForeignKey("users.user_id"), nullable=True)
    created_at = Column(TIMESTAMP, server_default=func.now())


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
    # --- prompt 12 (additive) ---
    denominations = Column(String, nullable=True)             # JSON text: {"500": 3, "100": 5, ...} counted at close
    close_note = Column(String(300), nullable=True)           # optional note recorded at close
    # --- Duty roster link (prompt 18 slice 12, additive) ---
    # Best-effort match to the planned roster row when the drawer is opened, so the staff report
    # can compare planned duty against the shift that was actually worked.
    roster_shift_id = Column(Integer, ForeignKey("staff_shifts.id"), nullable=True, index=True)


class Expense(Base):
    """Petty-cash outflow logged against an open cash shift."""
    __tablename__ = "expenses"
    id = Column(Integer, primary_key=True, index=True)
    shift_id = Column(Integer, ForeignKey("cash_shifts.id"), nullable=True, index=True)
    category = Column(String(30), nullable=True)              # supplies|staff|vendor|misc
    description = Column(String(200), nullable=True)
    amount = Column(Numeric(10, 2), nullable=False, default=0)
    receipt_url = Column(String, nullable=True)               # prompt 12: text reference only (photo upload -> prompt 14/R2)
    client_ref = Column(String(80), nullable=True, unique=True)  # prompt 12: idempotent double-submit guard
    created_by = Column(Integer, ForeignKey("users.user_id"), nullable=True, index=True)
    created_at = Column(TIMESTAMP, server_default=func.now(), index=True)
    # --- Vendor attribution (prompt 18 slice 13, additive) ---
    vendor_id = Column(Integer, ForeignKey("vendors.id"), nullable=True, index=True)  # which vendor this outflow paid


class AppSetting(Base):
    """Generic runtime-editable settings (key -> value). First live-editable config store
    (prompt 12) — replaces env-only config for values an admin must change without a redeploy
    (e.g. cash-shift cycle + variance threshold). Read/written via utils/settings.py."""
    __tablename__ = "app_settings"
    key = Column(String(60), primary_key=True)
    value = Column(String, nullable=True)                     # stored as text; typed by the accessor
    updated_by = Column(Integer, ForeignKey("users.user_id"), nullable=True)
    updated_at = Column(DateTime, nullable=True)


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
    review_note = Column(String(500), nullable=True)          # acknowledge/dismiss note (prompt 11)


# =====================================================================
# RATES & TRAVEL-AGENT PRICING (prompt 10) — additive, new tables.
# Price depends on room type x channel/agent x date. Rates are stored
# per-night, GST-EXCLUSIVE (same basis as RoomType.price_per_night) so a
# resolved rate is a drop-in replacement for price_per_night; the existing
# GST + promotion math is unchanged. Resolution lives in utils/rate_engine.py.
# =====================================================================

class TravelAgent(Base):
    """A travel agent / OTA partner with negotiated rates + commission."""
    __tablename__ = "travel_agents"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False, index=True)
    contact = Column(String(100), nullable=True)              # phone / email / person
    gst_no = Column(String(20), nullable=True)                # agent's GSTIN (for their invoices)
    commission_percent = Column(Numeric(5, 2), nullable=False, default=0)  # % of room revenue payable to the agent
    credit_limit = Column(Numeric(12, 2), nullable=False, default=0)       # outstanding credit ceiling (informational)
    is_active = Column(Boolean, nullable=False, default=True, index=True)
    created_at = Column(TIMESTAMP, server_default=func.now())


class AgentRate(Base):
    """A negotiated per-night rate card: one row per (agent, room type) with an
    optional date window. GST-exclusive, per-night (like RoomType.price_per_night)."""
    __tablename__ = "agent_rates"
    id = Column(Integer, primary_key=True, index=True)
    agent_id = Column(Integer, ForeignKey("travel_agents.id"), nullable=False, index=True)
    room_type_id = Column(Integer, ForeignKey("room_types.room_type_id"), nullable=False, index=True)
    rate = Column(Numeric(10, 2), nullable=False)             # per-night, GST-exclusive
    valid_from = Column(Date, nullable=True)                  # null = no start bound
    valid_to = Column(Date, nullable=True)                    # null = no end bound
    created_at = Column(TIMESTAMP, server_default=func.now())

    agent = relationship("TravelAgent")
    room_type = relationship("RoomType")

    __table_args__ = (
        Index('idx_agent_rate_lookup', 'agent_id', 'room_type_id'),
    )


class RatePlan(Base):
    """A channel/date rate override for a room type (seasonal / weekend / per-agent).
    Highest priority wins in the resolver; days_of_week scopes it to weekdays
    (CSV of 0=Mon..6=Sun; null = every day). Per-night, GST-exclusive price."""
    __tablename__ = "rate_plans"
    id = Column(Integer, primary_key=True, index=True)
    room_type_id = Column(Integer, ForeignKey("room_types.room_type_id"), nullable=False, index=True)
    channel = Column(String(20), nullable=False, index=True)  # walk_in|website|agent
    agent_id = Column(Integer, ForeignKey("travel_agents.id"), nullable=True, index=True)  # null = channel-wide
    price = Column(Numeric(10, 2), nullable=False)            # per-night, GST-exclusive
    valid_from = Column(Date, nullable=True)
    valid_to = Column(Date, nullable=True)
    days_of_week = Column(String(20), nullable=True)         # CSV "0..6" (Mon=0); null = all days; e.g. "5,6" = weekend
    priority = Column(Integer, nullable=False, default=0)     # higher wins on ties
    is_active = Column(Boolean, nullable=False, default=True, index=True)
    created_at = Column(TIMESTAMP, server_default=func.now())

    room_type = relationship("RoomType")
    agent = relationship("TravelAgent")

    __table_args__ = (
        Index('idx_rate_plan_lookup', 'room_type_id', 'channel'),
    )


class AgentPayment(Base):
    """A commission payout to an agent (the 'paid' side of the settlement report)."""
    __tablename__ = "agent_payments"
    id = Column(Integer, primary_key=True, index=True)
    agent_id = Column(Integer, ForeignKey("travel_agents.id"), nullable=False, index=True)
    amount = Column(Numeric(12, 2), nullable=False)
    paid_on = Column(Date, nullable=False)
    mode = Column(String(20), nullable=True)                 # cash|upi|bank|adjustment
    reference = Column(String(100), nullable=True)           # UPI / bank / cheque ref
    note = Column(String(200), nullable=True)
    created_by = Column(Integer, ForeignKey("users.user_id"), nullable=True)
    created_at = Column(TIMESTAMP, server_default=func.now(), index=True)

    agent = relationship("TravelAgent")


# =====================================================================
# ANTI-FRAUD / INTERNAL CONTROLS (prompt 11) — additive.
# owner_otps backs the owner-approval flow for sensitive desk actions
# (refund, below-floor discount, ...). In this build the one-time code is
# delivered on-screen (admin Approvals inbox) + server log; prompt 15 swaps
# in WhatsApp delivery behind the same create/consume interface.
# =====================================================================

class OwnerOtp(Base):
    """A one-time owner-approval code for a sensitive front-desk action."""
    __tablename__ = "owner_otps"
    id = Column(Integer, primary_key=True, index=True)
    action = Column(String(40), nullable=False, index=True)   # refund|discount_below_floor|void|off_hours_issue
    context = Column(String, nullable=True)                   # JSON text — what's being approved (booking/payment/amount)
    code = Column(String(10), nullable=False)                 # 6-digit one-time code
    expires_at = Column(DateTime, nullable=False)
    used = Column(Boolean, nullable=False, default=False, index=True)
    used_at = Column(DateTime, nullable=True)
    used_by = Column(Integer, ForeignKey("users.user_id"), nullable=True)  # who consumed it (the requesting desk user)
    approved_by = Column(Integer, ForeignKey("users.user_id"), nullable=True)  # owner/admin who released the code (prompt 15)
    created_by = Column(Integer, ForeignKey("users.user_id"), nullable=True)
    created_at = Column(TIMESTAMP, server_default=func.now(), index=True)


# =====================================================================
# HOUSEKEEPING + MAINTENANCE (prompt 13) — additive, new tables.
# Cleaning status is set by the HOUSEKEEPER role (never reception — anti-fraud:
# reception can't "park a room in cleaning" to hide it). A room only becomes
# re-sellable after a supervisor/admin marks it INSPECTED (the inspected gate).
# The fine-grained housekeeping lifecycle lives here; Room.status stays the
# coarse vocab (vacant|occupied|cleaning|inspected|maintenance|blocked) that
# availability/check-in already read, so inspect => Room.status="vacant".
# Maintenance tickets are raisable by housekeeping/reception/admin (guest via
# WhatsApp = prompt 15), assigned to a `maintenance` user, tracked to verified.
# JSON (checklist) is stored as TEXT (json.dumps), matching the codebase.
# photo_url is a text reference only (photo upload -> prompt 14/R2).
# =====================================================================

class HousekeepingStatus(Base):
    """Current housekeeping state of a physical room (one row per room, upserted)."""
    __tablename__ = "housekeeping_status"
    id = Column(Integer, primary_key=True, index=True)
    room_id = Column(Integer, ForeignKey("rooms.room_id"), nullable=False, unique=True, index=True)
    status = Column(String(20), nullable=False, default="clean", index=True)  # dirty|cleaning|clean|inspected|maintenance|dnd
    updated_by = Column(Integer, ForeignKey("users.user_id"), nullable=True)
    updated_at = Column(DateTime, nullable=True)
    photo_url = Column(String, nullable=True)                 # text reference only (photo upload -> prompt 14/R2)


class HousekeepingTask(Base):
    """A cleaning/inspection task for a room (auto-created on checkout / room-move)."""
    __tablename__ = "housekeeping_tasks"
    id = Column(Integer, primary_key=True, index=True)
    room_id = Column(Integer, ForeignKey("rooms.room_id"), nullable=False, index=True)
    type = Column(String(20), nullable=False, default="checkout_clean")  # checkout_clean|touchup|deep|inspection
    assigned_to = Column(Integer, ForeignKey("users.user_id"), nullable=True, index=True)  # NULL = pooled (any housekeeper)
    status = Column(String(20), nullable=False, default="pending", index=True)  # pending|in_progress|done
    booking_id = Column(Integer, ForeignKey("bookings.booking_id"), nullable=True, index=True)  # the checkout that dirtied it
    checklist = Column(String, nullable=True)                 # JSON text: [{"item": "Bed", "done": true}, ...]
    photo_url = Column(String, nullable=True)                 # text reference only (prompt 14/R2)
    client_ref = Column(String(80), nullable=True, unique=True)  # idempotent double-submit guard (offline-safe)
    created_at = Column(TIMESTAMP, server_default=func.now(), index=True)
    started_at = Column(DateTime, nullable=True)
    done_at = Column(DateTime, nullable=True)


class MaintenanceTicket(Base):
    """A maintenance issue (room or common area) tracked from raise to verified-close."""
    __tablename__ = "maintenance_tickets"
    id = Column(Integer, primary_key=True, index=True)
    room_id = Column(Integer, ForeignKey("rooms.room_id"), nullable=True, index=True)  # NULL = common area
    area = Column(String(100), nullable=True)                 # free-text location (lobby, terrace, room label...)
    category = Column(String(20), nullable=False, default="other", index=True)  # electrical|plumbing|carpentry|appliance|lock|other
    issue = Column(String(500), nullable=False)
    photo_url = Column(String, nullable=True)                 # text reference only (prompt 14/R2)
    priority = Column(String(10), nullable=False, default="normal", index=True)  # low|normal|high|urgent
    status = Column(String(20), nullable=False, default="open", index=True)  # open|assigned|in_progress|awaiting_parts|resolved|verified|closed
    source = Column(String(20), nullable=False, default="reception")  # guest|reception|housekeeping|admin
    booking_id = Column(Integer, ForeignKey("bookings.booking_id"), nullable=True, index=True)  # guest-linked (CRM prompt 14)
    raised_by = Column(Integer, ForeignKey("users.user_id"), nullable=True)
    assignee = Column(Integer, ForeignKey("users.user_id"), nullable=True, index=True)  # a `maintenance` user
    resolution_notes = Column(String(500), nullable=True)
    verified_by = Column(Integer, ForeignKey("users.user_id"), nullable=True)
    client_ref = Column(String(80), nullable=True, unique=True)  # idempotent double-submit guard
    created_at = Column(TIMESTAMP, server_default=func.now(), index=True)
    assigned_at = Column(DateTime, nullable=True)
    resolved_at = Column(DateTime, nullable=True)
    verified_at = Column(DateTime, nullable=True)
    # --- Guest-complaint SLA / escalation / compensation (prompt 18c, slice 11, additive) ---
    # These only matter for complaints (source='guest'); maintenance tickets leave them NULL/0.
    sla_response_due_at = Column(DateTime, nullable=True, index=True)  # respond-by target, from config at create
    sla_resolve_due_at = Column(DateTime, nullable=True, index=True)   # resolve-by target
    first_responded_at = Column(DateTime, nullable=True)               # set by POST /complaints/{id}/respond
    escalation_level = Column(Integer, nullable=False, default=0)      # 0 = none; bumped on each SLA breach
    last_escalated_at = Column(DateTime, nullable=True)                # dedupe: one escalation per window
    compensation_charge_id = Column(Integer, ForeignKey("folio_charges.id"), nullable=True)  # goodwill folio credit
    compensation_amount = Column(Numeric(10, 2), nullable=True)       # ₹ credited (absolute value)


class TicketEscalation(Base):
    """Append-only trail of a complaint's SLA escalations (never updated/deleted, same posture as
    audit_logs). Each row = one breach that bumped the ticket's escalation_level and (best-effort)
    alerted the owner."""
    __tablename__ = "ticket_escalations"
    id = Column(Integer, primary_key=True, index=True)
    ticket_id = Column(Integer, ForeignKey("maintenance_tickets.id"), nullable=False, index=True)
    level = Column(Integer, nullable=False, default=1)         # the level this row escalated TO
    reason = Column(String(200), nullable=True)               # e.g. "response SLA breached", "resolve SLA breached"
    notified = Column(Boolean, default=False)                 # was the owner WhatsApp alert sent
    created_by = Column(Integer, ForeignKey("users.user_id"), nullable=True)  # NULL = scheduler
    created_at = Column(TIMESTAMP, server_default=func.now(), index=True)


class TicketItem(Base):
    """A part/material the assignee says a ticket needs; approved items can post to expenses."""
    __tablename__ = "ticket_items"
    id = Column(Integer, primary_key=True, index=True)
    ticket_id = Column(Integer, ForeignKey("maintenance_tickets.id"), nullable=False, index=True)
    item = Column(String(200), nullable=False)
    qty = Column(Numeric(10, 2), nullable=False, default=1)
    est_cost = Column(Numeric(10, 2), nullable=True)          # estimated unit/line cost (₹)
    status = Column(String(20), nullable=False, default="needed", index=True)  # needed|approved|purchased|installed
    expense_id = Column(Integer, ForeignKey("expenses.id"), nullable=True)  # set when a purchased item posts to the ledger
    added_by = Column(Integer, ForeignKey("users.user_id"), nullable=True)
    added_at = Column(TIMESTAMP, server_default=func.now(), index=True)


# =====================================================================
# CUSTOMER / GUEST CRM (prompt 14) — additive companion tables on Guest
# =====================================================================
class GuestProfile(Base):
    """Rich CRM companion for a Guest (one row per guest). The thin `Guest` record
    (name/phone/email + masked KYC) is left untouched; this holds the recognizable
    customer: identity docs, VIP/blacklist, loyalty balance, marketing consent, notes.
    Aggregates (stay_count/total_spend/last_stay) are computed on read, not stored."""
    __tablename__ = "guest_profiles"
    id = Column(Integer, primary_key=True, index=True)
    guest_id = Column(Integer, ForeignKey("guests.guest_id"), nullable=False, unique=True, index=True)
    id_type = Column(String(20), nullable=True)               # aadhaar|passport|driving_licence|voter_id|other
    id_number_masked = Column(String(30), nullable=True)      # masked "****1234" — raw number never stored
    id_photo_url = Column(String, nullable=True)              # storage URL (R2 or local /crm/files/*)
    guest_photo_url = Column(String, nullable=True)
    address = Column(String(300), nullable=True)
    dob = Column(Date, nullable=True)
    gstin = Column(String(20), nullable=True)                 # for company/GST invoices
    vip = Column(Boolean, default=False, index=True)
    blacklist = Column(Boolean, default=False, index=True)
    blacklist_reason = Column(String(300), nullable=True)
    loyalty_points = Column(Integer, default=0)               # denormalized running balance (ledger is authoritative)
    marketing_optin = Column(Boolean, default=False)
    notes = Column(String, nullable=True)
    # --- Form C / FRRO + police register (prompt 18, slice 3; additive, all optional) ---
    nationality = Column(String(60), nullable=True)                # e.g. "Indian", "British"
    is_foreign_national = Column(Boolean, default=False, index=True)  # drives the Form C register
    passport_number_masked = Column(String(30), nullable=True)     # masked "****1234" — raw never stored
    passport_place_of_issue = Column(String(80), nullable=True)
    passport_expiry = Column(Date, nullable=True)
    visa_number_masked = Column(String(30), nullable=True)         # masked
    visa_type = Column(String(40), nullable=True)                  # tourist|business|e-visa|...
    visa_expiry = Column(Date, nullable=True)
    arrived_from = Column(String(120), nullable=True)              # place the foreigner arrived from
    next_destination = Column(String(120), nullable=True)          # place proceeding to
    created_at = Column(TIMESTAMP, server_default=func.now())
    updated_at = Column(DateTime, nullable=True)
    updated_by = Column(Integer, ForeignKey("users.user_id"), nullable=True)


class LoyaltyLedger(Base):
    """Append-only points ledger. delta > 0 = accrual (per stay/spend at checkout),
    delta < 0 = redemption (spent as a folio discount). `booking_id` + reason let a
    checkout accrual be idempotent (one accrual row per booking)."""
    __tablename__ = "loyalty_ledger"
    id = Column(Integer, primary_key=True, index=True)
    guest_id = Column(Integer, ForeignKey("guests.guest_id"), nullable=False, index=True)
    booking_id = Column(Integer, ForeignKey("bookings.booking_id"), nullable=True, index=True)
    delta = Column(Integer, nullable=False)                   # + accrue / - redeem
    reason = Column(String(200), nullable=True)
    points_after = Column(Integer, nullable=True)             # balance snapshot after this row
    folio_charge_id = Column(Integer, nullable=True)          # the discount line, for redemptions
    created_by = Column(Integer, ForeignKey("users.user_id"), nullable=True)
    created_at = Column(TIMESTAMP, server_default=func.now(), index=True)


class PreArrivalRegistration(Base):
    """A tokenized pre-arrival registration link for a booking. The guest opens the
    public link, fills details + uploads ID; on submit the payload pre-populates the
    booking's guest/profile so the desk just verifies on arrival."""
    __tablename__ = "pre_arrival_registrations"
    id = Column(Integer, primary_key=True, index=True)
    token = Column(String(64), nullable=False, unique=True, index=True)  # urlsafe link key (public)
    booking_id = Column(Integer, ForeignKey("bookings.booking_id"), nullable=False, index=True)
    guest_id = Column(Integer, ForeignKey("guests.guest_id"), nullable=True, index=True)
    status = Column(String(20), nullable=False, default="sent")  # sent|opened|submitted|verified
    payload = Column(String, nullable=True)                  # JSON text of submitted fields
    expires_at = Column(DateTime, nullable=True)
    submitted_at = Column(DateTime, nullable=True)
    created_by = Column(Integer, ForeignKey("users.user_id"), nullable=True)
    created_at = Column(TIMESTAMP, server_default=func.now())


# =====================================================================
# Prompt 15 (WhatsApp & automated alerts)
# =====================================================================

class WhatsAppMessage(Base):
    """Outbound + inbound WhatsApp log / outbox. Every send (real or stub) writes one
    row; the inbound webhook writes direction='in' rows; delivery callbacks update
    `status`/`provider_id`. `client_ref` makes scheduled sends idempotent (a job that
    re-runs won't double-send)."""
    __tablename__ = "whatsapp_messages"
    id = Column(Integer, primary_key=True, index=True)
    direction = Column(String(3), nullable=False, default="out", index=True)  # out|in
    to_number = Column(String(20), nullable=False, index=True)                # E.164 (no '+')
    template = Column(String(60), nullable=True)          # catalog name, or 'inbound' for replies
    params = Column(String, nullable=True)                # JSON text of template params
    body = Column(String, nullable=True)                  # rendered/inbound text (preview)
    status = Column(String(20), nullable=False, default="queued", index=True)  # queued|sent|delivered|read|failed|received
    provider = Column(String(20), nullable=True)          # meta|stub
    provider_id = Column(String(120), nullable=True, index=True)  # Meta wamid (for status callbacks)
    error = Column(String(300), nullable=True)
    guest_id = Column(Integer, ForeignKey("guests.guest_id"), nullable=True, index=True)
    booking_id = Column(Integer, ForeignKey("bookings.booking_id"), nullable=True, index=True)
    client_ref = Column(String(120), nullable=True, unique=True)  # idempotency for scheduled sends
    created_at = Column(TIMESTAMP, server_default=func.now(), index=True)
    updated_at = Column(DateTime, nullable=True)


class WhatsAppOptOut(Base):
    """Guests (or admins) who opted out of WhatsApp messaging. Keyed by phone so an
    opt-out applies even without a saved guest profile. Owner/OTP/overstay bypass this."""
    __tablename__ = "whatsapp_optouts"
    phone = Column(String(20), primary_key=True)          # E.164 (no '+')
    reason = Column(String(120), nullable=True)
    source = Column(String(20), nullable=False, default="guest")  # guest|admin
    created_at = Column(TIMESTAMP, server_default=func.now())


class DayCloseSummary(Base):
    """Persisted end-of-day (night-audit / day-close) snapshot (prompt 16).

    The scheduled night-audit routine (utils/night_audit.py) writes one row per business
    date: occupancy, sales, tax and cash figures frozen at close so the owner has an
    immutable day-close record and the reports layer can show history without recomputing.
    Idempotent: the routine UPSERTs on `business_date` (re-running a date overwrites it)."""
    __tablename__ = "day_close_summaries"
    id = Column(Integer, primary_key=True, index=True)
    business_date = Column(Date, nullable=False, unique=True, index=True)
    rooms_total = Column(Integer, nullable=True)
    rooms_occupied = Column(Integer, nullable=True)
    occupancy_pct = Column(Float, nullable=True)
    room_revenue = Column(Numeric(12, 2), nullable=True)      # room-charge portion of the day's sales
    other_revenue = Column(Numeric(12, 2), nullable=True)     # food/misc/minibar etc.
    total_sales = Column(Numeric(12, 2), nullable=True)       # taxable value + tax (gross, non-void charges)
    taxable_total = Column(Numeric(12, 2), nullable=True)
    cgst_total = Column(Numeric(12, 2), nullable=True)
    sgst_total = Column(Numeric(12, 2), nullable=True)
    cash_collected = Column(Numeric(12, 2), nullable=True)
    cash_expected = Column(Numeric(12, 2), nullable=True)
    cash_variance = Column(Numeric(12, 2), nullable=True)     # counted - expected across shifts closed that day
    arrivals = Column(Integer, nullable=True)
    departures = Column(Integer, nullable=True)
    open_alerts = Column(Integer, nullable=True)
    folios_posted = Column(Integer, nullable=True)            # safety-net folios opened by this run
    generated_by = Column(String(20), nullable=True)         # 'scheduler' | 'manual' | staff label
    generated_at = Column(DateTime, nullable=True)


# ==========================================================================
# OTA tracking (prompt 17 Phase A) — makes OTA business first-class in the PMS.
# ==========================================================================
class OtaChannel(Base):
    """Per-OTA configuration (prompt 17). One row per online travel agency the hotel sells on.

    Holds the commission % pre-filled at desk entry, whether the mailbox auto-draft parser is
    enabled for this channel, and free-text cancellation rules. MakeMyTrip and Goibibo are one
    company (shared inGo-MMT extranet) but kept as separate rows so commission/reporting can
    differ per brand. Seeded by migration 015 (also auto-created via create_all)."""
    __tablename__ = "ota_channels"
    id = Column(Integer, primary_key=True, index=True)
    code = Column(String(20), nullable=False, unique=True, index=True)  # makemytrip|goibibo|booking_com|agoda|other_ota
    display_name = Column(String(60), nullable=False)
    commission_percent = Column(Numeric(5, 2), nullable=False, default=0)  # default commission, overridable at entry
    active = Column(Boolean, nullable=False, default=True)
    mailbox_parsing_enabled = Column(Boolean, nullable=False, default=False)  # let the IMAP poller auto-draft this channel
    cancellation_policy = Column(String(500), nullable=True)  # free-text OTA-specific cancellation/no-show rules
    created_at = Column(TIMESTAMP, server_default=func.now())
    updated_at = Column(TIMESTAMP, server_default=func.now(), onupdate=func.now(), nullable=True)


class OtaSettlement(Base):
    """An actual bank settlement/payout received from an OTA (prompt 17 payout reconciliation).

    Reception/admin records what the OTA actually paid into the bank for a period; the
    reconciliation report compares the sum of these against the expected payout (gross - commission)
    computed from the OTA bookings, and flags mismatches."""
    __tablename__ = "ota_settlements"
    id = Column(Integer, primary_key=True, index=True)
    channel_code = Column(String(20), nullable=False, index=True)  # matches OtaChannel.code
    period_start = Column(Date, nullable=True)
    period_end = Column(Date, nullable=True)
    reference = Column(String(80), nullable=True)   # bank/statement/OTA settlement reference
    amount = Column(Numeric(10, 2), nullable=False)  # actual amount received
    notes = Column(String(500), nullable=True)
    created_by = Column(Integer, ForeignKey("users.user_id"), nullable=True)
    created_at = Column(TIMESTAMP, server_default=func.now(), index=True)


class OtaDraftBooking(Base):
    """A booking parsed from an OTA confirmation/cancellation email, awaiting reception review (prompt 17).

    The optional IMAP poller (services/ota_service.py, off unless OTA_IMAP_* env set) parses a
    dedicated mailbox and upserts one draft per OTA email. Reception confirms a draft in one click
    (creates a real Booking via the desk-booking path) or dismisses it. Cancellation emails create a
    `cancellation` draft and auto-flag a matching confirmed booking. Deduped on message_id and on
    (channel_code, ota_booking_id, kind)."""
    __tablename__ = "ota_draft_bookings"
    id = Column(Integer, primary_key=True, index=True)
    channel_code = Column(String(20), nullable=False, index=True)
    ota_booking_id = Column(String(80), nullable=True, index=True)
    kind = Column(String(20), nullable=False, default="confirmation")  # confirmation | cancellation
    status = Column(String(20), nullable=False, default="pending", index=True)  # pending | confirmed | dismissed | flagged
    guest_name = Column(String(120), nullable=True)
    phone = Column(String(20), nullable=True)
    email = Column(String(120), nullable=True)
    check_in = Column(Date, nullable=True)
    check_out = Column(Date, nullable=True)
    room_type_hint = Column(String(120), nullable=True)   # OTA's room name (mapped to a PMS type at confirm time)
    amount = Column(Numeric(10, 2), nullable=True)         # gross booking value parsed from the email
    commission_percent = Column(Numeric(5, 2), nullable=True)  # snapshot of the channel commission at parse time
    message_id = Column(String(200), nullable=True, index=True)  # email Message-ID, for IMAP-level dedupe
    raw_source = Column(String, nullable=True)             # raw email text snippet (audit / manual fallback)
    linked_booking_id = Column(Integer, ForeignKey("bookings.booking_id"), nullable=True)  # set on confirm
    created_at = Column(TIMESTAMP, server_default=func.now(), index=True)

    __table_args__ = (
        UniqueConstraint("channel_code", "ota_booking_id", "kind", name="uq_ota_draft_channel_bookingid_kind"),
    )


# =====================================================================
# GOOGLE REVIEW AUTO-REPLY (prompt 20) — reputation management
# =====================================================================
class GoogleReview(Base):
    """A Google Business Profile review + the reply we generate/post (prompt 20).

    The GBP poller (services/gbp_client.py, off unless GBP_* env set) upserts one row per review,
    deduped on `gbp_review_id`. Reply generation (services/review_service.py) fills `reply_text`:
      * rating >= threshold -> a varied thank-you posted AUTOMATICALLY after a randomized delay
        (`scheduled_post_at`); reply_mode='auto'.
      * rating <  threshold -> a softened service-recovery draft that waits in the approval queue
        (reply_mode='approved') + an owner WhatsApp alert + an auto-created complaint ticket.
    Public replies NEVER include the matched guest's stay/room details (privacy) even when matched.
    """
    __tablename__ = "google_reviews"
    id = Column(Integer, primary_key=True, index=True)
    gbp_review_id = Column(String(200), nullable=False, unique=True, index=True)  # dedupe key from Google
    rating = Column(Integer, nullable=False, index=True)                          # 1..5 stars
    author_name = Column(String(150), nullable=True)
    review_text = Column(String, nullable=True)
    review_created_at = Column(DateTime, nullable=True, index=True)               # when the guest left it
    matched_guest_id = Column(Integer, ForeignKey("guests.guest_id"), nullable=True, index=True)  # best-effort match
    reply_text = Column(String, nullable=True)                                    # our generated/edited reply
    reply_mode = Column(String(20), nullable=True)                                # auto | approved | manual
    reply_status = Column(String(30), nullable=False, default="pending_generation", index=True)
    # pending_generation | auto_sent | pending_approval | approved_sent | skipped | failed
    scheduled_post_at = Column(DateTime, nullable=True, index=True)               # randomized delay target for auto posts
    replied_at = Column(DateTime, nullable=True)                                  # when the reply was actually posted
    ticket_id = Column(Integer, ForeignKey("maintenance_tickets.id"), nullable=True)  # complaint follow-up (low ratings)
    last_error = Column(String(500), nullable=True)                              # surfaced in the admin queue on failure
    retry_count = Column(Integer, nullable=False, default=0)                      # post retries (backoff)
    created_at = Column(TIMESTAMP, server_default=func.now(), index=True)
    updated_at = Column(DateTime, nullable=True, onupdate=func.now())


# =====================================================================
# CORPORATE BILL-TO-COMPANY (prompt 18, slice 7)
# =====================================================================
class Company(Base):
    """A corporate account the hotel bills instead of the guest ("bill to company").

    Room + routed incidental charges leave the guest folio and land on this company's
    running city ledger; the company is then billed once per period with a consolidated
    GST invoice. `credit_limit` caps the outstanding ledger balance — routing a folio that
    would breach it is refused (409) unless an admin overrides with a reason (audited)."""
    __tablename__ = "companies"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(150), nullable=False, unique=True, index=True)
    gstin = Column(String(20), nullable=True, index=True)     # printed on the consolidated tax invoice
    address = Column(String(300), nullable=True)
    city = Column(String(80), nullable=True)
    state = Column(String(80), nullable=True)
    state_code = Column(String(4), nullable=True)             # GST state code (place of supply)
    contact_person = Column(String(100), nullable=True)
    phone = Column(String(20), nullable=True)
    email = Column(String(120), nullable=True)
    credit_limit = Column(Numeric(12, 2), nullable=False, default=0)   # 0 = no credit allowed
    credit_days = Column(Integer, nullable=False, default=30)          # payment terms (informational + ageing)
    payment_terms = Column(String(120), nullable=True)
    is_active = Column(Boolean, default=True, index=True)
    notes = Column(String, nullable=True)
    created_by = Column(Integer, ForeignKey("users.user_id"), nullable=True)
    created_at = Column(TIMESTAMP, server_default=func.now(), index=True)
    updated_by = Column(Integer, ForeignKey("users.user_id"), nullable=True)
    updated_at = Column(DateTime, nullable=True)


class CompanyLedger(Base):
    """Append-only city ledger for a company. NEVER updated or deleted — a correction is a
    new opposite-signed row, same posture as `folio_charges` and `audit_logs`.

    amount > 0 = the company now owes more (a stay transferred in, or an adjustment);
    amount < 0 = the company paid / was credited. `balance_after` snapshots the running
    outstanding so a statement needs no window function."""
    __tablename__ = "company_ledger"
    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False, index=True)
    type = Column(String(12), nullable=False, index=True)     # charge | payment | adjustment
    description = Column(String(300), nullable=True)
    amount = Column(Numeric(12, 2), nullable=False, default=0)   # signed (see docstring)
    balance_after = Column(Numeric(12, 2), nullable=True)
    booking_id = Column(Integer, ForeignKey("bookings.booking_id"), nullable=True, index=True)
    folio_id = Column(Integer, ForeignKey("folios.id"), nullable=True, index=True)
    company_invoice_id = Column(Integer, ForeignKey("company_invoices.id"), nullable=True, index=True)
    method = Column(String(20), nullable=True)                # payments: cash|upi|bank_transfer|cheque|card
    reference = Column(String(120), nullable=True)            # cheque/UTR/receipt reference
    client_ref = Column(String(80), nullable=True, unique=True)  # idempotent double-submit guard (Expense idiom)
    created_by = Column(Integer, ForeignKey("users.user_id"), nullable=True, index=True)
    created_at = Column(TIMESTAMP, server_default=func.now(), index=True)


class CompanyInvoice(Base):
    """Consolidated GST tax invoice covering every stay transferred to a company in a period.

    Numbers come from the SAME `invoice_counters` table as guest invoices, under a distinct
    financial-year label prefix ("C2026-27"), so the two series never collide and no second
    counter table is needed. Format: CINV/<FY>/<seq:05d>."""
    __tablename__ = "company_invoices"
    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False, index=True)
    invoice_no = Column(String(30), unique=True, nullable=False, index=True)   # CINV/<FY>/<seq:05d>
    fy_label = Column(String(10), nullable=False, index=True)                  # e.g. "2026-27"
    seq = Column(Integer, nullable=False)
    invoice_date = Column(Date, nullable=False)
    period_from = Column(Date, nullable=False)
    period_to = Column(Date, nullable=False)
    taxable_total = Column(Numeric(12, 2), default=0)
    cgst_total = Column(Numeric(12, 2), default=0)
    sgst_total = Column(Numeric(12, 2), default=0)
    grand_total = Column(Numeric(12, 2), default=0)
    paid_total = Column(Numeric(12, 2), default=0)
    gst_breakup = Column(String, nullable=True)   # JSON: [{gst_percent, taxable, cgst, sgst, total}]
    stay_count = Column(Integer, default=0)
    status = Column(String(12), nullable=False, default="issued", index=True)  # issued | paid | cancelled
    notes = Column(String(300), nullable=True)
    created_by = Column(Integer, ForeignKey("users.user_id"), nullable=True)
    created_at = Column(TIMESTAMP, server_default=func.now(), index=True)
    cancelled_at = Column(DateTime, nullable=True)
    cancel_reason = Column(String(300), nullable=True)


# =====================================================================
# VENDOR / AMC TRACKING (prompt 18, slice 13)
# =====================================================================
class Vendor(Base):
    """A supplier or service provider (laundry, lock AMC, linen, electrical, IT...).
    Petty-cash `expenses` and maintenance part purchases attribute here, so the owner can
    see total spend per vendor alongside the contracts they hold."""
    __tablename__ = "vendors"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(150), nullable=False, unique=True, index=True)
    category = Column(String(20), nullable=False, default="other", index=True)
    # laundry | lock_amc | linen | electrical | plumbing | it | fnb | security | other
    contact_person = Column(String(100), nullable=True)
    phone = Column(String(20), nullable=True)
    email = Column(String(120), nullable=True)
    gstin = Column(String(20), nullable=True)
    address = Column(String(300), nullable=True)
    is_active = Column(Boolean, default=True, index=True)
    notes = Column(String, nullable=True)
    created_by = Column(Integer, ForeignKey("users.user_id"), nullable=True)
    created_at = Column(TIMESTAMP, server_default=func.now(), index=True)
    updated_by = Column(Integer, ForeignKey("users.user_id"), nullable=True)
    updated_at = Column(DateTime, nullable=True)


class VendorContract(Base):
    """A dated agreement with a vendor (AMC, rental, service, supply). The renewal sweep
    (utils/vendor_jobs.py, driven by the EXISTING WhatsApp scheduler) alerts the owner once
    per contract when `end_date` falls inside `renewal_reminder_days`."""
    __tablename__ = "vendor_contracts"
    id = Column(Integer, primary_key=True, index=True)
    vendor_id = Column(Integer, ForeignKey("vendors.id"), nullable=False, index=True)
    title = Column(String(200), nullable=False)
    contract_type = Column(String(12), nullable=False, default="amc", index=True)  # amc|rental|service|supply
    start_date = Column(Date, nullable=True)
    end_date = Column(Date, nullable=True, index=True)
    renewal_reminder_days = Column(Integer, nullable=False, default=30)
    amount = Column(Numeric(12, 2), nullable=True)
    billing_cycle = Column(String(12), nullable=True)         # monthly|quarterly|half_yearly|annual|one_time
    document_url = Column(String, nullable=True)              # text reference (utils/storage.py, prompt 14)
    status = Column(String(12), nullable=False, default="active", index=True)  # active|expired|cancelled
    last_reminder_sent_at = Column(DateTime, nullable=True)   # dedupe: one alert per contract per window
    notes = Column(String, nullable=True)
    created_by = Column(Integer, ForeignKey("users.user_id"), nullable=True)
    created_at = Column(TIMESTAMP, server_default=func.now(), index=True)
    updated_by = Column(Integer, ForeignKey("users.user_id"), nullable=True)
    updated_at = Column(DateTime, nullable=True)


# =====================================================================
# STAFF ROSTER + ATTENDANCE (prompt 18, slices 12 & 9)
# =====================================================================
class StaffShift(Base):
    """One planned duty on the roster. `cash_shifts.roster_shift_id` links the drawer that was
    actually opened back to the duty that was planned, which is what makes the staff report's
    planned-vs-actual column real rather than guesswork."""
    __tablename__ = "staff_shifts"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.user_id"), nullable=False, index=True)
    shift_date = Column(Date, nullable=False, index=True)
    shift_type = Column(String(10), nullable=False, default="general", index=True)  # morning|evening|night|general
    start_time = Column(Time, nullable=True)
    end_time = Column(Time, nullable=True)
    role_label = Column(String(40), nullable=True)            # e.g. "Front desk", "Housekeeping floor 2"
    status = Column(String(10), nullable=False, default="planned", index=True)  # planned|confirmed|swapped|absent
    notes = Column(String(300), nullable=True)
    created_by = Column(Integer, ForeignKey("users.user_id"), nullable=True)
    created_at = Column(TIMESTAMP, server_default=func.now())
    updated_by = Column(Integer, ForeignKey("users.user_id"), nullable=True)
    updated_at = Column(DateTime, nullable=True)

    __table_args__ = (
        UniqueConstraint("user_id", "shift_date", "shift_type", name="uq_staff_shift_slot"),
    )


class StaffAttendance(Base):
    """A worked session: clock-in -> clock-out. Sources:
      * `pin`   — the staff member types their existing User.pin at the desk (no new auth system),
      * `admin` — an admin records/corrects it,
      * `card`  — a staff key card tap resolves `users.staff_card_uid`. The endpoint is live and
        EncoderService.ReadCard() works; only per-card UID stability is unverified on hardware.
    An open row (clock_out IS NULL) is the "currently on duty" state."""
    __tablename__ = "staff_attendance"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.user_id"), nullable=False, index=True)
    clock_in = Column(DateTime, nullable=False, index=True)
    clock_out = Column(DateTime, nullable=True, index=True)
    minutes_worked = Column(Integer, nullable=True)           # snapshot on clock-out (avoids re-deriving)
    source = Column(String(8), nullable=False, default="pin", index=True)   # pin | admin | card
    station_id = Column(String(50), nullable=True)
    roster_shift_id = Column(Integer, ForeignKey("staff_shifts.id"), nullable=True, index=True)
    card_uid = Column(String(40), nullable=True)              # encoder seam — set when source='card'
    note = Column(String(300), nullable=True)
    created_by = Column(Integer, ForeignKey("users.user_id"), nullable=True)
    created_at = Column(TIMESTAMP, server_default=func.now(), index=True)


# =====================================================================
# INVENTORY / STOCK (prompt 18c, slice 8)
# =====================================================================
class StockItem(Base):
    """A stockable item the hotel holds (minibar drinks, toiletries, linen, supplies...).
    `current_qty` is a running snapshot maintained by StockMovement rows — the movement ledger
    is the source of truth, this column is the fast read (same posture as company_ledger's
    balance_after). Low stock = current_qty <= reorder_threshold."""
    __tablename__ = "stock_items"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(150), nullable=False, index=True)
    sku = Column(String(50), nullable=True, unique=True, index=True)   # optional short code
    category = Column(String(30), nullable=False, default="other", index=True)
    # minibar | toiletries | linen | supplies | fnb | cleaning | other
    unit = Column(String(20), nullable=False, default="pcs")          # pcs | bottle | kg | litre | pack | roll
    reorder_threshold = Column(Numeric(10, 2), nullable=False, default=0)  # alert when qty <= this
    current_qty = Column(Numeric(10, 2), nullable=False, default=0)    # running snapshot from movements
    sale_price = Column(Numeric(10, 2), nullable=True)                # ₹ charged to a guest folio (minibar)
    gst_percent = Column(Float, nullable=True)                        # GST slab for the sale price
    vendor_id = Column(Integer, ForeignKey("vendors.id"), nullable=True, index=True)  # usual supplier
    is_active = Column(Boolean, default=True, index=True)
    notes = Column(String, nullable=True)
    low_stock_alerted_at = Column(DateTime, nullable=True)            # dedupe: one alert per low-stock episode
    created_by = Column(Integer, ForeignKey("users.user_id"), nullable=True)
    created_at = Column(TIMESTAMP, server_default=func.now(), index=True)
    updated_by = Column(Integer, ForeignKey("users.user_id"), nullable=True)
    updated_at = Column(DateTime, nullable=True)


class StockMovement(Base):
    """Append-only stock ledger. NEVER updated/deleted — a correction is a new opposite row,
    same posture as company_ledger / folio_charges / audit_logs.

    delta_qty > 0 = stock came in (receive / positive adjust);
    delta_qty < 0 = stock went out (consume / wastage / negative adjust).
    `qty_after` snapshots the item's running quantity so a report needs no window function."""
    __tablename__ = "stock_movements"
    id = Column(Integer, primary_key=True, index=True)
    item_id = Column(Integer, ForeignKey("stock_items.id"), nullable=False, index=True)
    type = Column(String(10), nullable=False, index=True)     # receive | consume | adjust | wastage
    delta_qty = Column(Numeric(10, 2), nullable=False, default=0)  # signed (see docstring)
    qty_after = Column(Numeric(10, 2), nullable=True)         # running snapshot after this movement
    unit_cost = Column(Numeric(10, 2), nullable=True)         # ₹/unit on a receive (feeds expense/valuation)
    reason = Column(String(200), nullable=True)              # required for adjust/wastage
    folio_charge_id = Column(Integer, ForeignKey("folio_charges.id"), nullable=True, index=True)  # consume → minibar line
    expense_id = Column(Integer, ForeignKey("expenses.id"), nullable=True, index=True)  # receive → petty-cash outflow
    booking_id = Column(Integer, ForeignKey("bookings.booking_id"), nullable=True, index=True)
    client_ref = Column(String(80), nullable=True, unique=True)  # idempotent double-submit guard (Expense idiom)
    created_by = Column(Integer, ForeignKey("users.user_id"), nullable=True, index=True)
    created_at = Column(TIMESTAMP, server_default=func.now(), index=True)


# =====================================================================
# GUEST EXTRAS / IN-ROOM QR PORTAL (prompt 18d, slice 10)
# =====================================================================
class GuestPortalSession(Base):
    """A per-stay, long-lived token behind the in-room QR. Unlike `pre_arrival_registrations`
    (booking-scoped + single-submit), this stays valid for the WHOLE stay and drives MANY guest
    actions (room service, WiFi, wake-up, cab, checkout). The token in the URL is the only
    credential; every action re-validates that the booking is still `checked_in`. `expires_at` =
    the stay's checkout moment. Get-or-create per booking (one active session)."""
    __tablename__ = "guest_portal_sessions"
    id = Column(Integer, primary_key=True, index=True)
    token = Column(String(64), nullable=False, unique=True, index=True)  # urlsafe link key (public)
    booking_id = Column(Integer, ForeignKey("bookings.booking_id"), nullable=False, index=True)
    room_id = Column(Integer, ForeignKey("rooms.room_id"), nullable=True, index=True)
    guest_id = Column(Integer, ForeignKey("guests.guest_id"), nullable=True, index=True)
    status = Column(String(10), nullable=False, default="active", index=True)  # active | closed
    expires_at = Column(DateTime, nullable=True)
    created_by = Column(Integer, ForeignKey("users.user_id"), nullable=True)
    created_at = Column(TIMESTAMP, server_default=func.now())


class GuestRequest(Base):
    """Anything an in-house guest asks for from the in-room portal (or that the desk logs on their
    behalf). One table for all five extras. Reception fulfils these from the desk board: a
    `room_service` request posts a folio charge ONLY on fulfil (no charge without staff — the
    anti-fraud rule); a `checkout` request opens the existing staff checkout flow."""
    __tablename__ = "guest_requests"
    id = Column(Integer, primary_key=True, index=True)
    booking_id = Column(Integer, ForeignKey("bookings.booking_id"), nullable=True, index=True)
    room_id = Column(Integer, ForeignKey("rooms.room_id"), nullable=True, index=True)
    guest_id = Column(Integer, ForeignKey("guests.guest_id"), nullable=True, index=True)
    type = Column(String(16), nullable=False, index=True)     # room_service | wifi | wakeup | cab | checkout
    status = Column(String(16), nullable=False, default="requested", index=True)  # requested|acknowledged|completed|dismissed
    payload = Column(String, nullable=True)                   # JSON: items[], wake time, cab details, wifi code…
    note = Column(String(300), nullable=True)                 # free text (guest note or desk resolution)
    amount = Column(Numeric(10, 2), nullable=True)            # estimated/charged total (room service)
    folio_charge_id = Column(Integer, ForeignKey("folio_charges.id"), nullable=True)  # set when fulfilled → folio
    source = Column(String(8), nullable=False, default="portal")  # portal | desk
    client_ref = Column(String(80), nullable=True, unique=True)   # idempotent double-submit guard
    handled_by = Column(Integer, ForeignKey("users.user_id"), nullable=True)
    created_at = Column(TIMESTAMP, server_default=func.now(), index=True)
    updated_at = Column(DateTime, nullable=True)


class MenuItem(Base):
    """A room-service menu entry the guest can order from the portal. Kept separate from
    `stock_items` so prepared food isn't driven negative; a stocked item (a soft drink) can link
    via `stock_item_id` so fulfilling the order also decrements inventory."""
    __tablename__ = "menu_items"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(150), nullable=False, index=True)
    description = Column(String(300), nullable=True)
    category = Column(String(20), nullable=False, default="food", index=True)  # food|beverage|snack|service
    price = Column(Numeric(10, 2), nullable=False, default=0)
    gst_percent = Column(Float, nullable=True)
    is_available = Column(Boolean, default=True, index=True)
    sort_order = Column(Integer, nullable=False, default=0)
    stock_item_id = Column(Integer, ForeignKey("stock_items.id"), nullable=True)  # optional inventory link
    created_by = Column(Integer, ForeignKey("users.user_id"), nullable=True)
    created_at = Column(TIMESTAMP, server_default=func.now(), index=True)
    updated_by = Column(Integer, ForeignKey("users.user_id"), nullable=True)
    updated_at = Column(DateTime, nullable=True)
