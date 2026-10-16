"""v7p (owner, 15 Oct): "make fraud summary and room detail (HK + fraud) readable - the owner should clearly
be able to understand what had happened, how etc".

Every fraud alert stores a type code and a JSON detail. This turns one into plain language:

  title     - what kind of thing it is              ("Card cut on a desk that is not allowed")
  sentence  - what happened, with room, times, people and money, in the hotel's clock
  short     - the same without the room prefix      (for a table that already shows the room)
  meaning   - why it matters / what it can indicate
  check     - what the owner should look at
  status    - "Not reviewed yet" / "Checked by X on 15/10 14:05 - note" / "Dismissed by X ..."

One place, used by the fraud summary report, Room Detail (HK + Fraud), the owner's daily HK report and
the web fraud dashboard, so they all say the same thing. An unknown type still reads as words.
"""
import json
from datetime import datetime

from utils import clock

CATALOGUE = {
    "off_station_issuance": {
        "title": "Key card cut on a desk that is not allowed to",
        "meaning": "Cards should only be cut at the front desk computers you allowed. A card cut elsewhere "
                   "can be a card made for someone without a booking.",
        "check": "Was the card for a real guest / cleaning? Who was on that computer at that time?"},
    "off_hours_issuance": {
        "title": "Key card cut outside the allowed hours",
        "meaning": "Cards cut late at night or at unusual hours are a common way to let someone stay off the books.",
        "check": "Was there a real arrival or room change at that time?"},
    "card_issue_without_approval": {
        "title": "Extra / replacement key card cut without your approval",
        "meaning": "A lost-card re-issue or an extra card needs your code. Without it, a spare working key "
                   "may be with someone who should not have one.",
        "check": "Ask who cut it and why; make sure the old card was wiped."},
    "max_cards_exceeded": {
        "title": "More key cards cut than the room allows",
        "meaning": "Too many working keys for one room - someone may have a key they should not.",
        "check": "How many guests were in the room, and were the extra cards returned?"},
    "card_without_booking": {
        "title": "A key card was still working with no paid guest in the room",
        "meaning": "A room door could be opened when nobody was booked in it - a sign of an unrecorded stay.",
        "check": "Was the room used? Was the card returned or wiped?"},
    "card_without_payment": {
        "title": "Guest key card cut with no payment recorded",
        "meaning": "The guest got a working key before any money was recorded for the stay.",
        "check": "Was the guest charged later? Look at the bill for this booking."},
    "cash_variance": {
        "title": "Cash in the drawer did not match at shift close",
        "meaning": "The counted cash was different from what the system expected for that shift.",
        "check": "Ask the cashier to explain the difference; look for an unrecorded payment or refund."},
    "cleaning_card_overdue": {
        "title": "Cleaning card not returned in time",
        "meaning": "A cleaning card that is kept can open the room later - for example to let someone in.",
        "check": "Find the card and check the room."},
    "viewing_card_overdue": {
        "title": "Room-viewing card not returned in time",
        "meaning": "A viewing card kept after the viewing can open the room - a guest may have stayed without a booking.",
        "check": "Find the card; check whether the room was used."},
    "cleaning_too_fast": {
        "title": "Room marked clean too quickly",
        "meaning": "Cleaning finished far faster than the minimum - the room was probably not really cleaned.",
        "check": "Inspect the room; ask the cleaner."},
    "cleaning_too_long": {
        "title": "Room kept 'in cleaning' far too long",
        "meaning": "A room left in cleaning for hours with no guest can be used for an off-the-books stay.",
        "check": "Was someone inside? Was the cleaning card out?"},
    "inspection_overdue": {
        "title": "Cleaned room not inspected in time",
        "meaning": "Nobody checked the room after cleaning - the room may not be ready for the next guest.",
        "check": "Have the supervisor inspect it."},
    "same_id_two_rooms": {
        "title": "Same guest ID used for more than one room",
        "meaning": "One ID used for several rooms can hide who is really staying in each room.",
        "check": "Make sure every room has its own guests' IDs."},
    "shift_old_card_live": {
        "title": "Old room's key still works after a room change",
        "meaning": "After the guest moved, their card for the old room was not wiped - it still opens that room.",
        "check": "Wipe or replace the old room's key before it is let again."},
    "overstay_unattended": {
        "title": "Guest stayed past check-out and charging stopped",
        "meaning": "The automatic overstay charge stopped after its day limit, but the guest is still in the room.",
        "check": "Check out or extend the guest at the desk."},
    "overstay_unbillable": {
        "title": "Guest stayed past check-out but could not be charged",
        "meaning": "The bill was already closed / invoiced, so the extra night could not be added.",
        "check": "Charge the night by hand or close the stay."},
    "paid_after_expiry": {
        "title": "Online payment arrived after the booking expired",
        "meaning": "Money was received for a booking that had already been released.",
        "check": "Confirm or refund the booking."},
    "repeated_refunds": {
        "title": "Many refunds to the same person / reference",
        "meaning": "Repeated refunds to one place can be money being moved out.",
        "check": "Check each refund against a real reason."},
    "ota_no_email": {
        "title": "OTA booking checked in without the OTA's confirmation email",
        "meaning": "There is no email from the OTA proving this booking - it may not be real or may be a different rate.",
        "check": "Check the booking on the OTA extranet."},
    "card_not_returned": {
        "title": "Cleaning / viewing card recorded LOST (not returned)",
        "meaning": "The card was never wiped - it still opens the room until it expires. A 'lost' card can be one "
                   "kept to let someone into the room.",
        "check": "Ask who had it; check the room until the card's time runs out."},
    "card_not_cut": {
        "title": "Guest let in / moved without cutting a key card",
        "meaning": "A check-in, extension or room change finished without a new key - the old key may still be in use.",
        "check": "Ask why no card was cut; check the room's lock."},
}


