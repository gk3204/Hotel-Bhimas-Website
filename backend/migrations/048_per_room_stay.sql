-- 048 — the stay becomes per-room (F-13, F-14, F-15).
--
-- The owner's rule, in their words: "Early check-out, early check-in, late check-in and extend stay
-- etc should function exactly as how for single room; in multiple booking we apply the same rules
-- but for each room and not for the entire booking."
--
-- Until now the BOOKING was the unit of arrival, departure and billing, so a three-room family was
-- all-or-nothing: the desk could not check in the car that had arrived, could not check out the room
-- that was leaving, and could not tell which room a charge belonged to. This gives every room its
-- own stay.
--
-- ⚠️ The booking-level columns are NOT removed and NOT abandoned. `bookings.check_out`,
-- `checked_in_at`, `checked_out_at` and `status` are read in ~94 places across 22 backend files
-- (availability, reports, overstay, the card window, the night audit). They are now maintained as
-- AGGREGATES of the rooms — `check_out` = the latest room's, `status` = checked_in once ANY room has
-- arrived and checked_out only when ALL have left — by `reception.sync_booking_from_items()`, which
-- runs after every per-room change. That is what lets this land without rewriting those 94 readers.
--
-- Idempotent and additive, like every migration here.

ALTER TABLE booking_items ADD COLUMN IF NOT EXISTS checked_in_at          TIMESTAMP;
ALTER TABLE booking_items ADD COLUMN IF NOT EXISTS checked_out_at         TIMESTAMP;
ALTER TABLE booking_items ADD COLUMN IF NOT EXISTS check_out              DATE;
ALTER TABLE booking_items ADD COLUMN IF NOT EXISTS original_check_out     DATE;
ALTER TABLE booking_items ADD COLUMN IF NOT EXISTS stay_started_at        TIMESTAMP;
ALTER TABLE booking_items ADD COLUMN IF NOT EXISTS expected_arrival_at    TIMESTAMP;
ALTER TABLE booking_items ADD COLUMN IF NOT EXISTS checkout_extended_until TIMESTAMP;

CREATE INDEX IF NOT EXISTS ix_booking_items_checked_in_at  ON booking_items (checked_in_at);
CREATE INDEX IF NOT EXISTS ix_booking_items_checked_out_at ON booking_items (checked_out_at);
CREATE INDEX IF NOT EXISTS ix_booking_items_check_out      ON booking_items (check_out);

-- Backfill every existing row from its parent booking, so a stay already in the database behaves
-- exactly as it did before: same dates, same arrival and departure moments, same clock anchor.
-- A booking that has not been checked into simply inherits NULL arrival fields, which is correct.
UPDATE booking_items i
   SET check_out               = COALESCE(i.check_out, b.check_out),
       original_check_out      = COALESCE(i.original_check_out, b.original_check_out),
       checked_in_at           = COALESCE(i.checked_in_at, b.checked_in_at),
       checked_out_at          = COALESCE(i.checked_out_at, b.checked_out_at),
       stay_started_at         = COALESCE(i.stay_started_at, b.stay_started_at),
       expected_arrival_at     = COALESCE(i.expected_arrival_at, b.expected_arrival_at),
       checkout_extended_until = COALESCE(i.checkout_extended_until, b.checkout_extended_until)
  FROM bookings b
 WHERE b.booking_id = i.booking_id
   AND (i.check_out IS NULL OR i.checked_in_at IS NULL);
