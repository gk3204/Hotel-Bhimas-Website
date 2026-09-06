-- 037: per-room lock type (card | key).
-- Card-lock rooms use the RFID encoder (check-in/checkout/shift + the housekeeping cleaning
-- card). Key-lock rooms have a physical metal key and skip the whole encode/decode flow.
-- Idempotent: safe to re-run.
ALTER TABLE rooms ADD COLUMN IF NOT EXISTS lock_type VARCHAR(8) NOT NULL DEFAULT 'card';
