"""Object-storage transport for guest ID scans (private R2 bucket).

Pure transport: this module knows nothing about Fernet, refs or bookings. It moves opaque
bytes to and from a key. `secure_id_store` owns the encryption and the ref scheme, and calls
in here only once the payload is already ciphertext.

WHY NOT `utils/storage.py`. That module serves public CRM photos and is wrong here in three
independent ways:
  * it FAILS OPEN — any exception falls through to a local-disk write (storage.py:79-90).
    Doing that with an ID scan writes government-ID PII onto an ephemeral container disk,
    which is the exact failure FE-3 exists to prevent.
  * it requires `R2_PUBLIC_URL` (storage.py:38), because its whole delivery model is a public
    CDN URL. Scans must never have one.
  * it only implements put_object.
So this is a separate module with its own env prefix. Do NOT let ID_SCAN_R2_BUCKET fall back
to R2_BUCKET: that bucket has a public CDN in front of it.

Config (all independent of the CRM R2_* vars):
    ID_SCAN_STORAGE       local | r2      (default local — deploying this file changes nothing)
    ID_SCAN_R2_BUCKET
    ID_SCAN_R2_ACCESS_KEY
    ID_SCAN_R2_SECRET
    ID_SCAN_R2_ACCOUNT_ID   (endpoint derived from it) or ID_SCAN_R2_ENDPOINT
    ID_SCAN_R2_PREFIX     default "id-scans/"

Errors map to exactly two types so `secure_id_store` keeps the exception contract that
`routers/reception.py` already handles:
    FileNotFoundError  -> the object is not there              (router: 404)
    RuntimeError       -> storage unreachable/misconfigured    (router: 503)
"""
import logging
import os
import threading

logger = logging.getLogger(__name__)

_DEFAULT_PREFIX = "id-scans/"

_client = None
_client_lock = threading.Lock()


def _config() -> dict | None:
    """The R2 config when fully set, else None. Note: no public_url — this bucket is private."""
    cfg = {
        "account_id": os.getenv("ID_SCAN_R2_ACCOUNT_ID"),
        "endpoint": os.getenv("ID_SCAN_R2_ENDPOINT"),
        "access_key": os.getenv("ID_SCAN_R2_ACCESS_KEY"),
        "secret": os.getenv("ID_SCAN_R2_SECRET"),
        "bucket": os.getenv("ID_SCAN_R2_BUCKET"),
    }
    if all(cfg[k] for k in ("access_key", "secret", "bucket")) and (cfg["account_id"] or cfg["endpoint"]):
        return cfg
    return None


def is_configured() -> bool:
    return _config() is not None


def prefix() -> str:
    p = os.getenv("ID_SCAN_R2_PREFIX", _DEFAULT_PREFIX)
    return p if (not p or p.endswith("/")) else p + "/"


def describe() -> dict:
    """Non-secret summary for the admin status endpoint."""
    cfg = _config()
    return {
        "configured": cfg is not None,
        "bucket": cfg["bucket"] if cfg else None,
        "prefix": prefix(),
    }


def _get_client():
    """Cached boto3 client. `storage.py` builds one per call, which is fine for the occasional
    CRM photo but not for the check-in desk — client construction is credential/session setup
    and this sits on the upload path."""
    global _client
    if _client is not None:
        return _client
    with _client_lock:
        if _client is not None:
            return _client
        cfg = _config()
        if cfg is None:
            raise RuntimeError("id_scan_storage_unconfigured")
        try:
            import boto3  # lazy; a missing dep must not break local mode
            from botocore.config import Config
        except Exception as e:
            raise RuntimeError("id_scan_storage_unavailable") from e
        endpoint = cfg["endpoint"] or f"https://{cfg['account_id']}.r2.cloudflarestorage.com"
        # Timeouts are the important part: with `uvicorn --workers 2`, a default 60s socket
        # hang during check-in pins a worker and reads as an outage at the desk.
        _client = boto3.client(
            "s3", endpoint_url=endpoint,
            aws_access_key_id=cfg["access_key"], aws_secret_access_key=cfg["secret"],
            region_name="auto",
            config=Config(signature_version="s3v4",
                          retries={"max_attempts": 3, "mode": "standard"},
                          connect_timeout=5, read_timeout=20,
                          s3={"addressing_style": "virtual"}),
        )
        logger.info(f"[scan_objectstore] R2 client ready (bucket={cfg['bucket']}, prefix={prefix()})")
        return _client


