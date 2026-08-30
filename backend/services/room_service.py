"""Room-service orders: menu snapshot, KOT numbering, and folio posting (backlog v2 TBC-4).

An order is a `GuestRequest` with `type='room_service'` — the same row whether the guest
raised it from the in-room QR portal or staff took it on the tablet / desk. Reusing that
table means one board shows every order regardless of how it arrived, and the existing
portal "Complete" path and the new "Deliver" path share exactly one charge-posting
implementation (this module) rather than drifting apart.

The anti-fraud invariant is unchanged: **an order is a request; charges only post when a
logged-in staff member delivers it.** A guest can never move money on their own bill.
"""
import json
import logging
from datetime import date, datetime
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy.orm import Session

from models import (Booking, BookingItem, Folio, FolioCharge, Guest, GuestRequest,
                    InvoiceCounter, MenuItem, Room, StockItem)
from utils.audit import _resolve_user_id

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------- KOT numbers

def allocate_kot_no(db: Session, when: date | None = None) -> str:
    """Next kitchen-docket number, e.g. `KOT/20260802/007`.

    Reuses the proven `invoice_counters` upsert (the same atomic allocator behind INV/ and
    CINV/) under a per-day scope key, so no second counter table is needed and two orders
    taken at the same moment can't collide. Resets daily — a kitchen calls out "KOT 7",
    not "KOT 41,318".
    """
    day = (when or date.today()).strftime("%Y%m%d")
    # `invoice_counters.fy_label` is VARCHAR(10), so the scope key must fit: "K" + YYYYMMDD
    # is 9 chars. It can't collide with the invoice series, whose labels are "2026-27" and
    # the corporate "C2026-27".
    scope = f"K{day}"
    row = db.query(InvoiceCounter).filter(
        InvoiceCounter.fy_label == scope).with_for_update().first()
    if row is None:
        row = InvoiceCounter(fy_label=scope, last_seq=0)
        db.add(row)
        db.flush()
    row.last_seq = (row.last_seq or 0) + 1
    db.flush()
    return f"KOT/{day}/{row.last_seq:03d}"


# ---------------------------------------------------------------- menu snapshot

def snapshot_items(db: Session, lines) -> tuple[list, float]:
    """Turn `[{menu_item_id, qty}]` into priced order lines.

    The price ALWAYS comes from the server's menu, never from the caller — the guest portal
    relies on this, and the staff tablet gets the same treatment so a tampered request can't
    discount a meal. Also snapshots name/GST so the bill is right even if the menu changes
    between ordering and delivery.
    """
    items, total = [], 0.0
    for ln in lines:
        m = db.query(MenuItem).filter(MenuItem.id == ln.menu_item_id,
                                      MenuItem.is_available == True).first()  # noqa: E712
        if not m:
            raise HTTPException(status_code=404,
                                detail=f"Menu item {ln.menu_item_id} is not available")
        price = float(m.price or 0)
        items.append({
            "menu_item_id": m.id, "name": m.name, "qty": ln.qty,
            "unit_price": price,
            "gst_percent": float(m.gst_percent) if m.gst_percent is not None else None,
            "stock_item_id": m.stock_item_id,
        })
        total += price * ln.qty
    return items, round(total, 2)


# ---------------------------------------------------------------- folio posting

