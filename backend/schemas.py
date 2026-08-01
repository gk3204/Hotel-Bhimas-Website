from typing import List, Optional
from pydantic import BaseModel, Field, EmailStr, field_validator
from datetime import date, time, datetime

# Editable category families (F-A) accept any lowercase slug here; the fixed enum-regexes
# were replaced by this permissive shape so admins can add options, and membership is
# enforced against the configured list at the endpoint (utils.settings.validate_category).
CATEGORY_SLUG_RE = "^[a-z0-9_]{2,40}$"

class UserCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=8, max_length=100)
    role: str = Field(..., pattern="^(admin|reception|housekeeper|maintenance|supervisor|user)$")

    @field_validator('username')
    @classmethod
    def username_alphanumeric(cls, v):
        if not v.replace('_', '').replace('-', '').isalnum():
            raise ValueError('Username must be alphanumeric with underscores/hyphens only')
        return v


class UserUpdate(BaseModel):
    password: Optional[str] = Field(None, min_length=8, max_length=100)
    role: Optional[str] = Field(None, pattern="^(admin|reception|housekeeper|maintenance|supervisor|user)$")


class UserResponse(BaseModel):
    user_id: int
    username: str
    role: str

    class Config:
        from_attributes = True

class RoomTypeCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    price_per_night: float = Field(..., gt=0, le=1000000)
    gst_percent: float = Field(default=18, ge=0, le=100)
    max_occupancy: int = Field(..., ge=1, le=20)
    total_rooms: int = Field(default=1, ge=1, le=100)
    is_ac: bool = False   # FE-10: AC vs non-AC room type

    @field_validator('price_per_night')
    @classmethod
    def validate_price(cls, v):
        if v <= 0:
            raise ValueError('Price must be greater than 0')
        if v != round(v, 2):
            raise ValueError('Price can only have 2 decimal places')
        return v

class RoomTypeUpdate(BaseModel):
    is_active: bool

class RoomTypeUpdateDetails(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=100)
    price_per_night: Optional[float] = Field(None, gt=0, le=1000000)
    gst_percent: Optional[float] = Field(None, ge=0, le=100)
    max_occupancy: Optional[int] = Field(None, ge=1, le=20)
    total_rooms: Optional[int] = Field(None, ge=1, le=100)
    is_ac: Optional[bool] = None   # FE-10
    is_active: Optional[bool] = None

    @field_validator('price_per_night')
    @classmethod
    def validate_price(cls, v):
        if v is not None and v <= 0:
            raise ValueError('Price must be greater than 0')
        return v


class RoomCreate(BaseModel):
    room_number: str = Field(..., min_length=1, max_length=10)
    room_type_id: int = Field(..., ge=1)
    building: int = Field(default=1, ge=1, le=99)
    floor: int = Field(default=1, ge=1, le=99)
    max_cards: int = Field(default=4, ge=1, le=20)
    lock_no: Optional[str] = Field(None, max_length=20)
    is_active: bool = Field(default=True)
    status: Optional[str] = Field(
        default="vacant",
        pattern="^(vacant|occupied|cleaning|inspected|maintenance|blocked)$",
    )


class RoomUpdate(BaseModel):
    room_number: Optional[str] = Field(None, min_length=1, max_length=10)
    room_type_id: Optional[int] = Field(None, ge=1)
    building: Optional[int] = Field(None, ge=1, le=99)
    floor: Optional[int] = Field(None, ge=1, le=99)
    max_cards: Optional[int] = Field(None, ge=1, le=20)
    lock_no: Optional[str] = Field(None, max_length=20)
    is_active: Optional[bool] = None
    status: Optional[str] = Field(
        None, pattern="^(vacant|occupied|cleaning|inspected|maintenance|blocked)$"
    )


class RoomStatusUpdate(BaseModel):
    status: str = Field(..., pattern="^(vacant|occupied|cleaning|inspected|maintenance|blocked)$")


class RoomActiveToggle(BaseModel):
    is_active: bool


class BookingItemCreate(BaseModel):
    room_type_id: int = Field(..., gt=0)
    quantity: int = Field(default=1, ge=1, le=5)


class BookingCreate(BaseModel):
    rooms: list[BookingItemCreate] = Field(..., min_items=1, max_items=10)
    guest_name: str = Field(..., min_length=2, max_length=100)
    phone: str = Field(..., min_length=10, max_length=15)
    email: Optional[EmailStr] = None
    check_in: date
    check_in_time: time  # Required: guest's expected arrival time ("HH:MM")
    check_out: date
    booking_source: str = Field("online", pattern="^(online|frontdesk|website)$")

    @field_validator('check_out')
    @classmethod
    def validate_checkout(cls, v, info):
        if 'check_in' in info.data and v <= info.data['check_in']:
            raise ValueError('Check-out date must be after check-in date')
        return v


class DeskBookingCreate(BaseModel):
    """Reception desk booking (walk-in today or advance future). No online gateway / convenience fee."""
    rooms: list[BookingItemCreate] = Field(..., min_length=1, max_length=10)
    guest_name: str = Field(..., min_length=2, max_length=100)
    phone: str = Field(..., min_length=7, max_length=15)
    email: Optional[EmailStr] = None
    check_in: date
    check_in_time: time
    check_out: date
    booking_source: str = Field(
        "walk_in",
        pattern="^(walk_in|frontdesk|agent|makemytrip|goibibo|booking_com|agoda|other_ota|other)$",
    )
    agent_id: Optional[int] = Field(None, gt=0)  # prompt 10: price from this travel agent's rate + accrue commission
    # prompt 17: OTA tracking — set when booking_source is an OTA channel. commission % defaults to
    # the per-OTA config (ota_channels) when not supplied; net payout is computed server-side.
    ota_booking_id: Optional[str] = Field(None, max_length=80)
    ota_commission_percent: Optional[float] = Field(None, ge=0, le=100)
    # prompt 18 slice 7: corporate bill-to. `company` bills the whole stay to the employer,
    # `split` bills the room and leaves incidentals with the guest. Ignored without company_id.
    company_id: Optional[int] = Field(None, gt=0)
    bill_to: Optional[str] = Field(None, pattern="^(guest|company|split)$")

    @field_validator('check_out')
    @classmethod
    def validate_desk_checkout(cls, v, info):
        if 'check_in' in info.data and v <= info.data['check_in']:
            raise ValueError('Check-out date must be after check-in date')
        return v


# ---- OTA tracking (prompt 17 Phase A) ----
class OtaChannelUpdate(BaseModel):
    """Admin edit of a per-OTA config row. Only provided fields change."""
    display_name: Optional[str] = Field(None, min_length=1, max_length=60)
    commission_percent: Optional[float] = Field(None, ge=0, le=100)
    active: Optional[bool] = None
    mailbox_parsing_enabled: Optional[bool] = None
    cancellation_policy: Optional[str] = Field(None, max_length=500)


