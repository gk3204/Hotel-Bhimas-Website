"""v7d (owner, 13 Oct): "automatic resend email for those where mailjet per day limit is over".

Every e-mail goes through utils.email_service._send_mj. When Mailjet refuses one for a reason that
can pass - the free plan's 200-a-day limit (production hit it every day 4-7 Oct, when an alert flood
used it up and booking 381's confirmation never went), a rate limit, an outage, a dropped
connection - the exact request is kept here instead of being lost, and a sweep re-sends it:
roughly hourly per message, for up to RETRY_HOURS, oldest first, stopping at the first refusal
while the limit still holds. A permanent refusal (a bad address) is not queued.
"""
import json
import logging
from datetime import datetime, timedelta

from sqlalchemy import text

logger = logging.getLogger(__name__)

RETRY_HOURS = 72
RETRY_EVERY_MINUTES = 60
SWEEP_BATCH = 30
_LOCK_KEY = 0x0E4A1B0C


def retryable(status_code, body_text: str) -> bool:
    """Is a Mailjet refusal worth trying again later?"""
    try:
        code = int(status_code)
    except (TypeError, ValueError):
        return True
    if code == 429 or code >= 500 or code in (401, 403):
        return True
    low = (body_text or "").lower()
    return any(w in low for w in ("limit", "quota", "exceed", "throttl", "too many"))


def enqueue(data: dict, kind: str, error: str) -> None:
    """Keep a refused e-mail for the sweep. Own session; never raises."""
    try:
        from database import SessionLocal
        from models import EmailOutbox
        msg = ((data or {}).get("Messages") or [{}])[0]
        to = ",".join(t.get("Email", "") for t in (msg.get("To") or []))
        db = SessionLocal()
        try:
            db.add(EmailOutbox(kind=(kind or "email")[:40], to_email=to[:500],
                               subject=(msg.get("Subject") or "")[:300], payload=json.dumps(data),
                               status="pending", attempts=0, last_error=(error or "")[:500],
                               created_at=datetime.utcnow(),
                               next_try_at=datetime.utcnow() + timedelta(minutes=RETRY_EVERY_MINUTES)))
            db.commit()
        finally:
            db.close()
        logger.warning(f"email outbox: queued a {kind} e-mail to {to} for a later retry ({error})")
    except Exception as e:                      # queueing must never break the caller
        logger.error(f"email outbox: could not queue a {kind} e-mail: {e}")


def _client_for(kind):
    from utils import email_service as ES
    return ES.enquiry_mailjet if kind == "enquiry" else ES.mailjet


def retry_due(db, limit: int = SWEEP_BATCH) -> dict:
    """Re-send the due e-mails. Returns {"tried", "sent", "still_pending", "given_up", "blocked"}."""
    from models import EmailOutbox
    from utils.email_service import _safe_mode
    out = {"tried": 0, "sent": 0, "still_pending": 0, "given_up": 0, "blocked": False}
    if _safe_mode():
        return out
    now = datetime.utcnow()
    rows = (db.query(EmailOutbox)
            .filter(EmailOutbox.status == "pending", EmailOutbox.next_try_at <= now)
            .order_by(EmailOutbox.id).limit(limit).all())
    for row in rows:
        out["tried"] += 1
        row.attempts = int(row.attempts or 0) + 1
        try:
            result = _client_for(row.kind).send.create(data=json.loads(row.payload))
            code = result.status_code
            try:
                body = json.dumps(result.json())
            except Exception:
                body = ""
        except Exception as e:
            code, body = None, f"send error: {e}"
        if code == 200:
            row.status, row.sent_at, row.last_error = "sent", datetime.utcnow(), None
            out["sent"] += 1
            db.commit()
            continue
        row.last_error = (f"HTTP {code}: {body}" if code else body)[:500]
        if not retryable(code, body):
            row.status = "failed"
            out["given_up"] += 1
        elif (row.created_at or now) < now - timedelta(hours=RETRY_HOURS):
            row.status = "failed"
            out["given_up"] += 1
        else:
            row.next_try_at = now + timedelta(minutes=RETRY_EVERY_MINUTES)
            out["still_pending"] += 1
        db.commit()
        if code != 200 and retryable(code, body):
            out["blocked"] = True      # still at the limit - the rest would be refused too
            break
    return out


def retry_due_locked(db) -> dict | None:
    """The scheduled sweep: one worker at a time (uvicorn runs several), never raises."""
    lock_conn = None
    try:
        lock_conn = db.get_bind().connect()
        if not lock_conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": _LOCK_KEY}).scalar():
            return None
        return retry_due(db)
    except Exception as e:
        logger.error(f"email outbox sweep failed: {e}")
        try:
            db.rollback()
        except Exception:
            pass
        return None
    finally:
        if lock_conn is not None:
            try:
                lock_conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": _LOCK_KEY})
                lock_conn.close()
            except Exception:
                pass


def summary(db) -> dict:
    from models import EmailOutbox
    from sqlalchemy import func
    counts = dict(db.query(EmailOutbox.status, func.count(EmailOutbox.id)).group_by(EmailOutbox.status).all())
    return {"pending": int(counts.get("pending", 0)), "sent": int(counts.get("sent", 0)),
            "failed": int(counts.get("failed", 0))}
