"""Admin 2FA (TOTP) enrolment (prompt 18, slice 6).

Endpoints (all admin-gated) for turning TOTP two-factor auth on/off for the logged-in admin:
  POST /admin/2fa/enroll   -> generate a secret, return the otpauth URI + a QR PNG data-URI
  POST /admin/2fa/confirm  -> verify the first code, flip totp_enabled on
  POST /admin/2fa/disable  -> turn 2FA off (verify a current code first)
  GET  /admin/2fa/status   -> {enabled, available, required}

The *login* challenge/verify lives in routers/admin.py (it must run before a full token exists).
Every state change is audited (append-only) via utils/audit.write_audit.
"""
from datetime import datetime

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import SessionLocal
from models import User
from utils.auth_utils import require_admin
from utils.audit import write_audit
from utils.settings import get_compliance_config
from utils import twofa

router = APIRouter(prefix="/admin/2fa", tags=["admin-2fa"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class CodeIn(BaseModel):
    code: str


def _current_user_row(db: Session, payload: dict) -> User:
    user = db.query(User).filter(User.username == payload.get("sub")).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@router.get("/status")
def twofa_status(payload=Depends(require_admin), db: Session = Depends(get_db)):
    user = _current_user_row(db, payload)
    cfg = get_compliance_config(db)
    return {
        "enabled": bool(user.totp_enabled),
        "available": twofa.available(),
        "required": cfg["admin_2fa_required"],
        "enrolled_at": user.totp_enrolled_at.isoformat() if user.totp_enrolled_at else None,
    }


@router.post("/enroll")
def twofa_enroll(payload=Depends(require_admin), db: Session = Depends(get_db)):
    """Generate a new secret for the current admin and return the enrolment QR.
    Does NOT enable 2FA yet — /confirm does, after the admin proves they scanned it."""
    if not twofa.available():
        raise HTTPException(status_code=503, detail="2FA is unavailable (pyotp not installed)")
    user = _current_user_row(db, payload)
    secret = twofa.new_secret()
    user.totp_secret = secret          # stored but inert until confirmed
    user.totp_enabled = False
    db.commit()
    uri = twofa.provisioning_uri(secret, user.username)
    write_audit(db, payload, "admin.2fa.enroll", "user", str(user.user_id), commit=True)
    return {"secret": secret, "otpauth_uri": uri, "qr_data_uri": twofa.qr_data_uri(uri)}


@router.post("/confirm")
def twofa_confirm(data: CodeIn, payload=Depends(require_admin), db: Session = Depends(get_db)):
    user = _current_user_row(db, payload)
    if not user.totp_secret:
        raise HTTPException(status_code=400, detail="Start enrolment first (POST /admin/2fa/enroll)")
    if not twofa.verify(user.totp_secret, data.code):
        raise HTTPException(status_code=400, detail="Invalid code — check the time on your phone and retry")
    user.totp_enabled = True
    user.totp_enrolled_at = datetime.utcnow()
    db.commit()
    write_audit(db, payload, "admin.2fa.confirm", "user", str(user.user_id), commit=True)
    return {"enabled": True}


@router.post("/disable")
def twofa_disable(data: CodeIn, payload=Depends(require_admin), db: Session = Depends(get_db)):
    user = _current_user_row(db, payload)
    if not user.totp_enabled:
        return {"enabled": False}
    # Require a valid current code to switch it off (prevents a hijacked session from disabling it).
    if not twofa.verify(user.totp_secret or "", data.code):
        raise HTTPException(status_code=400, detail="Invalid code")
    user.totp_enabled = False
    user.totp_secret = None
    user.totp_enrolled_at = None
    db.commit()
    write_audit(db, payload, "admin.2fa.disable", "user", str(user.user_id), commit=True)
    return {"enabled": False}
