-- Migration: 040_id_scan_archived.sql
-- Description: Mark an ID scan as archived offline (pulled to the owner's PC, removed from R2).
-- Purpose:
--   Scans move to a private R2 bucket, are pulled weekly to an encrypted folder on the owner's
--   PC, and — once that local copy is verified and the scan is older than the retention floor —
--   are deleted from R2 to free space. After that the bytes are no longer reachable from the
--   server, so the UI must say "archived offline" rather than presenting a broken viewer.
--
--   id_scan_ref is deliberately NOT nulled. It stays the pointer INTO the archive: the path of
--   the member inside the weekly zip IS the ref. Nulling it would make the file unfindable.
-- Database: PostgreSQL
-- Idempotent: YES (ADD COLUMN IF NOT EXISTS)
--
-- NOTE: create_all() does NOT add columns to an existing table, so this file must be run
-- against any database that already has booking_guests.

BEGIN;

ALTER TABLE booking_guests ADD COLUMN IF NOT EXISTS id_scan_archived_at      TIMESTAMP;
ALTER TABLE booking_guests ADD COLUMN IF NOT EXISTS id_scan_back_archived_at TIMESTAMP;

COMMENT ON COLUMN booking_guests.id_scan_archived_at IS
    'When the FRONT scan was archived offline and deleted from object storage. NULL = still online. '
    'id_scan_ref remains valid and locates the file inside the weekly archive zip.';
COMMENT ON COLUMN booking_guests.id_scan_back_archived_at IS
    'When the REVERSE scan was archived offline and deleted from object storage. NULL = still online.';

COMMIT;
