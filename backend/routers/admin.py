from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from jose import jwt
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from passlib.context import CryptContext
import os
import logging
from dotenv import load_dotenv
from database import SessionLocal
from models import User

logger = logging.getLogger(__name__)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

router = APIRouter(prefix="/admin")
load_dotenv()

SECRET_KEY = os.getenv("SECRET_KEY")
ALGORITHM = "HS256"

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


class LoginSchema(BaseModel):
    username: str
    password: str


def _issue_access_token(user: User) -> str:
    return jwt.encode(
        {
            "sub": user.username,
            "role": user.role,
            "exp": datetime.utcnow() + timedelta(hours=8),
        },
        SECRET_KEY,
        algorithm=ALGORITHM,
    )


@router.post("/login")
def admin_login(data: LoginSchema, db: Session = Depends(get_db)):
    """Login endpoint (rate limiting handled by middleware).

    When the user has TOTP 2FA enabled (prompt 18), the password step returns a short-lived
    `challenge` token and `twofa_required: true` instead of an access token; the client then
    POSTs the 6-digit code to /admin/login/2fa to receive the real access token."""

    # 🔎 Find user in DB
    user = db.query(User).filter(User.username == data.username).first()

    if not user:
        logger.warning(f"Failed login attempt for user: {data.username}")
        raise HTTPException(status_code=401, detail="Invalid credentials")

    # 🔐 Verify hashed password
    if not pwd_context.verify(data.password, user.password_hash):
        logger.warning(f"Failed login attempt for user: {data.username}")
        raise HTTPException(status_code=401, detail="Invalid credentials")

    # 🚫 Deactivated staff can't log in (additive hardening, prompt 18)
    if user.is_active is False:
        logger.warning(f"Login blocked for deactivated user: {data.username}")
        raise HTTPException(status_code=403, detail="Account is deactivated")

    # 🔐 2FA: if enabled, hand back a short-lived challenge instead of an access token
    if user.totp_enabled:
        challenge = jwt.encode(
            {
                "sub": user.username,
                "twofa_pending": True,
                "exp": datetime.utcnow() + timedelta(minutes=5),
            },
            SECRET_KEY,
            algorithm=ALGORITHM,
        )
        return {"twofa_required": True, "challenge": challenge}

    logger.info(f"Successful login for user: {user.username}")
    return {"access_token": _issue_access_token(user)}


class TwoFactorLoginSchema(BaseModel):
    challenge: str
    code: str


@router.post("/login/2fa")
def admin_login_2fa(data: TwoFactorLoginSchema, db: Session = Depends(get_db)):
    """Second factor: exchange a valid password-stage `challenge` + TOTP code for an access token."""
    from jose import JWTError
    from utils import twofa as _twofa

    try:
        payload = jwt.decode(data.challenge, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        raise HTTPException(status_code=401, detail="Challenge expired — please log in again")
    if not payload.get("twofa_pending"):
        raise HTTPException(status_code=401, detail="Invalid challenge")

    user = db.query(User).filter(User.username == payload.get("sub")).first()
    if not user or not user.totp_enabled:
        raise HTTPException(status_code=401, detail="Invalid challenge")
    if user.is_active is False:
        raise HTTPException(status_code=403, detail="Account is deactivated")
    if not _twofa.verify(user.totp_secret or "", data.code):
        logger.warning(f"Failed 2FA attempt for user: {user.username}")
        raise HTTPException(status_code=401, detail="Invalid authentication code")

    logger.info(f"Successful 2FA login for user: {user.username}")
    return {"access_token": _issue_access_token(user)}
