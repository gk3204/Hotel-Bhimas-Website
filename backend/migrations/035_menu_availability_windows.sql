-- Migration: 035_menu_availability_windows.sql
-- Description: Room-service menu items can be limited to time-of-day windows (multiple per day,
--   e.g. Fried Rice 12:00-15:00 AND 19:00-22:00). Outside its windows an item is shown but not
--   orderable by the guest. Stored as a JSON array of {"start":"HH:MM","end":"HH:MM"} (IST);
--   NULL/empty = available all day (still subject to is_available on/off).
--     * menu_items.available_windows — JSON text, nullable
-- NOTE: COLUMN ADD — SQLAlchemy create_all does NOT add columns to an existing table, so this
--   migration MUST be run on the deployed DB. A fresh DB gets the column via create_all.
-- Database: PostgreSQL
-- Idempotent: YES (IF NOT EXISTS)

BEGIN;

ALTER TABLE menu_items ADD COLUMN IF NOT EXISTS available_windows TEXT;

COMMIT;
