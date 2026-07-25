-- Migration: 016_compliance_india.sql
-- Description: India compliance / Tally / GST e-invoicing / admin 2FA (prompt 18, slices 1-6).
-- Purpose:
--   1. users           -> admin TOTP 2FA columns (totp_secret, totp_enabled, totp_enrolled_at).
--   2. guest_profiles  -> Form C / FRRO columns (nationality, passport/visa masked, arrived_from, ...).
--   3. e_invoices      -> GST e-invoice (IRN) rows, one per Invoice (pluggable, stub-by-default).
--   4. app_settings    -> seed police/FRRO + Tally ledger + 2FA business config (never overwrites).
-- All tables/columns are ALSO auto-created by SQLAlchemy create_all; included here for prod parity via psql.
-- Database: PostgreSQL
-- Idempotent: YES (IF NOT EXISTS + ON CONFLICT DO NOTHING)

BEGIN;

-- ============================================
-- 1. users: admin 2FA / TOTP (additive)
-- ============================================
ALTER TABLE users ADD COLUMN IF NOT EXISTS totp_secret      VARCHAR(64);
ALTER TABLE users ADD COLUMN IF NOT EXISTS totp_enabled     BOOLEAN DEFAULT FALSE;
ALTER TABLE users ADD COLUMN IF NOT EXISTS totp_enrolled_at TIMESTAMP;
CREATE INDEX IF NOT EXISTS idx_users_totp_enabled ON users(totp_enabled);

-- ============================================
-- 2. guest_profiles: Form C / FRRO (additive)
-- ============================================
ALTER TABLE guest_profiles ADD COLUMN IF NOT EXISTS nationality             VARCHAR(60);
ALTER TABLE guest_profiles ADD COLUMN IF NOT EXISTS is_foreign_national     BOOLEAN DEFAULT FALSE;
ALTER TABLE guest_profiles ADD COLUMN IF NOT EXISTS passport_number_masked  VARCHAR(30);
ALTER TABLE guest_profiles ADD COLUMN IF NOT EXISTS passport_place_of_issue VARCHAR(80);
ALTER TABLE guest_profiles ADD COLUMN IF NOT EXISTS passport_expiry         DATE;
ALTER TABLE guest_profiles ADD COLUMN IF NOT EXISTS visa_number_masked      VARCHAR(30);
ALTER TABLE guest_profiles ADD COLUMN IF NOT EXISTS visa_type               VARCHAR(40);
ALTER TABLE guest_profiles ADD COLUMN IF NOT EXISTS visa_expiry             DATE;
ALTER TABLE guest_profiles ADD COLUMN IF NOT EXISTS arrived_from            VARCHAR(120);
ALTER TABLE guest_profiles ADD COLUMN IF NOT EXISTS next_destination        VARCHAR(120);
CREATE INDEX IF NOT EXISTS idx_guest_profiles_foreign ON guest_profiles(is_foreign_national);

-- ============================================
-- 3. e_invoices (GST IRN per Invoice)
-- ============================================
CREATE TABLE IF NOT EXISTS e_invoices (
    id               SERIAL PRIMARY KEY,
    invoice_id       INTEGER NOT NULL UNIQUE REFERENCES invoices(id),
    irn              VARCHAR(80),
    ack_no           VARCHAR(40),
    ack_date         VARCHAR(30),
    signed_qr        TEXT,
    status           VARCHAR(12) NOT NULL DEFAULT 'stub',
    request_payload  TEXT,
    response_payload TEXT,
    error            VARCHAR(300),
    created_by       INTEGER REFERENCES users(user_id),
    created_at       TIMESTAMP DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_e_invoices_invoice ON e_invoices(invoice_id);
CREATE INDEX IF NOT EXISTS idx_e_invoices_irn     ON e_invoices(irn);
CREATE INDEX IF NOT EXISTS idx_e_invoices_status  ON e_invoices(status);

-- ============================================
-- 4. app_settings: compliance config seeds (never overwrites an admin edit)
-- ============================================
INSERT INTO app_settings (key, value) VALUES
    ('police_station_name',      ''),
    ('police_station_code',      ''),
    ('frro_office',              ''),
    ('hotel_registration_no',    ''),
    ('tally_company_name',       'Hotel Bhimas'),
    ('tally_sales_ledger',       'Room Sales'),
    ('tally_cgst_ledger',        'CGST Output'),
    ('tally_sgst_ledger',        'SGST Output'),
    ('tally_cash_ledger',        'Cash'),
    ('tally_bank_ledger',        'Bank'),
    ('tally_debtors_ledger',     'Sundry Debtors'),
    ('admin_2fa_required',       'false'),
    ('admin_idle_logout_minutes','15')
ON CONFLICT (key) DO NOTHING;

COMMIT;

-- ============================================
-- VERIFICATION (run manually if desired)
-- ============================================
-- SELECT column_name FROM information_schema.columns WHERE table_name='users' AND column_name LIKE 'totp%';
-- SELECT column_name FROM information_schema.columns WHERE table_name='guest_profiles' AND column_name IN
--   ('nationality','is_foreign_national','passport_number_masked','visa_number_masked','arrived_from','next_destination');
-- SELECT count(*) FROM e_invoices;
-- SELECT key, value FROM app_settings WHERE key LIKE 'tally_%' OR key LIKE 'police_%' OR key LIKE 'admin_2fa%';
