-- Migration: 021_guest_portal.sql
-- Description: Round-2 batch C part 1 (prompt 18d, slice 10) — guest extras / in-room QR portal.
-- Purpose:
--   1. guest_portal_sessions -> a per-stay, long-lived token behind the in-room QR. Unlike
--      pre_arrival_registrations (booking-scoped + single-submit), this stays valid for the whole
--      stay and drives many guest actions. The token in the URL is the only credential.
--   2. guest_requests -> one table for everything the guest asks for from the portal (room service,
--      WiFi voucher, wake-up, cab, contactless checkout). Reception fulfils from the desk board; a
--      room_service request posts a folio charge ONLY on fulfil (no charge without staff).
--   3. menu_items -> the room-service menu the guest orders from; optional stock_item_id link so
--      fulfilling a stocked drink also decrements inventory (prompt 18c stock ledger).
--   4. app_settings -> seed portal config (master + per-feature toggles + WiFi). Never overwrites.
-- New tables are ALSO auto-created by SQLAlchemy create_all; included here for prod parity via psql.
-- Database: PostgreSQL
-- Idempotent: YES (IF NOT EXISTS / ADD COLUMN IF NOT EXISTS / ON CONFLICT DO NOTHING)

BEGIN;

-- ============================================
-- 1. guest_portal_sessions (new table)
-- ============================================
CREATE TABLE IF NOT EXISTS guest_portal_sessions (
    id          SERIAL PRIMARY KEY,
    token       VARCHAR(64) NOT NULL UNIQUE,
    booking_id  INTEGER NOT NULL REFERENCES bookings(booking_id),
    room_id     INTEGER REFERENCES rooms(room_id),
    guest_id    INTEGER REFERENCES guests(guest_id),
    status      VARCHAR(10) NOT NULL DEFAULT 'active',
    expires_at  TIMESTAMP,
    created_by  INTEGER REFERENCES users(user_id),
    created_at  TIMESTAMP DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_portal_sessions_token   ON guest_portal_sessions(token);
CREATE INDEX IF NOT EXISTS idx_portal_sessions_booking ON guest_portal_sessions(booking_id);
CREATE INDEX IF NOT EXISTS idx_portal_sessions_room    ON guest_portal_sessions(room_id);
CREATE INDEX IF NOT EXISTS idx_portal_sessions_status  ON guest_portal_sessions(status);

-- ============================================
-- 2. guest_requests (new table)
-- ============================================
CREATE TABLE IF NOT EXISTS guest_requests (
    id              SERIAL PRIMARY KEY,
    booking_id      INTEGER REFERENCES bookings(booking_id),
    room_id         INTEGER REFERENCES rooms(room_id),
    guest_id        INTEGER REFERENCES guests(guest_id),
    type            VARCHAR(16) NOT NULL,
    status          VARCHAR(16) NOT NULL DEFAULT 'requested',
    payload         TEXT,
    note            VARCHAR(300),
    amount          NUMERIC(10, 2),
    folio_charge_id INTEGER REFERENCES folio_charges(id),
    source          VARCHAR(8) NOT NULL DEFAULT 'portal',
    client_ref      VARCHAR(80) UNIQUE,
    handled_by      INTEGER REFERENCES users(user_id),
    created_at      TIMESTAMP DEFAULT now(),
    updated_at      TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_guest_requests_booking    ON guest_requests(booking_id);
CREATE INDEX IF NOT EXISTS idx_guest_requests_room       ON guest_requests(room_id);
CREATE INDEX IF NOT EXISTS idx_guest_requests_guest      ON guest_requests(guest_id);
CREATE INDEX IF NOT EXISTS idx_guest_requests_type       ON guest_requests(type);
CREATE INDEX IF NOT EXISTS idx_guest_requests_status     ON guest_requests(status);
CREATE INDEX IF NOT EXISTS idx_guest_requests_created_at ON guest_requests(created_at);

-- ============================================
-- 3. menu_items (new table)
-- ============================================
CREATE TABLE IF NOT EXISTS menu_items (
    id            SERIAL PRIMARY KEY,
    name          VARCHAR(150) NOT NULL,
    description   VARCHAR(300),
    category      VARCHAR(20) NOT NULL DEFAULT 'food',
    price         NUMERIC(10, 2) NOT NULL DEFAULT 0,
    gst_percent   DOUBLE PRECISION,
    is_available  BOOLEAN DEFAULT TRUE,
    sort_order    INTEGER NOT NULL DEFAULT 0,
    stock_item_id INTEGER REFERENCES stock_items(id),
    created_by    INTEGER REFERENCES users(user_id),
    created_at    TIMESTAMP DEFAULT now(),
    updated_by    INTEGER REFERENCES users(user_id),
    updated_at    TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_menu_items_name      ON menu_items(name);
CREATE INDEX IF NOT EXISTS idx_menu_items_category  ON menu_items(category);
CREATE INDEX IF NOT EXISTS idx_menu_items_available ON menu_items(is_available);

-- ============================================
-- 4. app_settings: guest-portal config seeds (never overwrites an admin edit)
--    Master toggle + per-feature toggles + WiFi. wifi_voucher_mode: 'auto' shows a per-stay code
--    immediately; 'manual' means the desk issues it when fulfilling the request.
-- ============================================
INSERT INTO app_settings (key, value) VALUES
    ('guest_portal_enabled',                 'true'),
    ('portal_room_service_enabled',          'true'),
    ('portal_wifi_enabled',                  'true'),
    ('portal_wakeup_enabled',                'true'),
    ('portal_cab_enabled',                   'true'),
    ('portal_contactless_checkout_enabled',  'true'),
    ('wifi_ssid',                            'HotelBhimas-Guest'),
    ('wifi_voucher_mode',                    'auto')
ON CONFLICT (key) DO NOTHING;

COMMIT;

-- ============================================
-- VERIFICATION (run manually if desired)
-- ============================================
-- SELECT column_name FROM information_schema.columns WHERE table_name='guest_portal_sessions' ORDER BY ordinal_position;
-- SELECT column_name FROM information_schema.columns WHERE table_name='guest_requests' ORDER BY ordinal_position;
-- SELECT column_name FROM information_schema.columns WHERE table_name='menu_items' ORDER BY ordinal_position;
-- SELECT key, value FROM app_settings WHERE key LIKE 'portal_%' OR key LIKE 'guest_portal_%' OR key LIKE 'wifi_%' ORDER BY key;
