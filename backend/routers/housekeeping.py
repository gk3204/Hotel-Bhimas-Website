"""Housekeeping endpoints (room cleaning status, tasks, maintenance tickets).
Set by the housekeeper role only (reception cannot change cleaning status — anti-fraud).
Skeleton created in Milestone 0 (prompt 01). Implemented in prompt 13.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from database import SessionLocal
from utils.auth_utils import require_housekeeper_or_admin

router = APIRouter(prefix="/housekeeping", tags=["Housekeeping"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("/health", dependencies=[Depends(require_housekeeper_or_admin)])
def health():
    return {"status": "ok", "module": "housekeeping"}
