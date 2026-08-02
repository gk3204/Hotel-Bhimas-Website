"""One place to send a guest or owner a message — WhatsApp first, email as fallback.

Backlog v2 FE-11. Before this, fourteen call sites reached into `whatsapp_service` directly
and there was no fallback at all: if WhatsApp was unconfigured, misconfigured or simply
failed, the message was silently lost. The nearest thing to an abstraction was a private
helper inside `routers/reception.py` (`_wa_notify`), which had the right shape but was not
reusable — this generalises it.

Two things make the fallback non-obvious, and both are handled here:

  1. The WhatsApp stub driver reports ``status="sent"`` while delivering nothing (it just
     logs). A naive success check would therefore never fall back. Delivery only counts as
     real when ``whatsapp_service.is_configured()`` is true AND the row came back "sent".

  2. There is no owner email anywhere in the database — `User` has no email column. The
     owner's address is an app setting (`owner_email`), editable on the Settings hub.

Both channels are best-effort: `notify()` NEVER raises. A messaging failure must not break
check-in, booking or payment. Callers get an honest ``{channel, ok, detail}`` back so they
can tell the user what actually happened.
"""
import logging

from utils import settings as app_settings
from utils import whatsapp_service
from utils import whatsapp_templates

logger = logging.getLogger(__name__)

# Subject lines for the email rendering of each template. Anything not listed falls back
# to the hotel name, so a new template still sends rather than erroring.
_SUBJECTS = {
    "booking_confirmation": "Your booking is confirmed — Hotel Bhimas",
    "payment_receipt": "Payment received — Hotel Bhimas",
    "room_ready": "Your room is ready — Hotel Bhimas",
    "checkout_reminder": "Check-out reminder — Hotel Bhimas",
    "overstay": "About your check-out — Hotel Bhimas",
    "review_feedback": "How was your stay? — Hotel Bhimas",
    "complaint_ack": "We've logged your complaint — Hotel Bhimas",
    "prearrival_link": "Complete your pre-arrival registration — Hotel Bhimas",
    "payment_link": "Your payment link — Hotel Bhimas",
    "portal_link": "Your in-room services — Hotel Bhimas",
    "owner_otp": "Approval code — Hotel Bhimas",
    "daily_digest": "Daily digest — Hotel Bhimas",
    "weekly_digest": "Weekly digest — Hotel Bhimas",
    "fraud_alert": "Alert needs your attention — Hotel Bhimas",
    "cash_variance": "Cash variance alert — Hotel Bhimas",
    "review_alert": "A guest left a poor review — Hotel Bhimas",
    "vendor_renewal": "A vendor contract is due for renewal — Hotel Bhimas",
    "low_stock_alert": "Stock is running low — Hotel Bhimas",
    "complaint_escalation": "A complaint has escalated — Hotel Bhimas",
}

_RESULT_NONE = {"channel": "none", "ok": False, "detail": "No delivery channel available."}


def owner_email(db) -> str | None:
    """The owner's email address, set on the Settings hub. There is no owner email
    column anywhere in the schema, so this setting is the only source."""
    return (app_settings.get_setting(db, app_settings.OWNER_EMAIL_KEY, "") or "").strip() or None


def whatsapp_really_delivers() -> bool:
    """True only when a real provider is wired up. In stub mode `send_template` reports
    status='sent' having delivered nothing, so this guard is what makes fallback work."""
    return whatsapp_service.is_configured()


