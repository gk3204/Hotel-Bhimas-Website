"""How a confirmation prints the stay's check-in and check-out moments (v8l, owner 26 Oct).

The booked check-in time STARTS the 24-hour stay, so check-out is the same clock time on the
check-out date. A website guest typed 03:00 meaning 3 PM and was charged a second night; the
email and PDF used to show only "Arrival ~03:00", which reads as a hint, not as "you leave at 3 AM".
"""
from datetime import date, datetime, time


def _as_time(t):
    if t is None or t == "":
        return None
    if isinstance(t, time):
        return t
    try:
        return datetime.strptime(str(t)[:5], "%H:%M").time()
    except ValueError:
        return None


def clock_12h(t) -> str:
    """'03:00:00' -> '3:00 AM', '15:30' -> '3:30 PM'."""
    t = _as_time(t)
    if t is None:
        return ""
    h = t.hour % 12 or 12
    return f"{h}:{t.minute:02d} {'AM' if t.hour < 12 else 'PM'}"


def stay_moments(check_in: date, check_out: date, check_in_time, date_fmt="%d %B %Y"):
    """(check-in text, check-out text): the dates, plus the time when the booking has one."""
    t = clock_12h(check_in_time)
    ci = check_in.strftime(date_fmt) if hasattr(check_in, "strftime") else str(check_in)
    co = check_out.strftime(date_fmt) if hasattr(check_out, "strftime") else str(check_out)
    return (f"{ci}, {t}" if t else ci), (f"{co}, {t}" if t else co)
