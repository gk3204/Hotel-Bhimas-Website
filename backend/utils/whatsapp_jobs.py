"""Scheduled WhatsApp automations (prompt 15).

Pure functions over a `db` session (the utils/booking_cleanup.py + utils/fraud_detection.py
pattern). `run_whatsapp_jobs(db)` runs the full sweep; each job is idempotent via a
`whatsapp_messages.client_ref` so re-running (every N minutes via the in-process APScheduler,
or a manual admin trigger, or `python -m scripts.whatsapp_jobs`) never double-sends.

Automations:
  * send_checkout_reminders  -- ⭐ ~2h before the real checkout moment (extend offer)
  * send_overstay_alerts     -- checkout passed + grace, still in-house -> guest AND owner
  * send_review_requests     -- post-checkout Google review + "had a problem?", SUPPRESSED
                                when the guest has an open complaint
  * send_daily_digest_if_due -- once/day at the configured IST hour -> owner
  * notify_new_fraud_alerts  -- after reconciliation, new high-severity alerts -> owner
  * vendor_renewals          -- AMC/contract renewal window -> owner (prompt 18 slice 13)
"""
import logging
from datetime import datetime, timedelta

from models import Booking, Guest, BookingItem, Room, FraudAlert
from utils import settings as app_settings
from utils import whatsapp_service as wa
from services import notify as notify_service
from routers.reception import booking_checkout_moment, _grace_minutes

logger = logging.getLogger(__name__)

# India is UTC+5:30 (single property, no DST) — matches fraud_detection's IST handling.
IST_OFFSET = timedelta(hours=5, minutes=30)


def _now_ist() -> datetime:
    return datetime.utcnow() + IST_OFFSET


def _room_label(db, booking) -> str:
    """Comma-joined assigned room numbers for the booking (empty before check-in)."""
    rows = (db.query(Room.room_number)
            .join(BookingItem, BookingItem.room_id == Room.room_id)
            .filter(BookingItem.booking_id == booking.booking_id).all())
    nums = [r[0] for r in rows if r[0]]
    return ", ".join(nums) if nums else (booking.booking_id and f"#{booking.booking_id}" or "")


def _guest(db, booking):
    return db.query(Guest).filter(Guest.guest_id == booking.guest_id).first() if booking.guest_id else None


def send_checkout_reminders(db) -> int:
    """⭐ Remind in-house guests ~lead_hours before their real checkout moment."""
    cfg = app_settings.get_whatsapp_config(db)
    if not cfg["checkout_reminder_enabled"]:
        return 0
    lead = timedelta(hours=cfg["checkout_reminder_lead_hours"])
    now = datetime.now()
    sent = 0
    bookings = db.query(Booking).filter(Booking.status == "checked_in").all()
    for b in bookings:
        moment = booking_checkout_moment(b)
        # Fire once, the first sweep where checkout falls inside the lead window (and not past).
        if now <= moment <= now + lead:
            if wa.already_sent(db, f"checkout_reminder:{b.booking_id}"):
                continue
            g = _guest(db, b)
            if not g or not (g.phone or g.email):
                continue
            notify_service.notify_guest(
                db, g, template="checkout_reminder",
                params={"guest_name": g.name,
                        "checkout_time": moment.strftime("%d-%m-%Y %H:%M"),
                        "room_label": _room_label(db, b)},
                booking_id=b.booking_id,
                client_ref=f"checkout_reminder:{b.booking_id}")
            sent += 1
    return sent


def send_overstay_alerts(db) -> int:
    """Alert guest AND owner when checkout (plus grace) has passed but the guest is still in-house."""
    cfg = app_settings.get_whatsapp_config(db)
    if not cfg["overstay_enabled"]:
        return 0
    grace = timedelta(minutes=_grace_minutes())
    now = datetime.now()
    sent = 0
    bookings = db.query(Booking).filter(Booking.status == "checked_in").all()
    for b in bookings:
        moment = booking_checkout_moment(b)
        if now > moment + grace:
            g = _guest(db, b)
            room_label = _room_label(db, b)
            if g and (g.phone or g.email) and not wa.already_sent(db, f"overstay:{b.booking_id}"):
                notify_service.notify(
                    db, template="overstay",
                    params={"guest_name": g.name, "room_label": room_label},
                    to_phone=g.phone, to_email=g.email, to_name=g.name,
                    guest_id=g.guest_id, booking_id=b.booking_id,
                    client_ref=f"overstay:{b.booking_id}", respect_optout=False)
                sent += 1
            if cfg["owner_alerts_enabled"] and not wa.already_sent(db, f"overstay_owner:{b.booking_id}"):
                notify_service.notify_owner(
                    db, template="overstay",
                    params={"guest_name": g.name if g else "Guest", "room_label": room_label},
                    client_ref=f"overstay_owner:{b.booking_id}")
                sent += 1
    return sent


