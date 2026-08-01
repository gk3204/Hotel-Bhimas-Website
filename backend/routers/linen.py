"""Linen / laundry endpoints (FE-9).

A hotel-wide stage-count pool per linen/amenity item type. Launderable items cycle
clean -> (checkout) dirty -> (send) at_laundry -> (receive) clean; consumables use only
`clean` as stock (issue/replenish). Stage arithmetic + the append-only movement log live in
`services/linen_service.py`; the checkout auto-move is called from `routers/reception.check_out`.

Reads are reception-or-admin; catalog/config writes are admin. Web-only (no desk surface).
"""
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database import SessionLocal
from models import LinenItem, LinenSet, LinenMovement, RoomType
from schemas import (LinenItemCreate, LinenItemUpdate, LinenMoveRequest, LinenSetUpdate)
from services import linen_service
from utils.audit import _resolve_user_id, write_audit
from utils.auth_utils import require_admin, require_reception_or_admin

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/linen", tags=["Linen"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("/health", dependencies=[Depends(require_reception_or_admin)])
def health():
    return {"status": "ok", "module": "linen"}


# ---------------------------------------------------------------- helpers

def _get_item(db: Session, item_id: int) -> LinenItem:
    it = db.query(LinenItem).filter(LinenItem.id == item_id).first()
    if not it:
        raise HTTPException(status_code=404, detail="Linen item not found")
    return it


def _item_dict(it: LinenItem) -> dict:
    clean = float(it.clean or 0)
    return {
        "id": it.id, "name": it.name, "unit": it.unit, "launderable": it.launderable,
        "reorder_threshold": float(it.reorder_threshold or 0),
        "clean": clean, "dirty": float(it.dirty or 0), "at_laundry": float(it.at_laundry or 0),
        "is_active": it.is_active, "notes": it.notes,
        "low": clean <= float(it.reorder_threshold or 0),
    }


# ---------------------------------------------------------------- board + catalog

@router.get("/board")
def board(db: Session = Depends(get_db), user=Depends(require_reception_or_admin)):
    """All active items with their stage counts (the laundry board)."""
    items = db.query(LinenItem).filter(LinenItem.is_active == True).order_by(  # noqa: E712
        LinenItem.launderable.desc(), LinenItem.name).all()
    return {"items": [_item_dict(i) for i in items]}


@router.get("/items")
def list_items(db: Session = Depends(get_db), user=Depends(require_reception_or_admin)):
    items = db.query(LinenItem).order_by(LinenItem.is_active.desc(), LinenItem.name).all()
    return {"items": [_item_dict(i) for i in items]}


@router.post("/items", dependencies=[Depends(require_admin)])
def create_item(data: LinenItemCreate, db: Session = Depends(get_db), user=Depends(require_admin)):
    it = LinenItem(
        name=data.name.strip(), unit=(data.unit or "pcs").strip(), launderable=data.launderable,
        reorder_threshold=data.reorder_threshold, clean=data.opening_clean,
        notes=data.notes, created_by=_resolve_user_id(db, user),
    )
    db.add(it)
    db.flush()
    if float(data.opening_clean or 0) > 0:
        linen_service.record_move(db, it, None, "clean", data.opening_clean,
                                  reason="opening stock", user=user)
    db.commit()
    db.refresh(it)
    write_audit(db, user, "linen.item_create", "linen_item", it.id,
                after={"name": it.name, "launderable": it.launderable}, client="web", commit=True)
    return _item_dict(it)


@router.put("/items/{item_id}", dependencies=[Depends(require_admin)])
def update_item(item_id: int, data: LinenItemUpdate, db: Session = Depends(get_db), user=Depends(require_admin)):
    it = _get_item(db, item_id)
    changes = data.model_dump(exclude_unset=True)
    for k, v in changes.items():
        setattr(it, k, v.strip() if isinstance(v, str) else v)
    it.updated_by = _resolve_user_id(db, user)
    it.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(it)
    write_audit(db, user, "linen.item_update", "linen_item", it.id, after=changes, client="web", commit=True)
    return _item_dict(it)


@router.delete("/items/{item_id}", dependencies=[Depends(require_admin)])
def deactivate_item(item_id: int, db: Session = Depends(get_db), user=Depends(require_admin)):
    """Deactivate (never hard-delete — movement history references it)."""
    it = _get_item(db, item_id)
    it.is_active = False
    it.updated_by = _resolve_user_id(db, user)
    it.updated_at = datetime.utcnow()
    db.commit()
    write_audit(db, user, "linen.item_deactivate", "linen_item", it.id, client="web", commit=True)
    return {"id": it.id, "is_active": False}


# ---------------------------------------------------------------- stage moves

