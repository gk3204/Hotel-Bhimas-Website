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


def _backend() -> str:
    """Which storage backend is live: "local" (filesystem) or "r2" (private bucket).

    Defaults to local, so deploying the R2 code changes nothing until the variable is flipped.
    A HALF-configured "r2" is deliberately NOT a fallback to disk — see storage_ready()."""
    return (os.getenv("ID_SCAN_STORAGE") or "local").strip().lower()


def storage_backend() -> str:
    return _backend()


def storage_ready() -> bool:
    """True when the active backend can actually be used. Local is always ready; r2 needs its
    credentials. Surfaced by the admin status endpoint so nobody has to discover a
    misconfiguration from a 503 at the front desk."""
    if _backend() != "r2":
        return True
    from utils import scan_objectstore
    return scan_objectstore.is_configured()


def _key(ref: str) -> str:
    """Object key for a ref. The bucket and prefix are NEVER part of the stored ref — that is
    what keeps refs backend-independent and inside booking_guests' VARCHAR(120)."""
    from utils import scan_objectstore
    return f"{scan_objectstore.prefix()}{ref}"

# Content types we accept for an ID scan (kept small — these are ID documents). PUBLIC because
# it is the one allowlist: the upload endpoint checks it, the check-in schema checks the mime the
# desk sends back, and the read path clamps to it before setting a Content-Type header.
ALLOWED_MIME = {"image/jpeg", "image/png", "image/webp", "application/pdf", "image/heic"}
_ALLOWED_MIME = ALLOWED_MIME   # legacy alias
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
    return (content_type or "").lower() in ALLOWED_MIME


def safe_media_type(content_type: str | None) -> str:
    """The Content-Type it is safe to serve a decrypted scan with.

    `id_scan_mime` reaches us on the check-in payload, i.e. from the client. Anything outside the
    allowlist is served as an opaque download instead: the admin renders a scan into a blob: URL,
    which inherits the ADMIN ORIGIN, so a stored `text/html` would be a same-origin script the day
    that viewer previews anything other than a PDF. Rows written before this was validated can
    still hold whatever the desk sent, so the clamp lives here on the read path as well."""
    ct = (content_type or "").lower()
    return ct if ct in ALLOWED_MIME else "application/octet-stream"


# Extension per allowed type, for the download filename (never built from a guest's name).
_EXT_FOR_MIME = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp",
                 "application/pdf": "pdf", "image/heic": "heic"}


def extension_for(content_type: str | None) -> str:
    return _EXT_FOR_MIME.get((content_type or "").lower(), "bin")


def valid_ref(ref: str | None) -> bool:
    """True when `ref` has the exact stored shape booking_<digits>/<safe>.enc."""
    return bool(ref and _REF_RE.match(ref))


def ref_belongs_to(ref: str | None, booking_id: int) -> bool:
    """True when `ref` is well-formed AND lives under this booking's own folder.

    Scans are namespaced by the booking they were uploaded against, but the ref travels back to
    us on the check-in payload, where nothing re-checked it. Reusing one guest's ref for the whole
    roster made three occupants read "ID on file" from a single scan — and made `id.view` audit the
    wrong guest. Checking the prefix is what ties a stored image back to the booking it was taken
    for."""
    return valid_ref(ref) and ref.split("/", 1)[0] == f"booking_{int(booking_id)}"


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

    name = f"{uuid.uuid4().hex}.enc"
    ref = f"booking_{int(booking_id)}/{name}"
    blob = f.encrypt(data)

    if _backend() == "r2":
        # A storage failure surfaces as RuntimeError, which the caller already maps to 503.
        # It must NEVER fall back to the container disk: that filesystem is ephemeral, and
        # writing PII there is exactly what this module exists to prevent.
        from utils import scan_objectstore
        scan_objectstore.put_object(_key(ref), blob)
    else:
        folder = os.path.join(_SECURE_DIR, f"booking_{int(booking_id)}")
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, name), "wb") as fh:
            fh.write(blob)

    logger.info(f"[secure_id_store] stored encrypted ID scan: {ref} ({_backend()})")
    return ref


