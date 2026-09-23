"""PhonePe Payment Gateway — the desk's zero-fee UPI QR (v5q).

WHY THIS EXISTS
    Razorpay charges ~2% + GST on every desk collection, UPI included, even though UPI P2M
    carries no MDR by law. At the desk the guest is standing there with a phone; the only thing
    the aggregator provides is the confirmation. PhonePe PG settles UPI at 0%, which makes the
    desk's UPI QR free while keeping the part that matters — a webhook that posts the folio
    credit without anyone typing a UTR.

SCOPE — deliberately narrow
    Desk **UPI QR only**. The website checkout, desk payment LINKS and /send-link stay on
    Razorpay: those need a hosted page and a different fetch path, and the fee there is a card
    fee nobody avoids. One gateway per concern, chosen by the `desk_pay_gateway` setting.

    Which means production runs both gateways at once, and `Payment.gateway` is what tells them
    apart on every read path — refunds especially (see `process_gateway_refund`).

EVERY WIRE DETAIL LIVES HERE
    Paths, field names, the auth header shape: all of it is in this file so that when PhonePe
    changes an API version, one module moves. `routers/payments.py` only ever sees the six
    functions below and `PhonePeError`.

ENV (all four of the first group required for live mode)
    PHONEPE_CLIENT_ID, PHONEPE_CLIENT_SECRET, PHONEPE_CLIENT_VERSION
    PHONEPE_ENV                 sandbox | prod          (default sandbox)
    PHONEPE_WEBHOOK_USERNAME, PHONEPE_WEBHOOK_PASSWORD  (set the same pair in the PhonePe
                                dashboard against the webhook URL; without them every callback
                                is rejected, and confirmation falls back to polling)

Status helpers never raise — the desk poll path must survive PhonePe being down. `create_upi_qr`
and `refund` DO raise `PhonePeError`, because the caller has a decision to make (fall back to a
Razorpay link / tell the receptionist) rather than a value to ignore.
"""
import hashlib
import hmac
import logging
import os
import threading
import time

import requests

logger = logging.getLogger(__name__)

_TIMEOUT = 20

# PhonePe PG v2. Sandbox and production differ in host AND in path prefix.
_HOSTS = {
    "prod": {
        "oauth": "https://api.phonepe.com/apis/identity-manager/v1/oauth/token",
        "api": "https://api.phonepe.com/apis/pg",
    },
    "sandbox": {
        "oauth": "https://api-preprod.phonepe.com/apis/pg-sandbox/v1/oauth/token",
        "api": "https://api-preprod.phonepe.com/apis/pg-sandbox",
    },
}

# One token per process, refreshed a minute early. A lock because uvicorn runs four workers and
# each handles concurrent requests: two threads racing the token endpoint is wasteful, and
# PhonePe rate-limits it.
_token_lock = threading.Lock()
_token_cache: dict = {"access_token": None, "expires_at": 0.0}


class PhonePeError(Exception):
    """A call PhonePe refused, or could not be reached. Carries a message fit for a desk toast."""


def _env() -> dict:
    return {
        "client_id": os.getenv("PHONEPE_CLIENT_ID"),
        "client_secret": os.getenv("PHONEPE_CLIENT_SECRET"),
        "client_version": os.getenv("PHONEPE_CLIENT_VERSION"),
    }


def _mode() -> str:
    return "prod" if (os.getenv("PHONEPE_ENV") or "sandbox").strip().lower() in ("prod", "production") else "sandbox"


def is_configured() -> bool:
    """True only when every credential needed to raise a charge is present."""
    return all(_env().values())


def provider_status() -> dict:
    """Read-only status for the admin config screen. Never returns a secret."""
    return {
        "configured": is_configured(),
        "mode": _mode(),
        "webhook_auth_set": bool(os.getenv("PHONEPE_WEBHOOK_USERNAME") and os.getenv("PHONEPE_WEBHOOK_PASSWORD")),
    }


def _api(path: str) -> str:
    return _HOSTS[_mode()]["api"] + path


