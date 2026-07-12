-- Migration: 004_rooms_setup.sql
-- Description: Physical-room card-lock identity fields (Milestone 1, prompt 04).
-- Purpose:
--   rooms.building  -> card code BB (default 1)
--   rooms.floor     -> card code FF (default 1)
--   rooms.max_cards -> per-room key limit (default 4)
--   rooms.lock_no   -> optional physical lock id
--   rooms.is_active -> False = out of service (repair) → excluded from booking/assignment (default TRUE)
-- (Also auto-created by SQLAlchemy create_all; included for prod parity via psql.)
-- Database: PostgreSQL
-- Idempotent: YES (information_schema checks)

BEGIN;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='rooms' AND column_name='building') THEN
    ALTER TABLE rooms ADD COLUMN building INTEGER NOT NULL DEFAULT 1;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='rooms' AND column_name='floor') THEN
    ALTER TABLE rooms ADD COLUMN floor INTEGER NOT NULL DEFAULT 1;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='rooms' AND column_name='max_cards') THEN
    ALTER TABLE rooms ADD COLUMN max_cards INTEGER NOT NULL DEFAULT 4;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='rooms' AND column_name='lock_no') THEN
    ALTER TABLE rooms ADD COLUMN lock_no VARCHAR(20);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='rooms' AND column_name='is_active') THEN
    ALTER TABLE rooms ADD COLUMN is_active BOOLEAN NOT NULL DEFAULT TRUE;
  END IF;
END $$;
CREATE INDEX IF NOT EXISTS idx_rooms_is_active ON rooms(is_active);

COMMIT;

-- ============================================
-- VERIFICATION (run manually if desired)
-- ============================================
/*
SELECT column_name, data_type FROM information_schema.columns
WHERE table_name='rooms' AND column_name IN ('building','floor','max_cards','lock_no');
*/
