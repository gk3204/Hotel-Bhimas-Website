"""Pluggable WhatsApp messaging (prompt 15).

Replaces the trail of `logger` stubs left across prompts 11-14 ("WhatsApp delivery -> prompt 15")
with one send seam and two backends, mirroring utils/storage.py (R2-when-env-set):

  * Meta WhatsApp Cloud API — used when WHATSAPP_ACCESS_TOKEN + WHATSAPP_PHONE_NUMBER_ID are set.
    Sends a pre-approved `template` message via the Graph API (uses `requests`, already a dep).
  * Stub / log fallback — otherwise logs the rendered body and marks the row `sent` with
    provider='stub'. This makes the whole feature testable with no live credentials; flip to
    real delivery purely by setting env (no code change).

Every send (real or stub) writes ONE `whatsapp_messages` row so the admin log + delivery status
work in both modes. `client_ref` makes scheduled sends idempotent. Sends NEVER raise into the
caller — event hooks call this best-effort and must not break the booking/payment/checkout flow.
"""
import os
import json
import logging
import re
from datetime import datetime

import requests

from models import WhatsAppMessage, WhatsAppOptOut
from utils import settings as app_settings
from utils.whatsapp_templates import get_template, render_preview

logger = logging.getLogger(__name__)

_GRAPH_TIMEOUT = 15  # seconds


# ---------------------------------------------------------------------------
# Provider config (env-owned secrets — read at call time so they can change
# without restart, matching fraud_detection.get_config()).
# ---------------------------------------------------------------------------
def _provider_config():
    token = os.getenv("WHATSAPP_ACCESS_TOKEN")
    phone_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID")
    if token and phone_id:
        return {
            "token": token,
            "phone_id": phone_id,
            "api_version": os.getenv("WHATSAPP_API_VERSION", "v21.0"),
        }
    return None


def is_configured() -> bool:
    return _provider_config() is not None


def provider_status() -> dict:
    """Read-only surface for the admin UI: which driver is live (no secrets exposed)."""
    return {
        "provider": "meta" if is_configured() else "stub",
        "configured": is_configured(),
        "webhook_verify_configured": bool(os.getenv("WHATSAPP_VERIFY_TOKEN")),
    }


def normalize_number(phone) -> str | None:
    """E.164 digits only (Meta wants no '+'). Bare 10-digit India numbers get a 91 prefix."""
    if not phone:
        return None
    digits = "".join(ch for ch in str(phone) if ch.isdigit())
    if not digits:
        return None
    if len(digits) == 10:  # local India mobile
        digits = "91" + digits
    return digits


# ---------------------------------------------------------------------------
# Opt-out list
# ---------------------------------------------------------------------------
def is_opted_out(db, phone) -> bool:
    num = normalize_number(phone)
    if not num:
        return False
    return db.query(WhatsAppOptOut).filter(WhatsAppOptOut.phone == num).first() is not None


def set_optout(db, phone, source="guest", reason=None, commit=True):
    num = normalize_number(phone)
    if not num:
        return None
    row = db.query(WhatsAppOptOut).filter(WhatsAppOptOut.phone == num).first()
    if row is None:
        row = WhatsAppOptOut(phone=num, source=source, reason=reason)
        db.add(row)
    else:
        row.source = source
        row.reason = reason
    if commit:
        db.commit()
    return row


def remove_optout(db, phone, commit=True) -> bool:
    num = normalize_number(phone)
    if not num:
        return False
    row = db.query(WhatsAppOptOut).filter(WhatsAppOptOut.phone == num).first()
    if row is None:
        return False
    db.delete(row)
    if commit:
        db.commit()
    return True


def list_optouts(db):
    return db.query(WhatsAppOptOut).order_by(WhatsAppOptOut.created_at.desc()).all()


