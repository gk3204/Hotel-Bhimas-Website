-- Migration: 028_room_night_posting.sql
-- Description: The room-night posting spine (v4b1) + OTA prepayment + card-erase columns.
-- Purpose:
--   1. folio_charges.charge_date / booking_item_id / posting_reason
--      Until now `open_folio` was the ONLY writer of type='room' charges and it posted the
--      whole stay in one go, so nothing ever needed to know which night a line covered.
--      v4 adds three more writers/readers — extend-stay (v4b2), the automatic overstay
--      charge (v4b3) and its reversal — and every one of them has to be able to answer
--      "is this night already billed?" without double-charging a guest.
--      The only existing trace of the night is the human description string
--      ("Room Deluxe x1 - 05 Aug 2026"), which is what a receptionist reads on a bill and
--      will be reformatted eventually. Parsing that back was never an option.
--
--   2. uq_folio_room_night (partial unique index)
--      Turns a double-post into an IntegrityError instead of a second charge on a guest's
--      bill. Two subtleties, both load-bearing — do NOT "tidy" either away:
--        (a) It deliberately does NOT filter on `void`. A voided night keeps its
--            (booking_item_id, charge_date) slot forever, so "voided" reads as "settled, do
--            not re-post". That is exactly what stops the overstay sweep re-billing a night
--            the owner just reversed. A `WHERE void = false` index would do the opposite.
--        (b) `void_charge` appends a mirror line with the SAME type, which would collide.
--            `reversal_of_id IS NULL` excludes it, and the void helper leaves both new
--            columns NULL on the reversal — a reversal is a bookkeeping mirror, not a night.
--
--   3. bookings.card_reencode_required / original_check_out
--      An overstay charge or an extension moves check_out, which moves the key-card window,
--      so the guest's card must be re-cut. original_check_out preserves what the guest
--      actually booked (the runaway guard in v4b3 needs it, and so does an honest
--      "booked 3 nights, stayed 5" line on the bill).
--
--   4. bookings.prepaid_amount / prepaid_source
--      OTA bookings record NO Payment row — correctly, the channel took the money, not the
--      hotel. But total_paid() only sums Payment rows, so it returned 0 for every OTA stay:
--      the folio showed the full room amount due and the desk collected it a SECOND time,
--      check-in flagged an unpaid booking, the key card tripped `card_without_payment`, and
--      fraud_detection raised an alert on every single OTA guest. This column is the
--      prepayment, credited to the folio at open_folio.
--      ⚠️ It is the GROSS the guest paid the channel, NOT ota_net_payout (which is what the
--      hotel receives after commission). Conflating them under-credits the guest.
--
--   5. card_issuances.erased_at / erased_by
--      v4b4 makes checkout read + erase the card on the encoder. Recorded from day one even
--      while the gate is off, so the owner can see how often cards actually come back before
--      deciding to make it blocking.
--
-- Database: PostgreSQL
-- Idempotent: YES (ADD COLUMN / CREATE INDEX IF NOT EXISTS; constraint guarded by a DO block)
--
-- NOTE: create_all() does NOT add columns to an existing table, so this file MUST be run
-- against any database that already has folio_charges / bookings / card_issuances.
--
-- NOTE: this migration does NOT backfill charge_date. Room lines posted before it stay NULL;
-- Postgres treats NULLs as distinct so they never collide, but they also cannot be
-- recognised — which is why services/room_posting.post_room_nights REFUSES to run against a
-- folio that still has one. Run backend/scripts/backfill_room_charge_dates.py (dry-run by
-- default) to attribute the historic rows.

BEGIN;

-- 1. which night, which room, and why -----------------------------------------------------
ALTER TABLE folio_charges ADD COLUMN IF NOT EXISTS charge_date DATE;
ALTER TABLE folio_charges ADD COLUMN IF NOT EXISTS booking_item_id INTEGER;
ALTER TABLE folio_charges ADD COLUMN IF NOT EXISTS posting_reason VARCHAR(20);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE constraint_name = 'fk_folio_charges_booking_item'
    ) THEN
        ALTER TABLE folio_charges
            ADD CONSTRAINT fk_folio_charges_booking_item
            FOREIGN KEY (booking_item_id) REFERENCES booking_items(booking_item_id);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_folio_charges_booking_item_id
    ON folio_charges(booking_item_id);
CREATE INDEX IF NOT EXISTS idx_folio_charges_charge_date
    ON folio_charges(charge_date);

-- 2. one room line per (booking item, night) ----------------------------------------------
CREATE UNIQUE INDEX IF NOT EXISTS uq_folio_room_night
    ON folio_charges (booking_item_id, charge_date)
    WHERE type = 'room' AND reversal_of_id IS NULL;

-- 3. stay extension bookkeeping -------------------------------------------------------------
ALTER TABLE bookings ADD COLUMN IF NOT EXISTS card_reencode_required BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE bookings ADD COLUMN IF NOT EXISTS original_check_out DATE;

-- 4. prepayment collected by someone else (OTA / website) ------------------------------------
ALTER TABLE bookings ADD COLUMN IF NOT EXISTS prepaid_amount NUMERIC(12, 2) NOT NULL DEFAULT 0;
ALTER TABLE bookings ADD COLUMN IF NOT EXISTS prepaid_source VARCHAR(20);

-- 5. card erased at checkout -----------------------------------------------------------------
ALTER TABLE card_issuances ADD COLUMN IF NOT EXISTS erased_at TIMESTAMP;
ALTER TABLE card_issuances ADD COLUMN IF NOT EXISTS erased_by INTEGER;

-- documentation ------------------------------------------------------------------------------
COMMENT ON COLUMN folio_charges.charge_date IS
    'The night this room line covers (v4b1). NULL on every non-room line and on room lines '
    'posted before migration 028. Half of the uq_folio_room_night idempotency key.';
COMMENT ON COLUMN folio_charges.posting_reason IS
    'checkin | extend | overstay. Drives the "you may only reverse an auto-charged night" '
    'guard on POST /reception/overstay/{id}/reverse, and the desk''s auto-charge chip.';
COMMENT ON COLUMN bookings.prepaid_amount IS
    'GROSS amount the guest already paid a third party (OTA channel / website). NOT '
    'ota_net_payout, which is what the hotel receives after commission. Credited to the '
    'folio at open_folio so the desk does not collect the room a second time. Deliberately '
    'NOT a Payment row: that would flow into collections_summary, the cash-drawer gate and '
    'the shift variance, claiming the hotel collected money it never touched.';
COMMENT ON COLUMN bookings.original_check_out IS
    'What the guest actually booked, set once on the first extension of any kind and never '
    'overwritten. The v4b3 runaway guard needs it, since the overstay job moves check_out.';

COMMIT;
