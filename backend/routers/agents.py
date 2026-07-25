"""Travel-agent management, negotiated rate cards, and commission settlement (prompt 10).

- Agents CRUD + active toggle (admin).
- Per-agent negotiated rate cards (agent_rates) CRUD (admin).
- Commission settlement: booked / commission accrued / paid / outstanding, per agent,
  plus per-agent detail and payout recording (admin).

Rate PLANS (seasonal/weekend/channel overrides) live on routers/room_types.py per the
build prompt; the shared price resolver is utils/rate_engine.py.
"""
import logging
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from database import SessionLocal
from models import TravelAgent, AgentRate, AgentPayment, RoomType, Booking, Guest
from schemas import (TravelAgentCreate, TravelAgentUpdate, TravelAgentToggle,
                     AgentRateCreate, AgentRateUpdate, AgentPaymentCreate)
from utils.auth_utils import require_admin, require_reception_or_admin
from utils.audit import write_audit, _resolve_user_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agents", tags=["Travel Agents"])

# Booking statuses that count toward commission (everything except cancelled).
_CANCELLED = ("cancelled",)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _serialize_agent(a: TravelAgent) -> dict:
    return {
        "id": a.id,
        "name": a.name,
        "contact": a.contact,
        "gst_no": a.gst_no,
        "commission_percent": float(a.commission_percent or 0),
        "credit_limit": float(a.credit_limit or 0),
        "is_active": a.is_active,
        "created_at": a.created_at,
    }


def _serialize_rate(r: AgentRate) -> dict:
    return {
        "id": r.id,
        "agent_id": r.agent_id,
        "room_type_id": r.room_type_id,
        "room_type_name": r.room_type.name if r.room_type else None,
        "rate": float(r.rate),
        "valid_from": r.valid_from,
        "valid_to": r.valid_to,
    }


def _get_agent(db, agent_id) -> TravelAgent:
    agent = db.query(TravelAgent).filter(TravelAgent.id == agent_id).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Travel agent not found")
    return agent


# ============================================================
# Agents CRUD
# ============================================================

@router.get("/", dependencies=[Depends(require_reception_or_admin)])
def list_agents(active: bool | None = Query(None, description="filter by is_active"),
                db: Session = Depends(get_db)):
    """List agents. Reception-readable so the desk booking screen can populate its
    agent picker (?active=true). Mutations below are admin-only."""
    q = db.query(TravelAgent)
    if active is not None:
        q = q.filter(TravelAgent.is_active == active)
    agents = q.order_by(TravelAgent.name).all()
    return [_serialize_agent(a) for a in agents]


@router.post("/", dependencies=[Depends(require_admin)])
def create_agent(data: TravelAgentCreate, user=Depends(require_admin), db: Session = Depends(get_db)):
    agent = TravelAgent(
        name=data.name,
        contact=data.contact,
        gst_no=data.gst_no,
        commission_percent=data.commission_percent,
        credit_limit=data.credit_limit,
        is_active=data.is_active,
    )
    db.add(agent)
    db.commit()
    db.refresh(agent)
    write_audit(db, user, "agent.create", "travel_agent", agent.id,
                after=_serialize_agent(agent), client="web", commit=True)
    return _serialize_agent(agent)


@router.put("/{agent_id}", dependencies=[Depends(require_admin)])
def update_agent(agent_id: int, data: TravelAgentUpdate, user=Depends(require_admin),
                 db: Session = Depends(get_db)):
    agent = _get_agent(db, agent_id)
    before = _serialize_agent(agent)
    for key, value in data.dict(exclude_unset=True).items():
        setattr(agent, key, value)
    db.commit()
    db.refresh(agent)
    write_audit(db, user, "agent.update", "travel_agent", agent.id,
                before=before, after=_serialize_agent(agent), client="web", commit=True)
    return _serialize_agent(agent)


@router.patch("/{agent_id}/toggle", dependencies=[Depends(require_admin)])
def toggle_agent(agent_id: int, data: TravelAgentToggle, db: Session = Depends(get_db)):
    agent = _get_agent(db, agent_id)
    agent.is_active = data.is_active
    db.commit()
    return {"id": agent_id, "is_active": agent.is_active}


@router.delete("/{agent_id}", dependencies=[Depends(require_admin)])
def delete_agent(agent_id: int, user=Depends(require_admin), db: Session = Depends(get_db)):
    agent = _get_agent(db, agent_id)
    ref = db.query(func.count(Booking.booking_id)).filter(Booking.agent_id == agent_id).scalar()
    if ref:
        raise HTTPException(status_code=409,
                            detail=f"Cannot delete: {ref} booking(s) reference this agent. Deactivate it instead.")
    # Rate cards / plans are the agent's own child data — remove them with the agent.
    db.query(AgentRate).filter(AgentRate.agent_id == agent_id).delete(synchronize_session=False)
    db.query(AgentPayment).filter(AgentPayment.agent_id == agent_id).delete(synchronize_session=False)
    from models import RatePlan
    db.query(RatePlan).filter(RatePlan.agent_id == agent_id).delete(synchronize_session=False)
    db.delete(agent)
    db.commit()
    write_audit(db, user, "agent.delete", "travel_agent", agent_id, client="web", commit=True)
    return {"message": "Travel agent deleted", "id": agent_id}


