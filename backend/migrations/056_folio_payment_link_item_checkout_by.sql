-- v7e (owner, 13 Oct). Additive and idempotent.

-- (4) "In check-out invoice, for the payments made indicate the time of payment receipt/refund and
-- also RCPT number like RCPT-1". A folio's payment / refund lines did not say WHICH payment they
-- were, so the invoice could print neither the receipt number nor the moment of payment.
ALTER TABLE folio_charges ADD COLUMN IF NOT EXISTS payment_id INTEGER NULL REFERENCES payments(payment_id);
CREATE INDEX IF NOT EXISTS ix_folio_charges_payment_id ON folio_charges (payment_id);

-- (5) a room that leaves on its own (a per-room bill) records who checked it out, like the booking.
ALTER TABLE booking_items ADD COLUMN IF NOT EXISTS checked_out_by INTEGER NULL REFERENCES users(user_id);
UPDATE booking_items i
   SET checked_out_by = b.checked_out_by
  FROM bookings b
 WHERE i.booking_id = b.booking_id AND i.checked_out_by IS NULL
   AND i.checked_out_at IS NOT NULL AND b.checked_out_by IS NOT NULL;

-- Backfill the payment link, best-effort; a line left unmatched simply prints its posting time.
-- a) refund lines name their refund id: "Refund — <reason> (DESK-xxxx)"
UPDATE folio_charges fc
   SET payment_id = p.payment_id
  FROM folios f, payments p
 WHERE fc.payment_id IS NULL AND fc.type = 'payment' AND fc.amount > 0
   AND f.id = fc.folio_id AND p.booking_id = f.booking_id
   AND p.refund_id IS NOT NULL AND fc.description LIKE '%(' || p.refund_id || ')%';

-- b) online / gateway lines name the gateway id ("Advance paid — razorpay pay_x", "Desk upi_qr — upi (pay_x)")
UPDATE folio_charges fc
   SET payment_id = p.payment_id
  FROM folios f, payments p
 WHERE fc.payment_id IS NULL AND fc.type = 'payment' AND fc.amount < 0
   AND f.id = fc.folio_id AND p.booking_id = f.booking_id AND p.status = 'paid'
   AND ((p.payment_id_gateway IS NOT NULL AND fc.description LIKE '%' || p.payment_id_gateway || '%')
        OR (p.order_id IS NOT NULL AND fc.description LIKE '%' || p.order_id || '%'));

-- c) desk payments: the one paid payment of that booking for that amount, taken within 5 minutes
UPDATE folio_charges fc
   SET payment_id = m.payment_id
  FROM (SELECT fc2.id AS charge_id, min(p.payment_id) AS payment_id, count(*) AS n
          FROM folio_charges fc2
          JOIN folios f ON f.id = fc2.folio_id
          JOIN payments p ON p.booking_id = f.booking_id AND p.status = 'paid'
         WHERE fc2.payment_id IS NULL AND fc2.type = 'payment' AND fc2.amount < 0
           AND fc2.description NOT LIKE 'Prepaid to %'
           AND fc2.description NOT LIKE 'Transferred to company%'
           AND abs(extract(epoch FROM (fc2.posted_at - p.created_at))) <= 300
           AND (abs(fc2.amount) = p.amount
                OR EXISTS (SELECT 1 FROM payment_allocations pa
                            WHERE pa.payment_id = p.payment_id AND pa.folio_id = fc2.folio_id
                              AND pa.amount = abs(fc2.amount)))
         GROUP BY fc2.id) m
 WHERE fc.id = m.charge_id AND m.n = 1;

-- d) a deposit return that refunded exactly one payment of the booking, at that moment
UPDATE folio_charges fc
   SET payment_id = m.payment_id
  FROM (SELECT fc2.id AS charge_id, min(p.payment_id) AS payment_id, count(*) AS n
          FROM folio_charges fc2
          JOIN folios f ON f.id = fc2.folio_id
          JOIN payments p ON p.booking_id = f.booking_id AND p.refund_status = 'completed'
         WHERE fc2.payment_id IS NULL AND fc2.type = 'payment' AND fc2.amount > 0
           AND fc2.description LIKE 'Deposit return%'
           AND p.refunded_at IS NOT NULL
           AND abs(extract(epoch FROM (fc2.posted_at - p.refunded_at))) <= 300
         GROUP BY fc2.id) m
 WHERE fc.id = m.charge_id AND m.n = 1;
