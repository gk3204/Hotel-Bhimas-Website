-- Migration: 036_whatsapp_inbox.sql
-- Description: Staff WhatsApp inbox (two-way messaging).
--     * whatsapp_messages.sent_by            — staff user who sent a reply / inbox template (NULL = automated)
--     * whatsapp_conversation_reads          — per-number read marker for unread counts
-- NOTE: COLUMN ADD + NEW TABLE — SQLAlchemy create_all does NOT alter existing tables, so this
--   migration MUST be run on the deployed DB. A fresh DB gets both via create_all.
-- Database: PostgreSQL
-- Idempotent: YES (IF NOT EXISTS)

BEGIN;

ALTER TABLE whatsapp_messages ADD COLUMN IF NOT EXISTS sent_by INTEGER REFERENCES users(user_id);
CREATE INDEX IF NOT EXISTS ix_whatsapp_messages_sent_by ON whatsapp_messages (sent_by);

CREATE TABLE IF NOT EXISTS whatsapp_conversation_reads (
    phone                 VARCHAR(20) PRIMARY KEY,
    last_read_message_id  INTEGER NOT NULL DEFAULT 0,
    last_read_at          TIMESTAMP DEFAULT now(),
    updated_by            INTEGER REFERENCES users(user_id)
);

COMMIT;