class OtaSettlementCreate(BaseModel):
    """Record an actual bank settlement/payout received from an OTA (payout reconciliation)."""
    channel_code: str = Field(..., pattern="^(makemytrip|goibibo|booking_com|agoda|other_ota)$")
    amount: float = Field(..., gt=0, le=100000000)
    period_start: Optional[date] = None
    period_end: Optional[date] = None
    reference: Optional[str] = Field(None, max_length=80)
    notes: Optional[str] = Field(None, max_length=500)


class OtaDraftConfirm(BaseModel):
    """Confirm an email-parsed OTA draft into a real booking. The PMS room type + quantity must be
    chosen (the OTA's room name can't be reliably auto-mapped). Guest/date/commission fields default
    to the draft's parsed values but may be corrected here."""
    room_type_id: int = Field(..., gt=0)
    quantity: int = Field(1, ge=1, le=5)  # matches BookingItemCreate cap
    guest_name: Optional[str] = Field(None, min_length=2, max_length=100)
    phone: Optional[str] = Field(None, min_length=7, max_length=15)
    email: Optional[EmailStr] = None
    check_in: Optional[date] = None
    check_out: Optional[date] = None
    check_in_time: Optional[time] = None
    ota_commission_percent: Optional[float] = Field(None, ge=0, le=100)


class FolioOpenRequest(BaseModel):
    booking_id: int = Field(..., gt=0)


class FolioChargeCreate(BaseModel):
    """Post a food/misc charge to a folio. unit_price is GST-inclusive (menu price)."""
    type: str = Field(..., pattern="^(food|misc|minibar|laundry|extra_bed)$")
    description: str = Field(..., min_length=1, max_length=200)
    qty: float = Field(default=1, gt=0, le=999)
    unit_price: float = Field(..., ge=0, le=1000000)
    gst_percent: float = Field(default=5, ge=0, le=28)


class FolioVoidRequest(BaseModel):
    reason: str = Field(..., min_length=3, max_length=200)


class FolioDiscountRequest(BaseModel):
    amount: float = Field(..., gt=0, le=1000000)
    reason: str = Field(..., min_length=3, max_length=200)
    # Owner-approval OTP (prompt 11): required for a below-floor discount only when
    # DISCOUNT_OTP_REQUIRED is on. Obtain via POST /fraud/otp/request (action="discount_below_floor").
    owner_otp_id: Optional[int] = Field(None, gt=0)
    owner_otp_code: Optional[str] = Field(None, min_length=4, max_length=10)


class DeskPaymentRecord(BaseModel):
    """Minimal desk payment (prompt 06 pull-forward from 08): recorded, not a gateway charge."""
    booking_id: int = Field(..., gt=0)
    amount: float = Field(..., gt=0, le=10_000_000)
    method: str = Field(..., pattern="^(cash|card|upi|bank)$")
    reference: Optional[str] = Field(None, max_length=100)   # UPI ref / card-machine slip no / bank txn
    client_ref: Optional[str] = Field(None, max_length=64)   # desktop uuid (offline outbox dedupe)


class PaymentRefundRequest(BaseModel):
    """Desk refund of a recorded payment (prompt 08). Per-payment, single-shot
    (retryable only after a 'failed' gateway refund). Requires admin approval."""
    payment_id: int = Field(..., gt=0)
    amount: float = Field(..., gt=0, le=10_000_000)
    reason: str = Field(..., min_length=3, max_length=100)
    mode: str = Field("cash", pattern="^(cash|upi|bank)$")   # how money goes back (desk path; ignored for razorpay)
    reference: Optional[str] = Field(None, max_length=100)   # UPI / bank ref of the payout
    # Owner-approval OTP (prompt 11): required only when REFUND_REQUIRES_OWNER_OTP is on.
    # Obtain via POST /fraud/otp/request (action="refund").
    owner_otp_id: Optional[int] = Field(None, gt=0)
    owner_otp_code: Optional[str] = Field(None, min_length=4, max_length=10)


class ExcessReturnRequest(BaseModel):
    """Return an overpaid advance/deposit (prompt 08b). Amount is computed server-side
    (= the folio's credit balance) and auto-allocated across refundable desk payments."""
    booking_id: int = Field(..., gt=0)
    mode: str = Field("cash", pattern="^(cash|upi|bank)$")   # how the money is handed back
    reference: Optional[str] = Field(None, max_length=100)   # UPI / bank ref of the payout


class DeskCollectRequest(BaseModel):
    """Integrated desk collection (prompt 19): raise a gateway-confirmed payment for the exact
    amount. upi_qr -> a Razorpay dynamic UPI QR; link -> a Razorpay payment link (send via
    WhatsApp/SMS); cash -> falls back to the recorded desk-cash path (prompt 08). The webhook
    (not staff) marks the folio paid for upi_qr/link."""
    booking_id: int = Field(..., gt=0)
    amount: float = Field(..., gt=0, le=10_000_000)          # base amount owed (before any convenience fee)
    method: str = Field(..., pattern="^(upi_qr|link|cash)$")
    apply_fee: Optional[bool] = Field(None)                  # override config's per-method fee toggle
    client_ref: Optional[str] = Field(None, max_length=64)   # desktop uuid (idempotency / offline outbox)
    reference: Optional[str] = Field(None, max_length=100)   # cash fallback: UPI ref / slip no


class CheckinAssignment(BaseModel):
    """Specific physical rooms for one booking item (len(room_ids) must equal item.quantity)."""
    booking_item_id: int = Field(..., gt=0)
    room_ids: list[int] = Field(..., min_length=1, max_length=10)


class GuestKycEntry(BaseModel):
    """One occupant's KYC captured at check-in (FE-3). `id_number` is masked server-side (raw
    never persisted). `id_scan_ref` is the encrypted-scan reference returned by the scan-upload
    endpoint (never a public URL)."""
    name: str = Field(..., min_length=1, max_length=100)
    id_type: str = Field(..., pattern="^(aadhaar|passport|driving_licence|voter_id|other)$")
    id_number: str = Field(..., min_length=4, max_length=30)   # stored masked; raw never persisted
    id_scan_ref: Optional[str] = Field(None, max_length=120)
    id_scan_mime: Optional[str] = Field(None, max_length=40)
    is_primary: bool = False


class CheckinRequest(BaseModel):
    booking_id: int = Field(..., gt=0)
    assignments: list[CheckinAssignment] = Field(..., min_length=1, max_length=10)
    id_type: str = Field(..., pattern="^(aadhaar|passport|driving_licence|voter_id|other)$")
    id_number: str = Field(..., min_length=4, max_length=30)  # stored masked; raw value never persisted
    # FE-3: full occupant roster (lead + companions), each with masked ID + optional encrypted scan.
    # Backward-compatible: empty ⇒ old single-guest behaviour (lead from id_type/id_number above).
    additional_guests: list[GuestKycEntry] = Field(default_factory=list, max_length=20)
    client_ref: Optional[str] = Field(None, max_length=64)


