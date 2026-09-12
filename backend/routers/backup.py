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
import re
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
from pydantic import BaseModel, Field
from sqlalchemy import func, text
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from database import SessionLocal
from models import (Booking, BookingGuest, BackupRun, Folio, FolioCharge, Guest,
                    Invoice, Payment, Room, User)
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

# Custody: written ONLY by the client, and only once a verified copy exists on its disk.
OFFSITE_AT_KEY = "backup_offsite_last_at"
OFFSITE_MACHINE_KEY = "backup_offsite_machine"
OFFSITE_FOLDER_KEY = "backup_offsite_folder"
OFFSITE_FILENAME_KEY = "backup_offsite_filename"
OFFSITE_BYTES_KEY = "backup_offsite_bytes"
OFFSITE_SHA_KEY = "backup_offsite_sha256"
OFFSITE_OUTCOME_KEY = "backup_offsite_outcome"

LAST_FAILURE_AT_KEY = "backup_last_failure_at"
LAST_FAILURE_REASON_KEY = "backup_last_failure_reason"

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


def _release_rate_limit_slot(db: Session, key: str, user=None) -> None:
    """Hand the once-an-hour slot back after work that FAILED.

    The slot is deliberately claimed before the work starts, so two simultaneous requests
    cannot both run pg_dump. The cost was that a transient failure then locked out retries
    for an hour. Releasing it on failure keeps the concurrency guard and drops the lockout.

    Writes "" rather than deleting the row: `_parse_dt("")` is None, and none of these keys
    are in settings._DEFAULTS, so an empty value reads exactly like "never" — and there is
    no delete_setting helper to add.

    NOTE: do NOT be tempted to replace the app_settings claim with a Postgres advisory lock.
    The dump runs for minutes across a commit, so it would have to be a SESSION-scoped lock —
    precisely the pooled-connection stranding that already broke the purge path once (see the
    comment above `_PURGE_LOCK_KEY`). A settings row survives connection churn; a session lock
    does not.
    """
    try:
        # The failure may have left the session in a broken transaction, in which case the
        # release write would itself throw. Roll back first.
        db.rollback()
        set_setting(db, key, "", user=user, commit=True)
    except Exception:
        logger.error("could not release the %s rate-limit slot", key, exc_info=True)


def _record_failure(db: Session, at_key: str, reason_key: str, reason: str, user=None) -> None:
    """Leave a breadcrumb the web panel can show. Without this a failed export is visible
    only in the Railway logs, which the owner has no reason to be reading."""
    try:
        set_setting(db, at_key, datetime.utcnow().isoformat(), user=user)
        set_setting(db, reason_key, (reason or "")[:300], user=user, commit=True)
    except Exception:
        logger.error("could not record the %s failure breadcrumb", at_key, exc_info=True)


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


def _offsite_block(db: Session, keys: dict, stale_hours: int) -> dict:
    """Where the last VERIFIED copy of this artifact actually lives.

    Reported by the client after it renamed a checksum-verified file into place — the only
    party that can honestly assert a copy exists off the server. `age_hours` is measured from
    the SERVER's receipt time, never the client's clock, so a wrong clock on the admin PC
    cannot make this read healthy (or falsely stale)."""
    at = _parse_dt(get_setting(db, keys["at"]))
    age_hours = None
    if at:
        age_hours = round((datetime.utcnow() - at).total_seconds() / 3600, 1)
    block = {
        "reported": at is not None,
        "last_at": at.isoformat() if at else None,
        "machine": get_setting(db, keys["machine"]),
        "folder": get_setting(db, keys["folder"]),
        "filename": get_setting(db, keys["filename"]),
        "bytes": int(get_setting(db, keys["bytes"]) or 0) or None,
        "sha256": get_setting(db, keys["sha"]),
        "outcome": get_setting(db, keys["outcome"]),
        "age_hours": age_hours,
        "stale": at is None or age_hours is None or age_hours > stale_hours,
        "stale_hours": stale_hours,
    }
    if "count" in keys:
        block["count"] = int(get_setting(db, keys["count"]) or 0) or None
    return block


_DB_OFFSITE_KEYS = {"at": OFFSITE_AT_KEY, "machine": OFFSITE_MACHINE_KEY,
                    "folder": OFFSITE_FOLDER_KEY, "filename": OFFSITE_FILENAME_KEY,
                    "bytes": OFFSITE_BYTES_KEY, "sha": OFFSITE_SHA_KEY,
                    "outcome": OFFSITE_OUTCOME_KEY}


