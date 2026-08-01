-- Migration: 024_room_type_ac.sql
-- Description: AC / non-AC room types (FE-10).
-- Purpose: add room_types.is_ac (FALSE = non-AC default). Drives the "turn on AC" maintenance
--   task at check-in and the AC->non-AC downgrade approval gate on room shift.
-- NOTE: this is a COLUMN ADD — SQLAlchemy create_all does NOT add columns to an existing table,
--   so this migration MUST be run on the deployed DB. A fresh DB gets the column via create_all.
-- Database: PostgreSQL
-- Idempotent: YES (IF NOT EXISTS)

BEGIN;

ALTER TABLE room_types ADD COLUMN IF NOT EXISTS is_ac BOOLEAN NOT NULL DEFAULT FALSE;
CREATE INDEX IF NOT EXISTS idx_room_types_is_ac ON room_types(is_ac);

COMMIT;
