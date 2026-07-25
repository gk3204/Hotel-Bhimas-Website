-- Migration: 008_rates_agent_pricing.sql
-- Description: Rates & travel-agent pricing (Milestone 5, prompt 10). Price depends on
--              room type x channel/agent x date; per-agent commission is tracked + settled.
-- Purpose:
--   1. travel_agents  -> travel agents / OTA partners (commission %, credit limit)
--   2. agent_rates    -> negotiated per-night rate cards (agent x room type, date window)
--   3. rate_plans     -> channel/date rate overrides (seasonal / weekend / per-agent, priority)
--   4. agent_payments -> commission payouts (the 'paid' side of the settlement report)
--   5. bookings       -> agent_id, commission_percent, commission_amount (snapshot columns)
-- Rates are stored per-night, GST-EXCLUSIVE (same basis as room_types.price_per_night).
-- All new tables/columns are ALSO auto-created by SQLAlchemy create_all; included here for
-- explicit prod parity / running against an existing deployed DB via psql.
-- Database: PostgreSQL
-- Idempotent: YES (information_schema checks + IF NOT EXISTS)

BEGIN;

-- ============================================
-- 1. travel_agents
-- ============================================
CREATE TABLE IF NOT EXISTS travel_agents (
    id                 SERIAL PRIMARY KEY,
    name               VARCHAR(100) NOT NULL,
    contact            VARCHAR(100),
    gst_no             VARCHAR(20),
    commission_percent NUMERIC(5, 2) NOT NULL DEFAULT 0,
    credit_limit       NUMERIC(12, 2) NOT NULL DEFAULT 0,
    is_active          BOOLEAN NOT NULL DEFAULT TRUE,
    created_at         TIMESTAMP DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_travel_agents_name ON travel_agents(name);
CREATE INDEX IF NOT EXISTS idx_travel_agents_active ON travel_agents(is_active);

-- ============================================
-- 2. agent_rates
-- ============================================
CREATE TABLE IF NOT EXISTS agent_rates (
    id           SERIAL PRIMARY KEY,
    agent_id     INTEGER NOT NULL REFERENCES travel_agents(id),
    room_type_id INTEGER NOT NULL REFERENCES room_types(room_type_id),
    rate         NUMERIC(10, 2) NOT NULL,
    valid_from   DATE,
    valid_to     DATE,
    created_at   TIMESTAMP DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_agent_rates_agent ON agent_rates(agent_id);
CREATE INDEX IF NOT EXISTS idx_agent_rates_room_type ON agent_rates(room_type_id);
CREATE INDEX IF NOT EXISTS idx_agent_rate_lookup ON agent_rates(agent_id, room_type_id);

-- ============================================
-- 3. rate_plans
-- ============================================
CREATE TABLE IF NOT EXISTS rate_plans (
    id           SERIAL PRIMARY KEY,
    room_type_id INTEGER NOT NULL REFERENCES room_types(room_type_id),
    channel      VARCHAR(20) NOT NULL,
    agent_id     INTEGER REFERENCES travel_agents(id),
    price        NUMERIC(10, 2) NOT NULL,
    valid_from   DATE,
    valid_to     DATE,
    days_of_week VARCHAR(20),
    priority     INTEGER NOT NULL DEFAULT 0,
    is_active    BOOLEAN NOT NULL DEFAULT TRUE,
    created_at   TIMESTAMP DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_rate_plans_room_type ON rate_plans(room_type_id);
CREATE INDEX IF NOT EXISTS idx_rate_plans_channel ON rate_plans(channel);
CREATE INDEX IF NOT EXISTS idx_rate_plans_agent ON rate_plans(agent_id);
CREATE INDEX IF NOT EXISTS idx_rate_plans_active ON rate_plans(is_active);
CREATE INDEX IF NOT EXISTS idx_rate_plan_lookup ON rate_plans(room_type_id, channel);

-- ============================================
-- 4. agent_payments
-- ============================================
CREATE TABLE IF NOT EXISTS agent_payments (
    id         SERIAL PRIMARY KEY,
    agent_id   INTEGER NOT NULL REFERENCES travel_agents(id),
    amount     NUMERIC(12, 2) NOT NULL,
    paid_on    DATE NOT NULL,
    mode       VARCHAR(20),
    reference  VARCHAR(100),
    note       VARCHAR(200),
    created_by INTEGER REFERENCES users(user_id),
    created_at TIMESTAMP DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_agent_payments_agent ON agent_payments(agent_id);
CREATE INDEX IF NOT EXISTS idx_agent_payments_created_at ON agent_payments(created_at);

-- ============================================
-- 5. ALTER bookings (agent link + commission snapshot)
-- ============================================
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='bookings' AND column_name='agent_id') THEN
    ALTER TABLE bookings ADD COLUMN agent_id INTEGER REFERENCES travel_agents(id);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='bookings' AND column_name='commission_percent') THEN
    ALTER TABLE bookings ADD COLUMN commission_percent NUMERIC(5, 2);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='bookings' AND column_name='commission_amount') THEN
    ALTER TABLE bookings ADD COLUMN commission_amount NUMERIC(10, 2);
  END IF;
END $$;
CREATE INDEX IF NOT EXISTS idx_bookings_agent ON bookings(agent_id);

COMMIT;

-- ============================================
-- VERIFICATION (run manually if desired)
-- ============================================
-- SELECT table_name FROM information_schema.tables
--   WHERE table_name IN ('travel_agents','agent_rates','rate_plans','agent_payments');
-- SELECT column_name FROM information_schema.columns
--   WHERE table_name='bookings' AND column_name IN ('agent_id','commission_percent','commission_amount');