def _bucket() -> str:
    cfg = _config()
    if cfg is None:
        raise RuntimeError("id_scan_storage_unconfigured")
    return cfg["bucket"]


def _is_missing(exc) -> bool:
    resp = getattr(exc, "response", None) or {}
    code = (resp.get("Error") or {}).get("Code", "")
    status = (resp.get("ResponseMetadata") or {}).get("HTTPStatusCode")
    return code in ("NoSuchKey", "404", "NotFound") or status == 404


def put_object(key: str, body: bytes) -> None:
    """Store `body` at `key`. Raises RuntimeError on any storage failure — never falls back
    to disk, because that would write PII onto an ephemeral filesystem."""
    client = _get_client()
    try:
        client.put_object(Bucket=_bucket(), Key=key, Body=body,
                          ContentType="application/octet-stream")
    except Exception as e:
        logger.error(f"[scan_objectstore] put failed for {key}: {e}")
        raise RuntimeError("id_scan_storage_unavailable") from e


def get_object(key: str) -> bytes:
    """Fetch the bytes at `key`. FileNotFoundError when absent, RuntimeError when unreachable."""
    client = _get_client()
    try:
        resp = client.get_object(Bucket=_bucket(), Key=key)
        return resp["Body"].read()
    except Exception as e:
        if _is_missing(e):
            raise FileNotFoundError("scan not found") from e
        logger.error(f"[scan_objectstore] get failed for {key}: {e}")
        raise RuntimeError("id_scan_storage_unavailable") from e


def head_object(key: str) -> dict | None:
    """Size/etag/mtime for `key`, or None when absent."""
    client = _get_client()
    try:
        resp = client.head_object(Bucket=_bucket(), Key=key)
        return {
            "bytes": int(resp.get("ContentLength") or 0),
            "etag": (resp.get("ETag") or "").strip('"'),
            "last_modified": resp.get("LastModified"),
        }
    except Exception as e:
        if _is_missing(e):
            return None
        logger.error(f"[scan_objectstore] head failed for {key}: {e}")
        raise RuntimeError("id_scan_storage_unavailable") from e


def iter_objects(key_prefix: str = ""):
    """Yield {key, bytes, etag, last_modified} for every object under the prefix (paginated)."""
    client = _get_client()
    full_prefix = f"{prefix()}{key_prefix}"
    try:
        paginator = client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=_bucket(), Prefix=full_prefix):
            for obj in page.get("Contents", []) or []:
                yield {
                    "key": obj["Key"],
                    "bytes": int(obj.get("Size") or 0),
                    "etag": (obj.get("ETag") or "").strip('"'),
                    "last_modified": obj.get("LastModified"),
                }
    except Exception as e:
        logger.error(f"[scan_objectstore] list failed for {full_prefix}: {e}")
        raise RuntimeError("id_scan_storage_unavailable") from e


def delete_object(key: str) -> None:
    """Delete `key`. Idempotent — an object that is already gone is a success, so a retried
    purge cannot fail on its own previous work."""
    client = _get_client()
    try:
        client.delete_object(Bucket=_bucket(), Key=key)
    except Exception as e:
        if _is_missing(e):
            return
        logger.error(f"[scan_objectstore] delete failed for {key}: {e}")
        raise RuntimeError("id_scan_storage_unavailable") from e
