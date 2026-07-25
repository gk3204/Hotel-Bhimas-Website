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


def create_otp(db, action: str, context, user, commit: bool = True) -> OwnerOtp:
    """Create + persist an owner-approval code for `action`. The code is logged (interim
    delivery) but never returned to the requester — the owner reads it from the admin
    Approvals inbox. Caller is responsible for the surrounding transaction if commit=False."""
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
    # Delivery: the owner reads this from the admin dashboard / server log (always works) AND,
    # when a WhatsApp provider + owner number are configured, via WhatsApp (prompt 15). The
    # WhatsApp send is best-effort — it must never break the OTP flow.
    logger.info(f"🔐 Owner OTP for action={action} otp_id={otp.id}: code={code} "
                f"(expires {otp.expires_at.isoformat()})")
    try:
        from utils import whatsapp_service
        whatsapp_service.send_owner_otp(db, otp, commit=commit)
    except Exception as e:
        logger.warning(f"owner OTP WhatsApp delivery failed (on-screen still works): {e}")
    return otp


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
