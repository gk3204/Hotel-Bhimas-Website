"""Phone numbers — one place for normalising, comparing and refusing them (v5r).

Before this module the codebase had no phone validation at all: nine different Pydantic fields each
picked their own `min_length`, nothing checked digits, and `"aaaaaaa"` was an acceptable guest phone
everywhere. Two owner rules made that untenable:

  * a multi-room booking must give a DIFFERENT number for each room, so numbers have to be compared
    rather than merely stored — and `+91 98765 43210`, `09876543210` and `9876543210` are one number;
  * an OTA booking arrives carrying the CHANNEL's number (MakeMyTrip's call centre, or the synthetic
    placeholder the PMS stamps when the voucher masks the guest), and that must be refused as a guest
    number at check-in.

`normalize` is deliberately the same rule `utils.whatsapp_service.normalize_number` has always used —
digits only, a bare 10-digit Indian number gains its 91 — so a number stored by one path and matched by
the other cannot disagree. That function now delegates here.
"""
import logging

logger = logging.getLogger(__name__)

def digits(phone) -> str:
    """Just the digits of whatever was typed (empty string for None/garbage)."""
    return "".join(ch for ch in str(phone or "") if ch.isdigit())


def normalize(phone) -> str | None:
    """E.164-ish digits with no '+' — the form Meta wants and the form we compare on.

    A bare 10-digit Indian number gains its 91; a leading 0 (as in `09876543210`) is dropped so the
    two spellings of one mobile match. Returns None when there is nothing usable.
    """
    d = digits(phone)
    if not d:
        return None
    if len(d) == 11 and d.startswith("0"):
        d = d[1:]                     # 0 + 10-digit local mobile
    if len(d) == 10:
        d = "91" + d
    return d


def same_number(a, b) -> bool:
    """True when two spellings are the same phone. False if either is missing."""
    na, nb = normalize(a), normalize(b)
    return bool(na and nb and na == nb)


def is_valid_mobile(phone) -> bool:
    """A number the hotel could actually call or WhatsApp.

    Accepts an Indian mobile (10 digits starting 6-9, with or without the 91) and an international
    number of 11-15 digits that carries some other country code. Rejects short numbers, zero padding,
    a run of one repeated digit, and 10-digit numbers starting 0-5 — which is what an OTA-id
    placeholder usually looks like, and what this exists to catch.

    Judged on the RAW digits, not on `normalize`'s output: normalize bolts 91 onto any bare 10-digit
    number, so testing the normalised form would let `5876543210` pass as a 12-digit international
    number. (It did, in the first cut of this module.)
    """
    d = digits(phone)
    if not d:
        return False
    if len(d) == 11 and d.startswith("0"):
        d = d[1:]
    if len(set(d)) <= 2:              # 0000000000, 1111111111, 9999900000 - never a real number
        return False
    # Toll-free / service lines are never a guest's contact, and they are exactly what an OTA prints in
    # the voucher body ("For assistance call 1800 102 4444"). Covers Indian 1800/1860/1861/1900/1600/140
    # and US-style 1-800, both of which are 11 digits starting 18.
    if d.startswith(("1800", "1860", "1861", "1900", "1600", "140")) or (len(d) == 11 and d.startswith("18")):
        return False
    if len(d) == 10:
        return d[0] in "6789"         # Indian mobile
    if len(d) == 12 and d.startswith("91"):
        return d[2] in "6789"         # 91 + Indian mobile
    return 11 <= len(d) <= 15 and not d.startswith("91")   # some other country code


def ota_placeholder(ota_booking_id) -> str:
    """The stand-in number stamped when an OTA voucher masks the guest's phone.

    Go-MMT and most OTAs do not put the guest's number in the voucher at all, so confirming a draft
    cannot require one: the last 10 digits of the OTA booking id are used instead (unique per booking,
    so two OTA guests never merge into one guest record). The desk replaces it with the real number at
    check-in — which v5r now enforces.

    This used to live in routers/ota.py, where services/ota_service.py could not see it: the auto-confirm
    path called it anyway and raised NameError on every masked-phone voucher, and a broad `except` logged
    that as a harmless "skipped draft". No OTA booking was ever created. Keeping the helper here, with
    both callers importing it, is what makes that class of mistake impossible.
    """
    d = digits(ota_booking_id)
    return (d[-10:] if len(d) >= 10 else d.rjust(10, "0")) or "0000000000"


def is_ota_placeholder(phone, ota_booking_id=None) -> bool:
    """True when `phone` is a stamped placeholder rather than a real number.

    Checks the exact placeholder for this booking when the OTA id is known, and otherwise falls back to
    the shape: a 10-digit number that is not a valid mobile (zero-padded, or starting 0-5) is a
    placeholder for practical purposes — `is_valid_mobile` is the same test the desk shows a warning on.
    """
    if not phone:
        return False
    if ota_booking_id and same_number(phone, ota_placeholder(ota_booking_id)):
        return True
    return not is_valid_mobile(phone)


# --------------------------------------------------------------------------------------- blocked list

BLOCKED_PHONES_KEY = "blocked_guest_phones"


def blocked_numbers(db) -> list[str]:
    """Normalised numbers the admin has blocked as guest contacts — the OTAs' own call-centre and
    masked-relay numbers, added from Settings as the desk meets them.

    Free text, comma or semicolon separated, so the owner can paste `+91 124 462 8747; 9876543210`
    exactly as it appears on a voucher. Same shape as `owner_whatsapp`/`whatsapp_service.owner_numbers`.
    """
    from utils.settings import get_setting
    raw = get_setting(db, BLOCKED_PHONES_KEY, "") or ""
    out, seen = [], set()
    for part in raw.replace(";", ",").replace("\n", ",").split(","):
        n = normalize(part.strip())
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    return out


def is_blocked(db, phone) -> bool:
    """True when this number is on the admin's blocked list."""
    n = normalize(phone)
    return bool(n and n in blocked_numbers(db))


def describe(phone) -> str:
    """A short human form for error messages: the last four digits, never the whole number."""
    d = digits(phone)
    return (f"ending {d[-4:]}") if len(d) >= 4 else (d or "(blank)")
