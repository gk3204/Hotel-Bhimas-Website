"""Corporate bill-to-company service (prompt 18, slice 7).

The ONE place the corporate money rules live, so `routers/companies.py`, `routers/folio.py` and
`routers/reception.py` all behave identically.

Model of the world
------------------
A stay can be billed to the guest, to a company, or split between them. The split lives on the
EXISTING folio: every `FolioCharge` carries `bill_to` ('guest' | 'company'). There is no second
folio and no parallel charge table — reports, the night audit, the invoice PDF and the desktop all
keep reading `folio_charges` exactly as before.

  * `Folio.total` / `Folio.balance` keep their ORIGINAL whole-folio meaning. Nothing downstream
    had to change.
  * `Folio.company_total` / `Folio.company_balance` mirror the `bill_to='company'` subset.
  * The guest-side figure is DERIVED, never stored: guest_balance = balance - company_balance.

At checkout the company-routed balance is TRANSFERRED to the company's city ledger:
  * one append-only `CompanyLedger` row of type 'charge' (the company now owes it), and
  * one `FolioCharge(type='payment', bill_to='company')` on the folio that cancels it out.
So the guest side settles to zero without any member of staff asserting that cash was received --
the anti-fraud invariant from AGENT_ONBOARDING.md holds: money moves by ledger entry only.

Credit control
--------------
`check_credit` compares (current outstanding + what is about to be routed) against the company's
`credit_limit`. When `company_credit_block` is on (default) a breach is refused with 409. An admin
may override with a reason -- the override is written to the audit log, the same posture as a void
or a discount.
"""
import logging
from datetime import date, datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from models import Booking, Company, CompanyLedger, Folio, FolioCharge
from utils.audit import _resolve_user_id, write_audit
from utils.settings import get_backoffice_config

logger = logging.getLogger(__name__)

# The two sides a folio line can be routed to.
BILL_TO_GUEST = "guest"
BILL_TO_COMPANY = "company"
BILL_TO_VALUES = (BILL_TO_GUEST, BILL_TO_COMPANY)

# Charge types that are never routed to a company: payments/discounts are settlement
# artefacts of whichever side they were applied to, not billable stay charges.
NON_ROUTABLE_TYPES = ("payment",)


# ---------------------------------------------------------------- lookups

def get_company(db: Session, company_id, *, active_only: bool = False) -> Company | None:
    if not company_id:
        return None
    q = db.query(Company).filter(Company.id == company_id)
    if active_only:
        q = q.filter(Company.is_active == True)  # noqa: E712
    return q.first()


def outstanding(db: Session, company_id: int) -> float:
    """Current amount the company owes = sum of every ledger row (charges +, payments -).

    Summed from the ledger itself rather than trusting `balance_after`, so a manual DB fix or an
    out-of-order insert can never drift the number that credit control depends on."""
    total = db.query(func.coalesce(func.sum(CompanyLedger.amount), 0)).filter(
        CompanyLedger.company_id == company_id
    ).scalar()
    return round(float(total or 0), 2)


def company_summary(db: Session, company: Company) -> dict:
    """Company + live credit position, the shape every company endpoint returns."""
    owed = outstanding(db, company.id)
    limit = float(company.credit_limit or 0)
    return {
        "company_id": company.id,
        "name": company.name,
        "gstin": company.gstin,
        "address": company.address,
        "city": company.city,
        "state": company.state,
        "state_code": company.state_code,
        "contact_person": company.contact_person,
        "phone": company.phone,
        "email": company.email,
        "credit_limit": round(limit, 2),
        "credit_days": int(company.credit_days or 0),
        "payment_terms": company.payment_terms,
        "is_active": bool(company.is_active),
        "notes": company.notes,
        "outstanding": owed,
        "credit_available": round(limit - owed, 2),
        "over_limit": owed > limit,
        "created_at": str(company.created_at) if company.created_at else None,
    }


# ---------------------------------------------------------------- credit control

def check_credit(db: Session, company: Company, additional: float = 0.0) -> dict:
    """Would routing `additional` rupees to this company breach its credit limit?

    Returns the full picture (never raises) so callers can either refuse, warn, or record an
    override. `blocked` is only true when the admin-editable `company_credit_block` gate is on."""
    cfg = get_backoffice_config(db)
    owed = outstanding(db, company.id)
    limit = float(company.credit_limit or 0)
    projected = round(owed + float(additional or 0), 2)
    breached = projected > limit
    return {
        "company_id": company.id,
        "company_name": company.name,
        "outstanding": owed,
        "additional": round(float(additional or 0), 2),
        "projected": projected,
        "credit_limit": round(limit, 2),
        "credit_available": round(limit - owed, 2),
        "breached": breached,
        "blocked": bool(breached and cfg["company_credit_block"]),
        "enforcement": "block" if cfg["company_credit_block"] else "warn",
    }


