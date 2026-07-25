"""Vendor / AMC renewal reminders (prompt 18, slice 13).

Pure functions over a `db` session, the same shape as `utils/whatsapp_jobs.py` and
`utils/review_jobs.py`. The sweep is wired into the EXISTING WhatsApp scheduler
(`run_whatsapp_jobs`) rather than starting another APScheduler — one cadence, one place to
disable, one log line.

Idempotency: `vendor_contracts.last_reminder_sent_at` is stamped when an alert goes out, so a
contract is chased once per renewal window, not once per sweep. `POST /vendors/contracts/{id}/renew`
clears it, which re-arms the reminder for the new window.

Delivery rides `utils/whatsapp_service` — pluggable-off by default, so this is fully verifiable
before the owner's WhatsApp number is live.
"""
import logging
from datetime import date, datetime

from models import Vendor, VendorContract
from utils import settings as app_settings
from utils import whatsapp_service as wa

logger = logging.getLogger(__name__)


def mark_expired_contracts(db) -> int:
    """Flip active contracts whose end date has passed to 'expired'.

    Housekeeping, not an alert: it keeps `GET /vendors/renewals` and the vendor counts honest
    even if nobody acted on the reminder."""
    today = date.today()
    rows = db.query(VendorContract).filter(
        VendorContract.status == "active",
        VendorContract.end_date.isnot(None),
        VendorContract.end_date < today,
    ).all()
    for c in rows:
        c.status = "expired"
    if rows:
        db.commit()
    return len(rows)


def send_renewal_reminders(db) -> int:
    """WhatsApp the owner about every active contract entering its renewal window.

    One message per contract per window (see `last_reminder_sent_at`). Contracts with no end
    date are open-ended and never chased."""
    from routers.vendors import days_to_renewal, is_due_for_renewal

    cfg = app_settings.get_backoffice_config(db)
    if not cfg["vendor_renewal_alerts_enabled"]:
        return 0

    owner = wa.owner_number(db)
    if not owner:
        return 0

    today = date.today()
    contracts = db.query(VendorContract).filter(
        VendorContract.status == "active",
        VendorContract.end_date.isnot(None),
    ).all()

    sent = 0
    for c in contracts:
        if not is_due_for_renewal(c, today, cfg["vendor_renewal_lead_days"]):
            continue
        if c.last_reminder_sent_at is not None:
            continue          # already chased for this window

        vendor = db.query(Vendor).filter(Vendor.id == c.vendor_id).first()
        left = days_to_renewal(c, today)
        row = wa.send_template(
            db, owner, "vendor_renewal",
            {
                "vendor_name": vendor.name if vendor else "Vendor",
                "contract_title": c.title,
                "end_date": f"{c.end_date:%d-%m-%Y}",
                "days_left": str(max(0, left if left is not None else 0)),
            },
            client_ref=f"vendor_renewal:{c.id}:{c.end_date}",
            respect_optout=False,          # operational alert to the owner, not marketing
        )
        if row is not None:
            c.last_reminder_sent_at = datetime.utcnow()
            sent += 1

    if sent:
        db.commit()
    return sent


def run_vendor_renewal_sweep(db) -> dict:
    """Full vendor sweep: expire what has lapsed, then chase what is about to.

    Each step is isolated so one failing never blocks the other — matching run_whatsapp_jobs."""
    result = {}
    for name, fn in (("expired", mark_expired_contracts),
                     ("reminders_sent", send_renewal_reminders)):
        try:
            result[name] = fn(db)
        except Exception as e:
            logger.error(f"vendor_jobs: {name} failed: {e}")
            result[name] = f"error: {e}"
    logger.info(f"vendor_jobs sweep: {result}")
    return result