def already_sent(db, client_ref) -> bool:
    """True if a non-failed message with this client_ref already exists — lets scheduled jobs
    skip work + count only genuinely-new sends (send_template is idempotent regardless)."""
    if not client_ref:
        return False
    row = db.query(WhatsAppMessage).filter(WhatsAppMessage.client_ref == client_ref).first()
    return row is not None and row.status != "failed"


# ---------------------------------------------------------------------------
# Core send
# ---------------------------------------------------------------------------
def send_template(db, to, template, params=None, *, guest_id=None, booking_id=None,
                  client_ref=None, respect_optout=None, sent_by=None, commit=True):
    """Send a pre-approved template (or stub-log it). Returns the WhatsAppMessage row.
    NEVER raises — callers use it best-effort. `respect_optout` defaults to the template's
    catalog setting; owner/OTP/overstay templates bypass the opt-out list."""
    params = params or {}
    tpl = get_template(template)
    if respect_optout is None:
        respect_optout = tpl["respect_optout"] if tpl else True

    try:
        # 1. Idempotency: a prior non-failed row with this client_ref means already sent.
        if client_ref:
            existing = (db.query(WhatsAppMessage)
                        .filter(WhatsAppMessage.client_ref == client_ref).first())
            if existing is not None and existing.status != "failed":
                return existing
            if existing is not None:  # previous attempt failed — reuse the row for a retry
                db.delete(existing)
                db.flush()

        num = normalize_number(to)
        body = render_preview(template, params)

        if not num:
            return _log_row(db, "out", str(to or ""), template, params, body,
                            status="failed", error="invalid_number",
                            guest_id=guest_id, booking_id=booking_id, client_ref=client_ref,
                            sent_by=sent_by, commit=commit)

        # 2. Opt-out check (guest marketing/review only).
        if respect_optout and is_opted_out(db, num):
            return _log_row(db, "out", num, template, params, body,
                            status="failed", error="opted_out",
                            guest_id=guest_id, booking_id=booking_id, client_ref=client_ref,
                            sent_by=sent_by, commit=commit)

        # 3. Send via the live driver, or stub-log.
        cfg = _provider_config()
        if cfg is None:
            logger.info(f"📤 [whatsapp:stub] to={num} template={template} :: {body}")
            return _log_row(db, "out", num, template, params, body,
                            status="sent", provider="stub",
                            guest_id=guest_id, booking_id=booking_id, client_ref=client_ref,
                            sent_by=sent_by, commit=commit)

        return _send_meta(db, cfg, num, template, tpl, params, body,
                          guest_id=guest_id, booking_id=booking_id, client_ref=client_ref,
                          sent_by=sent_by, commit=commit)
    except Exception as e:  # last-resort guard — a send must never break the caller
        logger.error(f"whatsapp send_template unexpected error (to={to}, template={template}): {e}")
        try:
            db.rollback()
        except Exception:
            pass
        return None


# Meta rejects a body parameter whose text is empty with (#131008) "Parameter of type text is
# missing text value", failing the WHOLE send. Optional values legitimately go missing --
# room_label before a room is assigned, google_review_url when the owner hasn't set one, a
# nullable stock `unit` -- so every param is coerced to something Meta will accept rather than
# letting one blank take down the message. Meta also disallows newlines and tabs inside a
# parameter, hence the whitespace collapse.
_PARAM_FALLBACK = "-"


def _param_text(value) -> str:
    if value is None:
        return _PARAM_FALLBACK
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text or _PARAM_FALLBACK


def _body_params(order, params) -> list:
    return [{"type": "text", "text": _param_text((params or {}).get(k))} for k in order]


