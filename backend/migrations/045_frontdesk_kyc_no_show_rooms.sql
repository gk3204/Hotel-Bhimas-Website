-- Migration: 045_frontdesk_kyc_no_show_rooms.sql
-- Description: v5n — per-room KYC, per-room money attribution, and the no-show go-live cutoff.
-- Purpose:
--   * booking_guests.room_id   — WHICH room each occupant is in. Until now the roster was flat, so a
--                                3-room group's IDs could not be told apart and the registration slip
--                                said only "Guests in the room". Required by the new `per_room` ID
--                                rule, which demands one identified guest per assigned room.
--   * booking_guests.phone     — that room's own contact number (optional). Lets the desk send each
--                                room its own in-room portal link instead of every link going to the
--                                one number the booking was made with.
--   * stay_events.room_id      — which room an early check-in / late arrival / hourly extension
--                                applies to, so the arrival-exceptions report names the room and a
--                                void only clears that room's fee.
--   * folio_charges.room_id    — which room an extra (room service, laundry) belongs to. Room NIGHTS
--                                were always attributable through booking_item_id; everything else
--                                was booking-level, so a 3-room family's food all read as room 1.
--   * app_settings             — checkin_id_scope / checkin_scans_required (the admin-configurable
--                                ID rule) and no_show_from_date (the automatic no-show sweep ignores
--                                stays that ended before go-live; seeded with today's date, i.e. the
--                                day this migration runs on that database).
-- Additive and idempotent.

ALTER TABLE booking_guests ADD COLUMN IF NOT EXISTS room_id INTEGER NULL REFERENCES rooms(room_id);
ALTER TABLE booking_guests ADD COLUMN IF NOT EXISTS phone VARCHAR(20) NULL;
CREATE INDEX IF NOT EXISTS ix_booking_guests_room ON booking_guests(room_id);

ALTER TABLE stay_events ADD COLUMN IF NOT EXISTS room_id INTEGER NULL REFERENCES rooms(room_id);

ALTER TABLE folio_charges ADD COLUMN IF NOT EXISTS room_id INTEGER NULL REFERENCES rooms(room_id);
CREATE INDEX IF NOT EXISTS ix_folio_charges_room ON folio_charges(room_id);

-- Front-desk policy. `per_room` ships as the default: one responsible guest per room is what the
-- owner asked for, and it does NOT depend on bookings.adults, which is unreliable (the website asks
-- for one occupancy number per booking, so a 3-room reservation can arrive with adults = 1).
INSERT INTO app_settings (key, value)
SELECT 'checkin_id_scope', 'per_room'
WHERE NOT EXISTS (SELECT 1 FROM app_settings WHERE key = 'checkin_id_scope');

INSERT INTO app_settings (key, value)
SELECT 'checkin_scans_required', 'true'
WHERE NOT EXISTS (SELECT 1 FROM app_settings WHERE key = 'checkin_scans_required');

-- The automatic no-show sweep only looks at stays whose check-out date is on or after this date.
-- Seeded with the day the migration runs, so an existing database never has its history swept.
INSERT INTO app_settings (key, value)
SELECT 'no_show_from_date', to_char(CURRENT_DATE, 'YYYY-MM-DD')
WHERE NOT EXISTS (SELECT 1 FROM app_settings WHERE key = 'no_show_from_date');
