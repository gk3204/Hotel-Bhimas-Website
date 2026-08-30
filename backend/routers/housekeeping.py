"""Housekeeping endpoints (prompt 13): room cleaning status, cleaning tasks, minibar
restock, and the 'inspected' re-sale gate.
v4b8: the separate `supervisor` role is gone — `housekeeper` both cleans and inspects.

Anti-fraud design:
- Cleaning status is set ONLY by the HOUSEKEEPER role (reception has no route here) so
  reception can't "park a room in cleaning" to hide it. The desktop shows housekeeping
  status READ-ONLY via GET /reception/board.
- A room becomes re-sellable only after a housekeeper/admin marks it INSPECTED
  (POST /housekeeping/rooms/{id}/inspect), which flips the coarse Room.status back to
  'vacant'. A merely-cleaned room stays Room.status='cleaning' (not bookable). The
  `housekeeping_auto_inspect` config (admin, app_settings) makes 'mark clean' also inspect.
- Room.status_changed_at is bumped on every coarse transition so the prompt-11
  cleaning_too_long fraud detector stays accurate.

Skeleton created in Milestone 0 (prompt 01); implemented here in prompt 13.
"""
import json
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from database import SessionLocal
from models import (Booking, BookingItem, Folio, FolioCharge, HousekeepingStatus,
                    HousekeepingTask, Room, User)
from schemas import (HousekeepingConfigUpdate, HousekeepingStatusUpdate, InspectRequest,
                     MinibarRestockRequest, TaskCompleteRequest)
from utils.auth_utils import (require_admin, require_housekeeper_or_admin,
                              require_supervisor_or_admin, require_roles)

# Housekeeping board is readable by the housekeeper who works it, plus admin.
_board_viewer = require_roles("admin", "housekeeper")
from utils.audit import write_audit, _resolve_user_id
from utils.housekeeping import set_hk_status
from utils.settings import (HK_AUTO_INSPECT_KEY, get_housekeeping_config, set_setting,
                            validate_category)
from routers.folio import _recompute

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/housekeeping", tags=["Housekeeping"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("/health", dependencies=[Depends(require_housekeeper_or_admin)])
def health():
    return {"status": "ok", "module": "housekeeping"}


# ---------------------------------------------------------------- helpers

def _staff_name(db: Session, user_id):
    if not user_id:
        return None
    u = db.query(User).filter(User.user_id == user_id).first()
    return (u.full_name or u.username) if u else None


def _task_dict(db: Session, t: HousekeepingTask, room: Room = None) -> dict:
    room = room or db.query(Room).filter(Room.room_id == t.room_id).first()
    return {
        "id": t.id,
        "room_id": t.room_id,
        "room_number": room.room_number if room else None,
        "type": t.type,
        "status": t.status,
        "assigned_to": t.assigned_to,
        "assigned_to_name": _staff_name(db, t.assigned_to),
        "booking_id": t.booking_id,
        "checklist": json.loads(t.checklist) if t.checklist else None,
        "photo_url": t.photo_url,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "started_at": t.started_at.isoformat() if t.started_at else None,
        "done_at": t.done_at.isoformat() if t.done_at else None,
    }


def _is_cleaning_record(t: HousekeepingTask) -> bool:
    """True when this task carries a record of somebody actually working the room.

    Deliberately NOT `done_at is not None`: inspect_room closes every open task for the room,
    stamping done_at on tasks nobody ever touched. Only a picked cleaner name, an assignee or
    a start time means real work.
    """
    return bool(t.cleaned_by_name) or t.assigned_to is not None or t.started_at is not None


def _cleaning_record(tasks) -> HousekeepingTask:
    """The task that answers 'who cleaned this room', from a newest-first list.

    A room can hold a NEWER task that nobody has worked yet — a touch-up raised minutes after
    the checkout clean was finished. Taking the newest task of any status would let that empty
    row shadow the completed clean and report the room as cleaned by nobody, so prefer the
    newest worked task and fall back to the newest task only when none has been worked.
    """
    for t in tasks:
        if _is_cleaning_record(t):
            return t
    return tasks[0] if tasks else None


