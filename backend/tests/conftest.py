"""Test fixtures for the PhonePe desk-gateway seam (v5q).

This is the backend's first test suite, so it is deliberately small and focused rather than an
attempt to retrofit coverage everywhere. What it protects is the part of PhonePe that cannot be
exercised by hand until the merchant account is approved — and that will then be exercised for
the first time with a real guest standing at the desk.

Everything runs against an in-memory SQLite database built from the SQLAlchemy models, and every
PhonePe HTTP call is monkeypatched. No network, no Postgres, no credentials.

    cd backend && python -m pytest tests -q
"""
import os
import sys

import pytest

# The app imports modules as top-level names (`from models import ...`), so backend/ has to be on
# the path — the same assumption uvicorn makes when it runs from that directory.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Must be set before `database` is imported, because it reads the env at module scope.
os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("SECRET_KEY", "test-secret-key-not-used-for-anything-real")


@pytest.fixture(autouse=True)
def no_outbound_gateway_calls(monkeypatch):
    """Guarantee the suite cannot reach a real payment gateway.

    Not a theoretical precaution: backend/.env carries working Razorpay test keys and is loaded
    on import, so the first run of these tests genuinely POSTed to Razorpay's API from the
    fallback path. A unit test must not spend anyone's API quota or depend on the network, and a
    suite that quietly calls out is one credential swap away from doing it against live keys.

    PhonePe is stubbed per-test where a response is needed; here it is simply unconfigured.
    """
    for key in ("RAZORPAY_KEY_ID", "RAZORPAY_KEY_SECRET", "RAZORPAY_WEBHOOK_SECRET",
                "PHONEPE_CLIENT_ID", "PHONEPE_CLIENT_SECRET", "PHONEPE_CLIENT_VERSION",
                "PHONEPE_WEBHOOK_USERNAME", "PHONEPE_WEBHOOK_PASSWORD", "DESK_PAY_GATEWAY"):
        monkeypatch.delenv(key, raising=False)


@pytest.fixture()
def db():
    """A fresh in-memory database per test, wired to the app's own metadata.

    StaticPool + a shared connection because every ":memory:" connection is otherwise its own
    empty database — the tables would vanish between the fixture and the request handler.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    import models

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    models.Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def client(db):
    """An HTTP client bound straight to the ASGI app, with get_db and auth overridden.

    Deliberately NOT fastapi.testclient.TestClient: this project pins fastapi 0.109 (starlette
    0.35), whose TestClient constructs httpx with `app=`, and httpx 0.28 removed that argument.
    Driving ASGITransport ourselves works on both sides of that split and needs no change to the
    machine's installed packages.

    Lifespan is not run, which is what we want — no create_all against SQLite, and none of the
    five schedulers starting up inside a unit test.

    Auth is overridden because these tests are about gateway plumbing, not about who may call it;
    a real JWT would add a user-table dance to every case.
    """
    import httpx
    import main
    from routers import payments
    from utils import auth_utils

    def _get_db():
        yield db

    fake_user = {"user_id": None, "username": "tester", "role": "admin"}
    # Each router declares its own get_db (routers/payments.py:44) rather than sharing one out of
    # database.py, so the override has to name that function specifically.
    main.app.dependency_overrides[payments.get_db] = _get_db
    main.app.dependency_overrides[auth_utils.require_reception_or_admin] = lambda: fake_user
    main.app.dependency_overrides[auth_utils.require_admin] = lambda: fake_user
    try:
        yield _SyncAsgiClient(main.app)
    finally:
        main.app.dependency_overrides.clear()


class _SyncAsgiClient:
    """Synchronous `.post()` / `.get()` / `.put()` over an ASGI app.

    Each call runs its own event loop, so the tests stay plain synchronous functions and the
    fixture session can be shared with them without crossing a thread boundary.
    """

    def __init__(self, app):
        self._app = app

    def _run(self, method, url, **kwargs):
        import asyncio
        import httpx

        async def _go():
            transport = httpx.ASGITransport(app=self._app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
                return await ac.request(method, url, **kwargs)

        return asyncio.run(_go())

    def get(self, url, **kw):
        return self._run("GET", url, **kw)

    def post(self, url, **kw):
        return self._run("POST", url, **kw)

    def put(self, url, **kw):
        return self._run("PUT", url, **kw)


@pytest.fixture()
def booking_with_folio(db):
    """A confirmed booking with an OPEN folio — the state a desk collection happens in."""
    from datetime import date, timedelta
    from models import Booking, Folio, Guest

    guest = Guest(name="Test Guest", email="t@example.invalid", phone="9000000000")
    db.add(guest)
    db.flush()
    booking = Booking(guest_id=guest.guest_id, check_in=date.today(),
                      check_out=date.today() + timedelta(days=1),
                      total_amount=1500, status="confirmed")
    db.add(booking)
    db.flush()
    folio = Folio(booking_id=booking.booking_id, status="open", balance=0)
    db.add(folio)
    db.commit()
    return booking


@pytest.fixture()
def webhook_auth(monkeypatch):
    """Configure webhook credentials and return the header a genuine PhonePe callback carries."""
    import hashlib
    monkeypatch.setenv("PHONEPE_WEBHOOK_USERNAME", "bhimas-hook")
    monkeypatch.setenv("PHONEPE_WEBHOOK_PASSWORD", "hook-password")
    digest = hashlib.sha256(b"bhimas-hook:hook-password").hexdigest()
    return {"Authorization": digest}
