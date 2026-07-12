-- Migration: 006_frontdesk_checkin.sql
-- Description: Front desk check-in / check-out + card issuance (Milestone 1, prompt 06).
-- Purpose:
--   bookings.checked_in_at / checked_out_at -> timestamps for the checked_in / checked_out statuses
--   guests.id_type / id_number_masked       -> check-in KYC (masked; raw ID number never stored; photo = prompt 14)
--   payments.method / client_ref            -> minimal desk payments (cash|card|upi|bank) + offline outbox dedupe
--   card_issuances.issue_type / client_ref  -> checkin|extra|lost_reissue + offline outbox dedupe
-- (Also auto-created by SQLAlchemy create_all; included for prod parity via psql.)
-- Database: PostgreSQL
-- Idempotent: YES (information_schema checks + IF NOT EXISTS)

BEGIN;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='bookings' AND column_name='checked_in_at') THEN
    ALTER TABLE bookings ADD COLUMN checked_in_at TIMESTAMP;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='bookings' AND column_name='checked_out_at') THEN
    ALTER TABLE bookings ADD COLUMN checked_out_at TIMESTAMP;
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='guests' AND column_name='id_type') THEN
    ALTER TABLE guests ADD COLUMN id_type VARCHAR(20);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='guests' AND column_name='id_number_masked') THEN
    ALTER TABLE guests ADD COLUMN id_number_masked VARCHAR(30);
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='payments' AND column_name='method') THEN
    ALTER TABLE payments ADD COLUMN method VARCHAR(20);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='payments' AND column_name='client_ref') THEN
    ALTER TABLE payments ADD COLUMN client_ref VARCHAR(64);
  END IF;
END $$;
-- Unique (PG allows multiple NULLs) -> dedupes offline outbox re-flushes.
CREATE UNIQUE INDEX IF NOT EXISTS uq_payments_client_ref ON payments(client_ref);

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='card_issuances' AND column_name='issue_type') THEN
    ALTER TABLE card_issuances ADD COLUMN issue_type VARCHAR(20) NOT NULL DEFAULT 'checkin';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='card_issuances' AND column_name='client_ref') THEN
    ALTER TABLE card_issuances ADD COLUMN client_ref VARCHAR(64);
  END IF;
END $$;
CREATE UNIQUE INDEX IF NOT EXISTS uq_card_issuances_client_ref ON card_issuances(client_ref);

COMMIT;

-- ============================================
-- VERIFICATION (run manually if desired)
-- ============================================
/*
SELECT column_name FROM information_schema.columns
WHERE (table_name='bookings' AND column_name IN ('checked_in_at','checked_out_at'))
   OR (table_name='guests' AND column_name IN ('id_type','id_number_masked'))
   OR (table_name='payments' AND column_name IN ('method','client_ref'))
   OR (table_name='card_issuances' AND column_name IN ('issue_type','client_ref'));
SELECT indexname FROM pg_indexes
WHERE indexname IN ('uq_payments_client_ref','uq_card_issuances_client_ref');
*/
