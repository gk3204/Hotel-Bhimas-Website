-- Migration: 029_complimentary.sql
-- Description: Complimentary stays (v4b6) — a booking the hotel gives away, in whole or in part.
-- Purpose:
--   1. bookings.comp_mode / comp_reason / comp_by / comp_at / comp_otp_id
--      The owner asked for two kinds of free stay: `all` (room AND every folio extra) and
--      `room` (room rent free, extras still billed). A STRING mode rather than two booleans,
--      because the modes are mutually exclusive — a bool pair can encode the nonsense state
--      "free but also not free" — and because a third mode ("first night free", "F&B only")
--      is plausible. Follows the existing bookings.bill_to precedent (guest|company|split).
--
--      comp_otp_id points at the owner approval that authorised it, so the audit trail can
--      show WHICH code released a giveaway rather than just that one was used.
--
--      ⚠️ grand_total is deliberately NOT zeroed by a comp. It records what the stay was
--      WORTH; the comp records why it was not collected. Zeroing it would corrupt the agent
--      commission accrual, the OTA net-payout snapshot, loyalty accrual and every ADR/RevPAR
--      figure — and it is the number the giveaway report reads.
--
--   2. day_close_summaries.comp_room_value
--      Suppression (rather than a 100% discount line) keeps the GST reports honest: posting a
--      taxable line and discounting it away would create an output-tax liability on a supply
--      with no consideration. The cost is that ADR/RevPAR understate, because the revenue
--      simply is not there. This column carries the given-away value ALONGSIDE the day's
--      figures — never inside them — so "what did we comp this month?" is answerable without
--      touching a single tax number.
--
-- Database: PostgreSQL
-- Idempotent: YES (ADD COLUMN IF NOT EXISTS; constraint guarded by a DO block)
--
-- NOTE: create_all() does NOT add columns to an existing table, so this file MUST be run.

BEGIN;

ALTER TABLE bookings ADD COLUMN IF NOT EXISTS comp_mode VARCHAR(10) NOT NULL DEFAULT 'none';
ALTER TABLE bookings ADD COLUMN IF NOT EXISTS comp_reason VARCHAR(200);
ALTER TABLE bookings ADD COLUMN IF NOT EXISTS comp_by INTEGER;
ALTER TABLE bookings ADD COLUMN IF NOT EXISTS comp_at TIMESTAMP;
ALTER TABLE bookings ADD COLUMN IF NOT EXISTS comp_otp_id INTEGER;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE constraint_name = 'ck_bookings_comp_mode'
    ) THEN
        ALTER TABLE bookings
            ADD CONSTRAINT ck_bookings_comp_mode
            CHECK (comp_mode IN ('none', 'all', 'room'));
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_bookings_comp_mode ON bookings(comp_mode);

ALTER TABLE day_close_summaries ADD COLUMN IF NOT EXISTS comp_room_value NUMERIC(12, 2);

COMMENT ON COLUMN bookings.comp_mode IS
    'none | all | room. `all` = room rent AND every folio extra are free; `room` = room rent '
    'free, extras still billed. Charges are SUPPRESSED, not discounted — posting a taxable '
    'line then discounting it 100%% would create an output-tax liability on a supply with no '
    'consideration. Setting AND clearing require an owner approval code.';
COMMENT ON COLUMN bookings.comp_otp_id IS
    'The owner approval that authorised this comp, so the audit shows which code released it.';
COMMENT ON COLUMN day_close_summaries.comp_room_value IS
    'Value given away as complimentary on this business date. Reported ALONGSIDE the day''s '
    'revenue, never inside it — suppression keeps the GST figures right but makes ADR/RevPAR '
    'understate, and this is what answers "how much did we comp?".';

COMMIT;