def _d(alert):
    try:
        d = json.loads(alert.detail) if isinstance(alert.detail, str) else (alert.detail or {})
        return d if isinstance(d, dict) else {}
    except (TypeError, ValueError):
        return {}


# The detectors wrote detail times in UTC until v6m.15 (deployed 14 Oct 2026 21:32 IST = 16:02 UTC) and in the
# hotel's clock since; card `valid_to` has always been the hotel's wall clock. `detected_at` is UTC.
_LOCAL_SINCE_UTC = datetime(2026, 10, 14, 16, 0)


def _when(v, alert=None, always_local=False):
    """A detail timestamp as 'dd/mm HH:MM' in the hotel's clock (see _LOCAL_SINCE_UTC)."""
    if not v:
        return ""
    try:
        s = str(v)
        if "+" in s[10:] or s.endswith("Z"):
            t = datetime.fromisoformat(s.replace("Z", "+00:00"))
            t = clock.to_local(t.replace(tzinfo=None) - t.utcoffset())
        else:
            t = datetime.fromisoformat(s[:26])
            stored_local = always_local or (alert is None or alert.detected_at is None
                                            or alert.detected_at >= _LOCAL_SINCE_UTC)
            if not stored_local:
                t = clock.to_local(t)
        return t.strftime("%d/%m %H:%M")
    except Exception:
        return str(v)[:16]


def _money(v):
    try:
        return f"Rs {float(v):,.2f}"
    except (TypeError, ValueError):
        return "Rs ?"


def _kind(issue_type):
    return {"cleaning": "cleaning", "viewing": "viewing", "checkin": "guest", "extend": "guest (extension)",
            "shift": "guest (room change)", "lost_reissue": "replacement", "extra": "extra guest"}.get(
        issue_type or "", issue_type or "key")


