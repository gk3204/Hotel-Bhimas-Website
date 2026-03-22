from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from database import SessionLocal
from models import RoomTypeAvailability, RoomType
from schemas import AvailabilityBlockCreate
from utils.auth_utils import require_reception_or_admin
from datetime import datetime

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