def notify(db, *, template, params=None, to_phone=None, to_email=None, to_name=None,
           guest_id=None, booking_id=None, client_ref=None, commit=True,
           respect_optout=None):
    """Send `template` by WhatsApp, falling back to email. Never raises.

    Returns {"channel": "whatsapp"|"email"|"none", "ok": bool, "detail": str}.
    """
    params = params or {}

    # ---- 1. WhatsApp -----------------------------------------------------
    if to_phone and whatsapp_really_delivers():
        try:
            row = whatsapp_service.send_template(
                db, to_phone, template, params, guest_id=guest_id, booking_id=booking_id,
                client_ref=client_ref, respect_optout=respect_optout, commit=commit)
            if row is not None and getattr(row, "status", None) == "sent":
                return {"channel": "whatsapp", "ok": True, "detail": "Sent on WhatsApp."}
            reason = getattr(row, "error", None) if row is not None else "send failed"
            # An opt-out is a deliberate guest choice — respect it rather than
            # routing around it by email.
            if reason == "opted_out":
                return {"channel": "none", "ok": False,
                        "detail": "The recipient has opted out of messages."}
            logger.info(f"notify: WhatsApp {template} not delivered ({reason}) — trying email")
        except Exception as e:                       # pragma: no cover - defensive
            logger.warning(f"notify: WhatsApp {template} raised: {e}")
    elif to_phone:
        logger.info(f"notify: WhatsApp not configured — trying email for {template}")

    # ---- 2. Email fallback ----------------------------------------------
    if to_email:
        try:
            from utils import email_service
            body = whatsapp_templates.render_preview(template, params)
            if not body:
                body = f"Message from Hotel Bhimas ({template})."
            ok = email_service.send_notification_email(
                to_email, _SUBJECTS.get(template, "Hotel Bhimas"), body, to_name=to_name)
            if ok:
                return {"channel": "email", "ok": True, "detail": "Sent by email."}
            return {"channel": "none", "ok": False,
                    "detail": "WhatsApp and email both unavailable."}
        except Exception as e:                       # pragma: no cover - defensive
            logger.warning(f"notify: email fallback for {template} raised: {e}")

    # ---- 3. Nothing worked. Log it, but never raise. ---------------------
    if to_phone and not whatsapp_really_delivers():
        # Stub mode still logs the message body, which is the dev-time behaviour.
        try:
            whatsapp_service.send_template(
                db, to_phone, template, params, guest_id=guest_id, booking_id=booking_id,
                client_ref=client_ref, respect_optout=respect_optout, commit=commit)
            return {"channel": "none", "ok": False,
                    "detail": "WhatsApp is not configured and no email address is on file "
                              "— the message was logged only."}
        except Exception:
            pass
    return dict(_RESULT_NONE)


def notify_guest(db, guest, *, template, params=None, setting_key=None, booking_id=None,
                 client_ref=None):
    """Transactional guest message, gated by an app_settings toggle (the shape
    `routers/reception.py::_wa_notify` had). Never raises."""
    try:
        if not guest:
            return dict(_RESULT_NONE)
        if setting_key and str(app_settings.get_setting(db, setting_key, "true")
                               ).strip().lower() in ("false", "0", "no", ""):
            return {"channel": "none", "ok": False, "detail": "This message type is switched off."}
        return notify(db, template=template, params=params,
                      to_phone=getattr(guest, "phone", None),
                      to_email=getattr(guest, "email", None),
                      to_name=getattr(guest, "name", None),
                      guest_id=getattr(guest, "guest_id", None),
                      booking_id=booking_id, client_ref=client_ref)
    except Exception as e:
        logger.warning(f"notify_guest ({template}) failed: {e}")
        return dict(_RESULT_NONE)


def notify_owner(db, *, template, params=None, client_ref=None, commit=True):
    """Owner-facing alert (approval code, fraud, cash variance, digest...). Respects the
    owner-alerts toggle, goes to the owner's WhatsApp number, falls back to their email.
    Never raises."""
    try:
        cfg = app_settings.get_whatsapp_config(db)
        if not cfg.get("owner_alerts_enabled"):
            return {"channel": "none", "ok": False, "detail": "Owner alerts are switched off."}
        return notify(db, template=template, params=params,
                      to_phone=whatsapp_service.owner_number(db),
                      to_email=owner_email(db), to_name="Owner",
                      client_ref=client_ref, commit=commit, respect_optout=False)
    except Exception as e:
        logger.warning(f"notify_owner ({template}) failed: {e}")
        return dict(_RESULT_NONE)
