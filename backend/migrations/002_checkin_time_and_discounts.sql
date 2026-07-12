-- Migration: 002_checkin_time_and_discounts.sql
-- Description: Add per-booking arrival time + discount/promotion support
-- Purpose:
--   1. bookings.check_in_time   -> guest's expected arrival time
--   2. bookings.discount_amount -> aggregate promotion discount on a booking
--   3. booking_items.discount_amount -> per-line-item promotion discount
--   4. promotions table (also auto-created by SQLAlchemy create_all; included
--      here for explicit prod parity / running against a fresh DB via psql)
-- Database: PostgreSQL
-- Idempotent: YES (uses information_schema checks + IF NOT EXISTS)

BEGIN;

-- ============================================
-- 1. ALTER bookings TABLE
-- ============================================
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name='bookings' AND column_name='check_in_time'
  ) THEN
    ALTER TABLE bookings ADD COLUMN check_in_time TIME;
    COMMENT ON COLUMN bookings.check_in_time IS 'Guest expected arrival time (required for new bookings)';
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name='bookings' AND column_name='discount_amount'
  ) THEN
    ALTER TABLE bookings ADD COLUMN discount_amount NUMERIC(10, 2) DEFAULT 0;
    COMMENT ON COLUMN bookings.discount_amount IS 'Total promotion discount applied to the booking';
  END IF;
END $$;

-- ============================================
-- 2. ALTER booking_items TABLE
-- ============================================
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name='booking_items' AND column_name='discount_amount'
  ) THEN
    ALTER TABLE booking_items ADD COLUMN discount_amount NUMERIC(10, 2) DEFAULT 0;
    COMMENT ON COLUMN booking_items.discount_amount IS 'Promotion discount applied to this line item';
  END IF;
END $$;

-- ============================================
-- 3. CREATE promotions TABLE
-- ============================================
CREATE TABLE IF NOT EXISTS promotions (
    promotion_id   SERIAL PRIMARY KEY,
    name           VARCHAR(100) NOT NULL,
    discount_type  VARCHAR(10)  NOT NULL,           -- 'percent' | 'flat'
    discount_value NUMERIC(10, 2) NOT NULL,
    room_type_id   INTEGER REFERENCES room_types(room_type_id),  -- NULL = all room types
    is_active      BOOLEAN DEFAULT FALSE,
    valid_from     DATE,
    valid_to       DATE,
    created_at     TIMESTAMP DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_promotions_is_active ON promotions(is_active);
CREATE INDEX IF NOT EXISTS idx_promotions_room_type ON promotions(room_type_id);

COMMIT;

-- ============================================
-- VERIFICATION (run manually if desired)
-- ============================================
/*
SELECT column_name, data_type FROM information_schema.columns
WHERE table_name='bookings' AND column_name IN ('check_in_time','discount_amount');

SELECT column_name, data_type FROM information_schema.columns
WHERE table_name='booking_items' AND column_name='discount_amount';

\d promotions
*/