def _get_task(db: Session, task_id: int) -> HousekeepingTask:
    t = db.query(HousekeepingTask).filter(HousekeepingTask.id == task_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="Task not found")
    return t


# ---------------------------------------------------------------- rooms + status

@router.get("/rooms")
def list_rooms(mine: bool = Query(False), db: Session = Depends(get_db),
               user=Depends(_board_viewer)):
    """All active rooms + housekeeping status + the room's open cleaning task (if any).
    `mine=true` limits to rooms whose open task is unassigned or assigned to the caller
    (the housekeeper 'my rooms' view)."""
    me = _resolve_user_id(db, user)
    rows = db.query(Room, HousekeepingStatus).outerjoin(
        HousekeepingStatus, Room.room_id == HousekeepingStatus.room_id,
    ).filter(Room.is_active == True).order_by(Room.room_number).all()  # noqa: E712

    # open task per room (pending/in_progress) + most-recent task per room (any status,
    # for the 'cleaned by' name), both newest-first / first-wins.
    open_tasks, last_tasks, worked_tasks = {}, {}, {}
    for t in db.query(HousekeepingTask).order_by(HousekeepingTask.created_at.desc()).all():
        last_tasks.setdefault(t.room_id, t)
        if _is_cleaning_record(t):
            worked_tasks.setdefault(t.room_id, t)   # see _cleaning_record()
        if t.status in ("pending", "in_progress"):
            open_tasks.setdefault(t.room_id, t)

    name_cache = {}
    def _name(uid):
        if uid not in name_cache:
            name_cache[uid] = _staff_name(db, uid)
        return name_cache[uid]

    out = []
    for r, hk in rows:
        task = open_tasks.get(r.room_id)
        if mine and task is not None and task.assigned_to not in (None, me):
            continue
        last = worked_tasks.get(r.room_id) or last_tasks.get(r.room_id)
        out.append({
            "room_id": r.room_id,
            "room_number": r.room_number,
            "room_status": r.status,
            "housekeeping_status": hk.status if hk else None,
            "updated_at": hk.updated_at.isoformat() if hk and hk.updated_at else None,
            "photo_url": hk.photo_url if hk else None,
            "open_task": _task_dict(db, task, r) if task else None,
            "cleaned_by": _name(last.assigned_to) if last and last.assigned_to else None,  # ALT-7
            # v4b8: the PICKED names, shown as their own columns on the housekeeping board.
            "cleaned_by_name": last.cleaned_by_name if last else None,
            "inspected_by_name": last.inspected_by_name if last else None,
        })
    return {"rooms": out, "config": get_housekeeping_config(db)}


@router.get("/tasks")
def list_tasks(status: str = Query(None), mine: bool = Query(False),
               db: Session = Depends(get_db), user=Depends(_board_viewer)):
    """Cleaning tasks (filter by status; `mine=true` = unassigned or mine)."""
    me = _resolve_user_id(db, user)
    q = db.query(HousekeepingTask)
    if status:
        q = q.filter(HousekeepingTask.status == status)
    if mine:
        q = q.filter((HousekeepingTask.assigned_to == me) | (HousekeepingTask.assigned_to.is_(None)))
    tasks = q.order_by(HousekeepingTask.created_at.desc()).all()
    return {"tasks": [_task_dict(db, t) for t in tasks]}


