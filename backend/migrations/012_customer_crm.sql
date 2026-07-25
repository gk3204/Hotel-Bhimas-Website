-- Migration: 012_customer_crm.sql
-- Description: Customer / Guest CRM (prompt 14).
-- Purpose:
--   1. guest_profiles            -> rich CRM companion per guest (VIP/blacklist/loyalty/KYC/notes)
--   2. loyalty_ledger            -> append-only points ledger (accrual on checkout, redeem as discount)
--   3. pre_arrival_registrations -> tokenized pre-arrival digital-registration links
--   4. app_settings seed         -> loyalty rates + blacklist enforcement + link TTL
-- The thin `guests` table is left untouched; guest_profiles is the additive companion.
-- All new tables/columns are ALSO auto-created by SQLAlchemy create_all; included here for
-- explicit prod parity / running against a deployed DB via psql.
-- Database: PostgreSQL
-- Idempotent: YES (IF NOT EXISTS + ON CONFLICT DO NOTHING)

BEGIN;

-- ============================================
-- 1. guest_profiles (one companion row per guest)
-- ============================================
CREATE TABLE IF NOT EXISTS guest_profiles (
    id               SERIAL PRIMARY KEY,
    guest_id         INTEGER NOT NULL UNIQUE REFERENCES guests(guest_id),
    id_type          VARCHAR(20),                          -- aadhaar|passport|driving_licence|voter_id|other
    id_number_masked VARCHAR(30),                          -- masked "****1234" — raw never stored
    id_photo_url     TEXT,                                 -- storage URL (R2 or local /crm/files/*)
    guest_photo_url  TEXT,
    address          VARCHAR(300),
    dob              DATE,
    gstin            VARCHAR(20),
    vip              BOOLEAN DEFAULT FALSE,
    blacklist        BOOLEAN DEFAULT FALSE,
    blacklist_reason VARCHAR(300),
    loyalty_points   INTEGER DEFAULT 0,                    -- denormalized balance (ledger is authoritative)
    marketing_optin  BOOLEAN DEFAULT FALSE,
    notes            TEXT,
    created_at       TIMESTAMP DEFAULT now(),
    updated_at       TIMESTAMP,
    updated_by       INTEGER REFERENCES users(user_id)
);
CREATE INDEX IF NOT EXISTS idx_guest_profiles_vip ON guest_profiles(vip);
CREATE INDEX IF NOT EXISTS idx_guest_profiles_blacklist ON guest_profiles(blacklist);

-- ============================================
-- 2. loyalty_ledger (append-only points transactions)
-- ============================================
CREATE TABLE IF NOT EXISTS loyalty_ledger (
    id              SERIAL PRIMARY KEY,
    guest_id        INTEGER NOT NULL REFERENCES guests(guest_id),
    booking_id      INTEGER REFERENCES bookings(booking_id),
    delta           INTEGER NOT NULL,                      -- + accrue / - redeem
    reason          VARCHAR(200),
    points_after    INTEGER,
    folio_charge_id INTEGER,                               -- the discount line, for redemptions
    created_by      INTEGER REFERENCES users(user_id),
    created_at      TIMESTAMP DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_loyalty_ledger_guest ON loyalty_ledger(guest_id);
CREATE INDEX IF NOT EXISTS idx_loyalty_ledger_booking ON loyalty_ledger(booking_id);

-- ============================================
-- 3. pre_arrival_registrations (tokenized links)
-- ============================================
CREATE TABLE IF NOT EXISTS pre_arrival_registrations (
    id           SERIAL PRIMARY KEY,
    token        VARCHAR(64) NOT NULL UNIQUE,
    booking_id   INTEGER NOT NULL REFERENCES bookings(booking_id),
    guest_id     INTEGER REFERENCES guests(guest_id),
    status       VARCHAR(20) NOT NULL DEFAULT 'sent',      -- sent|opened|submitted|verified
    payload      TEXT,                                     -- JSON text of submitted fields
    expires_at   TIMESTAMP,
    submitted_at TIMESTAMP,
    created_by   INTEGER REFERENCES users(user_id),
    created_at   TIMESTAMP DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_prearrival_booking ON pre_arrival_registrations(booking_id);

-- ============================================
-- 4. Seed CRM config (only if absent — never overwrites an admin's value)
-- ============================================
INSERT INTO app_settings (key, value) VALUES
    ('loyalty_points_per_rupee', '0.01'),     -- 1 point per ₹100 of stay spend
    ('loyalty_rupee_per_point',  '1'),        -- 1 point redeems for ₹1
    ('blacklist_enforcement',    'warn'),     -- warn | block
    ('crm_registration_link_ttl_hours', '72')
ON CONFLICT (key) DO NOTHING;

COMMIT;

-- ============================================
-- VERIFICATION (run manually if desired)
-- ============================================
-- SELECT count(*) FROM guest_profiles;
-- SELECT count(*) FROM loyalty_ledger;
-- SELECT count(*) FROM pre_arrival_registrations;
-- SELECT key, value FROM app_settings WHERE key LIKE 'loyalty%' OR key IN ('blacklist_enforcement','crm_registration_link_ttl_hours');
