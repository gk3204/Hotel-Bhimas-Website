-- Migration: 019_backoffice.sql
-- Description: Back-office round-2 batch (prompt 18, slices 7 / 13 / 12 / 9).
-- Purpose:
--   1. companies / company_ledger / company_invoices -> corporate "bill to company" accounts:
--      a credit-limited city ledger that stays transfer into, billed once a period with a
--      consolidated GST invoice. Invoice numbers reuse the EXISTING invoice_counters table
--      under a distinct FY prefix ('C2026-27') so guest + company series never collide.
--   2. bookings / folios / folio_charges / invoices -> additive bill-to routing columns.
--      folio_charges.bill_to IS the split folio: one folio, each line routed to whoever pays.
--      folios.total/balance keep their ORIGINAL whole-folio meaning (reports, night audit and
--      the desktop are untouched); company_total/company_balance mirror the company subset.
--   3. vendors / vendor_contracts -> vendor + AMC tracking with renewal reminders.
--      expenses.vendor_id attributes petty-cash outflow to a vendor (prompt 12 ledger).
--   4. staff_shifts / staff_attendance -> duty roster + clock-in/out.
--      cash_shifts.roster_shift_id links the drawer actually opened to the duty planned.
--      users.staff_card_uid is the ENCODER SEAM for staff-card attendance (op still pending a
--      live capture, phase0/CAPTURE-PLAYBOOK.md; the PIN + card endpoints work without it).
--   5. app_settings -> seed back-office config. Never overwrites an admin edit.
-- New tables are ALSO auto-created by SQLAlchemy create_all; included here for prod parity via psql.
-- Database: PostgreSQL
-- Idempotent: YES (IF NOT EXISTS / ADD COLUMN IF NOT EXISTS / ON CONFLICT DO NOTHING)

BEGIN;