# ---------------------------------------------------------------- ledger

def post_ledger(db: Session, company: Company, *, type: str, amount: float,
                description: str | None = None, booking_id=None, folio_id=None,
                company_invoice_id=None, method: str | None = None,
                reference: str | None = None, client_ref: str | None = None,
                user=None) -> CompanyLedger:
    """Append one row to the company's city ledger and snapshot the running balance.

    APPEND-ONLY: nothing here ever updates or deletes an earlier row -- a correction is a new
    opposite-signed row, the same posture as folio voids and the audit log.

    `client_ref` makes a double-submit idempotent: if a row with that ref already exists it is
    returned untouched (mirrors `Expense.client_ref` / `Payment` idempotency in prompt 19).
    Joins the caller's transaction -- the caller commits."""
    if client_ref:
        existing = db.query(CompanyLedger).filter(CompanyLedger.client_ref == client_ref).first()
        if existing:
            return existing

    amount = round(float(amount or 0), 2)
    row = CompanyLedger(
        company_id=company.id,
        type=type,
        description=description,
        amount=amount,
        balance_after=round(outstanding(db, company.id) + amount, 2),
        booking_id=booking_id,
        folio_id=folio_id,
        company_invoice_id=company_invoice_id,
        method=method,
        reference=reference,
        client_ref=client_ref,
        created_by=_resolve_user_id(db, user),
    )
    db.add(row)
    db.flush()
    return row


# ---------------------------------------------------------------- folio routing

def company_lines(db: Session, folio_id: int):
    """Non-void folio lines currently routed to the company."""
    return db.query(FolioCharge).filter(
        FolioCharge.folio_id == folio_id,
        FolioCharge.void == False,          # noqa: E712
        FolioCharge.bill_to == BILL_TO_COMPANY,
    ).all()


def recompute_company_totals(db: Session, folio: Folio) -> None:
    """Refresh `company_total` / `company_balance` from the company-routed subset.

    Called from `folio._recompute`, so EVERY existing mutation path (charge, void, discount,
    payment) keeps the corporate mirror correct without any of them knowing about companies."""
    db.flush()
    lines = company_lines(db, folio.id)
    folio.company_total = round(sum(float(c.amount) for c in lines if c.type != "payment"), 2)
    folio.company_balance = round(sum(float(c.amount) for c in lines), 2)


def routable_charges(db: Session, folio_id: int, charge_ids=None):
    """Lines eligible for routing: non-void, and not a settlement artefact.

    `charge_ids=None` means "the whole stay" -- used when a booking is flagged bill_to=company."""
    q = db.query(FolioCharge).filter(
        FolioCharge.folio_id == folio_id,
        FolioCharge.void == False,          # noqa: E712
        FolioCharge.type.notin_(NON_ROUTABLE_TYPES),
    )
    if charge_ids:
        q = q.filter(FolioCharge.id.in_(list(charge_ids)))
    return q.all()


def route_charges(db: Session, folio: Folio, company: Company | None, *,
                  charge_ids=None, bill_to: str = BILL_TO_COMPANY) -> dict:
    """Route folio lines to the company (or back to the guest). Caller commits.

    Routing back to the guest (`bill_to='guest'`) needs no company and no credit check."""
    if bill_to not in BILL_TO_VALUES:
        raise ValueError(f"bill_to must be one of {BILL_TO_VALUES}")

    lines = routable_charges(db, folio.id, charge_ids)
    moved, amount = 0, 0.0
    for line in lines:
        if line.bill_to == bill_to:
            continue
        line.bill_to = bill_to
        moved += 1
        amount = round(amount + float(line.amount), 2)

    # SessionLocal has autoflush=False, so the re-read below would otherwise still see the OLD
    # bill_to values and wrongly conclude the company side is non-empty.
    db.flush()

    if bill_to == BILL_TO_COMPANY:
        folio.company_id = company.id if company else None
    elif not company_lines(db, folio.id):
        folio.company_id = None   # nothing left on the company side

    recompute_company_totals(db, folio)
    return {"moved": moved, "amount": amount, "bill_to": bill_to}


def projected_company_amount(db: Session, folio: Folio, charge_ids=None) -> float:
    """What routing these lines would ADD to the company's outstanding (excludes lines
    already on the company side, so re-routing the same folio doesn't double-count)."""
    lines = routable_charges(db, folio.id, charge_ids)
    return round(sum(float(c.amount) for c in lines if c.bill_to != BILL_TO_COMPANY), 2)


