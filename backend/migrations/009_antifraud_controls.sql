-- Migration: 009_antifraud_controls.sql
-- Description: Anti-fraud & internal controls (prompt 11, core). Owner-approval OTP flow
--              for sensitive desk actions + supporting columns for the detection sweeps.
-- Purpose:
--   1. owner_otps            -> one-time owner-approval codes (refund / below-floor discount / ...)
--   2. rooms.status_changed_at   -> when a room's status last changed (cleaning-too-long detection)
--   3. fraud_alerts.review_note  -> acknowledge/dismiss note on an alert
-- All new tables/columns are ALSO auto-created by SQLAlchemy create_all; included here for
-- explicit prod parity / running against an existing deployed DB via psql.
-- Database: PostgreSQL
-- Idempotent: YES (information_schema checks + IF NOT EXISTS)

BEGIN;

-- ============================================
-- 1. owner_otps
-- ============================================
CREATE TABLE IF NOT EXISTS owner_otps (
    id          SERIAL PRIMARY KEY,
    action      VARCHAR(40) NOT NULL,
    context     TEXT,
    code        VARCHAR(10) NOT NULL,
    expires_at  TIMESTAMP NOT NULL,
    used        BOOLEAN NOT NULL DEFAULT FALSE,
    used_at     TIMESTAMP,
    used_by     INTEGER REFERENCES users(user_id),
    approved_by INTEGER REFERENCES users(user_id),
    created_by  INTEGER REFERENCES users(user_id),
    created_at  TIMESTAMP DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_owner_otps_action ON owner_otps(action);
CREATE INDEX IF NOT EXISTS idx_owner_otps_used ON owner_otps(used);
CREATE INDEX IF NOT EXISTS idx_owner_otps_created_at ON owner_otps(created_at);

-- ============================================
-- 2. ALTER rooms (status change timestamp)
-- ============================================
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='rooms' AND column_name='status_changed_at') THEN
    ALTER TABLE rooms ADD COLUMN status_changed_at TIMESTAMP;
  END IF;
END $$;

-- ============================================
-- 3. ALTER fraud_alerts (review note)
-- ============================================
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='fraud_alerts' AND column_name='review_note') THEN
    ALTER TABLE fraud_alerts ADD COLUMN review_note VARCHAR(500);
  END IF;
END $$;

COMMIT;

-- ============================================
-- VERIFICATION (run manually if desired)
-- ============================================
-- SELECT table_name FROM information_schema.tables WHERE table_name='owner_otps';
-- SELECT column_name FROM information_schema.columns
--   WHERE table_name='rooms' AND column_name='status_changed_at';
-- SELECT column_name FROM information_schema.columns
--   WHERE table_name='fraud_alerts' AND column_name='review_note';