@router.get("/status", dependencies=[Depends(require_admin)])
def backup_status(db: Session = Depends(get_db)):
    """Is the nightly backup still running? The failure mode this exists to prevent is a job
    that quietly stopped weeks ago and is discovered at restore time.

    Two different facts live here, and conflating them is how a broken backup reads green:
      * `last_success_*` — the SERVER produced a dump and began streaming it.
      * `offsite` — a verified copy exists on a named machine, at a named path.
    Health follows `offsite` whenever the client has ever reported. Until then it falls back
    to the server-side view, so an up-to-date backend with an old CLI is not permanently red."""
    last_success = _parse_dt(get_setting(db, LAST_SUCCESS_AT_KEY))
    age_hours = None
    if last_success:
        age_hours = round((datetime.utcnow() - last_success).total_seconds() / 3600, 1)

    offsite = _offsite_block(db, _DB_OFFSITE_KEYS, 36)
    # Informational only, never an alarm: the server legitimately produces a newer dump than
    # the one on the admin PC the moment anyone presses "Download backup now".
    sha_matches = None
    if offsite["sha256"] and get_setting(db, LAST_SUCCESS_SHA_KEY):
        sha_matches = offsite["sha256"] == get_setting(db, LAST_SUCCESS_SHA_KEY)

    server_stale = last_success is None or age_hours is None or age_hours > 36
    return {
        "last_success_at": last_success.isoformat() if last_success else None,
        "last_success_by": get_setting(db, LAST_SUCCESS_BY_KEY),
        "last_success_bytes": int(get_setting(db, LAST_SUCCESS_BYTES_KEY) or 0) or None,
        "last_success_sha256": get_setting(db, LAST_SUCCESS_SHA_KEY),
        "age_hours": age_hours,
        # A nightly job that has not run for over 36 hours has missed at least one night.
        "stale": offsite["stale"] if offsite["reported"] else server_stale,
        "server_stale": server_stale,
        "pg_dump_available": shutil.which("pg_dump") is not None,
        "offsite": {**offsite, "sha_matches_server": sha_matches},
        "last_failure_at": get_setting(db, LAST_FAILURE_AT_KEY),
        "last_failure_reason": get_setting(db, LAST_FAILURE_REASON_KEY),
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
                    ip=ip, client=_client_kind(request), commit=True)
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
        # Released on timeout too: the CLI retries once a night and the web button is a human,
        # so an hour's lockout after a transient stall is the complaint this fixes.
        _release_rate_limit_slot(db, LAST_EXPORT_AT_KEY, user)
        _record_failure(db, LAST_FAILURE_AT_KEY, LAST_FAILURE_REASON_KEY,
                        f"pg_dump timed out after {DUMP_TIMEOUT_SECONDS}s", user)
        _cleanup(tmp_path)
        logger.error("pg_dump timed out after %ss", DUMP_TIMEOUT_SECONDS)
        raise HTTPException(status_code=504, detail="The database dump timed out.")
    except HTTPException as e:
        _release_rate_limit_slot(db, LAST_EXPORT_AT_KEY, user)
        _record_failure(db, LAST_FAILURE_AT_KEY, LAST_FAILURE_REASON_KEY,
                        getattr(e, "detail", "The database dump failed."), user)
        _cleanup(tmp_path)
        raise
    except Exception as e:
        _release_rate_limit_slot(db, LAST_EXPORT_AT_KEY, user)
        _record_failure(db, LAST_FAILURE_AT_KEY, LAST_FAILURE_REASON_KEY, str(e), user)
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
SCANS_OFFSITE_AT_KEY = "scans_offsite_last_at"
SCANS_OFFSITE_MACHINE_KEY = "scans_offsite_machine"
SCANS_OFFSITE_FOLDER_KEY = "scans_offsite_folder"
SCANS_OFFSITE_FILENAME_KEY = "scans_offsite_filename"
SCANS_OFFSITE_BYTES_KEY = "scans_offsite_bytes"
SCANS_OFFSITE_SHA_KEY = "scans_offsite_sha256"
SCANS_OFFSITE_COUNT_KEY = "scans_offsite_count"
SCANS_OFFSITE_OUTCOME_KEY = "scans_offsite_outcome"

# The scan archive runs WEEKLY, so the dump's 36-hour rule would flag a healthy job every time.
SCANS_STALE_HOURS = 192   # 8 days

