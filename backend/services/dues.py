"""v7s (owner, 16 Oct): "pay later" for travel-agent bookings.

"For agent bookings, sometimes the guest pays at check-out, or else a few days after check-out. The
option should be choosable at check-out; if after a few days, mark it as DUE. The invoice is
generated with the due amount, the web portal tracks it, and once we receive the money we mark the
payment done. In the cashier summary show it as due, not balance."

Model of the world
------------------
* At check-out the desk ticks "Pay later". The bill settles as usual (room freed, invoice raised),
  and `Folio.due_amount` records what was left unpaid. That figure never changes afterwards.
* What is STILL owed is the bill's live `balance` - every payment line lowers it, so a part payment
  at check-out and part payments later all just work.
* Money received later is recorded on the web (admin only) against THAT bill: a normal paid
  `Payment` (receipt RCPT-n) whose `client_ref` starts with `due<folio id>-`, plus its credit line on
  the bill. The cashier summary files it under "Due received" on the day it came in.
* When the balance reaches 0, `due_cleared_at` is stamped and the bill shows as Paid.
"""
import uuid
from decimal import Decimal

from fastapi import HTTPException

from models import (Booking, BookingItem, Folio, FolioCharge, Guest, Invoice, Payment, PaymentAllocation,
                    Room, TravelAgent, User)
from services import folio_resolver
from utils import clock
from utils.audit import _resolve_user_id

DUE_REF_PREFIX = "due"
METHODS = ("cash", "card", "upi", "bank")


def is_due_bill(folio) -> bool:
    return folio is not None and folio.due_amount is not None and float(folio.due_amount or 0) > 0


def outstanding(folio) -> float:
    """What is still owed on a pay-later bill (never negative)."""
    return max(0.0, round(float(folio.balance or 0), 2)) if is_due_bill(folio) else 0.0


def is_due_payment(payment) -> bool:
    return bool(payment is not None and (payment.client_ref or "").startswith(DUE_REF_PREFIX))


def due_payment_folio_id(payment):
    """The bill a due payment was received against (from its client_ref `due<folio id>-...`)."""
    if not is_due_payment(payment):
        return None
    head = (payment.client_ref or "")[len(DUE_REF_PREFIX):].split("-", 1)[0]
    return int(head) if head.isdigit() else None


def mark_due(db, folios, user) -> float:
    """Record the unpaid balance of each bill closing now as its due. Returns the total. Caller commits."""
    total, now, by = 0.0, clock.now_utc(), _resolve_user_id(db, user)
    for f in folios:
        bal = round(float(f.balance or 0), 2)
        if bal > 0:
            f.due_amount = Decimal(str(bal))
            f.due_marked_at = now
            f.due_marked_by = by
            f.due_cleared_at = None
            total += bal
    return round(total, 2)


def record_payment(db, folio, *, amount: float, method: str, reference, user, note=None):
    """Receive (part of) a due. Creates the receipt and its credit line on that bill. Caller commits."""
    from routers.folio import _recompute
    if not is_due_bill(folio):
        raise HTTPException(status_code=409, detail="This bill is not a pay-later (due) bill")
    owed = outstanding(folio)
    amount = round(float(amount or 0), 2)
    if owed <= 0:
        raise HTTPException(status_code=409, detail="Nothing is due on this bill - it is already paid")
    if amount <= 0:
        raise HTTPException(status_code=422, detail="Enter the amount received")
    if amount > owed + 0.005:
        raise HTTPException(status_code=422,
                            detail=f"Rs {amount:,.2f} is more than the Rs {owed:,.2f} still due on this bill")
    if method not in METHODS:
        raise HTTPException(status_code=422, detail="Mode must be cash, card, upi or bank")
    booking = db.query(Booking).filter(Booking.booking_id == folio.booking_id).first()
    by = _resolve_user_id(db, user)
    pay = Payment(booking_id=folio.booking_id, gateway="desk", method=method,
                  payment_id_gateway=(reference or None), amount=Decimal(str(amount)), currency="INR",
                  status="paid", client_ref=f"{DUE_REF_PREFIX}{folio.id}-{uuid.uuid4().hex[:12]}",
                  collected_by=by)
    db.add(pay)
    db.flush()
    ref = f" ({reference})" if reference else ""
    db.add(FolioCharge(folio_id=folio.id, type="payment",
                       description=f"Due received - {method}{ref}" + (f" - {note}" if note else ""),
                       qty=1, unit_price=amount, amount=-amount, posted_by=by,
                       booking_item_id=folio.booking_item_id, payment_id=pay.payment_id))
    if booking is not None and folio_resolver.is_per_room(booking):
        db.add(PaymentAllocation(payment_id=pay.payment_id, folio_id=folio.id, amount=amount))
    _recompute(db, folio)
    if round(float(folio.balance or 0), 2) <= 0:
        folio.due_cleared_at = clock.now_utc()
    return pay