def post_to_folio(db: Session, r: GuestRequest, user):
    """Post an order's lines to the stay's OPEN folio — one FolioCharge per menu line so each
    keeps its own GST slab — and decrement any linked stock. Does not commit; the caller owns
    the transaction. Raises 409 if there is no open folio to bill."""
    folio = db.query(Folio).filter(Folio.booking_id == r.booking_id).first()
    if not folio:
        raise HTTPException(status_code=409, detail="No folio for this stay")
    if folio.status != "open":
        raise HTTPException(status_code=409, detail="Folio is settled — cannot post room service")

    from routers.folio import _recompute
    payload = json.loads(r.payload) if r.payload else {}
    lines = payload.get("items", [])
    if not lines:
        raise HTTPException(status_code=409, detail="This order has no items")

    # v4b6: a fully complimentary stay is charged NOTHING for room service.
    # ⚠️ The stock still moves. The food left the store whether or not anyone paid for it, and
    # an inventory that silently ignores comped orders drifts away from the shelf — that is
    # the easy mistake here. `comp_mode == 'room'` bills extras normally: only the room is free.
    booking = db.query(Booking).filter(Booking.booking_id == r.booking_id).first()
    if booking is not None and getattr(booking, "comp_mode", "none") == "all":
        for ln in lines:
            _consume_stock(db, r, ln, user, charge_id=None)
        r.folio_charge_id = None
        payload["comped"] = True          # bill_payload prints "Complimentary"
        r.payload = json.dumps(payload)
        return folio

    first_charge_id = None
    for ln in lines:
        qty = float(ln.get("qty") or 1)
        unit = float(ln.get("unit_price") or 0)
        charge = FolioCharge(
            folio_id=folio.id, type="food",
            description=f"Room service — {ln.get('name', 'item')}",
            qty=qty, unit_price=unit, amount=round(qty * unit, 2),
            gst_percent=ln.get("gst_percent"), posted_by=_resolve_user_id(db, user),
            # v3 item 4 — the durable charge->dish link the product-wise sales report groups
            # on. This is the ONLY place it is ever set: both the tablet's "deliver" and the
            # portal's "complete" reach the folio through here, so they cannot drift.
            menu_item_id=ln.get("menu_item_id"),
        )
        db.add(charge)
        db.flush()
        first_charge_id = first_charge_id or charge.id
        _consume_stock(db, r, ln, user, charge_id=charge.id)

    _recompute(db, folio)
    r.folio_charge_id = first_charge_id
    return folio


def _consume_stock(db: Session, r: GuestRequest, ln: dict, user, *, charge_id):
    """Decrement the linked stock item, best-effort — a stock hiccup must not lose the charge.

    Extracted in v4b6 so a COMPLIMENTARY order still moves inventory even though it posts no
    folio line (hence `charge_id=None` on that path)."""
    if not ln.get("stock_item_id"):
        return
    qty = float(ln.get("qty") or 1)
    try:
        from routers.stock import record_movement
        item = db.query(StockItem).filter(StockItem.id == ln["stock_item_id"]).first()
        if item is not None:
            record_movement(db, item, "consume", -abs(qty), user=user,
                            reason=f"Room service — order #{r.id}"
                                   + ("" if charge_id else " (complimentary)"),
                            folio_charge_id=charge_id,
                            client_ref=f"portal_rs:{r.id}:{ln.get('menu_item_id')}",
                            commit=False)
    except Exception as e:
        logger.warning(f"room-service stock consume skipped: {e}")


# ---------------------------------------------------------------- printable docs

def _order_lines(r: GuestRequest) -> list:
    payload = json.loads(r.payload) if r.payload else {}
    return payload.get("items", []) or []


def _order_note(r: GuestRequest) -> str | None:
    payload = json.loads(r.payload) if r.payload else {}
    return payload.get("note")


def kot_payload(db: Session, r: GuestRequest) -> dict:
    """What the kitchen docket prints: what to cook, for which room, and nothing else.
    Deliberately carries NO prices — a KOT is a cooking instruction, not a bill."""
    room = db.query(Room).filter(Room.room_id == r.room_id).first() if r.room_id else None
    return {
        "order_id": r.id,
        "kot_no": r.kot_no,
        "room_number": room.room_number if room else None,
        "placed_at": (r.created_at.isoformat() if r.created_at else None),
        "source": r.source,
        "note": _order_note(r),
        "items": [{"name": ln.get("name"), "qty": ln.get("qty")} for ln in _order_lines(r)],
    }


