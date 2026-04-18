psql -U postgres -d hotel_bhimas -c "
SELECT column_name, data_type 
FROM information_schema.columns 
WHERE table_name = 'bookings' 
  AND column_name IN ('admin_cancelled', 'admin_cancelled_reason', 'admin_notes', 'admin_cancelled_by', 'admin_cancelled_at')
ORDER BY ordinal_position;
"-- Migration: 001_admin_cancellation.sql
-- Description: Add admin cancellation tracking and refund management columns
-- Created: 2024-04-17
-- Purpose: Support admin-initiated booking cancellation with Razorpay refund processing
-- Database: PostgreSQL
-- Idempotent: YES (uses information_schema checks)

-- ============================================
-- 1. ALTER Bookings TABLE
-- ============================================
-- Add columns to track admin-initiated cancellations with full audit trail

BEGIN;

-- Check if columns exist before adding (PostgreSQL compatible)
DO $$ 
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns 
    WHERE table_name='bookings' AND column_name='admin_cancelled'
  ) THEN
    ALTER TABLE bookings ADD COLUMN admin_cancelled BOOLEAN DEFAULT FALSE;
    COMMENT ON COLUMN bookings.admin_cancelled IS 'Flag distinguishing admin cancellations from user cancellations';
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns 
    WHERE table_name='bookings' AND column_name='admin_cancelled_reason'
  ) THEN
    ALTER TABLE bookings ADD COLUMN admin_cancelled_reason VARCHAR(100);
    COMMENT ON COLUMN bookings.admin_cancelled_reason IS 'Reason: Double booking error | Guest request | Technical issue | Other';
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns 
    WHERE table_name='bookings' AND column_name='admin_notes'
  ) THEN
    ALTER TABLE bookings ADD COLUMN admin_notes VARCHAR(500);
    COMMENT ON COLUMN bookings.admin_notes IS 'Optional admin notes about the cancellation';
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns 
    WHERE table_name='bookings' AND column_name='admin_cancelled_by'
  ) THEN
    ALTER TABLE bookings ADD COLUMN admin_cancelled_by VARCHAR(50);
    COMMENT ON COLUMN bookings.admin_cancelled_by IS 'Admin username/ID who performed cancellation';
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns 
    WHERE table_name='bookings' AND column_name='admin_cancelled_at'
  ) THEN
    ALTER TABLE bookings ADD COLUMN admin_cancelled_at TIMESTAMP;
    COMMENT ON COLUMN bookings.admin_cancelled_at IS 'When admin cancelled the booking (UTC)';
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns 
    WHERE table_name='bookings' AND column_name='updated_at'
  ) THEN
    ALTER TABLE bookings ADD COLUMN updated_at TIMESTAMP DEFAULT now();
    COMMENT ON COLUMN bookings.updated_at IS 'Last update timestamp for payment retry tracking';
  END IF;
END $$;

-- ============================================
-- 2. ALTER Payments TABLE
-- ============================================
-- Add columns to track refunds processed via Razorpay (admin-initiated cancellations)

DO $$ 
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns 
    WHERE table_name='payments' AND column_name='refund_id'
  ) THEN
    ALTER TABLE payments ADD COLUMN refund_id VARCHAR(50);
    COMMENT ON COLUMN payments.refund_id IS 'Razorpay refund ID (e.g., rfnd_xxx...)';
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns 
    WHERE table_name='payments' AND column_name='refund_amount'
  ) THEN
    ALTER TABLE payments ADD COLUMN refund_amount NUMERIC(10, 2);
    COMMENT ON COLUMN payments.refund_amount IS 'Amount refunded to guest in INR (may be less than payment if fee deducted)';
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns 
    WHERE table_name='payments' AND column_name='refund_status'
  ) THEN
    ALTER TABLE payments ADD COLUMN refund_status VARCHAR(50);
    COMMENT ON COLUMN payments.refund_status IS 'Status: pending | completed | failed';
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns 
    WHERE table_name='payments' AND column_name='refund_reason'
  ) THEN
    ALTER TABLE payments ADD COLUMN refund_reason VARCHAR(100);
    COMMENT ON COLUMN payments.refund_reason IS 'Admin reason for refund (audit trail)';
  END IF;
