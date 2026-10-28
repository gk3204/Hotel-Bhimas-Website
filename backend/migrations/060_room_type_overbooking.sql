-- v8m (owner, 28 Oct): "a toggle like allow overbooking for each room type, which when turned on
-- should make the booking available and will be handled by the reception team".
-- Off by default for every type. When on, reception / admin may book (and extend) up to
-- overbook_limit rooms beyond what the type has free on its tightest night; the booking is marked
-- OVERBOOKED for the desk. The website and automatic OTA imports are never overbooked.
ALTER TABLE room_types ADD COLUMN IF NOT EXISTS allow_overbooking BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE room_types ADD COLUMN IF NOT EXISTS overbook_limit INTEGER NOT NULL DEFAULT 1;
