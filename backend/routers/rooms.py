"""Physical room inventory (prompt 04). Rooms carry the building/floor/number the door locks were
programmed with (card code BBFFRR). Admin manages rooms; reception reads them (desktop room grid).
"""
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database import SessionLocal
from models import Room, RoomType, BookingItem, CardIssuance
from schemas import RoomCreate, RoomUpdate, RoomStatusUpdate, RoomActiveToggle
from utils.auth_utils import require_admin, require_reception_or_admin
from utils.audit import write_audit

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/rooms", tags=["Rooms"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _serialize(r: Room) -> dict:
    return {
        "room_id": r.room_id,
        "room_number": r.room_number,
        "room_type_id": r.room_type_id,
        "building": r.building,
        "floor": r.floor,
        "max_cards": r.max_cards,
        "lock_no": r.lock_no,
        "is_active": r.is_active,
        "status": r.status,
    }


# GET all rooms — reception (desktop room grid) + admin
@router.get("/", dependencies=[Depends(require_reception_or_admin)])
def list_rooms(db: Session = Depends(get_db)):
    rooms = db.query(Room).order_by(Room.room_number).all()
    return [_serialize(r) for r in rooms]


# CREATE room — admin only
@router.post("/", dependencies=[Depends(require_admin)])
def create_room(data: RoomCreate, db: Session = Depends(get_db)):
    try:
        if db.query(Room).filter(Room.room_number == data.room_number).first():
            raise HTTPException(status_code=400, detail="Room number already exists")
        if not db.query(RoomType).filter(RoomType.room_type_id == data.room_type_id).first():
            raise HTTPException(status_code=400, detail="Room type not found")

        room = Room(
            room_number=data.room_number,
            room_type_id=data.room_type_id,
            building=data.building,
            floor=data.floor,
            max_cards=data.max_cards,
            lock_no=data.lock_no,
            is_active=data.is_active,
            status=data.status or "vacant",
        )
        db.add(room)
        db.commit()
        db.refresh(room)
        write_audit(db, None, "room.create", "room", room.room_id, after=_serialize(room), client="web", commit=True)
        return _serialize(room)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"create_room failed: {e}", exc_info=True)
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to create room")


# UPDATE room — admin only
@router.put("/{room_id}", dependencies=[Depends(require_admin)])
def update_room(room_id: int, data: RoomUpdate, db: Session = Depends(get_db)):
    try:
        room = db.query(Room).filter(Room.room_id == room_id).first()
        if not room:
            raise HTTPException(status_code=404, detail="Room not found")

        before = _serialize(room)
        update_data = data.model_dump(exclude_unset=True)

        # unique room_number check if changing
        new_number = update_data.get("room_number")
        if new_number and new_number != room.room_number:
            if db.query(Room).filter(Room.room_number == new_number).first():
                raise HTTPException(status_code=400, detail="Room number already exists")

        if "room_type_id" in update_data:
            if not db.query(RoomType).filter(RoomType.room_type_id == update_data["room_type_id"]).first():
                raise HTTPException(status_code=400, detail="Room type not found")

        for key, value in update_data.items():
            setattr(room, key, value)
        db.commit()
        db.refresh(room)
        write_audit(db, None, "room.update", "room", room.room_id, before=before, after=_serialize(room), client="web", commit=True)
        return _serialize(room)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"update_room failed: {e}", exc_info=True)
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to update room")


# SET status only — admin only (reception changes status via check-in/out flows, not here)
@router.patch("/{room_id}/status", dependencies=[Depends(require_admin)])
def set_room_status(room_id: int, data: RoomStatusUpdate, db: Session = Depends(get_db)):
    room = db.query(Room).filter(Room.room_id == room_id).first()
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")
    before = room.status
    room.status = data.status
    if data.status != before:
        room.status_changed_at = datetime.utcnow()  # prompt 11: cleaning-too-long detection
    db.commit()
    write_audit(db, None, "room.status", "room", room.room_id,
                before={"status": before}, after={"status": room.status}, client="web", commit=True)
    return {"room_id": room_id, "status": room.status}


# ACTIVE toggle — admin only. is_active=False = out of service (repair) → excluded from booking/assignment.
@router.patch("/{room_id}/active", dependencies=[Depends(require_admin)])
def set_room_active(room_id: int, data: RoomActiveToggle, db: Session = Depends(get_db)):
    room = db.query(Room).filter(Room.room_id == room_id).first()
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")
    before = room.is_active
    room.is_active = data.is_active
    db.commit()
    write_audit(db, None, "room.active", "room", room.room_id,
                before={"is_active": before}, after={"is_active": room.is_active}, client="web", commit=True)
    return {"room_id": room_id, "is_active": room.is_active}


# DELETE room — admin only; blocked if referenced by bookings or issued cards
@router.delete("/{room_id}", dependencies=[Depends(require_admin)])
def delete_room(room_id: int, db: Session = Depends(get_db)):
    room = db.query(Room).filter(Room.room_id == room_id).first()
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")
    if db.query(BookingItem).filter(BookingItem.room_id == room_id).first():
        raise HTTPException(status_code=409, detail="Room is referenced by bookings; cannot delete")
    if db.query(CardIssuance).filter(CardIssuance.room_id == room_id).first():
        raise HTTPException(status_code=409, detail="Room has issued cards; cannot delete")
    db.delete(room)
    db.commit()
    write_audit(db, None, "room.delete", "room", room_id, before={"room_number": room.room_number}, client="web", commit=True)
    return {"message": "Room deleted", "room_id": room_id}
