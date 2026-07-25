-- Migration: 020_inventory_complaints.sql
-- Description: Round-2 batch B (prompt 18c, slices 8 & 11).
-- Purpose:
--   1. stock_items / stock_movements -> inventory. The hotel holds stockable items (minibar
--      drinks, toiletries, linen, supplies). stock_movements is an APPEND-ONLY ledger (never
--      updated/deleted, same posture as company_ledger); stock_items.current_qty is a running
--      snapshot maintained from movements. Low stock = current_qty <= reorder_threshold, alerted
--      once per episode via low_stock_alerted_at. Consumption links to the minibar folio_charge;
--      receiving can link to a petty-cash expense (+ vendor).
--   2. maintenance_tickets (source='guest' = a guest complaint) -> additive SLA / escalation /
--      compensation columns, plus a new APPEND-ONLY ticket_escalations trail. No new complaints
--      table: complaints ARE guest-sourced tickets (created by the WhatsApp webhook, the review
--      pipeline, and now the front desk). Maintenance tickets leave the new columns NULL/0.
--   3. app_settings -> seed inventory + complaint config. Never overwrites an admin edit.
-- New tables/columns are ALSO auto-created by SQLAlchemy create_all; included here for prod parity via psql.
-- Database: PostgreSQL
-- Idempotent: YES (IF NOT EXISTS / ADD COLUMN IF NOT EXISTS / ON CONFLICT DO NOTHING)

BEGIN;

