"""Room service — take an order, print the KOT, deliver it (backlog v2 TBC-4).

The owner's ask: a dedicated room-service login on a tablet that can take an order, send a
docket to the kitchen and hand the guest a printed bill — while the charges still land on
the room's folio like any other extra.

Reuse, not rebuild:
  * An order IS a `GuestRequest` (type='room_service'), the same row the in-room QR portal
    creates. One board therefore shows guest-placed and staff-placed orders together.
  * Prices, GST slabs, stock decrement and the folio posting all live in
    `services/room_service.py`, shared with the portal's "Complete" action.
  * KOT numbers come from the existing `invoice_counters` allocator, scoped per day.

Anti-fraud posture is unchanged: an order is a REQUEST; **charges post only when a
logged-in staff member delivers it**, and the price always comes from the server's menu.
"""
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from database import SessionLocal
from models import GuestRequest, MenuItem
from schemas import DeskRoomServiceOrder
from services import room_service
from utils.audit import write_audit, _resolve_user_id
from utils.auth_utils import require_roomservice

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/room-service", tags=["Room Service"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _get_order(db: Session, order_id: int) -> GuestRequest:
    r = (db.query(GuestRequest)
         .filter(GuestRequest.id == order_id,
                 GuestRequest.type == "room_service").first())
    if not r:
        raise HTTPException(status_code=404, detail="Order not found")
    return r


def _order_dict(db: Session, r: GuestRequest) -> dict:
    from routers.portal import _request_dict
    d = _request_dict(db, r, with_room=True)
    d["kot_no"] = r.kot_no
    d["printed_kot_at"] = r.printed_kot_at.isoformat() if r.printed_kot_at else None
    d["printed_bill_at"] = r.printed_bill_at.isoformat() if r.printed_bill_at else None
    return d


@router.get("/health", dependencies=[Depends(require_roomservice)])
def health():
    return {"status": "ok", "module": "room_service"}


@router.get("/menu")
def list_menu(db: Session = Depends(get_db), user=Depends(require_roomservice)):
    """The orderable menu. Read-only here — editing lives in the admin Guest Portal screen,
    so a room-service login can't change what a dish costs."""
    rows = (db.query(MenuItem)
            .filter(MenuItem.is_available == True)  # noqa: E712
            .order_by(MenuItem.sort_order, MenuItem.name).all())
    return {"items": [{
        "id": m.id, "name": m.name, "description": m.description, "category": m.category,
        "price": float(m.price or 0),
        "gst_percent": float(m.gst_percent) if m.gst_percent is not None else None,
    } for m in rows]}


@router.get("/rooms")
def list_rooms(db: Session = Depends(get_db), user=Depends(require_roomservice)):
    """Occupied rooms to order for. Only checked-in stays appear — anything else has no open
    folio to charge."""
    return {"rooms": room_service.in_house_rooms(db)}


@router.get("/orders")
def list_orders(open_only: bool = Query(True), limit: int = Query(100, ge=1, le=300),
                db: Session = Depends(get_db), user=Depends(require_roomservice)):
    """The order board — guest-placed (portal) and staff-placed together, newest first."""
    q = db.query(GuestRequest).filter(GuestRequest.type == "room_service")
    if open_only:
        q = q.filter(GuestRequest.status.in_(["requested", "acknowledged"]))
    rows = q.order_by(GuestRequest.created_at.desc()).limit(limit).all()
    return {"total": len(rows), "orders": [_order_dict(db, r) for r in rows]}


@router.post("/orders")
def create_order(data: DeskRoomServiceOrder, db: Session = Depends(get_db),
                 user=Depends(require_roomservice)):
    """Staff take an order. Starts `acknowledged` (staff already have it) with a KOT number
    allocated, so the kitchen docket can print immediately. No charge yet — that happens on
    delivery."""
    try:
        r = room_service.create_desk_order(db, data.booking_id, data.items, data.note, user,
                                           client_ref=data.client_ref)
        write_audit(db, user, "room_service.order", "guest_request", r.id,
                    after={"kot_no": r.kot_no, "booking_id": r.booking_id,
                           "amount": float(r.amount or 0)},
                    client="tablet", commit=True)
        return _order_dict(db, r)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"create_order failed: {e}", exc_info=True)
        db.rollback()
        raise HTTPException(status_code=500, detail="Could not place the order")


@router.get("/orders/{order_id}/kot")
def get_kot(order_id: int, mark: bool = Query(True),
            db: Session = Depends(get_db), user=Depends(require_roomservice)):
    """The kitchen docket — what to cook, for which room. Carries no prices. `mark=false`
    fetches it without stamping (used to preview / re-check before a reprint)."""
    r = _get_order(db, order_id)
    room_service.ensure_kot_no(db, r)
    payload = room_service.kot_payload(db, r)
    payload["reprint"] = bool(r.printed_kot_at)
    if mark:
        room_service.mark_printed(db, r, kot=True)
    return payload


@router.post("/orders/{order_id}/deliver")
def deliver_order(order_id: int, db: Session = Depends(get_db),
                  user=Depends(require_roomservice)):
    """Mark an order delivered: post its lines to the room's folio and return the printable
    bill. This is the only point at which a room-service order touches money, and it always
    requires a logged-in staff member."""
    try:
        r = _get_order(db, order_id)
        if r.status in ("completed", "dismissed"):
            # Idempotent — a double-tap on a tablet must not bill twice.
            return {**_order_dict(db, r), "duplicate": True,
                    "bill": room_service.bill_payload(db, r)}

        room_service.post_to_folio(db, r, user)
        r.status = "completed"
        r.handled_by = _resolve_user_id(db, user)
        r.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(r)

        write_audit(db, user, "room_service.deliver", "guest_request", r.id,
                    after={"kot_no": r.kot_no, "folio_charge_id": r.folio_charge_id,
                           "amount": float(r.amount or 0)},
                    client="tablet", commit=True)
        return {**_order_dict(db, r), "duplicate": False,
                "bill": room_service.bill_payload(db, r)}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"deliver_order failed: {e}", exc_info=True)
        db.rollback()
        raise HTTPException(status_code=500, detail="Could not deliver the order")


@router.get("/orders/{order_id}/bill")
def get_bill(order_id: int, mark: bool = Query(False),
             db: Session = Depends(get_db), user=Depends(require_roomservice)):
    """The guest's copy of a delivered order. NOT a tax invoice — the stay's GST invoice at
    checkout is, and these lines are already part of it."""
    r = _get_order(db, order_id)
    payload = room_service.bill_payload(db, r)
    payload["reprint"] = bool(r.printed_bill_at)
    if mark:
        room_service.mark_printed(db, r, bill=True)
    return payload


@router.post("/orders/{order_id}/cancel")
def cancel_order(order_id: int, db: Session = Depends(get_db),
                 user=Depends(require_roomservice)):
    """Cancel an order that hasn't been delivered. A delivered order is NOT cancellable here —
    its charges are on the folio, and reversing those is a void (reason + audit) on the folio
    itself, not a quiet delete."""
    r = _get_order(db, order_id)
    if r.status == "completed":
        raise HTTPException(
            status_code=409,
            detail="That order was already delivered and billed — void the folio line instead")
    r.status = "dismissed"
    r.handled_by = _resolve_user_id(db, user)
    r.updated_at = datetime.utcnow()
    db.commit()
    write_audit(db, user, "room_service.cancel", "guest_request", r.id,
                after={"kot_no": r.kot_no}, client="tablet", commit=True)
    return _order_dict(db, r)