def _send_meta(db, cfg, num, template, tpl, params, body, *, guest_id, booking_id, client_ref,
               sent_by=None, commit=True):
    row = _log_row(db, "out", num, template, params, body, status="queued", provider="meta",
                   guest_id=guest_id, booking_id=booking_id, client_ref=client_ref,
                   sent_by=sent_by, commit=False)
    url = f"https://graph.facebook.com/{cfg['api_version']}/{cfg['phone_id']}/messages"
    meta_name = tpl["meta_name"] if tpl else template
    lang = tpl["lang"] if tpl else "en"
    order = tpl["param_order"] if tpl else list(params.keys())
    body_params = _body_params(order, params)
    payload = {
        "messaging_product": "whatsapp",
        "to": num,
        "type": "template",
        "template": {
            "name": meta_name,
            "language": {"code": lang},
        },
    }
    components = [{"type": "body", "parameters": body_params}] if body_params else []
    # Authentication templates carry a mandatory copy-code button, and Meta requires the code to
    # be repeated as that button's parameter as well as the body's. Sending only the body is
    # rejected with 132000, which is why owner_otp could not be an auth template until now.
    if body_params and tpl and tpl.get("category") == "authentication":
        components.append({"type": "button", "sub_type": "url", "index": "0",
                           "parameters": [body_params[0]]})
    if components:
        payload["template"]["components"] = components
    try:
        resp = requests.post(
            url, json=payload,
            headers={"Authorization": f"Bearer {cfg['token']}", "Content-Type": "application/json"},
            timeout=_GRAPH_TIMEOUT,
        )
        if resp.status_code == 200:
            data = resp.json()
            wamid = (data.get("messages") or [{}])[0].get("id")
            row.status = "sent"
            row.provider_id = wamid
        else:
            row.status = "failed"
            row.error = f"http {resp.status_code}: {resp.text[:250]}"
            logger.error(f"❌ whatsapp meta send failed {resp.status_code}: {resp.text[:250]}")
    except Exception as e:
        row.status = "failed"
        row.error = f"request error: {str(e)[:250]}"
        logger.error(f"❌ whatsapp meta request error: {e}")
    row.updated_at = datetime.utcnow()
    if commit:
        db.commit()
    else:
        db.flush()
    return row


def send_text(db, to, text, *, sent_by=None, guest_id=None, booking_id=None, commit=True):
    """Send a FREE-FORM WhatsApp text (the staff inbox reply). Only valid inside the 24h customer
    service window — Meta rejects it otherwise (the row is logged 'failed' with the error). Logs one
    outbound row (template='reply'). Stub-logs when the provider is off. Never raises."""
    try:
        num = normalize_number(to)
        if not num:
            return _log_row(db, "out", str(to or ""), "reply", None, text,
                            status="failed", error="invalid_number",
                            guest_id=guest_id, booking_id=booking_id, sent_by=sent_by, commit=commit)

        cfg = _provider_config()
        if cfg is None:
            logger.info(f"📤 [whatsapp:stub] to={num} reply :: {text}")
            return _log_row(db, "out", num, "reply", None, text, status="sent", provider="stub",
                            guest_id=guest_id, booking_id=booking_id, sent_by=sent_by, commit=commit)

        row = _log_row(db, "out", num, "reply", None, text, status="queued", provider="meta",
                       guest_id=guest_id, booking_id=booking_id, sent_by=sent_by, commit=False)
        url = f"https://graph.facebook.com/{cfg['api_version']}/{cfg['phone_id']}/messages"
        payload = {"messaging_product": "whatsapp", "to": num, "type": "text",
                   "text": {"body": text}}
        try:
            resp = requests.post(
                url, json=payload,
                headers={"Authorization": f"Bearer {cfg['token']}", "Content-Type": "application/json"},
                timeout=_GRAPH_TIMEOUT)
            if resp.status_code == 200:
                data = resp.json()
                row.status = "sent"
                row.provider_id = (data.get("messages") or [{}])[0].get("id")
            else:
                row.status = "failed"
                row.error = f"http {resp.status_code}: {resp.text[:250]}"
                logger.error(f"❌ whatsapp text send failed {resp.status_code}: {resp.text[:250]}")
        except Exception as e:
            row.status = "failed"
            row.error = f"request error: {str(e)[:250]}"
            logger.error(f"❌ whatsapp text request error: {e}")
        row.updated_at = datetime.utcnow()
        if commit:
            db.commit()
        else:
            db.flush()
        return row
    except Exception as e:
        logger.error(f"whatsapp send_text unexpected error (to={to}): {e}")
        try:
            db.rollback()
        except Exception:
            pass
        return None


