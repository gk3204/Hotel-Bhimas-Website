"""services/phonepe_client — credentials, the token cache, and webhook authentication."""
import hashlib

import pytest

from services import phonepe_client


@pytest.fixture(autouse=True)
def _clear_token_cache():
    """The token cache is module-level; a value left by one test would leak into the next."""
    phonepe_client._token_cache.update({"access_token": None, "expires_at": 0.0})
    yield
    phonepe_client._token_cache.update({"access_token": None, "expires_at": 0.0})


# ---- configuration -----------------------------------------------------------------------

def test_is_configured_requires_every_credential(monkeypatch):
    monkeypatch.setenv("PHONEPE_CLIENT_ID", "id")
    monkeypatch.setenv("PHONEPE_CLIENT_SECRET", "secret")
    monkeypatch.delenv("PHONEPE_CLIENT_VERSION", raising=False)
    assert phonepe_client.is_configured() is False

    monkeypatch.setenv("PHONEPE_CLIENT_VERSION", "1")
    assert phonepe_client.is_configured() is True


def test_sandbox_is_the_default_mode(monkeypatch):
    """A missing PHONEPE_ENV must never mean production — that would take real money."""
    monkeypatch.delenv("PHONEPE_ENV", raising=False)
    assert phonepe_client._mode() == "sandbox"
    monkeypatch.setenv("PHONEPE_ENV", "prod")
    assert phonepe_client._mode() == "prod"
    monkeypatch.setenv("PHONEPE_ENV", "anything-else")
    assert phonepe_client._mode() == "sandbox"


def test_provider_status_never_leaks_a_secret(monkeypatch):
    monkeypatch.setenv("PHONEPE_CLIENT_ID", "id")
    monkeypatch.setenv("PHONEPE_CLIENT_SECRET", "super-secret")
    monkeypatch.setenv("PHONEPE_CLIENT_VERSION", "1")
    status = phonepe_client.provider_status()
    assert status["configured"] is True
    assert "super-secret" not in str(status)


# ---- webhook authentication --------------------------------------------------------------

def test_webhook_accepts_the_configured_digest(monkeypatch):
    monkeypatch.setenv("PHONEPE_WEBHOOK_USERNAME", "u")
    monkeypatch.setenv("PHONEPE_WEBHOOK_PASSWORD", "p")
    digest = hashlib.sha256(b"u:p").hexdigest()
    assert phonepe_client.verify_webhook({"Authorization": digest}, b"{}") is True
    # PhonePe has been seen to prefix the scheme; both spellings are the same credential.
    assert phonepe_client.verify_webhook({"Authorization": f"SHA256 {digest}"}, b"{}") is True
    # Header names are case-insensitive over the wire.
    assert phonepe_client.verify_webhook({"authorization": digest.upper()}, b"{}") is True


def test_webhook_rejects_a_wrong_or_missing_digest(monkeypatch):
    monkeypatch.setenv("PHONEPE_WEBHOOK_USERNAME", "u")
    monkeypatch.setenv("PHONEPE_WEBHOOK_PASSWORD", "p")
    assert phonepe_client.verify_webhook({"Authorization": "deadbeef"}, b"{}") is False
    assert phonepe_client.verify_webhook({}, b"{}") is False


def test_webhook_rejects_everything_when_no_credentials_are_set(monkeypatch):
    """Unset must mean "reject", never "accept anything".

    If it meant the latter, a deployment that forgot the pair would accept a forged
    "payment received" from anyone who found the URL. Rejecting costs nothing: the desk's
    status poll confirms the payment a second later.
    """
    monkeypatch.delenv("PHONEPE_WEBHOOK_USERNAME", raising=False)
    monkeypatch.delenv("PHONEPE_WEBHOOK_PASSWORD", raising=False)
    assert phonepe_client.verify_webhook({"Authorization": "anything"}, b"{}") is False
    assert phonepe_client.verify_webhook({}, b"{}") is False


# ---- token cache -------------------------------------------------------------------------

def test_token_is_fetched_once_and_reused(monkeypatch):
    monkeypatch.setenv("PHONEPE_CLIENT_ID", "id")
    monkeypatch.setenv("PHONEPE_CLIENT_SECRET", "secret")
    monkeypatch.setenv("PHONEPE_CLIENT_VERSION", "1")

    calls = []

    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            import time
            return {"access_token": "tok-123", "expires_at": time.time() + 3600}

    def _post(url, **kwargs):
        calls.append(url)
        return _Resp()

    monkeypatch.setattr(phonepe_client.requests, "post", _post)
    assert phonepe_client._access_token() == "tok-123"
    assert phonepe_client._access_token() == "tok-123"
    assert len(calls) == 1, "the token must be cached — four uvicorn workers would otherwise hammer it"