class CardIssueRequest(BaseModel):
    """Record a card issuance (desktop calls this AFTER encoding — record-before-encode lives
    client-side in the outbox; the server re-validates every row it receives)."""
    booking_id: int = Field(..., gt=0)
    room_id: int = Field(..., gt=0)
    card_uid: Optional[str] = Field(None, max_length=64)      # ReadCard hex after encode
    room_code: str = Field(..., min_length=8, max_length=16)  # BBFFRRAA
    valid_from: datetime
    valid_to: datetime
    issue_type: str = Field("checkin", pattern="^(checkin|extra|lost_reissue|shift)$")
    lost_card_id: Optional[int] = Field(None, gt=0)           # issuance to mark "lost" on lost_reissue
    owner_otp_id: Optional[int] = Field(None, gt=0)           # owner approval for extra/lost_reissue (ALT-1)
    owner_otp_code: Optional[str] = Field(None, min_length=4, max_length=10)
    station_id: Optional[str] = Field(None, max_length=50)
    encoded: bool = True      # False = pre-check only (nothing recorded on failure)
    offline: bool = False     # issued while the desktop was offline (evidence for fraud review)
    client_issued_at: Optional[datetime] = None               # desktop clock (evidence only)
    client_ref: Optional[str] = Field(None, max_length=64)


class CheckoutRequest(BaseModel):
    booking_id: int = Field(..., gt=0)
    override: bool = False                                    # settle despite non-zero balance
    override_reason: Optional[str] = Field(None, min_length=3, max_length=200)
    client_ref: Optional[str] = Field(None, max_length=64)


class RoomShiftRequest(BaseModel):
    """Move an in-house guest to a different room mid-stay (prompt 09). from_room_id is
    required because a group booking holds several rooms. applied_adjustment None = accept
    the server-computed rate difference; charging less than computed needs reason + admin
    (SHIFT_WAIVE_REQUIRES_ADMIN). dry_run validates + prices without changing anything."""
    booking_id: int = Field(..., gt=0)
    from_room_id: int = Field(..., gt=0)
    to_room_id: int = Field(..., gt=0)
    applied_adjustment: Optional[float] = Field(None, ge=-10_000_000, le=10_000_000)
    reason: Optional[str] = Field(None, min_length=3, max_length=200)
    owner_otp_id: Optional[int] = Field(None, gt=0)              # AC→non-AC downgrade approval (FE-10)
    owner_otp_code: Optional[str] = Field(None, min_length=4, max_length=10)
    dry_run: bool = False
    client_ref: Optional[str] = Field(None, max_length=64)


class PromotionCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    discount_type: str = Field(..., pattern="^(percent|flat)$")
    discount_value: float = Field(..., gt=0, le=1000000)
    room_type_id: Optional[int] = Field(None, gt=0)  # None = all room types
    is_active: bool = False
    valid_from: Optional[date] = None
    valid_to: Optional[date] = None

    @field_validator('discount_value')
    @classmethod
    def validate_percent(cls, v, info):
        if info.data.get('discount_type') == 'percent' and v > 100:
            raise ValueError('Percentage discount cannot exceed 100')
        return v

    @field_validator('valid_to')
    @classmethod
    def validate_window(cls, v, info):
        start = info.data.get('valid_from')
        if v is not None and start is not None and v < start:
            raise ValueError('valid_to must be on or after valid_from')
        return v


class PromotionUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=100)
    discount_type: Optional[str] = Field(None, pattern="^(percent|flat)$")
    discount_value: Optional[float] = Field(None, gt=0, le=1000000)
    room_type_id: Optional[int] = Field(None, gt=0)
    is_active: Optional[bool] = None
    valid_from: Optional[date] = None
    valid_to: Optional[date] = None


class PromotionToggle(BaseModel):
    is_active: bool

class AvailabilityBlockCreate(BaseModel):
    room_type_id: int = Field(..., gt=0)
    date: date
    reason: Optional[str] = Field(None, max_length=255)

class Enquiry(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    email: EmailStr
    phone: str = Field(..., min_length=10, max_length=15)
    message: str = Field(..., min_length=10, max_length=1000)


# =====================================================================
# RATES & TRAVEL-AGENT PRICING (prompt 10)
# =====================================================================

class TravelAgentCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    contact: Optional[str] = Field(None, max_length=100)
    gst_no: Optional[str] = Field(None, max_length=20)
    commission_percent: float = Field(default=0, ge=0, le=100)
    credit_limit: float = Field(default=0, ge=0, le=100_000_000)
    is_active: bool = True


class TravelAgentUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=100)
    contact: Optional[str] = Field(None, max_length=100)
    gst_no: Optional[str] = Field(None, max_length=20)
    commission_percent: Optional[float] = Field(None, ge=0, le=100)
    credit_limit: Optional[float] = Field(None, ge=0, le=100_000_000)
    is_active: Optional[bool] = None


class TravelAgentToggle(BaseModel):
    is_active: bool


class AgentRateCreate(BaseModel):
    """A negotiated per-night rate card (GST-exclusive) for one room type."""
    room_type_id: int = Field(..., gt=0)
    rate: float = Field(..., gt=0, le=1_000_000)
    valid_from: Optional[date] = None
    valid_to: Optional[date] = None

    @field_validator('valid_to')
    @classmethod
    def validate_window(cls, v, info):
        start = info.data.get('valid_from')
        if v is not None and start is not None and v < start:
            raise ValueError('valid_to must be on or after valid_from')
        return v


class AgentRateUpdate(BaseModel):
    room_type_id: Optional[int] = Field(None, gt=0)
    rate: Optional[float] = Field(None, gt=0, le=1_000_000)
    valid_from: Optional[date] = None
    valid_to: Optional[date] = None


class RatePlanCreate(BaseModel):
    """A channel/date rate override for a room type (seasonal / weekend / per-agent).
    price is per-night, GST-exclusive. days_of_week: CSV of 0=Mon..6=Sun (null = all days)."""
    channel: str = Field(..., pattern="^(walk_in|website|agent)$")
    agent_id: Optional[int] = Field(None, gt=0)   # required only for channel='agent' plans
    price: float = Field(..., gt=0, le=1_000_000)
    valid_from: Optional[date] = None
    valid_to: Optional[date] = None
    days_of_week: Optional[str] = Field(None, pattern=r"^[0-6](,[0-6])*$")
    priority: int = Field(default=0, ge=0, le=1000)
    is_active: bool = True

    @field_validator('valid_to')
    @classmethod
    def validate_window(cls, v, info):
        start = info.data.get('valid_from')
        if v is not None and start is not None and v < start:
            raise ValueError('valid_to must be on or after valid_from')
        return v


