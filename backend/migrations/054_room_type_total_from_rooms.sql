-- v6m.15 (owner, 12 Oct): "adjust room type count automatically based on count (rooms) group by room type".
-- room_types.total_rooms was typed in by hand and had drifted from the real rooms (production on
-- 12 Oct: Double Deluxe Ac said 16 for 12 rooms, Double Deluxe Non-Ac 8 for 12, Five Bed Non-Ac 4 for
-- 1). Availability is total_rooms - booked - out-of-service - held, so the website could sell 4 Double
-- Deluxe Ac rooms that do not exist and refused 4 Non-Ac rooms that do.
--
-- From now on the database keeps it equal to the number of rooms of that type (out-of-service rooms
-- included - availability subtracts those separately). A trigger, so every path that adds, deletes or
-- re-types a room - the API, a script, a manual SQL fix - keeps it right. Idempotent.

CREATE OR REPLACE FUNCTION sync_room_type_total() RETURNS trigger AS $$
BEGIN
    IF TG_OP IN ('INSERT', 'UPDATE') AND NEW.room_type_id IS NOT NULL THEN
        UPDATE room_types
           SET total_rooms = (SELECT count(*) FROM rooms WHERE room_type_id = NEW.room_type_id)
         WHERE room_type_id = NEW.room_type_id;
    END IF;
    IF TG_OP IN ('UPDATE', 'DELETE') AND OLD.room_type_id IS NOT NULL THEN
        UPDATE room_types
           SET total_rooms = (SELECT count(*) FROM rooms WHERE room_type_id = OLD.room_type_id)
         WHERE room_type_id = OLD.room_type_id;
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_rooms_sync_type_total ON rooms;
CREATE TRIGGER trg_rooms_sync_type_total
    AFTER INSERT OR DELETE OR UPDATE OF room_type_id ON rooms
    FOR EACH ROW EXECUTE FUNCTION sync_room_type_total();

-- ...and on the type itself: whoever inserts or edits a room_types row (the API, a script, a seed),
-- total_rooms is the real count, never a typed number.
CREATE OR REPLACE FUNCTION room_type_total_is_room_count() RETURNS trigger AS $$
BEGIN
    NEW.total_rooms := (SELECT count(*) FROM rooms WHERE room_type_id = NEW.room_type_id);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_room_types_total_is_count ON room_types;
CREATE TRIGGER trg_room_types_total_is_count
    BEFORE INSERT OR UPDATE OF total_rooms ON room_types
    FOR EACH ROW EXECUTE FUNCTION room_type_total_is_room_count();

-- bring every type in line now
UPDATE room_types rt
   SET total_rooms = (SELECT count(*) FROM rooms r WHERE r.room_type_id = rt.room_type_id)
 WHERE total_rooms IS DISTINCT FROM (SELECT count(*) FROM rooms r WHERE r.room_type_id = rt.room_type_id);
