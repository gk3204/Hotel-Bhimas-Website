"""Guest extras / in-room QR portal (prompt 18d, slice 10).

An in-house guest scans a QR in their room and opens a PUBLIC, token-scoped portal (no login) to
order room service, get a WiFi voucher, schedule a wake-up call, request a cab, or start a
contactless checkout. Reception fulfils these from the desk board.

Reuse, not rebuild:
  * Token mechanics copy the pre-arrival page (`routers/crm.py`): urlsafe token, 404/410 expiry
    guard, `client="guest"` audit, public by simply omitting any `Depends(require_*)`. But the
    portal token is LONG-LIVED (whole stay) and MULTI-action — each action re-validates the booking
    is still `checked_in`, and the token never locks on first use.
  * Room-service orders post to the folio ONLY when the desk fulfils them (no charge without staff —
    the anti-fraud rule), reusing the folio charge seam (`routers.folio._recompute`) + the stock
    consume seam (`routers.stock.record_movement`).
  * Contactless checkout raises a `checkout` REQUEST; the desk finishes the existing staff checkout.
  * In-room QR is generated locally with `segno` (already a dependency — 2FA/desk-pay use it).

Route order matters: every literal desk/admin path (/session, /requests, /menu-items, /config) is
declared BEFORE the public `/{token}` routes so FastAPI matches the literal first.
"""
import io
import json
import logging
import os
import secrets
from datetime import datetime, timedelta, time as _dtime
from decimal import Decimal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from database import SessionLocal
from models import (Booking, Folio, FolioCharge, Guest, GuestPortalSession, GuestRequest, MenuItem,
                    Room)
from schemas import (CabRequest, CheckoutRequestBody, MenuAvailabilityUpdate, MenuItemCreate,
                     MenuItemUpdate, PortalConfigUpdate, PortalSessionRequest, RequestActionRequest,
                     RoomServiceOrder, WakeupRequest, WifiRequestBody)
from utils import settings as app_settings
from utils.audit import _resolve_user_id, write_audit
from utils.auth_utils import require_admin, require_reception_or_admin

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/portal", tags=["Guest portal"])

REQUEST_TYPES = ("room_service", "wifi", "wakeup", "cab", "checkout")
OPEN_REQUEST_STATUSES = ("requested", "acknowledged")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------------------------------------------------------------- helpers

def _public_base_url() -> str:
    return os.getenv("PUBLIC_SITE_URL", "https://hotelbhimas.in").rstrip("/")


def _portal_url(token: str) -> str:
    return f"{_public_base_url()}/portal/{token}"


def _room_number(db: Session, room_id) -> str | None:
    if not room_id:
        return None
    r = db.query(Room).filter(Room.room_id == room_id).first()
    return r.room_number if r else None


def _get_or_create_session(db: Session, booking: Booking, user=None) -> GuestPortalSession:
    """One active portal session per booking. TTL = the stay's checkout moment (+ a day's grace)."""
    s = db.query(GuestPortalSession).filter(
        GuestPortalSession.booking_id == booking.booking_id,
        GuestPortalSession.status == "active").first()
    if s:
        return s
    from routers.reception import booking_checkout_moment
    try:
        expires = booking_checkout_moment(booking) + timedelta(hours=24)
    except Exception:
        expires = datetime.utcnow() + timedelta(days=3)
    room_id = None
    bi = booking.booking_items[0] if booking.booking_items else None
    if bi:
        room_id = bi.room_id
    s = GuestPortalSession(
        token=secrets.token_urlsafe(24), booking_id=booking.booking_id,
        room_id=room_id, guest_id=booking.guest_id, status="active",
        expires_at=expires, created_by=_resolve_user_id(db, user))
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


def _resolve_token(db: Session, token: str, *, for_action: bool = True):
    """Resolve a portal token → (session, booking). 404 unknown, 410 expired/closed, 409 if the
    stay is no longer checked in (for actions)."""
    s = db.query(GuestPortalSession).filter(GuestPortalSession.token == token).first()
    if not s:
        raise HTTPException(status_code=404, detail="This link is not valid.")
    if s.status != "active" or (s.expires_at and s.expires_at < datetime.utcnow()):
        raise HTTPException(status_code=410, detail="This link has expired. Please contact the front desk.")
    booking = db.query(Booking).filter(Booking.booking_id == s.booking_id).first()
    if not booking:
        raise HTTPException(status_code=404, detail="Stay not found")
    if for_action and booking.status != "checked_in":
        raise HTTPException(status_code=409, detail="This stay is not currently checked in.")
    return s, booking