SCANS_LAST_FAILURE_AT_KEY = "scans_last_failure_at"
SCANS_LAST_FAILURE_REASON_KEY = "scans_last_failure_reason"

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


_SCANS_OFFSITE_KEYS = {"at": SCANS_OFFSITE_AT_KEY, "machine": SCANS_OFFSITE_MACHINE_KEY,
                       "folder": SCANS_OFFSITE_FOLDER_KEY, "filename": SCANS_OFFSITE_FILENAME_KEY,
                       "bytes": SCANS_OFFSITE_BYTES_KEY, "sha": SCANS_OFFSITE_SHA_KEY,
                       "outcome": SCANS_OFFSITE_OUTCOME_KEY, "count": SCANS_OFFSITE_COUNT_KEY}


def _client_kind(request: Request | None) -> str:
    """Browser or nightly job? Both hold an admin JWT, so only the User-Agent separates them.

    `client="web"` used to be hard-coded on these audit rows, which quietly mislabelled every
    unattended run as a person clicking a button."""
    ua = (request.headers.get("user-agent") or "") if request is not None else ""
    return "cli" if ua.startswith("BhimasBackup/") else "web"


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
    offsite = _offsite_block(db, _SCANS_OFFSITE_KEYS, SCANS_STALE_HOURS)
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
        # Weekly job, so 8 days — not the dump's 36 hours.
        "stale": offsite["stale"] if offsite["reported"] else (
            last_ok is None or age_hours is None or age_hours > SCANS_STALE_HOURS),
        "offsite": offsite,
        "last_failure_at": get_setting(db, SCANS_LAST_FAILURE_AT_KEY),
        "last_failure_reason": get_setting(db, SCANS_LAST_FAILURE_REASON_KEY),
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
                    ip=ip, client=_client_kind(request), commit=True)
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
    except HTTPException as e:
        _release_rate_limit_slot(db, SCANS_LAST_EXPORT_AT_KEY, user)
        _record_failure(db, SCANS_LAST_FAILURE_AT_KEY, SCANS_LAST_FAILURE_REASON_KEY,
                        getattr(e, "detail", "The scan archive export failed."), user)
        _cleanup(tmp_path)
        raise
    except Exception as e:
        _release_rate_limit_slot(db, SCANS_LAST_EXPORT_AT_KEY, user)
        _record_failure(db, SCANS_LAST_FAILURE_AT_KEY, SCANS_LAST_FAILURE_REASON_KEY, str(e), user)
        _cleanup(tmp_path)
        logger.error("scan archive export failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="The scan archive export failed.")


# -----------------------------------------------------------------------------------------
# Purge — the only irreversible operation in this file.
#
# Deleting a scan from object storage is permanent, and afterwards the ONLY copy that exists
# is the zip on the owner's PC. So every check here is a refusal, not a repair: a ref that
# does not pass is skipped and reported, never deleted "to be safe".
#
# The order matters. The AGE GATE is enforced server-side from the object's own last_modified
# and is NOT overridable by the request, so neither a client bug nor a hostile caller can wipe
# this week's check-ins. The CHECKSUM gate compares what the client verified against what is
# actually stored right now: a mismatch means the client verified something else, or the object
# changed since the archive, and either way deleting would destroy the unverified copy.
# -----------------------------------------------------------------------------------------

# Distinct from overstay's 0x00570A11 and OTA's 0x00074A11.
_PURGE_LOCK_KEY = 0x005CA451

# Stop-and-ask thresholds. A purge facing an unexpectedly large batch should behave like the
# CLI pruner does when it cannot read a date: do nothing, and make a human look.
PURGE_MAX_REFS = 1000
PURGE_BLAST_RADIUS_REFS = 200
PURGE_BLAST_RADIUS_FRACTION = 0.5
# The fraction test only makes sense once a batch is big enough for "most of the bucket" to be a
# meaningful statement. Without this floor, a hotel holding 12 scans trips the 50% rule on a
# 7-scan purge every single week — and a guard that fires on every correct run teaches whoever
# runs it to pass force=true reflexively, which is worse than having no guard at all.
PURGE_BLAST_RADIUS_MIN_REFS_FOR_FRACTION = 25


def _purge_enabled() -> bool:
    return os.getenv("SCAN_PURGE_ENABLED", "false").strip().lower() in ("1", "true", "yes")


class PurgeRef(BaseModel):
    ref: str
    sha256: str


class PurgeRequest(BaseModel):
    batch_id: str
    confirm: str
    refs: list[PurgeRef]
    # Only ever set by a human who has read the 409 and decided the batch is genuinely correct.
    force: bool = False


@router.post("/scans/purge")
def scans_purge(payload: PurgeRequest, request: Request, db: Session = Depends(get_db),
                user=Depends(require_admin)):
    """Delete archived scans from object storage, after proving each one is safely archived.

    Ships DISABLED (`SCAN_PURGE_ENABLED=false`), matching how every other destructive automation
    in this codebase ships — `overstay_auto_charge_enabled` is FALSE out of the box for the same
    reason. Turning it on is a decision, not a default."""
    if not _purge_enabled():
        raise HTTPException(
            status_code=503,
            detail="Scan purging is disabled. Set SCAN_PURGE_ENABLED=true to arm it.")
    if payload.confirm != "DELETE":
        raise HTTPException(status_code=400, detail='Refusing to purge without confirm="DELETE".')
    if not payload.refs:
        raise HTTPException(status_code=400, detail="No refs supplied.")
    if len(payload.refs) > PURGE_MAX_REFS:
        raise HTTPException(status_code=400,
                            detail=f"Too many refs in one request (max {PURGE_MAX_REFS}).")
    if not secure_id_store.storage_ready():
        raise HTTPException(status_code=503, detail="ID scan storage is not configured.")

    # Blast radius. Checked BEFORE the advisory lock so a refusal costs nothing.
    if not payload.force:
        try:
            online = len(secure_id_store.list_scans())
        except Exception:
            online = 0
        too_many = len(payload.refs) > PURGE_BLAST_RADIUS_REFS
        too_much = (online
                    and len(payload.refs) >= PURGE_BLAST_RADIUS_MIN_REFS_FOR_FRACTION
                    and (len(payload.refs) / online) > PURGE_BLAST_RADIUS_FRACTION)
        if too_many or too_much:
            logger.error("purge refused on blast radius: %s refs of %s online",
                         len(payload.refs), online)
            raise HTTPException(
                status_code=409,
                detail=(f"That would delete {len(payload.refs)} of {online} stored scans. "
                        f"If that is really intended, resend with force=true."))

    ip = _client_ip(request)
    cutoff = datetime.utcnow() - timedelta(days=SCAN_ARCHIVE_MIN_AGE_DAYS)
    results, deleted_refs = [], []
    skipped = {}

    # One purge at a time across workers, so two runs cannot double-stamp the same rows.
    #
    # TRANSACTION-scoped (`_xact_`), not session-scoped, and that distinction is the whole
    # point: a session-level pg_try_advisory_lock is tied to the CONNECTION, and SQLAlchemy
    # hands the connection back to the pool on commit. The unlock then runs on a different
    # connection, releases nothing, and the lock is stranded on an idle pooled connection --
    # so every subsequent purge 409s until the process restarts. That is exactly what happened
    # the first time this ran. A transaction lock is released by Postgres itself on COMMIT or
    # ROLLBACK, so it cannot leak however the request ends.
    got_lock = db.execute(text("SELECT pg_try_advisory_xact_lock(:k)"),
                          {"k": _PURGE_LOCK_KEY}).scalar()
    if not got_lock:
        raise HTTPException(status_code=409, detail="Another purge is already running.")

    try:
        owners = _scan_ref_owners(db, {r.ref for r in payload.refs})
        for item in payload.refs:
            ref = item.ref

            def skip(reason):
                skipped[reason] = skipped.get(reason, 0) + 1
                results.append({"ref": ref, "status": "skipped", "reason": reason})

            if not secure_id_store.valid_ref(ref):
                skip("invalid")
                continue

            info = secure_id_store.stat_scan(ref)
            if info is None:
                # Already gone. Still stamp the row, so a retried run converges instead of
                # leaving a scan that is deleted but not marked archived.
                _stamp_archived(db, owners.get(ref))
                results.append({"ref": ref, "status": "already_gone"})
                continue

            last_mod = _naive(info.get("last_modified")) if info.get("last_modified") else None
            if last_mod is None or last_mod > cutoff:
                skip("too_recent")
                continue

            try:
                actual = hashlib.sha256(secure_id_store.read_ciphertext(ref)).hexdigest()
            except FileNotFoundError:
                _stamp_archived(db, owners.get(ref))
                results.append({"ref": ref, "status": "already_gone"})
                continue
            if actual.lower() != (item.sha256 or "").lower():
                # The archived copy is not what is stored. Deleting now would destroy the only
                # version nobody has verified.
                logger.error("purge checksum mismatch for %s", ref)
                skip("mismatch")
                continue

            secure_id_store.delete_scan(ref)
            _stamp_archived(db, owners.get(ref))
            deleted_refs.append(ref)
            results.append({"ref": ref, "status": "deleted"})

        # Commit ends the transaction, which is also what releases the advisory lock above.
        db.commit()
    except Exception:
        db.rollback()          # releases the lock too — nothing to unlock by hand
        raise

    if deleted_refs:
        set_setting(db, SCANS_LAST_PURGE_AT_KEY, datetime.utcnow().isoformat(),
                    user=user, commit=True)

    # The full list is the record of what left the system. ~30 KB at 500 refs — worth it.
    write_audit(db, user, "backup.scans_purge", "id_scans", None,
                after={"batch_id": payload.batch_id, "requested": len(payload.refs),
                       "deleted": len(deleted_refs), "skipped": skipped,
                       "forced": payload.force, "refs_deleted": deleted_refs},
                ip=ip, client=_client_kind(request), commit=True)
    logger.warning("🗑️ purge by %s from %s — %s deleted, %s skipped (batch %s)",
                   (user or {}).get("sub"), ip, len(deleted_refs), sum(skipped.values()),
                   payload.batch_id)

    return {"batch_id": payload.batch_id, "requested": len(payload.refs),
            "deleted": len(deleted_refs), "skipped": skipped,
            "min_age_days": SCAN_ARCHIVE_MIN_AGE_DAYS, "results": results}


def _stamp_archived(db: Session, owner: dict | None) -> None:
    """Record that this side's scan is now offline. id_scan_ref is deliberately left in place:
    it is the pointer into the archive zip, and nulling it would make the file unfindable."""
    if not owner:
        return                                    # an orphan: nothing references it
    g = db.query(BookingGuest).filter(BookingGuest.id == owner["guest_id"]).first()
    if g is None:
        return
    if owner["side"] == "back":
        g.id_scan_back_archived_at = datetime.utcnow()
    else:
        g.id_scan_archived_at = datetime.utcnow()


# =========================================================================================
# Custody reporting
#
# The server can say "I produced a dump". Only the machine that renamed a checksum-verified
# file into place can say "a copy exists, here, on me". Those are different facts, and the
# gap between them is a real failure mode: a download that dies mid-transfer and is discarded
# still leaves the server's own timestamp looking healthy.
#
# So the client reports back, and the panel's health follows THIS, not the server's view.
# =========================================================================================

_SHA_RE = re.compile(r"^[0-9a-f]{64}$")

_REPORT_KEYS = {
    "database": {**_DB_OFFSITE_KEYS,
                 "fail_at": LAST_FAILURE_AT_KEY, "fail_reason": LAST_FAILURE_REASON_KEY},
    "scans": {**_SCANS_OFFSITE_KEYS,
              "fail_at": SCANS_LAST_FAILURE_AT_KEY, "fail_reason": SCANS_LAST_FAILURE_REASON_KEY},
}


class BackupReportIn(BaseModel):
    run_id: str = Field(max_length=40)
    kind: str                                   # database | scans
    outcome: str                                # ok | failed
    machine: str = Field(default="", max_length=100)
    folder: str = Field(default="", max_length=400)
    filename: str | None = Field(default=None, max_length=200)
    bytes: int | None = None
    sha256: str | None = Field(default=None, max_length=64)
    count: int | None = None                    # scans only
    started_at: str | None = Field(default=None, max_length=40)
    finished_at: str | None = Field(default=None, max_length=40)
    tool_version: str | None = Field(default=None, max_length=40)
    detail: str | None = Field(default=None, max_length=500)


@router.post("/report")
def backup_report(payload: BackupReportIn, request: Request,
                  db: Session = Depends(get_db), user=Depends(require_admin)):
    """The client confirming what it actually holds.

    ⚠️ `require_admin` is not optional here and the router carries no default gate: an open
    endpoint would let anyone forge a green backup status, which is worse than having no
    status at all."""
    kind = (payload.kind or "").strip().lower()
    outcome = (payload.outcome or "").strip().lower()
    if kind not in _REPORT_KEYS:
        raise HTTPException(status_code=422, detail='kind must be "database" or "scans".')
    if outcome not in ("ok", "failed"):
        raise HTTPException(status_code=422, detail='outcome must be "ok" or "failed".')

    sha = (payload.sha256 or "").strip().lower() or None
    if outcome == "ok":
        # A success claim without a real checksum and a real size is not evidence of custody.
        if not sha or not _SHA_RE.match(sha):
            raise HTTPException(status_code=422, detail="A successful report needs a sha256.")
        if not payload.bytes or payload.bytes <= 0:
            raise HTTPException(status_code=422, detail="A successful report needs bytes > 0.")

    keys = _REPORT_KEYS[kind]
    # Server receipt time is authoritative. The client's own clock is recorded separately and
    # only ever displayed, so a wrong clock on the admin PC cannot fake freshness.
    now = datetime.utcnow()
    ip = _client_ip(request)

    if outcome == "ok":
        set_setting(db, keys["at"], now.isoformat(), user=user)
        set_setting(db, keys["machine"], payload.machine or "", user=user)
        set_setting(db, keys["folder"], payload.folder or "", user=user)
        set_setting(db, keys["filename"], payload.filename or "", user=user)
        set_setting(db, keys["bytes"], str(payload.bytes or 0), user=user)
        set_setting(db, keys["sha"], sha or "", user=user)
        if "count" in keys:
            set_setting(db, keys["count"], str(payload.count or 0), user=user)
        set_setting(db, keys["outcome"], "ok", user=user, commit=True)
    else:
        # ⚠️ A failed report must NEVER touch the offsite keys. If it did, a run that failed
        # would refresh "last verified copy" and read as recent. The truth we want on screen is
        # "last good copy: Tuesday on OWNER-PC — and the last attempt failed 20 minutes ago",
        # which needs both records to survive independently of each other.
        _record_failure(db, keys["fail_at"], keys["fail_reason"],
                        payload.detail or "The client reported a failure.", user)

    # History. (run_id, kind) is unique, so a retried POST updates rather than duplicating.
    row = (db.query(BackupRun)
           .filter(BackupRun.run_id == payload.run_id, BackupRun.kind == kind).first())
    duplicate = row is not None
    if row is None:
        row = BackupRun(run_id=payload.run_id, kind=kind)
        db.add(row)
    row.outcome = outcome
    row.machine = payload.machine or None
    row.folder = payload.folder or None
    row.filename = payload.filename
    row.size_bytes = payload.bytes
    row.sha256 = sha
    row.item_count = payload.count
    row.client_local_time = payload.finished_at or payload.started_at
    row.started_at = _parse_dt((payload.started_at or "").replace("Z", ""))
    row.finished_at = now
    row.detail = (payload.detail or None)
    row.tool_version = payload.tool_version
    row.ip = ip
    db.commit()

    write_audit(db, user, "backup.report", "backup_run", row.id,
                after={"kind": kind, "outcome": outcome, "machine": payload.machine,
                       "folder": payload.folder, "filename": payload.filename,
                       "bytes": payload.bytes, "sha256": sha},
                ip=ip, client=_client_kind(request), commit=True)
    logger.info("📥 backup report: %s %s from %s (%s)", kind, outcome, payload.machine, ip)
    return {"recorded": True, "duplicate": duplicate, "kind": kind, "outcome": outcome}


@router.get("/runs", dependencies=[Depends(require_admin)])
def backup_runs(db: Session = Depends(get_db), kind: str | None = None, limit: int = 20):
    """Recent confirmed artifacts, newest first — the when / where / which-machine history."""
    limit = max(1, min(int(limit), 100))
    q = db.query(BackupRun)
    if kind in _REPORT_KEYS:
        q = q.filter(BackupRun.kind == kind)
    rows = q.order_by(BackupRun.finished_at.desc().nullslast(), BackupRun.id.desc()).limit(limit).all()
    return {"count": len(rows), "runs": [{
        "id": r.id, "run_id": r.run_id, "kind": r.kind, "outcome": r.outcome,
        "machine": r.machine, "folder": r.folder, "filename": r.filename,
        "bytes": r.size_bytes, "sha256": r.sha256, "count": r.item_count,
        "client_local_time": r.client_local_time,
        "finished_at": r.finished_at.isoformat() if r.finished_at else None,
        "detail": r.detail, "tool_version": r.tool_version,
    } for r in rows]}
