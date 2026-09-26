"""Where does a payment go when a stay has more than one bill? (v6e, Batch D)

A guest hands over one card. The money is a payment against the BOOKING — that is what
`payments.payment_id` records, what the cash drawer counts, and what the shift variance reconciles.
But once a stay bills per room, that one payment has to land on one or more of several bills, and
a refund later has to be unwound from the same places.

The rules, in the order they apply:

1. **The desk said which room** — the whole payment goes on that room's bill. This is the normal
   case at a front desk: room 12 is leaving and settles.
2. **Nobody said** — the payment is allocated across the OPEN bills, oldest first, each taking as
   much as it still owes. Anything left over (the guest overpaid, or every bill is clear) lands on
   the oldest open bill as a credit, where the desk can see it and return it.
3. **Group billing** — there is one bill, so none of this applies and the single credit line is
   posted exactly as it always was. No allocation rows are written, because there is nothing to
   record: the payment and the bill are one to one.

`PaymentAllocation` exists so a refund can be unwound from the same bills the money went on. A
refund without it would have to guess, and guessing on a family's separate bills means refunding
the wrong person.
"""
from models import BookingItem, Folio, FolioCharge, PaymentAllocation
from services import folio_resolver


def _open_folios(db, booking) -> list:
    rows = folio_resolver.folios_for_booking(db, booking.booking_id)
    return [f for f in rows if f.status == "open"] or rows


def plan(db, booking, amount: float, room_id=None) -> list:
    """Decide the split without writing anything: `[(folio, amount), …]`.

    Kept separate from `post` so the desk can be shown where a payment will land before it is
    taken — and so the split can be unit-tested without a folio charge in sight.
    """
    amount = round(float(amount or 0), 2)
    if amount == 0:
        return []
    if not folio_resolver.is_per_room(booking):
        folio = folio_resolver.primary_folio(db, booking)
        return [(folio, amount)] if folio else []

    if room_id:
        item = (db.query(BookingItem)
                .filter(BookingItem.booking_id == booking.booking_id,
                        BookingItem.room_id == int(room_id)).first())
        folio = folio_resolver.folio_for_item(db, booking, item) if item is not None else None
        if folio is not None:
            return [(folio, amount)]

    folios = _open_folios(db, booking)
    if not folios:
        return []
    out, left = [], amount
    for f in folios:
        if left <= 0:
            break
        owed = round(float(f.balance or 0), 2)
        if owed <= 0:
            continue
        take = round(min(owed, left), 2)
        out.append((f, take))
        left = round(left - take, 2)
    if left > 0:
        # An overpayment, or every bill already clear. It goes on the oldest OPEN bill rather than
        # being spread, so the credit is in one findable place when the desk returns it.
        if out and out[0][0] is folios[0]:
            out[0] = (folios[0], round(out[0][1] + left, 2))
        else:
            out.insert(0, (folios[0], left))
    return out


def post(db, booking, payment, amount: float, description: str, posted_by=None,
         room_id=None) -> list:
    """Post the payment's credit line(s) and record where the money went.

    Returns the `[(folio, amount)]` it used. Does not commit — the caller owns the transaction,
    as everywhere else that touches a folio.
    """
    splits = plan(db, booking, amount, room_id=room_id)
    per_room = folio_resolver.is_per_room(booking)
    for folio, part in splits:
        db.add(FolioCharge(
            folio_id=folio.id,
            type="payment",
            description=description,
            qty=1,
            unit_price=part,
            amount=-part,
            posted_by=posted_by,
            booking_item_id=folio.booking_item_id,
        ))
        if per_room and payment is not None:
            db.add(PaymentAllocation(payment_id=payment.payment_id, folio_id=folio.id,
                                     amount=part))
    return splits


def refund_targets(db, payment, amount: float) -> list:
    """Which bills a refund should come off, and how much from each.

    Mirrors how the money went in: the recorded allocations, pro-rata, so refunding half a payment
    takes half from each bill it was split across. With no allocations (a group-billed stay, or a
    payment taken before this existed) it is the stay's one bill, which is the old behaviour.
    """
    amount = round(float(amount or 0), 2)
    allocs = (db.query(PaymentAllocation)
              .filter(PaymentAllocation.payment_id == payment.payment_id)
              .order_by(PaymentAllocation.id).all())
    if not allocs:
        folio = folio_resolver.primary_folio(db, payment.booking_id)
        return [(folio, amount)] if folio else []
    total = round(sum(float(a.amount or 0) for a in allocs), 2)
    if total <= 0:
        return []
    out, running = [], 0.0
    for n, a in enumerate(allocs):
        folio = db.query(Folio).filter(Folio.id == a.folio_id).first()
        if folio is None:
            continue
        if n == len(allocs) - 1:
            part = round(amount - running, 2)
        else:
            part = round(amount * float(a.amount or 0) / total, 2)
            running = round(running + part, 2)
        if part:
            out.append((folio, part))
    return out
