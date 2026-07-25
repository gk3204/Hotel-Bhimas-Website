-- Migration: 010_cash_shift.sql
-- Description: Cash & shift management (prompt 12). Adds a generic runtime settings store
--              (first live-editable config) + supporting columns on cash_shifts / expenses.
-- Purpose:
--   1. app_settings                -> generic key/value config an admin can change at runtime
--                                     (seeded with cash-shift cycle + variance threshold)
--   2. cash_shifts.denominations   -> JSON text of the note/coin count entered at close
--   3. cash_shifts.close_note      -> optional note recorded at close
--   4. expenses.client_ref         -> idempotent double-submit guard for expense POSTs
-- The cash_shifts / expenses TABLES themselves already exist (prompt 01 / migration 003);
-- this only adds columns. All new tables/columns are ALSO auto-created by SQLAlchemy
-- create_all; included here for explicit prod parity / running against a deployed DB via psql.
-- Database: PostgreSQL
-- Idempotent: YES (information_schema checks + IF NOT EXISTS + ON CONFLICT DO NOTHING)

BEGIN;

-- ============================================
-- 1. app_settings (generic runtime config)
-- ============================================
CREATE TABLE IF NOT EXISTS app_settings (
    key        VARCHAR(60) PRIMARY KEY,
    value      TEXT,
    updated_by INTEGER REFERENCES users(user_id),
    updated_at TIMESTAMP
);

-- Seed cash-shift config defaults (only if absent — never overwrites an admin's value).
INSERT INTO app_settings (key, value) VALUES
    ('cash_cycle', 'shift'),
    ('cash_variance_threshold', '100'),
    ('cash_variance_alert_enabled', 'true')
ON CONFLICT (key) DO NOTHING;

-- ============================================
-- 2/3. ALTER cash_shifts (denominations + close note)
-- ============================================
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='cash_shifts' AND column_name='denominations') THEN
    ALTER TABLE cash_shifts ADD COLUMN denominations TEXT;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='cash_shifts' AND column_name='close_note') THEN
    ALTER TABLE cash_shifts ADD COLUMN close_note VARCHAR(300);
  END IF;
END $$;

-- ============================================
-- 4. ALTER expenses (idempotency client_ref)
-- ============================================
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='expenses' AND column_name='client_ref') THEN
    ALTER TABLE expenses ADD COLUMN client_ref VARCHAR(80);
    -- unique when present (multiple NULLs allowed by Postgres); guards double-submit
    CREATE UNIQUE INDEX IF NOT EXISTS idx_expenses_client_ref ON expenses(client_ref);
  END IF;
END $$;

COMMIT;

-- ============================================
-- VERIFICATION (run manually if desired)
-- ============================================
-- SELECT key, value FROM app_settings ORDER BY key;
-- SELECT column_name FROM information_schema.columns
--   WHERE table_name='cash_shifts' AND column_name IN ('denominations','close_note');
-- SELECT column_name FROM information_schema.columns
--   WHERE table_name='expenses' AND column_name='client_ref';