def bill_payload(db: Session, r: GuestRequest) -> dict:
    """What the guest bill prints: the priced order, GST-inclusive, with a note that it has
    been charged to the room. It is NOT a tax invoice — the stay's GST invoice at checkout is,
    and these lines are already part of it."""
    room = db.query(Room).filter(Room.room_id == r.room_id).first() if r.room_id else None
    guest = db.query(Guest).filter(Guest.guest_id == r.guest_id).first() if r.guest_id else None
    lines = []
    total = 0.0
    for ln in _order_lines(r):
        qty = float(ln.get("qty") or 1)
        unit = float(ln.get("unit_price") or 0)
        amount = round(qty * unit, 2)
        total += amount
        lines.append({"name": ln.get("name"), "qty": qty, "unit_price": unit, "amount": amount})
    return {
        "order_id": r.id,
        "kot_no": r.kot_no,
        "booking_id": r.booking_id,
        "room_number": room.room_number if room else None,
        "guest_name": guest.name if guest else None,
        "placed_at": (r.created_at.isoformat() if r.created_at else None),
        "delivered_at": (r.updated_at.isoformat() if r.updated_at else None),
        "note": _order_note(r),
        "items": lines,
        "total": round(total, 2),
        "charged_to_room": True,
    }


# ---------------------------------------------------------------- rooms in house

def in_house_rooms(db: Session) -> list:
    """Occupied rooms with their booking + guest, for the order form's room picker.
    Only checked-in stays can be charged, so only they are offered."""
    rows = []
    q = (db.query(Booking, BookingItem, Room, Guest)
         .join(BookingItem, BookingItem.booking_id == Booking.booking_id)
         .join(Room, Room.room_id == BookingItem.room_id)
         .outerjoin(Guest, Guest.guest_id == Booking.guest_id)
         .filter(Booking.status == "checked_in", BookingItem.room_id.isnot(None))
         .order_by(Room.room_number))
    for booking, _item, room, guest in q.all():
        folio = db.query(Folio).filter(Folio.booking_id == booking.booking_id).first()
        rows.append({
            "booking_id": booking.booking_id,
            "room_id": room.room_id,
            "room_number": room.room_number,
            "guest_name": guest.name if guest else None,
            "folio_open": bool(folio and folio.status == "open"),
        })
    return rows


def create_desk_order(db: Session, booking_id: int, items, note, user,
                      client_ref=None) -> GuestRequest:
    """Staff take an order on the tablet / desk. Same row shape as a portal order, so it
    lands on the same board — but it starts `acknowledged` (staff already have it) and gets
    its KOT number immediately so the kitchen docket can print."""
    if client_ref:
        dup = db.query(GuestRequest).filter(GuestRequest.client_ref == client_ref).first()
        if dup:
            return dup

    booking = db.query(Booking).filter(Booking.booking_id == booking_id).first()
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")
    if booking.status != "checked_in":
        raise HTTPException(status_code=409, detail="That guest is not checked in")

    room_id = next((bi.room_id for bi in booking.booking_items if bi.room_id), None)
    snapshot, total = snapshot_items(db, items)

    r = GuestRequest(
        booking_id=booking.booking_id, room_id=room_id, guest_id=booking.guest_id,
        type="room_service", status="acknowledged",
        payload=json.dumps({"items": snapshot, "note": note}),
        amount=Decimal(str(total)), source="desk", client_ref=client_ref,
        handled_by=_resolve_user_id(db, user),
        kot_no=allocate_kot_no(db),
    )
    db.add(r)
    db.commit()
    db.refresh(r)
    return r


def ensure_kot_no(db: Session, r: GuestRequest) -> str:
    """Portal orders are created before this module sees them, so give one a KOT number the
    first time a docket is asked for. Idempotent."""
    if not r.kot_no:
        r.kot_no = allocate_kot_no(db)
        db.commit()
        db.refresh(r)
    return r.kot_no


def mark_printed(db: Session, r: GuestRequest, *, kot: bool = False, bill: bool = False):
    now = datetime.utcnow()
    if kot:
        r.printed_kot_at = now
    if bill:
        r.printed_bill_at = now
    db.commit()