class RatePlanUpdate(BaseModel):
    channel: Optional[str] = Field(None, pattern="^(walk_in|website|agent)$")
    agent_id: Optional[int] = Field(None, gt=0)
    price: Optional[float] = Field(None, gt=0, le=1_000_000)
    valid_from: Optional[date] = None
    valid_to: Optional[date] = None
    days_of_week: Optional[str] = Field(None, pattern=r"^[0-6](,[0-6])*$")
    priority: Optional[int] = Field(None, ge=0, le=1000)
    is_active: Optional[bool] = None


class AgentPaymentCreate(BaseModel):
    """Record a commission payout to an agent (the 'paid' side of settlement)."""
    amount: float = Field(..., gt=0, le=100_000_000)
    paid_on: date
    mode: str = Field("bank", pattern="^(cash|upi|bank|adjustment)$")
    reference: Optional[str] = Field(None, max_length=100)
    note: Optional[str] = Field(None, max_length=200)


class AgentBulkPaymentItem(BaseModel):
    agent_id: int
    amount: float = Field(..., gt=0, le=100_000_000)


class AgentBulkPayment(BaseModel):
    """Record a payout to several agents at once (settle the selected outstanding balances)."""
    items: List[AgentBulkPaymentItem] = Field(..., min_length=1, max_length=200)
    paid_on: date
    mode: str = Field("bank", pattern="^(cash|upi|bank|adjustment)$")
    reference: Optional[str] = Field(None, max_length=100)
    note: Optional[str] = Field(None, max_length=200)


# =====================================================================
# ANTI-FRAUD / INTERNAL CONTROLS (prompt 11)
# =====================================================================

class OtpRequest(BaseModel):
    """Request an owner-approval one-time code for a sensitive action.
    The code is delivered on-screen (admin Approvals inbox) + server log in this build;
    prompt 15 swaps in WhatsApp delivery. The code is never returned from this endpoint."""
    action: str = Field(..., pattern="^(refund|discount_below_floor|void|off_hours_issue)$")
    context: Optional[dict] = None   # free-form JSON: what's being approved (booking/payment/amount)


class OtpVerify(BaseModel):
    """Standalone verify of an owner-approval code (also consumed inline by the gated endpoints)."""
    otp_id: int = Field(..., gt=0)
    code: str = Field(..., min_length=4, max_length=10)


class AlertReview(BaseModel):
    """Acknowledge or dismiss a fraud alert with an optional note."""
    status: str = Field(..., pattern="^(reviewed|dismissed)$")
    note: Optional[str] = Field(None, max_length=500)


# ============================================================
# Cash & shift management (prompt 12)
# ============================================================

class ShiftOpenRequest(BaseModel):
    """Open a cash drawer for a station with an opening float."""
    station_id: Optional[str] = Field(None, max_length=50)
    opening_balance: float = Field(..., ge=0)
    period: Optional[str] = Field(None, pattern="^(shift|day)$")  # defaults to admin's configured cycle


class ExpenseCreate(BaseModel):
    """Log a petty-cash expense against the station's open shift."""
    station_id: Optional[str] = Field(None, max_length=50)
    amount: float = Field(..., gt=0)
    category: str = Field(..., pattern=CATEGORY_SLUG_RE)   # membership checked against the editable list at the endpoint
    description: str = Field(..., min_length=2, max_length=200)
    receipt_ref: Optional[str] = Field(None, max_length=200)   # text reference only (photo -> prompt 14)
    client_ref: Optional[str] = Field(None, max_length=80)     # idempotency for double-submit


class FloatTopupRequest(BaseModel):
    """Add cash to an open drawer mid-shift (ALT-6). Increases the drawer float (and so the
    expected cash); each top-up is separately audited."""
    station_id: Optional[str] = Field(None, max_length=50)
    amount: float = Field(..., gt=0)
    note: Optional[str] = Field(None, max_length=200)


class ShiftCloseRequest(BaseModel):
    """Close the open shift: staff enters the counted cash (and optional note count)."""
    station_id: Optional[str] = Field(None, max_length=50)
    counted_cash: float = Field(..., ge=0)
    denominations: Optional[dict] = None                       # {"500": 3, "100": 5, ...}
    note: Optional[str] = Field(None, max_length=300)


class CashConfigUpdate(BaseModel):
    """Admin sets the cycle + variance-alert threshold (runtime, no redeploy)."""
    cycle: Optional[str] = Field(None, pattern="^(shift|day)$")
    variance_threshold: Optional[float] = Field(None, ge=0)
    variance_alert_enabled: Optional[bool] = None


# =====================================================================
# HOUSEKEEPING + MAINTENANCE (prompt 13)
# =====================================================================

class HousekeepingStatusUpdate(BaseModel):
    """A housekeeper sets a room's cleaning state. NOT 'inspected' (admin-only gate)
    and NOT 'vacant' (that's the re-sale flip done by inspect)."""
    status: str = Field(..., pattern="^(dirty|cleaning|clean|maintenance|dnd)$")
    photo_ref: Optional[str] = Field(None, max_length=500)     # text reference only (photo -> prompt 14)


class TaskCompleteRequest(BaseModel):
    """Mark a cleaning task done (room -> clean; auto-inspected if config on)."""
    checklist: Optional[list] = None                           # [{"item": "Bed", "done": true}, ...]
    photo_ref: Optional[str] = Field(None, max_length=500)
    client_ref: Optional[str] = Field(None, max_length=80)     # idempotency for offline double-submit


class InspectRequest(BaseModel):
    """Supervisor/admin marks a cleaned room inspected -> re-sellable (Room.status=vacant)."""
    note: Optional[str] = Field(None, max_length=300)


class MinibarRestockRequest(BaseModel):
    """Housekeeper restocks the minibar -> posts a GST-inclusive charge to the guest folio."""
    room_id: int
    description: str = Field(..., min_length=1, max_length=200)  # e.g. "2x Water, 1x Chips"
    qty: float = Field(default=1, gt=0, le=999)
    unit_price: float = Field(..., ge=0, le=1000000)          # GST-inclusive unit price
    gst_percent: float = Field(default=5, ge=0, le=28)
    client_ref: Optional[str] = Field(None, max_length=80)


class HousekeepingConfigUpdate(BaseModel):
    """Admin toggles auto-inspect (mark-clean also inspects, skipping the supervisor gate)."""
    auto_inspect: bool


class MaintenanceTicketCreate(BaseModel):
    """Raise a maintenance ticket. room_id optional (common areas); source derived from role."""
    room_id: Optional[int] = None
    area: Optional[str] = Field(None, max_length=100)
    category: str = Field(default="other", pattern=CATEGORY_SLUG_RE)   # membership checked at the endpoint
    issue: str = Field(..., min_length=3, max_length=500)
    priority: str = Field(default="normal", pattern="^(low|normal|high|urgent)$")
    booking_id: Optional[int] = None
    photo_ref: Optional[str] = Field(None, max_length=500)
    client_ref: Optional[str] = Field(None, max_length=80)


