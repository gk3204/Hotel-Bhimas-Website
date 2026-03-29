"""
restore_db.py — Restore the database from backup.sql on first deploy.

Uses psycopg2 directly so that PostgreSQL COPY ... FROM stdin blocks
(which are psql-native and cannot be run through SQLAlchemy's execute())
are handled correctly via cursor.copy_expert().

The restore is idempotent: it checks whether the `room_types` table
already contains rows before doing anything, so re-deploys are safe.
"""

import os
import io
import logging
import re

logger = logging.getLogger(__name__)

# Locate backup.sql relative to this file's directory (/app in the container).
_BACKUP_PATH = os.path.join(os.path.dirname(__file__), "backup.sql")


def _get_raw_connection():
    """Return a raw psycopg2 connection using DATABASE_URL."""
    import psycopg2

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        db_user = os.getenv("DB_USER")
        db_password = os.getenv("DB_PASSWORD")
        db_host = os.getenv("DB_HOST")
        db_port = os.getenv("DB_PORT")
        db_name = os.getenv("DB_NAME")
        database_url = (
            f"postgresql://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"
        )

    # psycopg2 accepts the standard postgres:// URI directly.
    # Railway sometimes uses the postgres:// scheme; normalise it.
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)

    return psycopg2.connect(database_url)


def _tables_already_populated(conn) -> bool:
    """Return True if room_types already has rows (restore already done)."""
    with conn.cursor() as cur:
        try:
            cur.execute(
                "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = 'room_types')"
            )
            table_exists = cur.fetchone()[0]
            if not table_exists:
                return False
            cur.execute("SELECT COUNT(*) FROM public.room_types")
            count = cur.fetchone()[0]
            return count > 0
        except Exception:
            return False


def _parse_sql_segments(sql_text: str):
    """
    Split a pg_dump SQL file into a list of segments, each being either:
      ("sql",  <statement string>)   — a regular SQL statement
      ("copy", <copy header>, <data>) — a COPY ... FROM stdin block

    The COPY header is the full "COPY ... FROM stdin;" line.
    The data is the tab-delimited payload (everything between the header
    and the terminating "\\." line), as a string ready for copy_expert().
    """
    segments = []
    lines = sql_text.splitlines(keepends=True)

    i = 0
    current_sql_lines = []

    while i < len(lines):
        line = lines[i]

        # Detect start of a COPY ... FROM stdin block.
        if re.match(r"^\s*COPY\s+\S.*FROM\s+stdin", line, re.IGNORECASE):
            # Flush any accumulated SQL first.
            if current_sql_lines:
                stmt = "".join(current_sql_lines).strip()
                if stmt:
                    segments.append(("sql", stmt))
                current_sql_lines = []

            copy_header = line.rstrip("\n").rstrip("\r")
            data_lines = []
            i += 1
            while i < len(lines):
                data_line = lines[i]
                if data_line.rstrip("\n").rstrip("\r") == "\\.":
                    i += 1
                    break
                data_lines.append(data_line)
                i += 1

            segments.append(("copy", copy_header, "".join(data_lines)))
            continue

        # Skip comment-only lines and blank lines at the top level
        # (they don't need to be executed).
        stripped = line.strip()
        if stripped.startswith("--") or stripped == "":
            i += 1
            continue

        # Accumulate regular SQL lines until we hit a semicolon that ends
        # a statement.  pg_dump statements are always terminated by ";\n".
        current_sql_lines.append(line)
        if ";" in line:
            stmt = "".join(current_sql_lines).strip()
            if stmt:
                segments.append(("sql", stmt))
            current_sql_lines = []

        i += 1

    # Flush any trailing SQL.
    if current_sql_lines:
        stmt = "".join(current_sql_lines).strip()
        if stmt:
            segments.append(("sql", stmt))

    return segments


def restore_from_backup() -> None:
    """
    Main entry point.  Called once at application startup.

    Reads backup.sql, connects to the database, and restores the schema
    and data — but only if the database is empty (idempotent).
    """
    if not os.path.exists(_BACKUP_PATH):
        logger.warning(
            "restore_db: backup.sql not found at %s — skipping restore.",
            _BACKUP_PATH,
        )
        return

    conn = None
    try:
        conn = _get_raw_connection()
        conn.autocommit = False

        if _tables_already_populated(conn):
            logger.info(
                "restore_db: room_types table already has data — skipping restore."
            )
            return

        logger.info("restore_db: Starting database restore from backup.sql …")

        with open(_BACKUP_PATH, "r", encoding="utf-8-sig") as f:
            sql_text = f.read()

        segments = _parse_sql_segments(sql_text)

        with conn.cursor() as cur:
            for segment in segments:
                kind = segment[0]

                if kind == "sql":
                    stmt = segment[1]

                    # Skip statements that are only meaningful for the original
                    # dump owner (postgres superuser) and would fail on Railway.
                    if re.search(
                        r"\bOWNER\s+TO\b|\bGRANT\b|\bREVOKE\b",
                        stmt,
                        re.IGNORECASE,
                    ):
                        logger.debug("restore_db: skipping owner/grant stmt: %s", stmt[:80])
                        continue

                    try:
                        cur.execute(stmt)
                    except Exception as exc:
                        # Non-fatal: log and continue so one bad statement
                        # doesn't abort the whole restore.
                        logger.warning(
                            "restore_db: statement failed (continuing): %s — %s",
                            stmt[:120],
                            exc,
                        )
                        conn.rollback()

                elif kind == "copy":
                    copy_header = segment[1]
                    data = segment[2]

                    try:
                        cur.copy_expert(copy_header, io.StringIO(data))
                    except Exception as exc:
                        logger.warning(
                            "restore_db: COPY failed (continuing): %s — %s",
                            copy_header[:120],
                            exc,
                        )
                        conn.rollback()

        conn.commit()
        logger.info("restore_db: Database restore completed successfully.")

    except Exception as exc:
        logger.error("restore_db: Restore failed: %s", exc, exc_info=True)
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass
