-- P2a (owner, 5-6 Nov): Yatra's per-booking commission invoices and credit notes are stored in the same
-- ota_tax_statements table as Go-MMT's monthly statements, so each row now says whose document it is.
-- Every row before this migration is a Go-MMT statement (P1 batch 3 read only Go-MMT mail).
ALTER TABLE ota_tax_statements ADD COLUMN IF NOT EXISTS channel VARCHAR(20) NOT NULL DEFAULT 'go_mmt';
-- the OTA's GSTIN as printed on its commission invoice / credit note (Yatra 27AAACY2602D1ZS) - the GST
-- input-credit register (P2b) lists the supplier's GSTIN per invoice
ALTER TABLE ota_tax_statements ADD COLUMN IF NOT EXISTS supplier_gstin VARCHAR(20);
-- a Yatra credit note names the invoice it reverses
ALTER TABLE ota_tax_statements ADD COLUMN IF NOT EXISTS ref_doc_no VARCHAR(60);
CREATE INDEX IF NOT EXISTS ix_ota_tax_statements_channel_kind_period ON ota_tax_statements (channel, kind, period);