class TicketAssignRequest(BaseModel):
    """Admin assigns a ticket to a maintenance user (open -> assigned)."""
    assignee_id: int


class TicketStatusUpdate(BaseModel):
    """Assignee advances a ticket: in_progress|awaiting_parts|resolved."""
    status: str = Field(..., pattern="^(in_progress|awaiting_parts|resolved)$")
    note: Optional[str] = Field(None, max_length=500)


class TicketVerifyRequest(BaseModel):
    """Supervisor/admin verifies a resolved ticket -> closed."""
    resolution_notes: Optional[str] = Field(None, max_length=500)


class TicketItemCreate(BaseModel):
    """Assignee adds a required part/material to a ticket."""
    item: str = Field(..., min_length=1, max_length=200)
    qty: float = Field(default=1, gt=0, le=9999)
    est_cost: Optional[float] = Field(None, ge=0, le=10000000)


class TicketItemPurchase(BaseModel):
    """Admin logs an approved item as purchased -> optionally posts to expenses."""
    actual_cost: float = Field(..., ge=0, le=10000000)         # ₹ actually spent (posts to the ledger)
    category: str = Field(default="vendor", pattern="^(supplies|staff|vendor|misc)$")
    station_id: Optional[str] = Field(None, max_length=50)     # if given + shift open, links the expense to the drawer
    post_to_expenses: bool = True
    client_ref: Optional[str] = Field(None, max_length=80)


# =====================================================================
# CUSTOMER / GUEST CRM (prompt 14)
# =====================================================================
_ID_TYPE_PATTERN = "^(aadhaar|passport|driving_licence|voter_id|other)$"


class GuestProfileUpdate(BaseModel):
    """Upsert a guest's CRM profile (desk/admin). `id_number` is masked server-side;
    the raw value is never stored. Any field omitted is left unchanged."""
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    phone: Optional[str] = Field(None, min_length=5, max_length=15)
    email: Optional[EmailStr] = None
    id_type: Optional[str] = Field(None, pattern=_ID_TYPE_PATTERN)
    id_number: Optional[str] = Field(None, max_length=40)      # raw; masked on store
    id_photo_url: Optional[str] = Field(None, max_length=500)
    guest_photo_url: Optional[str] = Field(None, max_length=500)
    address: Optional[str] = Field(None, max_length=300)
    dob: Optional[date] = None
    gstin: Optional[str] = Field(None, max_length=20)
    marketing_optin: Optional[bool] = None
    notes: Optional[str] = Field(None, max_length=2000)
    # --- Form C / FRRO fields (prompt 18; passport/visa numbers masked server-side) ---
    nationality: Optional[str] = Field(None, max_length=60)
    is_foreign_national: Optional[bool] = None
    passport_number: Optional[str] = Field(None, max_length=40)      # raw; masked on store
    passport_place_of_issue: Optional[str] = Field(None, max_length=80)
    passport_expiry: Optional[date] = None
    visa_number: Optional[str] = Field(None, max_length=40)          # raw; masked on store
    visa_type: Optional[str] = Field(None, max_length=40)
    visa_expiry: Optional[date] = None
    arrived_from: Optional[str] = Field(None, max_length=120)
    next_destination: Optional[str] = Field(None, max_length=120)


class VipUpdate(BaseModel):
    """Flag/unflag a guest as VIP (admin)."""
    vip: bool


class BlacklistUpdate(BaseModel):
    """Add/remove a guest from the blacklist/watchlist (admin). Reason required when adding."""
    blacklist: bool
    reason: Optional[str] = Field(None, max_length=300)

    @field_validator("reason")
    @classmethod
    def _reason_when_blacklisting(cls, v, info):
        if info.data.get("blacklist") and not (v and v.strip()):
            raise ValueError("A reason is required to blacklist a guest")
        return v


class LoyaltyRedeem(BaseModel):
    """Redeem loyalty points as a folio discount on the guest's open folio."""
    points: int = Field(..., gt=0, le=10000000)
    booking_id: Optional[int] = None                          # which open folio to discount (else newest in-house)


class PreArrivalCreate(BaseModel):
    """Create + send a pre-arrival digital-registration link for a booking."""
    booking_id: int
    channel: str = Field(default="email", pattern="^(email|whatsapp|none)$")


class PreArrivalSubmit(BaseModel):
    """Guest submits their details via the public pre-arrival link (no auth)."""
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    phone: Optional[str] = Field(None, min_length=5, max_length=15)
    email: Optional[EmailStr] = None
    id_type: Optional[str] = Field(None, pattern=_ID_TYPE_PATTERN)
    id_number: Optional[str] = Field(None, max_length=40)     # raw; masked on store
    id_photo_url: Optional[str] = Field(None, max_length=500)
    address: Optional[str] = Field(None, max_length=300)
    dob: Optional[date] = None
    gstin: Optional[str] = Field(None, max_length=20)
    marketing_optin: Optional[bool] = None


# =====================================================================
# WHATSAPP & AUTOMATED ALERTS (prompt 15)
# =====================================================================

class WhatsAppConfigUpdate(BaseModel):
    """Admin sets which automations fire, their timing, the owner recipient + review link
    (runtime, no redeploy). Provider secrets stay in ENV."""
    checkout_reminder_enabled: Optional[bool] = None
    checkout_reminder_lead_hours: Optional[float] = Field(None, ge=0.5, le=48)
    overstay_enabled: Optional[bool] = None
    confirmation_enabled: Optional[bool] = None
    receipt_enabled: Optional[bool] = None
    room_ready_enabled: Optional[bool] = None
    review_enabled: Optional[bool] = None
    review_delay_hours: Optional[float] = Field(None, ge=0, le=168)
    owner_alerts_enabled: Optional[bool] = None
    owner_whatsapp: Optional[str] = Field(None, max_length=20)
    google_review_url: Optional[str] = Field(None, max_length=300)
    job_interval_minutes: Optional[int] = Field(None, ge=1, le=1440)
    daily_digest_hour: Optional[int] = Field(None, ge=0, le=23)


class OptOutCreate(BaseModel):
    """Add a phone to the WhatsApp opt-out list."""
    phone: str = Field(..., min_length=6, max_length=20)
    reason: Optional[str] = Field(None, max_length=120)


class WhatsAppTestSend(BaseModel):
    """Send one template to a number for manual verification."""
    to: str = Field(..., min_length=6, max_length=20)
    template: str = Field(..., min_length=1, max_length=60)
    params: Optional[dict] = None


