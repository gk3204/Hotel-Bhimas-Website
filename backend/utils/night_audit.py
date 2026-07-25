"""Automated night audit / day-close (prompt 16).

An end-of-day routine (scheduled in main.py, or triggered manually via POST /reports/day-close/run,
or the standalone `python -m scripts.night_audit_jobs`) that:
  1. ensure_folios_posted -> safety net: any in-house booking without a folio gets one opened
     (folio.open_folio posts the whole stay's room charges; idempotent — normally folios already
     exist from check-in).
  2. compute_day_close     -> UPSERT a DayCloseSummary snapshot (occupancy, sales, tax, cash) for
     the business date. Re-running the same date overwrites its row (idempotent).
  3. roll_business_date    -> advance the `business_date` app_setting to the closed date.

The business date closed is the day just ended: (current business_date) or (yesterday) — see
_target_date. All figures reuse the routers.reports query helpers so the snapshot matches the live
reports exactly. Pure functions over `db`, each wrapped so one failure doesn't abort the rest;
returns a summary dict.
"""
import logging
from datetime import date, datetime, timedelta

from models import Booking, CashShift, DayCloseSummary, Folio
from sqlalchemy import func
from utils.settings import (get_reports_config, set_setting, BUSINESS_DATE_KEY)

logger = logging.getLogger(__name__)


def _target_date(db, business_date=None):
    """The date to close. Explicit arg wins; else the stored business_date; else yesterday
    (the most recently completed day). Falls back to today only if yesterday would be negative."""
    if business_date is not None:
        return business_date
    cfg = get_reports_config(db)
    if cfg["business_date"]:
        try:
            return date.fromisoformat(cfg["business_date"])
        except ValueError:
            pass
    return date.today() - timedelta(days=1)


def ensure_folios_posted(db):
    """Open a folio (posting room charges) for every in-house booking that lacks one. Safety net —
    check-in already opens folios, so this normally does nothing. Returns the count opened."""
    from routers.folio import open_folio, get_db  # noqa: F401 (open_folio is the endpoint fn)
    from schemas import FolioOpenRequest

    opened = 0
    checked_in = (db.query(Booking).filter(Booking.status == "checked_in").all())
    for b in checked_in:
        has_folio = db.query(Folio).filter(Folio.booking_id == b.booking_id).first()
        if has_folio:
            continue
        try:
            # Reuse the folio endpoint's logic directly (it commits + is idempotent). A system
            # actor (user=None) is fine — write_audit resolves to NULL user for scheduled runs.
            open_folio(FolioOpenRequest(booking_id=b.booking_id), db=db, user={"role": "admin"})
            opened += 1
        except Exception as e:
            logger.warning(f"night-audit: could not open folio for booking {b.booking_id}: {e}")
            db.rollback()
    return opened


def compute_day_close(db, business_date, folios_posted=0, generated_by="scheduler"):
    """Build + UPSERT the DayCloseSummary snapshot for `business_date`. Reuses the reports query
    helpers so the numbers match the live reports. Returns the serialized snapshot dict."""
    from routers.reports import (occupancy_data, sales_by_date, collections_summary,
                                 arrivals_departures_data, _serialize_day_close)
    from utils.fraud_detection import compute_daily_digest

    occ = occupancy_data(db, business_date, business_date, "day")
    occ_row = occ["rows"][0] if occ["rows"] else {"occupied": 0, "occupancy_pct": 0.0}
    sales = sales_by_date(db, business_date, business_date)
    st = sales["totals"]
    coll = collections_summary(db, business_date, business_date)
    ad = arrivals_departures_data(db, business_date, business_date)
    adt = ad["totals"]

    # Cash expected/variance = sum across shifts CLOSED on the business date.
    shifts = (db.query(CashShift).filter(CashShift.status == "closed",
                                         func.date(CashShift.closed_at) == business_date).all())
    cash_expected = round(sum(float(s.expected_cash or 0) for s in shifts), 2)
    cash_variance = round(sum(float(s.variance or 0) for s in shifts), 2)

    digest = compute_daily_digest(db, business_date)

    row = db.query(DayCloseSummary).filter(
        DayCloseSummary.business_date == business_date).first()
    if row is None:
        row = DayCloseSummary(business_date=business_date)
        db.add(row)
    row.rooms_total = occ["total_rooms"]
    row.rooms_occupied = occ_row["occupied"]
    row.occupancy_pct = occ_row["occupancy_pct"]
    row.room_revenue = st["room_revenue"]
    row.other_revenue = st["other_revenue"]
    row.total_sales = st["gross_sales"]
    row.taxable_total = st["taxable"]
    row.cgst_total = st["cgst"]
    row.sgst_total = st["sgst"]
    row.cash_collected = coll["cash"]
    row.cash_expected = cash_expected
    row.cash_variance = cash_variance
    row.arrivals = adt["arrivals"]
    row.departures = adt["departures"]
    row.open_alerts = digest["open_alerts"]
    row.folios_posted = folios_posted
    row.generated_by = generated_by
    row.generated_at = datetime.utcnow()
    db.commit()
    db.refresh(row)
    return _serialize_day_close(row)


def roll_business_date(db, business_date, user=None):
    """Advance the stored business date to the day just closed (so the next audit closes the
    following day)."""
    set_setting(db, BUSINESS_DATE_KEY, business_date.isoformat(), user, commit=True)


def run_night_audit(db, business_date=None, user=None, generated_by="scheduler"):
    """Full night-audit sweep for one business date. Idempotent — safe to re-run. Returns a
    summary dict {business_date, folios_posted, snapshot}."""
    target = _target_date(db, business_date)
    logger.info(f"🌙 Night audit / day-close for {target} (by {generated_by})")

    try:
        folios_posted = ensure_folios_posted(db)
    except Exception as e:
        logger.error(f"night-audit: ensure_folios_posted failed: {e}")
        db.rollback()
        folios_posted = 0

    snapshot = compute_day_close(db, target, folios_posted=folios_posted, generated_by=generated_by)

    try:
        roll_business_date(db, target, user)
    except Exception as e:
        logger.warning(f"night-audit: roll_business_date failed: {e}")
        db.rollback()

    logger.info(f"✅ Day-close {target}: sales ₹{snapshot['total_sales']} "
                f"occ {snapshot['occupancy_pct']}% cash ₹{snapshot['cash_collected']} "
                f"(folios opened: {folios_posted})")
    return {"business_date": str(target), "folios_posted": folios_posted, "snapshot": snapshot}
