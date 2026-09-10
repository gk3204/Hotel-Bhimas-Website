"""Database backup export (v4b10, R11).

The hotel keeps its own copy of the database on a machine it controls, so it survives losing
the Railway account or project — while Railway's managed snapshots survive the hotel's PC dying.
Neither substitutes for the other; run both.

WHY THE DUMP IS TAKEN *HERE* AND NOT ON THE CLIENT
    The obvious shortcut is to run `pg_dump` on the admin PC against Railway's public TCP proxy
    (`DATABASE_PUBLIC_URL`). It needs no backend code at all — and it puts a credential that can
    read every guest's ID document and rewrite any folio into a config file on a Windows desktop,
    in a system whose stated first concern is internal fraud. This endpoint yields the identical
    artifact behind an admin JWT that can be revoked, and the client then needs no `pg_dump`
    installed and no version matching against the server. `postgresql-client` is already in the
    backend image (Dockerfile), so this costs nothing.

WHY `--format=custom`
    A real `pg_restore` archive: selective restore, parallel restore, compressed, and restorable
    on any matching major version. A hand-rolled SQLAlchemy export would be a second thing to
    keep correct forever, and would silently drift from the schema.

⚠️ The export hands over the ENTIRE database. It is admin-JWT only, audited with the caller and
their IP, and rate-limited to one per hour — the limit lives in `app_settings`, not in process
memory, because uvicorn runs with `--workers 4` and an in-memory counter would allow four.
"""
import hashlib
import json
import logging
import os
import shutil
import subprocess
import tempfile
import uuid
import zipfile
from datetime import datetime, timedelta
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy import func, text
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from database import SessionLocal
from models import (Booking, BookingGuest, Folio, FolioCharge, Guest, Invoice,
                    Payment, Room, User)
from utils import scan_objectstore, secure_id_store
from utils.audit import write_audit
from utils.auth_utils import require_admin
from utils.settings import get_setting, set_setting

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/backup", tags=["Backup"])

# Persisted state (app_settings). Shared across worker processes, unlike anything in memory.
LAST_EXPORT_AT_KEY = "backup_last_export_at"        # ISO-8601, set BEFORE the dump starts
LAST_SUCCESS_AT_KEY = "backup_last_success_at"      # ISO-8601, set only on a completed dump
LAST_SUCCESS_BY_KEY = "backup_last_success_by"
LAST_SUCCESS_BYTES_KEY = "backup_last_success_bytes"
LAST_SUCCESS_SHA_KEY = "backup_last_success_sha256"

MIN_INTERVAL = timedelta(hours=1)
DUMP_TIMEOUT_SECONDS = 900          # 15 min: a stuck pg_dump must not pin a worker forever


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        raise HTTPException(status_code=503, detail="DATABASE_URL is not configured")
    # SQLAlchemy accepts postgresql+psycopg2://; libpq does not.
    return url.replace("postgresql+psycopg2://", "postgresql://", 1)


def _client_ip(request: Request | None) -> str | None:
    if request is None:
        return None
    # Railway sits behind a proxy, so the socket peer is the edge, not the operator.
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else None


def _parse_dt(value):
    try:
        return datetime.fromisoformat(value) if value else None
    except ValueError:
        return None


