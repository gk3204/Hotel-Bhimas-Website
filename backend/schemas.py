from typing import Optional
from pydantic import BaseModel, Field, EmailStr, field_validator
from datetime import date

class UserCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=8, max_length=100)
    role: str = Field(..., pattern="^(admin|reception|user)$")

    @field_validator('username')
    @classmethod
    def username_alphanumeric(cls, v):
        if not v.replace('_', '').replace('-', '').isalnum():
            raise ValueError('Username must be alphanumeric with underscores/hyphens only')
        return v


class UserUpdate(BaseModel):
    password: Optional[str] = Field(None, min_length=8, max_length=100)
    role: Optional[str] = Field(None, pattern="^(admin|reception|user)$")


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


class BookingItemCreate(BaseModel):
    room_type_id: int = Field(..., gt=0)
    quantity: int = Field(default=1, ge=1, le=5)


class BookingCreate(BaseModel):
    rooms: list[BookingItemCreate] = Field(..., min_items=1, max_items=10)
    guest_name: str = Field(..., min_length=2, max_length=100)
    phone: str = Field(..., min_length=10, max_length=15)
    email: Optional[EmailStr] = None
    check_in: date
    check_out: date
    booking_source: str = Field("online", pattern="^(online|frontdesk|website)$")

    @field_validator('check_out')
    @classmethod
    def validate_checkout(cls, v, info):
        if 'check_in' in info.data and v <= info.data['check_in']:
            raise ValueError('Check-out date must be after check-in date')
        return v

class AvailabilityBlockCreate(BaseModel):
    room_type_id: int = Field(..., gt=0)
    date: date
    reason: Optional[str] = Field(None, max_length=255)

class Enquiry(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    email: EmailStr
    phone: str = Field(..., min_length=10, max_length=15)
    message: str = Field(..., min_length=10, max_length=1000)

