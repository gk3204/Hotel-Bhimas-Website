"""Physical room inventory (prompt 04). Rooms carry the building/floor/number the door locks were
programmed with (card code BBFFRR). Admin manages rooms; reception reads them (desktop room grid).
"""
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database import SessionLocal
from models import Room, RoomType, Booking, BookingItem, CardIssuance, Guest, MaintenanceTicket
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


def _type_names(db) -> dict:
    """{room_type_id: name} — built once per call site so a room LIST does not N+1.
    Deliberately not memoised at module level: a renamed room type would then show the old
    name until the process restarted."""
    return dict(db.query(RoomType.room_type_id, RoomType.name).all())


def _serialize(r: Room, type_names: dict | None = None) -> dict:
    return {
        "room_id": r.room_id,
        "room_number": r.room_number,
        "room_type_id": r.room_type_id,
        "building": r.building,
        "floor": r.floor,
        "max_cards": r.max_cards,
        "lock_no": r.lock_no,
        # v4b7: the second type this room may be sold as, plus its name for the admin list.
        "alt_room_type_id": r.alt_room_type_id,
        "alt_room_type_name": (type_names or {}).get(r.alt_room_type_id),
        "is_active": r.is_active,
        "status": r.status,
    }


# GET all rooms — reception (desktop room grid) + admin
@router.get("/", dependencies=[Depends(require_reception_or_admin)])
def list_rooms(db: Session = Depends(get_db)):
    rooms = db.query(Room).order_by(Room.room_number).all()
    names = _type_names(db)
    return [_serialize(r, names) for r in rooms]


# CREATE room — admin only
@router.get("/{room_id}/history", dependencies=[Depends(require_reception_or_admin)])
def room_history(room_id: int, limit: int = 25, db: Session = Depends(get_db)):
    """Per-room history (FE-8): recent stays, maintenance tickets, and cards issued for this
    physical room — the room's timeline for the admin drill-down. `limit` caps each list."""
    room = db.query(Room).filter(Room.room_id == room_id).first()
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")
    limit = max(1, min(200, limit))

    # ---- stays (bookings that included this room), newest arrival first ----
    stays = []
    q = (db.query(Booking, Guest)
         .join(BookingItem, BookingItem.booking_id == Booking.booking_id)
         .outerjoin(Guest, Guest.guest_id == Booking.guest_id)
         .filter(BookingItem.room_id == room_id)
         .order_by(Booking.check_in.desc())
         .limit(limit))
    for b, g in q.all():
        stays.append({
            "booking_id": b.booking_id,
            "guest_name": g.name if g else None,
            "guest_phone": g.phone if g else None,
            "check_in": str(b.check_in) if b.check_in else None,
            "check_out": str(b.check_out) if b.check_out else None,
            "status": b.status,
            "checked_in_at": b.checked_in_at.isoformat() if b.checked_in_at else None,
            "checked_out_at": b.checked_out_at.isoformat() if b.checked_out_at else None,
            "grand_total": float(b.grand_total or 0),
            "source": b.booking_source,
        })

    # ---- maintenance tickets raised on this room, newest first ----
    tickets = []
    for t in (db.query(MaintenanceTicket)
              .filter(MaintenanceTicket.room_id == room_id)
              .order_by(MaintenanceTicket.created_at.desc()).limit(limit).all()):
        tickets.append({
            "id": t.id, "category": t.category, "issue": t.issue, "priority": t.priority,
            "status": t.status, "source": t.source,
            "created_at": t.created_at.isoformat() if t.created_at else None,
            "resolved_at": t.resolved_at.isoformat() if t.resolved_at else None,
        })

    # ---- cards issued for this room, newest first ----
    cards = []
    for c in (db.query(CardIssuance)
              .filter(CardIssuance.room_id == room_id)
              .order_by(CardIssuance.issued_at.desc()).limit(limit).all()):
        cards.append({
            "id": c.id, "booking_id": c.booking_id, "card_type": c.card_type,
            "issue_type": c.issue_type, "status": c.status,
            "issued_at": c.issued_at.isoformat() if c.issued_at else None,
            "valid_to": c.valid_to.isoformat() if c.valid_to else None,
        })

    return {
        "room": _serialize(room),
        "stays": stays,
        "tickets": tickets,
        "cards": cards,
        "counts": {"stays": len(stays), "tickets": len(tickets), "cards": len(cards)},
    }


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
            alt_room_type_id=data.alt_room_type_id,
            is_active=data.is_active,
            status=data.status or "vacant",
        )
        db.add(room)
        db.commit()
        db.refresh(room)
        names = _type_names(db)
        write_audit(db, None, "room.create", "room", room.room_id, after=_serialize(room, names), client="web", commit=True)
        return _serialize(room, names)
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
        # v4b7: 0 clears the alternate; otherwise it must exist, be active, and differ from
        # the room's own type (also enforced by ck_rooms_alt_differs at the DB level).
        if "alt_room_type_id" in update_data:
            alt = update_data["alt_room_type_id"]
            if not alt:
                update_data["alt_room_type_id"] = None
            else:
                alt_rt = db.query(RoomType).filter(RoomType.room_type_id == alt).first()
                if not alt_rt:
                    raise HTTPException(status_code=400, detail="Alternate room type not found")
                if not alt_rt.is_active:
                    raise HTTPException(status_code=400, detail="Alternate room type is inactive")
                own = update_data.get("room_type_id", room.room_type_id)
                if alt == own:
                    raise HTTPException(
                        status_code=400,
                        detail="The alternate type must differ from the room's own type")

        for key, value in update_data.items():
            setattr(room, key, value)
        db.commit()
        db.refresh(room)
        names = _type_names(db)
        write_audit(db, None, "room.update", "room", room.room_id, before=before, after=_serialize(room, names), client="web", commit=True)
        return _serialize(room, names)
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
