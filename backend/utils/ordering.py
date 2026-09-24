"""List ordering — one place for the sort keys every screen shares (v5s).

The owner's ask was "ordering and sorting on all pages … room related based on room number, menu
related alphabetical on names, guest related guest name". Auditing that turned up more than a
tidiness problem, which is why these keys live in a module instead of being retyped at each site:

  * `rooms.room_number` is `VARCHAR(10)`, so `ORDER BY room_number` is a TEXT sort. On this property's
    real 65 rooms that produces `1, 10, 11, … 19, 2, 23, … 5, 50, … 7, 70, 8, 9`. Ten call sites had
    that bug, and one of them (`reports._booking_room_label`) used the text order to decide WHICH ROOM
    a room-service charge was billed to — so the sort key was moving money.
  * a booking's room labels were joined unordered for the fire evacuation roll-call and the police
    register, which could print a two-room stay as "303, 301".
  * guest names in the real data include `'sathiskumar'`, `'Sathis Kumar'` and `'SRINIVASAN S'`; a raw
    `ORDER BY name` scatters those, so name ordering is case-insensitive here.

Every list also wants a UNIQUE tie-breaker as its last key. Two rows sharing a sort value are free to
swap places between requests — which is the subtle version of "the order keeps changing" — and with
`OFFSET`/`LIMIT` paging it makes rows appear twice or vanish.
"""
from sqlalchemy import BigInteger, func

# --------------------------------------------------------------------------- rooms


def room_number_expr(col):
    """The numeric value of a room label, for ORDER BY: 9 sorts before 10.

    Rooms are numeric by construction — `reception._room_code` refuses a non-numeric room number on
    both the check-in and the room-shift path, whatever the lock type — so a numeric key is safe.
    It is still written defensively: the digits are extracted rather than cast directly, so a label
    that somehow contains a letter sorts by its digits instead of raising a DataError mid-request.
    """
    return func.nullif(func.regexp_replace(col, r"\D", "", "g"), "").cast(BigInteger)


def room_number_key(col):
    """ORDER BY key for a room label: numeric first, then the label itself.

    Use as `query.order_by(*room_number_key(Room.room_number))`. The trailing label keeps the order
    total when two labels share a numeric value ("7" and "7A"), and puts a label with no digits at
    all last rather than first.
    """
    return (room_number_expr(col).nullslast(), col)


def room_number_sort_key(room_number):
    """Python-side equivalent, for lists already in memory (report rows, joined room labels).

    Returns a tuple, so `sorted(rooms, key=room_number_sort_key)` matches what the database does.
    """
    digits = "".join(c for c in str(room_number or "") if c.isdigit())
    return (0, int(digits), str(room_number or "")) if digits else (1, 0, str(room_number or ""))


def room_labels(labels):
    """A booking's room labels in room order — what gets joined with ", " for display and print.

    This is the fire-roll-call / police-register fix: those documents listed a multi-room stay's
    rooms in whatever order the join returned.
    """
    return sorted({str(x) for x in labels if x not in (None, "")}, key=room_number_sort_key)


def first_room_label(labels):
    """The LOWEST room of a stay, for the reports that attribute a charge to one room. Text order
    used to decide this, which meant room 10 could be picked over room 9."""
    ordered = room_labels(labels)
    return ordered[0] if ordered else None


# --------------------------------------------------------------------------- names


def name_key(col):
    """Case-insensitive alphabetical, blanks last — for room types, vendors, companies, agents,
    staff, stock items and every other master-data list."""
    return (func.nullif(func.trim(col), "").is_(None), func.lower(func.trim(col)))


def guest_name_key(col):
    """Guests by name. Same rule as `name_key`; named separately because it is the one the owner
    asked for by name and it reads better at the call sites."""
    return name_key(col)


def name_sort_key(value):
    """Python-side equivalent of `name_key`."""
    s = str(value or "").strip()
    return (1, "") if not s else (0, s.casefold())


# --------------------------------------------------------------------------- menu


def menu_item_key(category_col, sort_order_col, name_col):
    """Menu items: category, then the owner's curated `sort_order`, then name.

    The owner asked for "menu related alphabetical on names", but `menu_items.sort_order` is already
    populated for all 268 items (10, 20, 30 … 2680) and is the menu-card sequence. They confirmed the
    curated order wins and name is only the tie-breaker; the API already did `sort_order, name`, so
    this only adds the category grouping.
    """
    return (func.lower(func.coalesce(category_col, "")), sort_order_col, func.lower(name_col))
