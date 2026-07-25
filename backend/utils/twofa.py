"""Admin 2FA / TOTP helpers (prompt 18, slice 6).

Thin wrapper around `pyotp` (RFC 6238 time-based one-time passwords) so the router stays small.
A user's base32 secret lives on `users.totp_secret` (same storage posture as `users.pin`;
encrypting it at rest with Fernet is a noted follow-up). Enrolment renders the provisioning
`otpauth://` URI as a QR **PNG data-URI** via `segno` (pure-python — no PIL / native deps and no
external host), so the browser needs no QR library and the CSP stays clean.

`pyotp`/`segno` are declared in requirements; imports are guarded so the app still boots (with 2FA
disabled) if a deployment hasn't installed them yet — mirroring the boto3/OCR pluggable idiom.
"""
import base64
import io
import logging

logger = logging.getLogger(__name__)

ISSUER = "Hotel Bhimas"

try:
    import pyotp  # type: ignore
    _HAS_PYOTP = True
except Exception:  # pragma: no cover - only when dependency missing
    pyotp = None
    _HAS_PYOTP = False

try:
    import segno  # type: ignore
    _HAS_SEGNO = True
except Exception:  # pragma: no cover
    segno = None
    _HAS_SEGNO = False


def available() -> bool:
    """True when the TOTP dependency is installed (2FA can be used)."""
    return _HAS_PYOTP


def new_secret() -> str:
    """Fresh base32 TOTP secret."""
    if not _HAS_PYOTP:
        raise RuntimeError("pyotp is not installed; 2FA is unavailable")
    return pyotp.random_base32()


def provisioning_uri(secret: str, username: str) -> str:
    """otpauth:// URI to seed an authenticator app (Google Authenticator / Authy / ...)."""
    if not _HAS_PYOTP:
        raise RuntimeError("pyotp is not installed; 2FA is unavailable")
    return pyotp.TOTP(secret).provisioning_uri(name=username, issuer_name=ISSUER)


def verify(secret: str, code: str, *, valid_window: int = 1) -> bool:
    """Verify a 6-digit TOTP code. `valid_window=1` tolerates ±1 step (30 s) of clock drift."""
    if not _HAS_PYOTP or not secret or not code:
        return False
    try:
        return bool(pyotp.TOTP(secret).verify(str(code).strip(), valid_window=valid_window))
    except Exception:
        logger.exception("TOTP verify failed")
        return False


def qr_data_uri(otpauth_uri: str) -> str | None:
    """Render an otpauth URI to a PNG data-URI (`data:image/png;base64,...`) for the enrolment
    screen, or None when segno is unavailable (the UI then falls back to showing the secret)."""
    if not _HAS_SEGNO:
        return None
    try:
        buf = io.BytesIO()
        segno.make(otpauth_uri, error="m").save(buf, kind="png", scale=5, border=2)
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        return f"data:image/png;base64,{b64}"
    except Exception:
        logger.exception("QR render failed")
        return None
