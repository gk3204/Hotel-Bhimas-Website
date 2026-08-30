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
import logging
import os
import shutil
import subprocess
import tempfile
from datetime import datetime, timedelta
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy import func, text
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from database import SessionLocal
from models import (Booking, Folio, FolioCharge, Guest, Invoice, Payment, Room,
                    User)
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


def _rate_limit_check(db: Session):
    """One export per hour. Reads persisted state so all four workers share the limit."""
    last = _parse_dt(get_setting(db, LAST_EXPORT_AT_KEY))
    if last is None:
        return
    waited = datetime.utcnow() - last
    if waited < MIN_INTERVAL:
        mins = int((MIN_INTERVAL - waited).total_seconds() // 60) + 1
        raise HTTPException(
            status_code=429,
            detail=f"A backup was taken less than an hour ago. Try again in {mins} minute(s).")


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