def _ctx(db, alert, cache):
    """Room number, guest and staff from the alert's own links (cached across a report)."""
    from models import Booking, CardIssuance, Room, User
    out = {"room": None, "guest": None, "staff": None}
    if alert.room_id:
        k = ("room", alert.room_id)
        if k not in cache:
            r = db.query(Room).filter(Room.room_id == alert.room_id).first()
            cache[k] = r.room_number if r else None
        out["room"] = cache[k]
    if alert.booking_id:
        k = ("bk", alert.booking_id)
        if k not in cache:
            b = db.query(Booking).filter(Booking.booking_id == alert.booking_id).first()
            cache[k] = (b.display_guest_name if b else None)
        out["guest"] = cache[k]
    uid = None
    if alert.card_id:
        k = ("card", alert.card_id)
        if k not in cache:
            c = db.query(CardIssuance).filter(CardIssuance.id == alert.card_id).first()
            cache[k] = (c.issued_by, c.issued_at, c.station_id) if c else (None, None, None)
        uid = cache[k][0]
    d = _d(alert)
    uid = uid or d.get("staff_id")
    if uid:
        k = ("user", uid)
        if k not in cache:
            u = db.query(User).filter(User.user_id == uid).first()
            cache[k] = (u.full_name or u.username) if u else None
        out["staff"] = cache[k]
    if not out["room"]:
        out["room"] = d.get("room_number") or d.get("room")
    return out


def _sentence(t, d, c, alert):
    """The 'what happened' text WITHOUT the room prefix."""
    by = f" by {c['staff']}" if c.get("staff") else ""
    bk = f" (booking #{alert.booking_id}{' - ' + c['guest'] if c.get('guest') else ''})" if alert.booking_id else ""
    if t == "off_station_issuance":
        st = d.get("station_id") or "an unknown desk (no station recorded)"
        allowed = ", ".join(d.get("allowed_stations") or []) or "-"
        return (f"a key card was cut at {_when(d.get('issued_at'), alert)}{by} on {st}; only {allowed} may cut cards{bk}.")
    if t == "off_hours_issuance":
        return (f"a key card was cut at {_when(d.get('issued_at'), alert)}{by} on {d.get('station_id') or 'a desk'}, "
                f"outside the allowed hours {d.get('allowed_hours') or ''}{bk}.")
    if t in ("card_issue_without_approval", "max_cards_exceeded"):
        what = _kind(d.get("issue_type"))
        extra = " and more cards than the room allows" if "max_cards_exceeded" in (d.get("reasons") or []) and t != "max_cards_exceeded" else ""
        core = ("more key cards were cut than the room allows" if t == "max_cards_exceeded"
                else f"a {what} card was cut without your approval code{extra}")
        return f"{core} - at {_when(d.get('client_issued_at'), alert)}{by} on {d.get('station_id') or 'a desk'}{bk}."
    if t == "card_without_booking":
        return (f"a {_kind(d.get('issue_type'))} card cut at {_when(d.get('issued_at'), alert)}{by} was still working when no paid "
                f"guest was in the room{bk}.")
    if t == "card_without_payment":
        return f"a guest key card was cut at {_when(d.get('issued_at'), alert)}{by} with no payment recorded{bk}."
    if t == "cash_variance":
        v = float(d.get("variance") or 0)
        return (f"shift #{d.get('shift_id')} on {d.get('station_id') or 'the desk'}{by}: counted {_money(d.get('counted_cash'))} "
                f"against {_money(d.get('expected_cash'))} expected - {'SHORT' if v < 0 else 'OVER'} by {_money(abs(v))}.")
    if t in ("cleaning_card_overdue", "viewing_card_overdue"):
        kind = "cleaning" if t.startswith("cleaning") else "viewing"
        return (f"the {kind} card cut at {_when(d.get('issued_at'), alert)}{by} (valid until {_when(d.get('valid_to'), alert, True)}) was "
                f"still not returned {d.get('minutes_overdue', '?')} min after it expired.")
    if t == "cleaning_too_fast":
        who = f", cleaned by {d['cleaned_by']}" if d.get("cleaned_by") else ""
        return (f"cleaning started {_when(d.get('since'), alert)} was marked done after only {d.get('minutes_taken', '?')} min "
                f"(minimum {d.get('threshold_minutes', '?')} min){who}.")
    if t == "cleaning_too_long":
        card = "; the cleaning card was still out" if d.get("cleaning_card_outstanding") else ""
        return (f"kept 'in cleaning' for {d.get('hours_in_cleaning', '?')} h since {_when(d.get('since'), alert)} "
                f"(limit {d.get('threshold_minutes', '?')} min) with no guest booked{card}.")
    if t == "inspection_overdue":
        return (f"cleaned at {_when(d.get('since'), alert)} but not inspected for {d.get('hours_since_cleaned', '?')} h "
                f"(limit {d.get('threshold_minutes', '?')} min).")
    if t == "same_id_two_rooms":
        return (f"the same guest ID {d.get('id_number_masked') or ''} was used to check into "
                f"{len(d.get('room_ids') or [])} rooms (booking {', '.join('#' + str(x) for x in d.get('booking_ids') or [])}).")
    if t == "shift_old_card_live":
        return d.get("note") or "after a room change the old room's card was not wiped."
    if t == "overstay_unattended":
        return (f"the guest is still in the room past check-out {d.get('current_check_out') or ''} (booked to "
                f"{d.get('booked_check_out') or '?'}); automatic charging stopped after {d.get('max_auto_days', '?')} day(s){bk}.")
    if t == "overstay_unbillable":
        return (f"the guest stayed past check-out but the bill {d.get('invoice_no') or ''} was already closed, so "
                f"{d.get('nights_overdue', '?')} night(s) could not be charged{bk}.")
    if t == "paid_after_expiry":
        return f"{_money(d.get('amount'))} was paid online ({d.get('via') or 'gateway'}) after the booking had expired{bk}."
    if t == "repeated_refunds":
        return (f"{d.get('refund_count', '?')} refunds went to the same reference {d.get('refund_reference') or ''} "
                f"(limit {d.get('threshold', '?')}){bk}.")
    if t == "ota_no_email":
        return (f"the {d.get('channel') or 'OTA'} booking {d.get('ota_booking_id') or ''} was checked in on "
                f"{d.get('check_in') or ''} without the OTA's confirmation email{bk}.")
    if t == "card_not_returned":
        return (f"the {d.get('card_type') or 'room'} card cut at {_when(d.get('issued_at'), alert)} was recorded LOST by "
                f"{d.get('recorded_by') or 'the desk'} - \"{d.get('reason') or 'no reason'}\"; it still opens the door until "
                f"{_when(d.get('valid_to'), alert, True)}.")
    if t == "card_not_cut":
        return (f"rooms {', '.join(str(x) for x in d.get('rooms') or []) or '?'} went through {d.get('step') or 'a step'} "
                f"without a key card being cut ({d.get('reason') or 'no reason given'}){bk}.")
    note = d.get("note")
    return (note[0].lower() + note[1:]) if note else f"{t.replace('_', ' ')}{bk}."


