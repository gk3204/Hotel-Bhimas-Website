"""Anti-fraud reconciliation sweep entrypoint (prompt 11).

Run periodically to raise fraud_alerts from already-populated data:
    python -m scripts.fraud_jobs
No in-process scheduler exists yet — wire this to an external cron / Fly scheduled machine
(same pattern as scripts/expire_booking_jobs.py). The admin dashboard also runs the sweep
inline (POST /fraud/reconcile, or on load), so this is optional automation.
"""
from database import SessionLocal
from utils.fraud_detection import run_reconciliation


def main():
    db = SessionLocal()
    try:
        result = run_reconciliation(db)
        print(f"reconciliation: {result}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
