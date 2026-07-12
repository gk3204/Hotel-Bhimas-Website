"""Cash & shift management (opening balance, expenses, closing reconciliation).
Skeleton created in Milestone 0 (prompt 01). Implemented in prompt 12.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from database import SessionLocal
from utils.auth_utils import require_reception_or_admin

router = APIRouter(prefix="/cash-shift", tags=["Cash & Shift"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("/health", dependencies=[Depends(require_reception_or_admin)])
def health():
    return {"status": "ok", "module": "cash_shift"}