# =====================================================================
# CORPORATE BILL-TO-COMPANY (prompt 18, slice 7)
# =====================================================================
class CompanyCreate(BaseModel):
    """A corporate account the hotel bills instead of the guest."""
    name: str = Field(..., min_length=2, max_length=150)
    gstin: Optional[str] = Field(None, max_length=20)
    address: Optional[str] = Field(None, max_length=300)
    city: Optional[str] = Field(None, max_length=80)
    state: Optional[str] = Field(None, max_length=80)
    state_code: Optional[str] = Field(None, max_length=4)
    contact_person: Optional[str] = Field(None, max_length=100)
    phone: Optional[str] = Field(None, max_length=20)
    email: Optional[str] = Field(None, max_length=120)
    credit_limit: float = Field(default=0, ge=0, le=100_000_000)
    credit_days: int = Field(default=30, ge=0, le=365)
    payment_terms: Optional[str] = Field(None, max_length=120)
    notes: Optional[str] = None


class CompanyUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=150)
    gstin: Optional[str] = Field(None, max_length=20)
    address: Optional[str] = Field(None, max_length=300)
    city: Optional[str] = Field(None, max_length=80)
    state: Optional[str] = Field(None, max_length=80)
    state_code: Optional[str] = Field(None, max_length=4)
    contact_person: Optional[str] = Field(None, max_length=100)
    phone: Optional[str] = Field(None, max_length=20)
    email: Optional[str] = Field(None, max_length=120)
    credit_limit: Optional[float] = Field(None, ge=0, le=100_000_000)
    credit_days: Optional[int] = Field(None, ge=0, le=365)
    payment_terms: Optional[str] = Field(None, max_length=120)
    is_active: Optional[bool] = None
    notes: Optional[str] = None


class CompanyPaymentRecord(BaseModel):
    """Record a settlement received from a company (credits its city ledger)."""
    amount: float = Field(..., gt=0, le=100_000_000)
    method: str = Field(default="bank_transfer",
                        pattern="^(cash|upi|bank_transfer|cheque|card|adjustment)$")
    reference: Optional[str] = Field(None, max_length=120)   # UTR / cheque no / receipt
    description: Optional[str] = Field(None, max_length=300)
    company_invoice_id: Optional[int] = Field(None, gt=0)    # settle a specific consolidated bill
    client_ref: Optional[str] = Field(None, max_length=64)   # idempotent double-submit guard


class CompanyAdjustment(BaseModel):
    """A signed correction on the city ledger. Append-only — never edits an earlier row."""
    amount: float = Field(..., le=100_000_000)               # signed: + company owes more, - credit note
    reason: str = Field(..., min_length=3, max_length=300)
    client_ref: Optional[str] = Field(None, max_length=64)

    @field_validator("amount")
    @classmethod
    def not_zero(cls, v):
        if v == 0:
            raise ValueError("Adjustment amount cannot be zero")
        return v


class CompanyInvoiceCreate(BaseModel):
    """Raise one consolidated GST invoice for every stay transferred in the period."""
    period_from: date
    period_to: date
    notes: Optional[str] = Field(None, max_length=300)

    @field_validator("period_to")
    @classmethod
    def period_ordered(cls, v, info):
        start = info.data.get("period_from")
        if start and v < start:
            raise ValueError("period_to must be on or after period_from")
        return v


class FolioBillToRequest(BaseModel):
    """Route a folio (or specific lines) to a company, or back to the guest.

    `charge_ids` omitted = the whole stay. `override_reason` lets an admin push past the
    credit limit; the override is audited."""
    bill_to: str = Field(default="company", pattern="^(guest|company)$")
    company_id: Optional[int] = Field(None, gt=0)
    charge_ids: Optional[List[int]] = None
    override_reason: Optional[str] = Field(None, min_length=3, max_length=300)


# =====================================================================
# VENDOR / AMC TRACKING (prompt 18, slice 13)
# =====================================================================
VENDOR_CATEGORY_RE = "^(laundry|lock_amc|linen|electrical|plumbing|it|fnb|security|other)$"


class VendorCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=150)
    category: str = Field(default="other", pattern=VENDOR_CATEGORY_RE)
    contact_person: Optional[str] = Field(None, max_length=100)
    phone: Optional[str] = Field(None, max_length=20)
    email: Optional[str] = Field(None, max_length=120)
    gstin: Optional[str] = Field(None, max_length=20)
    address: Optional[str] = Field(None, max_length=300)
    notes: Optional[str] = None


class VendorUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=150)
    category: Optional[str] = Field(None, pattern=VENDOR_CATEGORY_RE)
    contact_person: Optional[str] = Field(None, max_length=100)
    phone: Optional[str] = Field(None, max_length=20)
    email: Optional[str] = Field(None, max_length=120)
    gstin: Optional[str] = Field(None, max_length=20)
    address: Optional[str] = Field(None, max_length=300)
    is_active: Optional[bool] = None
    notes: Optional[str] = None


class VendorContractCreate(BaseModel):
    title: str = Field(..., min_length=2, max_length=200)
    contract_type: str = Field(default="amc", pattern="^(amc|rental|service|supply)$")
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    renewal_reminder_days: int = Field(default=30, ge=1, le=365)
    amount: Optional[float] = Field(None, ge=0, le=100_000_000)
    billing_cycle: Optional[str] = Field(
        None, pattern="^(monthly|quarterly|half_yearly|annual|one_time)$")
    document_url: Optional[str] = Field(None, max_length=500)
    notes: Optional[str] = None

    @field_validator("end_date")
    @classmethod
    def dates_ordered(cls, v, info):
        start = info.data.get("start_date")
        if v and start and v < start:
            raise ValueError("end_date must be on or after start_date")
        return v


class VendorContractUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=2, max_length=200)
    contract_type: Optional[str] = Field(None, pattern="^(amc|rental|service|supply)$")
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    renewal_reminder_days: Optional[int] = Field(None, ge=1, le=365)
    amount: Optional[float] = Field(None, ge=0, le=100_000_000)
    billing_cycle: Optional[str] = Field(
        None, pattern="^(monthly|quarterly|half_yearly|annual|one_time)$")
    document_url: Optional[str] = Field(None, max_length=500)
    status: Optional[str] = Field(None, pattern="^(active|expired|cancelled)$")
    notes: Optional[str] = None


# =====================================================================
# STAFF ROSTER + ATTENDANCE (prompt 18, slices 12 & 9)
# =====================================================================
SHIFT_TYPE_RE = "^(morning|evening|night|general)$"


class StaffShiftCreate(BaseModel):
    user_id: int = Field(..., gt=0)
    shift_date: date
    shift_type: str = Field(default="general", pattern=SHIFT_TYPE_RE)
    start_time: Optional[time] = None
    end_time: Optional[time] = None
    role_label: Optional[str] = Field(None, max_length=40)
    status: str = Field(default="planned", pattern="^(planned|confirmed|swapped|absent)$")
    notes: Optional[str] = Field(None, max_length=300)


