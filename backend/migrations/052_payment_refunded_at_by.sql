-- v6m.12: WHEN a refund was paid out and WHO paid it.
-- The refund voucher printed the time it was PRINTED and the original collector, because neither
-- fact was stored. Additive and idempotent.
ALTER TABLE payments ADD COLUMN IF NOT EXISTS refunded_at TIMESTAMP NULL;
ALTER TABLE payments ADD COLUMN IF NOT EXISTS refunded_by INTEGER NULL REFERENCES users(user_id);

-- Backfill from the folio credit line each refund posted (posted_at is UTC, like the new column).
-- Desk / gateway refunds name their refund id in the line's description.
UPDATE payments p
   SET refunded_at = fc.posted_at, refunded_by = fc.posted_by
  FROM folio_charges fc
 WHERE p.refunded_at IS NULL
   AND p.refund_status = 'completed'
   AND p.refund_id IS NOT NULL
   AND fc.type = 'payment'
   AND fc.description LIKE '%' || p.refund_id || '%';

-- "Deposit return" (excess returned at checkout) lines carry no refund id: take the latest such line on
-- the same booking.
UPDATE payments p
   SET refunded_at = x.posted_at, refunded_by = x.posted_by
  FROM (SELECT DISTINCT ON (f.booking_id) f.booking_id, fc.posted_at, fc.posted_by
          FROM folio_charges fc JOIN folios f ON f.id = fc.folio_id
         WHERE fc.type = 'payment' AND fc.description LIKE 'Deposit return%'
         ORDER BY f.booking_id, fc.posted_at DESC) x
 WHERE p.refunded_at IS NULL
   AND p.refund_status = 'completed'
   AND p.refund_reason = 'Excess deposit return'
   AND p.booking_id = x.booking_id;
