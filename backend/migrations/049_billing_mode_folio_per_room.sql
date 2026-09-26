-- 049 — one bill for the group, or one bill per room (Batch D).
--
-- The owner asked for the choice to be made WHEN THE BOOKING IS TAKEN: a family on one card wants a
-- single bill; three colleagues on one corporate reservation each want their own. Batch C already
-- gave every room its own arrival, departure and subtotal; this gives it its own BILL when asked
-- for — its own folio, its own invoice number, its own settlement.
--
-- ⚠️ `folios.booking_id` was UNIQUE, and **39 places** in the backend do
-- `query(Folio).filter(Folio.booking_id == X).first()` on the strength of it. The constraint is
-- replaced by two PARTIAL unique indexes rather than simply dropped, so the database still refuses
-- the two states that would corrupt a bill:
--
--   * `uq_folios_booking_group` — at most ONE booking-level folio per booking (booking_item_id
--     IS NULL). This is the group-mode folio, and it is exactly what exists today.
--   * `uq_folios_booking_item`  — at most ONE folio per booking item. This is a room's own bill.
--
-- A booking is therefore in exactly one shape: one null-item folio (group), or N item folios
-- (per room). Mixing them is possible to write but never produced by the code, and the resolver
-- in `services/folio_resolver.py` states which shape a booking is in.
--
-- Idempotent and additive. Existing folios keep `booking_item_id = NULL`, existing bookings get
-- `billing_mode = 'group'`, and a stay already in the database behaves exactly as it did before.

ALTER TABLE bookings ADD COLUMN IF NOT EXISTS billing_mode VARCHAR(10) NOT NULL DEFAULT 'group';

ALTER TABLE folios ADD COLUMN IF NOT EXISTS booking_item_id INTEGER
    REFERENCES booking_items(booking_item_id);

-- Drop whatever UNIQUE constraint the column carries. The name is `folios_booking_id_key` on a
-- database built by SQLAlchemy's create_all, but it is looked up rather than assumed, because a
-- database restored from a dump can carry a different one and a wrong guess would leave the old
-- constraint in place and per-room billing failing at the first insert.
DO $$
DECLARE c RECORD;
BEGIN
    FOR c IN
        SELECT con.conname
          FROM pg_constraint con
          JOIN pg_class rel ON rel.oid = con.conrelid
          JOIN pg_attribute att ON att.attrelid = rel.oid AND att.attnum = ANY (con.conkey)
         WHERE rel.relname = 'folios'
           AND con.contype = 'u'
           AND array_length(con.conkey, 1) = 1
           AND att.attname = 'booking_id'
    LOOP
        EXECUTE format('ALTER TABLE folios DROP CONSTRAINT %I', c.conname);
    END LOOP;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS uq_folios_booking_group
    ON folios (booking_id) WHERE booking_item_id IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_folios_booking_item
    ON folios (booking_item_id) WHERE booking_item_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_folios_booking_id ON folios (booking_id);

-- Which rooms a payment was applied to, when a booking bills per room. A payment is taken against
-- the BOOKING (the guest hands over one card), so in per-room mode it is allocated across the open
-- room folios and this records where it landed. Without it a refund could not be unwound.
CREATE TABLE IF NOT EXISTS payment_allocations (
    id           SERIAL PRIMARY KEY,
    payment_id   INTEGER NOT NULL REFERENCES payments(payment_id),
    folio_id     INTEGER NOT NULL REFERENCES folios(id),
    amount       NUMERIC(10, 2) NOT NULL DEFAULT 0,
    created_at   TIMESTAMP DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_payment_allocations_payment ON payment_allocations (payment_id);
CREATE INDEX IF NOT EXISTS ix_payment_allocations_folio ON payment_allocations (folio_id);
