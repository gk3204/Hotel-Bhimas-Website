-- Migration: 032_booking_occupancy.sql
-- Description: Per-booking occupancy — adults + children.
-- Purpose: capture how many adults / children a booking is for. Adults count against the room
--   type's max_occupancy (= max adults); children are separate. ID is mandatory for adults at
--   check-in, optional for minors.
-- NOTE: COLUMN ADD — SQLAlchemy create_all does NOT add columns to an existing table, so this
--   migration MUST be run on the deployed DB. A fresh DB gets the columns via create_all.
-- Database: PostgreSQL
-- Idempotent: YES (IF NOT EXISTS)

BEGIN;

ALTER TABLE bookings ADD COLUMN IF NOT EXISTS adults   INTEGER NOT NULL DEFAULT 1;
ALTER TABLE bookings ADD COLUMN IF NOT EXISTS children INTEGER NOT NULL DEFAULT 0;

-- Per-occupant KYC: mark a child (recorded by name, no ID required).
ALTER TABLE booking_guests ADD COLUMN IF NOT EXISTS is_minor BOOLEAN NOT NULL DEFAULT FALSE;

COMMIT;