def _wifi_code(session: GuestPortalSession) -> str:
    """A stable per-stay voucher code (auto mode) derived from the token — no code table needed."""
    return session.token[:8].upper()


_IST = ZoneInfo("Asia/Kolkata")


def _parse_windows(raw) -> list:
    """menu_items.available_windows JSON text -> [{"start","end"}, ...]. Tolerant of junk/None."""
    if not raw:
        return []
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
        out = []
        for w in (data or []):
            s, e = (w.get("start"), w.get("end")) if isinstance(w, dict) else (w[0], w[1])
            if s and e:
                out.append({"start": str(s), "end": str(e)})
        return out
    except Exception:
        return []


def _in_window(now_t: _dtime, start: str, end: str) -> bool:
    """Is now_t within [start, end)? Handles a window that crosses midnight (start > end)."""
    try:
        sh, sm = (int(x) for x in start.split(":"))
        eh, em = (int(x) for x in end.split(":"))
    except Exception:
        return False
    s, e = _dtime(sh, sm), _dtime(eh, em)
    if s <= e:
        return s <= now_t < e
    return now_t >= s or now_t < e   # crosses midnight


def _orderable_now(m: MenuItem, now_local: datetime | None = None) -> bool:
    """True when the guest can order this item right now: available AND (all-day OR inside a window)."""
    if not m.is_available:
        return False
    windows = _parse_windows(getattr(m, "available_windows", None))
    if not windows:
        return True
    now_t = (now_local or datetime.now(_IST)).time()
    return any(_in_window(now_t, w["start"], w["end"]) for w in windows)


def _menu_dict(m: MenuItem) -> dict:
    return {
        "id": m.id, "name": m.name, "description": m.description, "category": m.category,
        "price": float(m.price or 0), "gst_percent": float(m.gst_percent) if m.gst_percent is not None else None,
        "is_available": bool(m.is_available), "sort_order": m.sort_order,
        "stock_item_id": m.stock_item_id,
        "available_windows": _parse_windows(getattr(m, "available_windows", None)),
        "orderable_now": _orderable_now(m),
    }


def _request_dict(db: Session, r: GuestRequest, with_room: bool = False) -> dict:
    payload = json.loads(r.payload) if r.payload else None
    d = {
        "id": r.id, "booking_id": r.booking_id, "room_id": r.room_id,
        "type": r.type, "status": r.status, "payload": payload, "note": r.note,
        "amount": float(r.amount) if r.amount is not None else None,
        "folio_charge_id": r.folio_charge_id, "source": r.source,
        "created_at": str(r.created_at) if r.created_at else None,
        "updated_at": str(r.updated_at) if r.updated_at else None,
    }
    if with_room:
        d["room_number"] = _room_number(db, r.room_id)
        g = db.query(Guest).filter(Guest.guest_id == r.guest_id).first() if r.guest_id else None
        d["guest_name"] = g.name if g else None
    return d


def _create_request(db: Session, session: GuestPortalSession, booking: Booking, rtype: str,
                    payload=None, amount=None, note=None, source="portal", client_ref=None,
                    status="requested", user=None) -> GuestRequest:
    if client_ref:
        dup = db.query(GuestRequest).filter(GuestRequest.client_ref == client_ref).first()
        if dup:
            return dup
    r = GuestRequest(
        booking_id=booking.booking_id, room_id=session.room_id, guest_id=booking.guest_id,
        type=rtype, status=status, payload=json.dumps(payload) if payload is not None else None,
        amount=Decimal(str(round(amount, 2))) if amount is not None else None, note=note,
        source=source, client_ref=client_ref, handled_by=_resolve_user_id(db, user),
    )
    db.add(r)
    db.commit()
    db.refresh(r)
    return r


@router.get("/health", dependencies=[Depends(require_reception_or_admin)])
def health():
    return {"status": "ok", "module": "portal"}


# ================================================================
# DESK endpoints (reception + admin) — declared BEFORE /{token}
# ================================================================

@router.post("/session")
def mint_session(data: PortalSessionRequest, db: Session = Depends(get_db),
                 user=Depends(require_reception_or_admin)):
    """Get-or-create the in-room portal session (QR link) for a checked-in booking."""
    booking = db.query(Booking).filter(Booking.booking_id == data.booking_id).first()
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")
    if booking.status != "checked_in":
        raise HTTPException(status_code=409, detail="Only a checked-in stay has an in-room portal")
    s = _get_or_create_session(db, booking, user)
    return {"token": s.token, "link": _portal_url(s.token),
            "room_number": _room_number(db, s.room_id),
            "expires_at": str(s.expires_at) if s.expires_at else None}


@router.get("/session/{booking_id}/qr.png")
def session_qr(booking_id: int, db: Session = Depends(get_db),
               user=Depends(require_reception_or_admin)):
    """PNG QR of the guest portal link for this booking (generated locally with segno)."""
    booking = db.query(Booking).filter(Booking.booking_id == booking_id).first()
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")
    if booking.status != "checked_in":
        raise HTTPException(status_code=409, detail="Only a checked-in stay has an in-room portal")
    s = _get_or_create_session(db, booking, user)
    try:
        import segno
        buf = io.BytesIO()
        segno.make(_portal_url(s.token), error="m").save(buf, kind="png", scale=6, border=3)
        return Response(content=buf.getvalue(), media_type="image/png")
    except Exception as e:
        logger.error(f"portal QR generation failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to render the QR")


@router.post("/session/{booking_id}/send-link")
def send_session_link(booking_id: int, db: Session = Depends(get_db),
                      user=Depends(require_reception_or_admin)):
    """Best-effort: WhatsApp the portal link to the guest (stub-logged until WHATSAPP_* is set)."""
    booking = db.query(Booking).filter(Booking.booking_id == booking_id).first()
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")
    if booking.status != "checked_in":
        raise HTTPException(status_code=409, detail="Only a checked-in stay has an in-room portal")
    s = _get_or_create_session(db, booking, user)
    guest = db.query(Guest).filter(Guest.guest_id == booking.guest_id).first()
    sent = False
    if guest and guest.phone:
        try:
            from utils import whatsapp_service as wa
            row = wa.send_template(db, guest.phone, "portal_link",
                                   {"guest_name": guest.name or "Guest",
                                    "room_number": _room_number(db, s.room_id) or "-",
                                    "link": _portal_url(s.token)},
                                   guest_id=guest.guest_id, booking_id=booking.booking_id,
                                   client_ref=f"portal_link:{s.token}", respect_optout=False)
            sent = row is not None
        except Exception as e:
            logger.error(f"portal link send failed: {e}")
    return {"sent": sent, "link": _portal_url(s.token)}


@router.get("/requests")
def list_requests(status: str | None = Query(None), type: str | None = Query(None),
                  open_only: bool = Query(False), db: Session = Depends(get_db),
                  user=Depends(require_reception_or_admin)):
    """Guest requests for the desk board — newest first, with room + guest context."""
    query = db.query(GuestRequest)
    if status:
        query = query.filter(GuestRequest.status == status)
    if type:
        query = query.filter(GuestRequest.type == type)
    if open_only:
        query = query.filter(GuestRequest.status.in_(OPEN_REQUEST_STATUSES))
    rows = query.order_by(GuestRequest.id.desc()).limit(300).all()
    return {"total": len(rows),
            "open_count": sum(1 for r in rows if r.status in OPEN_REQUEST_STATUSES),
            "data": [_request_dict(db, r, with_room=True) for r in rows]}


def _get_request(db: Session, request_id: int) -> GuestRequest:
    r = db.query(GuestRequest).filter(GuestRequest.id == request_id).first()
    if not r:
        raise HTTPException(status_code=404, detail="Request not found")
    return r


