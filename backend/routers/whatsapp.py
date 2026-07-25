"""WhatsApp & automated alerts (prompt 15).

Admin console + automation control + the Meta inbound webhook:
  - GET/PUT /whatsapp/config      -- automation toggles, timing, owner recipient, review link (admin)
  - GET  /whatsapp/messages       -- delivery log with filters (admin)
  - GET  /whatsapp/templates      -- the pre-approved template catalog (admin)
  - opt-out list CRUD             -- GET/POST /whatsapp/opt-outs, DELETE /whatsapp/opt-outs/{phone}
  - POST /whatsapp/send-test      -- send one template for manual verification (admin)
  - POST /whatsapp/jobs/run       -- run the automation sweep inline (admin; mirrors /fraud/reconcile)
  - GET/POST /whatsapp/webhook    -- PUBLIC: Meta verification + inbound replies / delivery callbacks.
    A complaint keyword/reply creates a source='guest' ticket (prompt 13) + acknowledges, and the
    open complaint suppresses review requests until it's resolved.

The message provider is OFF by default (stub/log) until WHATSAPP_* env is set — see whatsapp_service.
"""
import json
import logging
import os

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from database import SessionLocal
from models import WhatsAppMessage, Booking
from schemas import WhatsAppConfigUpdate, OptOutCreate, WhatsAppTestSend
from utils.auth_utils import require_admin
from utils.audit import write_audit
from utils import settings as app_settings
from utils import whatsapp_service as wa
from utils.whatsapp_templates import catalog, get_template
from utils.whatsapp_jobs import run_whatsapp_jobs

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/whatsapp", tags=["WhatsApp"])

# Inbound keyword routing.
_OPTOUT_WORDS = {"stop", "unsubscribe", "optout", "opt-out"}
_COMPLAINT_WORDS = {"problem", "complaint", "complain", "issue", "bad", "unhappy"}


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# --------------------------------------------------------------------- config
@router.get("/config")
def get_config_ep(db: Session = Depends(get_db), user=Depends(require_admin)):
    """Automation config + read-only provider status (which driver is live, no secrets)."""
    cfg = app_settings.get_whatsapp_config(db)
    cfg["provider_status"] = wa.provider_status()
    return cfg


_CONFIG_KEYS = {
    "checkout_reminder_enabled": app_settings.WA_CHECKOUT_REMINDER_KEY,
    "checkout_reminder_lead_hours": app_settings.WA_CHECKOUT_LEAD_HOURS_KEY,
    "overstay_enabled": app_settings.WA_OVERSTAY_KEY,
    "confirmation_enabled": app_settings.WA_CONFIRMATION_KEY,
    "receipt_enabled": app_settings.WA_RECEIPT_KEY,
    "room_ready_enabled": app_settings.WA_ROOM_READY_KEY,
    "review_enabled": app_settings.WA_REVIEW_KEY,
    "review_delay_hours": app_settings.WA_REVIEW_DELAY_HOURS_KEY,
    "owner_alerts_enabled": app_settings.WA_OWNER_ALERTS_KEY,
    "owner_whatsapp": app_settings.WA_OWNER_NUMBER_KEY,
    "google_review_url": app_settings.WA_REVIEW_URL_KEY,
    "job_interval_minutes": app_settings.WA_JOB_INTERVAL_KEY,
    "daily_digest_hour": app_settings.WA_DIGEST_HOUR_KEY,
}


@router.put("/config")
def update_config_ep(data: WhatsAppConfigUpdate, db: Session = Depends(get_db),
                     user=Depends(require_admin)):
    """Admin edits automation toggles / timing / owner number at runtime (no redeploy)."""
    before = app_settings.get_whatsapp_config(db)
    payload = data.model_dump(exclude_none=True)
    for field, value in payload.items():
        key = _CONFIG_KEYS.get(field)
        if not key:
            continue
        if isinstance(value, bool):
            value = "true" if value else "false"
        app_settings.set_setting(db, key, value, user)
    db.commit()
    after = app_settings.get_whatsapp_config(db)
    write_audit(db, user, "settings.whatsapp_update", "app_settings", None,
                before=before, after=after, client="web", commit=True)
    after["provider_status"] = wa.provider_status()
    return after


# --------------------------------------------------------------------- templates
@router.get("/templates")
def list_templates_ep(db: Session = Depends(get_db), user=Depends(require_admin)):
    """The template catalog (name, category, sample body, opt-out policy)."""
    return {"templates": catalog(), "provider_status": wa.provider_status()}


# --------------------------------------------------------------------- message log
def _msg_dict(m: WhatsAppMessage) -> dict:
    return {
        "id": m.id, "direction": m.direction, "to": m.to_number, "template": m.template,
        "status": m.status, "provider": m.provider, "provider_id": m.provider_id,
        "error": m.error, "body": m.body, "guest_id": m.guest_id, "booking_id": m.booking_id,
        "created_at": m.created_at.isoformat() if m.created_at else None,
    }


@router.get("/messages")
def list_messages_ep(status: str = Query(None), direction: str = Query(None),
                     limit: int = Query(100, ge=1, le=500),
                     db: Session = Depends(get_db), user=Depends(require_admin)):
    """Delivery log with optional status/direction filters (newest first)."""
    q = db.query(WhatsAppMessage)
    if status:
        q = q.filter(WhatsAppMessage.status == status)
    if direction:
        q = q.filter(WhatsAppMessage.direction == direction)
    rows = q.order_by(WhatsAppMessage.id.desc()).limit(limit).all()
    return {"data": [_msg_dict(m) for m in rows], "count": len(rows)}


