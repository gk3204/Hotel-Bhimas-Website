-- 038: per-room-type "show on website" flag.
-- A room type can be sold at the desk / OTA / agents while being hidden from the public website.
-- Defaults TRUE so every existing type stays visible until the owner hides it. Idempotent.
ALTER TABLE room_types ADD COLUMN IF NOT EXISTS show_on_website BOOLEAN NOT NULL DEFAULT TRUE;
