"""Apply every migration in backend/migrations to a target database, in order.

Replaces the `for f in $(ls *.sql | sort)` loop in the go-live runbook (B3/G2):
that loop is awkward on Windows and stops on the first file, which is how the
stray `psql ...` header in 001 went unnoticed for so long.

Every migration is idempotent, so running this against an already-migrated
database is a no-op -- that is what makes the Part H cutover safe.

IMPORTANT: the base tables come from SQLAlchemy's create_all() in the FastAPI
lifespan, not from migration 001. Let the app boot once against a new database
BEFORE running this, or the migrations fail on missing base tables.

Usage:
    python backend/scripts/run_migrations.py                 # uses backend/.env
    python backend/scripts/run_migrations.py "<DATABASE_URL>"
    python backend/scripts/run_migrations.py "<DATABASE_URL>" --dry-run
"""
import glob
import os
import sys

import psycopg2

MIGRATIONS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "migrations")


def resolve_url(argv):
    for a in argv:
        if not a.startswith("-"):
            return a
    from dotenv import load_dotenv
    backend = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    load_dotenv(os.path.join(backend, ".env"))
    url = os.getenv("DATABASE_URL")
    if url:
        return url
    return (f"postgresql://{os.getenv('DB_USER')}:{os.getenv('DB_PASSWORD')}"
            f"@{os.getenv('DB_HOST')}:{os.getenv('DB_PORT')}/{os.getenv('DB_NAME')}")


def main():
    url = resolve_url(sys.argv[1:])
    dry = "--dry-run" in sys.argv
    files = sorted(glob.glob(os.path.join(MIGRATIONS, "*.sql")))
    if not files:
        sys.exit(f"No .sql files found in {MIGRATIONS}")

    # Never print the URL: it carries the password.
    host = url.split("@")[-1] if "@" in url else url
    print(f"Target: ...@{host}")
    print(f"{len(files)} migrations{' (dry run -- rolling back each)' if dry else ''}\n")

    failed = []
    for path in files:
        name = os.path.basename(path)
        with open(path, encoding="utf-8") as fh:
            body = fh.read()
        conn = psycopg2.connect(url, connect_timeout=15)
        conn.autocommit = False
        try:
            conn.cursor().execute(body)
            if dry:
                conn.rollback()
            else:
                conn.commit()
            print(f"    OK    {name}")
        except Exception as exc:
            conn.rollback()
            msg = str(exc).strip().splitlines()[0]
            print(f"    FAIL  {name}\n            {msg}")
            failed.append(name)
        finally:
            conn.close()

    print()
    if failed:
        print(f"RESULT: {len(failed)} of {len(files)} FAILED -- {', '.join(failed)}")
        sys.exit(2)
    print(f"RESULT: all {len(files)} migrations applied cleanly")


if __name__ == "__main__":
    main()
