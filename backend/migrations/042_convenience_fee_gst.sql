-- Migration: 042_convenience_fee_gst.sql
-- Description: File existing convenience-fee folio lines under the 18% GST slab.
-- Purpose:
--   The website-booking fee line ("Convenience fee (online booking)") was posted with a NULL
--   gst_percent and the desk-collect fee line with gst_percent = 0, although the amount on both
--   is the fee PLUS 18% GST on it. The GST invoice groups charge lines by gst_percent, so these
--   landed in a spurious "0%" row. Going forward folio.open_folio / payments._mark_payment_paid
--   post them with the real rate; this backfills the rows already on file. The invoice PDF
--   recomputes the GST summary live from folio_charges, so reprints of existing invoices are
--   corrected as well. Idempotent — re-running finds nothing left to update.
UPDATE folio_charges
   SET gst_percent = 18
 WHERE type = 'misc'
   AND description LIKE 'Convenience fee%'
   AND (gst_percent IS NULL OR gst_percent = 0);
