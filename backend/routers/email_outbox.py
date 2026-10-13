"""v7d: the e-mails Mailjet refused (daily limit, outage) and their automatic re-sends - admin only."""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from database import SessionLocal
from models import EmailOutbox
from services import email_outbox as OB
from utils import clock
from utils.auth_utils import require_admin

router = APIRouter(prefix="/admin/email-outbox", tags=["Email outbox"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("", dependencies=[Depends(require_admin)])
def list_outbox(status: str = Query("pending"), limit: int = Query(100, ge=1, le=500),
                db: Session = Depends(get_db)):
    q = db.query(EmailOutbox)
    if status != "all":
        q = q.filter(EmailOutbox.status == status)
    rows = q.order_by(EmailOutbox.id.desc()).limit(limit).all()
    return {**OB.summary(db), "rows": [{
        "id": r.id, "kind": r.kind, "to": r.to_email, "subject": r.subject, "status": r.status,
        "attempts": r.attempts, "last_error": r.last_error,
        "created_at": clock.to_local(r.created_at).isoformat() if r.created_at else None,
        "next_try_at": clock.to_local(r.next_try_at).isoformat() if r.next_try_at else None,
        "sent_at": clock.to_local(r.sent_at).isoformat() if r.sent_at else None,
    } for r in rows]}


@router.post("/retry", dependencies=[Depends(require_admin)])
def retry_now(db: Session = Depends(get_db)):
    """Try every waiting e-mail now (not just the due ones)."""
    from datetime import datetime
    for r in db.query(EmailOutbox).filter(EmailOutbox.status == "pending").all():
        r.next_try_at = datetime.utcnow()
    db.commit()
    return {**OB.retry_due(db), **{"summary": OB.summary(db)}}