@router.post("/requests/{request_id}/ack")
def ack_request(request_id: int, db: Session = Depends(get_db),
                user=Depends(require_reception_or_admin)):
    r = _get_request(db, request_id)
    if r.status in ("completed", "dismissed"):
        raise HTTPException(status_code=409, detail="Request is already closed")
    r.status = "acknowledged"
    r.handled_by = _resolve_user_id(db, user)
    r.updated_at = datetime.utcnow()
    db.commit()
    write_audit(db, user, "portal.request_ack", "guest_request", r.id, client="web", commit=True)
    return _request_dict(db, r, with_room=True)


@router.post("/requests/{request_id}/complete")
def complete_request(request_id: int, data: RequestActionRequest, db: Session = Depends(get_db),
                     user=Depends(require_reception_or_admin)):
    """Fulfil a request. A room-service order posts its folio charges here (no charge without
    staff). WiFi (manual mode) stores the issued code. A checkout request is just cleared — the
    desk finishes the settlement through the normal checkout flow."""
    try:
        r = _get_request(db, request_id)
        if r.status in ("completed", "dismissed"):
            return {**_request_dict(db, r, with_room=True), "duplicate": True}

        # v4b5 (R4): a room-service order cannot be completed once the guest has left. The
        # request was checked against the stay when it was CREATED, but nothing re-checked it
        # here — so a late tap tried to bill a settled folio and failed with an incidental
        # "folio is settled" 409, leaving the order stranded in `acknowledged`.
        if r.type == "room_service" and r.booking_id:
            bk = db.query(Booking).filter(Booking.booking_id == r.booking_id).first()
            if bk and bk.status != "checked_in":
                raise HTTPException(
                    status_code=409,
                    detail="That guest has already checked out — this order can no longer be "
                           "billed. Correct it on the folio instead.")

        if r.type == "room_service":
            _post_room_service_to_folio(db, r, user)
        elif r.type == "wifi" and data.code:
            r.note = f"WiFi code: {data.code.strip()}"

        if data.note:
            r.note = f"{r.note + chr(10) if r.note else ''}{data.note.strip()}"
        r.status = "completed"
        r.handled_by = _resolve_user_id(db, user)
        r.updated_at = datetime.utcnow()
        db.commit()
        write_audit(db, user, "portal.request_complete", "guest_request", r.id,
                    after={"type": r.type, "folio_charge_id": r.folio_charge_id,
                           "amount": float(r.amount) if r.amount is not None else None},
                    client="web", commit=True)
        return {**_request_dict(db, r, with_room=True), "duplicate": False}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"complete_request failed: {e}", exc_info=True)
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to complete the request")


def _post_room_service_to_folio(db: Session, r: GuestRequest, user):
    """Post a room-service order's lines to the stay's open folio.

    The implementation moved to `services/room_service.py` (TBC-4) so this portal path and
    the new tablet/desk "Deliver" path share ONE charge-posting routine — GST slabs, stock
    decrement and idempotency can't drift between the two ways an order gets fulfilled."""
    from services import room_service
    return room_service.post_to_folio(db, r, user)


@router.post("/requests/{request_id}/dismiss")
def dismiss_request(request_id: int, data: RequestActionRequest, db: Session = Depends(get_db),
                    user=Depends(require_reception_or_admin)):
    r = _get_request(db, request_id)
    if r.status == "completed":
        raise HTTPException(status_code=409, detail="A completed request cannot be dismissed")
    r.status = "dismissed"
    if data.note:
        r.note = f"{r.note + chr(10) if r.note else ''}{data.note.strip()}"
    r.handled_by = _resolve_user_id(db, user)
    r.updated_at = datetime.utcnow()
    db.commit()
    write_audit(db, user, "portal.request_dismiss", "guest_request", r.id, client="web", commit=True)
    return _request_dict(db, r, with_room=True)


# ---------------------------------------------------------------- menu (admin) + config

@router.get("/menu-items")
def list_menu(available_only: bool = Query(False), db: Session = Depends(get_db),
              user=Depends(require_reception_or_admin)):
    query = db.query(MenuItem)
    if available_only:
        query = query.filter(MenuItem.is_available == True)  # noqa: E712
    rows = query.order_by(MenuItem.sort_order, MenuItem.name).all()
    return {"total": len(rows), "data": [_menu_dict(m) for m in rows]}


