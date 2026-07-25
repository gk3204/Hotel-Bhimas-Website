-- Migration: 017_desk_payments.sql
-- Description: Integrated desk payments — UPI dynamic QR + Razorpay payment link + cash (prompt 19).
-- Purpose:
--   1. payments      -> desk-collect columns (collect_method, qr_code_id, qr_image_url, payment_link_id)
--                       so the Razorpay webhook can match a Payment when there is no order_id
--                       (qr_code.credited / payment_link.paid events carry none).
--   2. app_settings  -> seed desk-payment method toggles + convenience-fee policy + QR expiry + VPA flag
--                       (never overwrites an admin edit).
-- All columns are ALSO auto-created by SQLAlchemy create_all; included here for prod parity via psql.
-- Database: PostgreSQL
-- Idempotent: YES (IF NOT EXISTS + ON CONFLICT DO NOTHING)

BEGIN;

-- ============================================
-- 1. payments: desk UPI-QR / payment-link collection (additive)
-- ============================================
ALTER TABLE payments ADD COLUMN IF NOT EXISTS collect_method          VARCHAR(20);
ALTER TABLE payments ADD COLUMN IF NOT EXISTS qr_code_id              VARCHAR(50);
ALTER TABLE payments ADD COLUMN IF NOT EXISTS qr_image_url            VARCHAR(300);
ALTER TABLE payments ADD COLUMN IF NOT EXISTS payment_link_id         VARCHAR(50);
ALTER TABLE payments ADD COLUMN IF NOT EXISTS convenience_fee_amount  NUMERIC(10,2);
CREATE INDEX IF NOT EXISTS idx_payments_collect_method ON payments(collect_method);
CREATE INDEX IF NOT EXISTS idx_payments_qr_code_id     ON payments(qr_code_id);
CREATE INDEX IF NOT EXISTS idx_payments_payment_link   ON payments(payment_link_id);

-- ============================================
-- 2. app_settings: desk-payment config seeds (never overwrites an admin edit)
--    Fee policy: UPI QR ~0% MDR (fee off), cards ~2% + 18% GST on the fee (fee on),
--    links off by default. Cash = 0%. VPA (own-bank, 0%) mode deferred, default off.
-- ============================================
INSERT INTO app_settings (key, value) VALUES
    ('desk_pay_upi_qr_enabled',   'true'),
    ('desk_pay_link_enabled',     'true'),
    ('desk_pay_cash_enabled',     'true'),
    ('desk_pay_fee_card_percent', '2.0'),
    ('desk_pay_fee_gst_percent',  '18.0'),
    ('desk_pay_fee_on_card',      'true'),
    ('desk_pay_fee_on_upi',       'false'),
    ('desk_pay_fee_on_link',      'false'),
    ('desk_pay_qr_expiry_minutes','15'),
    ('desk_pay_vpa_enabled',      'false')
ON CONFLICT (key) DO NOTHING;

COMMIT;

-- ============================================
-- VERIFICATION (run manually if desired)
-- ============================================
-- SELECT column_name FROM information_schema.columns WHERE table_name='payments'
--   AND column_name IN ('collect_method','qr_code_id','qr_image_url','payment_link_id');
-- SELECT key, value FROM app_settings WHERE key LIKE 'desk_pay_%' ORDER BY key;