def _upload_media(cfg, media_path, mime="application/pdf") -> str | None:
    """Upload a local file to Meta → return the media id (referenced in a document message)."""
    url = f"https://graph.facebook.com/{cfg['api_version']}/{cfg['phone_id']}/media"
    try:
        with open(media_path, "rb") as fh:
            resp = requests.post(
                url,
                headers={"Authorization": f"Bearer {cfg['token']}"},
                data={"messaging_product": "whatsapp", "type": mime},
                files={"file": (os.path.basename(media_path), fh, mime)},
                timeout=_GRAPH_TIMEOUT * 2)
        if resp.status_code == 200:
            return resp.json().get("id")
        logger.error(f"❌ whatsapp media upload failed {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        logger.error(f"❌ whatsapp media upload error: {e}")
    return None


def send_document(db, to, media_path, filename, caption, *, template=None, params=None,
                  client_ref=None, sent_by=None, commit=True):
    """Send a PDF (or other) document over WhatsApp. Outside the 24h window an automated send needs
    a `template` with a DOCUMENT header (Meta rule); inside the window a free-form document works.
    Stub-logs when the provider is off. Never raises — the caller (a scheduled job) is best-effort.
    `caption` is the accompanying text and is stored as the row body/preview."""
    params = params or {}
    try:
        if client_ref and already_sent(db, client_ref):
            return db.query(WhatsAppMessage).filter(WhatsAppMessage.client_ref == client_ref).first()
        num = normalize_number(to)
        tname = template or "document"
        if not num:
            return _log_row(db, "out", str(to or ""), tname, params, caption, status="failed",
                            error="invalid_number", client_ref=client_ref, sent_by=sent_by, commit=commit)

        cfg = _provider_config()
        if cfg is None:
            logger.info(f"📤 [whatsapp:stub] to={num} document={filename} :: {caption}")
            return _log_row(db, "out", num, tname, params, caption, status="sent", provider="stub",
                            client_ref=client_ref, sent_by=sent_by, commit=commit)

        row = _log_row(db, "out", num, tname, params, caption, status="queued", provider="meta",
                       client_ref=client_ref, sent_by=sent_by, commit=False)
        media_id = _upload_media(cfg, media_path)
        if not media_id:
            row.status = "failed"
            row.error = "media_upload_failed"
            if commit:
                db.commit()
            return row
        url = f"https://graph.facebook.com/{cfg['api_version']}/{cfg['phone_id']}/messages"
        if template:
            # Template with a document header: header media by id, body params in order.
            tpl = get_template(template)
            order = tpl["param_order"] if tpl else list(params.keys())
            payload = {
                "messaging_product": "whatsapp", "to": num, "type": "template",
                "template": {
                    "name": (tpl["meta_name"] if tpl else template),
                    "language": {"code": tpl["lang"] if tpl else "en"},
                    "components": [
                        {"type": "header", "parameters": [
                            {"type": "document", "document": {"id": media_id, "filename": filename}}]},
                        {"type": "body", "parameters": _body_params(order, params)},
                    ],
                },
            }
        else:
            payload = {"messaging_product": "whatsapp", "to": num, "type": "document",
                       "document": {"id": media_id, "filename": filename, "caption": caption}}
        try:
            resp = requests.post(
                url, json=payload,
                headers={"Authorization": f"Bearer {cfg['token']}", "Content-Type": "application/json"},
                timeout=_GRAPH_TIMEOUT)
            if resp.status_code == 200:
                row.status = "sent"
                row.provider_id = (resp.json().get("messages") or [{}])[0].get("id")
            else:
                row.status = "failed"
                row.error = f"http {resp.status_code}: {resp.text[:200]}"
        except Exception as e:
            row.status = "failed"
            row.error = f"request error: {str(e)[:200]}"
        row.updated_at = datetime.utcnow()
        if commit:
            db.commit()
        return row
    except Exception as e:
        logger.error(f"whatsapp send_document unexpected error (to={to}): {e}")
        try:
            db.rollback()
        except Exception:
            pass
        return None


def _log_row(db, direction, to_number, template, params, body, *, status, provider=None,
             error=None, provider_id=None, guest_id=None, booking_id=None, client_ref=None,
             sent_by=None, commit=True):
    row = WhatsAppMessage(
        direction=direction,
        to_number=to_number,
        template=template,
        params=json.dumps(params, default=str) if params else None,
        body=body,
        status=status,
        provider=provider,
        provider_id=provider_id,
        error=error,
        guest_id=guest_id,
        booking_id=booking_id,
        client_ref=client_ref,
        sent_by=sent_by,
        updated_at=datetime.utcnow(),
    )
    db.add(row)
    if commit:
        db.commit()
    else:
        db.flush()
    return row


def _resolve_guest_id(db, num):
    """Best-effort guest lookup by number (exact, then last-10), for inbox names. Never raises."""
    try:
        from routers.crm import match_guest
        g = match_guest(db, num)
        if g is None and num and len(num) > 10:
            g = match_guest(db, num[-10:])
        return g.guest_id if g else None
    except Exception:
        return None


def log_inbound(db, from_number, text, provider_id=None, commit=True):
    """Record an inbound reply for the admin log + inbox. Resolves the guest (by phone) so the
    inbox can show a name."""
    num = normalize_number(from_number) or str(from_number or "")
    return _log_row(db, "in", num, "inbound", None, text, status="received",
                    provider="meta", provider_id=provider_id,
                    guest_id=_resolve_guest_id(db, num), commit=commit)


def within_window(db, phone):
    """WhatsApp's 24h customer-service window: (bool, expires_at). True when the latest INBOUND
    message from this number is under 24h old, so a free-text reply is allowed. Compared entirely
    on the DB clock (now() vs created_at) so it's immune to server/app timezone mismatch."""
    from sqlalchemy import text
    num = normalize_number(phone)
    if not num:
        return (False, None)
    row = db.execute(text(
        "SELECT (now() - created_at) < interval '24 hours' AS ok, "
        "       created_at + interval '24 hours' AS expires "
        "FROM whatsapp_messages "
        "WHERE direction = 'in' AND to_number = :num "
        "ORDER BY id DESC LIMIT 1"), {"num": num}).first()
    if row is None:
        return (False, None)
    return (bool(row.ok), row.expires)


def update_delivery_status(db, provider_id, status, commit=True) -> bool:
    """Advance a message's delivery status from a Meta status callback (sent->delivered->read,
    or failed). Never downgrades a read back to delivered."""
    if not provider_id:
        return False
    row = (db.query(WhatsAppMessage)
           .filter(WhatsAppMessage.provider_id == provider_id).first())
    if row is None:
        return False
    rank = {"queued": 0, "sent": 1, "delivered": 2, "read": 3, "failed": 3}
    if rank.get(status, 0) >= rank.get(row.status, 0):
        row.status = status
        row.updated_at = datetime.utcnow()
        if commit:
            db.commit()
        return True
    return False


# ---------------------------------------------------------------------------
# Owner-facing wrappers (respect the wa_owner_alerts_enabled toggle + owner number)
# ---------------------------------------------------------------------------
def owner_number(db) -> str | None:
    return normalize_number(app_settings.get_whatsapp_config(db)["owner_whatsapp"])


def _owner_alerts_on(db) -> bool:
    return app_settings.get_whatsapp_config(db)["owner_alerts_enabled"]


# The owner wrappers below route through services/notify.py so an alert that can't go by
# WhatsApp still reaches the owner's email (backlog v2 FE-11). They each return notify()'s
# {channel, ok, detail} dict. Imported lazily — notify imports this module.
def _notify_owner(db, template, params, client_ref, commit):
    from services import notify as notify_service
    return notify_service.notify_owner(db, template=template, params=params,
                                       client_ref=client_ref, commit=commit)


# An approval message that says only "refund" asks the owner to approve a CATEGORY. The
# sentence they actually need already exists: build_context() writes ctx["summary"] for every
# action, and the admin Approvals inbox shows it as its headline (routers/fraud.py). Send the
# same sentence so both channels say the same thing.
_OTP_ACTION_MAX = 300


def _otp_action_text(otp):
    """The human-readable line the owner reads for an approval code.

    Falls back to the bare action for pre-v4b0 rows written before build_context existed --
    the same fallback the web inbox uses. WhatsApp rejects a parameter containing newlines,
    tabs or 4+ consecutive spaces, so the text is flattened and capped before it goes out.
    Never raises: a context that cannot be read must not block an approval."""
    summary = requested_by = ""
    try:
        ctx = json.loads(otp.context) if getattr(otp, "context", None) else {}
        if isinstance(ctx, dict):
            summary = (ctx.get("summary") or "").strip()
            requested_by = (ctx.get("requested_by") or "").strip()
    except Exception:
        pass
    text = summary or (getattr(otp, "action", "") or "approval").replace("_", " ")
    if requested_by:
        text = f"{text}, requested by {requested_by}"
    text = re.sub(r"\s+", " ", text).strip()
    return text[:_OTP_ACTION_MAX].rstrip(" ,;:-\u2014")


def send_owner_otp(db, otp, commit=True):
    """Deliver an owner-approval code to the owner (WhatsApp, else email). Best-effort:
    the on-screen Approvals inbox always works even when this is a no-op.

    TWO messages, by Meta's rules rather than by choice: an authentication template's body is
    fixed at "<CODE> is your verification code.", so the code cannot travel with a description
    of what it approves. The context goes first as a utility message, then the code.

    Returns the CODE message's result -- that is the one deliver_otp() reports on, because a
    desk that never receives the code cannot proceed, while a missing context line is cosmetic.
    """
    _notify_owner(db, "owner_approval_request", {"action": _otp_action_text(otp)},
                  f"owner_approval_request:{otp.id}", commit)
    return _notify_owner(db, "owner_otp", {"code": otp.code},
                         f"owner_otp:{otp.id}", commit)


def send_fraud_alert(db, alert, commit=True):
    detail = ""
    try:
        d = json.loads(alert.detail) if getattr(alert, "detail", None) else {}
        detail = d.get("message") or d.get("dedupe_key") or ""
    except Exception:
        detail = ""
    return _notify_owner(db, "fraud_alert",
                         {"alert_type": getattr(alert, "type", "alert"),
                          "detail": detail or "see dashboard"},
                         f"fraud_alert:{alert.id}", commit)


def send_cash_variance_alert(db, shift, variance, commit=True):
    return _notify_owner(db, "cash_variance",
                         {"shift_id": shift.id, "variance": f"{variance:.2f}"},
                         f"cash_variance:{shift.id}", commit)


def send_daily_digest(db, digest, commit=True):
    day = digest.get("day")
    occ = (digest.get("occupancy") or {}).get("occupancy_pct", 0)
    return _notify_owner(db, "daily_digest",
                         {"day": day, "occupancy_pct": occ,
                          "revenue": f"{digest.get('revenue_today', 0):.2f}",
                          "open_alerts": digest.get("open_alerts", 0)},
                         f"daily_digest:{day}", commit)
