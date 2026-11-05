-- P1 batch 3 (owner, 4-5 Nov): Go-MMT's monthly TDS / TCS / commission statements, booking by booking.
-- feedback@zen2-makemytrip.com mails an .xlsx per statement (and a PDF invoice / credit memo for
-- commission). Each mail becomes one ota_tax_statements row (the files kept for the CA) and one
-- ota_tax_statement_lines row per booking row of this hotel's account (HTLDOM014889). Amounts are
-- normalised so TDS and TCS are positive "deducted" figures; a credit memo stays negative.
-- services/ota_statements.py owns this. Design: HotelLock/docs/ota-payment-tracking-plan.md section 8.

CREATE TABLE IF NOT EXISTS ota_tax_statements (
    id SERIAL PRIMARY KEY,
    kind VARCHAR(16) NOT NULL,                 -- tds | tcs | commission | credit_memo
    period DATE NOT NULL,                      -- first day of the month the statement is for
    ota_account_code VARCHAR(30),
    message_id VARCHAR(200),
    subject VARCHAR(300),
    mail_date TIMESTAMP,
    source VARCHAR(10) NOT NULL DEFAULT 'mail',   -- mail | upload
    file_name VARCHAR(200),
    file_data BYTEA,
    pdf_name VARCHAR(200),
    pdf_data BYTEA,
    doc_no VARCHAR(60),                        -- commission invoice / credit memo number
    row_count INTEGER NOT NULL DEFAULT 0,
    other_rows INTEGER NOT NULL DEFAULT 0,     -- rows of another property's account, not stored
    total_base NUMERIC(14, 2),
    total_gst NUMERIC(14, 2),
    total_amount NUMERIC(14, 2),
    superseded BOOLEAN NOT NULL DEFAULT FALSE, -- a later TDS/TCS statement for the same month replaced it
    created_at TIMESTAMP DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_ota_tax_statements_message_id
    ON ota_tax_statements (message_id) WHERE message_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_ota_tax_statements_kind_period ON ota_tax_statements (kind, period);

CREATE TABLE IF NOT EXISTS ota_tax_statement_lines (
    id SERIAL PRIMARY KEY,
    statement_id INTEGER NOT NULL REFERENCES ota_tax_statements(id) ON DELETE CASCADE,
    ota_booking_id VARCHAR(80) NOT NULL,
    pnr VARCHAR(120),
    guest_name VARCHAR(120),
    booking_date DATE,
    check_in DATE,
    check_out DATE,
    brand VARCHAR(30),
    doc_no VARCHAR(60),
    base_amount NUMERIC(14, 2),
    gst_amount NUMERIC(14, 2),
    amount NUMERIC(14, 2) NOT NULL,
    booking_id INTEGER REFERENCES bookings(booking_id)
);
CREATE INDEX IF NOT EXISTS ix_ota_tax_statement_lines_statement ON ota_tax_statement_lines (statement_id);
CREATE INDEX IF NOT EXISTS ix_ota_tax_statement_lines_ota_booking_id ON ota_tax_statement_lines (ota_booking_id);
CREATE INDEX IF NOT EXISTS ix_ota_tax_statement_lines_doc_no ON ota_tax_statement_lines (doc_no);
