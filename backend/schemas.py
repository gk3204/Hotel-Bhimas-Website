from typing import Optional
from pydantic import BaseModel, Field, EmailStr, field_validator
from datetime import date, time, datetime

class UserCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=8, max_length=100)
    role: str = Field(..., pattern="^(admin|reception|housekeeper|maintenance|user)$")

    @field_validator('username')
    @classmethod
    def username_alphanumeric(cls, v):
        if not v.replace('_', '').replace('-', '').isalnum():
            raise ValueError('Username must be alphanumeric with underscores/hyphens only')
        return v


class UserUpdate(BaseModel):
    password: Optional[str] = Field(None, min_length=8, max_length=100)
    role: Optional[str] = Field(None, pattern="^(admin|reception|housekeeper|maintenance|user)$")


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
        pattern="^(walk_in|frontdesk|agent|makemytrip|goibibo|booking_com|agoda|other)$",
    )

    @field_validator('check_out')
    @classmethod
    def validate_desk_checkout(cls, v, info):
        if 'check_in' in info.data and v <= info.data['check_in']:
            raise ValueError('Check-out date must be after check-in date')
        return v


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


class ExcessReturnRequest(BaseModel):
    """Return an overpaid advance/deposit (prompt 08b). Amount is computed server-side
    (= the folio's credit balance) and auto-allocated across refundable desk payments."""
    booking_id: int = Field(..., gt=0)
    mode: str = Field("cash", pattern="^(cash|upi|bank)$")   # how the money is handed back
    reference: Optional[str] = Field(None, max_length=100)   # UPI / bank ref of the payout


class CheckinAssignment(BaseModel):
    """Specific physical rooms for one booking item (len(room_ids) must equal item.quantity)."""
    booking_item_id: int = Field(..., gt=0)
    room_ids: list[int] = Field(..., min_length=1, max_length=10)


class CheckinRequest(BaseModel):
    booking_id: int = Field(..., gt=0)
    assignments: list[CheckinAssignment] = Field(..., min_length=1, max_length=10)
    id_type: str = Field(..., pattern="^(aadhaar|passport|driving_licence|voter_id|other)$")
    id_number: str = Field(..., min_length=4, max_length=30)  # stored masked; raw value never persisted
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