# ============================================================
# Negotiated rate cards (agent_rates)
# ============================================================

@router.get("/{agent_id}/rates", dependencies=[Depends(require_reception_or_admin)])
def list_agent_rates(agent_id: int, db: Session = Depends(get_db)):
    _get_agent(db, agent_id)
    rates = db.query(AgentRate).filter(AgentRate.agent_id == agent_id) \
        .order_by(AgentRate.room_type_id, AgentRate.valid_from).all()
    return [_serialize_rate(r) for r in rates]


@router.post("/{agent_id}/rates", dependencies=[Depends(require_admin)])
def create_agent_rate(agent_id: int, data: AgentRateCreate, user=Depends(require_admin),
                      db: Session = Depends(get_db)):
    _get_agent(db, agent_id)
    if not db.query(RoomType).filter(RoomType.room_type_id == data.room_type_id).first():
        raise HTTPException(status_code=400, detail="Room type not found")
    rate = AgentRate(
        agent_id=agent_id,
        room_type_id=data.room_type_id,
        rate=data.rate,
        valid_from=data.valid_from,
        valid_to=data.valid_to,
    )
    db.add(rate)
    db.commit()
    db.refresh(rate)
    write_audit(db, user, "agent_rate.create", "agent_rate", rate.id,
                after=_serialize_rate(rate), client="web", commit=True)
    return _serialize_rate(rate)


@router.put("/rates/{rate_id}", dependencies=[Depends(require_admin)])
def update_agent_rate(rate_id: int, data: AgentRateUpdate, user=Depends(require_admin),
                      db: Session = Depends(get_db)):
    rate = db.query(AgentRate).filter(AgentRate.id == rate_id).first()
    if not rate:
        raise HTTPException(status_code=404, detail="Agent rate not found")
    update = data.dict(exclude_unset=True)
    if update.get("room_type_id") is not None:
        if not db.query(RoomType).filter(RoomType.room_type_id == update["room_type_id"]).first():
            raise HTTPException(status_code=400, detail="Room type not found")
    for key, value in update.items():
        setattr(rate, key, value)
    db.commit()
    db.refresh(rate)
    write_audit(db, user, "agent_rate.update", "agent_rate", rate.id,
                after=_serialize_rate(rate), client="web", commit=True)
    return _serialize_rate(rate)


@router.delete("/rates/{rate_id}", dependencies=[Depends(require_admin)])
def delete_agent_rate(rate_id: int, user=Depends(require_admin), db: Session = Depends(get_db)):
    rate = db.query(AgentRate).filter(AgentRate.id == rate_id).first()
    if not rate:
        raise HTTPException(status_code=404, detail="Agent rate not found")
    db.delete(rate)
    db.commit()
    write_audit(db, user, "agent_rate.delete", "agent_rate", rate_id, client="web", commit=True)
    return {"message": "Agent rate deleted", "id": rate_id}


# ============================================================
# Commission settlement
# ============================================================

def _parse_date(s: str | None, field: str):
    if not s:
        return None
    try:
        return date.fromisoformat(s)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid {field}. Use YYYY-MM-DD")


def _agent_totals(db, agent_id, dfrom, dto):
    """(bookings, room_revenue, commission_accrued, commission_paid) for one agent.
    Accrual is filtered by booking check_in; payouts by paid_on. No range = all-time =
    true outstanding."""
    bq = db.query(
        func.count(Booking.booking_id),
        func.coalesce(func.sum(Booking.total_amount), 0),
        func.coalesce(func.sum(Booking.commission_amount), 0),
    ).filter(
        Booking.agent_id == agent_id,
        Booking.status.notin_(_CANCELLED),
    )
    if dfrom:
        bq = bq.filter(Booking.check_in >= dfrom)
    if dto:
        bq = bq.filter(Booking.check_in <= dto)
    count, revenue, accrued = bq.one()

    pq = db.query(func.coalesce(func.sum(AgentPayment.amount), 0)) \
        .filter(AgentPayment.agent_id == agent_id)
    if dfrom:
        pq = pq.filter(AgentPayment.paid_on >= dfrom)
    if dto:
        pq = pq.filter(AgentPayment.paid_on <= dto)
    paid = pq.scalar()

    return int(count), float(revenue or 0), float(accrued or 0), float(paid or 0)


