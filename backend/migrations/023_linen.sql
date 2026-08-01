-- Migration: 023_linen.sql
-- Description: Linen / laundry tracking (FE-9) — hotel-wide stage-count pool per item type.
-- Purpose:
--   linen_items      -> item catalog + stage counts (clean|dirty|at_laundry); launderable flag
--                       (False = consumable: only `clean` used as stock).
--   linen_sets       -> a room type's default linen/amenity set (item + qty).
--   linen_movements  -> append-only log of stage moves (checkout->dirty, send, receive, issue, replenish).
-- Launderable cycle: clean -> (checkout) dirty -> (send) at_laundry -> (receive) clean.
-- These tables are ALSO auto-created by SQLAlchemy create_all; included for explicit prod parity.
-- Database: PostgreSQL
-- Idempotent: YES (IF NOT EXISTS)

BEGIN;

CREATE TABLE IF NOT EXISTS linen_items (
    id                SERIAL PRIMARY KEY,
    name              VARCHAR(100) NOT NULL,
    unit              VARCHAR(20)  NOT NULL DEFAULT 'pcs',
    launderable       BOOLEAN      NOT NULL DEFAULT TRUE,
    reorder_threshold NUMERIC(10,2) NOT NULL DEFAULT 0,
    clean             NUMERIC(10,2) NOT NULL DEFAULT 0,
    dirty             NUMERIC(10,2) NOT NULL DEFAULT 0,
    at_laundry        NUMERIC(10,2) NOT NULL DEFAULT 0,
    is_active         BOOLEAN      NOT NULL DEFAULT TRUE,
    notes             VARCHAR(300),
    created_by        INTEGER REFERENCES users(user_id),
    created_at        TIMESTAMP DEFAULT NOW(),
    updated_by        INTEGER REFERENCES users(user_id),
    updated_at        TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_linen_items_launderable ON linen_items(launderable);
CREATE INDEX IF NOT EXISTS idx_linen_items_active ON linen_items(is_active);

CREATE TABLE IF NOT EXISTS linen_sets (
    id           SERIAL PRIMARY KEY,
    room_type_id INTEGER NOT NULL REFERENCES room_types(room_type_id),
    item_id      INTEGER NOT NULL REFERENCES linen_items(id),
    qty          NUMERIC(10,2) NOT NULL DEFAULT 0,
    CONSTRAINT uq_linen_set_roomtype_item UNIQUE (room_type_id, item_id)
);
CREATE INDEX IF NOT EXISTS idx_linen_sets_roomtype ON linen_sets(room_type_id);

CREATE TABLE IF NOT EXISTS linen_movements (
    id          SERIAL PRIMARY KEY,
    item_id     INTEGER NOT NULL REFERENCES linen_items(id),
    from_stage  VARCHAR(12),
    to_stage    VARCHAR(12),
    qty         NUMERIC(10,2) NOT NULL DEFAULT 0,
    reason      VARCHAR(200),
    room_id     INTEGER REFERENCES rooms(room_id),
    booking_id  INTEGER REFERENCES bookings(booking_id),
    created_by  INTEGER REFERENCES users(user_id),
    created_at  TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_linen_moves_item ON linen_movements(item_id);
CREATE INDEX IF NOT EXISTS idx_linen_moves_created ON linen_movements(created_at);

COMMIT;
