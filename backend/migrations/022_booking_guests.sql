-- Migration: 022_booking_guests.sql
-- Description: Per-occupant guest KYC for a booking (FE-3).
-- Purpose:
--   booking_guests -> one row per guest staying in a room (lead + companions). ID numbers are
--   stored MASKED only (raw never persisted); the ID scan image is stored ENCRYPTED on disk
--   (utils/secure_id_store.py) and referenced by id_scan_ref — never a public URL.
-- This table is ALSO auto-created by SQLAlchemy create_all; included here for explicit prod
-- parity / running against a deployed DB via psql.
-- Database: PostgreSQL
-- Idempotent: YES (IF NOT EXISTS)

BEGIN;

CREATE TABLE IF NOT EXISTS booking_guests (
    id                SERIAL PRIMARY KEY,
    booking_id        INTEGER NOT NULL REFERENCES bookings(booking_id),
    name              VARCHAR(100) NOT NULL,
    id_type           VARCHAR(20),                 -- aadhaar|passport|driving_licence|voter_id|other
    id_number_masked  VARCHAR(30),                 -- masked "****1234" — raw never stored
    id_scan_ref       VARCHAR(120),                -- encrypted-file ref: booking_<id>/<uuid>.enc
    id_scan_mime      VARCHAR(40),                 -- content type, for the decrypt-on-read stream
    is_primary        BOOLEAN NOT NULL DEFAULT FALSE,
    created_at        TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_booking_guests_booking ON booking_guests(booking_id);
CREATE INDEX IF NOT EXISTS idx_booking_guests_primary ON booking_guests(is_primary);

COMMIT;