def _rate_limit_check(db: Session, key: str = LAST_EXPORT_AT_KEY, what: str = "backup"):
    """One export per hour. Reads persisted state so every worker shares the limit.

    `key` is a parameter because the scan archive MUST NOT share the database dump's slot:
    the weekly scans run would otherwise 429 that night's `pg_dump`, and the failure would
    look like a broken backup rather than a shared counter."""
    last = _parse_dt(get_setting(db, key))
    if last is None:
        return
    waited = datetime.utcnow() - last
    if waited < MIN_INTERVAL:
        mins = int((MIN_INTERVAL - waited).total_seconds() // 60) + 1
        raise HTTPException(
            status_code=429,
            detail=f"A {what} was taken less than an hour ago. Try again in {mins} minute(s).")


def _row_counts(db: Session) -> dict:
    """A cheap sanity check for the person holding the file: a dump of an empty database
    looks exactly like a dump of a full one until you restore it."""
    counts = {}
    for label, model in (("bookings", Booking), ("guests", Guest), ("folios", Folio),
                         ("folio_charges", FolioCharge), ("payments", Payment),
                         ("invoices", Invoice), ("rooms", Room), ("users", User)):
        try:
            counts[label] = int(db.query(func.count()).select_from(model).scalar() or 0)
        except Exception:                                   # a table that isn't there yet
            counts[label] = None
    return counts


def _server_version(db: Session) -> str | None:
    try:
        return db.execute(text("SHOW server_version")).scalar()
    except Exception:
        return None


@router.get("/status", dependencies=[Depends(require_admin)])
def backup_status(db: Session = Depends(get_db)):
    """Is the nightly backup still running? The failure mode this exists to prevent is a job
    that quietly stopped weeks ago and is discovered at restore time."""
    last_success = _parse_dt(get_setting(db, LAST_SUCCESS_AT_KEY))
    age_hours = None
    if last_success:
        age_hours = round((datetime.utcnow() - last_success).total_seconds() / 3600, 1)
    return {
        "last_success_at": last_success.isoformat() if last_success else None,
        "last_success_by": get_setting(db, LAST_SUCCESS_BY_KEY),
        "last_success_bytes": int(get_setting(db, LAST_SUCCESS_BYTES_KEY) or 0) or None,
        "last_success_sha256": get_setting(db, LAST_SUCCESS_SHA_KEY),
        "age_hours": age_hours,
        # A nightly job that has not run for over 36 hours has missed at least one night.
        "stale": last_success is None or age_hours is None or age_hours > 36,
        "pg_dump_available": shutil.which("pg_dump") is not None,
    }


@router.get("/manifest", dependencies=[Depends(require_admin)])
def backup_manifest(db: Session = Depends(get_db)):
    """What a dump taken right now would contain. Written beside the .dump by the client so a
    restore can be checked against what was actually in the database at the time."""
    return {
        "generated_at": datetime.utcnow().isoformat(),
        "server_version": _server_version(db),
        "row_counts": _row_counts(db),
        "format": "pg_dump --format=custom --no-owner --no-privileges",
        "restore_with": "pg_restore --clean --if-exists --no-owner --dbname=<target> <file>",
    }


@router.get("/export")
def backup_export(request: Request, db: Session = Depends(get_db),
                  user=Depends(require_admin)):
    """Stream a `pg_dump` custom-format archive of the whole database.

    ⚠️ The dump is spooled to a TEMP FILE first, deliberately. `X-Backup-Sha256` has to be
    correct, and HTTP headers precede the body — so a hash of a stream we are already sending
    is impossible. The alternative, holding the dump in memory, is what would actually hurt:
    a worker RSS spike the size of the database. Disk on Railway is ephemeral and the file is
    deleted after the response is sent, whether or not the client completed the download.
    """
    _rate_limit_check(db)

    if shutil.which("pg_dump") is None:
        raise HTTPException(
            status_code=503,
            detail="pg_dump is not installed in this container — the backend image must "
                   "include postgresql-client.")

    url = _database_url()
    started = datetime.utcnow()
    # Claim the rate-limit slot BEFORE the work, so two simultaneous requests cannot both pass.
    set_setting(db, LAST_EXPORT_AT_KEY, started.isoformat(), user=user, commit=True)

    stamp = started.strftime("%Y-%m-%d_%H%M")
    filename = f"bhimas-backup-{stamp}.dump"
    tmp = tempfile.NamedTemporaryFile(prefix="bhimas-backup-", suffix=".dump", delete=False)
    tmp_path = tmp.name
    tmp.close()

    ip = _client_ip(request)
    try:
        proc = subprocess.run(
            ["pg_dump", "--format=custom", "--no-owner", "--no-privileges",
             "--file", tmp_path, url],
            capture_output=True, timeout=DUMP_TIMEOUT_SECONDS,
        )
        if proc.returncode != 0:
            # stderr can echo the connection string; log it, never return it to the caller.
            logger.error("pg_dump failed (%s): %s", proc.returncode,
                         proc.stderr.decode("utf-8", "replace")[:2000])
            raise HTTPException(status_code=500, detail="The database dump failed. See server logs.")

        size = os.path.getsize(tmp_path)
        if size == 0:
            raise HTTPException(status_code=500, detail="The database dump came out empty.")

        sha = hashlib.sha256()
        with open(tmp_path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                sha.update(chunk)
        digest = sha.hexdigest()

        set_setting(db, LAST_SUCCESS_AT_KEY, datetime.utcnow().isoformat(), user=user)
        set_setting(db, LAST_SUCCESS_BY_KEY, (user or {}).get("sub") or "admin", user=user)
        set_setting(db, LAST_SUCCESS_BYTES_KEY, str(size), user=user)
        set_setting(db, LAST_SUCCESS_SHA_KEY, digest, user=user, commit=True)

        write_audit(db, user, "backup.export", "database", None,
                    after={"filename": filename, "bytes": size, "sha256": digest,
                           "took_seconds": round((datetime.utcnow() - started).total_seconds(), 1)},
                    ip=ip, client="web", commit=True)
        logger.info("💾 Database backup exported by %s from %s — %s bytes",
                    (user or {}).get("sub"), ip, size)

        return FileResponse(
            tmp_path,
            media_type="application/octet-stream",
            filename=filename,
            headers={
                "X-Backup-Sha256": digest,
                "X-Backup-Bytes": str(size),
                "X-Backup-Taken-At": started.isoformat(),
            },
            # Runs after the body is sent — and also if the client disconnects mid-download.
            background=BackgroundTask(_cleanup, tmp_path),
        )
    except subprocess.TimeoutExpired:
        _cleanup(tmp_path)
        logger.error("pg_dump timed out after %ss", DUMP_TIMEOUT_SECONDS)
        raise HTTPException(status_code=504, detail="The database dump timed out.")
    except HTTPException:
        _cleanup(tmp_path)
        raise
    except Exception as e:
        _cleanup(tmp_path)
        logger.error("backup export failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="The backup export failed.")


def _cleanup(path: str):
    try:
        os.unlink(path)
    except OSError:
        pass


# =========================================================================================
# Guest ID scan archive
#
# The database dump above does NOT contain ID scans — they are Fernet-encrypted blobs in
# object storage, not rows. These endpoints let the owner's PC pull a verified copy of them
# on the same schedule, so the two halves of "everything the hotel holds" are both covered.
#
# Everything here moves CIPHERTEXT and never decrypts. The archive is deliberately unreadable
# on the machine that stores it: ID_SCAN_ENCRYPTION_KEY lives only in the server environment
# and an offline escrow. That is the property that makes it safe to hold on a desktop.
# =========================================================================================

SCANS_LAST_EXPORT_AT_KEY = "scans_last_export_at"
SCANS_LAST_SUCCESS_AT_KEY = "scans_last_success_at"
SCANS_LAST_SUCCESS_COUNT_KEY = "scans_last_success_count"
SCANS_LAST_SUCCESS_SHA_KEY = "scans_last_success_sha256"
SCANS_LAST_PURGE_AT_KEY = "scans_last_purge_at"

# Nothing may be deleted from object storage until it is at least this old, regardless of what
# any client asks for. Enforced server-side in the purge path (stage 4).
SCAN_ARCHIVE_MIN_AGE_DAYS = int(os.getenv("SCAN_ARCHIVE_MIN_AGE_DAYS", "90"))

SCANS_MAX_LIMIT = 2000
SCANS_DEFAULT_LIMIT = 500
# Railway's disk is ephemeral and shared; refuse rather than fill it. 8 MB/scan cap means the
# default batch of 500 cannot realistically approach this, but a bad `limit` could.
SCANS_MAX_EXPORT_BYTES = 512 * 1024 * 1024


def _scan_ref_owners(db: Session, refs: set) -> dict:
    """Map each ref -> {guest_id, booking_id, side, mime} for refs that a roster row still
    points at. A ref absent from this map is an ORPHAN: stored bytes nothing references."""
    if not refs:
        return {}
    owners = {}
    rows = (db.query(BookingGuest)
            .filter((BookingGuest.id_scan_ref.in_(refs)) |
                    (BookingGuest.id_scan_back_ref.in_(refs))).all())
    for g in rows:
        if g.id_scan_ref in refs:
            owners[g.id_scan_ref] = {"guest_id": g.id, "booking_id": g.booking_id,
                                     "side": "front", "mime": g.id_scan_mime,
                                     "archived_at": g.id_scan_archived_at}
        if g.id_scan_back_ref in refs:
            owners[g.id_scan_back_ref] = {"guest_id": g.id, "booking_id": g.booking_id,
                                          "side": "back", "mime": g.id_scan_back_mime,
                                          "archived_at": g.id_scan_back_archived_at}
    return owners


def _collect_scans(db: Session, before_days: int, limit: int) -> list:
    """Stored scans, oldest first, annotated with the roster row that owns each one."""
    items = secure_id_store.list_scans()
    if before_days > 0:
        cutoff = datetime.utcnow() - timedelta(days=before_days)
        items = [i for i in items
                 if i["last_modified"] is not None
                 and _naive(i["last_modified"]) <= cutoff]
    items = items[:limit]
    owners = _scan_ref_owners(db, {i["ref"] for i in items})
    for i in items:
        own = owners.get(i["ref"])
        i["in_use"] = own is not None
        i["booking_id"] = own["booking_id"] if own else None
        i["guest_id"] = own["guest_id"] if own else None
        i["side"] = own["side"] if own else None
        i["id_scan_mime"] = own["mime"] if own else None
        i["already_archived"] = bool(own and own["archived_at"])
    return items


def _naive(dt):
    """R2 returns tz-aware datetimes; the local backend returns naive ones. Compare in UTC."""
    return dt.replace(tzinfo=None) if getattr(dt, "tzinfo", None) else dt


@router.get("/scans/status", dependencies=[Depends(require_admin)])
def scans_status(db: Session = Depends(get_db)):
    """Health of the ID-scan archive.

    Deliberately a SEPARATE endpoint from /status: an object-storage outage must not break the
    one panel whose entire job is telling the owner whether the database backup is healthy."""
    store = scan_objectstore.describe()
    backend = secure_id_store.storage_backend()
    online_count = online_bytes = 0
    oldest = None
    error = None
    try:
        if secure_id_store.storage_ready():
            items = secure_id_store.list_scans()
            online_count = len(items)
            online_bytes = sum(i["bytes"] for i in items)
            oldest = _naive(items[0]["last_modified"]).isoformat() if items else None
    except Exception as e:                                  # storage down — report, never 500
        logger.error("scan status listing failed: %s", e)
        error = "Object storage could not be listed. See server logs."

    last_ok = _parse_dt(get_setting(db, SCANS_LAST_SUCCESS_AT_KEY))
    age_hours = None
    if last_ok:
        age_hours = round((datetime.utcnow() - last_ok).total_seconds() / 3600.0, 1)
    return {
        "backend": backend,
        "storage_ready": secure_id_store.storage_ready(),
        "bucket": store.get("bucket"),
        "prefix": store.get("prefix"),
        "online_count": online_count,
        "online_bytes": online_bytes,
        "oldest_scan_at": oldest,
        "last_export_at": last_ok.isoformat() if last_ok else None,
        "last_export_count": get_setting(db, SCANS_LAST_SUCCESS_COUNT_KEY),
        "last_export_sha256": get_setting(db, SCANS_LAST_SUCCESS_SHA_KEY),
        "last_purge_at": get_setting(db, SCANS_LAST_PURGE_AT_KEY),
        "age_hours": age_hours,
        "min_age_days": SCAN_ARCHIVE_MIN_AGE_DAYS,
        "purge_enabled": (os.getenv("SCAN_PURGE_ENABLED", "false").strip().lower()
                          in ("1", "true", "yes")),
        "error": error,
    }


@router.get("/scans/manifest", dependencies=[Depends(require_admin)])
def scans_manifest(db: Session = Depends(get_db),
                   before_days: int = 0,
                   limit: int = SCANS_DEFAULT_LIMIT):
    """What the archive WOULD contain, without transferring anything.

    `before_days=0` (the default) means every stored scan: the weekly archive wants a copy of
    everything promptly, not only of what is old enough to delete. The age floor is a
    delete-side control and lives in the purge path, not here."""
    limit = max(1, min(int(limit), SCANS_MAX_LIMIT))
    if not secure_id_store.storage_ready():
        raise HTTPException(status_code=503, detail="ID scan storage is not configured.")
    items = _collect_scans(db, max(0, int(before_days)), limit)
    return {
        "generated_at": datetime.utcnow().isoformat(),
        "backend": secure_id_store.storage_backend(),
        "count": len(items),
        "total_bytes": sum(i["bytes"] for i in items),
        "orphan_count": sum(1 for i in items if not i["in_use"]),
        "min_age_days": SCAN_ARCHIVE_MIN_AGE_DAYS,
        "items": [{
            "ref": i["ref"], "bytes": i["bytes"],
            "last_modified": _naive(i["last_modified"]).isoformat() if i["last_modified"] else None,
            "booking_id": i["booking_id"], "guest_id": i["guest_id"], "side": i["side"],
            "in_use": i["in_use"], "already_archived": i["already_archived"],
        } for i in items],
    }


@router.get("/scans/export")
def scans_export(request: Request, db: Session = Depends(get_db),
                 user=Depends(require_admin),
                 before_days: int = 0,
                 limit: int = SCANS_DEFAULT_LIMIT):
    """Stream a ZIP of the encrypted ID scans, plus a manifest naming every member.

    Spooled to a temp file for the same reason as /export: `X-Scans-Sha256` must precede the
    body, so it cannot be a hash of a stream already being sent.

    ZIP_STORED, not deflate — the members are Fernet ciphertext, which is incompressible, so
    compression would burn CPU for ~0%.

    Each member sits at its literal ref path, so THE PATH INSIDE THE ZIP IS THE REF. That is
    what lets an archived scan still be located years later from `booking_guests.id_scan_ref`.
    """
    _rate_limit_check(db, SCANS_LAST_EXPORT_AT_KEY, "scan archive")
    if not secure_id_store.storage_ready():
        raise HTTPException(status_code=503, detail="ID scan storage is not configured.")

    limit = max(1, min(int(limit), SCANS_MAX_LIMIT))
    started = datetime.utcnow()
    items = _collect_scans(db, max(0, int(before_days)), limit)
    projected = sum(i["bytes"] for i in items)
    if projected > SCANS_MAX_EXPORT_BYTES:
        raise HTTPException(
            status_code=413,
            detail=(f"That batch is {projected // (1024*1024)} MB, over the "
                    f"{SCANS_MAX_EXPORT_BYTES // (1024*1024)} MB limit. Lower `limit` and run again."))

    set_setting(db, SCANS_LAST_EXPORT_AT_KEY, started.isoformat(), user=user, commit=True)

    batch_id = uuid.uuid4().hex
    stamp = started.strftime("%Y-%m-%d_%H%M")
    filename = f"bhimas-scans-{stamp}.zip"
    tmp = tempfile.NamedTemporaryFile(prefix="bhimas-scans-", suffix=".zip", delete=False)
    tmp_path = tmp.name
    tmp.close()

    ip = _client_ip(request)
    try:
        members = []
        with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_STORED) as zf:
            for i in items:
                try:
                    blob = secure_id_store.read_ciphertext(i["ref"])
                except FileNotFoundError:
                    # Listed a moment ago, gone now. Skip it rather than fail the whole run.
                    logger.warning("scan vanished between listing and archive: %s", i["ref"])
                    continue
                zf.writestr(i["ref"], blob)
                members.append({
                    "ref": i["ref"], "bytes": len(blob),
                    "sha256": hashlib.sha256(blob).hexdigest(),
                    "booking_id": i["booking_id"], "guest_id": i["guest_id"],
                    "side": i["side"], "id_scan_mime": i["id_scan_mime"],
                    "in_use": i["in_use"],
                    "last_modified": (_naive(i["last_modified"]).isoformat()
                                      if i["last_modified"] else None),
                })
            manifest = {
                "generated_at": started.isoformat(),
                "batch_id": batch_id,
                "backend": secure_id_store.storage_backend(),
                "count": len(members),
                "total_bytes": sum(m["bytes"] for m in members),
                "encryption": "fernet",
                "note": ("Members are Fernet ciphertext. Decrypt with ID_SCAN_ENCRYPTION_KEY "
                         "from offline escrow — see backend/scripts/decrypt_scan.py. "
                         "The path of each member IS its id_scan_ref."),
                "items": members,
            }
            zf.writestr("scans-manifest.json", json.dumps(manifest, indent=2))

        size = os.path.getsize(tmp_path)
        if size == 0:
            raise HTTPException(status_code=500, detail="The scan archive came out empty.")

        sha = hashlib.sha256()
        with open(tmp_path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                sha.update(chunk)
        digest = sha.hexdigest()

        set_setting(db, SCANS_LAST_SUCCESS_AT_KEY, datetime.utcnow().isoformat(), user=user)
        set_setting(db, SCANS_LAST_SUCCESS_COUNT_KEY, str(len(members)), user=user)
        set_setting(db, SCANS_LAST_SUCCESS_SHA_KEY, digest, user=user, commit=True)

        write_audit(db, user, "backup.scans_export", "id_scans", None,
                    after={"batch_id": batch_id, "count": len(members), "bytes": size,
                           "sha256": digest},
                    ip=ip, client="web", commit=True)
        logger.info("🗄️ ID scan archive exported by %s from %s — %s scans, %s bytes",
                    (user or {}).get("sub"), ip, len(members), size)

        return FileResponse(
            tmp_path, media_type="application/zip", filename=filename,
            headers={
                # Deliberately distinct from X-Backup-*: no future refactor should be able to
                # verify a scan archive against a database dump's hash, or vice versa.
                "X-Scans-Sha256": digest,
                "X-Scans-Bytes": str(size),
                "X-Scans-Count": str(len(members)),
                "X-Scans-Batch-Id": batch_id,
                "X-Scans-Taken-At": started.isoformat(),
            },
            background=BackgroundTask(_cleanup, tmp_path),
        )
    except HTTPException:
        _cleanup(tmp_path)
        raise
    except Exception as e:
        _cleanup(tmp_path)
        logger.error("scan archive export failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="The scan archive export failed.")