def send_review_requests(db) -> int:
    """Post-checkout review + feedback. Suppressed when the guest has an open complaint —
    never ask an unhappy guest for a public review."""
    from routers.crm import guest_has_open_complaint  # local import avoids import-order coupling
    cfg = app_settings.get_whatsapp_config(db)
    if not cfg["review_enabled"]:
        return 0
    if not (cfg["google_review_url"] or "").strip():
        # The template embeds the URL inline ("we'd love a Google review: {review_url}"), so an
        # unset one has no sane placeholder -- it used to be sent as "" and Meta rejected the whole
        # message with (#131008). Hold the requests until the owner configures the URL.
        logger.info("review requests skipped: google_review_url not configured")
        return 0
    delay = timedelta(hours=cfg["review_delay_hours"])
    now = datetime.utcnow()
    # Window: checked out at least `delay` ago, but not older than delay + 2 days (don't backfill
    # ancient stays when the feature is first switched on). Idempotency guards double-send anyway.
    lower = now - delay - timedelta(days=2)
    upper = now - delay
    sent = 0
    bookings = (db.query(Booking)
                .filter(Booking.status == "checked_out",
                        Booking.checked_out_at.isnot(None),
                        Booking.checked_out_at >= lower,
                        Booking.checked_out_at <= upper).all())
    for b in bookings:
        if wa.already_sent(db, f"review:{b.booking_id}"):
            continue
        g = _guest(db, b)
        if not g or not g.phone:
            continue
        if guest_has_open_complaint(db, g.guest_id):
            continue  # suppress until resolved
        notify_service.notify_guest(
            db, g, template="review_feedback",
            params={"guest_name": g.name, "review_url": cfg["google_review_url"]},
            booking_id=b.booking_id, client_ref=f"review:{b.booking_id}")
        sent += 1
    return sent


def _deliver_report(db, *, cref, report, pdf, caption, doc_template, doc_params, subject, filename):
    """Shared delivery for the daily/weekly owner report: WhatsApp PDF (document-header template) +
    email attachment, idempotent per `cref`. Best-effort — never raises."""
    if wa.already_sent(db, cref):
        return 0
    num = wa.owner_number(db)
    if num and pdf:
        wa.send_document(db, num, pdf, filename, caption,
                         template=doc_template, params=doc_params, client_ref=cref)
    # Email the same PDF (free + reliable even before the WhatsApp doc template is approved).
    try:
        from services import notify as notify_service
        from utils.email_service import send_owner_report_email
        oe = notify_service.owner_email(db)
        if oe and pdf:
            send_owner_report_email(oe, subject, f"<pre>{caption}</pre>", pdf, filename)
    except Exception as e:
        logger.warning(f"owner report email failed: {e}")
    # Guarantee idempotency even when the owner has no WhatsApp number (email-only path).
    if not wa.already_sent(db, cref):
        wa._log_row(db, "out", num or "owner", doc_template, doc_params, caption,
                    status="sent", provider="job", client_ref=cref, commit=True)
    return 1


def send_daily_digest_if_due(db) -> int:
    """Build the rich previous-day report → PDF, and send it to the owner (WhatsApp + email) once,
    at/after the configured IST hour."""
    cfg = app_settings.get_whatsapp_config(db)
    if not cfg["owner_alerts_enabled"]:
        return 0
    now_ist = _now_ist()
    if now_ist.hour < cfg["daily_digest_hour"]:
        return 0
    day = now_ist.date() - timedelta(days=1)     # the previous day
    cref = f"day_report:{day}"
    if wa.already_sent(db, cref):
        return 0
    from services import day_report as dr
    report = dr.compute_day_report(db, day)
    caption = dr.summary_text(report)
    try:
        pdf = dr.render_day_report_pdf(report)
    except Exception as e:
        logger.error(f"daily report PDF failed: {e}")
        pdf = None
    return _deliver_report(db, cref=cref, report=report, pdf=pdf, caption=caption,
                           doc_template="daily_report_doc", doc_params={"day": str(day)},
                           subject=f"Hotel Bhimas — daily report {day}",
                           filename=f"HotelBhimas_daily_{day}.pdf")


