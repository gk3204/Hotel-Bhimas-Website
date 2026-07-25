"""Night-audit / day-close entrypoint (prompt 16).

Run at end-of-day to post pending room charges, snapshot the day-close, and roll the business
date:
    python -m scripts.night_audit_jobs                # closes the stored/previous business date
    python -m scripts.night_audit_jobs 2026-07-19     # closes a specific date (idempotent)

main.py also runs this in-process via APScheduler (cron at night_audit_hour, IST) and the admin
can trigger it with POST /reports/day-close/run. Idempotent — safe to re-run for any date.
"""
import sys
from datetime import date

from database import SessionLocal
from utils.night_audit import run_night_audit


def main():
    business_date = None
    if len(sys.argv) > 1:
        try:
            business_date = date.fromisoformat(sys.argv[1])
        except ValueError:
            print(f"Invalid date '{sys.argv[1]}'. Use YYYY-MM-DD.")
            sys.exit(1)
    db = SessionLocal()
    try:
        result = run_night_audit(db, business_date=business_date, generated_by="cron")
        print(f"night-audit: {result}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
