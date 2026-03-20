from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from jose import jwt
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from passlib.context import CryptContext
import os
import logging
from dotenv import load_dotenv
from slowapi import Limiter
from slowapi.util import get_remote_address
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

# Rate limiting
limiter = Limiter(key_func=get_remote_address)

SECRET_KEY = os.getenv("SECRET_KEY")
ALGORITHM = "HS256"

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


class LoginSchema(BaseModel):
    username: str
    password: str


@router.post("/login")
@limiter.limit("5/minute")
def admin_login(request, data: LoginSchema, db: Session = Depends(get_db)):
    """Login endpoint with rate limiting (5 attempts per minute)"""
    
    # 🔎 Find user in DB
    user = db.query(User).filter(User.username == data.username).first()

    if not user:
        logger.warning(f"Failed login attempt for user: {data.username}")
        raise HTTPException(status_code=401, detail="Invalid credentials")

    # 🔐 Verify hashed password
    if not pwd_context.verify(data.password, user.password_hash):
        logger.warning(f"Failed login attempt for user: {data.username}")
        raise HTTPException(status_code=401, detail="Invalid credentials")

        
    # 🎟 Generate JWT
    token = jwt.encode(
        {
            "sub": user.username,
            "role": user.role,
            "exp": datetime.utcnow() + timedelta(hours=8),
        },
        SECRET_KEY,
        algorithm=ALGORITHM,
    )

    logger.info(f"Successful login for user: {user.username}")
    return {"access_token": token}
