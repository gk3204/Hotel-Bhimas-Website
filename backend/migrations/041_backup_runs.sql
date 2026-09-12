-- Migration: 041_backup_runs.sql
-- Description: History of confirmed backup artifacts, reported by the machine that holds them.
-- Purpose:
--   app_settings records that the SERVER produced a dump. That is not the same as a copy
--   existing off the server: a download that dies mid-transfer, fails its checksum and is
--   discarded still leaves the server's timestamp looking healthy. Only the client can assert
--   custody, so BhimasBackup POSTs /admin/backup/report after it renames a verified file into
--   place, and each report lands here.
--   Roughly 420 rows a year (nightly dump + weekly scan archive) — no pruning needed.
-- Database: PostgreSQL
-- Idempotent: YES (CREATE TABLE / INDEX IF NOT EXISTS)
--
-- NOTE: ALSO auto-created by SQLAlchemy create_all on startup; included here for explicit
-- production parity, the same way 022 documents booking_guests.

BEGIN;

CREATE TABLE IF NOT EXISTS backup_runs (
    id                 SERIAL PRIMARY KEY,
    run_id             VARCHAR(40)  NOT NULL,
    kind               VARCHAR(20)  NOT NULL,
    outcome            VARCHAR(20)  NOT NULL,
    machine            VARCHAR(100),
    folder             VARCHAR(400),
    filename           VARCHAR(200),
    size_bytes         BIGINT,
    sha256             VARCHAR(64),
    item_count         INTEGER,
    client_local_time  VARCHAR(40),
    started_at         TIMESTAMP,
    finished_at        TIMESTAMP,
    detail             VARCHAR(500),
    tool_version       VARCHAR(40),
    reported_by        INTEGER REFERENCES users(user_id),
    ip                 VARCHAR(50),
    created_at         TIMESTAMP DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_backup_runs_run_id      ON backup_runs (run_id);
CREATE INDEX IF NOT EXISTS idx_backup_runs_kind        ON backup_runs (kind);
CREATE INDEX IF NOT EXISTS idx_backup_runs_finished_at ON backup_runs (finished_at);
CREATE INDEX IF NOT EXISTS idx_backup_runs_created_at  ON backup_runs (created_at);

-- A retried report must update its row, not add a second one.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_backup_runs_run_kind') THEN
        ALTER TABLE backup_runs ADD CONSTRAINT uq_backup_runs_run_kind UNIQUE (run_id, kind);
    END IF;
END $$;

COMMENT ON TABLE backup_runs IS
    'One backup artifact as confirmed by the machine holding it — custody, not production.';
COMMENT ON COLUMN backup_runs.finished_at IS
    'Server receipt time. Authoritative for staleness; the client clock is client_local_time.';

COMMIT;
