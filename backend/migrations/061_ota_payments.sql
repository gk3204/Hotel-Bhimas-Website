-- P1 (owner, 4-5 Nov): track which Goibibo / MakeMyTrip booking was paid in which Go-MMT payment.
-- The "MMT Payment(Wire) Confirmation" mail lists every booking a bank transfer covers, with the
-- transfer's UTR. Each mail becomes one ota_settlements row (extended below) and one
-- ota_settlement_lines row per booking. A booking's state (pending / done / received) is computed
-- from these rows by services/ota_payments.py and is never stored on the booking.
-- Design: HotelLock/docs/ota-payment-tracking-plan.md

ALTER TABLE ota_settlements ADD COLUMN IF NOT EXISTS payment_date DATE;
ALTER TABLE ota_settlements ADD COLUMN IF NOT EXISTS utr VARCHAR(40);
ALTER TABLE ota_settlements ADD COLUMN IF NOT EXISTS ota_account_code VARCHAR(30);
ALTER TABLE ota_settlements ADD COLUMN IF NOT EXISTS bank_name VARCHAR(80);
ALTER TABLE ota_settlements ADD COLUMN IF NOT EXISTS bank_account VARCHAR(40);
ALTER TABLE ota_settlements ADD COLUMN IF NOT EXISTS lines_total NUMERIC(12, 2);
ALTER TABLE ota_settlements ADD COLUMN IF NOT EXISTS source VARCHAR(10) NOT NULL DEFAULT 'manual';
ALTER TABLE ota_settlements ADD COLUMN IF NOT EXISTS message_id VARCHAR(200);
ALTER TABLE ota_settlements ADD COLUMN IF NOT EXISTS raw_source TEXT;
ALTER TABLE ota_settlements ADD COLUMN IF NOT EXISTS state VARCHAR(12) NOT NULL DEFAULT 'done';
ALTER TABLE ota_settlements ADD COLUMN IF NOT EXISTS flags VARCHAR(200);
ALTER TABLE ota_settlements ADD COLUMN IF NOT EXISTS received_at TIMESTAMP;
ALTER TABLE ota_settlements ADD COLUMN IF NOT EXISTS received_by INTEGER REFERENCES users(user_id);
ALTER TABLE ota_settlements ADD COLUMN IF NOT EXISTS received_on DATE;
ALTER TABLE ota_settlements ADD COLUMN IF NOT EXISTS received_amount NUMERIC(12, 2);
ALTER TABLE ota_settlements ADD COLUMN IF NOT EXISTS received_note VARCHAR(500);
ALTER TABLE ota_settlements ADD COLUMN IF NOT EXISTS undone_at TIMESTAMP;
ALTER TABLE ota_settlements ADD COLUMN IF NOT EXISTS undone_by INTEGER REFERENCES users(user_id);
ALTER TABLE ota_settlements ADD COLUMN IF NOT EXISTS undo_reason VARCHAR(500);
-- amount was NUMERIC(10,2); a month of Go-MMT payouts can pass 99,99,999.99 only in theory, but a
-- manual period settlement could. Widen while we are here.
ALTER TABLE ota_settlements ALTER COLUMN amount TYPE NUMERIC(12, 2);

CREATE UNIQUE INDEX IF NOT EXISTS uq_ota_settlements_message_id
    ON ota_settlements (message_id) WHERE message_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_ota_settlements_utr
    ON ota_settlements (utr) WHERE utr IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_ota_settlements_payment_date ON ota_settlements (payment_date);

CREATE TABLE IF NOT EXISTS ota_settlement_lines (
    id SERIAL PRIMARY KEY,
    settlement_id INTEGER NOT NULL REFERENCES ota_settlements(id) ON DELETE CASCADE,
    channel_code VARCHAR(20) NOT NULL,
    ota_booking_id VARCHAR(80) NOT NULL,
    pnr VARCHAR(120),
    guest_name VARCHAR(120),
    hotel_name VARCHAR(120),
    check_in DATE,
    check_out DATE,
    original_cost NUMERIC(12, 2),
    amount_paid NUMERIC(12, 2) NOT NULL,
    booking_type VARCHAR(30),
    brand VARCHAR(30),
    booking_id INTEGER REFERENCES bookings(booking_id),
    expected_amount NUMERIC(12, 2),
    match_status VARCHAR(20) NOT NULL DEFAULT 'unknown_booking',
    linked_by INTEGER REFERENCES users(user_id),
    created_at TIMESTAMP DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_ota_settlement_lines_settlement ON ota_settlement_lines (settlement_id);
CREATE INDEX IF NOT EXISTS ix_ota_settlement_lines_ota_booking_id ON ota_settlement_lines (ota_booking_id);
CREATE INDEX IF NOT EXISTS ix_ota_settlement_lines_booking ON ota_settlement_lines (booking_id);

-- The voucher's own TCS (0.5%) and TDS (0.1%) per booking. They live on the draft (the voucher's
-- home); a booking reads them through its confirmation draft (ota_payments.voucher_taxes).
ALTER TABLE ota_draft_bookings ADD COLUMN IF NOT EXISTS tcs_amount NUMERIC(10, 2);
ALTER TABLE ota_draft_bookings ADD COLUMN IF NOT EXISTS tds_amount NUMERIC(10, 2);
