"""Room-type capacity arithmetic — the ONE place that decides what counts as sold.

Backlog v2 FE-7. Before this module the desk (`routers/reception.py`) and the public
website (`routers/room_type_availability.py`) each carried their own copy of the
"which booking statuses reserve a room" list, and neither counted a guest who had
already **checked in**. Check-in flips `Booking.status` to `checked_in`, so the stay
dropped straight out of the booked count and the occupied room was offered for sale
again — an overbooking hole on both surfaces.

Both routers now call these helpers, so the two can never drift again.
"""

from datetime import timedelta

from sqlalchemy import func

from models import Booking, BookingItem, Room

# Booking statuses that hold a room out of inventory for their date range.
# `checked_in` belongs here: the guest is physically in the room.
# `checked_out` / `cancelled` / `expired` deliberately do NOT — those rooms are resellable.
RESERVED_STATUSES = ["confirmed", "pending_payment", "payment_pending", "checked_in"]

# Physical rooms that cannot be sold right now regardless of bookings.
# `occupied` is absent on purpose — an occupied room is already counted through its
# `checked_in` booking above, and counting it here too would subtract it twice.
# `cleaning` is absent too: it is transient and a same-day resell is legitimate.
OUT_OF_SERVICE_STATUSES = ("maintenance", "blocked")


# Namespace for the per-room-type advisory lock. pg_advisory_xact_lock takes two int4s; the first
# keeps our keys from colliding with any other advisory lock in the database (the night audit holds
# one of its own, utils/night_audit.py).
_CAPACITY_LOCK_NS = 0x524F4F4D  # "ROOM"


def lock_room_type(db, room_type_id) -> None:
    """Serialise capacity decisions for ONE room type, for the rest of this transaction.

    F-08 — the overbooking guard did not hold. `booked_qty(lock=True)` takes `SELECT ... FOR UPDATE`
    on the bookings that overlap the dates, but a row lock can only lock rows that ALREADY EXIST:
    when the competing bookings have not been inserted yet the subquery matches nothing, so there is
    nothing to lock. Every concurrent request read `booked = 0`, every one passed the check, and
    every one inserted. Measured before this existed: five simultaneous requests for the last five
    rooms were ALL accepted — 25 rooms sold against 5 — and `available` then read 0, so the oversell
    was invisible until the guests arrived. `READ COMMITTED` (database.py) makes that phantom the
    expected behaviour, not bad luck.

    An advisory lock has no such problem: it locks a NUMBER, which exists whether or not any row
    does. Taken on the room type, so it serialises only bookings competing for the same inventory,
    and `_xact_` releases it on commit or rollback — nothing to leak. Precedent in this codebase:
    `utils/night_audit.py`.

    Call this BEFORE reading capacity, inside the booking transaction.
    """
    from sqlalchemy import text
    db.execute(text("SELECT pg_advisory_xact_lock(:ns, :k)"),
               {"ns": _CAPACITY_LOCK_NS, "k": int(room_type_id)})


def booked_qty(db, room_type_id, check_in, check_out, lock=False, exclude_booking_id=None):
    """Rooms of this type consumed by bookings overlapping [check_in, check_out).

    `lock=True` adds a row lock on the overlapping bookings, which is worth having but is NOT what
    makes the capacity check safe — see `lock_room_type` above, which the callers take first.
    """
    inner = db.query(Booking.booking_id).filter(
        Booking.status.in_(RESERVED_STATUSES),
        Booking.check_in < check_out,
        Booking.check_out > check_in,
    )
    if exclude_booking_id:       # v7v: excluded from the dated count too, not only the still-in one
        inner = inner.filter(Booking.booking_id != exclude_booking_id)
    if lock:
        inner = inner.with_for_update()
    by_dates = db.query(func.sum(BookingItem.quantity)).filter(
        BookingItem.room_type_id == room_type_id,
        BookingItem.booking_id.in_(inner),
    ).scalar() or 0

    # v7f (staff, 13 Oct): "5 rooms of a type and 3 are occupied ... availability is 3 until 12 AM but
    # later changing to 5 even though it is still occupied". The count above goes by the BOOKED
    # dates, so at midnight every in-house guest whose booked check-out is the new day dropped out -
    # on production 9 of the 10 rooms in house - while still upstairs (a 24-hour stay that began at
    # 19:20 leaves at 19:20). For a stay starting today, a room is taken until its guest actually
    # checks out, whatever the dates say. Rooms only the date rule missed are added, so none twice.
    from utils import clock as _clock
    still_in = 0
    if check_in <= _clock.business_today():
        q = (db.query(func.sum(BookingItem.quantity))
             .join(Booking, Booking.booking_id == BookingItem.booking_id)
             .filter(BookingItem.room_type_id == room_type_id,
                     Booking.status == "checked_in",
                     BookingItem.checked_in_at.isnot(None),
                     BookingItem.checked_out_at.is_(None),
                     Booking.check_out <= check_in))      # not already counted by its dates
        if exclude_booking_id:
            q = q.filter(Booking.booking_id != exclude_booking_id)
        still_in = q.scalar() or 0
    return int(by_dates) + int(still_in)