# --------------------------------------------------------------------- opt-outs
@router.get("/opt-outs")
def list_optouts_ep(db: Session = Depends(get_db), user=Depends(require_admin)):
    rows = wa.list_optouts(db)
    return {"data": [{"phone": r.phone, "reason": r.reason, "source": r.source,
                      "created_at": r.created_at.isoformat() if r.created_at else None}
                     for r in rows]}


@router.post("/opt-outs")
def add_optout_ep(data: OptOutCreate, db: Session = Depends(get_db), user=Depends(require_admin)):
    row = wa.set_optout(db, data.phone, source="admin", reason=data.reason)
    if row is None:
        raise HTTPException(status_code=400, detail="Invalid phone number")
    write_audit(db, user, "whatsapp.optout_add", "whatsapp_optout", None,
                after={"phone": row.phone}, client="web", commit=True)
    return {"phone": row.phone, "reason": row.reason, "source": row.source}


@router.delete("/opt-outs/{phone}")
def remove_optout_ep(phone: str, db: Session = Depends(get_db), user=Depends(require_admin)):
    ok = wa.remove_optout(db, phone)
    if not ok:
        raise HTTPException(status_code=404, detail="Not on the opt-out list")
    write_audit(db, user, "whatsapp.optout_remove", "whatsapp_optout", None,
                after={"phone": phone}, client="web", commit=True)
    return {"removed": True, "phone": phone}


# --------------------------------------------------------------------- manual send / run
@router.post("/send-test")
def send_test_ep(data: WhatsAppTestSend, db: Session = Depends(get_db), user=Depends(require_admin)):
    """Send one template to a number for verification (respects opt-out per the catalog)."""
    if not get_template(data.template):
        raise HTTPException(status_code=400, detail=f"Unknown template '{data.template}'")
    row = wa.send_template(db, data.to, data.template, data.params or {})
    if row is None:
        raise HTTPException(status_code=502, detail="Send failed unexpectedly")
    return _msg_dict(row)


@router.post("/jobs/run")
def run_jobs_ep(db: Session = Depends(get_db), user=Depends(require_admin)):
    """Run the automation sweep inline (the 'automatic' fallback + a test hook)."""
    return {"ran": True, "result": run_whatsapp_jobs(db)}


# --------------------------------------------------------------------- webhook (PUBLIC)
@router.get("/webhook")
def verify_webhook(request: Request):
    """Meta webhook verification handshake (echo hub.challenge when the verify token matches)."""
    params = request.query_params
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")
    expected = os.getenv("WHATSAPP_VERIFY_TOKEN")
    if mode == "subscribe" and expected and token == expected:
        return PlainTextResponse(challenge or "")
    raise HTTPException(status_code=403, detail="verification failed")


def _latest_booking_for_guest(db, guest_id):
    return (db.query(Booking)
            .filter(Booking.guest_id == guest_id)
            .order_by(Booking.booking_id.desc()).first())


def _handle_inbound_text(db, from_number, text):
    """Route an inbound reply: opt-out keyword, or complaint -> ticket + acknowledgement."""
    from routers.crm import match_guest
    from routers.maintenance import create_guest_ticket

    lowered = (text or "").strip().lower()
    wa.log_inbound(db, from_number, text)

    # Opt-out keywords.
    if lowered in _OPTOUT_WORDS:
        wa.set_optout(db, from_number, source="guest", reason="replied STOP")
        return

    # Complaint keywords / link -> raise a guest ticket + acknowledge.
    if any(w in lowered for w in _COMPLAINT_WORDS):
        norm = wa.normalize_number(from_number)
        # match by the exact stored phone; try both normalized and last-10-digit forms.
        guest = match_guest(db, norm)
        if guest is None and norm and len(norm) > 10:
            guest = match_guest(db, norm[-10:])
        booking = _latest_booking_for_guest(db, guest.guest_id) if guest else None
        booking_id = booking.booking_id if booking else None
        ticket = create_guest_ticket(
            db, issue=f"Guest WhatsApp complaint: {text[:400]}",
            booking_id=booking_id,
            client_ref=f"wa_complaint:{norm}:{booking_id}" if booking_id else None,
            commit=True)
        if guest and guest.phone:
            wa.send_template(db, guest.phone, "complaint_ack", {"guest_name": guest.name},
                             guest_id=guest.guest_id, booking_id=booking_id,
                             respect_optout=False)
        logger.info(f"WhatsApp complaint -> ticket #{ticket.id} (guest={guest.guest_id if guest else None})")


@router.post("/webhook")
async def receive_webhook(request: Request, db: Session = Depends(get_db)):
    """Inbound Meta events: delivery statuses -> update the log; inbound messages -> route.
    Always returns 200 so Meta doesn't retry-storm; errors are logged."""
    try:
        payload = await request.json()
    except Exception:
        return {"received": True}
    try:
        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {}) or {}
                for st in value.get("statuses", []) or []:
                    wa.update_delivery_status(db, st.get("id"), st.get("status"))
                for msg in value.get("messages", []) or []:
                    if msg.get("type") == "text":
                        _handle_inbound_text(db, msg.get("from"),
                                             (msg.get("text") or {}).get("body", ""))
                    else:
                        wa.log_inbound(db, msg.get("from"), f"[{msg.get('type')}]", msg.get("id"))
    except Exception as e:
        logger.error(f"whatsapp webhook processing error: {e}")
    return {"received": True}
