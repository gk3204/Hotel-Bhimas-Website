from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database import SessionLocal
from models import RoomType
from schemas import RoomTypeCreate, RoomTypeUpdate, RoomTypeUpdateDetails

router = APIRouter(
    prefix="/room-types",
    tags=["Room Types"]
)

# Dependency
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# GET all room types
@router.get("/")
def get_room_types(db: Session = Depends(get_db)):
    return db.query(RoomType).all()

# CREATE room type
@router.post("/")
def create_room_type(
    data: RoomTypeCreate,
    db: Session = Depends(get_db)
):
    room_type = RoomType(
        name=data.name,
        price_per_night=data.price_per_night,
        gst_percent=data.gst_percent,
        max_occupancy=data.max_occupancy,
        total_rooms=data.total_rooms,
        is_active=True
    )

    db.add(room_type)
    db.commit()
    db.refresh(room_type)
    return room_type


# TOGGLE active/inactive
@router.patch("/{room_type_id}")
def toggle_room_type(
    room_type_id: int,
    data: RoomTypeUpdate,
    db: Session = Depends(get_db)
):
    room_type = db.query(RoomType).filter(
        RoomType.room_type_id == room_type_id
    ).first()

    if not room_type:
        raise HTTPException(status_code=404, detail="Room type not found")

    room_type.is_active = data.is_active
    db.commit()

    return {
        "message": "Room type updated",
        "room_type_id": room_type_id,
        "is_active": room_type.is_active
    }

@router.put("/{room_type_id}")
def update_room_type_details(
    room_type_id: int,
    data: RoomTypeUpdateDetails,
    db: Session = Depends(get_db)
):
    room_type = db.query(RoomType).filter(
        RoomType.room_type_id == room_type_id
    ).first()

    if not room_type:
        raise HTTPException(status_code=404, detail="Room type not found")

    update_data = data.dict(exclude_unset=True)

    for key, value in update_data.items():
        setattr(room_type, key, value)

    db.commit()
    db.refresh(room_type)

    return room_type



