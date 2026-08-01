from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from database import Base, engine
from dotenv import load_dotenv
import os
import logging
from contextlib import asynccontextmanager
from routers import room_types, admin, users, adminsecurity, payments
from routers.bookings import router as booking_router
from routers.room_type_availability import router as availability_router
from routers.enquiry import router as enquiry_router
from routers.promotions import router as promotions_router
# --- PMS foundation routers (Milestone 0, prompt 01) ---
from routers import reception, folio, cards, cash_shift, housekeeping, fraud
# --- PMS Milestone 1 routers ---
from routers import rooms
# --- PMS Milestone 3 routers (prompt 13: maintenance tickets) ---
from routers import maintenance
# --- PMS Milestone 5 routers (prompt 10: rates & travel-agent pricing) ---
from routers import agents
# --- PMS Milestone 4 routers (prompt 14: customer / guest CRM) ---
from routers import crm
# --- PMS Milestone 2/4 routers (prompt 15: WhatsApp & automated alerts) ---
from routers import whatsapp
# --- PMS Milestone 5 routers (prompt 16: reports & dashboard) ---
from routers import reports
# --- PMS Milestone 6 routers (prompt 17: OTA tracking) ---
from routers import ota
# --- PMS Milestone 7 routers (prompt 18: India compliance / Tally / 2FA) ---
from routers import compliance, twofa
from routers import reviews
# --- PMS round-2 back office (prompt 18 slices 7/13/12/9: corporate billing, vendors, staff) ---
from routers import companies, vendors, roster
# --- PMS round-2 batch B (prompt 18c slices 8/11: inventory, guest complaints) ---
from routers import stock, complaints
# --- PMS round-2 batch C part 1 (prompt 18d slice 10: guest extras / in-room QR portal) ---
from routers import portal
# --- Admin audit-log viewer (read-only surface over the append-only audit trail) ---
from routers import audit

# --- Editable operational settings: category lists + reg-slip rules (F-A backbone) ---
from routers import opsettings

# --- Linen / laundry tracking (FE-9) ---
from routers import linen

# Load environment variables
load_dotenv()

# Setup logging
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)
def _start_whatsapp_scheduler():
    """In-process APScheduler for WhatsApp automations (prompt 15). Fires the sweep every
    `wa_job_interval_minutes`. Disabled with WHATSAPP_SCHEDULER_ENABLED=false. Each send is
    idempotent, so a missed/overlapping tick is harmless. Returns the scheduler (or None)."""
    if os.getenv("WHATSAPP_SCHEDULER_ENABLED", "true").strip().lower() in ("false", "0", "no"):
        logger.info("WhatsApp scheduler disabled (WHATSAPP_SCHEDULER_ENABLED=false)")
        return None
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from database import SessionLocal
        from utils.settings import get_whatsapp_config
        from utils.whatsapp_jobs import run_whatsapp_jobs

        db = SessionLocal()
        try:
            interval = get_whatsapp_config(db)["job_interval_minutes"]
        finally:
            db.close()

        def _tick():
            _db = SessionLocal()
            try:
                run_whatsapp_jobs(_db)
            except Exception as e:
                logger.error(f"WhatsApp scheduled sweep failed: {e}")
            finally:
                _db.close()

        sched = BackgroundScheduler(daemon=True, timezone="UTC")
        sched.add_job(_tick, "interval", minutes=interval, id="whatsapp_sweep",
                      max_instances=1, coalesce=True)
        sched.start()
        logger.info(f"✅ WhatsApp scheduler started (every {interval} min)")
        return sched
    except Exception as e:
        logger.error(f"❌ WhatsApp scheduler failed to start: {e}")
        return None


