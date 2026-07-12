-- Migration: 003_pms_foundation.sql
-- Description: PMS foundation (Milestone 0, prompt 01) — staff fields, audit columns,
--              and the core hotel-operations tables the reception desktop app + admin share.
-- Purpose:
--   1. users            -> staff fields (full_name, is_active, pin, last_login)
--   2. audit_logs       -> append-only audit columns (entity_type/id, before/after, ip, client)
--   3. card_issuances   -> every key card cut (anti-fraud backbone)
--   4. folios           -> per-stay bill (one open folio per in-house booking)
--   5. folio_charges    -> folio line items (room/food/misc/tax/discount/payment)
--   6. cash_shifts      -> cash-drawer sessions for reconciliation
--   7. expenses         -> petty-cash outflows against a shift
--   8. fraud_alerts     -> flagged anomalies for owner/admin review
-- All new tables are ALSO auto-created by SQLAlchemy create_all; included here for
-- explicit prod parity / running against an existing deployed DB via psql.
-- Database: PostgreSQL
-- Idempotent: YES (information_schema checks + IF NOT EXISTS)

BEGIN;

-- ============================================
-- 1. ALTER users TABLE (staff fields)
-- ============================================
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='users' AND column_name='full_name') THEN
    ALTER TABLE users ADD COLUMN full_name VARCHAR(100);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='users' AND column_name='is_active') THEN
    ALTER TABLE users ADD COLUMN is_active BOOLEAN DEFAULT TRUE;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='users' AND column_name='pin') THEN
    ALTER TABLE users ADD COLUMN pin VARCHAR(10);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='users' AND column_name='last_login') THEN
    ALTER TABLE users ADD COLUMN last_login TIMESTAMP;
  END IF;
END $$;
CREATE INDEX IF NOT EXISTS idx_users_is_active ON users(is_active);

-- ============================================
-- 2. ALTER audit_logs TABLE (append-only detail columns)
-- ============================================
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='audit_logs' AND column_name='entity_type') THEN
    ALTER TABLE audit_logs ADD COLUMN entity_type VARCHAR(50);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='audit_logs' AND column_name='entity_id') THEN
    ALTER TABLE audit_logs ADD COLUMN entity_id VARCHAR(50);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='audit_logs' AND column_name='before') THEN
    ALTER TABLE audit_logs ADD COLUMN before TEXT;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='audit_logs' AND column_name='after') THEN
    ALTER TABLE audit_logs ADD COLUMN after TEXT;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='audit_logs' AND column_name='ip') THEN
    ALTER TABLE audit_logs ADD COLUMN ip VARCHAR(50);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='audit_logs' AND column_name='client') THEN
    ALTER TABLE audit_logs ADD COLUMN client VARCHAR(20);
  END IF;
END $$;
CREATE INDEX IF NOT EXISTS idx_audit_entity_type ON audit_logs(entity_type);
CREATE INDEX IF NOT EXISTS idx_audit_entity_id ON audit_logs(entity_id);

-- ============================================
-- 3. card_issuances
-- ============================================
CREATE TABLE IF NOT EXISTS card_issuances (
    id          SERIAL PRIMARY KEY,
    booking_id  INTEGER REFERENCES bookings(booking_id),
    room_id     INTEGER REFERENCES rooms(room_id),
    card_uid    VARCHAR(64),
    card_type   VARCHAR(20) NOT NULL DEFAULT 'guest',
    room_code   VARCHAR(16),
    valid_from  TIMESTAMP,
    valid_to    TIMESTAMP,
    issued_by   INTEGER REFERENCES users(user_id),
    station_id  VARCHAR(50),
    issued_at   TIMESTAMP DEFAULT now(),
    status      VARCHAR(20) NOT NULL DEFAULT 'active'
);
CREATE INDEX IF NOT EXISTS idx_card_issuances_booking ON card_issuances(booking_id);
CREATE INDEX IF NOT EXISTS idx_card_issuances_room ON card_issuances(room_id);
CREATE INDEX IF NOT EXISTS idx_card_issuances_uid ON card_issuances(card_uid);
CREATE INDEX IF NOT EXISTS idx_card_issuances_type ON card_issuances(card_type);
CREATE INDEX IF NOT EXISTS idx_card_issuances_status ON card_issuances(status);
CREATE INDEX IF NOT EXISTS idx_card_issuances_issued_at ON card_issuances(issued_at);

