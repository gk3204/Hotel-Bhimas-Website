-- Migration: 046_ota_rooms_phone_rules.sql
-- Description: v5r — OTA vouchers keep their room count and their hold reason; phone rules get a
--              blocked-numbers list and auto-confirm becomes an admin choice.
-- Purpose:
--   * ota_draft_bookings.rooms       — how many ROOMS the voucher is for. The count was always in the
--                                     email ("TOTAL NO OF ROOMS 2", "2 x Double Deluxe Ac") and always
--                                     discarded, so every OTA booking was created as ONE room and the
--                                     rest of a multi-room reservation stayed on sale in the PMS.
--   * ota_draft_bookings.last_error  — why a draft is still pending. Auto-confirm failures went only to
--                                     the log; one of them (a NameError on every masked-phone voucher)
--                                     silently stopped all OTA bookings for weeks while the drafts
--                                     screen gave the desk no clue.
--   * app_settings.ota_auto_confirm_mode / _max_variance_percent
--                                    — the owner's choice of confirming vouchers automatically or by
--                                      hand, separately for single- and multi-room, plus a money guard:
--                                      when the voucher total and the PMS price disagree by more than
--                                      the tolerance the draft is held for review.
--   * app_settings.blocked_guest_phones
--                                    — numbers that may never be stored as a guest contact: the OTAs'
--                                      call-centre and relay numbers. Free text, comma/semicolon
--                                      separated; ships empty and is refused at check-in.
-- Additive and idempotent.

ALTER TABLE ota_draft_bookings ADD COLUMN IF NOT EXISTS rooms INTEGER NULL;
ALTER TABLE ota_draft_bookings ADD COLUMN IF NOT EXISTS last_error TEXT NULL;

INSERT INTO app_settings (key, value)
SELECT 'ota_auto_confirm_mode', 'single_only'
WHERE NOT EXISTS (SELECT 1 FROM app_settings WHERE key = 'ota_auto_confirm_mode');

INSERT INTO app_settings (key, value)
SELECT 'ota_auto_confirm_max_variance_percent', '5'
WHERE NOT EXISTS (SELECT 1 FROM app_settings WHERE key = 'ota_auto_confirm_max_variance_percent');

INSERT INTO app_settings (key, value)
SELECT 'blocked_guest_phones', ''
WHERE NOT EXISTS (SELECT 1 FROM app_settings WHERE key = 'blocked_guest_phones');
