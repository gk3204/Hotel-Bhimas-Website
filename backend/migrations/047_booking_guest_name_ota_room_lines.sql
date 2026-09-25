-- 047 — a booking remembers the name it was made in (F-01), and an OTA draft can hold rooms of
-- more than one type (F-18).
--
-- Idempotent and additive, like every migration here: safe to re-run, safe to run against a database
-- that already has some of it.

-- ---------------------------------------------------------------------------------------------
-- F-01: bookings.guest_name
-- ---------------------------------------------------------------------------------------------
-- A booking had no name of its own — every document read it through `bookings.guest_id -> guests.name`.
-- Guests are matched by phone (deliberately, so history and loyalty attach to one customer), and the
-- match block then refreshed that one row with whatever name the new booking carried. One phone per
-- family or per company driver is completely normal at a desk, so a later booking silently renamed
-- the earlier one — and with it a tax invoice ALREADY ISSUED, the registration slip, and the police
-- register. This column is the booking's own record of who it was made for; `guests.name` stays the
-- customer's current best name for the CRM.
ALTER TABLE bookings ADD COLUMN IF NOT EXISTS guest_name VARCHAR(100);

-- Backfill from the guest record. For most rows this is already right; where a guest was renamed by
-- a later booking, this preserves what the system believes today rather than inventing history it
-- cannot recover. From here on the value is written at booking creation and never touched again.
UPDATE bookings b
   SET guest_name = g.name
  FROM guests g
 WHERE g.guest_id = b.guest_id
   AND b.guest_name IS NULL;

-- ---------------------------------------------------------------------------------------------
-- F-18: ota_draft_bookings.room_lines
-- ---------------------------------------------------------------------------------------------
-- A draft could describe only ONE room type (`room_type_hint`) and a count, so a voucher like
-- NH73163518841498 — 1 Four Bed + 1 Double Deluxe + 2 Triple Bed — could not be represented at all.
-- Confirming it as one type x 4 billed Rs 7,350 against a voucher of Rs 6,300. This holds the
-- per-room breakdown the voucher itself prints, as JSON:
--     [{"hint": "Four Bed Non-Ac", "rooms": 1, "amount": 1837.5}, ...]
-- `rooms` (the total) is unchanged, so everything downstream that reads it keeps working.
ALTER TABLE ota_draft_bookings ADD COLUMN IF NOT EXISTS room_lines TEXT;
