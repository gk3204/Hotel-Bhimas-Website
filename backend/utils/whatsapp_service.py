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
                  client_ref=None, respect_optout=None, commit=True):
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
                            commit=commit)

        # 2. Opt-out check (guest marketing/review only).
        if respect_optout and is_opted_out(db, num):
            return _log_row(db, "out", num, template, params, body,
                            status="failed", error="opted_out",
                            guest_id=guest_id, booking_id=booking_id, client_ref=client_ref,
                            commit=commit)

        # 3. Send via the live driver, or stub-log.
        cfg = _provider_config()
        if cfg is None:
            logger.info(f"📤 [whatsapp:stub] to={num} template={template} :: {body}")
            return _log_row(db, "out", num, template, params, body,
                            status="sent", provider="stub",
                            guest_id=guest_id, booking_id=booking_id, client_ref=client_ref,
                            commit=commit)

        return _send_meta(db, cfg, num, template, tpl, params, body,
                          guest_id=guest_id, booking_id=booking_id, client_ref=client_ref,
                          commit=commit)
    except Exception as e:  # last-resort guard — a send must never break the caller
        logger.error(f"whatsapp send_template unexpected error (to={to}, template={template}): {e}")
        try:
            db.rollback()
        except Exception:
            pass
        return None


def _send_meta(db, cfg, num, template, tpl, params, body, *, guest_id, booking_id, client_ref, commit):
    row = _log_row(db, "out", num, template, params, body, status="queued", provider="meta",
                   guest_id=guest_id, booking_id=booking_id, client_ref=client_ref, commit=False)
    url = f"https://graph.facebook.com/{cfg['api_version']}/{cfg['phone_id']}/messages"
    meta_name = tpl["meta_name"] if tpl else template
    lang = tpl["lang"] if tpl else "en"
    order = tpl["param_order"] if tpl else list(params.keys())
    body_params = [{"type": "text", "text": str(params.get(k, ""))} for k in order]
    payload = {
        "messaging_product": "whatsapp",
        "to": num,
        "type": "template",
        "template": {
            "name": meta_name,
            "language": {"code": lang},
        },
    }
    if body_params:
        payload["template"]["components"] = [{"type": "body", "parameters": body_params}]
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


def _log_row(db, direction, to_number, template, params, body, *, status, provider=None,
             error=None, provider_id=None, guest_id=None, booking_id=None, client_ref=None,
             commit=True):
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
        updated_at=datetime.utcnow(),
    )
    db.add(row)
    if commit:
        db.commit()
    else:
        db.flush()
    return row


def log_inbound(db, from_number, text, provider_id=None, commit=True):
    """Record an inbound reply for the admin log."""
    num = normalize_number(from_number) or str(from_number or "")
    return _log_row(db, "in", num, "inbound", None, text, status="received",
                    provider="meta", provider_id=provider_id, commit=commit)


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


def send_owner_otp(db, otp, commit=True):
    """Deliver an owner-approval code to the owner's WhatsApp (prompt 11 -> 15). Best-effort:
    the on-screen/log delivery in owner_otp.create_otp still works if this is a no-op."""
    if not _owner_alerts_on(db):
        return None
    num = owner_number(db)
    if not num:
        return None
    return send_template(db, num, "owner_otp",
                         {"code": otp.code, "action": otp.action},
                         client_ref=f"owner_otp:{otp.id}", respect_optout=False, commit=commit)


def send_fraud_alert(db, alert, commit=True):
    if not _owner_alerts_on(db):
        return None
    num = owner_number(db)
    if not num:
        return None
    detail = ""
    try:
        d = json.loads(alert.detail) if getattr(alert, "detail", None) else {}
        detail = d.get("message") or d.get("dedupe_key") or ""
    except Exception:
        detail = ""
    return send_template(db, num, "fraud_alert",
                         {"alert_type": getattr(alert, "type", "alert"), "detail": detail or "see dashboard"},
                         client_ref=f"fraud_alert:{alert.id}", respect_optout=False, commit=commit)


def send_cash_variance_alert(db, shift, variance, commit=True):
    if not _owner_alerts_on(db):
        return None
    num = owner_number(db)
    if not num:
        return None
    return send_template(db, num, "cash_variance",
                         {"shift_id": shift.id, "variance": f"{variance:.2f}"},
                         client_ref=f"cash_variance:{shift.id}", respect_optout=False, commit=commit)


def send_daily_digest(db, digest, commit=True):
    if not _owner_alerts_on(db):
        return None
    num = owner_number(db)
    if not num:
        return None
    day = digest.get("day")
    occ = (digest.get("occupancy") or {}).get("occupancy_pct", 0)
    return send_template(db, num, "daily_digest",
                         {"day": day, "occupancy_pct": occ,
                          "revenue": f"{digest.get('revenue_today', 0):.2f}",
                          "open_alerts": digest.get("open_alerts", 0)},
                         client_ref=f"daily_digest:{day}", respect_optout=False, commit=commit)