-- ============================================
-- 4. folios
-- ============================================
CREATE TABLE IF NOT EXISTS folios (
    id          SERIAL PRIMARY KEY,
    booking_id  INTEGER NOT NULL UNIQUE REFERENCES bookings(booking_id),
    status      VARCHAR(20) NOT NULL DEFAULT 'open',
    total       NUMERIC(10, 2) DEFAULT 0,
    balance     NUMERIC(10, 2) DEFAULT 0,
    opened_at   TIMESTAMP DEFAULT now(),
    settled_at  TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_folios_status ON folios(status);

-- ============================================
-- 5. folio_charges
-- ============================================
CREATE TABLE IF NOT EXISTS folio_charges (
    id          SERIAL PRIMARY KEY,
    folio_id    INTEGER NOT NULL REFERENCES folios(id),
    type        VARCHAR(20) NOT NULL,
    description VARCHAR(200),
    qty         NUMERIC(10, 2) DEFAULT 1,
    unit_price  NUMERIC(10, 2) DEFAULT 0,
    amount      NUMERIC(10, 2) NOT NULL DEFAULT 0,
    gst_percent DOUBLE PRECISION,
    posted_by   INTEGER REFERENCES users(user_id),
    posted_at   TIMESTAMP DEFAULT now(),
    void        BOOLEAN DEFAULT FALSE,
    void_reason VARCHAR(200)
);
CREATE INDEX IF NOT EXISTS idx_folio_charges_folio ON folio_charges(folio_id);
CREATE INDEX IF NOT EXISTS idx_folio_charges_type ON folio_charges(type);
CREATE INDEX IF NOT EXISTS idx_folio_charges_posted_at ON folio_charges(posted_at);
CREATE INDEX IF NOT EXISTS idx_folio_charges_void ON folio_charges(void);

-- ============================================
-- 6. cash_shifts
-- ============================================
CREATE TABLE IF NOT EXISTS cash_shifts (
    id               SERIAL PRIMARY KEY,
    staff_id         INTEGER NOT NULL REFERENCES users(user_id),
    station_id       VARCHAR(50),
    period           VARCHAR(10) NOT NULL DEFAULT 'shift',
    opening_balance  NUMERIC(10, 2) DEFAULT 0,
    collections_cash NUMERIC(10, 2) DEFAULT 0,
    expenses_total   NUMERIC(10, 2) DEFAULT 0,
    payouts_total    NUMERIC(10, 2) DEFAULT 0,
    expected_cash    NUMERIC(10, 2),
    counted_cash     NUMERIC(10, 2),
    variance         NUMERIC(10, 2),
    status           VARCHAR(10) NOT NULL DEFAULT 'open',
    opened_at        TIMESTAMP DEFAULT now(),
    closed_by        INTEGER REFERENCES users(user_id),
    closed_at        TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_cash_shifts_staff ON cash_shifts(staff_id);
CREATE INDEX IF NOT EXISTS idx_cash_shifts_status ON cash_shifts(status);
CREATE INDEX IF NOT EXISTS idx_cash_shifts_opened_at ON cash_shifts(opened_at);

-- ============================================
-- 7. expenses
-- ============================================
CREATE TABLE IF NOT EXISTS expenses (
    id          SERIAL PRIMARY KEY,
    shift_id    INTEGER REFERENCES cash_shifts(id),
    category    VARCHAR(30),
    description VARCHAR(200),
    amount      NUMERIC(10, 2) NOT NULL DEFAULT 0,
    receipt_url TEXT,
    created_by  INTEGER REFERENCES users(user_id),
    created_at  TIMESTAMP DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_expenses_shift ON expenses(shift_id);
CREATE INDEX IF NOT EXISTS idx_expenses_created_by ON expenses(created_by);
CREATE INDEX IF NOT EXISTS idx_expenses_created_at ON expenses(created_at);

-- ============================================
-- 8. fraud_alerts
-- ============================================
CREATE TABLE IF NOT EXISTS fraud_alerts (
    id          SERIAL PRIMARY KEY,
    type        VARCHAR(50) NOT NULL,
    severity    VARCHAR(10) NOT NULL DEFAULT 'med',
    booking_id  INTEGER REFERENCES bookings(booking_id),
    room_id     INTEGER REFERENCES rooms(room_id),
    card_id     INTEGER REFERENCES card_issuances(id),
    detail      TEXT,
    detected_at TIMESTAMP DEFAULT now(),
    status      VARCHAR(20) NOT NULL DEFAULT 'open',
    reviewed_by INTEGER REFERENCES users(user_id),
    reviewed_at TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_fraud_alerts_type ON fraud_alerts(type);
CREATE INDEX IF NOT EXISTS idx_fraud_alerts_severity ON fraud_alerts(severity);
CREATE INDEX IF NOT EXISTS idx_fraud_alerts_booking ON fraud_alerts(booking_id);
CREATE INDEX IF NOT EXISTS idx_fraud_alerts_room ON fraud_alerts(room_id);
CREATE INDEX IF NOT EXISTS idx_fraud_alerts_status ON fraud_alerts(status);
CREATE INDEX IF NOT EXISTS idx_fraud_alerts_detected_at ON fraud_alerts(detected_at);

COMMIT;

-- ============================================
-- VERIFICATION (run manually if desired)
-- ============================================
/*
SELECT column_name, data_type FROM information_schema.columns
WHERE table_name='users' AND column_name IN ('full_name','is_active','pin','last_login');

SELECT table_name FROM information_schema.tables
WHERE table_name IN ('card_issuances','folios','folio_charges','cash_shifts','expenses','fraud_alerts')
ORDER BY table_name;
*/
