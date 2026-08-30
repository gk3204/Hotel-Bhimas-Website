-- Migration: 033_ota_draft_occupancy.sql
-- Description: Occupancy (adults + children) parsed from an OTA email onto its draft, so confirming
--   the draft carries the real party size into the booking (v4 occupancy) instead of defaulting to
--   1 adult. Populated by the MakeMyTrip / Goibibo / Yatra parsers.
-- NOTE: COLUMN ADD — SQLAlchemy create_all does NOT add columns to an existing table, so this
--   migration MUST be run on the deployed DB. A fresh DB gets the columns via create_all.
-- Database: PostgreSQL
-- Idempotent: YES (IF NOT EXISTS)

BEGIN;

ALTER TABLE ota_draft_bookings ADD COLUMN IF NOT EXISTS adults   INTEGER NOT NULL DEFAULT 1;
ALTER TABLE ota_draft_bookings ADD COLUMN IF NOT EXISTS children INTEGER NOT NULL DEFAULT 0;

COMMIT;
