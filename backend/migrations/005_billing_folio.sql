-- Migration: 005_billing_folio.sql
-- Description: Billing & folio + GST invoices (Milestone 1, prompt 07).
-- Purpose:
--   folio_charges.reversal_of_id -> links a reversing entry to the voided original (never hard-delete)
--   invoices                     -> GST tax-invoice snapshot per folio (sequential INV/<FY>/<seq>)
--   invoice_counters             -> per-financial-year sequence, allocated atomically via upsert
-- (Also auto-created by SQLAlchemy create_all; included for prod parity via psql.)
-- Database: PostgreSQL
-- Idempotent: YES (information_schema checks + IF NOT EXISTS)

BEGIN;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='folio_charges' AND column_name='reversal_of_id') THEN
    ALTER TABLE folio_charges ADD COLUMN reversal_of_id INTEGER REFERENCES folio_charges(id);
  END IF;
END $$;
CREATE INDEX IF NOT EXISTS idx_folio_charges_reversal_of ON folio_charges(reversal_of_id);

CREATE TABLE IF NOT EXISTS invoices (
    id            SERIAL PRIMARY KEY,
    folio_id      INTEGER NOT NULL UNIQUE REFERENCES folios(id),
    booking_id    INTEGER NOT NULL REFERENCES bookings(booking_id),
    invoice_no    VARCHAR(30) NOT NULL UNIQUE,
    fy_label      VARCHAR(10) NOT NULL,
    seq           INTEGER NOT NULL,
    invoice_date  DATE NOT NULL,
    taxable_total NUMERIC(10, 2) DEFAULT 0,
    cgst_total    NUMERIC(10, 2) DEFAULT 0,
    sgst_total    NUMERIC(10, 2) DEFAULT 0,
    grand_total   NUMERIC(10, 2) DEFAULT 0,
    balance_due   NUMERIC(10, 2) DEFAULT 0,
    gst_breakup   VARCHAR,
    created_by    INTEGER REFERENCES users(user_id),
    created_at    TIMESTAMP DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_invoices_booking ON invoices(booking_id);
CREATE INDEX IF NOT EXISTS idx_invoices_fy ON invoices(fy_label);

CREATE TABLE IF NOT EXISTS invoice_counters (
    fy_label VARCHAR(10) PRIMARY KEY,
    last_seq INTEGER NOT NULL DEFAULT 0
);

COMMIT;

-- ============================================
-- VERIFICATION (run manually if desired)
-- ============================================
/*
SELECT column_name FROM information_schema.columns
WHERE table_name='folio_charges' AND column_name='reversal_of_id';
SELECT table_name FROM information_schema.tables
WHERE table_name IN ('invoices', 'invoice_counters');
*/
