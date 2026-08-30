-- Migration: 027_sales_reports_shift_split.sql
-- Description: Product-wise room-service sales + shift-wise payment split (v3 items 4 & 6).
-- Purpose:
--   1. folio_charges.menu_item_id
--      The owner asked "which dishes actually sell?". Until now nothing linked a folio line
--      back to the dish: FolioCharge had no menu reference, and guest_requests.folio_charge_id
--      stores only the FIRST charge of a multi-line order. The only alternative was parsing
--      the order payload JSON, which cannot see a voided charge and so would never reconcile
--      with the revenue reports. This column is the durable link, written in exactly one
--      place (services/room_service.post_to_folio).
--
--   2. idx_payments_shift_id
--      Desk payments now stamp shift_id for EVERY method, not just cash, so a shift can be
--      reported as cash / card / UPI / bank. The shift-payments report groups on this column
--      across a date range, so it needs the index. (The column itself already exists and
--      already declares index=True on the model — this is belt-and-braces for databases
--      created before that flag, and is a no-op where the index is already present.)
--
-- Database: PostgreSQL
-- Idempotent: YES (ADD COLUMN IF NOT EXISTS / CREATE INDEX IF NOT EXISTS)
--
-- NOTE: create_all() does NOT add columns to an existing table, so this file must be run
-- against any database that already has folio_charges.
--
-- NOTE: this migration does NOT backfill menu_item_id. Charges posted before it stay NULL and
-- are simply absent from the product-wise report; run backend/scripts/backfill_menu_item_id.py
-- to attribute the historic ones (dry-run by default).

BEGIN;

ALTER TABLE folio_charges ADD COLUMN IF NOT EXISTS menu_item_id INTEGER;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE constraint_name = 'fk_folio_charges_menu_item'
    ) THEN
        ALTER TABLE folio_charges
            ADD CONSTRAINT fk_folio_charges_menu_item
            FOREIGN KEY (menu_item_id) REFERENCES menu_items(id);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_folio_charges_menu_item_id ON folio_charges(menu_item_id);
CREATE INDEX IF NOT EXISTS idx_payments_shift_id ON payments(shift_id);

COMMENT ON COLUMN folio_charges.menu_item_id IS
    'Room-service dish this line came from (v3 item 4). Set only by room_service.post_to_folio, '
    'one charge per menu line. NULL on every non-room-service charge and on lines posted before '
    'migration 027 — the product-wise sales report reads exactly the non-NULL, non-void rows.';

COMMIT;
