-- Migration: 030_alt_room_type.sql
-- Description: Per-room alternate room type (v4b7 — the FE-10 rework).
-- Purpose:
--   1. rooms.alt_room_type_id
--      A physical room can legitimately be sold as more than one thing. The owner's example:
--      Room 47 is a Triple A/C by default, but can also be let as a Triple Non-A/C. The room
--      keeps ONE default type (its existing room_type_id) and gains ONE optional alternate.
--
--      This replaces the FE-10 model, which put an `is_ac` flag on the room TYPE. That is
--      wrong for a property where only some rooms of a type were retrofitted — the flag was
--      then wrong for half of them, and both the AC ticket and the downgrade gate misfired.
--
--   2. booking_items.sold_as_room_type_id
--      Records the room's PHYSICAL type when it is sold as its alternate.
--      ⚠️ booking_items.room_type_id stays as what was BOOKED AND PRICED. availability
--      .booked_qty groups on it, so changing it would retroactively move a live reservation
--      between inventory buckets mid-stay. The guest pays for the type they booked; putting
--      them in a physically different room is the hotel's operational convenience.
--
--      Which is exactly why the sale is OTP-gated: the fraud vector is a receptionist putting
--      a guest into a Rs 4,000 room booked at Rs 2,000 — or collecting Rs 4,000 in cash and
--      recording the Rs 2,000 booking. This column makes every instance reportable afterwards.
--
-- ⚠️ KNOWN LIMITATION, stated in the UI: selling a room as its alternate makes the availability
--    counts drift by one for that stay, because RoomType.total_rooms is a static admin-set
--    integer that knows nothing about it. Bounded by the rule that an alternate sale is only
--    allowed when the booked type has NO free room anyway, and by the OTP gate. The real fix
--    (deriving total_rooms from a room count and teaching booked_qty about alt sales) touches
--    the public website's availability and is deliberately NOT in this migration.
--
-- Database: PostgreSQL
-- Idempotent: YES (ADD COLUMN / CREATE INDEX IF NOT EXISTS; constraints guarded by DO blocks)
--
-- NOTE: create_all() does NOT add columns to an existing table, so this file MUST be run.

BEGIN;

ALTER TABLE rooms ADD COLUMN IF NOT EXISTS alt_room_type_id INTEGER;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE constraint_name = 'fk_rooms_alt_room_type'
    ) THEN
        ALTER TABLE rooms
            ADD CONSTRAINT fk_rooms_alt_room_type
            FOREIGN KEY (alt_room_type_id) REFERENCES room_types(room_type_id);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE constraint_name = 'ck_rooms_alt_differs'
    ) THEN
        -- "Alternate" must actually be an alternative.
        ALTER TABLE rooms
            ADD CONSTRAINT ck_rooms_alt_differs
            CHECK (alt_room_type_id IS NULL OR alt_room_type_id <> room_type_id);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_rooms_alt_room_type_id ON rooms(alt_room_type_id);

ALTER TABLE booking_items ADD COLUMN IF NOT EXISTS sold_as_room_type_id INTEGER;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE constraint_name = 'fk_booking_items_sold_as_room_type'
    ) THEN
        ALTER TABLE booking_items
            ADD CONSTRAINT fk_booking_items_sold_as_room_type
            FOREIGN KEY (sold_as_room_type_id) REFERENCES room_types(room_type_id);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_booking_items_sold_as ON booking_items(sold_as_room_type_id);

COMMENT ON COLUMN rooms.alt_room_type_id IS
    'A second room type this physical room may be sold as (v4b7). NULL = this room is only '
    'ever its own type. Selling as the alternate needs an owner approval code, and is only '
    'permitted when no room of the booked type is free.';
COMMENT ON COLUMN booking_items.sold_as_room_type_id IS
    'The room''s PHYSICAL type when the stay was sold as an alternate. room_type_id stays as '
    'what was booked and priced — availability groups on it, so changing it would move a live '
    'reservation between inventory buckets mid-stay.';

COMMIT;