@router.post("/menu-items", dependencies=[Depends(require_admin)])
def create_menu_item(data: MenuItemCreate, db: Session = Depends(get_db), user=Depends(require_admin)):
    try:
        payload = data.model_dump()
        payload["category"] = app_settings.validate_category(db, "menu", data.category)   # editable list (F-A)
        aw = payload.pop("available_windows", None)
        payload["available_windows"] = json.dumps(aw) if aw else None
        item = MenuItem(**payload, created_by=_resolve_user_id(db, user))
        db.add(item)
        db.commit()
        db.refresh(item)
        write_audit(db, user, "portal.menu_create", "menu_item", item.id,
                    after={"name": item.name, "price": float(item.price or 0)}, client="web", commit=True)
        return _menu_dict(item)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"create_menu_item failed: {e}", exc_info=True)
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to create the menu item")


@router.put("/menu-items/{item_id}", dependencies=[Depends(require_admin)])
def update_menu_item(item_id: int, data: MenuItemUpdate, db: Session = Depends(get_db),
                     user=Depends(require_admin)):
    try:
        item = db.query(MenuItem).filter(MenuItem.id == item_id).first()
        if not item:
            raise HTTPException(status_code=404, detail="Menu item not found")
        changes = data.model_dump(exclude_unset=True)
        if "category" in changes and changes["category"] is not None:
            changes["category"] = app_settings.validate_category(db, "menu", changes["category"])
        if "available_windows" in changes:
            aw = changes["available_windows"]
            changes["available_windows"] = json.dumps(aw) if aw else None
        for k, v in changes.items():
            setattr(item, k, v)
        item.updated_by = _resolve_user_id(db, user)
        item.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(item)
        write_audit(db, user, "portal.menu_update", "menu_item", item.id, after=changes,
                    client="web", commit=True)
        return _menu_dict(item)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"update_menu_item failed: {e}", exc_info=True)
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to update the menu item")