@router.post("/tasks/{task_id}/start")
def start_task(task_id: int, db: Session = Depends(get_db),
               user=Depends(require_housekeeper_or_admin)):
    """Housekeeper starts cleaning: task -> in_progress, room -> cleaning, claim if unassigned."""
    t = _get_task(db, task_id)
    if t.status == "done":
        raise HTTPException(status_code=409, detail="Task is already done")
    me = _resolve_user_id(db, user)
    t.status = "in_progress"
    if t.started_at is None:
        t.started_at = datetime.utcnow()
    if t.assigned_to is None:
        t.assigned_to = me  # pooled -> claim on start
    room = db.query(Room).filter(Room.room_id == t.room_id).first()
    if room and room.status not in ("occupied",):
        if room.status != "cleaning":
            room.status = "cleaning"
            room.status_changed_at = datetime.utcnow()
    set_hk_status(db, t.room_id, "cleaning", user=user)
    db.commit()
    write_audit(db, user, "housekeeping.task_start", "housekeeping_task", t.id,
                after={"room_id": t.room_id}, client="housekeeper", commit=True)
    return _task_dict(db, t, room)


@router.post("/tasks/{task_id}/complete")
def complete_task(task_id: int, data: TaskCompleteRequest, db: Session = Depends(get_db),
                  user=Depends(require_housekeeper_or_admin)):
    """Mark a room clean. Room stays 'cleaning' (awaiting inspection) unless the
    `housekeeping_auto_inspect` config is on, in which case it is also inspected
    (Room.status -> vacant, re-sellable). Idempotent: re-completing a done task no-ops."""
    t = _get_task(db, task_id)
    room = db.query(Room).filter(Room.room_id == t.room_id).first()
    if t.status == "done":
        return {**_task_dict(db, t, room), "duplicate": True}  # idempotent

    photo = data.photo_ref
    t.status = "done"
    t.done_at = datetime.utcnow()
    if t.assigned_to is None:
        t.assigned_to = _resolve_user_id(db, user)
    if data.checklist is not None:
        t.checklist = json.dumps(data.checklist, default=str)
    if photo is not None:
        t.photo_url = photo
    if data.client_ref:
        t.client_ref = data.client_ref
    # v4b8: the NAME the housekeeper picked, validated against the admin-editable list.
    # Kept alongside `assigned_to` (the login), not instead of it.
    if data.cleaned_by_name:
        t.cleaned_by_name = validate_category(db, "cleaned_by", data.cleaned_by_name)

    set_hk_status(db, t.room_id, "clean", user=user, photo_url=photo)

    auto = get_housekeeping_config(db)["auto_inspect"]
    inspected = False
    if auto and room is not None:
        set_hk_status(db, t.room_id, "inspected", user=user)
        if room.status == "cleaning":
            room.status = "vacant"
            room.status_changed_at = datetime.utcnow()
        inspected = True

    db.commit()
    write_audit(db, user, "housekeeping.task_complete", "housekeeping_task", t.id,
                after={"room_id": t.room_id, "auto_inspected": inspected}, client="housekeeper", commit=True)
    return {**_task_dict(db, t, room), "auto_inspected": inspected}


@router.post("/rooms/{room_id}/status")
def set_room_status(room_id: int, data: HousekeepingStatusUpdate, db: Session = Depends(get_db),
                    user=Depends(require_housekeeper_or_admin)):
    """Housekeeper sets a room's cleaning state directly (dirty|cleaning|clean|maintenance|dnd).
    Cannot set 'inspected' (admin gate) or 'vacant' (re-sale). Reception has no access."""
    room = db.query(Room).filter(Room.room_id == room_id).first()
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")
    set_hk_status(db, room_id, data.status, user=user, photo_url=data.photo_ref)

    # reflect coarse Room.status where it is safe (never touch an occupied room's coarse
    # status except to flag maintenance; never set vacant/occupied here).
    before = room.status
    if data.status == "maintenance" and room.status != "occupied":
        room.status = "maintenance"
    elif data.status in ("dirty", "cleaning", "clean") and room.status not in ("occupied", "maintenance", "blocked"):
        room.status = "cleaning"
    if room.status != before:
        room.status_changed_at = datetime.utcnow()

    db.commit()
    write_audit(db, user, "housekeeping.set_status", "room", room_id,
                before={"room_status": before},
                after={"housekeeping_status": data.status, "room_status": room.status},
                client="housekeeper", commit=True)
    return {"room_id": room_id, "housekeeping_status": data.status, "room_status": room.status}


