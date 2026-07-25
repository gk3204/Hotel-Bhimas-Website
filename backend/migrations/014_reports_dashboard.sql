-- Migration: 014_reports_dashboard.sql
-- Description: Reports & Dashboard + automated night-audit / day-close (prompt 16).
-- Purpose:
--   1. day_close_summaries -> one persisted end-of-day snapshot per business date
--      (occupancy, sales, tax, cash) written by the night-audit routine (idempotent UPSERT).
--   2. app_settings seed  -> business_date (rolling), night_audit_hour (IST), night_audit_enabled.
-- The reporting endpoints (routers/reports.py) are pure read models over existing tables and
-- add NO schema of their own. day_close_summaries is ALSO auto-created by SQLAlchemy create_all;
-- included here for explicit prod parity / running against a deployed DB via psql.
-- Database: PostgreSQL
-- Idempotent: YES (IF NOT EXISTS + ON CONFLICT DO NOTHING)

BEGIN;

-- ============================================
-- 1. day_close_summaries (persisted day-close snapshot)
-- ============================================
CREATE TABLE IF NOT EXISTS day_close_summaries (
    id             SERIAL PRIMARY KEY,
    business_date  DATE NOT NULL UNIQUE,
    rooms_total    INTEGER,
    rooms_occupied INTEGER,
    occupancy_pct  DOUBLE PRECISION,
    room_revenue   NUMERIC(12, 2),
    other_revenue  NUMERIC(12, 2),
    total_sales    NUMERIC(12, 2),
    taxable_total  NUMERIC(12, 2),
    cgst_total     NUMERIC(12, 2),
    sgst_total     NUMERIC(12, 2),
    cash_collected NUMERIC(12, 2),
    cash_expected  NUMERIC(12, 2),
    cash_variance  NUMERIC(12, 2),
    arrivals       INTEGER,
    departures     INTEGER,
    open_alerts    INTEGER,
    folios_posted  INTEGER,
    generated_by   VARCHAR(20),
    generated_at   TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_day_close_business_date ON day_close_summaries(business_date);

-- ============================================
-- 2. Seed reports / night-audit config (only if absent — never overwrites an admin's value)
-- ============================================
INSERT INTO app_settings (key, value) VALUES
    ('business_date', ''),
    ('night_audit_hour', '3'),
    ('night_audit_enabled', 'true')
ON CONFLICT (key) DO NOTHING;

COMMIT;

-- ============================================
-- VERIFICATION (run manually if desired)
-- ============================================
-- SELECT count(*) FROM day_close_summaries;
-- SELECT key, value FROM app_settings WHERE key IN ('business_date','night_audit_hour','night_audit_enabled');
