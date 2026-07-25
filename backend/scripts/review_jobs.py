"""Google-review pipeline sweep entrypoint (prompt 20).

Runs the poll → generate → post pipeline once:
    python -m scripts.review_jobs

The app ALSO runs this in-process on a schedule (APScheduler in main.py lifespan, every
`review_poll_interval_minutes`), and the admin can trigger it inline via POST /reviews/poll.
This standalone entrypoint mirrors scripts/whatsapp_jobs.py for an external cron / Fly scheduled
machine. Every stage is idempotent, so overlapping runs are safe.
"""
from database import SessionLocal
from utils.review_jobs import run_review_jobs


def main():
    db = SessionLocal()
    try:
        result = run_review_jobs(db)
        print(f"review_jobs: {result}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
