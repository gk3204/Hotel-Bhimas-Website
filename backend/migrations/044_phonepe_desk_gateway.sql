-- Migration: 044_phonepe_desk_gateway.sql
-- Description: PhonePe as a second desk gateway for UPI QR — a stored UPI intent + the switch.
-- Purpose:
--   Razorpay charges ~2% + GST on every desk collection, UPI included, although UPI P2M carries
--   no MDR by law. PhonePe PG settles UPI at 0%, so desk QR collections become free while
--   keeping webhook confirmation. Two things are needed in the database:
--
--   1. payments.upi_intent — the `upi://pay?...` string PhonePe returns. It does not fit the
--      existing qr_image_url VARCHAR(300) and is not a URL to fetch: GET /payments/{id}/qr.png
--      renders it locally with segno, so the desk keeps working if PhonePe's CDN hiccups. A
--      Razorpay row leaves it NULL and still takes the proxy path.
--   2. app_settings.desk_pay_gateway — 'razorpay' (unchanged behaviour) or 'phonepe'. Seeded
--      to razorpay deliberately: applying this migration changes nothing until someone chooses.
--
--   Desk payment LINKS, /send-link and the whole website checkout stay on Razorpay regardless.
--   Both gateways therefore run at once in production, and payments.gateway is what tells a
--   row apart on every read path — refunds most of all.
-- Database: PostgreSQL
-- Idempotent: YES (ADD COLUMN IF NOT EXISTS / ON CONFLICT DO NOTHING)
--
-- NOTE: upi_intent is ALSO auto-created by SQLAlchemy create_all on startup; it is spelled out
-- here for explicit production parity, the same way 022 documents booking_guests.

BEGIN;

ALTER TABLE payments ADD COLUMN IF NOT EXISTS upi_intent TEXT;

INSERT INTO app_settings (key, value)
VALUES ('desk_pay_gateway', 'razorpay')
ON CONFLICT (key) DO NOTHING;

COMMIT;
