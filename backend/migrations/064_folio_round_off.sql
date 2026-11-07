-- v8t (owner, 6 Nov 2026): every bill is rounded to the nearest rupee.
-- The round-off is kept on the bill (folio), NOT as a charge line, so it is never taxable value
-- and never reaches a GST / revenue report that sums the lines. folio.total / balance include it.
-- Set by routers.folio._recompute while the bill is open; frozen once it is invoiced.
ALTER TABLE folios ADD COLUMN IF NOT EXISTS round_off NUMERIC(10, 2) NOT NULL DEFAULT 0;
