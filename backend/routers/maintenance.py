"""Maintenance ticket endpoints (prompt 13).

Tickets are raisable by housekeeping/reception/admin (and guests via WhatsApp in prompt 15;
room_id is optional so common areas work). An admin/supervisor assigns a ticket to a
`maintenance` user (electrician/plumber/AC tech...), who tracks it
in_progress -> awaiting_parts -> resolved and lists the required parts (ticket_items).
A ticket only reaches 'verified' (its terminal/closed state) after supervisor verification.
Admin approves items; a purchased item can post to `expenses` (prompt 12) so parts spend
hits the shift ledger + P&L.

Notifications (assignee) are an interim logger.info — WhatsApp delivery lands in prompt 15
behind the same call, matching the owner_otp / fraud-alert convention.
"""
import logging
from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from database import SessionLocal
from models import Expense, MaintenanceTicket, Room, TicketItem, User
from schemas import (MaintenanceTicketCreate, TicketAssignRequest, TicketItemCreate,
                     TicketItemPurchase, TicketStatusUpdate, TicketVerifyRequest)
from utils.auth_utils import (get_current_user, require_admin, require_maintenance_or_admin,
                              require_supervisor_or_admin, require_roles)
from utils.audit import write_audit, _resolve_user_id
from utils.settings import validate_category
from routers.cash_shift import _open_shift

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/maintenance", tags=["Maintenance"])

# Interim overdue thresholds (days open) by priority; WhatsApp escalation = prompt 15.
OVERDUE_DAYS = {"urgent": 1, "high": 2, "normal": 4, "low": 7}
# A ticket is "closed" (no more work) once verified.
TERMINAL_STATUSES = ("verified", "closed")
# role -> ticket source (source vocab: guest|reception|housekeeping|admin)
_ROLE_SOURCE = {"admin": "admin", "reception": "reception",
                "housekeeper": "housekeeping", "maintenance": "housekeeping"}


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------------------------------------------------------------- helpers

def _staff_name(db: Session, user_id):
    if not user_id:
        return None
    u = db.query(User).filter(User.user_id == user_id).first()
    return (u.full_name or u.username) if u else None


def _age_days(t: MaintenanceTicket) -> int:
    if not t.created_at:
        return 0
    return max(0, (datetime.utcnow() - t.created_at).days)


def _is_overdue(t: MaintenanceTicket) -> bool:
    if t.status in TERMINAL_STATUSES or t.status == "resolved":
        return False
    return _age_days(t) > OVERDUE_DAYS.get(t.priority, 4)


def _item_dict(db: Session, i: TicketItem) -> dict:
    return {
        "id": i.id, "ticket_id": i.ticket_id, "item": i.item,
        "qty": float(i.qty or 0), "est_cost": float(i.est_cost) if i.est_cost is not None else None,
        "status": i.status, "expense_id": i.expense_id,
        "added_by": i.added_by, "added_by_name": _staff_name(db, i.added_by),
        "added_at": i.added_at.isoformat() if i.added_at else None,
    }


def _ticket_dict(db: Session, t: MaintenanceTicket, with_items=True) -> dict:
    room = db.query(Room).filter(Room.room_id == t.room_id).first() if t.room_id else None
    d = {
        "id": t.id, "room_id": t.room_id,
        "room_number": room.room_number if room else None,
        "area": t.area, "category": t.category, "issue": t.issue,
        "priority": t.priority, "status": t.status, "source": t.source,
        "booking_id": t.booking_id, "photo_url": t.photo_url,
        "raised_by": t.raised_by, "raised_by_name": _staff_name(db, t.raised_by),
        "assignee": t.assignee, "assignee_name": _staff_name(db, t.assignee),
        "resolution_notes": t.resolution_notes,
        "verified_by": t.verified_by, "verified_by_name": _staff_name(db, t.verified_by),
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "assigned_at": t.assigned_at.isoformat() if t.assigned_at else None,
        "resolved_at": t.resolved_at.isoformat() if t.resolved_at else None,
        "verified_at": t.verified_at.isoformat() if t.verified_at else None,
        "age_days": _age_days(t), "overdue": _is_overdue(t),
    }
    if with_items:
        items = db.query(TicketItem).filter(TicketItem.ticket_id == t.id).order_by(TicketItem.id).all()
        d["items"] = [_item_dict(db, i) for i in items]
    return d