def read_scan(ref: str) -> bytes:
    """Decrypt + return the bytes for a stored ref. Raises FileNotFoundError (missing/bad ref)
    or RuntimeError (unconfigured / undecryptable)."""
    if not ref or not _REF_RE.match(ref):
        raise FileNotFoundError("invalid scan ref")
    f = _fernet()
    if f is None:
        raise RuntimeError("id_scan_encryption_unconfigured")
    if _backend() == "r2":
        from utils import scan_objectstore
        blob = scan_objectstore.get_object(_key(ref))   # FileNotFoundError / RuntimeError
    else:
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


# ---------------------------------------------------------------------------------------
# Archive support. Used only by the admin backup endpoints — NOT by the check-in desk.
# These deal in ciphertext and never decrypt: the archive is meant to be unreadable without
# the escrowed ID_SCAN_ENCRYPTION_KEY, which is what makes it safe to hold off-server.
# ---------------------------------------------------------------------------------------

def read_ciphertext(ref: str) -> bytes:
    """The stored bytes for `ref`, still encrypted. For archiving only."""
    if not valid_ref(ref):
        raise FileNotFoundError("invalid scan ref")
    if _backend() == "r2":
        from utils import scan_objectstore
        return scan_objectstore.get_object(_key(ref))
    path = os.path.join(_SECURE_DIR, ref)
    if not os.path.exists(path):
        raise FileNotFoundError("scan not found")
    with open(path, "rb") as fh:
        return fh.read()


def list_scans(limit: int | None = None) -> list[dict]:
    """Every stored scan as {ref, bytes, last_modified}, oldest first.

    Anything that fails valid_ref() is dropped rather than reported: a stray object in the
    bucket must never become a delete target for the purge path."""
    out: list[dict] = []
    if _backend() == "r2":
        from utils import scan_objectstore
        plen = len(scan_objectstore.prefix())
        for obj in scan_objectstore.iter_objects():
            ref = obj["key"][plen:]
            if not valid_ref(ref):
                logger.warning(f"[secure_id_store] ignoring unexpected object: {obj['key']}")
                continue
            out.append({"ref": ref, "bytes": obj["bytes"], "last_modified": obj["last_modified"]})
    else:
        import datetime
        if os.path.isdir(_SECURE_DIR):
            for folder in os.listdir(_SECURE_DIR):
                fpath = os.path.join(_SECURE_DIR, folder)
                if not os.path.isdir(fpath):
                    continue
                for name in os.listdir(fpath):
                    ref = f"{folder}/{name}"
                    if not valid_ref(ref):
                        continue
                    st = os.stat(os.path.join(fpath, name))
                    out.append({
                        "ref": ref, "bytes": st.st_size,
                        "last_modified": datetime.datetime.fromtimestamp(st.st_mtime),
                    })
    out.sort(key=lambda r: (r["last_modified"] is None, r["last_modified"]))
    return out[:limit] if limit else out


def stat_scan(ref: str) -> dict | None:
    """{bytes, last_modified} for `ref`, or None when it is not stored."""
    if not valid_ref(ref):
        return None
    if _backend() == "r2":
        from utils import scan_objectstore
        return scan_objectstore.head_object(_key(ref))
    path = os.path.join(_SECURE_DIR, ref)
    if not os.path.exists(path):
        return None
    import datetime
    st = os.stat(path)
    return {"bytes": st.st_size, "etag": None,
            "last_modified": datetime.datetime.fromtimestamp(st.st_mtime)}


def delete_scan(ref: str) -> bool:
    """Remove a stored scan. Idempotent: already-gone counts as success, so a retried purge
    never fails on its own previous work. Raises FileNotFoundError on a malformed ref."""
    if not valid_ref(ref):
        raise FileNotFoundError("invalid scan ref")
    if _backend() == "r2":
        from utils import scan_objectstore
        scan_objectstore.delete_object(_key(ref))
    else:
        path = os.path.join(_SECURE_DIR, ref)
        if os.path.exists(path):
            os.remove(path)
    logger.info(f"[secure_id_store] deleted stored scan: {ref}")
    return True