class StaffShiftUpdate(BaseModel):
    shift_type: Optional[str] = Field(None, pattern=SHIFT_TYPE_RE)
    start_time: Optional[time] = None
    end_time: Optional[time] = None
    role_label: Optional[str] = Field(None, max_length=40)
    status: Optional[str] = Field(None, pattern="^(planned|confirmed|swapped|absent)$")
    notes: Optional[str] = Field(None, max_length=300)


class RosterBulkCopy(BaseModel):
    """Copy a week of planned duties forward (the common way a roster is built)."""
    source_week_start: date
    target_week_start: date
    overwrite: bool = False


class AttendancePunch(BaseModel):
    """Clock in/out at the desk with the staff member's existing User.pin."""
    username: str = Field(..., min_length=1, max_length=50)
    pin: str = Field(..., min_length=3, max_length=10)
    station_id: Optional[str] = Field(None, max_length=50)
    note: Optional[str] = Field(None, max_length=300)


class AttendanceCardPunch(BaseModel):
    """Clock in/out by staff key-card tap — resolves users.staff_card_uid.

    The endpoint is live and testable now; the DESKTOP tap that feeds it waits on the
    proRFL staff-card capture (phase0/CAPTURE-PLAYBOOK.md)."""
    card_uid: str = Field(..., min_length=4, max_length=40)
    station_id: Optional[str] = Field(None, max_length=50)


class AttendanceManual(BaseModel):
    """Admin records or corrects a worked session (append-only history, audited)."""
    user_id: int = Field(..., gt=0)
    clock_in: datetime
    clock_out: Optional[datetime] = None
    note: Optional[str] = Field(None, max_length=300)

    @field_validator("clock_out")
    @classmethod
    def out_after_in(cls, v, info):
        start = info.data.get("clock_in")
        if v and start and v <= start:
            raise ValueError("clock_out must be after clock_in")
        return v


class StaffCardAssign(BaseModel):
    """Bind a staff key-card UID to a user (or clear it with null)."""
    card_uid: Optional[str] = Field(None, max_length=40)


class BackofficeConfigUpdate(BaseModel):
    """Admin-editable back-office settings (secrets never live here)."""
    company_credit_block: Optional[bool] = None
    company_default_credit_days: Optional[int] = Field(None, ge=0, le=365)
    company_invoice_prefix: Optional[str] = Field(None, min_length=2, max_length=8)
    vendor_renewal_alerts_enabled: Optional[bool] = None
    vendor_renewal_lead_days: Optional[int] = Field(None, ge=1, le=365)
    roster_default_shift_type: Optional[str] = Field(None, pattern=SHIFT_TYPE_RE)
    attendance_pin_enabled: Optional[bool] = None
    attendance_auto_close_hours: Optional[int] = Field(None, ge=1, le=48)


# =====================================================================
# INVENTORY / STOCK (prompt 18c, slice 8)
# =====================================================================
STOCK_CATEGORY_RE = "^(minibar|toiletries|linen|supplies|fnb|cleaning|other)$"
STOCK_UNIT_RE = "^(pcs|bottle|kg|litre|pack|roll|set)$"


class StockItemCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=150)
    sku: Optional[str] = Field(None, max_length=50)
    category: str = Field(default="other", pattern=STOCK_CATEGORY_RE)
    unit: str = Field(default="pcs", pattern=STOCK_UNIT_RE)
    reorder_threshold: float = Field(default=0, ge=0, le=1_000_000)
    opening_qty: float = Field(default=0, ge=0, le=1_000_000)   # seeds an initial 'adjust' movement
    sale_price: Optional[float] = Field(None, ge=0, le=1_000_000)
    gst_percent: Optional[float] = Field(None, ge=0, le=100)
    vendor_id: Optional[int] = Field(None, gt=0)
    notes: Optional[str] = None


class StockItemUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=150)
    sku: Optional[str] = Field(None, max_length=50)
    category: Optional[str] = Field(None, pattern=STOCK_CATEGORY_RE)
    unit: Optional[str] = Field(None, pattern=STOCK_UNIT_RE)
    reorder_threshold: Optional[float] = Field(None, ge=0, le=1_000_000)
    sale_price: Optional[float] = Field(None, ge=0, le=1_000_000)
    gst_percent: Optional[float] = Field(None, ge=0, le=100)
    vendor_id: Optional[int] = Field(None, gt=0)
    is_active: Optional[bool] = None
    notes: Optional[str] = None


class StockReceiveRequest(BaseModel):
    """Receive stock in (delta > 0). Optionally posts a petty-cash expense to the open shift."""
    qty: float = Field(..., gt=0, le=1_000_000)
    unit_cost: Optional[float] = Field(None, ge=0, le=1_000_000)
    post_expense: bool = False               # if true + unit_cost, log qty*unit_cost to expenses
    vendor_id: Optional[int] = Field(None, gt=0)
    reason: Optional[str] = Field(None, max_length=200)
    client_ref: Optional[str] = Field(None, max_length=80)


class StockAdjustRequest(BaseModel):
    """Correct stock (adjust = signed delta, e.g. a stock-count fix) or write off wastage (delta < 0)."""
    delta_qty: float = Field(..., le=1_000_000, ge=-1_000_000)
    type: str = Field(default="adjust", pattern="^(adjust|wastage)$")
    reason: str = Field(..., min_length=2, max_length=200)   # required for adjust/wastage
    client_ref: Optional[str] = Field(None, max_length=80)

    @field_validator("delta_qty")
    @classmethod
    def non_zero(cls, v):
        if v == 0:
            raise ValueError("delta_qty must be non-zero")
        return v


class StockConfigUpdate(BaseModel):
    low_stock_alerts_enabled: Optional[bool] = None
    low_stock_default_threshold: Optional[float] = Field(None, ge=0, le=1_000_000)


# ============================================================
# LINEN / LAUNDRY (FE-9)
# ============================================================
class LinenItemCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    unit: str = Field(default="pcs", max_length=20)
    launderable: bool = True
    reorder_threshold: float = Field(default=0, ge=0, le=1_000_000)
    opening_clean: float = Field(default=0, ge=0, le=1_000_000)   # seeds the initial clean/stock count
    notes: Optional[str] = Field(None, max_length=300)


class LinenItemUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=100)
    unit: Optional[str] = Field(None, max_length=20)
    launderable: Optional[bool] = None
    reorder_threshold: Optional[float] = Field(None, ge=0, le=1_000_000)
    is_active: Optional[bool] = None
    notes: Optional[str] = Field(None, max_length=300)


class LinenMoveRequest(BaseModel):
    """A single linen stage move (send-laundry / receive / replenish / issue). Qty is positive."""
    qty: float = Field(..., gt=0, le=1_000_000)
    reason: Optional[str] = Field(None, max_length=200)