def out_of_service_count(db, room_type_id):
    """Physical rooms of this type that are deactivated or out of service."""
    return db.query(func.count(Room.room_id)).filter(
        Room.room_type_id == room_type_id,
        (Room.is_active == False) | (Room.status.in_(OUT_OF_SERVICE_STATUSES)),  # noqa: E712
    ).scalar() or 0


# ---- b5: unconfirmed OTA drafts HOLD their rooms ----
# The owner: "we don't take bookings and then find no room for the guest". An OTA confirmation email
# becomes a DRAFT; until someone confirms it, it used to hold nothing, so the desk (or the website)
# could sell the room the OTA guest was already on their way to. A pending/flagged confirmation
# draft now holds its rooms for its dates. A draft the OTA has since cancelled is `cancelled`, and a
# draft already turned into a booking is counted through that booking, so neither holds anything.
HOLDING_DRAFT_STATUSES = ("pending", "flagged")


def live_ota_booking_ids(db, ota_ids) -> set:
    """The OTA ids among `ota_ids` that already have a live booking. A draft for one of these is
    counted (and listed) through that booking, never a second time. Shared by `ota_draft_holds` and
    the arrival report so the two cannot disagree about which drafts are still unconfirmed."""
    ids = {i for i in (ota_ids or ()) if i}
    if not ids:
        return set()
    return {r[0] for r in db.query(Booking.ota_booking_id).filter(
        Booking.ota_booking_id.in_(ids),
        Booking.status.in_(RESERVED_STATUSES + ["checked_out"]),
    ).all()}


def ota_draft_holds(db, check_in, check_out, exclude_ota_booking_id=None):
    """Rooms held by unconfirmed OTA confirmation drafts overlapping [check_in, check_out).

    Returns (holds, unmapped): holds is {room_type_id: rooms}; unmapped counts rooms from drafts
    whose room type could not be identified (reported, never guessed onto a type).
    `exclude_ota_booking_id` skips the draft(s) for the booking being created, so a draft never
    blocks its own confirmation (the confirm path books under the draft's OTA id).
    """
    from models import OtaDraftBooking
    from services import ota_service

    q = db.query(OtaDraftBooking).filter(
        OtaDraftBooking.kind == "confirmation",
        OtaDraftBooking.status.in_(HOLDING_DRAFT_STATUSES),
        OtaDraftBooking.check_in.isnot(None),
        OtaDraftBooking.check_out.isnot(None),
        OtaDraftBooking.check_in < check_out,
        OtaDraftBooking.check_out > check_in,
    )
    if exclude_ota_booking_id:
        q = q.filter((OtaDraftBooking.ota_booking_id.is_(None))
                     | (OtaDraftBooking.ota_booking_id != exclude_ota_booking_id))
    drafts = q.all()
    if not drafts:
        return {}, 0

    # A draft whose OTA id already has a live booking is counted through that booking.
    live = live_ota_booking_ids(db, {d.ota_booking_id for d in drafts if d.ota_booking_id})

    holds, unmapped = {}, 0
    for d in drafts:
        if d.ota_booking_id and d.ota_booking_id in live:
            continue
        for rt_id, n in ota_service.draft_room_type_lines(db, d):
            if rt_id:
                holds[rt_id] = holds.get(rt_id, 0) + int(n)
            else:
                unmapped += int(n)
    return holds, unmapped


# ---- v7b: alternate room types (owner, 12 Oct, item 4) ----
# "check room availability for ota based on alternate type if the primary type room was unavailable
# ... say there is a room with double non-ac and its alternate type is double ac then this can be taken
# into account although during check-in it might need approval".
#
# A room of type X whose `alt_room_type_id` is Y can host a type-Y stay (check-in already allows it,
# behind the ac_downgrade / alt-type owner approval - reception._assert_booked_type_sold_out). What was
# missing is the ARITHMETIC:
#   * a type-Y booking beyond Y's own rooms OVERFLOWS onto its lenders, and the lender's room is then
#     taken for those dates - so the lender type must count it as sold (`lent`), or the website/desk
#     sells that room a second time;
#   * how many more type-Y rooms could still be found through alternates (`alt_free`) - which only the
#     OTA confirm at the desk may use (owner decision: held for the desk, never auto-confirmed).
# Same date semantics as booked_qty (any overlap), so the numbers agree with every other guard.

