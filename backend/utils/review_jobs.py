"""Scheduled Google-review pipeline (prompt 20).

Pure functions over a `db` session (the utils/whatsapp_jobs.py pattern). `run_review_jobs(db)` runs
the full sweep; each stage is idempotent so re-running (every N minutes via the in-process
APScheduler, a manual admin trigger, or `python -m scripts.review_jobs`) is safe.

Stages:
  * poll_reviews    -- fetch from GBP (services/gbp_client, off unless GBP_* env set) -> upsert
                       `google_reviews`, deduped on gbp_review_id. New rows: pending_generation.
  * generate_pending-- for pending_generation rows: generate a reply. High rating -> schedule an
                       auto-post after a randomized delay. Low rating (or a flagged high one) ->
                       approval queue, PLUS: owner WhatsApp alert, reviewer->checkout match, and an
                       auto-created complaint ticket (source=guest).
  * post_due        -- for auto rows whose scheduled_post_at has arrived: post via GBP; on success
                       auto_sent + audit; on failure retry with backoff, then mark failed.
"""
import logging
from datetime import datetime, timedelta
import random

from sqlalchemy import func

from models import GoogleReview, Booking, Guest
from utils import settings as app_settings
from utils.audit import write_audit
from services import gbp_client, review_service

logger = logging.getLogger(__name__)

_MAX_POST_RETRIES = 5


def poll_reviews(db) -> int:
    """Fetch new/updated reviews from GBP and upsert them (dedupe on gbp_review_id). No-op when the
    GBP integration is not configured. Returns the number of NEW rows inserted."""
    fetched = gbp_client.list_reviews(db)
    inserted = 0
    for r in fetched:
        gid = r.get("gbp_review_id")
        if not gid:
            continue
        row = db.query(GoogleReview).filter(GoogleReview.gbp_review_id == gid).first()
        if row is None:
            row = GoogleReview(
                gbp_review_id=gid,
                rating=int(r.get("rating") or 0),
                author_name=r.get("author_name"),
                review_text=r.get("review_text"),
                review_created_at=r.get("review_created_at"),
                reply_status="pending_generation",
            )
            db.add(row)
            inserted += 1
        else:
            # Refresh mutable fields; never clobber our reply/status once we've acted.
            row.rating = int(r.get("rating") or row.rating)
            row.author_name = r.get("author_name") or row.author_name
            row.review_text = r.get("review_text") if r.get("review_text") is not None else row.review_text
    db.commit()
    return inserted


def _match_recent_checkout(db, review: GoogleReview) -> int | None:
    """Best-effort: match a reviewer to a recent checkout by (case-insensitive) name within a
    window. Returns the matched guest_id only on a single confident match, else None. We never use
    stay details in the public reply — the match is purely for the internal follow-up ticket."""
    name = (review.author_name or "").strip()
    if len(name) < 3:
        return None
    # Reuse the review-request window idea: checkouts in the last ~30 days.
    lower = datetime.utcnow() - timedelta(days=30)
    rows = (db.query(Guest.guest_id)
            .join(Booking, Booking.guest_id == Guest.guest_id)
            .filter(Booking.status == "checked_out",
                    Booking.checked_out_at.isnot(None),
                    Booking.checked_out_at >= lower,
                    func.lower(Guest.name) == name.lower())
            .distinct().all())
    if len(rows) == 1:
        return rows[0][0]
    return None


def _schedule_post_at(cfg: dict) -> datetime:
    """A randomized time in [now+min, now+max] hours so auto-replies don't look robotic."""
    lo, hi = cfg["delay_min_hours"], cfg["delay_max_hours"]
    hours = lo if hi <= lo else random.uniform(lo, hi)
    return datetime.utcnow() + timedelta(hours=hours)


