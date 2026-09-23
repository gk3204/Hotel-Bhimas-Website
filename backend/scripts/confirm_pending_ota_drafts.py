"""One-off (v5r): turn the backlog of pending OTA drafts into real bookings.

Why there is a backlog
----------------------
`services/ota_service.auto_confirm_draft` called `_ota_placeholder_phone`, which was defined only in
`routers/ota.py` — so every voucher whose phone the OTA masks (all of Go-MMT, Yatra and Goibibo) raised
`NameError` inside a broad `except` and was logged as a harmless *"skipped draft"*. Production reached
**143 pending drafts and zero OTA bookings**, with guests arriving against reservations the PMS did not
know about. v5r fixes the import; this script works through what piled up meanwhile.

What it does
------------
Each pending confirmation draft goes through the *normal* confirm path, so every existing guard still
applies — availability (a room already sold to a walk-in refuses and the draft stays pending), room-type
mapping, the OTA-id duplicate check, prepaid crediting, and the v5r voucher-vs-price variance guard.
It cannot oversell: the booking either fits the inventory or it is left for the desk with the reason
recorded on the row.

Multi-room drafts follow the `ota_auto_confirm_mode` setting, exactly as the live poller does — by
default they are held for review rather than created, because the room count and the money on those come
from free text.

    python backend/scripts/confirm_pending_ota_drafts.py                        # local .env, dry run
    python backend/scripts/confirm_pending_ota_drafts.py "<DATABASE_URL>"       # dry run
    python backend/scripts/confirm_pending_ota_drafts.py "<DATABASE_URL>" --apply
    python backend/scripts/confirm_pending_ota_drafts.py "<DATABASE_URL>" --apply --upcoming-only
    python backend/scripts/confirm_pending_ota_drafts.py "<DATABASE_URL>" --apply --allow-multi-room

`--upcoming-only` skips drafts whose check-out has already passed (the backfill imported months of old
mail). `--allow-multi-room` overrides the setting for this run only. Re-running is safe: a confirmed
draft is skipped, and a draft already linked to a live booking is linked rather than duplicated.
"""
import os
import sys
from datetime import date


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


def main(argv):
    apply_it = "--apply" in argv
    upcoming_only = "--upcoming-only" in argv
    allow_multi = "--allow-multi-room" in argv
    url = _resolve_url(argv)

    os.environ["DATABASE_URL"] = url
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
    from database import SessionLocal
    from models import OtaDraftBooking, Booking
    from services import ota_service
    from utils import settings as app_settings

    db = SessionLocal()
    try:
        cfg = app_settings.get_ota_config(db)
        if allow_multi and apply_it:
            # For this run only, in memory — the stored setting is left exactly as the owner set it.
            app_settings.set_setting(db, app_settings.OTA_AUTO_CONFIRM_MODE_KEY, "all")
            db.flush()
        q = (db.query(OtaDraftBooking)
             .filter(OtaDraftBooking.kind == "confirmation",
                     OtaDraftBooking.status == "pending",
                     OtaDraftBooking.linked_booking_id.is_(None))
             .order_by(OtaDraftBooking.check_in))
        drafts = q.all()
        if upcoming_only:
            today = date.today()
            drafts = [d for d in drafts if d.check_out and d.check_out >= today]

        print(f"{len(drafts)} pending confirmation draft(s)"
              f"{' with an upcoming stay' if upcoming_only else ''}; "
              f"auto-confirm mode = {'all (this run)' if allow_multi and apply_it else cfg['auto_confirm_mode']}, "
              f"price tolerance {cfg['max_variance_percent']:g}%"
              f"{'' if apply_it else '   (DRY RUN — nothing written)'}")
        multi = [d for d in drafts if (d.rooms or 1) > 1]
        if multi:
            print(f"  of those, {len(multi)} are multi-room: "
                  + ", ".join(f"#{d.id}({d.rooms} rooms)" for d in multi[:12]))

        if not apply_it:
            for d in drafts[:40]:
                print(f"  #{d.id:<5} {d.channel_code:<11} {d.ota_booking_id or '-':<16} "
                      f"{str(d.check_in):<11}→{str(d.check_out):<11} rooms={d.rooms or 1} "
                      f"{(d.guest_name or '-')[:28]}")
            if len(drafts) > 40:
                print(f"  … and {len(drafts) - 40} more")
            print("\nRe-run with --apply to create the bookings.")
            return 0

        made, held = [], []
        for d in drafts:
            try:
                booking_id = ota_service.auto_confirm_draft(db, d, user=None)
                db.commit()
            except Exception as e:                      # never let one bad draft stop the run
                db.rollback()
                booking_id = None
                print(f"  #{d.id} errored: {e}")
            if booking_id:
                made.append((d.id, booking_id))
                print(f"  #{d.id:<5} → booking {booking_id}  ({d.channel_code} {d.ota_booking_id})")
            else:
                fresh = db.query(OtaDraftBooking).filter(OtaDraftBooking.id == d.id).first()
                held.append((d.id, (fresh.last_error if fresh else None) or "held"))

        print(f"\nCreated {len(made)} booking(s); {len(held)} left pending for the desk.")
        for did, why in held[:25]:
            print(f"  #{did}: {why}")
        if len(held) > 25:
            print(f"  … and {len(held) - 25} more (each shows its reason on the drafts screen)")
        live = db.query(Booking).filter(Booking.status.in_(("confirmed", "checked_in"))).count()
        print(f"Live bookings now: {live}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
