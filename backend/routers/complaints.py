"""Guest-complaint endpoints (prompt 18c, slice 11).

A guest complaint IS a maintenance ticket with source='guest' — the same rows the WhatsApp
inbound webhook (prompt 15) and the review pipeline (prompt 20) already create. This module does
NOT introduce a second table; it adds the front-desk create path plus the SLA / escalation /
compensation lifecycle on top of the existing ticket.

Reuse, not rebuild:
  * Create goes through `routers.maintenance.create_guest_ticket` (the documented source='guest'
    seam), then this module stamps per-priority SLA due-times from config.
  * Escalation appends an APPEND-ONLY `ticket_escalations` row and (best-effort) WhatsApps the
    owner via `utils.whatsapp_service`. The SLA sweep (`utils/complaint_jobs.py`) rides the
    EXISTING WhatsApp scheduler and calls `escalate_ticket` here — one escalation path.
  * Compensation posts a `discount` folio line via the same charge → `_recompute` → audit flow
    the desk discount uses (`routers.folio`), and is admin-gated + reason-required + audited.
"""
import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from database import SessionLocal
from models import Booking, Folio, FolioCharge, MaintenanceTicket, Room, TicketEscalation
from schemas import (ComplaintBulkResolve, ComplaintCompensate, ComplaintConfigUpdate,
                     ComplaintCreate, ComplaintEscalate, ComplaintResolve, ComplaintRespond)
from utils import settings as app_settings
from utils.audit import _resolve_user_id, write_audit
from utils.auth_utils import require_admin, require_roles

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/complaints", tags=["Complaints"])

# Complaint handling is a front-desk (reception) activity; admin too. v4b8 retired the
# separate supervisor role, so oversight sits with admin.
# Config / compensation / escalation-run stay admin-only below.
staff = require_roles("admin", "reception")

# Statuses at which a complaint is still "open" and its SLA clock runs.
OPEN_STATUSES = ("open", "assigned", "in_progress", "awaiting_parts")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("/health", dependencies=[Depends(staff)])
def health():
    return {"status": "ok", "module": "complaints"}


# ---------------------------------------------------------------- helpers

def _get_complaint(db: Session, complaint_id: int) -> MaintenanceTicket:
    t = db.query(MaintenanceTicket).filter(MaintenanceTicket.id == complaint_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="Complaint not found")
    return t


def stamp_sla(ticket: MaintenanceTicket, cfg: dict, base: datetime | None = None):
    """Set respond-by / resolve-by targets from the per-priority SLA config."""
    base = base or datetime.utcnow()
    pr = ticket.priority if ticket.priority in ("urgent", "high", "normal", "low") else "normal"
    ticket.sla_response_due_at = base + timedelta(hours=cfg["sla_response_hours"][pr])
    ticket.sla_resolve_due_at = base + timedelta(hours=cfg["sla_resolve_hours"][pr])


def _breach(ticket: MaintenanceTicket, now: datetime | None = None) -> dict:
    """Which SLA (if any) a still-open complaint has breached."""
    now = now or datetime.utcnow()
    open_ = ticket.status in OPEN_STATUSES
    resp_breach = bool(open_ and ticket.first_responded_at is None
                       and ticket.sla_response_due_at and now > ticket.sla_response_due_at)
    resolve_breach = bool(open_ and ticket.sla_resolve_due_at and now > ticket.sla_resolve_due_at)
    return {"response_breached": resp_breach, "resolve_breached": resolve_breach,
            "any_breached": resp_breach or resolve_breach}


def _complaint_dict(db: Session, t: MaintenanceTicket, with_trail: bool = False) -> dict:
    booking = db.query(Booking).filter(Booking.booking_id == t.booking_id).first() if t.booking_id else None
    guest_name = booking.guest.name if booking and booking.guest else None
    room = db.query(Room).filter(Room.room_id == t.room_id).first() if t.room_id else None
    b = _breach(t)
    data = {
        "id": t.id,
        "issue": t.issue,
        "category": t.category,
        "priority": t.priority,
        "status": t.status,
        "source": t.source,
        "booking_id": t.booking_id,
        "guest_name": guest_name,
        "room_id": t.room_id,
        "room_number": room.room_number if room else None,
        "created_at": str(t.created_at) if t.created_at else None,
        "sla_response_due_at": str(t.sla_response_due_at) if t.sla_response_due_at else None,
        "sla_resolve_due_at": str(t.sla_resolve_due_at) if t.sla_resolve_due_at else None,
        "first_responded_at": str(t.first_responded_at) if t.first_responded_at else None,
        "resolved_at": str(t.resolved_at) if t.resolved_at else None,
        "escalation_level": int(t.escalation_level or 0),
        "last_escalated_at": str(t.last_escalated_at) if t.last_escalated_at else None,
        "compensation_amount": float(t.compensation_amount) if t.compensation_amount is not None else None,
        "resolution_notes": t.resolution_notes,
        "response_breached": b["response_breached"],
        "resolve_breached": b["resolve_breached"],
        "sla_breached": b["any_breached"],
    }
    if with_trail:
        trail = db.query(TicketEscalation).filter(
            TicketEscalation.ticket_id == t.id).order_by(TicketEscalation.id).all()
        data["escalations"] = [{
            "id": e.id, "level": e.level, "reason": e.reason, "notified": bool(e.notified),
            "created_at": str(e.created_at) if e.created_at else None,
        } for e in trail]
    return data


