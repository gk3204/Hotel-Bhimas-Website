from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import and_, func
from database import SessionLocal
from models import RoomTypeAvailability, RoomType, Booking, BookingItem
from schemas import AvailabilityBlockCreate
from utils.auth_utils import require_reception_or_admin
from utils.booking_cleanup import expire_pending_bookings
from datetime import datetime, date

router = APIRouter(
    prefix="/room-type-availability",
    tags=["Room Type Availability"]
)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# 🔒 BLOCK a date
@router.post("/block",dependencies=[Depends(require_reception_or_admin)])
def block_date(
    data: AvailabilityBlockCreate,
    db: Session = Depends(get_db)
):
    room_type = db.query(RoomType).filter(
        RoomType.room_type_id == data.room_type_id
    ).first()

    if not room_type:
        raise HTTPException(status_code=404, detail="Room type not found")

    exists = db.query(RoomTypeAvailability).filter(
        RoomTypeAvailability.room_type_id == data.room_type_id,
        RoomTypeAvailability.date == data.date
    ).first()

    if exists:
        raise HTTPException(status_code=409, detail="Date already blocked")

    block = RoomTypeAvailability(
        room_type_id=data.room_type_id,
        date=data.date,
        is_available=False,  # ✅ EXPLICITLY SET TO FALSE WHEN BLOCKING
        reason=data.reason
    )

    db.add(block)
    db.commit()

    return {"message": "Date blocked successfully"}


# 🔓 UNBLOCK a date
@router.delete("/unblock",dependencies=[Depends(require_reception_or_admin)])
def unblock_date(
    room_type_id: int = Query(...),
    date: str = Query(...),
    db: Session = Depends(get_db)
):
    """Unblock a date for a room type"""
    
    # Convert string to date object
    try:
        block_date = datetime.strptime(date, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD")
    
    block = db.query(RoomTypeAvailability).filter(
        RoomTypeAvailability.room_type_id == room_type_id,
        RoomTypeAvailability.date == block_date
    ).first()

    if not block:
        raise HTTPException(status_code=404, detail="No block found for this date")

    db.delete(block)
    db.commit()

    return {"message": "Date unblocked successfully"}


# 📅 VIEW blocked dates
@router.get("/{room_type_id}")
def get_blocked_dates(
    room_type_id: int,
    db: Session = Depends(get_db)
):
    return db.query(RoomTypeAvailability).filter(
        RoomTypeAvailability.room_type_id == room_type_id
    ).all()


# ✅ CHECK AVAILABLE ROOMS FOR DATE RANGE
@router.get("/check-availability/{room_type_id}")
def check_room_availability(
    room_type_id: int,
    check_in: str = Query(..., description="Check-in date in YYYY-MM-DD format"),
    check_out: str = Query(..., description="Check-out date in YYYY-MM-DD format"),
    db: Session = Depends(get_db)
):
    """
    Get available rooms for a specific room type and date range.
    Returns total_rooms, booked_rooms, and available_rooms.
    """
    # 🔒 Clean up expired bookings so users see accurate availability
    expire_pending_bookings(db)
    
    try:
        check_in_date = datetime.strptime(check_in, "%Y-%m-%d").date()
        check_out_date = datetime.strptime(check_out, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD")
    
    # Validate date range
    if check_in_date >= check_out_date:
        raise HTTPException(status_code=400, detail="Check-out must be after check-in")
    
    # Get room type
    room_type = db.query(RoomType).filter(
        RoomType.room_type_id == room_type_id
    ).first()
    
    if not room_type:
        raise HTTPException(status_code=404, detail="Room type not found")
    
    # Check if any date is blocked
    blocked_date = db.query(RoomTypeAvailability).filter(
        RoomTypeAvailability.room_type_id == room_type_id,
        RoomTypeAvailability.date >= check_in_date,
        RoomTypeAvailability.date < check_out_date,
        RoomTypeAvailability.is_available == False
    ).first()
    
    if blocked_date:
        return {
            "room_type_id": room_type_id,
            "room_type_name": room_type.name,
            "total_rooms": room_type.total_rooms,
            "booked_rooms": 0,
            "available_rooms": 0,
            "is_blocked": True,
            "block_reason": blocked_date.reason or "Rooms not available for selected dates"
        }
    
    # Count booked rooms for overlapping dates (only confirmed & pending_payment & payment_pending bookings)
    booked_rooms = db.query(func.sum(BookingItem.quantity)).filter(
        BookingItem.room_type_id == room_type_id,
        BookingItem.booking_id.in_(
            db.query(Booking.booking_id).filter(
                Booking.status.in_(["confirmed", "pending_payment", "payment_pending"]),
                Booking.check_in < check_out_date,
                Booking.check_out > check_in_date
            )
        )
    ).scalar() or 0
    
    available_rooms = room_type.total_rooms - booked_rooms
    
    return {
        "room_type_id": room_type_id,
        "room_type_name": room_type.name,
        "total_rooms": room_type.total_rooms,
        "booked_rooms": int(booked_rooms),
        "available_rooms": max(0, int(available_rooms)),
        "is_blocked": False,
        "check_in": check_in_date,
        "check_out": check_out_date
    }