def _agent_name(db, agent_id, cache):
    if not agent_id:
        return None
    if agent_id not in cache:
        a = db.query(TravelAgent).filter(TravelAgent.id == agent_id).first()
        cache[agent_id] = a.name if a else f"Agent #{agent_id}"
    return cache[agent_id]


def _rooms(db, folio, booking):
    items = ([db.query(BookingItem).filter(BookingItem.booking_item_id == folio.booking_item_id).first()]
             if folio.booking_item_id else
             db.query(BookingItem).filter(BookingItem.booking_id == booking.booking_id).all())
    nums = []
    for it in items:
        if it is not None and it.room_id:
            r = db.query(Room).filter(Room.room_id == it.room_id).first()
            if r:
                nums.append(r.room_number)
    return ", ".join(nums) or "-"


def _fmt(dt):
    loc = clock.to_local(dt) if dt else None
    return loc.strftime("%d/%m/%y %H:%M") if loc else ""


def row(db, folio, _cache=None):
    """One pay-later bill, as the Dues page / exports show it."""
    _cache = {} if _cache is None else _cache
    b = db.query(Booking).filter(Booking.booking_id == folio.booking_id).first()
    g = db.query(Guest).filter(Guest.guest_id == b.guest_id).first() if b else None
    inv = db.query(Invoice).filter(Invoice.folio_id == folio.id).first()
    pays = []
    for p in (db.query(Payment).filter(Payment.booking_id == folio.booking_id, Payment.status == "paid")
              .order_by(Payment.created_at).all()):
        if due_payment_folio_id(p) == folio.id:
            u = db.query(User).filter(User.user_id == p.collected_by).first() if p.collected_by else None
            pays.append({"receipt_no": f"RCPT-{p.payment_id}", "payment_id": p.payment_id, "at": _fmt(p.created_at),
                         "amount": round(float(p.amount or 0), 2), "method": p.method,
                         "reference": p.payment_id_gateway or "",
                         "by": (u.full_name or u.username) if u else ""})
    owed = outstanding(folio)
    marked = clock.to_local(folio.due_marked_at) if folio.due_marked_at else None
    days = (clock.now_local().date() - marked.date()).days if marked else 0
    by = db.query(User).filter(User.user_id == folio.due_marked_by).first() if folio.due_marked_by else None
    return {
        "folio_id": folio.id, "booking_id": folio.booking_id,
        "invoice_no": inv.invoice_no if inv else f"F{folio.id}",
        "guest": (b.display_guest_name if b else None) or (g.name if g else ""),
        "phone": g.phone if g else "",
        "rooms": _rooms(db, folio, b) if b else "-",
        "agent_id": b.agent_id if b else None,
        "agent": _agent_name(db, b.agent_id if b else None, _cache) or "-",
        "checked_out_at": _fmt(folio.settled_at or folio.due_marked_at),
        "marked_by": (by.full_name or by.username) if by else "",
        "bill_amount": round(float(folio.total or 0), 2),
        "due_amount": round(float(folio.due_amount or 0), 2),
        "received": round(float(sum(x["amount"] for x in pays)), 2),
        "outstanding": owed,
        "days_pending": days if owed > 0 else 0,
        "status": "due" if owed > 0 else "paid",
        "paid_at": _fmt(folio.due_cleared_at) if owed <= 0 else "",
        "payments": pays,
    }


def list_dues(db, status="open", agent_id=None, q=None):
    folios = (db.query(Folio).filter(Folio.due_amount.isnot(None), Folio.due_amount > 0)
              .order_by(Folio.due_marked_at.desc().nullslast(), Folio.id.desc()).all())
    cache, rows = {}, []
    for f in folios:
        r = row(db, f, cache)
        if status == "open" and r["status"] != "due":
            continue
        if status == "paid" and r["status"] != "paid":
            continue
        if agent_id and r["agent_id"] != agent_id:
            continue
        if q:
            ql = q.strip().lower()
            hay = " ".join(str(r[k] or "") for k in ("invoice_no", "guest", "phone", "rooms", "agent", "booking_id")).lower()
            if ql not in hay:
                continue
        rows.append(r)
    per_agent = {}
    for r in rows:
        a = per_agent.setdefault(r["agent_id"] or 0, {"agent_id": r["agent_id"], "agent": r["agent"], "bills": 0,
                                                       "due_amount": 0.0, "received": 0.0, "outstanding": 0.0})
        a["bills"] += 1
        for k in ("due_amount", "received", "outstanding"):
            a[k] = round(float(a[k] + r[k]), 2)
    return {"rows": rows,
            "agents": sorted(per_agent.values(), key=lambda a: -a["outstanding"]),
            "totals": {k: round(float(sum(r[k] for r in rows)), 2) for k in ("due_amount", "received", "outstanding")},
            "count": len(rows)}