def generate_pending(db) -> int:
    """Generate replies for pending_generation rows. Returns the number processed."""
    cfg = app_settings.get_review_config(db)
    rows = db.query(GoogleReview).filter(GoogleReview.reply_status == "pending_generation").all()
    processed = 0
    for r in rows:
        try:
            text, mode, needs_approval = review_service.generate_reply(db, r)
            r.reply_text = text
            r.reply_mode = mode
            if needs_approval:
                r.reply_status = "pending_approval"
                r.scheduled_post_at = None
            else:
                # auto-post after the randomized delay window (only reachable for high ratings)
                r.scheduled_post_at = _schedule_post_at(cfg)
                # stays pending_generation-conceptually; post_due drives it to auto_sent

            # Low-rating loop-in: owner alert + reviewer match + complaint ticket.
            if r.rating < cfg["auto_reply_threshold"]:
                review_service.send_low_review_alert(db, r)
                if r.matched_guest_id is None:
                    r.matched_guest_id = _match_recent_checkout(db, r)
                if r.ticket_id is None:
                    _open_complaint_ticket(db, r)
            db.commit()
            processed += 1
        except Exception as e:
            db.rollback()
            logger.error(f"review generate failed for #{r.id}: {e}")
    return processed


def _open_complaint_ticket(db, review: GoogleReview):
    """Auto-create a source=guest complaint ticket so a negative review is actually followed up,
    not just replied to publicly. Idempotent on client_ref."""
    from routers.maintenance import create_guest_ticket
    booking_id = None
    if review.matched_guest_id:
        b = (db.query(Booking)
             .filter(Booking.guest_id == review.matched_guest_id)
             .order_by(Booking.booking_id.desc()).first())
        booking_id = b.booking_id if b else None
    issue = (f"Negative Google review ({review.rating}★) from "
             f"{review.author_name or 'a guest'} — follow up with the guest.")
    ticket = create_guest_ticket(
        db, issue=issue, booking_id=booking_id,
        category="other", priority="high",
        client_ref=f"review:{review.id}", commit=False)
    review.ticket_id = ticket.id


def _post_one(db, review: GoogleReview) -> bool:
    """Post a review's reply via GBP. Returns True on success. Updates status/audit/retry."""
    ok, err = gbp_client.post_reply(db, review.gbp_review_id, review.reply_text or "")
    if ok:
        review.reply_status = "approved_sent" if review.reply_mode == "approved" else "auto_sent"
        review.replied_at = datetime.utcnow()
        review.last_error = None
        write_audit(db, None, "review.reply_post", "google_review", review.id,
                    after={"rating": review.rating, "mode": review.reply_mode,
                           "status": review.reply_status}, client="scheduler", commit=False)
        return True
    review.retry_count = (review.retry_count or 0) + 1
    review.last_error = err
    if review.retry_count >= _MAX_POST_RETRIES:
        review.reply_status = "failed"
    return False


def post_due(db) -> int:
    """Post auto-reply rows whose scheduled_post_at has arrived. Returns the number posted."""
    now = datetime.utcnow()
    rows = (db.query(GoogleReview)
            .filter(GoogleReview.reply_mode == "auto",
                    GoogleReview.reply_status.in_(("pending_generation", "failed")),
                    GoogleReview.reply_text.isnot(None),
                    GoogleReview.scheduled_post_at.isnot(None),
                    GoogleReview.scheduled_post_at <= now,
                    GoogleReview.retry_count < _MAX_POST_RETRIES).all())
    posted = 0
    for r in rows:
        try:
            if _post_one(db, r):
                posted += 1
            db.commit()
        except Exception as e:
            db.rollback()
            logger.error(f"review post failed for #{r.id}: {e}")
    return posted


def post_approved(db, review: GoogleReview) -> tuple[bool, str | None]:
    """Post an admin-approved draft immediately (called from the router on approve). Commits."""
    ok = _post_one(db, review)
    db.commit()
    return ok, review.last_error


_STAGES = [
    ("poll", poll_reviews),
    ("generate", generate_pending),
    ("post", post_due),
]


def run_review_jobs(db) -> dict:
    """Run the full sweep once. Each stage is isolated so one failing never blocks the rest."""
    result = {}
    for name, fn in _STAGES:
        try:
            result[name] = fn(db)
        except Exception as e:
            logger.error(f"review_jobs: {name} failed: {e}")
            result[name] = f"error: {e}"
    logger.info(f"review_jobs sweep: {result}")
    return result
