"""Backfill folio_charges.charge_date / booking_item_id on room lines posted before
migration 028 (v4b1).

WHY THIS IS NEEDED
------------------
Migration 028 adds a per-night identity to room charges so that check-in, extend-stay and the
automatic overstay charge can all ask "is this night already billed?". Rows posted before it
have charge_date IS NULL. Postgres treats NULLs as distinct, so they never trip the unique
index — but they also cannot be RECOGNISED, which means extending such a stay would post the
same nights again and double-charge the guest.

`services/room_posting.post_room_nights` therefore refuses to run against a folio that still
holds an un-backfilled room line. This script clears that.

HOW IT ATTRIBUTES
-----------------
The old `open_folio` loop wrote room lines in strict night order per booking item
(`for item ... for n in range(nights)`), and rows carry a monotonic id. So within one folio,
grouping by the item and ordering by id maps row N onto `booking.check_in + N`.

Where a folio's room lines cannot be mapped confidently — a row count that isn't
`items x nights`, or an item that cannot be matched — the folio is REPORTED and SKIPPED, never
guessed at. Same discipline as scripts/backfill_menu_item_id.py.

USAGE
-----
    python -m scripts.backfill_room_charge_dates              # dry run, writes nothing
    python -m scripts.backfill_room_charge_dates --commit     # actually write
    python -m scripts.backfill_room_charge_dates --booking 1183 --commit
"""
import argparse
import os
import sys
from collections import defaultdict
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import SessionLocal                     # noqa: E402
from models import Booking, BookingItem, Folio, FolioCharge   # noqa: E402


def backfill(db, *, commit=False, only_booking=None):
    q = (db.query(Folio, Booking)
           .join(Booking, Booking.booking_id == Folio.booking_id))
    if only_booking:
        q = q.filter(Booking.booking_id == only_booking)

    stats = {"folios": 0, "updated": 0, "skipped_folios": 0, "already": 0}
    problems = []

    for folio, booking in q.order_by(Folio.id).all():
        rows = (db.query(FolioCharge)
                  .filter(FolioCharge.folio_id == folio.id,
                          FolioCharge.type == "room",
                          FolioCharge.reversal_of_id.is_(None))
                  .order_by(FolioCharge.id)
                  .all())
        if not rows:
            continue
        stats["folios"] += 1

        stale = [r for r in rows if r.charge_date is None]
        if not stale:
            stats["already"] += 1
            continue

        items = (db.query(BookingItem)
                   .filter(BookingItem.booking_id == booking.booking_id)
                   .order_by(BookingItem.booking_item_id)
                   .all())
        nights = max(1, (booking.check_out - booking.check_in).days)

        # The old loop wrote items x nights rows, in that order. Anything else means the folio
        # was edited by hand or by a path we do not know about — report, do not guess.
        if not items or len(rows) != len(items) * nights:
            problems.append(
                f"folio {folio.id} (booking {booking.booking_id}): {len(rows)} room line(s) "
                f"but {len(items)} item(s) x {nights} night(s) = {len(items) * nights} expected "
                f"— SKIPPED, needs a human")
            stats["skipped_folios"] += 1
            continue

        # Group the rows back onto their items in the order they were written.
        by_item = defaultdict(list)
        for idx, row in enumerate(rows):
            by_item[items[idx // nights].booking_item_id].append(row)

        for item_id, item_rows in by_item.items():
            for n, row in enumerate(item_rows):
                night = booking.check_in + timedelta(days=n)
                if row.charge_date is None:
                    row.charge_date = night
                    stats["updated"] += 1
                if row.booking_item_id is None:
                    row.booking_item_id = item_id
                if row.posting_reason is None:
                    row.posting_reason = "checkin"

    if commit:
        db.commit()
    else:
        db.rollback()
    return stats, problems


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--commit", action="store_true",
                    help="actually write (default is a dry run that writes nothing)")
    ap.add_argument("--booking", type=int, default=None, help="limit to one booking id")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        stats, problems = backfill(db, commit=args.commit, only_booking=args.booking)
    finally:
        db.close()

    mode = "COMMITTED" if args.commit else "DRY RUN (nothing written)"
    print(f"\n{mode}")
    print(f"  folios with room lines : {stats['folios']}")
    print(f"  already backfilled     : {stats['already']}")
    print(f"  charge_date set        : {stats['updated']}")
    print(f"  folios skipped         : {stats['skipped_folios']}")
    if problems:
        print("\nNeeds a human — these folios were NOT touched:")
        for p in problems:
            print("  -", p)
    if not args.commit and stats["updated"]:
        print("\nRe-run with --commit to apply.")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