@router.get("/settlement", dependencies=[Depends(require_admin)])
def settlement_report(from_: str | None = Query(None, alias="from"),
                      to: str | None = Query(None),
                      db: Session = Depends(get_db)):
    """Agent-wise settlement: booked, commission accrued, paid, outstanding.
    Optional ?from&to (YYYY-MM-DD) scope by booking check-in / payout date;
    omit both for the running lifetime balance (true outstanding)."""
    dfrom = _parse_date(from_, "from")
    dto = _parse_date(to, "to")
    rows = []
    for a in db.query(TravelAgent).order_by(TravelAgent.name).all():
        count, revenue, accrued, paid = _agent_totals(db, a.id, dfrom, dto)
        rows.append({
            "agent_id": a.id,
            "agent_name": a.name,
            "commission_percent": float(a.commission_percent or 0),
            "is_active": a.is_active,
            "bookings": count,
            "room_revenue": round(revenue, 2),
            "commission_accrued": round(accrued, 2),
            "commission_paid": round(paid, 2),
            "outstanding": round(accrued - paid, 2),
        })
    totals = {
        "room_revenue": round(sum(r["room_revenue"] for r in rows), 2),
        "commission_accrued": round(sum(r["commission_accrued"] for r in rows), 2),
        "commission_paid": round(sum(r["commission_paid"] for r in rows), 2),
        "outstanding": round(sum(r["outstanding"] for r in rows), 2),
    }
    return {"from": str(dfrom) if dfrom else None, "to": str(dto) if dto else None,
            "agents": rows, "totals": totals}


@router.get("/{agent_id}/settlement", dependencies=[Depends(require_admin)])
def agent_settlement_detail(agent_id: int,
                            from_: str | None = Query(None, alias="from"),
                            to: str | None = Query(None),
                            db: Session = Depends(get_db)):
    """One agent's settlement + the underlying bookings and payouts."""
    agent = _get_agent(db, agent_id)
    dfrom = _parse_date(from_, "from")
    dto = _parse_date(to, "to")
    count, revenue, accrued, paid = _agent_totals(db, agent_id, dfrom, dto)

    bq = db.query(Booking).filter(Booking.agent_id == agent_id, Booking.status.notin_(_CANCELLED))
    if dfrom:
        bq = bq.filter(Booking.check_in >= dfrom)
    if dto:
        bq = bq.filter(Booking.check_in <= dto)
    bookings = []
    for b in bq.order_by(Booking.check_in.desc()).all():
        guest = db.query(Guest).filter(Guest.guest_id == b.guest_id).first()
        bookings.append({
            "booking_id": b.booking_id,
            "guest_name": guest.name if guest else None,
            "check_in": str(b.check_in),
            "check_out": str(b.check_out),
            "status": b.status,
            "room_total": float(b.total_amount or 0),
            "commission_percent": float(b.commission_percent) if b.commission_percent is not None else None,
            "commission_amount": float(b.commission_amount or 0),
        })

    pq = db.query(AgentPayment).filter(AgentPayment.agent_id == agent_id)
    if dfrom:
        pq = pq.filter(AgentPayment.paid_on >= dfrom)
    if dto:
        pq = pq.filter(AgentPayment.paid_on <= dto)
    payments = [{
        "id": p.id, "amount": float(p.amount), "paid_on": str(p.paid_on),
        "mode": p.mode, "reference": p.reference, "note": p.note,
    } for p in pq.order_by(AgentPayment.paid_on.desc()).all()]

    return {
        "agent": _serialize_agent(agent),
        "from": str(dfrom) if dfrom else None, "to": str(dto) if dto else None,
        "summary": {
            "bookings": count, "room_revenue": round(revenue, 2),
            "commission_accrued": round(accrued, 2), "commission_paid": round(paid, 2),
            "outstanding": round(accrued - paid, 2),
        },
        "bookings": bookings,
        "payments": payments,
    }


@router.get("/{agent_id}/payments", dependencies=[Depends(require_admin)])
def list_agent_payments(agent_id: int, db: Session = Depends(get_db)):
    _get_agent(db, agent_id)
    rows = db.query(AgentPayment).filter(AgentPayment.agent_id == agent_id) \
        .order_by(AgentPayment.paid_on.desc()).all()
    return [{
        "id": p.id, "amount": float(p.amount), "paid_on": str(p.paid_on),
        "mode": p.mode, "reference": p.reference, "note": p.note,
    } for p in rows]


@router.post("/{agent_id}/payments", dependencies=[Depends(require_admin)])
def record_agent_payment(agent_id: int, data: AgentPaymentCreate, user=Depends(require_admin),
                         db: Session = Depends(get_db)):
    """Record a commission payout to the agent (reduces outstanding)."""
    _get_agent(db, agent_id)
    payment = AgentPayment(
        agent_id=agent_id,
        amount=data.amount,
        paid_on=data.paid_on,
        mode=data.mode,
        reference=data.reference,
        note=data.note,
        created_by=_resolve_user_id(db, user),
    )
    db.add(payment)
    db.commit()
    db.refresh(payment)
    write_audit(db, user, "agent.payout", "travel_agent", agent_id,
                after={"amount": float(data.amount), "mode": data.mode, "paid_on": str(data.paid_on)},
                client="web", commit=True)
    return {"id": payment.id, "agent_id": agent_id, "amount": float(payment.amount),
            "paid_on": str(payment.paid_on), "mode": payment.mode}
