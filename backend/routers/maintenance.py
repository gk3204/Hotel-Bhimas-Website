"""Maintenance ticket endpoints (prompt 13).

Tickets are raisable by housekeeping/reception/admin (and guests via WhatsApp in prompt 15;
room_id is optional so common areas work). An admin/housekeeper assigns a ticket to a
`maintenance` user (electrician/plumber/AC tech...) — or, from v4b8, the ticket auto-assigns to
the technician designated in Settings — who tracks it in_progress -> awaiting_parts ->
work_done and lists the required parts (ticket_items).
A ticket only reaches 'resolved' (its terminal state) once a housekeeper/admin signs the work
off; the technician's own terminal step is 'work_done'. `verified_by` / `verified_at` record
that sign-off, kept under their original column names.
Admin approves items; a purchased item can post to `expenses` (prompt 12) so parts spend
hits the shift ledger + P&L.

Notifications (assignee) are an interim logger.info — WhatsApp delivery lands in prompt 15
behind the same call, matching the owner_otp / fraud-alert convention.
"""
import logging
from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_
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
# v4b8 lifecycle: open -> assigned -> in_progress | awaiting_parts -> work_done -> resolved.
# The technician marks WORK_DONE; only the sign-off (POST /verify) marks it RESOLVED, which is
# terminal. Before this, a technician could call their own job resolved.
TERMINAL_STATUSES = ("resolved", "closed")
def _default_assignee(db):
    """v4b8: the technician every new ticket is handed to automatically.

    The owner chose ONE designated person over round-robin — a small hotel has one maintenance
    man, and "whose job is it?" should never be a question. Falls back to unassigned (and the
    existing self-claim button) when unset, when the user is gone, deactivated, or is no longer
    a maintenance user. ⚠️ Never raises: a bad setting must not stop a ticket being raised.
    """
    try:
        from utils.settings import get_backoffice_config
        uid = get_backoffice_config(db).get("maintenance_default_assignee_id") or 0
        if not uid:
            return None
        u = db.query(User).filter(User.user_id == uid).first()
        if u and u.role == "maintenance" and getattr(u, "is_active", True) is not False:
            return u.user_id
        logger.warning(f"maintenance default assignee {uid} is not an active maintenance user "
                       f"— leaving the ticket unassigned")
    except Exception as e:
        logger.warning(f"maintenance default assignee lookup failed: {e}")
    return None


def _auto_assign(db) -> dict:
    """The status/assignee kwargs for a NEW ticket (v4b8).

    With a designated technician configured, a ticket is born ASSIGNED to them — the owner's
    point being that "whose job is it?" should never be a question. Without one it is born
    `open` and unassigned exactly as before, and the self-claim button still works.
    """
    uid = _default_assignee(db)
    if uid:
        return {"status": "assigned", "assignee": uid, "assigned_at": datetime.utcnow()}
    return {"status": "open"}


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
    if t.status in TERMINAL_STATUSES or t.status == "work_done":
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


def _raise_ac_ticket(db: Session, booking_id, room, *, turning_on: bool, commit=False):
    """Shared body for the AC on/off tickets. source='reception' (NOT 'guest' — that would be
    a complaint). Idempotent per booking+room+direction. Best-effort: callers wrap this so a
    failure never blocks the stay flow."""
    if room is None:
        return None
    kind = "on" if turning_on else "off"
    client_ref = f"ac-{kind}-{booking_id}-{room.room_id}"
    dup = db.query(MaintenanceTicket).filter(MaintenanceTicket.client_ref == client_ref).first()
    if dup:
        return dup
    t = MaintenanceTicket(
        room_id=room.room_id, category="appliance",
        issue=f"Turn {kind} AC — Room {room.room_number}",
        # Turning the AC ON is guest comfort and blocks the stay starting well; turning it
        # OFF is energy saving on an empty room, so it does not need to jump the queue.
        priority="high" if turning_on else "normal",
        **_auto_assign(db), booking_id=booking_id,
        source="reception", client_ref=client_ref,
    )
    db.add(t)
    if commit:
        db.commit()
        db.refresh(t)
    else:
        db.flush()
    logger.info(f"❄️ AC-{kind} ticket #{t.id} raised for room {room.room_number} (booking={booking_id})")
    return t


def raise_ac_on_ticket(db: Session, booking_id, room, commit=False):
    """Auto-raise a 'turn on AC' ticket for an AC room at check-in / shift-into-AC (FE-10)."""
    return _raise_ac_ticket(db, booking_id, room, turning_on=True, commit=commit)


