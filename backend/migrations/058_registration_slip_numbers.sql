-- v7m (owner, 15 Oct): "all registration slips should have a unique number, visible on the slip -
-- don't use the booking id as the slip number". Additive and idempotent.
--
-- One row per slip: a booking's rooms that arrived together are one slip (a split arrival - two
-- cars - is two slips). `room_key` is the slip's room ids, sorted and comma-joined, so a reprint of
-- the same slip finds the same row and keeps its number. The number is REG-<id>.
CREATE TABLE IF NOT EXISTS registration_slips (
    id          SERIAL PRIMARY KEY,
    booking_id  INTEGER      NOT NULL REFERENCES bookings(booking_id),
    room_key    VARCHAR(200) NOT NULL,
    created_at  TIMESTAMP    NOT NULL DEFAULT now(),   -- UTC
    created_by  INTEGER      NULL REFERENCES users(user_id),
    CONSTRAINT uq_registration_slip_booking_rooms UNIQUE (booking_id, room_key)
);
CREATE INDEX IF NOT EXISTS ix_registration_slips_booking ON registration_slips (booking_id);
