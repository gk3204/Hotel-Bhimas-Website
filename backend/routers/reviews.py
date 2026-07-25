"""Google review auto-reply endpoints (prompt 20, admin-only).

Reputation management for the hotel's Google reviews:
  * inbox + approval queue for the drafts our poller/generator produced
  * approve/skip/regenerate a draft
  * config (thresholds, delay window, per-band auto-send, LLM on/off, cadence, owner alerts)
  * manual poll trigger
  * a dev-only "inject test review" path so the whole pipeline is verifiable without live GBP
  * weekly rating trend + unanswered count for the owner dashboard (prompt 16)

Public replies are posted through services/gbp_client (real GBP API only when GBP_* env is set,
else stub-logged). See utils/review_jobs.py for the poll -> generate -> post pipeline.
"""
import logging
import os
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from database import SessionLocal
from models import GoogleReview, Guest, MaintenanceTicket
from utils.auth_utils import require_admin
from utils.audit import write_audit
from utils import settings as app_settings
from utils import review_jobs
from services import gbp_client, review_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/reviews", tags=["Reviews"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _test_inject_enabled() -> bool:
    # On by default in non-prod; document turning it OFF in prod (see how-to-test).
    return os.getenv("REVIEW_TEST_INJECT_ENABLED", "true").strip().lower() not in ("false", "0", "no")


