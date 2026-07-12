"""Anti-fraud / internal-controls endpoints (fraud_alerts dashboard, reconciliation, OTP approvals).
Admin-only. Skeleton created in Milestone 0 (prompt 01). Implemented in prompt 11.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from database import SessionLocal
from utils.auth_utils import require_admin

router = APIRouter(prefix="/fraud", tags=["Anti-Fraud"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("/health", dependencies=[Depends(require_admin)])
def health():
    return {"status": "ok", "module": "fraud"}