-- ============================================
-- 1. stock_items (new table)
-- ============================================
CREATE TABLE IF NOT EXISTS stock_items (
    id                  SERIAL PRIMARY KEY,
    name                VARCHAR(150) NOT NULL,
    sku                 VARCHAR(50) UNIQUE,
    category            VARCHAR(30) NOT NULL DEFAULT 'other',
    unit                VARCHAR(20) NOT NULL DEFAULT 'pcs',
    reorder_threshold   NUMERIC(10, 2) NOT NULL DEFAULT 0,
    current_qty         NUMERIC(10, 2) NOT NULL DEFAULT 0,
    sale_price          NUMERIC(10, 2),
    gst_percent         DOUBLE PRECISION,
    vendor_id           INTEGER REFERENCES vendors(id),
    is_active           BOOLEAN DEFAULT TRUE,
    notes               TEXT,
    low_stock_alerted_at TIMESTAMP,
    created_by          INTEGER REFERENCES users(user_id),
    created_at          TIMESTAMP DEFAULT now(),
    updated_by          INTEGER REFERENCES users(user_id),
    updated_at          TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_stock_items_name       ON stock_items(name);
CREATE INDEX IF NOT EXISTS idx_stock_items_sku        ON stock_items(sku);
CREATE INDEX IF NOT EXISTS idx_stock_items_category   ON stock_items(category);
CREATE INDEX IF NOT EXISTS idx_stock_items_vendor     ON stock_items(vendor_id);
CREATE INDEX IF NOT EXISTS idx_stock_items_is_active  ON stock_items(is_active);
CREATE INDEX IF NOT EXISTS idx_stock_items_created_at ON stock_items(created_at);

-- ============================================
-- 2. stock_movements (new table) -- APPEND-ONLY stock ledger
--    delta_qty > 0 = stock in (receive / positive adjust);
--    delta_qty < 0 = stock out (consume / wastage / negative adjust).
-- ============================================
CREATE TABLE IF NOT EXISTS stock_movements (
    id              SERIAL PRIMARY KEY,
    item_id         INTEGER NOT NULL REFERENCES stock_items(id),
    type            VARCHAR(10) NOT NULL,
    delta_qty       NUMERIC(10, 2) NOT NULL DEFAULT 0,
    qty_after       NUMERIC(10, 2),
    unit_cost       NUMERIC(10, 2),
    reason          VARCHAR(200),
    folio_charge_id INTEGER REFERENCES folio_charges(id),
    expense_id      INTEGER REFERENCES expenses(id),
    booking_id      INTEGER REFERENCES bookings(booking_id),
    client_ref      VARCHAR(80) UNIQUE,
    created_by      INTEGER REFERENCES users(user_id),
    created_at      TIMESTAMP DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_stock_movements_item       ON stock_movements(item_id);
CREATE INDEX IF NOT EXISTS idx_stock_movements_type       ON stock_movements(type);
CREATE INDEX IF NOT EXISTS idx_stock_movements_folio_chg  ON stock_movements(folio_charge_id);
CREATE INDEX IF NOT EXISTS idx_stock_movements_expense    ON stock_movements(expense_id);
CREATE INDEX IF NOT EXISTS idx_stock_movements_booking    ON stock_movements(booking_id);
CREATE INDEX IF NOT EXISTS idx_stock_movements_created_by ON stock_movements(created_by);
CREATE INDEX IF NOT EXISTS idx_stock_movements_created_at ON stock_movements(created_at);

-- ============================================
-- 3. ticket_escalations (new table) -- APPEND-ONLY complaint escalation trail
-- ============================================
CREATE TABLE IF NOT EXISTS ticket_escalations (
    id          SERIAL PRIMARY KEY,
    ticket_id   INTEGER NOT NULL REFERENCES maintenance_tickets(id),
    level       INTEGER NOT NULL DEFAULT 1,
    reason      VARCHAR(200),
    notified    BOOLEAN DEFAULT FALSE,
    created_by  INTEGER REFERENCES users(user_id),
    created_at  TIMESTAMP DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_ticket_escalations_ticket     ON ticket_escalations(ticket_id);
CREATE INDEX IF NOT EXISTS idx_ticket_escalations_created_at ON ticket_escalations(created_at);

-- ============================================
-- 4. Additive columns on maintenance_tickets (complaint SLA / escalation / compensation)
--    Only meaningful for source='guest' complaints; maintenance tickets leave them NULL/0.
-- ============================================
ALTER TABLE maintenance_tickets ADD COLUMN IF NOT EXISTS sla_response_due_at    TIMESTAMP;
ALTER TABLE maintenance_tickets ADD COLUMN IF NOT EXISTS sla_resolve_due_at     TIMESTAMP;
ALTER TABLE maintenance_tickets ADD COLUMN IF NOT EXISTS first_responded_at     TIMESTAMP;
ALTER TABLE maintenance_tickets ADD COLUMN IF NOT EXISTS escalation_level       INTEGER NOT NULL DEFAULT 0;
ALTER TABLE maintenance_tickets ADD COLUMN IF NOT EXISTS last_escalated_at      TIMESTAMP;
ALTER TABLE maintenance_tickets ADD COLUMN IF NOT EXISTS compensation_charge_id INTEGER REFERENCES folio_charges(id);
ALTER TABLE maintenance_tickets ADD COLUMN IF NOT EXISTS compensation_amount    NUMERIC(10, 2);
CREATE INDEX IF NOT EXISTS idx_maint_tickets_sla_response ON maintenance_tickets(sla_response_due_at);
CREATE INDEX IF NOT EXISTS idx_maint_tickets_sla_resolve  ON maintenance_tickets(sla_resolve_due_at);

-- ============================================
-- 5. app_settings: inventory + complaint config seeds (never overwrites an admin edit)
--    SLA hours are per-priority; escalation on by default, auto-compensation OFF by default
--    (compensation is always an admin, reason-required, audited action).
-- ============================================
INSERT INTO app_settings (key, value) VALUES
    ('low_stock_alerts_enabled',          'true'),
    ('low_stock_default_threshold',       '5'),
    ('complaint_sla_response_hours_urgent', '1'),
    ('complaint_sla_response_hours_high',   '2'),
    ('complaint_sla_response_hours_normal', '4'),
    ('complaint_sla_response_hours_low',    '8'),
    ('complaint_sla_resolve_hours_urgent',  '4'),
    ('complaint_sla_resolve_hours_high',    '8'),
    ('complaint_sla_resolve_hours_normal', '24'),
    ('complaint_sla_resolve_hours_low',    '48'),
    ('complaint_escalation_enabled',       'true'),
    ('complaint_auto_compensation_enabled', 'false')
ON CONFLICT (key) DO NOTHING;

COMMIT;

-- ============================================
-- VERIFICATION (run manually if desired)
-- ============================================
-- SELECT column_name FROM information_schema.columns WHERE table_name='stock_items' ORDER BY ordinal_position;
-- SELECT column_name FROM information_schema.columns WHERE table_name='stock_movements' ORDER BY ordinal_position;
-- SELECT column_name FROM information_schema.columns WHERE table_name='ticket_escalations' ORDER BY ordinal_position;
-- SELECT column_name FROM information_schema.columns WHERE table_name='maintenance_tickets' AND column_name LIKE 'sla%' OR column_name LIKE '%escalat%' OR column_name LIKE 'compensation%';
-- SELECT key, value FROM app_settings WHERE key LIKE 'low_stock_%' OR key LIKE 'complaint_%' ORDER BY key;
