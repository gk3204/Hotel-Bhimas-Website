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
    open complaint suppresses review requests until it's resolved. An EXTEND reply (promised by
    checkout_reminder) is acknowledged and left in the inbox for the desk. Quick-reply BUTTON
    taps are routed through the same keyword matcher as typed text -- see _inbound_text_of.

The message provider is OFF by default (stub/log) until WHATSAPP_* env is set — see whatsapp_service.
"""
import json
import logging
import os
import re
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from database import SessionLocal
from models import WhatsAppMessage, WhatsAppConversationRead, Booking, Guest
from schemas import WhatsAppConfigUpdate, OptOutCreate, WhatsAppTestSend, WhatsAppReplyRequest
from utils.auth_utils import require_admin, require_reception_or_admin
from utils.audit import write_audit, _resolve_user_id
from utils import settings as app_settings
from utils import whatsapp_service as wa
from utils.whatsapp_templates import catalog, get_template, fill_sample_params
from utils.whatsapp_jobs import run_whatsapp_jobs

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/whatsapp", tags=["WhatsApp"])

# Inbound keyword routing. Every rule below is matched against _normalize_keyword_text()
# output -- lowercased, punctuation/emoji stripped -- never against the raw message, so
# "STOP.", "Stop 🙏" and a button labelled "Stop promotions" all land the same way.
_OPTOUT_WORDS = {
    "stop", "unsubscribe", "optout", "opt out",
    "stop promotions", "stop promotion", "stop messages", "stop messaging",
    "unsubscribe me", "remove me", "no more messages",
}
# A message is also an opt-out when it OPENS with one of these AND is at most two words long.
# The word cap is the safety valve: it accepts "Stop promotions" and "unsubscribe me" while
# leaving "can you stop the AC" and "please stop by room 204" alone.
_OPTOUT_LEAD = {"stop", "unsubscribe", "optout"}
_OPTOUT_MAX_WORDS = 2

# Word-boundary, NOT substring. The old `any(w in lowered ...)` test matched "issue" inside
# "tissue", so "can I get more tissues" silently opened a complaint ticket and fired
# complaint_ack at the guest.
_COMPLAINT_RE = re.compile(
    r"\b(problem|problems|complaint|complaints|complain|issue|issues|bad|unhappy)\b")
# checkout_reminder invites "Reply EXTEND" -- see _handle_extend_request.
_EXTEND_RE = re.compile(r"\bextend\b")

_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)
_WS_RE = re.compile(r"\s+")


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
    "portal_link_enabled": app_settings.WA_PORTAL_LINK_KEY,
    "review_enabled": app_settings.WA_REVIEW_KEY,
    "review_delay_hours": app_settings.WA_REVIEW_DELAY_HOURS_KEY,
    "owner_alerts_enabled": app_settings.WA_OWNER_ALERTS_KEY,
    "owner_whatsapp": app_settings.WA_OWNER_NUMBER_KEY,
    "owner_email": app_settings.OWNER_EMAIL_KEY,      # email fallback (FE-11)
    "google_review_url": app_settings.WA_REVIEW_URL_KEY,
    "job_interval_minutes": app_settings.WA_JOB_INTERVAL_KEY,
    "daily_digest_hour": app_settings.WA_DIGEST_HOUR_KEY,
    "weekly_digest_enabled": app_settings.WA_WEEKLY_DIGEST_KEY,
    "weekly_digest_weekday": app_settings.WA_WEEKLY_WEEKDAY_KEY,
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
        "sent_by": m.sent_by,
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


# --------------------------------------------------------------------- inbox (conversations)
# Reception + admin: the front desk answers guest WhatsApp messages here.
def _guest_name(db, num, guest_id):
    """Name for a conversation: the linked guest, else a phone match (exact / last-10), else None."""
    if guest_id:
        g = db.query(Guest).filter(Guest.guest_id == guest_id).first()
        if g:
            return g.name
    from routers.crm import match_guest
    g = match_guest(db, num)
    if g is None and num and len(num) > 10:
        g = match_guest(db, num[-10:])
    return g.name if g else None


def _conversation_numbers(db):
    """Numbers that are real staff conversations: any inbound, OR any staff-sent outbound
    (sent_by set). Automated one-way blasts (OTP/confirmations) never appear on their own."""
    nums = {r[0] for r in db.query(WhatsAppMessage.to_number)
            .filter(WhatsAppMessage.direction == "in").distinct()}
    nums |= {r[0] for r in db.query(WhatsAppMessage.to_number)
             .filter(WhatsAppMessage.direction == "out",
                     WhatsAppMessage.sent_by.isnot(None)).distinct()}
    return [n for n in nums if n]


@router.get("/conversations")
def list_conversations_ep(db: Session = Depends(get_db), user=Depends(require_reception_or_admin)):
    """Conversation list: one row per customer number, newest activity first, with unread counts."""
    nums = _conversation_numbers(db)
    if not nums:
        return {"data": []}
    last_ids = dict(db.query(WhatsAppMessage.to_number, func.max(WhatsAppMessage.id))
                    .filter(WhatsAppMessage.to_number.in_(nums))
                    .group_by(WhatsAppMessage.to_number).all())
    last_rows = {m.to_number: m for m in db.query(WhatsAppMessage)
                 .filter(WhatsAppMessage.id.in_(list(last_ids.values()))).all()}
    unread = dict(db.query(WhatsAppMessage.to_number, func.count(WhatsAppMessage.id))
                  .outerjoin(WhatsAppConversationRead,
                             WhatsAppConversationRead.phone == WhatsAppMessage.to_number)
                  .filter(WhatsAppMessage.direction == "in",
                          WhatsAppMessage.to_number.in_(nums),
                          WhatsAppMessage.id > func.coalesce(WhatsAppConversationRead.last_read_message_id, 0))
                  .group_by(WhatsAppMessage.to_number).all())
    out = []
    for num in nums:
        last = last_rows.get(num)
        ok, expires = wa.within_window(db, num)
        out.append({
            "phone": num,
            "guest_id": last.guest_id if last else None,
            "guest_name": _guest_name(db, num, last.guest_id if last else None),
            "last_body": last.body if last else None,
            "last_direction": last.direction if last else None,
            "last_at": last.created_at.isoformat() if last and last.created_at else None,
            "last_status": last.status if last else None,
            "unread_count": int(unread.get(num, 0)),
            "within_window": ok,
            "window_expires_at": expires.isoformat() if expires else None,
        })
    out.sort(key=lambda c: c["last_at"] or "", reverse=True)
    return {"data": out}


@router.get("/conversations/{phone}")
def get_conversation_ep(phone: str, db: Session = Depends(get_db),
                        user=Depends(require_reception_or_admin)):
    """The full thread (in + out, chronological) for one number, plus the 24h-window state."""
    num = wa.normalize_number(phone) or phone
    rows = (db.query(WhatsAppMessage).filter(WhatsAppMessage.to_number == num)
            .order_by(WhatsAppMessage.id.asc()).all())
    ok, expires = wa.within_window(db, num)
    gid = next((m.guest_id for m in reversed(rows) if m.guest_id), None)
    return {
        "phone": num,
        "guest_name": _guest_name(db, num, gid),
        "within_window": ok,
        "window_expires_at": expires.isoformat() if expires else None,
        "messages": [_msg_dict(m) for m in rows],
    }


def _mark_read(db, num, uid):
    max_id = (db.query(func.max(WhatsAppMessage.id))
              .filter(WhatsAppMessage.to_number == num).scalar()) or 0
    r = db.query(WhatsAppConversationRead).filter(WhatsAppConversationRead.phone == num).first()
    if r is None:
        db.add(WhatsAppConversationRead(phone=num, last_read_message_id=max_id, updated_by=uid))
    else:
        r.last_read_message_id = max(r.last_read_message_id or 0, max_id)
        r.updated_by = uid
        r.last_read_at = datetime.utcnow()
    db.commit()


@router.post("/conversations/{phone}/read")
def mark_conversation_read_ep(phone: str, db: Session = Depends(get_db),
                              user=Depends(require_reception_or_admin)):
    """Mark a conversation read up to its latest message (clears its unread badge)."""
    num = wa.normalize_number(phone) or phone
    _mark_read(db, num, _resolve_user_id(db, user))
    return {"phone": num, "ok": True}


@router.post("/conversations/{phone}/reply")
def reply_conversation_ep(phone: str, data: WhatsAppReplyRequest, db: Session = Depends(get_db),
                          user=Depends(require_reception_or_admin)):
    """Send a reply. Free `text` needs the 24h window open; a `template` works any time (also used
    to reopen a closed window and to start a brand-new chat)."""
    num = wa.normalize_number(phone)
    if not num:
        raise HTTPException(status_code=400, detail="Invalid phone number")
    uid = _resolve_user_id(db, user)
    if data.template:
        if not get_template(data.template):
            raise HTTPException(status_code=400, detail=f"Unknown template '{data.template}'")
        row = wa.send_template(db, num, data.template, data.params or {},
                               sent_by=uid, respect_optout=False)
    elif data.text:
        ok, _ = wa.within_window(db, num)
        if not ok:
            raise HTTPException(
                status_code=409,
                detail="The 24-hour reply window has closed — send an approved template to reopen the chat.")
        row = wa.send_text(db, num, data.text, sent_by=uid)
    else:
        raise HTTPException(status_code=400, detail="Provide either text or a template.")
    if row is None:
        raise HTTPException(status_code=502, detail="Send failed unexpectedly")
    write_audit(db, user, "whatsapp.reply", "whatsapp_message", row.id,
                after={"to": num, "kind": "template" if data.template else "text",
                       "status": row.status}, client="desktop", commit=True)
    _mark_read(db, num, uid)
    return _msg_dict(row)


@router.get("/unread-count")
def unread_count_ep(db: Session = Depends(get_db), user=Depends(require_reception_or_admin)):
    """Total unread inbound messages across all conversations (for the nav badge)."""
    total = (db.query(func.count(WhatsAppMessage.id))
             .outerjoin(WhatsAppConversationRead,
                        WhatsAppConversationRead.phone == WhatsAppMessage.to_number)
             .filter(WhatsAppMessage.direction == "in",
                     WhatsAppMessage.id > func.coalesce(WhatsAppConversationRead.last_read_message_id, 0))
             .scalar()) or 0
    return {"unread": int(total)}


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
    """Send one template to a number for verification (respects opt-out per the catalog).

    Every catalog template has at least one variable, and the admin UI posts no params, so any
    the caller omits are filled with sample values -- Meta rejects an empty body parameter with
    (#131008) and would fail the whole send.
    """
    if not get_template(data.template):
        raise HTTPException(status_code=400, detail=f"Unknown template '{data.template}'")
    row = wa.send_template(db, data.to, data.template,
                           fill_sample_params(data.template, data.params))
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


def _normalize_keyword_text(text):
    """Lowercase, drop punctuation/emoji, collapse whitespace. Keyword rules match on this, so
    "STOP.", "Stop 🙏" and "Stop  promotions" all reduce to the same comparable string."""
    return _WS_RE.sub(" ", _PUNCT_RE.sub(" ", (text or "").lower())).strip()


def _inbound_text_of(msg):
    """The keyword-matchable text of an inbound webhook message, or None when it carries none
    (image/audio/location/...), which keeps the "[image]"-style log fallback.

    A quick-reply button tap on a TEMPLATE does not arrive as type "text" -- it arrives as type
    "button" with the label under button.text -- so routing on "text" alone left every template
    button (the review_feedback opt-out button included) dead on arrival no matter how it was
    labelled. Interactive replies use a third shape again: interactive.button_reply /
    interactive.list_reply, carrying id + title."""
    mtype = msg.get("type")
    if mtype == "text":
        return (msg.get("text") or {}).get("body", "")
    if mtype == "button":
        btn = msg.get("button") or {}
        return btn.get("text") or btn.get("payload") or ""
    if mtype == "interactive":
        inter = msg.get("interactive") or {}
        inner = inter.get(inter.get("type") or "") or {}
        return inner.get("title") or inner.get("id") or ""
    return None


def _resolve_guest_and_booking(db, from_number):
    """(normalised number, guest, booking_id) for an inbound sender: match the stored phone
    exactly, then on the last 10 digits, then take their latest booking. Shared by the complaint
    and extend paths."""
    from routers.crm import match_guest
    norm = wa.normalize_number(from_number)
    guest = match_guest(db, norm)
    if guest is None and norm and len(norm) > 10:
        guest = match_guest(db, norm[-10:])
    booking = _latest_booking_for_guest(db, guest.guest_id) if guest else None
    return norm, guest, (booking.booking_id if booking else None)


def _handle_extend_request(db, from_number, guest, booking_id):
    """The guest replied EXTEND to checkout_reminder, which promises "we'll arrange it".

    Their own inbound message just opened the 24h customer-service window, so a free-text reply is
    allowed here and no extra Meta template is needed. The request itself stays in the conversation
    inbox, which already carries an unread badge (_conversation_numbers / unread_count_ep), for the
    desk to price and confirm through the normal POST /reception/extend flow."""
    wa.send_text(
        db, from_number,
        "Thanks - we've passed your request to extend your stay to the front desk. "
        "They'll confirm the room and the rate with you shortly.",
        guest_id=guest.guest_id if guest else None, booking_id=booking_id)
    logger.info("WhatsApp EXTEND request from %s (guest=%s)",
                from_number, guest.guest_id if guest else None)


def _handle_inbound_text(db, from_number, text, provider_id=None):
    """Route an inbound reply: opt-out keyword, extension request, or complaint -> ticket + ack."""
    from routers.maintenance import create_guest_ticket

    norm_text = _normalize_keyword_text(text)
    inbound_row = wa.log_inbound(db, from_number, text, provider_id)

    # Opt-out: whole-message match, or a short message opening with a stop word.
    words = norm_text.split()
    if norm_text in _OPTOUT_WORDS or (
            words and words[0] in _OPTOUT_LEAD and len(words) <= _OPTOUT_MAX_WORDS):
        wa.set_optout(db, from_number, source="guest",
                      reason=f"replied: {(text or '')[:60]}")
        return

    # Extension request. Checked BEFORE the complaint words on purpose: with overstay auto-billing
    # live (v4b3), a missed "I have an issue, can I extend?" costs the guest money, and the desk
    # still reads the full text in the inbox either way.
    if _EXTEND_RE.search(norm_text):
        _, guest, booking_id = _resolve_guest_and_booking(db, from_number)
        # Tag the inbound row so the desk's notification poll can flag it as a distinct EXTEND
        # request (a "Guest wants to extend" alert), not just another inbox message.
        if inbound_row is not None:
            try:
                inbound_row.template = "extend_request"
                db.commit()
            except Exception:
                db.rollback()
        _handle_extend_request(db, from_number, guest, booking_id)
        return

    # Complaint keywords -> raise a guest ticket + acknowledge.
    if _COMPLAINT_RE.search(norm_text):
        norm, guest, booking_id = _resolve_guest_and_booking(db, from_number)
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
                    body = _inbound_text_of(msg)
                    if body is not None:
                        _handle_inbound_text(db, msg.get("from"), body, msg.get("id"))
                    else:
                        wa.log_inbound(db, msg.get("from"), f"[{msg.get('type')}]", msg.get("id"))
    except Exception as e:
        logger.error(f"whatsapp webhook processing error: {e}")
    return {"received": True}