@router.post("/rooms/{room_id}/inspect")
def inspect_room(room_id: int, data: InspectRequest, db: Session = Depends(get_db),
                 user=Depends(require_supervisor_or_admin)):
    """Supervisor/admin marks a cleaned room INSPECTED -> re-sellable (Room.status=vacant).
    This is the anti-fraud gate: reception/housekeepers cannot make a room re-sellable."""
    room = db.query(Room).filter(Room.room_id == room_id).first()
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")
    if room.status == "occupied":
        raise HTTPException(status_code=409, detail="Cannot inspect an occupied room")

    # cleaned-by: the housekeeper on the room's most recent cleaning task (ALT-7). Captured
    # before we close tasks so the inspection records who actually cleaned the room.
    last_task = _cleaning_record(
        db.query(HousekeepingTask)
        .filter(HousekeepingTask.room_id == room_id)
        .order_by(HousekeepingTask.created_at.desc()).all())
    cleaned_by_id = last_task.assigned_to if last_task else None
    cleaned_by = _staff_name(db, cleaned_by_id)
    supervisor = _staff_name(db, _resolve_user_id(db, user))
    # v4b8: the PICKED names, validated against the admin-editable lists. These are a parallel
    # record to the login-derived ones above — "whose name goes on the sheet" vs "which login
    # did it" — so both are kept and neither overwrites the other.
    cleaned_by_name = (validate_category(db, "cleaned_by", data.cleaned_by_name)
                       if data.cleaned_by_name else
                       (last_task.cleaned_by_name if last_task else None))
    inspected_by_name = (validate_category(db, "inspected_by", data.inspected_by_name)
                         if data.inspected_by_name else None)

    set_hk_status(db, room_id, "inspected", user=user)
    before = room.status
    if room.status in ("cleaning", "inspected", "maintenance", "blocked"):
        room.status = "vacant"
        room.status_changed_at = datetime.utcnow()

    # close out any open cleaning tasks for the room
    for t in db.query(HousekeepingTask).filter(
            HousekeepingTask.room_id == room_id,
            HousekeepingTask.status.in_(["pending", "in_progress"])).all():
        t.status = "done"
        if t.done_at is None:
            t.done_at = datetime.utcnow()

    # Stamp the sign-off onto the room's most recent task, so the board can show it.
    if last_task is not None:
        if cleaned_by_name:
            last_task.cleaned_by_name = cleaned_by_name
        last_task.inspected_by_name = inspected_by_name
        last_task.inspected_at = datetime.utcnow()

    db.commit()
    write_audit(db, user, "housekeeping.inspect", "room", room_id,
                before={"room_status": before},
                after={"room_status": room.status, "note": data.note,
                       "cleaned_by": cleaned_by, "cleaned_by_id": cleaned_by_id,
                       "supervisor": supervisor,
                       "cleaned_by_name": cleaned_by_name,
                       "inspected_by_name": inspected_by_name},
                client="web", commit=True)
    return {"room_id": room_id, "housekeeping_status": "inspected", "room_status": room.status,
            "cleaned_by": cleaned_by, "supervisor": supervisor,
            "cleaned_by_name": cleaned_by_name, "inspected_by_name": inspected_by_name,
            "note": data.note}


# ---------------------------------------------------------------- minibar -> folio