END $$;

-- ============================================
-- 3. CREATE INDEXES FOR PERFORMANCE
-- ============================================
-- Help with queries filtering by admin actions and refund status

CREATE INDEX IF NOT EXISTS idx_bookings_admin_cancelled 
  ON bookings(admin_cancelled, admin_cancelled_at DESC);
  
CREATE INDEX IF NOT EXISTS idx_bookings_admin_cancelled_by 
  ON bookings(admin_cancelled_by) 
  WHERE admin_cancelled = TRUE;
  
CREATE INDEX IF NOT EXISTS idx_payments_refund_status 
  ON payments(refund_status) 
  WHERE refund_id IS NOT NULL;

COMMIT;


-- ============================================
-- 4. MIGRATION VERIFICATION
-- ============================================
-- Run these queries to verify the migration completed successfully:

/*
-- Check booking table columns
SELECT column_name, data_type, is_nullable, column_default
FROM information_schema.columns
WHERE table_name = 'booking' 
  AND column_name IN ('admin_cancelled', 'admin_cancelled_reason', 'admin_notes', 'admin_cancelled_by', 'admin_cancelled_at')
ORDER BY ordinal_position;

-- Check payment table columns
SELECT column_name, data_type, is_nullable, column_default
FROM information_schema.columns
WHERE table_name = 'payment' 
  AND column_name IN ('refund_id', 'refund_amount', 'refund_status', 'refund_reason')
ORDER BY ordinal_position;

-- Check indexes created
SELECT indexname, indexdef 
FROM pg_indexes 
WHERE tablename IN ('booking', 'payment')
  AND indexname LIKE 'idx_%admin%' OR indexname LIKE 'idx_%refund%';
*/

-- ============================================
-- 5. MIGRATION NOTES
-- ============================================
/*
IMPORTANT NOTES:

1. ROOM AVAILABILITY:
   When a booking transitions to "admin_cancelled" status:
   - It's automatically excluded from availability calculations
   - The availability check query filters: status IN ["confirmed", "pending_payment", "payment_pending"]
   - "admin_cancelled" bookings no longer count toward room capacity
   - Rooms become immediately available for new bookings

2. REFUND PROCESSING:
   - Admin triggers cancellation via: POST /bookings/{id}/admin-cancel
   - Backend calls: process_razorpay_refund(payment_id, refund_amount, reason)
   - Razorpay API synchronously processes: razorpay_client.refund.create()
   - On success: payment.refund_id, refund_amount, refund_status="completed" are saved
   - Guest notified via email with refund_id and 5-7 day timeline

3. DATA CONSISTENCY:
   Each admin cancellation creates matching records:
   - booking: status="admin_cancelled", admin_cancelled=TRUE, timestamps, reason, notes, admin
   - payment: refund_id (Razorpay ID), refund_amount, refund_status, refund_reason
   Admin can trace refund via refund_id in Razorpay dashboard

4. ROLLBACK (if needed):
   All columns have DEFAULT values, no dependencies:
   
   ALTER TABLE booking DROP COLUMN admin_cancelled CASCADE;
   ALTER TABLE booking DROP COLUMN admin_cancelled_reason CASCADE;
   ALTER TABLE booking DROP COLUMN admin_notes CASCADE;
   ALTER TABLE booking DROP COLUMN admin_cancelled_by CASCADE;
   ALTER TABLE booking DROP COLUMN admin_cancelled_at CASCADE;
   
   ALTER TABLE payment DROP COLUMN refund_id CASCADE;
   ALTER TABLE payment DROP COLUMN refund_amount CASCADE;
   ALTER TABLE payment DROP COLUMN refund_status CASCADE;
   ALTER TABLE payment DROP COLUMN refund_reason CASCADE;
   
   DROP INDEX IF EXISTS idx_booking_admin_cancelled;
   DROP INDEX IF EXISTS idx_booking_admin_cancelled_by;
   DROP INDEX IF EXISTS idx_payment_refund_status;
*/