class LinenSetItem(BaseModel):
    item_id: int = Field(..., gt=0)
    qty: float = Field(..., ge=0, le=100000)


class LinenSetUpdate(BaseModel):
    """Replace a room type's default linen set (rows with qty 0 are dropped)."""
    items: list[LinenSetItem] = Field(default_factory=list, max_length=100)


# =====================================================================
# GUEST COMPLAINTS (prompt 18c, slice 11) — a view over source='guest' tickets
# =====================================================================
COMPLAINT_CATEGORY_RE = CATEGORY_SLUG_RE   # editable list (F-A); membership checked at the endpoint
COMPLAINT_PRIORITY_RE = "^(low|normal|high|urgent)$"


class ComplaintCreate(BaseModel):
    """Front desk logs a guest's complaint against a stay (source='guest')."""
    issue: str = Field(..., min_length=3, max_length=500)
    booking_id: Optional[int] = None
    room_id: Optional[int] = None
    category: str = Field(default="other", pattern=COMPLAINT_CATEGORY_RE)
    priority: str = Field(default="normal", pattern=COMPLAINT_PRIORITY_RE)
    client_ref: Optional[str] = Field(None, max_length=80)


class ComplaintRespond(BaseModel):
    """Stamp first response (acknowledged to the guest); optionally add a note."""
    note: Optional[str] = Field(None, max_length=500)


class ComplaintEscalate(BaseModel):
    """Manually escalate a complaint (bumps escalation_level + alerts the owner)."""
    reason: Optional[str] = Field(None, max_length=200)


class ComplaintResolve(BaseModel):
    resolution_notes: Optional[str] = Field(None, max_length=500)


class ComplaintBulkResolve(BaseModel):
    """Resolve several complaints at once (clear a backlog). Already-closed ones are skipped."""
    ids: List[int] = Field(..., min_length=1, max_length=200)
    resolution_notes: Optional[str] = Field(None, max_length=500)


class ComplaintCompensate(BaseModel):
    """Admin logs a goodwill credit to the guest's folio (reason required, audited)."""
    amount: float = Field(..., gt=0, le=1_000_000)
    reason: str = Field(..., min_length=2, max_length=200)
    client_ref: Optional[str] = Field(None, max_length=80)


class ComplaintConfigUpdate(BaseModel):
    complaint_sla_response_hours_urgent: Optional[int] = Field(None, ge=1, le=168)
    complaint_sla_response_hours_high: Optional[int] = Field(None, ge=1, le=168)
    complaint_sla_response_hours_normal: Optional[int] = Field(None, ge=1, le=168)
    complaint_sla_response_hours_low: Optional[int] = Field(None, ge=1, le=168)
    complaint_sla_resolve_hours_urgent: Optional[int] = Field(None, ge=1, le=720)
    complaint_sla_resolve_hours_high: Optional[int] = Field(None, ge=1, le=720)
    complaint_sla_resolve_hours_normal: Optional[int] = Field(None, ge=1, le=720)
    complaint_sla_resolve_hours_low: Optional[int] = Field(None, ge=1, le=720)
    complaint_escalation_enabled: Optional[bool] = None
    complaint_auto_compensation_enabled: Optional[bool] = None


# =====================================================================
# GUEST EXTRAS / IN-ROOM QR PORTAL (prompt 18d, slice 10)
# =====================================================================
MENU_CATEGORY_RE = CATEGORY_SLUG_RE   # editable list (F-A); membership checked at the endpoint


class RoomServiceLine(BaseModel):
    menu_item_id: int = Field(..., gt=0)
    qty: int = Field(default=1, ge=1, le=50)


class RoomServiceOrder(BaseModel):
    """Guest places a room-service order from the portal. Creates a request; the desk posts the
    folio charge on fulfil (no charge without staff)."""
    items: List[RoomServiceLine] = Field(..., min_length=1)
    note: Optional[str] = Field(None, max_length=300)
    client_ref: Optional[str] = Field(None, max_length=80)


class WakeupRequest(BaseModel):
    time: str = Field(..., pattern=r"^([01]\d|2[0-3]):[0-5]\d$")   # HH:MM (24h)
    note: Optional[str] = Field(None, max_length=300)
    client_ref: Optional[str] = Field(None, max_length=80)


class CabRequest(BaseModel):
    pickup_time: Optional[str] = Field(None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    destination: str = Field(..., min_length=2, max_length=200)
    passengers: int = Field(default=1, ge=1, le=20)
    note: Optional[str] = Field(None, max_length=300)
    client_ref: Optional[str] = Field(None, max_length=80)


class WifiRequestBody(BaseModel):
    client_ref: Optional[str] = Field(None, max_length=80)


class CheckoutRequestBody(BaseModel):
    note: Optional[str] = Field(None, max_length=300)
    client_ref: Optional[str] = Field(None, max_length=80)


class RequestActionRequest(BaseModel):
    """Desk acts on a guest request (acknowledge / complete / dismiss). For a room-service
    completion the desk may add/override a resolution note; the charge is posted from the order."""
    note: Optional[str] = Field(None, max_length=300)
    code: Optional[str] = Field(None, max_length=40)   # WiFi voucher code issued on manual fulfil


class MenuItemCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=150)
    description: Optional[str] = Field(None, max_length=300)
    category: str = Field(default="food", pattern=MENU_CATEGORY_RE)
    price: float = Field(..., ge=0, le=1_000_000)
    gst_percent: Optional[float] = Field(None, ge=0, le=100)
    is_available: bool = True
    sort_order: int = Field(default=0, ge=0, le=10_000)
    stock_item_id: Optional[int] = Field(None, gt=0)


class MenuItemUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=150)
    description: Optional[str] = Field(None, max_length=300)
    category: Optional[str] = Field(None, pattern=MENU_CATEGORY_RE)
    price: Optional[float] = Field(None, ge=0, le=1_000_000)
    gst_percent: Optional[float] = Field(None, ge=0, le=100)
    is_available: Optional[bool] = None
    sort_order: Optional[int] = Field(None, ge=0, le=10_000)
    stock_item_id: Optional[int] = Field(None, gt=0)


class PortalSessionRequest(BaseModel):
    """Desk mints (or re-fetches) the in-room QR session for a checked-in booking."""
    booking_id: int = Field(..., gt=0)


class PortalConfigUpdate(BaseModel):
    guest_portal_enabled: Optional[bool] = None
    portal_room_service_enabled: Optional[bool] = None
    portal_wifi_enabled: Optional[bool] = None
    portal_wakeup_enabled: Optional[bool] = None
    portal_cab_enabled: Optional[bool] = None
    portal_contactless_checkout_enabled: Optional[bool] = None
    wifi_ssid: Optional[str] = Field(None, max_length=60)
    wifi_voucher_mode: Optional[str] = Field(None, pattern="^(auto|manual)$")