def _move(db, user, item_id, from_stage, to_stage, data: LinenMoveRequest, action: str):
    it = _get_item(db, item_id)
    if from_stage in ("dirty", "at_laundry") and float(data.qty) > float(getattr(it, from_stage) or 0):
        raise HTTPException(status_code=400,
                            detail=f"Only {float(getattr(it, from_stage) or 0):g} {it.unit} in {from_stage}")
    linen_service.record_move(db, it, from_stage, to_stage, data.qty, reason=data.reason or action, user=user)
    db.commit()
    db.refresh(it)
    write_audit(db, user, f"linen.{action}", "linen_item", it.id,
                after={"qty": data.qty, "from": from_stage, "to": to_stage}, client="web", commit=True)
    return _item_dict(it)


@router.post("/items/{item_id}/send-laundry", dependencies=[Depends(require_admin)])
def send_laundry(item_id: int, data: LinenMoveRequest, db: Session = Depends(get_db), user=Depends(require_admin)):
    return _move(db, user, item_id, "dirty", "at_laundry", data, "send_laundry")


@router.post("/items/{item_id}/receive", dependencies=[Depends(require_admin)])
def receive_laundry(item_id: int, data: LinenMoveRequest, db: Session = Depends(get_db), user=Depends(require_admin)):
    return _move(db, user, item_id, "at_laundry", "clean", data, "receive")


@router.post("/items/{item_id}/replenish", dependencies=[Depends(require_admin)])
def replenish(item_id: int, data: LinenMoveRequest, db: Session = Depends(get_db), user=Depends(require_admin)):
    """New stock in (purchase / correction up) → clean."""
    return _move(db, user, item_id, None, "clean", data, "replenish")


@router.post("/items/{item_id}/issue", dependencies=[Depends(require_reception_or_admin)])
def issue(item_id: int, data: LinenMoveRequest, db: Session = Depends(get_db), user=Depends(require_reception_or_admin)):
    """Issue/consume from clean (guest extra or consumable used) → 'out' (track-only, no charge)."""
    it = _get_item(db, item_id)
    if float(data.qty) > float(it.clean or 0):
        raise HTTPException(status_code=400, detail=f"Only {float(it.clean or 0):g} {it.unit} in stock")
    return _move(db, user, item_id, "clean", "out", data, "issue")


# ---------------------------------------------------------------- room-type default sets

@router.get("/sets")
def list_sets(db: Session = Depends(get_db), user=Depends(require_reception_or_admin)):
    """Every room type's default linen set, keyed by room_type_id."""
    types = db.query(RoomType).filter(RoomType.is_active == True).order_by(RoomType.name).all()  # noqa: E712
    rows = db.query(LinenSet).all()
    by_type: dict = {}
    for s in rows:
        by_type.setdefault(s.room_type_id, []).append({"item_id": s.item_id, "qty": float(s.qty or 0)})
    return {"room_types": [{"room_type_id": t.room_type_id, "name": t.name,
                            "items": by_type.get(t.room_type_id, [])} for t in types]}


@router.put("/sets/{room_type_id}", dependencies=[Depends(require_admin)])
def set_room_type_set(room_type_id: int, data: LinenSetUpdate, db: Session = Depends(get_db),
                      user=Depends(require_admin)):
    """Replace a room type's default linen set (rows with qty 0 are dropped)."""
    if not db.query(RoomType).filter(RoomType.room_type_id == room_type_id).first():
        raise HTTPException(status_code=404, detail="Room type not found")
    db.query(LinenSet).filter(LinenSet.room_type_id == room_type_id).delete()
    kept = 0
    for row in data.items:
        if float(row.qty) <= 0:
            continue
        if not db.query(LinenItem).filter(LinenItem.id == row.item_id).first():
            continue
        db.add(LinenSet(room_type_id=room_type_id, item_id=row.item_id, qty=row.qty))
        kept += 1
    db.commit()
    write_audit(db, user, "linen.set_update", "room_type", room_type_id,
                after={"items": kept}, client="web", commit=True)
    return {"room_type_id": room_type_id, "items": kept}


# ---------------------------------------------------------------- movement log

@router.get("/movements")
def list_movements(item_id: int | None = None, limit: int = 100,
                   db: Session = Depends(get_db), user=Depends(require_reception_or_admin)):
    q = db.query(LinenMovement)
    if item_id:
        q = q.filter(LinenMovement.item_id == item_id)
    rows = q.order_by(LinenMovement.created_at.desc()).limit(max(1, min(500, limit))).all()
    return {"movements": [{
        "id": m.id, "item_id": m.item_id, "from_stage": m.from_stage, "to_stage": m.to_stage,
        "qty": float(m.qty or 0), "reason": m.reason, "room_id": m.room_id, "booking_id": m.booking_id,
        "created_at": m.created_at.isoformat() if m.created_at else None,
    } for m in rows]}
