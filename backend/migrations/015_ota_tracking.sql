-- Migration: 015_ota_tracking.sql
-- Description: OTA tracking (prompt 17 Phase A) — MakeMyTrip / Goibibo / Booking.com / Agoda.
-- Purpose:
--   1. bookings              -> 3 additive OTA columns (ota_booking_id, ota_commission_percent, ota_net_payout).
--   2. ota_channels          -> per-OTA config (commission %, active, mailbox parsing toggle, cancellation rules).
--   3. ota_settlements       -> actual bank payouts received from an OTA (payout reconciliation).
--   4. ota_draft_bookings    -> bookings parsed from OTA emails, awaiting reception one-click confirm.
-- All tables are ALSO auto-created by SQLAlchemy create_all; included here for prod parity via psql.
-- Phase B (channel-manager two-way sync) is a later prompt and adds no schema here.
-- Database: PostgreSQL
-- Idempotent: YES (IF NOT EXISTS + ON CONFLICT DO NOTHING)

BEGIN;

-- ============================================
-- 1. bookings: OTA tracking columns (additive)
-- ============================================
ALTER TABLE bookings ADD COLUMN IF NOT EXISTS ota_booking_id         VARCHAR(80);
ALTER TABLE bookings ADD COLUMN IF NOT EXISTS ota_commission_percent NUMERIC(5, 2);
ALTER TABLE bookings ADD COLUMN IF NOT EXISTS ota_net_payout         NUMERIC(10, 2);
CREATE INDEX IF NOT EXISTS idx_bookings_ota_booking_id ON bookings(ota_booking_id);

-- ============================================
-- 2. ota_channels (per-OTA config)
-- ============================================
CREATE TABLE IF NOT EXISTS ota_channels (
    id                      SERIAL PRIMARY KEY,
    code                    VARCHAR(20) NOT NULL UNIQUE,
    display_name            VARCHAR(60) NOT NULL,
    commission_percent      NUMERIC(5, 2) NOT NULL DEFAULT 0,
    active                  BOOLEAN NOT NULL DEFAULT TRUE,
    mailbox_parsing_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    cancellation_policy     VARCHAR(500),
    created_at              TIMESTAMP DEFAULT now(),
    updated_at              TIMESTAMP DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_ota_channels_code ON ota_channels(code);

-- Self-heal: if the table was first created by SQLAlchemy create_all, its NOT NULL columns may lack a
-- DB-side DEFAULT (the model uses Python-side default=), so a column-omitting INSERT would fail. Set the
-- defaults explicitly here (harmless if already set) before seeding.
ALTER TABLE ota_channels ALTER COLUMN commission_percent SET DEFAULT 0;
ALTER TABLE ota_channels ALTER COLUMN active SET DEFAULT TRUE;
ALTER TABLE ota_channels ALTER COLUMN mailbox_parsing_enabled SET DEFAULT FALSE;

-- Seed the recognised channels (commission 0, active, mailbox off — admin tunes later; never overwrites).
-- Values are listed explicitly so the seed works regardless of whether the column defaults exist.
INSERT INTO ota_channels (code, display_name, commission_percent, active, mailbox_parsing_enabled) VALUES
    ('makemytrip', 'MakeMyTrip', 0, TRUE, FALSE),
    ('goibibo',    'Goibibo',    0, TRUE, FALSE),
    ('booking_com','Booking.com',0, TRUE, FALSE),
    ('agoda',      'Agoda',      0, TRUE, FALSE),
    ('other_ota',  'Other OTA',  0, TRUE, FALSE)
ON CONFLICT (code) DO NOTHING;

-- ============================================
-- 3. ota_settlements (actual bank payouts)
-- ============================================
CREATE TABLE IF NOT EXISTS ota_settlements (
    id           SERIAL PRIMARY KEY,
    channel_code VARCHAR(20) NOT NULL,
    period_start DATE,
    period_end   DATE,
    reference    VARCHAR(80),
    amount       NUMERIC(10, 2) NOT NULL,
    notes        VARCHAR(500),
    created_by   INTEGER REFERENCES users(user_id),
    created_at   TIMESTAMP DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_ota_settlements_channel ON ota_settlements(channel_code);
CREATE INDEX IF NOT EXISTS idx_ota_settlements_created ON ota_settlements(created_at);

-- ============================================
-- 4. ota_draft_bookings (parsed-from-email drafts)
-- ============================================
CREATE TABLE IF NOT EXISTS ota_draft_bookings (
    id                 SERIAL PRIMARY KEY,
    channel_code       VARCHAR(20) NOT NULL,
    ota_booking_id     VARCHAR(80),
    kind               VARCHAR(20) NOT NULL DEFAULT 'confirmation',
    status             VARCHAR(20) NOT NULL DEFAULT 'pending',
    guest_name         VARCHAR(120),
    phone              VARCHAR(20),
    email              VARCHAR(120),
    check_in           DATE,
    check_out          DATE,
    room_type_hint     VARCHAR(120),
    amount             NUMERIC(10, 2),
    commission_percent NUMERIC(5, 2),
    message_id         VARCHAR(200),
    raw_source         TEXT,
    linked_booking_id  INTEGER REFERENCES bookings(booking_id),
    created_at         TIMESTAMP DEFAULT now(),
    CONSTRAINT uq_ota_draft_channel_bookingid_kind UNIQUE (channel_code, ota_booking_id, kind)
);
CREATE INDEX IF NOT EXISTS idx_ota_draft_channel     ON ota_draft_bookings(channel_code);
CREATE INDEX IF NOT EXISTS idx_ota_draft_status      ON ota_draft_bookings(status);
CREATE INDEX IF NOT EXISTS idx_ota_draft_bookingid   ON ota_draft_bookings(ota_booking_id);
CREATE INDEX IF NOT EXISTS idx_ota_draft_message_id  ON ota_draft_bookings(message_id);

COMMIT;

-- ============================================
-- VERIFICATION (run manually if desired)
-- ============================================
-- SELECT code, display_name, commission_percent, active FROM ota_channels ORDER BY id;
-- SELECT column_name FROM information_schema.columns WHERE table_name='bookings' AND column_name LIKE 'ota_%';
-- SELECT count(*) FROM ota_settlements; SELECT count(*) FROM ota_draft_bookings;