def status_text(db, alert, cache=None) -> str:
    cache = {} if cache is None else cache
    if alert.status == "open":
        return "Not reviewed yet"
    who = None
    if alert.reviewed_by:
        k = ("user", alert.reviewed_by)
        if k not in cache:
            from models import User
            u = db.query(User).filter(User.user_id == alert.reviewed_by).first()
            cache[k] = (u.full_name or u.username) if u else None
        who = cache[k]
    at = clock.to_local(alert.reviewed_at).strftime("%d/%m %H:%M") if alert.reviewed_at else ""
    verb = "Dismissed" if alert.status == "dismissed" else "Checked"
    note = f" - {alert.review_note}" if alert.review_note else ""
    return f"{verb}{' by ' + who if who else ''}{' on ' + at if at else ''}{note}"


def explain(db, alert, cache=None) -> dict:
    cache = {} if cache is None else cache
    t = alert.type or ""
    cat = CATALOGUE.get(t, {"title": t.replace("_", " ").capitalize(), "meaning": "", "check": ""})
    c = _ctx(db, alert, cache)
    d = _d(alert)
    short = _sentence(t, d, c, alert)
    short = short[0].upper() + short[1:] if short else short
    room = c.get("room")
    sentence = f"Room {room}: {short[0].lower() + short[1:]}" if room and short else short
    return {"title": cat["title"], "sentence": sentence, "short": short, "meaning": cat["meaning"],
            "check": cat["check"], "room": room, "guest": c.get("guest"), "staff": c.get("staff"),
            "status_text": status_text(db, alert, cache),
            "when": clock.to_local(alert.detected_at).strftime("%d/%m/%y %H:%M") if alert.detected_at else ""}