def _get_ticket(db: Session, ticket_id: int) -> MaintenanceTicket:
    t = db.query(MaintenanceTicket).filter(MaintenanceTicket.id == ticket_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return t


def raise_ac_on_ticket(db: Session, booking_id, room, commit=False):
    """Auto-raise a 'turn on AC' maintenance ticket for an AC room at check-in / shift-into-AC
    (FE-10). source='reception' (NOT 'guest' — that would be a complaint). Idempotent per
    booking+room. Best-effort: callers wrap this so a failure never blocks the stay flow."""
    if room is None:
        return None
    client_ref = f"ac-on-{booking_id}-{room.room_id}"
    dup = db.query(MaintenanceTicket).filter(MaintenanceTicket.client_ref == client_ref).first()
    if dup:
        return dup
    t = MaintenanceTicket(
        room_id=room.room_id, category="appliance",
        issue=f"Turn on AC — Room {room.room_number}",
        priority="high", status="open", booking_id=booking_id,
        source="reception", client_ref=client_ref,
    )
    db.add(t)
    if commit:
        db.commit()
        db.refresh(t)
    else:
        db.flush()
    logger.info(f"❄️ AC-on ticket #{t.id} raised for room {room.room_number} (booking={booking_id})")
    return t


def create_guest_ticket(db: Session, issue: str, booking_id=None, room_id=None,
                        category="other", priority="normal", client_ref=None, commit=True) -> MaintenanceTicket:
    """Programmatically raise a guest complaint ticket (source='guest') — used by the
    WhatsApp inbound webhook (prompt 15), which has no logged-in staff user. Idempotent on
    `client_ref`. There is no HTTP path that sets source='guest', so this is the seam."""
    if client_ref:
        dup = db.query(MaintenanceTicket).filter(MaintenanceTicket.client_ref == client_ref).first()
        if dup:
            return dup
    t = MaintenanceTicket(
        room_id=room_id, category=category, issue=(issue or "Guest complaint")[:500],
        priority=priority, status="open", booking_id=booking_id, source="guest",
        client_ref=client_ref,
    )
    db.add(t)
    if commit:
        db.commit()
        db.refresh(t)
    else:
        db.flush()
    logger.info(f"🔧 Guest complaint ticket #{t.id} raised via WhatsApp (booking={booking_id})")
    return t


# ---------------------------------------------------------------- create / list

@router.post("/tickets")
def create_ticket(data: MaintenanceTicketCreate,
                  db: Session = Depends(get_db),
                  user=Depends(require_roles("admin", "reception", "housekeeper", "maintenance", "supervisor"))):
    """Raise a ticket. Source is derived from the caller's role."""
    if data.client_ref:
        dup = db.query(MaintenanceTicket).filter(MaintenanceTicket.client_ref == data.client_ref).first()
        if dup:
            return {**_ticket_dict(db, dup), "duplicate": True}
    if data.room_id is not None:
        if not db.query(Room).filter(Room.room_id == data.room_id).first():
            raise HTTPException(status_code=404, detail="Room not found")
    category = validate_category(db, "maintenance", data.category)   # against the editable list (F-A)

    t = MaintenanceTicket(
        room_id=data.room_id, area=data.area, category=category, issue=data.issue,
        priority=data.priority, status="open", booking_id=data.booking_id,
        source=_ROLE_SOURCE.get(user.get("role"), "reception"),
        photo_url=data.photo_ref, raised_by=_resolve_user_id(db, user),
        client_ref=data.client_ref,
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    write_audit(db, user, "maintenance.ticket_create", "maintenance_ticket", t.id,
                after={"category": t.category, "priority": t.priority, "room_id": t.room_id,
                       "source": t.source}, client="web", commit=True)
    logger.info(f"🔧 Maintenance ticket #{t.id} raised ({t.category}/{t.priority}) source={t.source}")
    return {**_ticket_dict(db, t), "duplicate": False}


@router.get("/tickets")
def list_tickets(status: str = Query(None), category: str = Query(None),
                 assignee: int = Query(None), overdue: bool = Query(False),
                 db: Session = Depends(get_db),
                 user=Depends(require_roles("admin", "reception", "housekeeper", "maintenance", "supervisor"))):
    """List tickets with filters. A `maintenance` user sees only their own assigned tickets."""
    q = db.query(MaintenanceTicket)
    if user.get("role") == "maintenance":
        q = q.filter(MaintenanceTicket.assignee == _resolve_user_id(db, user))
    if status:
        q = q.filter(MaintenanceTicket.status == status)
    if category:
        q = q.filter(MaintenanceTicket.category == category)
    if assignee is not None:
        q = q.filter(MaintenanceTicket.assignee == assignee)
    tickets = q.order_by(MaintenanceTicket.created_at.desc()).all()
    out = [_ticket_dict(db, t, with_items=False) for t in tickets]
    if overdue:
        out = [d for d in out if d["overdue"]]
    summary = {
        "total": len(out),
        "open": sum(1 for d in out if d["status"] not in TERMINAL_STATUSES),
        "overdue": sum(1 for d in out if d["overdue"]),
    }
    return {"tickets": out, "summary": summary}


@router.get("/tickets/{ticket_id}")
def get_ticket(ticket_id: int, db: Session = Depends(get_db),
               user=Depends(require_roles("admin", "reception", "housekeeper", "maintenance", "supervisor"))):
    t = _get_ticket(db, ticket_id)
    if user.get("role") == "maintenance" and t.assignee != _resolve_user_id(db, user):
        raise HTTPException(status_code=403, detail="Not your ticket")
    return _ticket_dict(db, t)


# ---------------------------------------------------------------- assign / track / verify

@router.post("/tickets/{ticket_id}/assign")
def assign_ticket(ticket_id: int, data: TicketAssignRequest, db: Session = Depends(get_db),
                  user=Depends(require_supervisor_or_admin)):
    """Admin assigns a ticket to a maintenance user (open|assigned -> assigned)."""
    t = _get_ticket(db, ticket_id)
    if t.status in TERMINAL_STATUSES:
        raise HTTPException(status_code=409, detail=f"Ticket is {t.status}")
    assignee = db.query(User).filter(User.user_id == data.assignee_id).first()
    if not assignee:
        raise HTTPException(status_code=404, detail="Assignee not found")
    if assignee.role != "maintenance":
        raise HTTPException(status_code=400, detail="Assignee must be a maintenance user")
    before = {"status": t.status, "assignee": t.assignee}
    t.assignee = assignee.user_id
    t.assigned_at = datetime.utcnow()
    if t.status == "open":
        t.status = "assigned"
    db.commit()
    write_audit(db, user, "maintenance.assign", "maintenance_ticket", t.id,
                before=before, after={"status": t.status, "assignee": t.assignee}, client="web", commit=True)
    logger.info(f"🔔 [notify] Ticket #{t.id} assigned to {assignee.username}")
    # Best-effort WhatsApp to the assignee (prompt 15) — only if they have a phone on file.
    if assignee.phone:
        try:
            from utils import whatsapp_service
            whatsapp_service.send_template(
                db, assignee.phone, "fraud_alert",
                {"alert_type": f"Ticket #{t.id} assigned",
                 "detail": f"{t.category}/{t.priority}: {t.issue[:80]}"},
                client_ref=f"ticket_assign:{t.id}", respect_optout=False)
        except Exception as e:
            logger.warning(f"assignee whatsapp notify failed: {e}")
    return _ticket_dict(db, t)


@router.post("/tickets/{ticket_id}/status")
def update_status(ticket_id: int, data: TicketStatusUpdate, db: Session = Depends(get_db),
                  user=Depends(require_maintenance_or_admin)):
    """Assignee advances the ticket (in_progress|awaiting_parts|resolved). Every transition
    is timestamped + audited. A maintenance user may only update their own ticket, and cannot
    self-close (verification is admin-only)."""
    t = _get_ticket(db, ticket_id)
    me = _resolve_user_id(db, user)
    if user.get("role") == "maintenance" and t.assignee != me:
        raise HTTPException(status_code=403, detail="Not your ticket")
    if t.assignee is None:
        raise HTTPException(status_code=409, detail="Ticket is not assigned yet")
    if t.status in TERMINAL_STATUSES:
        raise HTTPException(status_code=409, detail=f"Ticket is {t.status}")
    before = t.status
    t.status = data.status
    if data.status == "resolved":
        t.resolved_at = datetime.utcnow()
        if data.note:
            t.resolution_notes = data.note
    db.commit()
    write_audit(db, user, "maintenance.status", "maintenance_ticket", t.id,
                before={"status": before}, after={"status": t.status, "note": data.note},
                client="web", commit=True)
    return _ticket_dict(db, t)


@router.post("/tickets/{ticket_id}/verify")
def verify_ticket(ticket_id: int, data: TicketVerifyRequest, db: Session = Depends(get_db),
                  user=Depends(require_supervisor_or_admin)):
    """Supervisor/admin verifies a resolved ticket -> verified (terminal/closed)."""
    t = _get_ticket(db, ticket_id)
    if t.status != "resolved":
        raise HTTPException(status_code=409, detail="Only a resolved ticket can be verified")
    t.status = "verified"
    t.verified_by = _resolve_user_id(db, user)
    t.verified_at = datetime.utcnow()
    if data.resolution_notes:
        t.resolution_notes = data.resolution_notes
    db.commit()
    write_audit(db, user, "maintenance.verify", "maintenance_ticket", t.id,
                after={"status": "verified", "verified_by": t.verified_by}, client="web", commit=True)
    logger.info(f"✅ Ticket #{t.id} verified & closed")
    return _ticket_dict(db, t)


# ---------------------------------------------------------------- required items

@router.post("/tickets/{ticket_id}/items")
def add_item(ticket_id: int, data: TicketItemCreate, db: Session = Depends(get_db),
             user=Depends(require_maintenance_or_admin)):
    """Assignee adds a required part/material to a ticket."""
    t = _get_ticket(db, ticket_id)
    if user.get("role") == "maintenance" and t.assignee != _resolve_user_id(db, user):
        raise HTTPException(status_code=403, detail="Not your ticket")
    it = TicketItem(
        ticket_id=t.id, item=data.item, qty=Decimal(str(data.qty)),
        est_cost=Decimal(str(data.est_cost)) if data.est_cost is not None else None,
        status="needed", added_by=_resolve_user_id(db, user),
    )
    db.add(it)
    db.commit()
    db.refresh(it)
    write_audit(db, user, "maintenance.item_add", "ticket_item", it.id,
                after={"ticket_id": t.id, "item": it.item}, client="web", commit=True)
    return _item_dict(db, it)


@router.post("/items/{item_id}/approve")
def approve_item(item_id: int, db: Session = Depends(get_db), user=Depends(require_admin)):
    """Admin approves a required item (needed -> approved)."""
    it = db.query(TicketItem).filter(TicketItem.id == item_id).first()
    if not it:
        raise HTTPException(status_code=404, detail="Item not found")
    if it.status != "needed":
        raise HTTPException(status_code=409, detail=f"Item is already {it.status}")
    it.status = "approved"
    db.commit()
    write_audit(db, user, "maintenance.item_approve", "ticket_item", it.id, client="web", commit=True)
    return _item_dict(db, it)


@router.post("/items/{item_id}/purchase")
def purchase_item(item_id: int, data: TicketItemPurchase, db: Session = Depends(get_db),
                  user=Depends(require_admin)):
    """Admin logs an approved item as purchased; optionally posts an Expense (prompt 12) so
    the parts spend hits the shift ledger / P&L. Links the item to its expense row."""
    it = db.query(TicketItem).filter(TicketItem.id == item_id).first()
    if not it:
        raise HTTPException(status_code=404, detail="Item not found")
    if it.status not in ("approved", "purchased"):
        raise HTTPException(status_code=409, detail="Only an approved item can be purchased")
    if it.expense_id:
        raise HTTPException(status_code=409, detail="Item already posted to expenses")

    ticket = db.query(MaintenanceTicket).filter(MaintenanceTicket.id == it.ticket_id).first()
    expense_id = None
    if data.post_to_expenses:
        if data.client_ref:
            existing = db.query(Expense).filter(Expense.client_ref == data.client_ref).first()
            if existing:
                expense_id = existing.id
        if expense_id is None:
            # Link the spend to the currently OPEN cash shift so it hits the live shift ledger
            # (station-scoped when a station is given, else the most-recent open shift — single
            # property). Falls back to a standalone expense (shift_id NULL) only when no shift is open.
            shift = _open_shift(db, data.station_id)
            exp = Expense(
                shift_id=shift.id if shift else None,
                # Expense categories are admin-editable (F-A); this was the last create path
                # still carrying the old fixed enum (FE-6).
                category=validate_category(db, "expense", data.category),
                description=f"Ticket #{ticket.id}: {it.item}" if ticket else it.item,
                amount=Decimal(str(round(data.actual_cost, 2))),
                client_ref=data.client_ref,
                created_by=_resolve_user_id(db, user),
            )
            db.add(exp)
            db.flush()
            expense_id = exp.id
    it.status = "purchased"
    it.expense_id = expense_id
    db.commit()
    write_audit(db, user, "maintenance.item_purchase", "ticket_item", it.id,
                after={"actual_cost": data.actual_cost, "expense_id": expense_id},
                client="web", commit=True)
    return {**_item_dict(db, it), "expense_id": expense_id}
