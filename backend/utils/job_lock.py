"""v7l: one-worker-at-a-time locks for the scheduled jobs.

uvicorn runs 4 workers and each starts every scheduler, so each job takes a Postgres SESSION-level
advisory lock. A session lock belongs to the CONNECTION, and every connection here comes from a
pool - which is where two jobs went wrong on production (14 Oct):

  - the overstay sweep took the lock on the ORM session. A commit inside the sweep hands that
    connection back to the pool; the unlock then ran on a different connection and released
    nothing. The lock stayed on an idle pooled connection, and from 13:07 every later sweep in
    the other workers reported "another instance holds the lock" - room 17 sat past check-out +
    grace with no overstay charge.
  - the Razorpay late-payment sweep (and the WhatsApp jobs) "closed" the lock connection without
    unlocking, on the belief that closing releases it. close() on a pooled connection only returns
    it to the pool - still holding the lock.

`job_lock` takes the lock on its own connection, unlocks on THAT connection, and if the unlock
fails it invalidates the connection (really closes it) so the lock cannot outlive the job.
"""
import logging
from contextlib import contextmanager

from sqlalchemy import text

logger = logging.getLogger(__name__)


@contextmanager
def job_lock(bind, key: int, name: str = "job"):
    """Yields True when this worker holds the lock (run the job), False when another does (skip).
    `bind` is an Engine or a Session (its bind is used). Never raises on the lock itself; a lock
    check that fails yields True, matching the jobs' old 'proceed without it' behaviour."""
    engine = bind.get_bind() if hasattr(bind, "get_bind") else bind
    conn = None
    got = False
    try:
        conn = engine.connect()
        got = bool(conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": key}).scalar())
        conn.commit()      # end the implicit transaction; the SESSION lock stays on this connection
    except Exception as e:
        logger.warning(f"{name}: advisory-lock check failed ({e}); proceeding without it")
        got = None
    try:
        yield got is not False
    finally:
        if conn is not None:
            released = not got          # nothing to release unless we took it
            if got:
                try:
                    released = bool(conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": key}).scalar())
                    conn.commit()
                except Exception as e:
                    logger.warning(f"{name}: advisory-unlock failed: {e}")
            try:
                if released:
                    conn.close()
                else:
                    conn.invalidate()   # the DB connection really closes - Postgres drops its locks
            except Exception:
                pass