-- ============================================
-- 1. companies (new table)
-- ============================================
CREATE TABLE IF NOT EXISTS companies (
    id              SERIAL PRIMARY KEY,
    name            VARCHAR(150) NOT NULL UNIQUE,
    gstin           VARCHAR(20),
    address         VARCHAR(300),
    city            VARCHAR(80),
    state           VARCHAR(80),
    state_code      VARCHAR(4),
    contact_person  VARCHAR(100),
    phone           VARCHAR(20),
    email           VARCHAR(120),
    credit_limit    NUMERIC(12, 2) NOT NULL DEFAULT 0,
    credit_days     INTEGER NOT NULL DEFAULT 30,
    payment_terms   VARCHAR(120),
    is_active       BOOLEAN DEFAULT TRUE,
    notes           TEXT,
    created_by      INTEGER REFERENCES users(user_id),
    created_at      TIMESTAMP DEFAULT now(),
    updated_by      INTEGER REFERENCES users(user_id),
    updated_at      TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_companies_name       ON companies(name);
CREATE INDEX IF NOT EXISTS idx_companies_gstin      ON companies(gstin);
CREATE INDEX IF NOT EXISTS idx_companies_is_active  ON companies(is_active);
CREATE INDEX IF NOT EXISTS idx_companies_created_at ON companies(created_at);

-- ============================================
-- 2. company_invoices (new table) -- created before company_ledger (FK target)
-- ============================================
CREATE TABLE IF NOT EXISTS company_invoices (
    id             SERIAL PRIMARY KEY,
    company_id     INTEGER NOT NULL REFERENCES companies(id),
    invoice_no     VARCHAR(30) NOT NULL UNIQUE,
    fy_label       VARCHAR(10) NOT NULL,
    seq            INTEGER NOT NULL,
    invoice_date   DATE NOT NULL,
    period_from    DATE NOT NULL,
    period_to      DATE NOT NULL,
    taxable_total  NUMERIC(12, 2) DEFAULT 0,
    cgst_total     NUMERIC(12, 2) DEFAULT 0,
    sgst_total     NUMERIC(12, 2) DEFAULT 0,
    grand_total    NUMERIC(12, 2) DEFAULT 0,
    paid_total     NUMERIC(12, 2) DEFAULT 0,
    gst_breakup    TEXT,
    stay_count     INTEGER DEFAULT 0,
    status         VARCHAR(12) NOT NULL DEFAULT 'issued',
    notes          VARCHAR(300),
    created_by     INTEGER REFERENCES users(user_id),
    created_at     TIMESTAMP DEFAULT now(),
    cancelled_at   TIMESTAMP,
    cancel_reason  VARCHAR(300)
);
CREATE INDEX IF NOT EXISTS idx_company_invoices_company    ON company_invoices(company_id);
CREATE INDEX IF NOT EXISTS idx_company_invoices_no         ON company_invoices(invoice_no);
CREATE INDEX IF NOT EXISTS idx_company_invoices_fy         ON company_invoices(fy_label);
CREATE INDEX IF NOT EXISTS idx_company_invoices_status     ON company_invoices(status);
CREATE INDEX IF NOT EXISTS idx_company_invoices_created_at ON company_invoices(created_at);

-- ============================================
-- 3. company_ledger (new table) -- APPEND-ONLY city ledger
--    amount > 0 = company owes more; amount < 0 = company paid / was credited.
-- ============================================
CREATE TABLE IF NOT EXISTS company_ledger (
    id                 SERIAL PRIMARY KEY,
    company_id         INTEGER NOT NULL REFERENCES companies(id),
    type               VARCHAR(12) NOT NULL,
    description        VARCHAR(300),
    amount             NUMERIC(12, 2) NOT NULL DEFAULT 0,
    balance_after      NUMERIC(12, 2),
    booking_id         INTEGER REFERENCES bookings(booking_id),
    folio_id           INTEGER REFERENCES folios(id),
    company_invoice_id INTEGER REFERENCES company_invoices(id),
    method             VARCHAR(20),
    reference          VARCHAR(120),
    client_ref         VARCHAR(80) UNIQUE,
    created_by         INTEGER REFERENCES users(user_id),
    created_at         TIMESTAMP DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_company_ledger_company    ON company_ledger(company_id);
CREATE INDEX IF NOT EXISTS idx_company_ledger_type       ON company_ledger(type);
CREATE INDEX IF NOT EXISTS idx_company_ledger_booking    ON company_ledger(booking_id);
CREATE INDEX IF NOT EXISTS idx_company_ledger_folio      ON company_ledger(folio_id);
CREATE INDEX IF NOT EXISTS idx_company_ledger_cinvoice   ON company_ledger(company_invoice_id);
CREATE INDEX IF NOT EXISTS idx_company_ledger_created_by ON company_ledger(created_by);
CREATE INDEX IF NOT EXISTS idx_company_ledger_created_at ON company_ledger(created_at);

-- ============================================
-- 4. vendors (new table)
-- ============================================
CREATE TABLE IF NOT EXISTS vendors (
    id             SERIAL PRIMARY KEY,
    name           VARCHAR(150) NOT NULL UNIQUE,
    category       VARCHAR(20) NOT NULL DEFAULT 'other',
    contact_person VARCHAR(100),
    phone          VARCHAR(20),
    email          VARCHAR(120),
    gstin          VARCHAR(20),
    address        VARCHAR(300),
    is_active      BOOLEAN DEFAULT TRUE,
    notes          TEXT,
    created_by     INTEGER REFERENCES users(user_id),
    created_at     TIMESTAMP DEFAULT now(),
    updated_by     INTEGER REFERENCES users(user_id),
    updated_at     TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_vendors_name       ON vendors(name);
CREATE INDEX IF NOT EXISTS idx_vendors_category   ON vendors(category);
CREATE INDEX IF NOT EXISTS idx_vendors_is_active  ON vendors(is_active);
CREATE INDEX IF NOT EXISTS idx_vendors_created_at ON vendors(created_at);

-- ============================================
-- 5. vendor_contracts (new table)
-- ============================================
CREATE TABLE IF NOT EXISTS vendor_contracts (
    id                    SERIAL PRIMARY KEY,
    vendor_id             INTEGER NOT NULL REFERENCES vendors(id),
    title                 VARCHAR(200) NOT NULL,
    contract_type         VARCHAR(12) NOT NULL DEFAULT 'amc',
    start_date            DATE,
    end_date              DATE,
    renewal_reminder_days INTEGER NOT NULL DEFAULT 30,
    amount                NUMERIC(12, 2),
    billing_cycle         VARCHAR(12),
    document_url          TEXT,
    status                VARCHAR(12) NOT NULL DEFAULT 'active',
    last_reminder_sent_at TIMESTAMP,
    notes                 TEXT,
    created_by            INTEGER REFERENCES users(user_id),
    created_at            TIMESTAMP DEFAULT now(),
    updated_by            INTEGER REFERENCES users(user_id),
    updated_at            TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_vendor_contracts_vendor     ON vendor_contracts(vendor_id);
CREATE INDEX IF NOT EXISTS idx_vendor_contracts_type       ON vendor_contracts(contract_type);
CREATE INDEX IF NOT EXISTS idx_vendor_contracts_end_date   ON vendor_contracts(end_date);
CREATE INDEX IF NOT EXISTS idx_vendor_contracts_status     ON vendor_contracts(status);
CREATE INDEX IF NOT EXISTS idx_vendor_contracts_created_at ON vendor_contracts(created_at);

-- ============================================
-- 6. staff_shifts (new table) -- duty roster
-- ============================================
CREATE TABLE IF NOT EXISTS staff_shifts (
    id          SERIAL PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users(user_id),
    shift_date  DATE NOT NULL,
    shift_type  VARCHAR(10) NOT NULL DEFAULT 'general',
    start_time  TIME,
    end_time    TIME,
    role_label  VARCHAR(40),
    status      VARCHAR(10) NOT NULL DEFAULT 'planned',
    notes       VARCHAR(300),
    created_by  INTEGER REFERENCES users(user_id),
    created_at  TIMESTAMP DEFAULT now(),
    updated_by  INTEGER REFERENCES users(user_id),
    updated_at  TIMESTAMP,
    CONSTRAINT uq_staff_shift_slot UNIQUE (user_id, shift_date, shift_type)
);
CREATE INDEX IF NOT EXISTS idx_staff_shifts_user   ON staff_shifts(user_id);
CREATE INDEX IF NOT EXISTS idx_staff_shifts_date   ON staff_shifts(shift_date);
CREATE INDEX IF NOT EXISTS idx_staff_shifts_type   ON staff_shifts(shift_type);
CREATE INDEX IF NOT EXISTS idx_staff_shifts_status ON staff_shifts(status);

-- ============================================
-- 7. staff_attendance (new table) -- clock in/out
-- ============================================
CREATE TABLE IF NOT EXISTS staff_attendance (
    id              SERIAL PRIMARY KEY,
    user_id         INTEGER NOT NULL REFERENCES users(user_id),
    clock_in        TIMESTAMP NOT NULL,
    clock_out       TIMESTAMP,
    minutes_worked  INTEGER,
    source          VARCHAR(8) NOT NULL DEFAULT 'pin',
    station_id      VARCHAR(50),
    roster_shift_id INTEGER REFERENCES staff_shifts(id),
    card_uid        VARCHAR(40),
    note            VARCHAR(300),
    created_by      INTEGER REFERENCES users(user_id),
    created_at      TIMESTAMP DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_staff_attendance_user       ON staff_attendance(user_id);
CREATE INDEX IF NOT EXISTS idx_staff_attendance_clock_in   ON staff_attendance(clock_in);
CREATE INDEX IF NOT EXISTS idx_staff_attendance_clock_out  ON staff_attendance(clock_out);
CREATE INDEX IF NOT EXISTS idx_staff_attendance_source     ON staff_attendance(source);
CREATE INDEX IF NOT EXISTS idx_staff_attendance_roster     ON staff_attendance(roster_shift_id);
CREATE INDEX IF NOT EXISTS idx_staff_attendance_created_at ON staff_attendance(created_at);

-- ============================================
-- 8. Additive columns on EXISTING tables (never destructive)
-- ============================================
ALTER TABLE bookings      ADD COLUMN IF NOT EXISTS company_id INTEGER REFERENCES companies(id);
ALTER TABLE bookings      ADD COLUMN IF NOT EXISTS bill_to    VARCHAR(10) NOT NULL DEFAULT 'guest';
CREATE INDEX IF NOT EXISTS idx_bookings_company ON bookings(company_id);
CREATE INDEX IF NOT EXISTS idx_bookings_bill_to ON bookings(bill_to);

ALTER TABLE folios        ADD COLUMN IF NOT EXISTS company_id      INTEGER REFERENCES companies(id);
ALTER TABLE folios        ADD COLUMN IF NOT EXISTS company_total   NUMERIC(10, 2) DEFAULT 0;
ALTER TABLE folios        ADD COLUMN IF NOT EXISTS company_balance NUMERIC(10, 2) DEFAULT 0;
CREATE INDEX IF NOT EXISTS idx_folios_company ON folios(company_id);

ALTER TABLE folio_charges ADD COLUMN IF NOT EXISTS bill_to VARCHAR(10) NOT NULL DEFAULT 'guest';
CREATE INDEX IF NOT EXISTS idx_folio_charges_bill_to ON folio_charges(bill_to);

ALTER TABLE invoices      ADD COLUMN IF NOT EXISTS company_id         INTEGER REFERENCES companies(id);
ALTER TABLE invoices      ADD COLUMN IF NOT EXISTS company_invoice_id INTEGER REFERENCES company_invoices(id);
CREATE INDEX IF NOT EXISTS idx_invoices_company  ON invoices(company_id);
CREATE INDEX IF NOT EXISTS idx_invoices_cinvoice ON invoices(company_invoice_id);

ALTER TABLE expenses      ADD COLUMN IF NOT EXISTS vendor_id INTEGER REFERENCES vendors(id);
CREATE INDEX IF NOT EXISTS idx_expenses_vendor ON expenses(vendor_id);

ALTER TABLE cash_shifts   ADD COLUMN IF NOT EXISTS roster_shift_id INTEGER REFERENCES staff_shifts(id);
CREATE INDEX IF NOT EXISTS idx_cash_shifts_roster ON cash_shifts(roster_shift_id);

ALTER TABLE users         ADD COLUMN IF NOT EXISTS staff_card_uid VARCHAR(40);
CREATE INDEX IF NOT EXISTS idx_users_staff_card_uid ON users(staff_card_uid);

-- ============================================
-- 9. app_settings: back-office config seeds (never overwrites an admin edit)
--    Corporate credit is OFF-by-default in the sense that a company with credit_limit 0 can
--    take no credit; company_credit_block=true refuses an over-limit routing (admin override
--    is still possible, with a reason, and is audited).
-- ============================================
INSERT INTO app_settings (key, value) VALUES
    ('company_credit_block',            'true'),
    ('company_default_credit_days',     '30'),
    ('company_invoice_prefix',          'CINV'),
    ('vendor_renewal_alerts_enabled',   'true'),
    ('vendor_renewal_lead_days',        '30'),
    ('roster_default_shift_type',       'general'),
    ('attendance_pin_enabled',          'true'),
    ('attendance_auto_close_hours',     '16')
ON CONFLICT (key) DO NOTHING;

COMMIT;

-- ============================================
-- VERIFICATION (run manually if desired)
-- ============================================
-- SELECT column_name FROM information_schema.columns WHERE table_name='companies' ORDER BY ordinal_position;
-- SELECT column_name FROM information_schema.columns WHERE table_name='company_ledger' ORDER BY ordinal_position;
-- SELECT column_name FROM information_schema.columns WHERE table_name='staff_attendance' ORDER BY ordinal_position;
-- SELECT column_name FROM information_schema.columns WHERE table_name='folio_charges' AND column_name='bill_to';
-- SELECT column_name FROM information_schema.columns WHERE table_name='bookings' AND column_name IN ('company_id','bill_to');
-- SELECT key, value FROM app_settings WHERE key LIKE 'company_%' OR key LIKE 'vendor_%' OR key LIKE 'roster_%' OR key LIKE 'attendance_%' ORDER BY key;
