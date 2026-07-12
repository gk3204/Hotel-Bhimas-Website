-- Migration: 007_payments_refunds.sql
-- Description: Payments & refunds at desk (Milestone 1, prompt 08).
-- Purpose:
--   payments.shift_id         -> open cash shift the CASH payment was collected in (prompt 12 reconciles; nullable, link-only)
--   payments.refund_shift_id  -> open cash shift a CASH refund was paid out of (prompt 12 reconciles; nullable, link-only)
--   payments.collected_by     -> desk user (users.user_id) who took the money, from the JWT
--   payments.refund_mode      -> how a desk refund went back to the guest: cash|upi|bank (razorpay refunds leave NULL)
--   payments.refund_reference -> UPI / bank reference of the refund payout
-- (Also auto-created by SQLAlchemy create_all; included for prod parity via psql.)
-- Database: PostgreSQL
-- Idempotent: YES (information_schema checks + IF NOT EXISTS)

BEGIN;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='payments' AND column_name='shift_id') THEN
    ALTER TABLE payments ADD COLUMN shift_id INTEGER REFERENCES cash_shifts(id);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='payments' AND column_name='refund_shift_id') THEN
    ALTER TABLE payments ADD COLUMN refund_shift_id INTEGER REFERENCES cash_shifts(id);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='payments' AND column_name='collected_by') THEN
    ALTER TABLE payments ADD COLUMN collected_by INTEGER REFERENCES users(user_id);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='payments' AND column_name='refund_mode') THEN
    ALTER TABLE payments ADD COLUMN refund_mode VARCHAR(20);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='payments' AND column_name='refund_reference') THEN
    ALTER TABLE payments ADD COLUMN refund_reference VARCHAR(100);
  END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_payments_shift ON payments(shift_id);

COMMIT;

-- ============================================
-- VERIFICATION (run manually if desired)
-- ============================================
/*
SELECT column_name FROM information_schema.columns
WHERE table_name='payments'
  AND column_name IN ('shift_id','refund_shift_id','collected_by','refund_mode','refund_reference');
SELECT indexname FROM pg_indexes WHERE indexname = 'idx_payments_shift';
*/
