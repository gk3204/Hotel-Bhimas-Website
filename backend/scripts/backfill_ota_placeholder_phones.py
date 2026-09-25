"""Normalise legacy OTA placeholder phone numbers (F-05).

An OTA voucher masks the guest's phone, so confirming a draft stamps a stand-in built from the
booking reference. The ORIGINAL form was the last ten digits of that reference — and any reference
ending 6-9 therefore produced a **valid Indian mobile belonging to a real person**. Booking
`NH7001234567` became `7001234567`, and the system sent that stranger a confirmation naming the
guest, their dates and their amount.

Two things fixed that. `services/notify.py` now refuses to message a placeholder, and
`utils/phone.ota_placeholder` produces a `0`-prefixed number, which no mobile is, so the shape test
alone recognises one. This script closes the gap in between: rows stamped BEFORE that change still
look like real mobiles, and are only recognised when the booking's own reference is at hand.

Deliberately conservative — it rewrites a number only when it is **exactly** the legacy placeholder
for that booking's own OTA reference. A guest who happens to own that number, or who gave their real
one at check-in, is left alone.

    python -m scripts.backfill_ota_placeholder_phones            # dry run (default)
    python -m scripts.backfill_ota_placeholder_phones --apply

Targets whatever `database.py` resolves. For Railway, run it once with DATABASE_URL set to the
DATABASE_PUBLIC_URL for that one command.
"""
import argparse
import logging
import sys

from database import SessionLocal
from models import Booking, Guest
from utils.phone import _legacy_ota_placeholder, ota_placeholder, same_number

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true",
                    help="write the changes (without this it only reports)")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        rows = (db.query(Booking, Guest)
                .join(Guest, Guest.guest_id == Booking.guest_id)
                .filter(Booking.ota_booking_id.isnot(None))
                .all())

        changes, already, untouched = [], 0, 0
        for b, g in rows:
            ref = b.ota_booking_id
            if not ref or not g.phone:
                continue
            if same_number(g.phone, ota_placeholder(ref)):
                already += 1                       # already the new form
                continue
            if same_number(g.phone, _legacy_ota_placeholder(ref)):
                changes.append((g, b, g.phone, ota_placeholder(ref)))
            else:
                untouched += 1                     # a real number the desk captured - leave it

        print("OTA bookings examined : %d" % len(rows))
        print("already normalised    : %d" % already)
        print("real numbers, skipped : %d" % untouched)
        print("to rewrite            : %d" % len(changes))
        for g, b, old, new in changes:
            print("   guest %-5s booking %-5s  %s  ->  %s   (%s)"
                  % (g.guest_id, b.booking_id, old, new, b.ota_booking_id))

        if not changes:
            print("\nNothing to do.")
            return 0
        if not args.apply:
            print("\nDRY RUN - nothing written. Re-run with --apply to make these changes.")
            return 0

        for g, _b, _old, new in changes:
            g.phone = new
        db.commit()
        print("\nApplied %d change(s)." % len(changes))
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
