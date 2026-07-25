from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from database import SessionLocal
from models import RoomType, RatePlan, TravelAgent
from schemas import RoomTypeCreate, RoomTypeUpdate, RoomTypeUpdateDetails, \
    RatePlanCreate, RatePlanUpdate
from utils.auth_utils import require_admin, require_reception_or_admin
from utils.audit import write_audit
from utils.rate_engine import quote_stay

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


# ============================================================
# RATE PLANS (prompt 10) — seasonal / weekend / per-channel / per-agent
# overrides for a room type. Admin-managed; read by the shared resolver.
# ============================================================

def _serialize_plan(p: RatePlan) -> dict:
    return {
        "id": p.id,
        "room_type_id": p.room_type_id,
        "channel": p.channel,
        "agent_id": p.agent_id,
        "agent_name": p.agent.name if p.agent else None,
        "price": float(p.price),
        "valid_from": p.valid_from,
        "valid_to": p.valid_to,
        "days_of_week": p.days_of_week,
        "priority": p.priority,
        "is_active": p.is_active,
    }


def _require_room_type(db, room_type_id):
    rt = db.query(RoomType).filter(RoomType.room_type_id == room_type_id).first()
    if not rt:
        raise HTTPException(status_code=404, detail="Room type not found")
    return rt


@router.get("/{room_type_id}/rate-plans", dependencies=[Depends(require_reception_or_admin)])
def list_rate_plans(room_type_id: int, db: Session = Depends(get_db)):
    _require_room_type(db, room_type_id)
    plans = db.query(RatePlan).filter(RatePlan.room_type_id == room_type_id) \
        .order_by(RatePlan.channel, RatePlan.priority.desc(), RatePlan.id).all()
    return [_serialize_plan(p) for p in plans]


@router.post("/{room_type_id}/rate-plans", dependencies=[Depends(require_admin)])
def create_rate_plan(room_type_id: int, data: RatePlanCreate, user=Depends(require_admin),
                     db: Session = Depends(get_db)):
    _require_room_type(db, room_type_id)
    if data.channel == "agent" and not data.agent_id:
        raise HTTPException(status_code=400, detail="agent_id is required for an agent-channel rate plan")
    if data.agent_id and not db.query(TravelAgent).filter(TravelAgent.id == data.agent_id).first():
        raise HTTPException(status_code=400, detail="Travel agent not found")
    plan = RatePlan(
        room_type_id=room_type_id,
        channel=data.channel,
        agent_id=data.agent_id,
        price=data.price,
        valid_from=data.valid_from,
        valid_to=data.valid_to,
        days_of_week=data.days_of_week,
        priority=data.priority,
        is_active=data.is_active,
    )
    db.add(plan)
    db.commit()
    db.refresh(plan)
    write_audit(db, user, "rate_plan.create", "rate_plan", plan.id,
                after=_serialize_plan(plan), client="web", commit=True)
    return _serialize_plan(plan)


@router.put("/rate-plans/{plan_id}", dependencies=[Depends(require_admin)])
def update_rate_plan(plan_id: int, data: RatePlanUpdate, user=Depends(require_admin),
                     db: Session = Depends(get_db)):
    plan = db.query(RatePlan).filter(RatePlan.id == plan_id).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Rate plan not found")
    update = data.dict(exclude_unset=True)
    if update.get("agent_id") and not db.query(TravelAgent).filter(TravelAgent.id == update["agent_id"]).first():
        raise HTTPException(status_code=400, detail="Travel agent not found")
    for key, value in update.items():
        setattr(plan, key, value)
    if plan.channel == "agent" and not plan.agent_id:
        raise HTTPException(status_code=400, detail="agent_id is required for an agent-channel rate plan")
    db.commit()
    db.refresh(plan)
    write_audit(db, user, "rate_plan.update", "rate_plan", plan.id,
                after=_serialize_plan(plan), client="web", commit=True)
    return _serialize_plan(plan)


@router.delete("/rate-plans/{plan_id}", dependencies=[Depends(require_admin)])
def delete_rate_plan(plan_id: int, user=Depends(require_admin), db: Session = Depends(get_db)):
    plan = db.query(RatePlan).filter(RatePlan.id == plan_id).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Rate plan not found")
    db.delete(plan)
    db.commit()
    write_audit(db, user, "rate_plan.delete", "rate_plan", plan_id, client="web", commit=True)
    return {"message": "Rate plan deleted", "id": plan_id}


@router.get("/{room_type_id}/quote", dependencies=[Depends(require_reception_or_admin)])
def quote_rate(room_type_id: int,
               check_in: str = Query(..., description="YYYY-MM-DD"),
               check_out: str = Query(..., description="YYYY-MM-DD"),
               channel: str = Query("walk_in"),
               agent_id: int | None = Query(None),
               quantity: int = Query(1, ge=1, le=20),
               db: Session = Depends(get_db)):
    """Resolved GST-exclusive room base for a stay (per-night breakdown). Used by the
    admin rate grid + the desktop agent-rate preview. Reads the same resolver the
    desk booking uses, so the preview matches the booked price."""
    rt = _require_room_type(db, room_type_id)
    try:
        ci = datetime.strptime(check_in, "%Y-%m-%d").date()
        co = datetime.strptime(check_out, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD")
    if co <= ci:
        raise HTTPException(status_code=400, detail="Check-out must be after check-in")
    if channel not in ("walk_in", "website", "agent"):
        raise HTTPException(status_code=400, detail="channel must be walk_in|website|agent")
    q = quote_stay(db, rt, ci, co, channel=channel, agent_id=agent_id, quantity=quantity)
    return {
        "room_type_id": room_type_id,
        "room_type_name": rt.name,
        "gst_percent": float(rt.gst_percent),
        "channel": channel,
        "agent_id": agent_id,
        **q,
    }



