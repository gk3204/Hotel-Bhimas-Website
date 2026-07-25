"""Guest-complaint SLA escalation (prompt 18c, slice 11).

Pure functions over a `db` session, the same shape as `utils/vendor_jobs.py`. The sweep rides the
EXISTING WhatsApp scheduler (`run_whatsapp_jobs`) rather than starting another APScheduler.

An open complaint whose response-by or resolve-by SLA has passed is escalated: the shared
`routers.complaints.escalate_ticket` bumps the level, appends the append-only `ticket_escalations`
trail, and best-effort WhatsApps the owner.

Idempotency / no spam: a complaint is escalated at most once per `escalation_reminder_hours`
window (tracked by `maintenance_tickets.last_escalated_at`), so a persistently-breached complaint
is chased periodically, not every tick. Delivery rides `utils/whatsapp_service` (pluggable-off).
"""
import logging
from datetime import datetime, timedelta

from models import MaintenanceTicket
from utils import settings as app_settings

logger = logging.getLogger(__name__)

# Minimum gap between two escalations of the same complaint (hours). Kept as a module constant —
# escalation itself is config-gated; this only paces the reminders.
ESCALATION_REMINDER_HOURS = 6


def escalate_breached_complaints(db) -> int:
    """Escalate every open guest complaint whose SLA has breached and that hasn't been escalated
    within the reminder window. Returns the number escalated this sweep."""
    from routers.complaints import OPEN_STATUSES, _breach, escalate_ticket

    cfg = app_settings.get_complaints_config(db)
    if not cfg["escalation_enabled"]:
        return 0

    now = datetime.utcnow()
    cutoff = now - timedelta(hours=ESCALATION_REMINDER_HOURS)
    open_tickets = db.query(MaintenanceTicket).filter(
        MaintenanceTicket.source == "guest",
        MaintenanceTicket.status.in_(OPEN_STATUSES),
    ).all()

    escalated = 0
    for t in open_tickets:
        b = _breach(t, now)
        if not b["any_breached"]:
            continue
        if t.last_escalated_at is not None and t.last_escalated_at > cutoff:
            continue          # already chased inside this window
        reason = ("resolve SLA breached" if b["resolve_breached"]
                  else "response SLA breached")
        # commit=False: batch the whole sweep into one commit at the end
        escalate_ticket(db, t, reason, user=None, notify=True, commit=False)
        escalated += 1

    if escalated:
        db.commit()
    return escalated


def run_complaint_sla_sweep(db) -> dict:
    """Full complaint SLA sweep. Isolated per step (matches run_whatsapp_jobs)."""
    result = {}
    for name, fn in (("escalated", escalate_breached_complaints),):
        try:
            result[name] = fn(db)
        except Exception as e:
            logger.error(f"complaint_jobs: {name} failed: {e}")
            result[name] = f"error: {e}"
    logger.info(f"complaint_jobs sweep: {result}")
    return result
