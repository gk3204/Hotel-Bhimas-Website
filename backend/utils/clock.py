"""One clock for the whole backend (F-03).

**The rule, in one line: every timestamp stored in the database is naive UTC.**

Why this module exists. The Railway database runs **UTC** while the app container runs
`TZ=Asia/Kolkata` (see `docs/configuration-reference.md`). That is a perfectly normal setup, and the
code half-honoured it:

  * columns with `server_default=func.now()` are written by Postgres  -> **UTC**
  * `datetime.utcnow()` writes                                        -> **UTC**
  * `datetime.now()` writes                                           -> **IST**, 5.5 h ahead

...so one column could hold both (`bookings.cancelled_at` was written by `datetime.now()` in
`routers/bookings.py` and by `datetime.utcnow()` in `routers/payments.py`), and any query that built
a day window from a business date and compared it to a UTC column was off by five and a half hours.
The sharpest symptom: the cashier summary decides "is this receipt a checkout settlement or an
advance?" with `payment.created_at >= booking.checked_out_at` — UTC against IST — so **every checkout
settlement printed under ADVANCE**. The Tally export filed a 01:00 payment in the previous month.

There are exactly two kinds of time in this product and they must not be confused:

  1. **An instant** — when something happened. Stored UTC, everywhere, via `now_utc()`.
  2. **A wall-clock moment at this hotel** — a check-in time, a card's validity window. The door lock
     has no timezone: it compares against whatever was encoded on the card. Those use `now_local()`
     and stay local, deliberately.

The business day is the hotel's day (IST), so anything that groups or filters by date must convert
with `business_day_bounds()` rather than comparing a local date to a UTC column.

Absorbs the two ad-hoc constants that grew before this module existed:
`utils/fraud_detection.IST` and `routers/portal._IST`.
"""
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

# Single property, one city, no DST. Named rather than a fixed offset so a future India timezone
# change (or a second property) is a data problem and not an arithmetic one.
APP_TZ = ZoneInfo("Asia/Kolkata")
UTC = ZoneInfo("UTC")


def now_utc() -> datetime:
    """The current instant as **naive UTC** — what every database column should hold."""
    return datetime.now(UTC).replace(tzinfo=None)


def now_local() -> datetime:
    """The current wall-clock time **at the hotel**, naive.

    ⛔ Only for values that are wall-clock by nature: the card validity window handed to the encoder,
    and anything shown to a receptionist as "now". Never for a stored instant.
    """
    return datetime.now(APP_TZ).replace(tzinfo=None)


def business_today() -> date:
    """Today's date at the hotel. `date.today()` agrees while the container is IST, but says so."""
    return now_local().date()


def to_local(dt: datetime | None) -> datetime | None:
    """Naive UTC (as stored) -> naive local. Passes through None and already-aware values sanely."""
    if dt is None:
        return None
    aware = dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt
    return aware.astimezone(APP_TZ).replace(tzinfo=None)


def to_utc(dt: datetime | None) -> datetime | None:
    """Naive local wall-clock -> naive UTC, for comparing against stored columns."""
    if dt is None:
        return None
    aware = dt.replace(tzinfo=APP_TZ) if dt.tzinfo is None else dt
    return aware.astimezone(UTC).replace(tzinfo=None)


def business_day_bounds(day_from: date, day_to: date | None = None) -> tuple[datetime, datetime]:
    """The UTC half-open range `[start, end)` covering these business days at the hotel.

    `day_to` defaults to `day_from` (a single day). Use it for EVERY query that filters or groups a
    stored timestamp by a business date:

        start, end = business_day_bounds(dfrom, dto)
        q.filter(Payment.created_at >= start, Payment.created_at < end)

    Half-open on purpose — `<= 23:59:59.999999` loses the last microsecond of the day and invites the
    "why is this one receipt missing" bug.
    """
    if day_to is None:
        day_to = day_from
    start = to_utc(datetime.combine(day_from, time.min))
    end = to_utc(datetime.combine(day_to + timedelta(days=1), time.min))
    return start, end


def business_date_of(dt: datetime | None) -> date | None:
    """Which business day a stored (naive UTC) timestamp belongs to.

    The Python-side counterpart of `business_day_bounds` — for grouping rows already in memory. In
    SQL prefer the bounds, so the database can use the index.
    """
    local = to_local(dt)
    return local.date() if local else None


def business_date_sql(col):
    """SQL expression: the **business date** (IST) of a column that stores a naive-UTC instant.

    A drop-in replacement for `func.date(col)` in report queries. `func.date()` takes the date of the
    raw UTC value, so the owner's "today" ran 05:30 IST to 05:29 the next morning: a night desk's
    takings landed on the wrong day and the first 5.5 hours of every day went missing from the report
    that covered it.

        func.date(Payment.created_at) == day     ->  business_date_sql(Payment.created_at) == day

    ⛔ Only for UTC instants. Columns that hold the hotel's WALL CLOCK — `checked_in_at`,
    `checked_out_at`, `stay_started_at`, and the card window — are already local, and running this
    over them would shift them the wrong way.

    Note it is a function over a column, so an index on that column will not be used. These are
    reporting queries over one small hotel's data; correctness is worth more than the scan here.
    """
    from sqlalchemy import cast, Date, literal_column, text
    return cast(col.op("AT TIME ZONE")(text("'UTC'")).op("AT TIME ZONE")(text("'Asia/Kolkata'")), Date)