def _type_blocked(db, room_type_id, check_in, check_out) -> bool:
    from models import RoomTypeAvailability
    return db.query(RoomTypeAvailability.id).filter(
        RoomTypeAvailability.room_type_id == room_type_id,
        RoomTypeAvailability.date >= check_in,
        RoomTypeAvailability.date < check_out,
        RoomTypeAvailability.is_available == False,  # noqa: E712
    ).first() is not None


def alternate_plan(db, check_in, check_out, holds=None, exclude_ota_booking_id=None) -> dict:
    """{"own_free": {type: n}, "lent": {type: n}, "alt_free": {type: n}, "lenders": {Y: [(X, rooms)]}}.

    own_free  rooms of the type left after its own bookings, out-of-service rooms and OTA holds
              (negative when the type is booked into its alternates)
    lent      rooms of the type taken by OTHER types' overflow - subtract from its availability
    alt_free  extra rooms a type could still get through its alternates
    Types with no alternate links appear in none of the dicts (lent/alt_free = 0)."""
    from models import RoomType
    rows = (db.query(Room.room_type_id, Room.alt_room_type_id, func.count(Room.room_id))
            .filter(Room.alt_room_type_id.isnot(None),
                    Room.alt_room_type_id != Room.room_type_id,
                    Room.is_active == True,  # noqa: E712
                    ~Room.status.in_(OUT_OF_SERVICE_STATUSES))
            .group_by(Room.room_type_id, Room.alt_room_type_id).all())
    if not rows:
        return {"own_free": {}, "lent": {}, "alt_free": {}, "lenders": {}}
    if holds is None:
        holds, _u = ota_draft_holds(db, check_in, check_out, exclude_ota_booking_id=exclude_ota_booking_id)
    lenders = {}
    for x, y, n in rows:
        lenders.setdefault(y, []).append((x, int(n)))
    involved = {x for x, _y, _n in rows} | {y for _x, y, _n in rows}
    own_free = {}
    for rt in db.query(RoomType).filter(RoomType.room_type_id.in_(involved)).all():
        t = rt.room_type_id
        own_free[t] = (int(rt.total_rooms or 0) - int(booked_qty(db, t, check_in, check_out))
                       - int(out_of_service_count(db, t)) - int(holds.get(t, 0)))
    blocked = {t for t in involved if _type_blocked(db, t, check_in, check_out)}
    lent, pair = {}, {}
    for y in sorted(lenders):
        need = max(0, -own_free.get(y, 0))
        for x, n in sorted(lenders[y]):
            if need <= 0:
                break
            if x in blocked:
                continue
            spare = own_free.get(x, 0) - lent.get(x, 0)
            give = min(need, n - pair.get((x, y), 0), max(0, spare))
            if give > 0:
                lent[x] = lent.get(x, 0) + give
                pair[(x, y)] = pair.get((x, y), 0) + give
                need -= give
    alt_free = {}
    for y, lst in lenders.items():
        alt_free[y] = sum(min(n - pair.get((x, y), 0), max(0, own_free.get(x, 0) - lent.get(x, 0)))
                          for x, n in lst if x not in blocked)
    return {"own_free": own_free, "lent": lent, "alt_free": alt_free, "lenders": lenders}


def _nights(check_in, check_out):
    d = check_in
    while d < check_out:
        yield d
        d = d + timedelta(days=1)


def overbook_room(rt, user, shortfall: int) -> bool:
    """v8m (owner, 28 Oct): may this booking take `shortfall` rooms beyond what the type has free?
    Only when the admin switched overbooking on for the type, only up to its limit (counted on the
    tightest night, so rooms already over - an overstay - use the allowance up), and only for a
    person at the desk: the website and the automatic OTA mail import (user None) never overbook."""
    if shortfall <= 0:
        return True
    if rt is None or not getattr(rt, "allow_overbooking", False) or not user:
        return False
    return shortfall <= int(getattr(rt, "overbook_limit", 1) or 1)


def overbook_extra(rt, available_raw: int) -> int:
    """How many more rooms reception may book than the free count shows (0 when it is off)."""
    if rt is None or not getattr(rt, "allow_overbooking", False):
        return 0
    return max(0, int(getattr(rt, "overbook_limit", 1) or 1) + min(0, int(available_raw)))