def test_expired_token_is_refetched(monkeypatch):
    monkeypatch.setenv("PHONEPE_CLIENT_ID", "id")
    monkeypatch.setenv("PHONEPE_CLIENT_SECRET", "secret")
    monkeypatch.setenv("PHONEPE_CLIENT_VERSION", "1")
    phonepe_client._token_cache.update({"access_token": "stale", "expires_at": 0.0})

    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            import time
            return {"access_token": "fresh", "expires_at": time.time() + 3600}

    monkeypatch.setattr(phonepe_client.requests, "post", lambda url, **kw: _Resp())
    assert phonepe_client._access_token() == "fresh"


def test_rejected_credentials_raise_rather_than_returning_none(monkeypatch):
    monkeypatch.setenv("PHONEPE_CLIENT_ID", "id")
    monkeypatch.setenv("PHONEPE_CLIENT_SECRET", "wrong")
    monkeypatch.setenv("PHONEPE_CLIENT_VERSION", "1")

    class _Resp:
        status_code = 401
        text = "unauthorized"

    monkeypatch.setattr(phonepe_client.requests, "post", lambda url, **kw: _Resp())
    with pytest.raises(phonepe_client.PhonePeError):
        phonepe_client._access_token()


def test_unconfigured_client_raises_a_clear_error(monkeypatch):
    for key in ("PHONEPE_CLIENT_ID", "PHONEPE_CLIENT_SECRET", "PHONEPE_CLIENT_VERSION"):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(phonepe_client.PhonePeError, match="not configured"):
        phonepe_client._access_token()


# ---- status ------------------------------------------------------------------------------

def test_fetch_status_never_raises_when_phonepe_is_down(monkeypatch):
    """This backs the desk's poll loop; an exception there would surface as a 500 mid-checkout."""
    monkeypatch.setenv("PHONEPE_CLIENT_ID", "id")
    monkeypatch.setenv("PHONEPE_CLIENT_SECRET", "secret")
    monkeypatch.setenv("PHONEPE_CLIENT_VERSION", "1")
    phonepe_client._token_cache.update({"access_token": "tok", "expires_at": 9e18})

    def _boom(*a, **kw):
        raise phonepe_client.requests.RequestException("connection reset")

    monkeypatch.setattr(phonepe_client.requests, "get", _boom)
    assert phonepe_client.fetch_status("BHS-1-abc") is None


def test_fetch_status_maps_the_completed_response(monkeypatch):
    monkeypatch.setenv("PHONEPE_CLIENT_ID", "id")
    monkeypatch.setenv("PHONEPE_CLIENT_SECRET", "secret")
    monkeypatch.setenv("PHONEPE_CLIENT_VERSION", "1")
    phonepe_client._token_cache.update({"access_token": "tok", "expires_at": 9e18})

    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return {"state": "completed",
                    "paymentDetails": [{"transactionId": "T123", "paymentMode": "UPI_QR"}]}

    monkeypatch.setattr(phonepe_client.requests, "get", lambda url, **kw: _Resp())
    st = phonepe_client.fetch_status("BHS-1-abc")
    assert st == {"state": "COMPLETED", "transaction_id": "T123", "payment_mode": "UPI_QR"}


def test_create_upi_qr_raises_when_no_intent_comes_back(monkeypatch):
    """A 200 with no intent is worse than an error: the desk would show an empty QR window."""
    monkeypatch.setenv("PHONEPE_CLIENT_ID", "id")
    monkeypatch.setenv("PHONEPE_CLIENT_SECRET", "secret")
    monkeypatch.setenv("PHONEPE_CLIENT_VERSION", "1")
    phonepe_client._token_cache.update({"access_token": "tok", "expires_at": 9e18})

    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return {"orderId": "OMO123"}

    monkeypatch.setattr(phonepe_client.requests, "post", lambda url, **kw: _Resp())
    with pytest.raises(phonepe_client.PhonePeError, match="no UPI intent"):
        phonepe_client.create_upi_qr("BHS-1-abc", 150000, 900)
