-- Migration: 011_housekeeping.sql
-- Description: Housekeeping + maintenance module (prompt 13).
-- Purpose:
--   1. housekeeping_status  -> current cleaning state per room (one row/room, upserted)
--   2. housekeeping_tasks    -> cleaning/inspection tasks (auto-created on checkout/room-move)
--   3. maintenance_tickets   -> maintenance issues raised -> assigned -> tracked -> verified
--   4. ticket_items          -> parts/materials a ticket needs; approved items post to expenses
--   5. app_settings seed     -> housekeeping_auto_inspect (mark-clean also inspects when 'true')
-- Cleaning status is set by the HOUSEKEEPER role only (reception cannot — anti-fraud). A room
-- becomes re-sellable only after a supervisor/admin marks it inspected (Room.status -> vacant).
-- All new tables/columns are ALSO auto-created by SQLAlchemy create_all; included here for
-- explicit prod parity / running against a deployed DB via psql.
-- Database: PostgreSQL
-- Idempotent: YES (IF NOT EXISTS + ON CONFLICT DO NOTHING)

BEGIN;

-- ============================================
-- 1. housekeeping_status (one current row per room)
-- ============================================
CREATE TABLE IF NOT EXISTS housekeeping_status (
    id          SERIAL PRIMARY KEY,
    room_id     INTEGER NOT NULL UNIQUE REFERENCES rooms(room_id),
    status      VARCHAR(20) NOT NULL DEFAULT 'clean',   -- dirty|cleaning|clean|inspected|maintenance|dnd
    updated_by  INTEGER REFERENCES users(user_id),
    updated_at  TIMESTAMP,
    photo_url   TEXT                                     -- text reference only (photo upload -> prompt 14/R2)
);
CREATE INDEX IF NOT EXISTS idx_hk_status_status ON housekeeping_status(status);

-- ============================================
-- 2. housekeeping_tasks (cleaning/inspection work items)
-- ============================================
CREATE TABLE IF NOT EXISTS housekeeping_tasks (
    id          SERIAL PRIMARY KEY,
    room_id     INTEGER NOT NULL REFERENCES rooms(room_id),
    type        VARCHAR(20) NOT NULL DEFAULT 'checkout_clean',  -- checkout_clean|touchup|deep|inspection
    assigned_to INTEGER REFERENCES users(user_id),              -- NULL = pooled (any housekeeper)
    status      VARCHAR(20) NOT NULL DEFAULT 'pending',         -- pending|in_progress|done
    booking_id  INTEGER REFERENCES bookings(booking_id),
    checklist   TEXT,                                           -- JSON text
    photo_url   TEXT,
    client_ref  VARCHAR(80),
    created_at  TIMESTAMP DEFAULT now(),
    started_at  TIMESTAMP,
    done_at     TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_hk_tasks_room ON housekeeping_tasks(room_id);
CREATE INDEX IF NOT EXISTS idx_hk_tasks_status ON housekeeping_tasks(status);
CREATE INDEX IF NOT EXISTS idx_hk_tasks_assigned ON housekeeping_tasks(assigned_to);
CREATE UNIQUE INDEX IF NOT EXISTS idx_hk_tasks_client_ref ON housekeeping_tasks(client_ref);

-- ============================================
-- 3. maintenance_tickets
-- ============================================
CREATE TABLE IF NOT EXISTS maintenance_tickets (
    id               SERIAL PRIMARY KEY,
    room_id          INTEGER REFERENCES rooms(room_id),          -- NULL = common area
    area             VARCHAR(100),
    category         VARCHAR(20) NOT NULL DEFAULT 'other',        -- electrical|plumbing|carpentry|appliance|lock|other
    issue            VARCHAR(500) NOT NULL,
    photo_url        TEXT,
    priority         VARCHAR(10) NOT NULL DEFAULT 'normal',       -- low|normal|high|urgent
    status           VARCHAR(20) NOT NULL DEFAULT 'open',         -- open|assigned|in_progress|awaiting_parts|resolved|verified|closed
    source           VARCHAR(20) NOT NULL DEFAULT 'reception',    -- guest|reception|housekeeping|admin
    booking_id       INTEGER REFERENCES bookings(booking_id),
    raised_by        INTEGER REFERENCES users(user_id),
    assignee         INTEGER REFERENCES users(user_id),
    resolution_notes VARCHAR(500),
    verified_by      INTEGER REFERENCES users(user_id),
    client_ref       VARCHAR(80),
    created_at       TIMESTAMP DEFAULT now(),
    assigned_at      TIMESTAMP,
    resolved_at      TIMESTAMP,
    verified_at      TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_mt_status ON maintenance_tickets(status);
CREATE INDEX IF NOT EXISTS idx_mt_category ON maintenance_tickets(category);
CREATE INDEX IF NOT EXISTS idx_mt_assignee ON maintenance_tickets(assignee);
CREATE INDEX IF NOT EXISTS idx_mt_room ON maintenance_tickets(room_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_mt_client_ref ON maintenance_tickets(client_ref);

-- ============================================
-- 4. ticket_items (required parts/materials)
-- ============================================
CREATE TABLE IF NOT EXISTS ticket_items (
    id          SERIAL PRIMARY KEY,
    ticket_id   INTEGER NOT NULL REFERENCES maintenance_tickets(id),
    item        VARCHAR(200) NOT NULL,
    qty         NUMERIC(10,2) NOT NULL DEFAULT 1,
    est_cost    NUMERIC(10,2),
    status      VARCHAR(20) NOT NULL DEFAULT 'needed',   -- needed|approved|purchased|installed
    expense_id  INTEGER REFERENCES expenses(id),
    added_by    INTEGER REFERENCES users(user_id),
    added_at    TIMESTAMP DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_ticket_items_ticket ON ticket_items(ticket_id);
CREATE INDEX IF NOT EXISTS idx_ticket_items_status ON ticket_items(status);

-- ============================================
-- 5. Seed housekeeping config (only if absent — never overwrites an admin's value)
-- ============================================
INSERT INTO app_settings (key, value) VALUES
    ('housekeeping_auto_inspect', 'false')
ON CONFLICT (key) DO NOTHING;

COMMIT;

-- ============================================
-- VERIFICATION (run manually if desired)
-- ============================================
-- SELECT count(*) FROM housekeeping_status;
-- SELECT count(*) FROM housekeeping_tasks;
-- SELECT count(*) FROM maintenance_tickets;
-- SELECT count(*) FROM ticket_items;
-- SELECT key, value FROM app_settings WHERE key='housekeeping_auto_inspect';
