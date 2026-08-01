"""Encrypted, per-booking storage for guest ID scans (FE-3).

Guest ID document scans are sensitive PII, so — unlike the generic `utils/storage.py`
(flat, public-URL CRM photos) — they are:
  * encrypted at rest with Fernet (AES-128-CBC + HMAC) using a key from the environment,
  * written under per-booking subfolders  <ID_SCAN_DIR>/booking_<id>/<uuid>.enc,
  * NEVER exposed as a static/public URL — the reception router streams them back only to an
    authenticated reception/admin caller after decrypting on read.

Secure by default: if `ID_SCAN_ENCRYPTION_KEY` is not set, `save_scan` refuses to write, so a
scan is never persisted in plaintext. Name/number KYC still works without a scan.

`cryptography` is available transitively via `python-jose[cryptography]` (and pinned explicitly
in requirements.txt).
"""
import os
import re
import uuid
import logging

logger = logging.getLogger(__name__)

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # .../backend
_SECURE_DIR = os.getenv("ID_SCAN_DIR") or os.path.join(_BASE_DIR, "secure_id_scans")

# Content types we accept for an ID scan (kept small — these are ID documents).
_ALLOWED_MIME = {"image/jpeg", "image/png", "image/webp", "application/pdf", "image/heic"}
_MAX_BYTES = 8 * 1024 * 1024  # 8 MB, mirrors the CRM upload cap

# A stored ref is exactly "booking_<digits>/<hex-or-safe>.enc" — anything else is rejected on read
# (prevents path traversal / reading arbitrary files).
_REF_RE = re.compile(r"^booking_\d+/[A-Za-z0-9_-]+\.enc$")


def _fernet():
    """Return a Fernet instance from ID_SCAN_ENCRYPTION_KEY, or None if unset/invalid."""
    key = os.getenv("ID_SCAN_ENCRYPTION_KEY")
    if not key:
        return None
    try:
        from cryptography.fernet import Fernet
        return Fernet(key.encode() if isinstance(key, str) else key)
    except Exception as e:
        logger.error(f"[secure_id_store] ID_SCAN_ENCRYPTION_KEY is set but invalid: {e}")
        return None


def is_configured() -> bool:
    """True when encryption is available (so scans can be stored/served)."""
    return _fernet() is not None


def allowed_mime(content_type: str | None) -> bool:
    return (content_type or "").lower() in _ALLOWED_MIME


def save_scan(booking_id: int, data: bytes, content_type: str | None = None) -> str:
    """Encrypt + persist an ID scan under booking_<id>/, returning its relative ref.

    Raises RuntimeError when encryption is unconfigured (caller maps to 503) — a scan is
    never written in plaintext. Raises ValueError for an empty/oversized payload."""
    f = _fernet()
    if f is None:
        raise RuntimeError("id_scan_encryption_unconfigured")
    if not data:
        raise ValueError("empty file")
    if len(data) > _MAX_BYTES:
        raise ValueError("file too large (max 8 MB)")

    folder = os.path.join(_SECURE_DIR, f"booking_{int(booking_id)}")
    os.makedirs(folder, exist_ok=True)
    name = f"{uuid.uuid4().hex}.enc"
    with open(os.path.join(folder, name), "wb") as fh:
        fh.write(f.encrypt(data))
    ref = f"booking_{int(booking_id)}/{name}"
    logger.info(f"[secure_id_store] stored encrypted ID scan: {ref}")
    return ref


def read_scan(ref: str) -> bytes:
    """Decrypt + return the bytes for a stored ref. Raises FileNotFoundError (missing/bad ref)
    or RuntimeError (unconfigured / undecryptable)."""
    if not ref or not _REF_RE.match(ref):
        raise FileNotFoundError("invalid scan ref")
    f = _fernet()
    if f is None:
        raise RuntimeError("id_scan_encryption_unconfigured")
    path = os.path.join(_SECURE_DIR, ref)
    if not os.path.exists(path):
        raise FileNotFoundError("scan not found")
    with open(path, "rb") as fh:
        blob = fh.read()
    try:
        return f.decrypt(blob)
    except Exception as e:
        # Wrong key or corrupted file — never leak the ciphertext.
        logger.error(f"[secure_id_store] failed to decrypt {ref}: {e}")
        raise RuntimeError("id_scan_undecryptable") from e
