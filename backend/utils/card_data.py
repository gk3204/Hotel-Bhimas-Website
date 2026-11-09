"""What a key card's data says (v8y) - the server's copy of the desk's ReceptionApp.Encoder.CardCheck.

The encoder gives the card's DATA, not a chip serial number, so two cards are told apart by what is
written on them. From production read-backs (830 of 830 guest cards, Nov 2026):

    a written card   = "551501C9283EEF" + "E3"|"63" + (0x80 + the lock's room number) + ...
    an erased card   = "551501C9283EEF" + "FFFF..."   - identical for every card: NO room is left on it
    an unreadable read = "5515..." without the header (e.g. "55150081FFFF...")

So a live card names the room it opens, and the key block (chars 14..46) identifies the card itself;
a blank card names nothing - it cannot be told from any other blank card in the drawer.
"""

HEADER = "551501C9283EEF"


def _clean(data) -> str:
    return (data or "").strip().rstrip("\x00").upper()


def is_card(data) -> bool:
    d = _clean(data)
    return len(d) >= 20 and d.startswith(HEADER)


def is_blank(data) -> bool:
    """A card of this system with no key on it (wiped, or never written)."""
    d = _clean(data)
    if not is_card(d):
        return False
    body = d[len(HEADER):]
    return body.count("F") + body.count("0") >= len(body) * 0.8


def key_block(data):
    """The part of a written card that identifies it (the desk matches on the same slice)."""
    d = _clean(data)
    if not is_card(d) or is_blank(d) or len(d) < 46:
        return None
    return d[14:46]


def card_lock_room(data):
    """The lock room number a written card opens, or None (blank / unreadable / not this system)."""
    d = _clean(data)
    if not is_card(d) or is_blank(d) or len(d) < 18:
        return None
    try:
        v = int(d[16:18], 16) - 0x80
    except ValueError:
        return None
    return v if 0 < v < 128 else None


def room_lock_number(room):
    """The number the room's LOCK is programmed with: RR of rooms.lock_no (BBFFRRAA), else the room
    number (v8c - a door can carry another number, e.g. room 29's lock is 79). None when it cannot be
    on a card (a card holds 1..127), so a room numbered beyond that is simply not room-checked."""
    v = _lock_number(room)
    return v if v is not None and 0 < v < 128 else None


def _lock_number(room):
    lock_no = "".join(ch for ch in str(getattr(room, "lock_no", "") or "") if ch.isdigit())
    if len(lock_no) >= 6:
        try:
            return int(lock_no[4:6])
        except ValueError:
            pass
    try:
        return int(str(getattr(room, "room_number", "")).strip())
    except ValueError:
        return None