# ---------------------------------------------------------------- checkout transfer

def transfer_folio_to_company(db: Session, folio: Folio, *, user=None,
                              client_ref: str | None = None) -> dict | None:
    """Move the folio's outstanding company-side balance onto the company's city ledger.

    Called at checkout (and available on demand). Writes the ledger charge AND the matching
    `type='payment'` folio line so the company side of the folio nets to zero. Idempotent via
    `client_ref` -- a repeated checkout will not double-post. Caller commits.

    Returns None when there is nothing to transfer."""
    company = get_company(db, folio.company_id)
    if company is None:
        return None

    # Check the idempotency key BEFORE the amount. After a successful transfer the company-side
    # balance is zero (charge + counter-entry), so an amount-first check would report "nothing to
    # transfer" and the caller could not tell that apart from an already-completed transfer.
    ref = client_ref or f"folio-transfer:{folio.id}"
    existing = db.query(CompanyLedger).filter(CompanyLedger.client_ref == ref).first()
    if existing:
        return {"already_transferred": True, "ledger_id": existing.id,
                "amount": float(existing.amount or 0), "company_id": company.id,
                "company_name": company.name, "outstanding": outstanding(db, company.id)}

    recompute_company_totals(db, folio)
    amount = round(float(folio.company_balance or 0), 2)
    if amount <= 0:
        return None

    booking = db.query(Booking).filter(Booking.booking_id == folio.booking_id).first()
    guest_name = booking.guest.name if booking and booking.guest else "Guest"
    stay = f"{booking.check_in:%d-%m-%Y} to {booking.check_out:%d-%m-%Y}" if booking else ""

    ledger = post_ledger(
        db, company,
        type="charge",
        amount=amount,
        description=f"Stay transferred — {guest_name} (booking #{folio.booking_id}) {stay}".strip(),
        booking_id=folio.booking_id,
        folio_id=folio.id,
        client_ref=ref,
        user=user,
    )

    # The folio-side counter-entry. Negative + type='payment' so it lands in the existing
    # balance math untouched; bill_to='company' keeps it inside the company mirror.
    db.add(FolioCharge(
        folio_id=folio.id,
        type="payment",
        description=f"Transferred to company account — {company.name}",
        qty=1,
        unit_price=amount,
        amount=-amount,
        bill_to=BILL_TO_COMPANY,
        posted_by=_resolve_user_id(db, user),
    ))
    recompute_company_totals(db, folio)

    write_audit(db, user, "company.folio_transfer", "folio", folio.id,
                after={"company_id": company.id, "company_name": company.name,
                       "amount": amount, "booking_id": folio.booking_id,
                       "ledger_id": ledger.id},
                client="desktop")

    return {"already_transferred": False, "ledger_id": ledger.id, "amount": amount,
            "company_id": company.id, "company_name": company.name,
            "outstanding": outstanding(db, company.id)}


# ---------------------------------------------------------------- ageing

def ageing_buckets(db: Session, company: Company, as_of: date | None = None) -> dict:
    """Outstanding split into 0-30 / 31-60 / 61-90 / 90+ day buckets by charge age.

    Payments are applied oldest-charge-first (FIFO), the convention Indian hotels use for a
    running city-ledger statement."""
    as_of = as_of or date.today()
    rows = db.query(CompanyLedger).filter(
        CompanyLedger.company_id == company.id
    ).order_by(CompanyLedger.created_at, CompanyLedger.id).all()

    open_charges = []   # [age_days, remaining]
    credit = 0.0
    for r in rows:
        amt = float(r.amount or 0)
        if amt >= 0:
            when = r.created_at or datetime.utcnow()
            age = (as_of - when.date()).days
            open_charges.append([age, amt])
        else:
            credit = round(credit + (-amt), 2)

    for entry in open_charges:                 # FIFO application
        if credit <= 0:
            break
        applied = min(credit, entry[1])
        entry[1] = round(entry[1] - applied, 2)
        credit = round(credit - applied, 2)

    buckets = {"current": 0.0, "d31_60": 0.0, "d61_90": 0.0, "d90_plus": 0.0}
    for age, remaining in open_charges:
        if remaining <= 0:
            continue
        if age <= 30:
            key = "current"
        elif age <= 60:
            key = "d31_60"
        elif age <= 90:
            key = "d61_90"
        else:
            key = "d90_plus"
        buckets[key] = round(buckets[key] + remaining, 2)

    buckets["total"] = round(sum(v for k, v in buckets.items() if k != "total"), 2)
    buckets["unapplied_credit"] = round(credit, 2)
    return buckets
