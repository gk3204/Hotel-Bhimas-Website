-- Migration: 043_arrival_rules_noshow_gst.sql
-- Description: v5m — arrival & departure rules, no-shows, hourly extension, B2B GST invoice.
-- Purpose:
--   * bookings.expected_arrival_at   — booked date + expected time (OTA forced 12:00); the reference
--                                      the early/late arrival rules measure the guest's deviation from.
--   * bookings.stay_started_at       — the anchor the 24h stay clock runs from (set at check-in per the
--                                      rules: actual arrival, or the expected time when late beyond
--                                      threshold). booking_checkout_moment prefers it over checked_in_at.
--   * bookings.checkout_extended_until — a planned hourly extension: an explicit checkout moment that
--                                      card window / overstay sweep / reminders follow.
--   * bookings.no_show_at            — set when a confirmed booking's check-out date passed unarrived.
--   * bookings.original_check_in     — booked check-in before an admin re-date (pairs original_check_out).
--   * stay_events                    — one row per early check-in / late arrival / hourly extension:
--                                      the source of truth for the arrival-exceptions report + daily digest.
--   * invoices.buyer_*               — B2B snapshot (GSTIN, legal name, address, place-of-supply code).
--   * guest_profiles.gst_*           — the guest's GST identity captured pre-arrival / at the desk.
--   * app_settings.arrival_rules     — the JSON rule set (seeded with defaults; admin-editable).
-- Additive and idempotent.

ALTER TABLE bookings ADD COLUMN IF NOT EXISTS expected_arrival_at TIMESTAMP NULL;
ALTER TABLE bookings ADD COLUMN IF NOT EXISTS stay_started_at TIMESTAMP NULL;
ALTER TABLE bookings ADD COLUMN IF NOT EXISTS checkout_extended_until TIMESTAMP NULL;
ALTER TABLE bookings ADD COLUMN IF NOT EXISTS no_show_at TIMESTAMP NULL;
ALTER TABLE bookings ADD COLUMN IF NOT EXISTS original_check_in DATE NULL;

-- Backfill the expected arrival for live bookings from the booked date + time (OTA -> noon).
UPDATE bookings b
   SET expected_arrival_at = (b.check_in::timestamp + COALESCE(
         CASE WHEN b.booking_source IN ('makemytrip','goibibo','booking_com','agoda','yatra','other_ota')
              THEN TIME '12:00' ELSE b.check_in_time END, TIME '12:00'))
 WHERE b.expected_arrival_at IS NULL
   AND b.status IN ('confirmed', 'checked_in');

CREATE TABLE IF NOT EXISTS stay_events (
    id                SERIAL PRIMARY KEY,
    booking_id        INTEGER NOT NULL REFERENCES bookings(booking_id),
    kind              VARCHAR(24) NOT NULL,          -- early_checkin | late_arrival | hourly_extension
    expected_at       TIMESTAMP NULL,
    actual_at         TIMESTAMP NULL,
    deviation_minutes INTEGER NULL,
    hours             INTEGER NULL,
    charge_amount     NUMERIC(10,2) NOT NULL DEFAULT 0,
    charge_basis      VARCHAR(16) NOT NULL DEFAULT 'free',  -- free|fixed|percent|full_night|exempt_comp|override|voided
    rule_json         TEXT NULL,
    approval          VARCHAR(12) NOT NULL DEFAULT 'none',  -- none|admin|owner_otp
    approved_by       INTEGER NULL REFERENCES users(user_id),
    folio_charge_id   INTEGER NULL REFERENCES folio_charges(id),
    created_by        INTEGER NULL REFERENCES users(user_id),
    created_at        TIMESTAMP NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_stay_events_booking ON stay_events(booking_id);
CREATE INDEX IF NOT EXISTS ix_stay_events_created ON stay_events(created_at);
CREATE INDEX IF NOT EXISTS ix_stay_events_kind ON stay_events(kind);

ALTER TABLE invoices ADD COLUMN IF NOT EXISTS buyer_name VARCHAR(160) NULL;
ALTER TABLE invoices ADD COLUMN IF NOT EXISTS buyer_gstin VARCHAR(20) NULL;
ALTER TABLE invoices ADD COLUMN IF NOT EXISTS buyer_address VARCHAR(300) NULL;
ALTER TABLE invoices ADD COLUMN IF NOT EXISTS buyer_state_code VARCHAR(4) NULL;

ALTER TABLE guest_profiles ADD COLUMN IF NOT EXISTS gst_legal_name VARCHAR(160) NULL;
ALTER TABLE guest_profiles ADD COLUMN IF NOT EXISTS gst_state_code VARCHAR(4) NULL;

INSERT INTO app_settings (key, value)
SELECT 'arrival_rules',
       '{"exempt_comp": true,'
       ' "early_checkin": [{"sources": ["makemytrip","goibibo","booking_com","agoda","yatra","other_ota","website"],'
       '                    "threshold_minutes": 30, "full_night_after_minutes": 360,'
       '                    "charge": {"mode": "fixed", "amount": 300}, "approval": "on_change"},'
       '                   {"sources": ["*"], "threshold_minutes": 120, "full_night_after_minutes": 480,'
       '                    "charge": {"mode": "percent", "percent": 25}, "approval": "on_change"}],'
       ' "late_arrival": [{"sources": ["*"], "threshold_minutes": 120}],'
       ' "hourly_extension": [{"sources": ["*"], "threshold_minutes": 30, "full_night_after_minutes": 360,'
       '                       "max_hours": 8, "overflow": "refuse",'
       '                       "charge": {"mode": "percent", "percent": 25}, "approval": "on_change"}]}'
WHERE NOT EXISTS (SELECT 1 FROM app_settings WHERE key = 'arrival_rules');
