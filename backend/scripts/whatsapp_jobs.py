"""WhatsApp automation sweep entrypoint (prompt 15).

Runs the checkout-reminder / overstay / review / daily-digest / fraud-alert automations once:
    python -m scripts.whatsapp_jobs

The app ALSO runs this in-process on a schedule (APScheduler in main.py lifespan, every
`wa_job_interval_minutes`), and the admin can trigger it inline via POST /whatsapp/jobs/run.
This standalone entrypoint mirrors scripts/fraud_jobs.py for an external cron / Fly scheduled
machine (belt-and-suspenders if the web process isn't running the scheduler). Every send is
idempotent, so overlapping runs are safe.
"""
from database import SessionLocal
from utils.whatsapp_jobs import run_whatsapp_jobs


def main():
    db = SessionLocal()
    try:
        result = run_whatsapp_jobs(db)
        print(f"whatsapp_jobs: {result}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