def _access_token() -> str:
    """Client-credentials token, cached until just before it expires."""
    with _token_lock:
        now = time.time()
        if _token_cache["access_token"] and now < _token_cache["expires_at"]:
            return _token_cache["access_token"]

        cfg = _env()
        if not all(cfg.values()):
            raise PhonePeError("PhonePe is not configured on this server")
        try:
            resp = requests.post(
                _HOSTS[_mode()]["oauth"],
                data={
                    "client_id": cfg["client_id"],
                    "client_secret": cfg["client_secret"],
                    "client_version": cfg["client_version"],
                    "grant_type": "client_credentials",
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            raise PhonePeError(f"Could not reach PhonePe: {exc}") from exc

        if resp.status_code != 200:
            logger.error("[phonepe] token failed %s %s", resp.status_code, resp.text[:300])
            raise PhonePeError("PhonePe rejected the merchant credentials")

        body = resp.json()
        token = body.get("access_token")
        if not token:
            raise PhonePeError("PhonePe returned no access token")
        # `expires_at` is an absolute epoch second in PhonePe's response; fall back to a
        # conservative 15 minutes if it is missing or unparseable.
        try:
            expires_at = float(body.get("expires_at") or 0) - 60
        except (TypeError, ValueError):
            expires_at = 0
        _token_cache["access_token"] = token
        _token_cache["expires_at"] = expires_at if expires_at > now else now + 900
        return token


def _headers() -> dict:
    return {
        "Content-Type": "application/json",
        "Authorization": f"O-Bearer {_access_token()}",
    }


def create_upi_qr(order_ref: str, amount_paise: int, expiry_seconds: int, meta: dict | None = None) -> dict:
    """Raise a single-use UPI charge and return the intent the guest's app can pay.

    Returns {"order_id", "intent_url", "expire_at"}. The intent URL is a `upi://pay?...` string —
    we render the QR ourselves (segno, already a dependency) rather than hotlinking a
    PhonePe-hosted PNG, so the desk keeps working if their CDN hiccups and the image costs one
    local render instead of a round trip.

    Raises PhonePeError; the caller decides whether to fall back to a Razorpay link.
    """
    payload = {
        "merchantOrderId": order_ref,
        "amount": int(amount_paise),
        "expireAfter": int(expiry_seconds),
        "metaInfo": {k: str(v) for k, v in (meta or {}).items()},
        "paymentFlow": {"type": "PG", "paymentMode": {"type": "UPI_QR"}},
    }
    try:
        resp = requests.post(_api("/checkout/v2/pay"), json=payload, headers=_headers(), timeout=_TIMEOUT)
    except requests.RequestException as exc:
        raise PhonePeError(f"Could not reach PhonePe: {exc}") from exc

    if resp.status_code not in (200, 201):
        logger.error("[phonepe] pay failed %s %s", resp.status_code, resp.text[:400])
        raise PhonePeError(f"PhonePe refused the charge ({resp.status_code})")

    body = resp.json() or {}
    # PhonePe has spelled this field differently across versions; accept what it sends rather
    # than failing a live payment over a rename.
    intent = body.get("intentUrl") or body.get("qrData") or body.get("redirectUrl")
    if not intent:
        logger.error("[phonepe] pay returned no intent: %s", str(body)[:400])
        raise PhonePeError("PhonePe returned no UPI intent for this order")
    return {
        "order_id": body.get("orderId"),
        "intent_url": intent,
        "expire_at": body.get("expireAt"),
    }


def fetch_status(order_ref: str) -> dict | None:
    """Ask PhonePe where an order stands. None when it could not be determined.

    Returns {"state", "transaction_id", "payment_mode"} with state one of
    PENDING | COMPLETED | FAILED | EXPIRED. Never raises: this backs the desk's poll loop, which
    runs every few seconds while a guest stands at the counter.
    """
    try:
        resp = requests.get(_api(f"/checkout/v2/order/{order_ref}/status"),
                            headers=_headers(), timeout=_TIMEOUT)
    except (requests.RequestException, PhonePeError) as exc:
        logger.warning("[phonepe] status %s unreachable: %s", order_ref, exc)
        return None

    if resp.status_code != 200:
        logger.warning("[phonepe] status %s -> %s %s", order_ref, resp.status_code, resp.text[:200])
        return None

    body = resp.json() or {}
    details = body.get("paymentDetails") or []
    first = details[0] if details else {}
    return {
        "state": (body.get("state") or "").upper(),
        "transaction_id": first.get("transactionId"),
        "payment_mode": first.get("paymentMode"),
    }


def refund(refund_ref: str, order_ref: str, amount_paise: int) -> dict:
    """Refund all or part of a settled order. Returns {"refund_id", "state"}.

    A refund is frequently accepted as PENDING and completes minutes later, so the caller must
    persist the state rather than assume success.
    """
    payload = {
        "merchantRefundId": refund_ref,
        "originalMerchantOrderId": order_ref,
        "amount": int(amount_paise),
    }
    try:
        resp = requests.post(_api("/payments/v2/refund"), json=payload, headers=_headers(), timeout=_TIMEOUT)
    except requests.RequestException as exc:
        raise PhonePeError(f"Could not reach PhonePe: {exc}") from exc

    if resp.status_code not in (200, 201, 202):
        logger.error("[phonepe] refund failed %s %s", resp.status_code, resp.text[:400])
        raise PhonePeError(f"PhonePe refused the refund ({resp.status_code})")

    body = resp.json() or {}
    return {"refund_id": body.get("refundId") or refund_ref, "state": (body.get("state") or "PENDING").upper()}


def fetch_refund_status(refund_ref: str) -> dict | None:
    """Where a refund got to. None when undeterminable. Never raises."""
    try:
        resp = requests.get(_api(f"/payments/v2/refund/{refund_ref}/status"),
                            headers=_headers(), timeout=_TIMEOUT)
    except (requests.RequestException, PhonePeError) as exc:
        logger.warning("[phonepe] refund status %s unreachable: %s", refund_ref, exc)
        return None
    if resp.status_code != 200:
        return None
    body = resp.json() or {}
    return {"state": (body.get("state") or "").upper(), "refund_id": body.get("refundId") or refund_ref}


def verify_webhook(headers, body: bytes) -> bool:
    """Is this callback really from PhonePe?

    PhonePe does not sign the body. You configure a username and password against the webhook in
    their dashboard, and every callback carries `Authorization: SHA256(username:password)` — a
    shared secret, so the check is a constant-time compare of a fixed digest.

    Consequences worth being clear about: an attacker who learns the pair can forge a "payment
    received", so treat it like a password (rotate it if it ever appears in a log or a chat), and
    an UNSET pair returns False for everything rather than accepting everything. With the pair
    unset the desk still confirms payments — through the status poll, a second later.
    """
    user = os.getenv("PHONEPE_WEBHOOK_USERNAME")
    password = os.getenv("PHONEPE_WEBHOOK_PASSWORD")
    if not user or not password:
        logger.error("[phonepe] webhook rejected: PHONEPE_WEBHOOK_USERNAME/PASSWORD are not set")
        return False

    supplied = (headers.get("Authorization") or headers.get("authorization") or "").strip()
    if supplied.lower().startswith("sha256 "):
        supplied = supplied[7:].strip()
    expected = hashlib.sha256(f"{user}:{password}".encode()).hexdigest()
    return hmac.compare_digest(supplied.lower(), expected)