def escalate_ticket(db: Session, ticket: MaintenanceTicket, reason: str, *,
                    user=None, notify: bool = True, commit: bool = True) -> TicketEscalation:
    """Bump the complaint's escalation level, append the append-only trail row, and best-effort
    WhatsApp the owner. Shared by the manual endpoint and the SLA sweep so they never drift."""
    ticket.escalation_level = int(ticket.escalation_level or 0) + 1
    ticket.last_escalated_at = datetime.utcnow()
    row = TicketEscalation(ticket_id=ticket.id, level=ticket.escalation_level,
                           reason=(reason or "")[:200], created_by=_resolve_user_id(db, user))
    db.add(row)
    db.flush()
    if notify:
        try:
            from utils import whatsapp_service as wa
            owners = wa.owner_numbers(db)   # a hotel may have more than one owner
            any_sent = False
            for i, owner in enumerate(owners):
                sent = wa.send_template(
                    db, owner, "complaint_escalation",
                    {"complaint_id": str(ticket.id), "priority": ticket.priority,
                     "level": str(ticket.escalation_level),
                     "issue": (ticket.issue or "")[:80], "reason": reason or "SLA breach"},
                    client_ref=f"complaint_esc:{ticket.id}:{ticket.escalation_level}:{i}",
                    respect_optout=False,
                )
                any_sent = any_sent or (sent is not None)
            row.notified = any_sent
        except Exception as e:  # never let a delivery hiccup block the escalation
            logger.error(f"complaint escalation alert failed: {e}")
    if commit:
        db.commit()
        db.refresh(row)
    return row


# ---------------------------------------------------------------- create / list

@router.post("/")
def create_complaint(data: ComplaintCreate, db: Session = Depends(get_db),
                     user=Depends(staff)):
    """Front desk logs a guest's complaint against a stay. Creates a source='guest' ticket and
    stamps its SLA due-times. Idempotent on client_ref (via create_guest_ticket)."""
    try:
        from routers.maintenance import create_guest_ticket
        category = app_settings.validate_category(db, "complaint", data.category)   # editable list (F-A)
        priority = app_settings.validate_category(db, "priority", data.priority)    # editable list (v3 item 2)
        if data.room_id is not None and not db.query(Room).filter(Room.room_id == data.room_id).first():
            raise HTTPException(status_code=404, detail="Room not found")
        if data.booking_id is not None and not db.query(Booking).filter(
                Booking.booking_id == data.booking_id).first():
            raise HTTPException(status_code=404, detail="Booking not found")

        existing = None
        if data.client_ref:
            existing = db.query(MaintenanceTicket).filter(
                MaintenanceTicket.client_ref == data.client_ref).first()

        ticket = create_guest_ticket(
            db, issue=data.issue, booking_id=data.booking_id, room_id=data.room_id,
            category="other" if data.category not in ("maintenance",) else "other",
            priority=priority, client_ref=data.client_ref, commit=False)
        # Store the guest-facing complaint category in area (the ticket's category enum is the
        # maintenance taxonomy; complaints use their own — kept in `area` so both stay valid).
        if ticket.area is None:
            ticket.area = f"complaint:{data.category}"
        if existing is None:            # a fresh ticket — set SLA + raiser
            ticket.raised_by = _resolve_user_id(db, user)
            stamp_sla(ticket, app_settings.get_complaints_config(db))
        db.commit()
        db.refresh(ticket)
        write_audit(db, user, "complaint.create", "maintenance_ticket", ticket.id,
                    after={"category": data.category, "priority": ticket.priority,
                           "booking_id": ticket.booking_id, "duplicate": existing is not None},
                    client="web", commit=True)
        return {**_complaint_dict(db, ticket), "duplicate": existing is not None}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"create_complaint failed: {e}", exc_info=True)
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to log the complaint")