def free_rooms(db, room_type_id, check_in, check_out, *, total=None, exclude_ota_booking_id=None,
               exclude_booking_id=None, lock=False, cache=None) -> dict:
    """v7v (owner, 17 Oct: "6 Four Bed Non-AC rooms but the desk shows 1, and the OTA booking is declined
    saying availability is not there"): rooms of a type free for EVERY night of [check_in, check_out).

    The helpers above count everything overlapping the WHOLE range, so a 3-night stay added the
    bookings of all three nights together - 4 guests on the 17th + 2 on the 18th read as 6 rooms
    taken, though no night had more than 4 (prod draft #1381: "-2 available", every night had 2 free).
    This evaluates one night at a time and returns the TIGHTEST night:
      {"available": rooms free on the tightest night (may be negative), "booked", "held", "lent",
       "inactive": that night's figures, "alt_free": extra rooms through alternate types free on every
       night, "lenders": the alternate lenders, "night": the tightest night}.
    `cache` (a dict) shares the per-night OTA holds / alternate plan across room types in one request."""
    from models import RoomType
    if total is None:
        rt = db.query(RoomType).filter(RoomType.room_type_id == room_type_id).first()
        total = int(rt.total_rooms or 0) if rt else 0
    inactive = int(out_of_service_count(db, room_type_id))
    cache = {} if cache is None else cache
    worst, with_alt_min, lenders = None, None, {}
    for d in _nights(check_in, check_out):
        e = d + timedelta(days=1)
        key = (d, exclude_ota_booking_id)
        if key not in cache:
            holds, _u = ota_draft_holds(db, d, e, exclude_ota_booking_id=exclude_ota_booking_id)
            cache[key] = (holds, alternate_plan(db, d, e, holds=holds))
        holds, plan = cache[key]
        booked = int(booked_qty(db, room_type_id, d, e, lock=lock, exclude_booking_id=exclude_booking_id))
        held = int(holds.get(room_type_id, 0))
        lent = int(plan["lent"].get(room_type_id, 0))
        avail = int(total) - booked - inactive - held - lent
        alt = int(plan["alt_free"].get(room_type_id, 0))
        lenders = plan.get("lenders") or lenders
        if worst is None or avail < worst["available"]:
            worst = {"available": avail, "booked": booked, "held": held, "lent": lent, "inactive": inactive,
                     "night": d}
        wa = max(0, avail) + alt
        with_alt_min = wa if with_alt_min is None else min(with_alt_min, wa)
    if worst is None:            # an empty range consumes nothing
        worst = {"available": int(total) - inactive, "booked": 0, "held": 0, "lent": 0, "inactive": inactive,
                 "night": check_in}
        with_alt_min = max(0, worst["available"])
    worst["alt_free"] = max(0, (with_alt_min or 0) - max(0, worst["available"]))
    worst["lenders"] = lenders
    return worst


def lent_out(db, check_in, check_out, holds=None, exclude_ota_booking_id=None) -> dict:
    """{type: rooms of that type taken by other types booked into their alternates}."""
    return alternate_plan(db, check_in, check_out, holds=holds,
                          exclude_ota_booking_id=exclude_ota_booking_id)["lent"]


def alternate_shortfall(db, room_type_id, check_in, check_out, quantity, exclude_ota_booking_id=None) -> dict:
    """v7b: can `quantity` rooms of this type be found once its alternates are counted?
    {"own": rooms of the type itself still free, "alt": extra rooms through alternates,
     "fits_own": bool, "fits_with_alt": bool, "lender_names": [type names that would host it]}"""
    from models import RoomType
    rt = db.query(RoomType).filter(RoomType.room_type_id == room_type_id).first()
    # v7v: night by night (free_rooms), not the whole range added together
    fr = free_rooms(db, room_type_id, check_in, check_out, exclude_ota_booking_id=exclude_ota_booking_id)
    own = fr["available"]
    alt = int(fr["alt_free"])
    names = []
    for x, _n in (fr.get("lenders") or {}).get(room_type_id, []):
        t = db.query(RoomType).filter(RoomType.room_type_id == x).first()
        if t is not None:
            names.append(t.name)
    return {"own": max(0, own), "alt": alt, "fits_own": quantity <= max(0, own),
            "fits_with_alt": quantity <= max(0, own) + alt, "lender_names": names,
            "type_name": rt.name if rt else f"type {room_type_id}"}
