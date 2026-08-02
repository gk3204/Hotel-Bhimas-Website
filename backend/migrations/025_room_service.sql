-- Migration: 025_room_service.sql
-- Description: Room service — KOT number + print stamps (backlog v2 TBC-4).
-- Purpose: give every room-service order a kitchen-docket number staff can call out, and
--   record when its KOT / guest bill were printed (so a docket isn't printed twice by the
--   desk's auto-print, and a reprint is deliberate).
--   Orders themselves are still `guest_requests` rows (type='room_service') — reusing that
--   table means the guest-portal board, the desk board and the tablet all show one list.
-- NOTE: these are COLUMN ADDs — SQLAlchemy create_all does NOT add columns to an existing
--   table, so this migration MUST be run on the deployed DB. A fresh DB gets them via create_all.
-- Database: PostgreSQL
-- Idempotent: YES (IF NOT EXISTS)

BEGIN;

ALTER TABLE guest_requests ADD COLUMN IF NOT EXISTS kot_no VARCHAR(24);
ALTER TABLE guest_requests ADD COLUMN IF NOT EXISTS printed_kot_at TIMESTAMP;
ALTER TABLE guest_requests ADD COLUMN IF NOT EXISTS printed_bill_at TIMESTAMP;

CREATE INDEX IF NOT EXISTS idx_guest_requests_kot_no ON guest_requests(kot_no);

COMMIT;
