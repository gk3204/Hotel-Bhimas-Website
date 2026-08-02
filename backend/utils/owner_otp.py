"""Owner-approval one-time codes (prompt 11).

Sensitive front-desk actions (refund, below-floor discount, ...) can require an owner
OTP before they proceed. The flow:
    1. desk calls POST /fraud/otp/request   -> create_otp(...) stores a row, returns otp_id
    2. the 6-digit code is delivered on-screen (admin Approvals inbox) + server log
       (THIS build's interim channel; prompt 15 swaps in WhatsApp behind create_otp)
    3. desk re-submits the action with owner_otp_id + owner_otp_code
    4. the gated endpoint calls consume_otp(...) which validates + single-uses the code

Enforcement is opt-in per action via env flags (default OFF) so existing behaviour and
the prompt-08 tests are unchanged until an owner turns it on.
"""
import json
import logging
import os
import secrets
from datetime import datetime, timedelta

from fastapi import HTTPException

from models import OwnerOtp
from utils.audit import _resolve_user_id

logger = logging.getLogger(__name__)


def _ttl_minutes() -> int:
    try:
        return max(1, int(os.getenv("OWNER_OTP_TTL_MINUTES", "10")))
    except (TypeError, ValueError):
        return 10


def deliver_otp(db, otp, commit: bool = True) -> dict:
    """Push the code to the owner's WhatsApp and report honestly whether it left the
    building (backlog v2 F-C).

    The on-screen Approvals inbox + server log are the channel that ALWAYS works. WhatsApp
    is best-effort on top and must never break the OTP flow, so every failure is swallowed
    — but the caller gets a truthful `{channel, ok, detail}` back so the desk can tell the
    receptionist "sent to the owner's WhatsApp" instead of guessing.

    Note the stub driver reports status="sent" while delivering nothing, so a send only
    counts as real when a provider is actually configured.
    """
    logger.info(f"🔐 Owner OTP for action={otp.action} otp_id={otp.id}: code={otp.code} "
                f"(expires {otp.expires_at.isoformat()})")
    try:
        from utils import whatsapp_service
        if not whatsapp_service.is_configured():
            return {"channel": "onscreen", "ok": False,
                    "detail": "WhatsApp is not configured — read the code from the admin Approvals inbox."}
        row = whatsapp_service.send_owner_otp(db, otp, commit=commit)
        if row is None:
            return {"channel": "onscreen", "ok": False,
                    "detail": "Owner alerts are off or no owner number is set — "
                              "read the code from the admin Approvals inbox."}
        if getattr(row, "status", None) == "sent":
            return {"channel": "whatsapp", "ok": True,
                    "detail": "Approval code sent to the owner's WhatsApp."}
        return {"channel": "onscreen", "ok": False,
                "detail": f"WhatsApp send failed ({getattr(row, 'error', None) or 'unknown'}) — "
                          f"read the code from the admin Approvals inbox."}
    except Exception as e:
        logger.warning(f"owner OTP WhatsApp delivery failed (on-screen still works): {e}")
        return {"channel": "onscreen", "ok": False,
                "detail": "WhatsApp delivery failed — read the code from the admin Approvals inbox."}


def create_otp(db, action: str, context, user, commit: bool = True, return_delivery: bool = False):
    """Create + persist an owner-approval code for `action`. The code is logged (interim
    delivery) but never returned to the requester — the owner reads it from the admin
    Approvals inbox. Caller is responsible for the surrounding transaction if commit=False.

    Returns the `OwnerOtp`, or `(otp, delivery_dict)` when `return_delivery=True`."""
    code = f"{secrets.randbelow(1_000_000):06d}"
    otp = OwnerOtp(
        action=action,
        context=json.dumps(context, default=str) if context is not None else None,
        code=code,
        expires_at=datetime.utcnow() + timedelta(minutes=_ttl_minutes()),
        used=False,
        created_by=_resolve_user_id(db, user),
    )
    db.add(otp)
    db.flush()
    if commit:
        db.commit()
    delivery = deliver_otp(db, otp, commit=commit)
    return (otp, delivery) if return_delivery else otp


def consume_otp(db, otp_id, code, action: str, user=None):
    """Validate + single-use an owner OTP for `action`. Raises HTTPException on any
    failure (missing, wrong action, expired, already used, wrong code). On success the
    row is marked used and the caller may proceed. Does NOT commit — it joins the caller's
    transaction so the OTP is only spent if the action itself commits."""
    if not otp_id or not code:
        raise HTTPException(status_code=403,
                            detail="owner_otp_required: this action needs an owner approval code")
    otp = db.query(OwnerOtp).filter(OwnerOtp.id == otp_id).first()
    if not otp or otp.action != action:
        raise HTTPException(status_code=403, detail="Invalid owner approval code")
    if otp.used:
        raise HTTPException(status_code=403, detail="Owner approval code already used")
    if otp.expires_at < datetime.utcnow():
        raise HTTPException(status_code=403, detail="Owner approval code expired")
    if not secrets.compare_digest(str(otp.code), str(code)):
        raise HTTPException(status_code=403, detail="Invalid owner approval code")
    otp.used = True
    otp.used_at = datetime.utcnow()
    otp.used_by = _resolve_user_id(db, user)
    db.flush()
    return otp
