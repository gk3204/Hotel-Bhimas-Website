"""One-off: put auto-confirmed OTA drafts back to pending, and cancel the bookings they created.

Why they were auto-confirmed wrongly
------------------------------------
On the build production is running (**v5s**, commit 3aba971) the multi-room path has a hole that the
v6 line closed and production has not yet received:

* `_room_count` has no pattern for **"N Room(s)"** — the form Go-MMT and Goibibo actually print — so
  a multi-room voucher parses as `rooms = NULL` (this is finding F-18).
* `auto_confirm_draft` then does `rooms_wanted = int(d.rooms or 1)`, which turns *"we could not read
  how many rooms this is"* into a confident **1**.
* So the guard below it, `if rooms_wanted > 1 and mode != "all": hold`, **never fires**.

⚠️ The result is a multi-room reservation booked as a **single room**: the other rooms stay on sale,
the guest arrives with a confirmation the PMS cannot honour, and the money will not reconcile. That
is what this script is here to undo.

What it does, per draft
-----------------------
1. Refuses to touch anything already **checked in or checked out** — the guest is, or was, in the
   room. Those are listed for a human; software must not unwind a stay that happened.
2. Refuses when money has been **received against the booking** (any non-refunded desk payment) —
   cancelling would strand it. Listed for a human.
3. Otherwise **cancels the booking** through the normal cancel path, so the rooms go back on sale
   and the cancellation is audited like any other.
4. Clears `linked_booking_id` and sets the draft back to **pending**, with `last_error` saying why,
   so the desk sees it on the drafts screen with the reason on the row.

Dry run by DEFAULT. Nothing is written without `--apply`.

    python backend/scripts/revert_autoconfirmed_ota_drafts.py                       # local .env, dry run
    python backend/scripts/revert_autoconfirmed_ota_drafts.py "<DATABASE_URL>"      # dry run
    python backend/scripts/revert_autoconfirmed_ota_drafts.py "<DATABASE_URL>" --apply
    python backend/scripts/revert_autoconfirmed_ota_drafts.py "<DATABASE_URL>" --since 2026-08-01
    python backend/scripts/revert_autoconfirmed_ota_drafts.py "<DATABASE_URL>" --multi-room-only

`--since` limits by the draft's creation date. `--multi-room-only` touches only the drafts whose
voucher text still looks like more than one room (see `_looks_multi_room`) — which is the actual
defect — and leaves genuine single-room auto-confirmations alone. Re-running is safe: a draft that
is already pending is skipped.
"""
import argparse
import os
import re
import sys
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# "3 Room(s)", "Rooms: 2", "2 Rooms booked" — the forms v5s cannot read but a human can see at once.
_MULTI_PATTERNS = [
    r"(\d{1,2})\s*room\(s\)",
    r"total\s*(?:no\.?|number)\s*of\s*rooms?[\s:*|]*(\d{1,2})",
    r"(?:no\.?|number)\s*of\s*rooms?[\s:*|]*(\d{1,2})",
    r"(\d{1,2})\s*rooms?\s*(?:booked|reserved)\b",
    r"(\d{1,2})[ \t]*[xX][ \t]+[A-Za-z]",
]


def _looks_multi_room(raw: str | None) -> int | None:
    """The room count a HUMAN would read off the voucher, including the shapes v5s misses."""
    if not raw:
        return None
    text = raw.lower()
    for pat in _MULTI_PATTERNS:
        m = re.search(pat, text)
        if m:
            try:
                n = int(m.group(1))
            except (TypeError, ValueError):
                continue
            if 1 <= n <= 10:
                return n
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("database_url", nargs="?", default=None)
    ap.add_argument("--apply", action="store_true", help="actually write (default is a dry run)")
    ap.add_argument("--since", default=None, help="only drafts created on/after YYYY-MM-DD")
    ap.add_argument("--multi-room-only", action="store_true",
                    help="only drafts whose voucher text reads as more than one room")
    args = ap.parse_args()

    if args.database_url:
        os.environ["DATABASE_URL"] = args.database_url

    from database import SessionLocal                      # noqa: E402
    from models import Booking, OtaDraftBooking, Payment   # noqa: E402

    since = None
    if args.since:
        since = datetime.strptime(args.since, "%Y-%m-%d").date()

    db = SessionLocal()
    q = db.query(OtaDraftBooking).filter(OtaDraftBooking.status == "confirmed")
    drafts = q.order_by(OtaDraftBooking.id).all()

    considered = reverted = skipped_stay = skipped_money = skipped_single = 0
    blocked: list[str] = []

    print("mode: %s" % ("APPLY" if args.apply else "DRY RUN — nothing will be written"))
    print("confirmed drafts found: %d\n" % len(drafts))

    for d in drafts:
        created = d.created_at.date() if isinstance(d.created_at, datetime) else d.created_at
        if since and created and created < since:
            continue

        seen = _looks_multi_room(getattr(d, "raw_source", None))
        if args.multi_room_only and not (seen and seen > 1):
            skipped_single += 1
            continue
        considered += 1

        label = "draft %-5s %-12s %-18s created %s" % (
            d.id, d.channel_code, d.ota_booking_id or "-", created)

        b = (db.query(Booking).filter(Booking.booking_id == d.linked_booking_id).first()
             if d.linked_booking_id else None)
        if b is None:
            print("%s  -> no linked booking; draft set back to pending" % label)
            if args.apply:
                d.status = "pending"
                d.last_error = ("Auto-confirmed in error and reverted: the room count could not be "
                                "read from the voucher. Check the rooms and confirm by hand.")
            reverted += 1
            continue

        # (1) the stay happened — software must not unwind it
        if b.status in ("checked_in", "checked_out"):
            skipped_stay += 1
            blocked.append("%s  -> booking #%s is %s — NOT touched (the guest is/was in the room)"
                           % (label, b.booking_id, b.status))
            continue

        # (2) money received against it
        paid = (db.query(Payment)
                .filter(Payment.booking_id == b.booking_id, Payment.status == "paid")
                .filter((Payment.refund_status.is_(None)) | (Payment.refund_status == "failed"))
                .count())
        if paid:
            skipped_money += 1
            blocked.append("%s  -> booking #%s has %d unrefunded payment(s) — NOT touched"
                           % (label, b.booking_id, paid))
            continue

        print("%s  -> cancel booking #%s (%s, voucher reads %s room(s)) and re-open the draft"
              % (label, b.booking_id, b.status, seen if seen else "?"))
        if args.apply:
            b.status = "cancelled"
            b.cancelled_at = datetime.now()
            d.status = "pending"
            d.linked_booking_id = None
            d.last_error = (
                "Auto-confirmed in error (the voucher's room count could not be read, so it was "
                "booked as 1 room) and reverted on %s. Booking #%s cancelled; the rooms are back "
                "on sale. Check the room count and confirm by hand."
                % (date.today().isoformat(), b.booking_id))
        reverted += 1

    if blocked:
        print("\n⚠️  LEFT ALONE — these need a person, not a script:")
        for line in blocked:
            print("   " + line)

    print("\nconsidered %d | reverted %d | left (stay happened) %d | left (money) %d | "
          "skipped (single room) %d" % (considered, reverted, skipped_stay, skipped_money,
                                        skipped_single))

    if args.apply:
        db.commit()
        print("\nCOMMITTED.")
    else:
        db.rollback()
        print("\nDry run — nothing written. Re-run with --apply to commit.")
    db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
