-- 039: OTA booking-ID verification.
-- An OTA booking is only treated as prepaid once VERIFIED — either a matching OTA email imported,
-- or (no email yet) an owner-approval OTP at the desk. Tracks how it was verified.
-- Existing OTA bookings are grandfathered as verified so history is not retro-flagged. Idempotent.
ALTER TABLE bookings ADD COLUMN IF NOT EXISTS ota_verified BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE bookings ADD COLUMN IF NOT EXISTS ota_verified_at TIMESTAMP NULL;
ALTER TABLE bookings ADD COLUMN IF NOT EXISTS ota_verified_source VARCHAR(16) NULL;  -- email | owner_otp | legacy

UPDATE bookings
   SET ota_verified = TRUE, ota_verified_source = 'legacy'
 WHERE ota_booking_id IS NOT NULL
   AND ota_verified = FALSE
   AND ota_verified_source IS NULL;
