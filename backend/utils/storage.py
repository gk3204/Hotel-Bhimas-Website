"""Pluggable object storage for uploaded files — ID/guest photos (prompt 14).

The codebase had no upload infrastructure (photo columns were text-only, deferred to
"prompt 14 / R2"). This module gives one `save_upload()` seam with two backends:

  * Cloudflare R2 / S3 — used when the R2_* env vars are all set (boto3, S3-compatible).
  * Local disk fallback — otherwise writes to backend/uploads/ and returns a URL served
    by the CRM router's `GET /crm/files/{name}`. This makes the feature fully testable
    without provisioning R2; flip to R2 later purely by setting env (no code change).

boto3 is imported lazily and guarded, so a missing dependency only disables the R2 path.
"""
import os
import re
import uuid
import logging

logger = logging.getLogger(__name__)

# Local fallback dir (created on first use). Sits next to the backend package.
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # .../backend
UPLOAD_DIR = os.path.join(_BASE_DIR, "uploads")

_ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp", ".pdf", ".heic"}
_SAFE_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")


def _r2_config():
    """Return the R2/S3 config dict when fully configured, else None."""
    cfg = {
        "account_id": os.getenv("R2_ACCOUNT_ID"),
        "access_key": os.getenv("R2_ACCESS_KEY"),
        "secret": os.getenv("R2_SECRET"),
        "bucket": os.getenv("R2_BUCKET"),
        "public_url": os.getenv("R2_PUBLIC_URL"),  # e.g. https://cdn.example.com  (no trailing slash needed)
        "endpoint": os.getenv("R2_ENDPOINT"),      # optional explicit S3 endpoint (else derived from account_id)
    }
    required = ("access_key", "secret", "bucket", "public_url")
    if all(cfg[k] for k in required) and (cfg["account_id"] or cfg["endpoint"]):
        return cfg
    return None


def is_r2_configured() -> bool:
    return _r2_config() is not None


def _ext_for(filename: str, content_type: str | None) -> str:
    ext = os.path.splitext(filename or "")[1].lower()
    if ext in _ALLOWED_EXT:
        return ext
    # map a couple of common content types when the filename lacks an extension
    ct = (content_type or "").lower()
    return {
        "image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp",
        "application/pdf": ".pdf", "image/heic": ".heic",
    }.get(ct, ".jpg")


def save_bytes(data: bytes, filename: str, content_type: str | None = None, kind: str = "crm") -> str:
    """Persist `data` and return a URL. Uses R2 when configured, else local disk."""
    ext = _ext_for(filename, content_type)
    key = f"{kind}/{uuid.uuid4().hex}{ext}"

    cfg = _r2_config()
    if cfg:
        try:
            import boto3  # lazy; missing dep only disables R2
            endpoint = cfg["endpoint"] or f"https://{cfg['account_id']}.r2.cloudflarestorage.com"
            client = boto3.client(
                "s3", endpoint_url=endpoint,
                aws_access_key_id=cfg["access_key"], aws_secret_access_key=cfg["secret"],
                region_name="auto",
            )
            client.put_object(Bucket=cfg["bucket"], Key=key, Body=data,
                              ContentType=content_type or "application/octet-stream")
            logger.info(f"[storage] uploaded to R2: {key}")
            return f"{cfg['public_url'].rstrip('/')}/{key}"
        except Exception as e:
            # Never crash the request on a storage hiccup; fall back to local disk.
            logger.error(f"[storage] R2 upload failed ({e}); falling back to local disk", exc_info=True)

    # Local fallback
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    name = f"{uuid.uuid4().hex}{ext}"
    path = os.path.join(UPLOAD_DIR, name)
    with open(path, "wb") as f:
        f.write(data)
    logger.info(f"[storage] saved locally: {name}")
    return f"/crm/files/{name}"


def local_path(name: str) -> str | None:
    """Absolute path for a locally-stored file name, or None if the name is unsafe/missing."""
    if not name or not _SAFE_NAME.match(name):
        return None
    path = os.path.join(UPLOAD_DIR, name)
    return path if os.path.exists(path) else None
