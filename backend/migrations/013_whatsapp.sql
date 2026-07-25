-- Migration: 013_whatsapp.sql
-- Description: WhatsApp & automated alerts (prompt 15).
-- Purpose:
--   1. whatsapp_messages -> outbound + inbound message log / outbox (delivery status, idempotency)
--   2. whatsapp_optouts  -> per-phone opt-out list (guests who said STOP; admin-managed)
--   3. users.phone       -> staff WhatsApp/phone (maintenance assignee notify)
--   4. app_settings seed  -> automation toggles + timing + owner recipient + review link
-- All new tables/columns are ALSO auto-created by SQLAlchemy create_all; included here for
-- explicit prod parity / running against a deployed DB via psql.
-- Secrets (WHATSAPP_ACCESS_TOKEN / WHATSAPP_PHONE_NUMBER_ID / WHATSAPP_VERIFY_TOKEN) live in ENV,
-- NOT here — the sender is OFF (stub/log) until those are set.
-- Database: PostgreSQL
-- Idempotent: YES (IF NOT EXISTS + ON CONFLICT DO NOTHING)

BEGIN;

-- ============================================
-- 1. whatsapp_messages (log + outbox + inbound)
-- ============================================
CREATE TABLE IF NOT EXISTS whatsapp_messages (
    id          SERIAL PRIMARY KEY,
    direction   VARCHAR(3)  NOT NULL DEFAULT 'out',    -- out|in
    to_number   VARCHAR(20) NOT NULL,                  -- E.164 (no '+')
    template    VARCHAR(60),                           -- catalog name, or 'inbound' for replies
    params      TEXT,                                  -- JSON text of template params
    body        TEXT,                                  -- rendered/inbound text (preview)
    status      VARCHAR(20) NOT NULL DEFAULT 'queued', -- queued|sent|delivered|read|failed|received
    provider    VARCHAR(20),                           -- meta|stub
    provider_id VARCHAR(120),                          -- Meta wamid (for status callbacks)
    error       VARCHAR(300),
    guest_id    INTEGER REFERENCES guests(guest_id),
    booking_id  INTEGER REFERENCES bookings(booking_id),
    client_ref  VARCHAR(120) UNIQUE,                   -- idempotency for scheduled sends
    created_at  TIMESTAMP DEFAULT now(),
    updated_at  TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_wa_messages_status ON whatsapp_messages(status);
CREATE INDEX IF NOT EXISTS idx_wa_messages_direction ON whatsapp_messages(direction);
CREATE INDEX IF NOT EXISTS idx_wa_messages_to ON whatsapp_messages(to_number);
CREATE INDEX IF NOT EXISTS idx_wa_messages_provider_id ON whatsapp_messages(provider_id);
CREATE INDEX IF NOT EXISTS idx_wa_messages_booking ON whatsapp_messages(booking_id);
CREATE INDEX IF NOT EXISTS idx_wa_messages_created ON whatsapp_messages(created_at);

-- ============================================
-- 2. whatsapp_optouts (per-phone suppression)
-- ============================================
CREATE TABLE IF NOT EXISTS whatsapp_optouts (
    phone      VARCHAR(20) PRIMARY KEY,                -- E.164 (no '+')
    reason     VARCHAR(120),
    source     VARCHAR(20) NOT NULL DEFAULT 'guest',   -- guest|admin
    created_at TIMESTAMP DEFAULT now()
);

-- ============================================
-- 3. users.phone (staff notify target)
-- ============================================
ALTER TABLE users ADD COLUMN IF NOT EXISTS phone VARCHAR(20);

-- ============================================
-- 4. Seed WhatsApp config (only if absent — never overwrites an admin's value)
-- ============================================
INSERT INTO app_settings (key, value) VALUES
    ('wa_checkout_reminder_enabled', 'true'),
    ('wa_checkout_reminder_lead_hours', '2'),
    ('wa_overstay_enabled', 'true'),
    ('wa_confirmation_enabled', 'true'),
    ('wa_receipt_enabled', 'true'),
    ('wa_room_ready_enabled', 'true'),
    ('wa_review_enabled', 'true'),
    ('wa_review_delay_hours', '3'),
    ('wa_owner_alerts_enabled', 'true'),
    ('owner_whatsapp', ''),
    ('wa_google_review_url', ''),
    ('wa_job_interval_minutes', '15'),
    ('wa_daily_digest_hour', '9')
ON CONFLICT (key) DO NOTHING;

COMMIT;

-- ============================================
-- VERIFICATION (run manually if desired)
-- ============================================
-- SELECT count(*) FROM whatsapp_messages;
-- SELECT count(*) FROM whatsapp_optouts;
-- SELECT column_name FROM information_schema.columns WHERE table_name='users' AND column_name='phone';
-- SELECT key, value FROM app_settings WHERE key LIKE 'wa_%' OR key = 'owner_whatsapp';