def raise_ac_off_ticket(db: Session, booking_id, room, commit=False):
    """Auto-raise a 'turn off AC' ticket when an AC room is vacated (FE-10 follow-up) so an
    empty room isn't cooled all day. Same idempotency + best-effort contract as the on-ticket."""
    return _raise_ac_ticket(db, booking_id, room, turning_on=False, commit=commit)


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
        priority=priority, **_auto_assign(db), booking_id=booking_id, source="guest",
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
                  user=Depends(require_roles("admin", "reception", "housekeeper", "maintenance"))):
    """Raise a ticket. Source is derived from the caller's role."""
    if data.client_ref:
        dup = db.query(MaintenanceTicket).filter(MaintenanceTicket.client_ref == data.client_ref).first()
        if dup:
            return {**_ticket_dict(db, dup), "duplicate": True}
    if data.room_id is not None:
        if not db.query(Room).filter(Room.room_id == data.room_id).first():
            raise HTTPException(status_code=404, detail="Room not found")
    category = validate_category(db, "maintenance", data.category)   # against the editable list (F-A)
    priority = validate_category(db, "priority", data.priority)      # editable list (v3 item 2)

    t = MaintenanceTicket(
        room_id=data.room_id, area=data.area, category=category, issue=data.issue,
        priority=priority, **_auto_assign(db), booking_id=data.booking_id,
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
                 user=Depends(require_roles("admin", "reception", "housekeeper", "maintenance"))):
    """List tickets with filters. A `maintenance` user sees only their own assigned tickets."""
    q = db.query(MaintenanceTicket)
    if user.get("role") == "maintenance":
        # A technician sees their OWN assigned tickets PLUS unassigned ones they can self-claim.
        # Without the unassigned half, an open job never reached the staff app and the "Claim this
        # job" button could never appear.
        me = _resolve_user_id(db, user)
        q = q.filter(or_(MaintenanceTicket.assignee == me, MaintenanceTicket.assignee.is_(None)))
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
               user=Depends(require_roles("admin", "reception", "housekeeper", "maintenance"))):
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


@router.post("/tickets/{ticket_id}/claim")
def claim_ticket(ticket_id: int, db: Session = Depends(get_db),
                 user=Depends(require_maintenance_or_admin)):
    """A maintenance user picks up an unassigned ticket themselves (backlog v2 ALT-8).

    Until now only a supervisor/admin could assign, so a technician who spotted an open job
    was blocked with "Ticket is not assigned yet" until someone else acted. Claiming
    auto-assigns on pick-up. Deliberately narrow: only an OPEN, UNASSIGNED ticket can be
    claimed, so this can never take a job off a colleague — reassignment stays an admin
    action. Audited like any other assignment."""
    t = _get_ticket(db, ticket_id)
    if t.status in TERMINAL_STATUSES:
        raise HTTPException(status_code=409, detail=f"Ticket is {t.status}")
    if t.assignee is not None:
        raise HTTPException(status_code=409, detail="That ticket is already assigned")
    if t.status != "open":
        raise HTTPException(status_code=409, detail=f"Only an open ticket can be claimed (this one is {t.status})")

    me = _resolve_user_id(db, user)
    if not me:
        raise HTTPException(status_code=403, detail="Could not identify the claiming user")

    before = {"status": t.status, "assignee": t.assignee}
    t.assignee = me
    t.assigned_at = datetime.utcnow()
    t.status = "assigned"
    db.commit()
    write_audit(db, user, "maintenance.claim", "maintenance_ticket", t.id,
                before=before, after={"status": t.status, "assignee": t.assignee},
                client="pwa", commit=True)
    logger.info(f"🔧 Ticket #{t.id} claimed by user {me}")
    return _ticket_dict(db, t)


@router.post("/tickets/{ticket_id}/status")
def update_status(ticket_id: int, data: TicketStatusUpdate, db: Session = Depends(get_db),
                  user=Depends(require_maintenance_or_admin)):
    """Assignee advances the ticket (in_progress | awaiting_parts | work_done). Every
    transition is timestamped + audited. A maintenance user may only update their own ticket.
    v4b8: their last step is WORK_DONE — `resolved` belongs to the sign-off, so a technician
    still cannot close their own job."""
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
    if data.status == "work_done":
        # `resolved_at` still records when the WORK finished; the sign-off has verified_at.
        t.resolved_at = datetime.utcnow()
        if data.note:
            t.resolution_notes = data.note
        # The auto-raised "turn on/off AC" task (client_ref 'ac-…') is a trivial, self-evident job
        # that does NOT need a housekeeper sign-off — marking it done closes it outright. Every
        # other ticket still stops at work_done for the verify step.
        if (t.client_ref or "").startswith("ac-"):
            t.status = "resolved"
            t.verified_by = me
            t.verified_at = datetime.utcnow()
    db.commit()
    write_audit(db, user, "maintenance.status", "maintenance_ticket", t.id,
                before={"status": before}, after={"status": t.status, "note": data.note},
                client="web", commit=True)
    return _ticket_dict(db, t)


@router.post("/tickets/{ticket_id}/verify")
def verify_ticket(ticket_id: int, data: TicketVerifyRequest, db: Session = Depends(get_db),
                  user=Depends(require_supervisor_or_admin)):
    """Housekeeper/admin signs a finished job off -> RESOLVED (terminal).

    v4b8: this is the step that resolves a ticket. The technician marks `work_done`; the
    sign-off here is what closes it, so nobody approves their own work."""
    t = _get_ticket(db, ticket_id)
    if t.status != "work_done":
        raise HTTPException(
            status_code=409,
            detail="Only a ticket the technician has marked 'work done' can be signed off")
    t.status = "resolved"
    t.verified_by = _resolve_user_id(db, user)
    t.verified_at = datetime.utcnow()
    if data.resolution_notes:
        t.resolution_notes = data.resolution_notes
    db.commit()
    write_audit(db, user, "maintenance.verify", "maintenance_ticket", t.id,
                after={"status": "resolved", "verified_by": t.verified_by}, client="web", commit=True)
    logger.info(f"✅ Ticket #{t.id} signed off & resolved")
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