@router.get("/")
def list_complaints(status: str | None = Query(None), priority: str | None = Query(None),
                    source: str | None = Query(None), breached: bool = Query(False),
                    open_only: bool = Query(False), q: str | None = Query(None),
                    db: Session = Depends(get_db), user=Depends(staff)):
    """Guest complaints (source='guest' by default) with SLA + escalation state. Reception can see
    them to follow up at the desk; admin manages them on the Complaints screen."""
    query = db.query(MaintenanceTicket).filter(MaintenanceTicket.source == (source or "guest"))
    if status:
        query = query.filter(MaintenanceTicket.status == status)
    if priority:
        query = query.filter(MaintenanceTicket.priority == priority)
    if open_only:
        query = query.filter(MaintenanceTicket.status.in_(OPEN_STATUSES))
    if q:
        query = query.filter(MaintenanceTicket.issue.ilike(f"%{q.strip()}%"))
    rows = query.order_by(MaintenanceTicket.created_at.desc()).limit(500).all()
    data = [_complaint_dict(db, t) for t in rows]
    if breached:
        data = [d for d in data if d["sla_breached"]]
    return {"total": len(data), "breached_count": sum(1 for d in data if d["sla_breached"]),
            "open_count": sum(1 for d in data if d["status"] in OPEN_STATUSES), "data": data}


@router.get("/config")
def get_config(db: Session = Depends(get_db), user=Depends(staff)):
    return app_settings.get_complaints_config(db)


@router.put("/config", dependencies=[Depends(require_admin)])
def update_config(data: ComplaintConfigUpdate, db: Session = Depends(get_db),
                  user=Depends(require_admin)):
    changes = data.model_dump(exclude_unset=True)
    applied = {}
    for key, value in changes.items():
        if key in app_settings.COMPLAINT_EDITABLE_KEYS:
            app_settings.set_setting(db, key, value, user=user)
            applied[key] = value
    db.commit()
    write_audit(db, user, "complaint.config_update", "app_settings", None,
                after=applied, client="web", commit=True)
    return app_settings.get_complaints_config(db)


@router.post("/escalations/run", dependencies=[Depends(require_admin)])
def run_escalations(db: Session = Depends(get_db), user=Depends(require_admin)):
    """Fire the SLA-escalation sweep now. The SAME function the WhatsApp scheduler calls."""
    from utils.complaint_jobs import run_complaint_sla_sweep
    result = run_complaint_sla_sweep(db)
    write_audit(db, user, "complaint.escalations_run", "maintenance_ticket", None,
                after=result, client="web", commit=True)
    return result


@router.get("/{complaint_id}")
def get_complaint(complaint_id: int, db: Session = Depends(get_db),
                  user=Depends(staff)):
    t = _get_complaint(db, complaint_id)
    return _complaint_dict(db, t, with_trail=True)


# ---------------------------------------------------------------- lifecycle

@router.post("/{complaint_id}/respond")
def respond_complaint(complaint_id: int, data: ComplaintRespond, db: Session = Depends(get_db),
                      user=Depends(staff)):
    """Acknowledge the complaint to the guest — stamps first response (stops the response-SLA
    clock) and moves an untouched complaint to in_progress."""
    try:
        t = _get_complaint(db, complaint_id)
        if t.first_responded_at is None:
            t.first_responded_at = datetime.utcnow()
        if t.status == "open":
            t.status = "in_progress"
        if data.note:
            t.resolution_notes = f"{t.resolution_notes + chr(10) if t.resolution_notes else ''}{data.note}"
        db.commit()
        write_audit(db, user, "complaint.respond", "maintenance_ticket", t.id,
                    after={"note": data.note}, client="web", commit=True)
        return _complaint_dict(db, t)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"respond_complaint failed: {e}", exc_info=True)
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to record the response")


@router.post("/{complaint_id}/escalate")
def escalate_complaint(complaint_id: int, data: ComplaintEscalate, db: Session = Depends(get_db),
                       user=Depends(staff)):
    """Manually escalate a complaint (bumps the level + alerts the owner)."""
    try:
        t = _get_complaint(db, complaint_id)
        if t.status not in OPEN_STATUSES:
            raise HTTPException(status_code=409, detail="Complaint is already closed")
        escalate_ticket(db, t, data.reason or "Manually escalated", user=user, notify=True)
        write_audit(db, user, "complaint.escalate", "maintenance_ticket", t.id,
                    after={"level": t.escalation_level, "reason": data.reason}, client="web", commit=True)
        return _complaint_dict(db, t, with_trail=True)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"escalate_complaint failed: {e}", exc_info=True)
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to escalate the complaint")