def _review_dict(db: Session, r: GoogleReview) -> dict:
    guest = None
    if r.matched_guest_id:
        g = db.query(Guest).filter(Guest.guest_id == r.matched_guest_id).first()
        if g:
            guest = {"guest_id": g.guest_id, "name": g.name, "phone": g.phone}
    ticket = None
    if r.ticket_id:
        t = db.query(MaintenanceTicket).filter(MaintenanceTicket.id == r.ticket_id).first()
        if t:
            ticket = {"id": t.id, "status": t.status, "priority": t.priority}
    return {
        "id": r.id,
        "gbp_review_id": r.gbp_review_id,
        "rating": r.rating,
        "author_name": r.author_name,
        "review_text": r.review_text,
        "review_created_at": r.review_created_at.isoformat() if r.review_created_at else None,
        "matched_guest": guest,
        "ticket": ticket,
        "reply_text": r.reply_text,
        "reply_mode": r.reply_mode,
        "reply_status": r.reply_status,
        "scheduled_post_at": r.scheduled_post_at.isoformat() if r.scheduled_post_at else None,
        "replied_at": r.replied_at.isoformat() if r.replied_at else None,
        "last_error": r.last_error,
        "retry_count": r.retry_count,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


# --------------------------------------------------------------------- inbox / queue

@router.get("/")
def list_reviews(status: str | None = Query(None),
                 rating_max: int | None = Query(None),
                 pending_approval: bool = Query(False),
                 db: Session = Depends(get_db), user=Depends(require_admin)):
    """Review inbox. Filters: reply_status, rating<=rating_max, or only the pending-approval queue."""
    q = db.query(GoogleReview)
    if pending_approval:
        q = q.filter(GoogleReview.reply_status == "pending_approval")
    elif status:
        q = q.filter(GoogleReview.reply_status == status)
    if rating_max is not None:
        q = q.filter(GoogleReview.rating <= rating_max)
    rows = q.order_by(GoogleReview.created_at.desc(), GoogleReview.id.desc()).limit(500).all()
    counts = dict(db.query(GoogleReview.reply_status, func.count(GoogleReview.id))
                  .group_by(GoogleReview.reply_status).all())
    return {"data": [_review_dict(db, r) for r in rows], "counts": counts}


@router.get("/{review_id}")
def get_review(review_id: int, db: Session = Depends(get_db), user=Depends(require_admin)):
    r = db.query(GoogleReview).filter(GoogleReview.id == review_id).first()
    if not r:
        raise HTTPException(404, "Review not found")
    return _review_dict(db, r)


class ApproveRequest(BaseModel):
    reply_text: str | None = None  # optional edit before posting


@router.post("/{review_id}/approve")
def approve_reply(review_id: int, data: ApproveRequest,
                  db: Session = Depends(get_db), user=Depends(require_admin)):
    """Edit (optional) + post a draft now → approved_sent."""
    r = db.query(GoogleReview).filter(GoogleReview.id == review_id).first()
    if not r:
        raise HTTPException(404, "Review not found")
    if r.reply_status in ("auto_sent", "approved_sent"):
        raise HTTPException(409, "Reply already posted")
    if data.reply_text and data.reply_text.strip():
        r.reply_text = data.reply_text.strip()
    if not (r.reply_text or "").strip():
        raise HTTPException(400, "No reply text to post")
    r.reply_mode = "approved"
    ok, err = review_jobs.post_approved(db, r)
    write_audit(db, user, "review.approve", "google_review", r.id,
                after={"posted": ok, "error": err}, client="web", commit=True)
    if not ok:
        raise HTTPException(502, f"Failed to post reply: {err or 'unknown error'}")
    return _review_dict(db, r)


class SkipRequest(BaseModel):
    reason: str | None = None


@router.post("/{review_id}/skip")
def skip_reply(review_id: int, data: SkipRequest,
               db: Session = Depends(get_db), user=Depends(require_admin)):
    """Skip a draft (won't be posted)."""
    r = db.query(GoogleReview).filter(GoogleReview.id == review_id).first()
    if not r:
        raise HTTPException(404, "Review not found")
    if r.reply_status in ("auto_sent", "approved_sent"):
        raise HTTPException(409, "Reply already posted")
    r.reply_status = "skipped"
    r.scheduled_post_at = None
    write_audit(db, user, "review.skip", "google_review", r.id,
                after={"reason": (data.reason or "")[:250]}, client="web", commit=True)
    return _review_dict(db, r)


@router.post("/{review_id}/regenerate")
def regenerate_reply(review_id: int, db: Session = Depends(get_db), user=Depends(require_admin)):
    """Re-run reply generation for a not-yet-posted review (e.g. after toggling LLM/tone)."""
    r = db.query(GoogleReview).filter(GoogleReview.id == review_id).first()
    if not r:
        raise HTTPException(404, "Review not found")
    if r.reply_status in ("auto_sent", "approved_sent"):
        raise HTTPException(409, "Reply already posted")
    text, mode, needs_approval = review_service.generate_reply(db, r)
    r.reply_text, r.reply_mode = text, mode
    r.reply_status = "pending_approval" if needs_approval else r.reply_status
    db.commit()
    write_audit(db, user, "review.regenerate", "google_review", r.id, client="web", commit=True)
    return _review_dict(db, r)


# --------------------------------------------------------------------- config

# Friendly config key -> app_settings storage key (secrets stay in ENV, never here).
_CFG_KEYS = {
    "auto_reply_enabled": app_settings.REVIEW_AUTO_REPLY_KEY,
    "auto_reply_threshold": app_settings.REVIEW_THRESHOLD_KEY,
    "low_band_split": app_settings.REVIEW_LOW_BAND_SPLIT_KEY,
    "delay_min_hours": app_settings.REVIEW_DELAY_MIN_HOURS_KEY,
    "delay_max_hours": app_settings.REVIEW_DELAY_MAX_HOURS_KEY,
    "low_auto_send": app_settings.REVIEW_LOW_AUTO_SEND_KEY,
    "llm_enabled": app_settings.REVIEW_LLM_ENABLED_KEY,
    "poll_interval_minutes": app_settings.REVIEW_POLL_INTERVAL_KEY,
    "owner_alerts_enabled": app_settings.REVIEW_OWNER_ALERTS_KEY,
}


class ReviewConfigUpdate(BaseModel):
    auto_reply_enabled: bool | None = None
    auto_reply_threshold: int | None = None
    low_band_split: int | None = None
    delay_min_hours: float | None = None
    delay_max_hours: float | None = None
    low_auto_send: bool | None = None
    llm_enabled: bool | None = None
    poll_interval_minutes: int | None = None
    owner_alerts_enabled: bool | None = None


@router.get("/config")
def get_config(db: Session = Depends(get_db), user=Depends(require_admin)):
    cfg = app_settings.get_review_config(db)
    cfg["gbp_provider"] = gbp_client.provider_status()
    cfg["llm_configured"] = bool(os.getenv("ANTHROPIC_API_KEY"))
    cfg["test_inject_enabled"] = _test_inject_enabled()
    cfg["templates"] = {
        "thank_you": review_service.THANKYOU_TEMPLATES,
        "recovery_low": review_service.RECOVERY_TEMPLATES_LOW,
        "recovery_mid": review_service.RECOVERY_TEMPLATES_MID,
    }
    return cfg


@router.put("/config")
def put_config(data: ReviewConfigUpdate, db: Session = Depends(get_db), user=Depends(require_admin)):
    payload = data.model_dump(exclude_unset=True)
    for friendly, value in payload.items():
        key = _CFG_KEYS.get(friendly)
        if not key:
            continue
        if isinstance(value, bool):
            value = "true" if value else "false"
        app_settings.set_setting(db, key, value, user=user)
    db.commit()
    write_audit(db, user, "review.config_update", "settings", "reviews",
                after=payload, client="web", commit=True)
    return get_config(db, user)


# --------------------------------------------------------------------- poll / test / trends

@router.post("/poll")
def poll_now(db: Session = Depends(get_db), user=Depends(require_admin)):
    """Manually run the poll → generate → post sweep."""
    result = review_jobs.run_review_jobs(db)
    write_audit(db, user, "review.poll", "reviews", "manual", after=result, client="web", commit=True)
    return {"result": result}


class InjectRequest(BaseModel):
    rating: int
    author_name: str | None = None
    review_text: str | None = None


@router.post("/_test/inject")
def inject_test_review(data: InjectRequest, db: Session = Depends(get_db), user=Depends(require_admin)):
    """DEV ONLY: inject a fake review so the pipeline is verifiable without live GBP. Gate with
    REVIEW_TEST_INJECT_ENABLED=false in production."""
    if not _test_inject_enabled():
        raise HTTPException(403, "Test injection disabled (REVIEW_TEST_INJECT_ENABLED=false)")
    if data.rating < 1 or data.rating > 5:
        raise HTTPException(400, "rating must be 1-5")
    gid = f"test-{int(datetime.utcnow().timestamp() * 1000)}"
    r = GoogleReview(
        gbp_review_id=gid, rating=data.rating,
        author_name=data.author_name or "Test Reviewer",
        review_text=data.review_text, review_created_at=datetime.utcnow(),
        reply_status="pending_generation")
    db.add(r)
    db.commit()
    db.refresh(r)
    write_audit(db, user, "review.test_inject", "google_review", r.id,
                after={"rating": r.rating}, client="web", commit=True)
    return _review_dict(db, r)


@router.get("/trends/weekly")
def weekly_trends(db: Session = Depends(get_db), user=Depends(require_admin)):
    """7-day rating averages + count of unanswered reviews, for the owner dashboard."""
    today = datetime.utcnow().date()
    days = []
    for i in range(6, -1, -1):
        day = today - timedelta(days=i)
        start = datetime.combine(day, datetime.min.time())
        end = start + timedelta(days=1)
        rows = (db.query(func.avg(GoogleReview.rating), func.count(GoogleReview.id))
                .filter(GoogleReview.review_created_at >= start,
                        GoogleReview.review_created_at < end).first())
        avg, cnt = rows or (None, 0)
        days.append({"date": day.isoformat(),
                     "avg_rating": round(float(avg), 2) if avg is not None else None,
                     "count": int(cnt or 0)})
    unanswered = (db.query(func.count(GoogleReview.id))
                  .filter(GoogleReview.reply_status.in_(
                      ("pending_generation", "pending_approval", "failed"))).scalar() or 0)
    return {"days": days, "unanswered_count": int(unanswered)}