def _start_night_audit_scheduler():
    """In-process APScheduler for the automated night-audit / day-close (prompt 16). Fires once a
    day at `night_audit_hour` (IST) — posts pending room charges, snapshots the day-close, and rolls
    the business date. Disabled with NIGHT_AUDIT_SCHEDULER_ENABLED=false or the `night_audit_enabled`
    app_setting. Idempotent, so a missed/duplicate run is harmless. Returns the scheduler (or None)."""
    if os.getenv("NIGHT_AUDIT_SCHEDULER_ENABLED", "true").strip().lower() in ("false", "0", "no"):
        logger.info("Night-audit scheduler disabled (NIGHT_AUDIT_SCHEDULER_ENABLED=false)")
        return None
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from database import SessionLocal
        from utils.settings import get_reports_config
        from utils.night_audit import run_night_audit

        db = SessionLocal()
        try:
            cfg = get_reports_config(db)
        finally:
            db.close()
        if not cfg["night_audit_enabled"]:
            logger.info("Night-audit scheduler disabled (app_setting night_audit_enabled=false)")
            return None
        hour = cfg["night_audit_hour"]

        def _tick():
            _db = SessionLocal()
            try:
                run_night_audit(_db, generated_by="scheduler")
            except Exception as e:
                logger.error(f"Night-audit scheduled run failed: {e}")
            finally:
                _db.close()

        # Cron in India time (single property, no DST) so "3 AM" means 3 AM local.
        sched = BackgroundScheduler(daemon=True, timezone="Asia/Kolkata")
        sched.add_job(_tick, "cron", hour=hour, minute=0, id="night_audit",
                      max_instances=1, coalesce=True)
        sched.start()
        logger.info(f"✅ Night-audit scheduler started (daily at {hour:02d}:00 IST)")
        return sched
    except Exception as e:
        logger.error(f"❌ Night-audit scheduler failed to start: {e}")
        return None


def _start_ota_scheduler():
    """In-process APScheduler for the OTA email auto-draft poller (prompt 17). Polls the configured
    IMAP mailbox every `OTA_IMAP_POLL_INTERVAL_MINUTES` and upserts drafts for reception to confirm.
    NO-OP unless OTA_IMAP_* env is set (poll_mailbox short-circuits) — the poller is off by default,
    matching the WhatsApp/R2/OCR pluggable idiom. Disabled with OTA_IMAP_SCHEDULER_ENABLED=false.
    Returns the scheduler (or None)."""
    if os.getenv("OTA_IMAP_SCHEDULER_ENABLED", "true").strip().lower() in ("false", "0", "no"):
        logger.info("OTA IMAP scheduler disabled (OTA_IMAP_SCHEDULER_ENABLED=false)")
        return None
    try:
        from services import ota_service
        if not ota_service.is_imap_configured():
            logger.info("OTA IMAP scheduler idle (OTA_IMAP_* env not set — poller off)")
            return None
        from apscheduler.schedulers.background import BackgroundScheduler
        from database import SessionLocal

        try:
            interval = max(1, int(os.getenv("OTA_IMAP_POLL_INTERVAL_MINUTES", "15") or 15))
        except (TypeError, ValueError):
            interval = 15

        def _tick():
            _db = SessionLocal()
            try:
                ota_service.poll_mailbox(_db)
            except Exception as e:
                logger.error(f"OTA IMAP scheduled poll failed: {e}")
            finally:
                _db.close()

        sched = BackgroundScheduler(daemon=True, timezone="UTC")
        sched.add_job(_tick, "interval", minutes=interval, id="ota_imap_poll",
                      max_instances=1, coalesce=True)
        sched.start()
        logger.info(f"✅ OTA IMAP scheduler started (every {interval} min)")
        return sched
    except Exception as e:
        logger.error(f"❌ OTA IMAP scheduler failed to start: {e}")
        return None


def _start_review_scheduler():
    """In-process APScheduler for the Google-review pipeline (prompt 20). Every
    `review_poll_interval_minutes` it runs poll → generate → post. It still ticks when GBP is not
    configured (poll is a no-op, but the generate/delay/approval/post-stub pipeline drives injected
    test reviews), matching the pluggable idiom. Disabled with REVIEW_SCHEDULER_ENABLED=false.
    Returns the scheduler (or None)."""
    if os.getenv("REVIEW_SCHEDULER_ENABLED", "true").strip().lower() in ("false", "0", "no"):
        logger.info("Review scheduler disabled (REVIEW_SCHEDULER_ENABLED=false)")
        return None
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from database import SessionLocal
        from utils import settings as app_settings
        from utils import review_jobs

        _db0 = SessionLocal()
        try:
            interval = app_settings.get_review_config(_db0)["poll_interval_minutes"]
        finally:
            _db0.close()

        def _tick():
            _db = SessionLocal()
            try:
                review_jobs.run_review_jobs(_db)
            except Exception as e:
                logger.error(f"Review scheduled sweep failed: {e}")
            finally:
                _db.close()

        sched = BackgroundScheduler(daemon=True, timezone="UTC")
        sched.add_job(_tick, "interval", minutes=max(1, interval), id="review_poll",
                      max_instances=1, coalesce=True)
        sched.start()
        logger.info(f"✅ Review scheduler started (every {interval} min)")
        return sched
    except Exception as e:
        logger.error(f"❌ Review scheduler failed to start: {e}")
        return None


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        Base.metadata.create_all(bind=engine)
        logger.info("✅ Database connected and tables created")
    except Exception as e:
        logger.error(f"❌ Database connection failed: {e}")

    scheduler = _start_whatsapp_scheduler()
    night_audit_scheduler = _start_night_audit_scheduler()
    ota_scheduler = _start_ota_scheduler()
    review_scheduler = _start_review_scheduler()

    yield  # app runs here

    # Optional shutdown logic
    for sched in (scheduler, night_audit_scheduler, ota_scheduler, review_scheduler):
        if sched is not None:
            try:
                sched.shutdown(wait=False)
            except Exception:
                pass
    logger.info("🔻 App shutting down")

