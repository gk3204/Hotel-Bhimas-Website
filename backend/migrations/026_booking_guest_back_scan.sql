-- Migration: 026_booking_guest_back_scan.sql
-- Description: Second (reverse-side) ID scan per occupant.
-- Purpose:
--   The front desk now scans government IDs directly from a flatbed MFP (HP LaserJet M1005).
--   That device has no ADF and no duplex, so an Aadhaar card is captured as TWO passes —
--   front and back. booking_guests held exactly one scan reference (migration 022), so the
--   reverse side had nowhere to go.
--   Both sides live in the SAME encrypted store (utils/secure_id_store.py) and are read back
--   only through the authed decrypt-on-read route, which audits `id.view` per side.
-- Database: PostgreSQL
-- Idempotent: YES (ADD COLUMN IF NOT EXISTS)
--
-- NOTE: create_all() does NOT add columns to an existing table, so this file must be run
-- against any database that already has booking_guests.

BEGIN;

ALTER TABLE booking_guests ADD COLUMN IF NOT EXISTS id_scan_back_ref  VARCHAR(120);
ALTER TABLE booking_guests ADD COLUMN IF NOT EXISTS id_scan_back_mime VARCHAR(40);

COMMENT ON COLUMN booking_guests.id_scan_back_ref  IS
    'Encrypted-file ref for the REVERSE of the ID: booking_<id>/<uuid>.enc — never a public URL.';
COMMENT ON COLUMN booking_guests.id_scan_back_mime IS
    'Content type of the reverse scan, for the decrypt-on-read stream.';

COMMIT;
