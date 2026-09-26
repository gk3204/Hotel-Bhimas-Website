-- 050 — the lost-card fee, and a GST billing address that stops overwriting the guest's home one.
--
-- (1) LOST CARDS. The registration slip this hotel prints already promises that "a lost/unreturned
-- card is chargeable" (utils/settings.py), and the desk has had a "Reissue lost card" button since
-- v4b1 — but it recorded the loss and billed nothing. Two links are missing before a fee can exist:
--
--   * `replaces_card_id` — which card this one replaces. Today that only exists inside the audit
--     blob, so nothing can ask "has this lost card already been charged for?" — and it has to be
--     asked, because `POST /cards/issue` legitimately runs TWICE for one reissue (Card Management
--     pre-checks with encoded=false, and a valid pre-check records a row). The fee is idempotent on
--     the LOST card, not on the request.
--   * `fee_charge_id` — the folio line the fee was posted as. `stay_events.folio_charge_id` is the
--     same idea for arrival fees: the money lives on the folio, and the row points at it so a void
--     can be traced back.
--
-- The fee AMOUNT is a setting, not a column, and it ships at 0 — an upgrade must not start charging
-- guests on its own. Set it in Settings → Front desk before the feature does anything.
--
-- (2) GST BILLING ADDRESS. `_save_gst_to_profile` wrote the invoice's billing address into
-- `guest_profiles.address` — the same column KYC fills and the POLICE REGISTER prints. A guest who
-- gave their company's address to get a GST invoice had their home address in the statutory
-- register replaced by their office. `gst_address` separates the two: the register keeps the
-- residential address, the invoice keeps the billing one.
--
-- Additive and idempotent. No backfill: existing cards have no replacement link to reconstruct, and
-- an existing profile's `address` stays exactly where it is (read as the GST address only when no
-- `gst_address` has been given, so nothing on a past invoice changes).

ALTER TABLE card_issuances ADD COLUMN IF NOT EXISTS replaces_card_id INTEGER
    REFERENCES card_issuances(id);
ALTER TABLE card_issuances ADD COLUMN IF NOT EXISTS fee_charge_id INTEGER
    REFERENCES folio_charges(id);

CREATE INDEX IF NOT EXISTS ix_card_issuances_replaces ON card_issuances (replaces_card_id);

ALTER TABLE guest_profiles ADD COLUMN IF NOT EXISTS gst_address VARCHAR(300);
