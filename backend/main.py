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

# Load environment variables
load_dotenv()

# Setup logging
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)
@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        Base.metadata.create_all(bind=engine)
        logger.info("✅ Database connected and tables created")
    except Exception as e:
        logger.error(f"❌ Database connection failed: {e}")
    
    yield  # app runs here

    # Optional shutdown logic
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

# -------------------------
# Health Check
# -------------------------
@app.get("/")
def root():
    return {"status": "Hotel Bhimas API running"}
