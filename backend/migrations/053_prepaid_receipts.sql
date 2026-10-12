-- v6m.13 (owner list 11 Oct, item 4): a receipt for money the guest paid BEFORE arriving -
-- to the OTA (MakeMyTrip / Goibibo / Yatra...), to a travel agent, or on the website (Razorpay).
-- Issued once per booking at check-in, dated that moment. It is a document, NOT a Payment row:
-- an OTA stay deliberately has no Payment (prepaid_amount carries it), and a website payment
-- already has its own. Cash reports list it as a memo, never in the drawer.
CREATE TABLE IF NOT EXISTS prepaid_receipts (
    id           SERIAL PRIMARY KEY,
    booking_id   INTEGER NOT NULL UNIQUE REFERENCES bookings(booking_id),
    kind         VARCHAR(10)  NOT NULL,            -- ota | website | agent
    channel      VARCHAR(60)  NOT NULL,            -- printed: MakeMyTrip / Website (Razorpay) / Travel agent - X
    amount       NUMERIC(12, 2) NOT NULL DEFAULT 0,
    net_to_hotel NUMERIC(12, 2) NULL,              -- OTA: the payout after commission, when known
    reference    VARCHAR(200) NULL,                -- OTA booking id / Razorpay payment id(s)
    payment_ids  VARCHAR(200) NULL,                -- website: the Payment rows it covers (comma list)
    issued_at    TIMESTAMP NOT NULL DEFAULT now(), -- UTC, like every instant
    issued_by    INTEGER NULL REFERENCES users(user_id)
);
CREATE INDEX IF NOT EXISTS ix_prepaid_receipts_issued_at ON prepaid_receipts (issued_at);

-- item 5: the daily receipts ZIP is ON by default - add it to a stored EOD report list.
UPDATE app_settings
   SET value = value || ',receipts_zip'
 WHERE key = 'eod_report_keys'
   AND value IS NOT NULL AND value <> ''
   AND position('receipts_zip' in value) = 0;