@router.post("/{complaint_id}/resolve")
def resolve_complaint(complaint_id: int, data: ComplaintResolve, db: Session = Depends(get_db),
                      user=Depends(staff)):
    """Mark the complaint resolved."""
    try:
        t = _get_complaint(db, complaint_id)
        t.status = "resolved"
        t.resolved_at = datetime.utcnow()
        if t.first_responded_at is None:
            t.first_responded_at = datetime.utcnow()
        if data.resolution_notes:
            t.resolution_notes = f"{t.resolution_notes + chr(10) if t.resolution_notes else ''}{data.resolution_notes}"
        db.commit()
        write_audit(db, user, "complaint.resolve", "maintenance_ticket", t.id,
                    after={"resolution_notes": data.resolution_notes}, client="web", commit=True)
        return _complaint_dict(db, t)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"resolve_complaint failed: {e}", exc_info=True)
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to resolve the complaint")


@router.post("/bulk-resolve")
def bulk_resolve_complaints(data: ComplaintBulkResolve, db: Session = Depends(get_db),
                            user=Depends(staff)):
    """Resolve several guest complaints at once (clear a backlog). Skips any that aren't guest
    complaints or are already resolved/closed. Audited per ticket, same as single resolve."""
    from routers.maintenance import TERMINAL_STATUSES   # local: routers import each other
    resolved, skipped = 0, 0
    try:
        for cid in data.ids:
            t = db.query(MaintenanceTicket).filter(
                MaintenanceTicket.id == cid, MaintenanceTicket.source == "guest").first()
            if t is None or t.status in TERMINAL_STATUSES:
                skipped += 1
                continue
            t.status = "resolved"
            t.resolved_at = datetime.utcnow()
            if t.first_responded_at is None:
                t.first_responded_at = datetime.utcnow()
            if data.resolution_notes:
                t.resolution_notes = (
                    f"{t.resolution_notes + chr(10) if t.resolution_notes else ''}{data.resolution_notes}")
            db.commit()
            write_audit(db, user, "complaint.resolve", "maintenance_ticket", t.id,
                        after={"resolution_notes": data.resolution_notes, "bulk": True},
                        client="web", commit=True)
            resolved += 1
        return {"resolved": resolved, "skipped": skipped}
    except Exception as e:
        logger.error(f"bulk_resolve_complaints failed: {e}", exc_info=True)
        db.rollback()
        raise HTTPException(status_code=500, detail="Bulk resolve failed")


@router.post("/{complaint_id}/compensate", dependencies=[Depends(require_admin)])
def compensate_complaint(complaint_id: int, data: ComplaintCompensate, db: Session = Depends(get_db),
                         user=Depends(require_admin)):
    """Admin logs a goodwill credit to the guest's folio (a discount line). Admin-only, reason
    required, audited — the same posture as any folio discount/void. The complaint must be tied to
    a booking with an OPEN folio. Idempotent via a [client_ref] description marker."""
    try:
        from routers.folio import _recompute
        t = _get_complaint(db, complaint_id)
        if not t.booking_id:
            raise HTTPException(status_code=409,
                                detail="Complaint is not linked to a booking — cannot compensate")
        folio = db.query(Folio).filter(Folio.booking_id == t.booking_id).first()
        if not folio:
            raise HTTPException(status_code=409, detail="No folio for this stay")
        if folio.status != "open":
            raise HTTPException(status_code=409, detail="Folio is settled — cannot post a credit")

        marker = f"[{data.client_ref}] " if data.client_ref else ""
        desc = f"{marker}Goodwill (complaint #{t.id}) — {data.reason}"
        if data.client_ref:
            dup = db.query(FolioCharge).filter(
                FolioCharge.folio_id == folio.id, FolioCharge.type == "discount",
                FolioCharge.description == desc).first()
            if dup:
                return {**_complaint_dict(db, t), "duplicate": True, "charge_id": dup.id}

        charge = FolioCharge(
            folio_id=folio.id, type="discount", description=desc,
            qty=1, unit_price=data.amount, amount=-abs(data.amount),
            posted_by=_resolve_user_id(db, user),
        )
        db.add(charge)
        _recompute(db, folio)
        db.flush()
        t.compensation_charge_id = charge.id
        t.compensation_amount = round(float(t.compensation_amount or 0) + abs(data.amount), 2)
        db.commit()
        write_audit(db, user, "complaint.compensate", "maintenance_ticket", t.id,
                    after={"amount": abs(data.amount), "reason": data.reason,
                           "folio_id": folio.id, "charge_id": charge.id}, client="web", commit=True)
        return {**_complaint_dict(db, t), "duplicate": False, "charge_id": charge.id,
                "folio_id": folio.id, "folio_balance": float(folio.balance or 0)}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"compensate_complaint failed: {e}", exc_info=True)
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to log compensation")
