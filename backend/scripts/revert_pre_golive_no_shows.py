"""One-off (v5n): put pre-go-live bookings that the first automatic no-show sweep marked back to
`confirmed`.

Why this exists
---------------
v5m added the automatic no-show sweep but no start date, so its first run reached back through the
property's entire booking history. On 2026-09-22 at 03:00 it marked 51 website bookings (check-outs
from 2026-05-10 to 2026-09-21) as `no_show` and stamped every one of them with that morning's
timestamp — which is what the arrival-exceptions report and the owner's daily digest then showed as
"51 no-shows today". None of those stays belong to the live operation.

v5n fixes the sweep with a `no_show_from_date` cutoff (Settings → Front desk). This script cleans up
what the un-cutoff run already did: any booking still sitting in `no_show` whose check-out date is
BEFORE the cutoff goes back to `confirmed`, with `no_show_at` cleared and one audit row each. Their
dates are in the past, so they hold no inventory and cannot affect a future availability check.

Dry-run by default — nothing is written until you pass --apply.

    python backend/scripts/revert_pre_golive_no_shows.py                      # local .env DB, dry run
    python backend/scripts/revert_pre_golive_no_shows.py "<DATABASE_URL>"     # dry run
    python backend/scripts/revert_pre_golive_no_shows.py "<DATABASE_URL>" --apply
    python backend/scripts/revert_pre_golive_no_shows.py --cutoff 2026-09-22 --apply

With no --cutoff the script reads `no_show_from_date` from the database (seeded by migration 045).
Re-running is safe: once reverted, a booking is `confirmed` and no longer matches.
"""
import os
import sys
from datetime import datetime


def _resolve_url(argv):
    for a in argv[1:]:
        if not a.startswith("--"):
            return a
    from dotenv import load_dotenv
    backend = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    load_dotenv(os.path.join(backend, ".env"))
    url = os.getenv("DATABASE_URL")
    if url:
        return url
    return (f"postgresql://{os.getenv('DB_USER')}:{os.getenv('DB_PASSWORD')}"
            f"@{os.getenv('DB_HOST')}:{os.getenv('DB_PORT')}/{os.getenv('DB_NAME')}")


def _arg(argv, name):
    if name in argv:
        i = argv.index(name)
        if i + 1 < len(argv):
            return argv[i + 1]
    return None


def main(argv):
    apply = "--apply" in argv
    url = _resolve_url([a for a in argv if a != _arg(argv, "--cutoff")])
    cutoff_arg = _arg(argv, "--cutoff")

    import psycopg2
    conn = psycopg2.connect(url)
    conn.autocommit = False
    cur = conn.cursor()

    cutoff = cutoff_arg
    if not cutoff:
        cur.execute("SELECT value FROM app_settings WHERE key = 'no_show_from_date'")
        row = cur.fetchone()
        cutoff = (row[0] or "").strip() if row else ""
    if not cutoff:
        print("No cutoff: pass --cutoff YYYY-MM-DD, or run migration 045 so no_show_from_date is set.")
        return 2
    try:
        cutoff_date = datetime.strptime(cutoff[:10], "%Y-%m-%d").date()
    except ValueError:
        print(f"--cutoff must be YYYY-MM-DD (got {cutoff!r})")
        return 2

    cur.execute("""SELECT booking_id, booking_source, check_in, check_out, no_show_at,
                          COALESCE(prepaid_amount, 0)
                     FROM bookings
                    WHERE status = 'no_show' AND check_out < %s
                    ORDER BY check_out""", (cutoff_date,))
    rows = cur.fetchall()
    print(f"Cutoff {cutoff_date} — {len(rows)} pre-go-live no-show(s) to revert to 'confirmed'"
          f"{'' if apply else '  (DRY RUN — nothing written)'}")
    for bid, src, ci, co, at, prepaid in rows[:60]:
        print(f"  #{bid:<6} {src or '-':<12} {ci} → {co}   marked {at}   prepaid {prepaid}")
    if len(rows) > 60:
        print(f"  … and {len(rows) - 60} more")
    if not rows:
        conn.close()
        return 0
    if not apply:
        print("\nRe-run with --apply to write these changes.")
        conn.close()
        return 0

    # The audit trail matters more than the row change: the earlier no_show rows are already audited,
    # so the reversal needs its own entry naming why. user_id NULL = a system action, same as the
    # night audit's own rows.
    ids = [r[0] for r in rows]
    cur.executemany(
        """INSERT INTO audit_logs (user_id, action, entity_type, entity_id, before, after, client, created_at)
           VALUES (NULL, 'booking.no_show_reverted', 'booking', %s, %s, %s, 'script', now())""",
        [(str(bid),
          '{"status": "no_show", "no_show_at": "%s"}' % (at.isoformat() if at else ""),
          '{"status": "confirmed", "reason": "pre-go-live cleanup (v5n): check-out %s is before the '
          'no-show cutoff %s"}' % (co, cutoff_date))
         for bid, _src, _ci, co, at, _pp in rows])
    cur.execute("""UPDATE bookings SET status = 'confirmed', no_show_at = NULL
                    WHERE booking_id = ANY(%s)""", (ids,))
    conn.commit()
    print(f"\nReverted {cur.rowcount} booking(s) to 'confirmed' and wrote {len(ids)} audit row(s).")
    cur.execute("SELECT status, COUNT(*) FROM bookings GROUP BY status ORDER BY 2 DESC")
    print("Booking status now:", cur.fetchall())
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
