-- Migration: 034_ota_actual_commission.sql
-- Description: Read the ACTUAL commission + net payout the OTA voucher states, instead of applying a
--   configured per-channel %. MakeMyTrip / Goibibo / Yatra all print "(B) … Commission (including
--   GST)" and "Payable to Property" per booking, so we parse and store those. The channel % stays as
--   a fallback for desk-typed OTA bookings that carry no voucher figures.
--     * bookings.ota_commission_amount        — actual commission (incl GST) for this booking
--     * ota_draft_bookings.commission_amount  — same, parsed from the email onto the draft
--     * ota_draft_bookings.net_payout         — the voucher's "Payable to Property"
-- NOTE: COLUMN ADD — SQLAlchemy create_all does NOT add columns to an existing table, so this
--   migration MUST be run on the deployed DB. A fresh DB gets the columns via create_all.
-- Database: PostgreSQL
-- Idempotent: YES (IF NOT EXISTS)

BEGIN;

ALTER TABLE bookings           ADD COLUMN IF NOT EXISTS ota_commission_amount NUMERIC(10, 2);
ALTER TABLE ota_draft_bookings ADD COLUMN IF NOT EXISTS commission_amount     NUMERIC(10, 2);
ALTER TABLE ota_draft_bookings ADD COLUMN IF NOT EXISTS net_payout            NUMERIC(10, 2);

COMMIT;