app = FastAPI(lifespan=lifespan)

# -------------------------
# CORS Configuration (from environment)
# -------------------------
cors_origins = os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",")
cors_origins = [origin.strip() for origin in cors_origins]

logger.info(f"CORS enabled for origins: {cors_origins}")

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -------------------------
# Create Tables
# -------------------------
# Base.metadata.create_all(bind=engine)

# -------------------------
# Admin IP Restriction Middleware (Production-Safe)
# -------------------------
def get_client_ip(request: Request) -> str:
    """Get real client IP, handling proxies"""
    # Check X-Forwarded-For header (set by proxies like Nginx, CloudFlare)
    x_forwarded_for = request.headers.get("X-Forwarded-For")
    if x_forwarded_for:
        return x_forwarded_for.split(",")[0].strip()
    # Fallback to direct connection
    return request.client.host if request.client else "unknown"

def is_ip_in_network(ip: str, networks: list) -> bool:
    """Check if IP is in allowed CIDR networks"""
    from ipaddress import ip_address, ip_network
    try:
        client_ip = ip_address(ip)
        for network_str in networks:
            if "/" in network_str:
                network = ip_network(network_str, strict=False)
            else:
                network = ip_network(f"{network_str}/32", strict=False)
            if client_ip in network:
                return True
        return False
    except Exception as e:
        logger.warning(f"IP validation error: {e}")
        return False

@app.middleware("http")
async def restrict_admin_access(request: Request, call_next):
    """Restrict admin routes to allowed networks (configurable)"""
    if request.url.path.startswith("/admin"):
        # Check if admin restriction is enabled
        # Default: false (open in production, can enable with env var)
        restrict_admin = os.getenv("RESTRICT_ADMIN_ACCESS", "false").lower() == "true"
        
        if restrict_admin:
            allowed_networks = os.getenv("ALLOWED_ADMIN_NETWORKS", "192.168.0.0/16,127.0.0.1").split(",")
            allowed_networks = [net.strip() for net in allowed_networks]
            
            client_ip = get_client_ip(request)
            
            if not is_ip_in_network(client_ip, allowed_networks):
                logger.warning(f"Unauthorized admin access attempt from IP: {client_ip}")
                raise HTTPException(status_code=403, detail="Admin access restricted to allowed networks")

    response = await call_next(request)
    return response


# -------------------------
# Routers
# -------------------------
app.include_router(room_types.router)
app.include_router(booking_router)
app.include_router(availability_router)
app.include_router(enquiry_router)
app.include_router(admin.router)
app.include_router(users.router)
app.include_router(adminsecurity.router)
app.include_router(payments.router)
app.include_router(promotions_router)
# --- PMS foundation routers (Milestone 0, prompt 01) ---
app.include_router(reception.router)
app.include_router(folio.router)
app.include_router(cards.router)
app.include_router(cash_shift.router)
app.include_router(housekeeping.router)
app.include_router(fraud.router)
app.include_router(rooms.router)
app.include_router(agents.router)
app.include_router(maintenance.router)
app.include_router(crm.router)
app.include_router(whatsapp.router)
app.include_router(reports.router)
app.include_router(ota.router)
app.include_router(compliance.router)
app.include_router(twofa.router)
app.include_router(reviews.router)
app.include_router(companies.router)
app.include_router(companies.invoice_router)   # /company-invoices/* (addresses an invoice, not a company)
app.include_router(vendors.router)
app.include_router(roster.router)
app.include_router(stock.router)
app.include_router(complaints.router)
app.include_router(portal.router)
app.include_router(audit.router)
app.include_router(opsettings.router)
app.include_router(linen.router)

# -------------------------
# Health Check
# -------------------------
@app.get("/health")
def health():
    return {"status": "ok"}