@router.post("/minibar-restock")
def minibar_restock(data: MinibarRestockRequest, db: Session = Depends(get_db),
                    user=Depends(require_housekeeper_or_admin)):
    """Post a minibar consumption charge to the in-house guest's open folio."""
    item = db.query(BookingItem).join(Booking, BookingItem.booking_id == Booking.booking_id).filter(
        BookingItem.room_id == data.room_id,
        Booking.status == "checked_in",
    ).order_by(Booking.checked_in_at.desc()).first()
    if not item:
        raise HTTPException(status_code=409, detail="No in-house guest in this room")
    folio = db.query(Folio).filter(Folio.booking_id == item.booking_id).first()
    if not folio:
        raise HTTPException(status_code=409, detail="No folio for this stay")
    if folio.status != "open":
        raise HTTPException(status_code=409, detail="Folio is settled — cannot post a charge")
    # v4b6: a fully complimentary stay is charged nothing. The stock ledger below still moves —
    # the bottle physically left the fridge whether or not anyone paid for it.
    booking = db.query(Booking).filter(Booking.booking_id == item.booking_id).first()
    comped = booking is not None and getattr(booking, "comp_mode", "none") == "all"

    if data.client_ref:
        marker = f"[{data.client_ref}] {data.description}"
        dup = db.query(FolioCharge).filter(FolioCharge.folio_id == folio.id,
                                           FolioCharge.type == "minibar",
                                           FolioCharge.description == marker).first()
        if dup:
            return {"charge_id": dup.id, "duplicate": True, "folio_id": folio.id,
                    "balance": float(folio.balance or 0)}

    amount = 0.0 if comped else round(data.qty * data.unit_price, 2)
    desc = f"[{data.client_ref}] {data.description}" if data.client_ref else data.description
    charge = None
    if not comped:
        charge = FolioCharge(
            folio_id=folio.id, type="minibar", description=desc,
            qty=data.qty, unit_price=data.unit_price, amount=amount,
            gst_percent=data.gst_percent, posted_by=_resolve_user_id(db, user),
        )
        db.add(charge)
        _recompute(db, folio)   # flushes, so charge.id is available for the movement link below

    # Inventory (prompt 18c slice 8): if this minibar line names a stock item, record the
    # consumption on the stock ledger so the item's quantity decrements. Best-effort and
    # non-blocking — an unmatched description (or no inventory at all) just skips it.
    stock_item_id = None
    try:
        from routers.stock import record_movement
        from models import StockItem
        name = (data.description or "").strip()
        stock_item = (db.query(StockItem).filter(
            StockItem.is_active == True, StockItem.name.ilike(name)).first()   # noqa: E712
            if name else None)
        if stock_item is not None:
            mv = record_movement(db, stock_item, "consume", -abs(data.qty), user=user,
                                 reason=f"Minibar — room {data.room_id}",
                                 folio_charge_id=(charge.id if charge else None),
                                 client_ref=(f"minibar_consume:{charge.id}" if charge
                                             else f"minibar_comp:{item.booking_id}:{data.room_id}:"
                                                  f"{(data.description or '').strip()}"),
                                 commit=False)
            stock_item_id = stock_item.id
    except Exception as e:  # inventory is a soft add-on; never fail a folio post over it
        logger.warning(f"minibar stock consume skipped: {e}")

    db.commit()
    write_audit(db, user, "housekeeping.minibar_restock", "folio", folio.id,
                after={"charge_id": (charge.id if charge else None), "room_id": data.room_id,
                       "amount": amount, "complimentary": comped,
                       "description": data.description, "stock_item_id": stock_item_id},
                client="housekeeper", commit=True)
    return {"charge_id": (charge.id if charge else None), "duplicate": False,
            "folio_id": folio.id, "complimentary": comped,
            "amount": amount, "balance": float(folio.balance or 0),
            "stock_item_id": stock_item_id}


# ---------------------------------------------------------------- config

@router.get("/config")
def get_config(db: Session = Depends(get_db), user=Depends(require_housekeeper_or_admin)):
    return get_housekeeping_config(db)


@router.put("/config")
def update_config(data: HousekeepingConfigUpdate, db: Session = Depends(get_db),
                  user=Depends(require_admin)):
    """Admin toggles auto-inspect."""
    set_setting(db, HK_AUTO_INSPECT_KEY, "true" if data.auto_inspect else "false", user=user, commit=True)
    write_audit(db, user, "housekeeping.config", "app_settings", HK_AUTO_INSPECT_KEY,
                after={"auto_inspect": data.auto_inspect}, client="web", commit=True)
    return get_housekeeping_config(db)