def send_weekly_digest_if_due(db) -> int:
    """Once a week (configured weekday, at/after the digest hour) send a 7-day report ending
    yesterday. Idempotent per ISO week."""
    cfg = app_settings.get_whatsapp_config(db)
    if not cfg["owner_alerts_enabled"] or not cfg.get("weekly_digest_enabled", True):
        return 0
    now_ist = _now_ist()
    if now_ist.weekday() != cfg.get("weekly_digest_weekday", 0) or now_ist.hour < cfg["daily_digest_hour"]:
        return 0
    week_end = now_ist.date() - timedelta(days=1)
    iso = week_end.isocalendar()
    cref = f"week_report:{iso[0]}-W{iso[1]:02d}"
    if wa.already_sent(db, cref):
        return 0
    from services import day_report as dr
    report = dr.compute_week_report(db, week_end)
    caption = (f"Hotel Bhimas weekly ({report['from']} → {report['to']}): "
               f"avg occ {report['totals']['avg_occupancy_pct']}%, "
               f"collected ₹{report['totals']['collected']:,.0f}. Full report attached.")
    try:
        pdf = dr.render_week_report_pdf(report)
    except Exception as e:
        logger.error(f"weekly report PDF failed: {e}")
        pdf = None
    return _deliver_report(db, cref=cref, report=report, pdf=pdf, caption=caption,
                           doc_template="weekly_report_doc", doc_params={"week": f"W{iso[1]:02d} {iso[0]}"},
                           subject=f"Hotel Bhimas — weekly report {report['from']} to {report['to']}",
                           filename=f"HotelBhimas_weekly_{report['to']}.pdf")


def notify_new_fraud_alerts(db) -> int:
    """Run the reconciliation sweep, then alert the owner about any open high-severity
    alert — WhatsApp, falling back to their email (FE-11)."""
    from utils.fraud_detection import run_reconciliation
    from services import notify as notify_service
    cfg = app_settings.get_whatsapp_config(db)
    if not cfg["owner_alerts_enabled"]:
        return 0
    try:
        run_reconciliation(db)
    except Exception as e:
        logger.warning(f"whatsapp_jobs: reconciliation failed: {e}")
    # An owner with neither a WhatsApp number nor an email can't be reached at all.
    if not wa.owner_number(db) and not notify_service.owner_email(db):
        return 0
    alerts = (db.query(FraudAlert)
              .filter(FraudAlert.status == "open", FraudAlert.severity == "high").all())
    sent = 0
    for a in alerts:
        if wa.already_sent(db, f"fraud_alert:{a.id}"):
            continue
        result = wa.send_fraud_alert(db, a)  # idempotent per alert via client_ref
        if (result or {}).get("ok"):
            sent += 1
    return sent


def sweep_vendor_renewals(db) -> int:
    """Vendor/AMC renewal reminders (prompt 18 slice 13) ride this scheduler rather than
    starting another one. Returns the number of contracts chased."""
    from utils.vendor_jobs import run_vendor_renewal_sweep
    result = run_vendor_renewal_sweep(db)
    sent = result.get("reminders_sent", 0)
    return sent if isinstance(sent, int) else 0


def sweep_low_stock(db) -> int:
    """Low-stock alerts (prompt 18c slice 8) ride this scheduler. Returns items alerted."""
    from utils.stock_jobs import run_low_stock_sweep
    result = run_low_stock_sweep(db)
    n = result.get("low_stock_alerts", 0)
    return n if isinstance(n, int) else 0


def sweep_complaint_sla(db) -> int:
    """Guest-complaint SLA escalation (prompt 18c slice 11) rides this scheduler. Returns the
    number of complaints escalated."""
    from utils.complaint_jobs import run_complaint_sla_sweep
    result = run_complaint_sla_sweep(db)
    n = result.get("escalated", 0)
    return n if isinstance(n, int) else 0


_JOBS = [
    ("checkout_reminders", send_checkout_reminders),
    ("overstay_alerts", send_overstay_alerts),
    ("review_requests", send_review_requests),
    ("daily_digest", send_daily_digest_if_due),
    ("weekly_digest", send_weekly_digest_if_due),
    ("fraud_alerts", notify_new_fraud_alerts),
    ("vendor_renewals", sweep_vendor_renewals),
    ("low_stock_alerts", sweep_low_stock),
    ("complaint_sla", sweep_complaint_sla),
]


def run_whatsapp_jobs(db) -> dict:
    """Run every automation once. Each job is isolated so one failing never blocks the rest."""
    result = {}
    for name, fn in _JOBS:
        try:
            result[name] = fn(db)
        except Exception as e:
            logger.error(f"whatsapp_jobs: {name} failed: {e}")
            result[name] = f"error: {e}"
    logger.info(f"whatsapp_jobs sweep: {result}")
    return result