@router.delete("/menu-items/{item_id}", dependencies=[Depends(require_admin)])
def deactivate_menu_item(item_id: int, db: Session = Depends(get_db), user=Depends(require_admin)):
    """Mark unavailable, never hard-delete — past orders reference it."""
    item = db.query(MenuItem).filter(MenuItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Menu item not found")
    item.is_available = False
    item.updated_by = _resolve_user_id(db, user)
    item.updated_at = datetime.utcnow()
    db.commit()
    write_audit(db, user, "portal.menu_deactivate", "menu_item", item.id, client="web", commit=True)
    return {"status": "unavailable", "id": item.id, "name": item.name}


@router.patch("/menu-items/{item_id}/availability")
def set_menu_availability(item_id: int, data: MenuAvailabilityUpdate, db: Session = Depends(get_db),
                          user=Depends(require_reception_or_admin)):
    """Reception's quick on/off toggle for a menu item (e.g. kitchen ran out). Only flips
    is_available — price/name/time-windows stay admin-managed in the web menu editor."""
    item = db.query(MenuItem).filter(MenuItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Menu item not found")
    item.is_available = bool(data.is_available)
    item.updated_by = _resolve_user_id(db, user)
    item.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(item)
    write_audit(db, user, "portal.menu_availability", "menu_item", item.id,
                after={"is_available": item.is_available}, client="desktop", commit=True)
    return _menu_dict(item)


@router.get("/config")
def get_config(db: Session = Depends(get_db), user=Depends(require_reception_or_admin)):
    return app_settings.get_portal_config(db)


@router.put("/config", dependencies=[Depends(require_admin)])
def update_config(data: PortalConfigUpdate, db: Session = Depends(get_db), user=Depends(require_admin)):
    changes = data.model_dump(exclude_unset=True)
    applied = {}
    for key, value in changes.items():
        if key in app_settings.PORTAL_EDITABLE_KEYS:
            app_settings.set_setting(db, key, value, user=user)
            applied[key] = value
    db.commit()
    write_audit(db, user, "portal.config_update", "app_settings", None, after=applied,
                client="web", commit=True)
    return app_settings.get_portal_config(db)


# ================================================================
# PUBLIC endpoints (no auth) — token is the only credential
# ================================================================

def _feature_gate(cfg: dict, key: str):
    """Master gate + per-feature toggle. 403 (as a friendly message) when off."""
    if not cfg["enabled"]:
        raise HTTPException(status_code=404, detail="The in-room service is currently unavailable.")
    if not cfg.get(key, True):
        raise HTTPException(status_code=409, detail="This service is currently unavailable.")


@router.get("/{token}")
def portal_home(token: str, db: Session = Depends(get_db)):
    """PUBLIC — the in-room portal landing: stay info, enabled features, menu, WiFi, folio balance,
    and the guest's own requests."""
    cfg = app_settings.get_portal_config(db)
    if not cfg["enabled"]:
        raise HTTPException(status_code=404, detail="The in-room service is currently unavailable.")
    s, booking = _resolve_token(db, token, for_action=False)
    active = booking.status == "checked_in"
    guest = db.query(Guest).filter(Guest.guest_id == booking.guest_id).first()
    folio = db.query(Folio).filter(Folio.booking_id == booking.booking_id).first()
    reqs = db.query(GuestRequest).filter(GuestRequest.booking_id == booking.booking_id).order_by(
        GuestRequest.id.desc()).limit(30).all()
    menu = []
    if cfg["room_service_enabled"]:
        menu = [_menu_dict(m) for m in db.query(MenuItem).filter(
            MenuItem.is_available == True).order_by(MenuItem.sort_order, MenuItem.name).all()]  # noqa: E712
    wifi = None
    if cfg["wifi_enabled"]:
        wifi = {"ssid": cfg["wifi_ssid"],
                "code": _wifi_code(s) if cfg["wifi_voucher_mode"] == "auto" else None,
                "mode": cfg["wifi_voucher_mode"]}
    return {
        "hotel": "Hotel Bhimas",
        "active": active,
        "guest_name": guest.name if guest else None,
        "room_number": _room_number(db, s.room_id),
        "check_out": str(booking.check_out) if booking.check_out else None,
        "features": {
            "room_service": cfg["room_service_enabled"], "wifi": cfg["wifi_enabled"],
            "wakeup": cfg["wakeup_enabled"], "cab": cfg["cab_enabled"],
            "contactless_checkout": cfg["contactless_checkout_enabled"],
        },
        "menu": menu,
        "wifi": wifi,
        "folio": {"balance": float(folio.balance or 0) if folio else 0, "currency": "INR"},
        "requests": [_request_dict(db, r) for r in reqs],
    }


@router.get("/{token}/menu")
def portal_menu(token: str, db: Session = Depends(get_db)):
    """PUBLIC — the room-service menu. Returns ALL items (not just available ones) each with an
    `orderable_now` flag, so the portal can SHOW an item as "Not available" / out-of-window rather
    than silently hiding it. Ordering is still enforced server-side in order_room_service."""
    cfg = app_settings.get_portal_config(db)
    _feature_gate(cfg, "room_service_enabled")
    _resolve_token(db, token, for_action=False)
    rows = db.query(MenuItem).order_by(MenuItem.sort_order, MenuItem.name).all()
    return {"total": len(rows), "data": [_menu_dict(m) for m in rows]}


@router.post("/{token}/room-service")
def order_room_service(token: str, data: RoomServiceOrder, db: Session = Depends(get_db)):
    """PUBLIC — place a room-service order. Creates a request the desk fulfils; NO folio charge is
    posted here (no charge without staff)."""
    cfg = app_settings.get_portal_config(db)
    _feature_gate(cfg, "room_service_enabled")
    s, booking = _resolve_token(db, token)
    # snapshot each line's menu details so the desk posts the right price/GST even if the menu changes
    items, total = [], 0.0
    for ln in data.items:
        m = db.query(MenuItem).filter(MenuItem.id == ln.menu_item_id).first()
        # Reject an item that is off OR outside its time window (covers both cases).
        if not m or not _orderable_now(m):
            raise HTTPException(status_code=404, detail=f"Menu item {ln.menu_item_id} is not available right now")
        price = float(m.price or 0)
        items.append({"menu_item_id": m.id, "name": m.name, "qty": ln.qty,
                      "unit_price": price, "gst_percent": float(m.gst_percent) if m.gst_percent is not None else None,
                      "stock_item_id": m.stock_item_id})
        total += price * ln.qty
    r = _create_request(db, s, booking, "room_service",
                        payload={"items": items, "note": data.note}, amount=round(total, 2),
                        note=data.note, client_ref=data.client_ref)
    write_audit(db, None, "portal.room_service_order", "guest_request", r.id,
                after={"booking_id": booking.booking_id, "amount": round(total, 2)},
                client="guest", commit=True)
    return _request_dict(db, r)


@router.post("/{token}/wifi")
def request_wifi(token: str, data: WifiRequestBody, db: Session = Depends(get_db)):
    """PUBLIC — get the WiFi voucher. Auto mode returns a per-stay code immediately; manual mode
    logs a request for the desk to issue a code."""
    cfg = app_settings.get_portal_config(db)
    _feature_gate(cfg, "wifi_enabled")
    s, booking = _resolve_token(db, token)
    if cfg["wifi_voucher_mode"] == "auto":
        code = _wifi_code(s)
        r = _create_request(db, s, booking, "wifi", payload={"code": code, "ssid": cfg["wifi_ssid"]},
                            note=f"WiFi code: {code}", client_ref=data.client_ref, status="completed")
        return {"ssid": cfg["wifi_ssid"], "code": code, "status": "issued", "request": _request_dict(db, r)}
    r = _create_request(db, s, booking, "wifi", client_ref=data.client_ref)
    return {"ssid": cfg["wifi_ssid"], "code": None, "status": "requested", "request": _request_dict(db, r)}


@router.post("/{token}/wakeup")
def request_wakeup(token: str, data: WakeupRequest, db: Session = Depends(get_db)):
    """PUBLIC — schedule a wake-up call."""
    cfg = app_settings.get_portal_config(db)
    _feature_gate(cfg, "wakeup_enabled")
    s, booking = _resolve_token(db, token)
    r = _create_request(db, s, booking, "wakeup", payload={"time": data.time},
                        note=f"Wake-up at {data.time}" + (f" — {data.note}" if data.note else ""),
                        client_ref=data.client_ref)
    return _request_dict(db, r)


@router.post("/{token}/cab")
def request_cab(token: str, data: CabRequest, db: Session = Depends(get_db)):
    """PUBLIC — request a cab / tour desk pickup."""
    cfg = app_settings.get_portal_config(db)
    _feature_gate(cfg, "cab_enabled")
    s, booking = _resolve_token(db, token)
    r = _create_request(db, s, booking, "cab",
                        payload={"pickup_time": data.pickup_time, "destination": data.destination,
                                 "passengers": data.passengers},
                        note=f"Cab to {data.destination}"
                             + (f" at {data.pickup_time}" if data.pickup_time else "")
                             + (f" — {data.note}" if data.note else ""),
                        client_ref=data.client_ref)
    return _request_dict(db, r)


@router.post("/{token}/checkout")
def request_checkout(token: str, data: CheckoutRequestBody, db: Session = Depends(get_db)):
    """PUBLIC — start a contactless checkout. This raises a REQUEST; the front desk finishes the
    settlement + card return through the normal (staff) checkout — no self-checkout."""
    cfg = app_settings.get_portal_config(db)
    _feature_gate(cfg, "contactless_checkout_enabled")
    s, booking = _resolve_token(db, token)
    folio = db.query(Folio).filter(Folio.booking_id == booking.booking_id).first()
    r = _create_request(db, s, booking, "checkout",
                        payload={"balance": float(folio.balance or 0) if folio else 0},
                        note=data.note, client_ref=data.client_ref)
    write_audit(db, None, "portal.checkout_request", "guest_request", r.id,
                after={"booking_id": booking.booking_id}, client="guest", commit=True)
    return {**_request_dict(db, r),
            "message": "Thanks! Please leave your key card at the desk — we'll bring your bill over."}


@router.get("/{token}/requests")
def portal_requests(token: str, db: Session = Depends(get_db)):
    """PUBLIC — the guest sees their own requests + statuses."""
    s, booking = _resolve_token(db, token, for_action=False)
    rows = db.query(GuestRequest).filter(GuestRequest.booking_id == booking.booking_id).order_by(
        GuestRequest.id.desc()).limit(50).all()
    return {"total": len(rows), "data": [_request_dict(db, r) for r in rows]}
