"""Corporate bill-to-company endpoints (prompt 18, slice 7).

Company master data, the append-only city ledger + statement, credit control, and the monthly
consolidated GST invoice that replaces handing the employer a stack of per-stay bills.

Money rules live in `services/company_service.py` so `routers/folio.py` and `routers/reception.py`
behave identically. Export (json|csv|pdf) reuses the prompt-16 `_export_or_json` idiom rather than
inventing a second download path.

Role split: reading + the desk picker are reception+admin (the front desk must be able to look up a
company at check-in); everything that moves money or edits master data is admin-only.
"""
import json
import logging
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload

from database import SessionLocal
from models import (Booking, Company, CompanyInvoice, CompanyLedger, FolioCharge,
                    Invoice, User)
from schemas import (BackofficeConfigUpdate, CompanyAdjustment, CompanyCreate,
                     CompanyInvoiceCreate, CompanyPaymentRecord, CompanyUpdate)
from services import company_service
from utils.audit import _resolve_user_id, write_audit
from utils.auth_utils import require_admin, require_reception_or_admin
from utils.settings import (BACKOFFICE_EDITABLE_KEYS, MAINTENANCE_DEFAULT_ASSIGNEE_KEY,
                            get_backoffice_config, set_setting)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/companies", tags=["Companies"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("/health", dependencies=[Depends(require_reception_or_admin)])
def health():
    return {"status": "ok", "module": "companies"}


# ---------------------------------------------------------------- helpers

def _get_company(db: Session, company_id: int) -> Company:
    company = company_service.get_company(db, company_id)
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    return company


def _ledger_row(row: CompanyLedger) -> dict:
    return {
        "id": row.id,
        "when": str(row.created_at) if row.created_at else None,
        "type": row.type,
        "description": row.description,
        "amount": float(row.amount or 0),
        "balance_after": float(row.balance_after) if row.balance_after is not None else None,
        "booking_id": row.booking_id,
        "folio_id": row.folio_id,
        "company_invoice_id": row.company_invoice_id,
        "method": row.method,
        "reference": row.reference,
    }


def _company_invoice_dict(inv: CompanyInvoice) -> dict:
    return {
        "id": inv.id,
        "company_id": inv.company_id,
        "invoice_no": inv.invoice_no,
        "fy_label": inv.fy_label,
        "invoice_date": str(inv.invoice_date) if inv.invoice_date else None,
        "period_from": str(inv.period_from) if inv.period_from else None,
        "period_to": str(inv.period_to) if inv.period_to else None,
        "taxable_total": float(inv.taxable_total or 0),
        "cgst_total": float(inv.cgst_total or 0),
        "sgst_total": float(inv.sgst_total or 0),
        "grand_total": float(inv.grand_total or 0),
        "paid_total": float(inv.paid_total or 0),
        "balance_due": round(float(inv.grand_total or 0) - float(inv.paid_total or 0), 2),
        "stay_count": int(inv.stay_count or 0),
        "status": inv.status,
        "notes": inv.notes,
        "gst_rows": json.loads(inv.gst_breakup) if inv.gst_breakup else [],
        "created_at": str(inv.created_at) if inv.created_at else None,
    }


def _export(fmt, data, *, title, columns, rows, totals_row=None, meta=None, filename):
    """Reuses the prompt-16 report export dispatch so CSV/PDF behave identically everywhere."""
    from routers.reports import _export_or_json
    return _export_or_json(fmt, data, title=title, columns=columns, rows=rows,
                           totals_row=totals_row, meta=meta, filename=filename)


# ---------------------------------------------------------------- config

@router.get("/config", dependencies=[Depends(require_admin)])
def get_config(db: Session = Depends(get_db)):
    """Back-office settings shared by companies / vendors / roster (admin-editable)."""
    return get_backoffice_config(db)


@router.put("/config", dependencies=[Depends(require_admin)])
def update_config(data: BackofficeConfigUpdate, db: Session = Depends(get_db),
                  user=Depends(require_admin)):
    changed = {}
    for key, value in data.model_dump(exclude_unset=True).items():
        if key not in BACKOFFICE_EDITABLE_KEYS or value is None:
            continue
        # v4b8: refuse a designated technician who cannot receive tickets. _auto_assign falls
        # back silently at ticket time, so without this the admin would set a dead id, see it
        # saved, and never learn why tickets keep arriving unassigned.
        if key == MAINTENANCE_DEFAULT_ASSIGNEE_KEY and value:
            tech = db.query(User).filter(User.user_id == int(value)).first()
            if not tech or not tech.is_active or tech.role != "maintenance":
                raise HTTPException(
                    status_code=422,
                    detail="Pick an active user with the maintenance role, or 'nobody'.")
        set_setting(db, key, "true" if value is True else "false" if value is False else value,
                    user=user)
        changed[key] = value
    db.commit()
    if changed:
        write_audit(db, user, "backoffice.config_update", "app_settings", None,
                    after=changed, client="web", commit=True)
    return get_backoffice_config(db)


# ---------------------------------------------------------------- company CRUD

@router.get("/")
def list_companies(q: str | None = Query(None, description="name / GSTIN / contact search"),
                   active: bool | None = Query(None),
                   db: Session = Depends(get_db),
                   user=Depends(require_reception_or_admin)):
    """Company list with live credit position. Reception can read it — the desk needs the
    picker at booking and check-in."""
    query = db.query(Company)
    if active is not None:
        query = query.filter(Company.is_active == active)
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(or_(Company.name.ilike(like),
                                 Company.gstin.ilike(like),
                                 Company.contact_person.ilike(like),
                                 Company.phone.ilike(like)))
    companies = query.order_by(Company.name).all()
    data = [company_service.company_summary(db, c) for c in companies]
    return {"total": len(data), "data": data}


@router.post("/", dependencies=[Depends(require_admin)])
def create_company(data: CompanyCreate, db: Session = Depends(get_db), user=Depends(require_admin)):
    try:
        name = data.name.strip()
        if db.query(Company).filter(Company.name.ilike(name)).first():
            raise HTTPException(status_code=400, detail="A company with that name already exists")

        cfg = get_backoffice_config(db)
        payload = data.model_dump()
        payload["name"] = name
        if not payload.get("credit_days"):
            payload["credit_days"] = cfg["company_default_credit_days"]

        company = Company(**payload, created_by=_resolve_user_id(db, user))
        db.add(company)
        db.commit()
        db.refresh(company)

        write_audit(db, user, "company.create", "company", company.id,
                    after={"name": company.name, "credit_limit": float(company.credit_limit or 0)},
                    client="web", commit=True)
        return company_service.company_summary(db, company)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"create_company failed: {e}", exc_info=True)
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to create the company")


@router.get("/{company_id}")
def get_company_detail(company_id: int, db: Session = Depends(get_db),
                       user=Depends(require_reception_or_admin)):
    """Company + credit position + ageing + recent activity."""
    company = _get_company(db, company_id)
    recent = db.query(CompanyLedger).filter(
        CompanyLedger.company_id == company.id
    ).order_by(CompanyLedger.created_at.desc(), CompanyLedger.id.desc()).limit(10).all()
    invoices = db.query(CompanyInvoice).filter(
        CompanyInvoice.company_id == company.id
    ).order_by(CompanyInvoice.created_at.desc()).limit(10).all()
    return {
        **company_service.company_summary(db, company),
        "ageing": company_service.ageing_buckets(db, company),
        "recent_ledger": [_ledger_row(r) for r in recent],
        "recent_invoices": [_company_invoice_dict(i) for i in invoices],
    }


@router.put("/{company_id}", dependencies=[Depends(require_admin)])
def update_company(company_id: int, data: CompanyUpdate, db: Session = Depends(get_db),
                   user=Depends(require_admin)):
    try:
        company = _get_company(db, company_id)
        before = {"name": company.name, "credit_limit": float(company.credit_limit or 0),
                  "is_active": bool(company.is_active)}

        changes = data.model_dump(exclude_unset=True)
        if "name" in changes and changes["name"]:
            name = changes["name"].strip()
            clash = db.query(Company).filter(Company.name.ilike(name),
                                             Company.id != company.id).first()
            if clash:
                raise HTTPException(status_code=400, detail="A company with that name already exists")
            changes["name"] = name
        for key, value in changes.items():
            setattr(company, key, value)
        company.updated_by = _resolve_user_id(db, user)
        company.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(company)

        write_audit(db, user, "company.update", "company", company.id,
                    before=before, after=changes, client="web", commit=True)
        return company_service.company_summary(db, company)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"update_company failed: {e}", exc_info=True)
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to update the company")


@router.delete("/{company_id}", dependencies=[Depends(require_admin)])
def deactivate_company(company_id: int, db: Session = Depends(get_db), user=Depends(require_admin)):
    """Deactivate, never hard-delete — the ledger and past invoices must stay readable.
    Refuses while money is still outstanding."""
    try:
        company = _get_company(db, company_id)
        owed = company_service.outstanding(db, company.id)
        if abs(owed) > 0.009:
            raise HTTPException(
                status_code=409,
                detail=f"{company.name} still has ₹{owed:.2f} outstanding — settle the ledger first")

        company.is_active = False
        company.updated_by = _resolve_user_id(db, user)
        company.updated_at = datetime.utcnow()
        db.commit()

        write_audit(db, user, "company.deactivate", "company", company.id,
                    after={"name": company.name}, client="web", commit=True)
        return {"status": "deactivated", "company_id": company.id, "name": company.name}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"deactivate_company failed: {e}", exc_info=True)
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to deactivate the company")


# ---------------------------------------------------------------- ledger / statement

@router.get("/{company_id}/ledger")
def company_ledger(company_id: int,
                   from_: str | None = Query(None, alias="from"),
                   to: str | None = Query(None),
                   format: str = Query("json"),
                   db: Session = Depends(get_db),
                   user=Depends(require_reception_or_admin)):
    """City-ledger statement for the period (json | csv | pdf) — what you send the company."""
    from routers.reports import _meta, _range
    dfrom, dto = _range(from_, to, default_days=90)   # _range parses the ISO strings itself

    company = _get_company(db, company_id)
    rows = db.query(CompanyLedger).filter(
        CompanyLedger.company_id == company.id,
        CompanyLedger.created_at >= datetime.combine(dfrom, datetime.min.time()),
        CompanyLedger.created_at < datetime.combine(dto, datetime.max.time()),
    ).order_by(CompanyLedger.created_at, CompanyLedger.id).all()

    data_rows = [_ledger_row(r) for r in rows]
    charges = round(sum(r["amount"] for r in data_rows if r["amount"] > 0), 2)
    payments = round(-sum(r["amount"] for r in data_rows if r["amount"] < 0), 2)
    data = {
        "company": company_service.company_summary(db, company),
        "ageing": company_service.ageing_buckets(db, company),
        "from": str(dfrom), "to": str(dto),
        "charges_total": charges,
        "payments_total": payments,
        "closing_outstanding": company_service.outstanding(db, company.id),
        "rows": data_rows,
    }

    columns = ["Date", "Type", "Description", "Reference", "Amount (₹)", "Balance (₹)"]
    table = [[
        (r["when"] or "")[:10], r["type"], r["description"] or "", r["reference"] or "",
        f"{r['amount']:.2f}", f"{r['balance_after']:.2f}" if r["balance_after"] is not None else "",
    ] for r in data_rows]
    totals = ["", "", "Closing outstanding", "", "",
              f"{data['closing_outstanding']:.2f}"]
    meta = {**_meta(dfrom, dto), "Company": company.name,
            "GSTIN": company.gstin or "—",
            "Credit limit": f"₹{float(company.credit_limit or 0):.2f}"}
    return _export(format, data, title=f"Statement of account — {company.name}",
                   columns=columns, rows=table, totals_row=totals, meta=meta,
                   filename=f"statement_{company.id}_{dfrom}_{dto}")


@router.post("/{company_id}/payment", dependencies=[Depends(require_admin)])
def record_company_payment(company_id: int, data: CompanyPaymentRecord,
                           db: Session = Depends(get_db), user=Depends(require_admin)):
    """Record a settlement received from the company — a credit row on the city ledger.

    Admin-only and audited: this is the one place a corporate balance goes DOWN without an
    invoice being cancelled, so it stays off the front desk."""
    try:
        company = _get_company(db, company_id)

        cinv = None
        if data.company_invoice_id:
            cinv = db.query(CompanyInvoice).filter(
                CompanyInvoice.id == data.company_invoice_id,
                CompanyInvoice.company_id == company.id,
            ).first()
            if not cinv:
                raise HTTPException(status_code=404, detail="Consolidated invoice not found")

        row = company_service.post_ledger(
            db, company,
            type="payment",
            amount=-abs(float(data.amount)),
            description=data.description or f"Payment received — {data.method}",
            company_invoice_id=cinv.id if cinv else None,
            method=data.method,
            reference=data.reference,
            client_ref=data.client_ref,
            user=user,
        )

        if cinv is not None:
            cinv.paid_total = round(float(cinv.paid_total or 0) + abs(float(data.amount)), 2)
            if cinv.paid_total >= float(cinv.grand_total or 0) - 0.009:
                cinv.status = "paid"

        db.commit()
        write_audit(db, user, "company.payment", "company", company.id,
                    after={"amount": float(data.amount), "method": data.method,
                           "reference": data.reference, "ledger_id": row.id,
                           "company_invoice_id": cinv.id if cinv else None},
                    client="web", commit=True)
        return {"status": "recorded", "ledger": _ledger_row(row),
                **company_service.company_summary(db, company)}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"record_company_payment failed: {e}", exc_info=True)
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to record the payment")


@router.post("/{company_id}/adjustment", dependencies=[Depends(require_admin)])
def record_adjustment(company_id: int, data: CompanyAdjustment,
                      db: Session = Depends(get_db), user=Depends(require_admin)):
    """Append a signed correction (credit note / re-charge). Never edits an earlier row."""
    try:
        company = _get_company(db, company_id)
        row = company_service.post_ledger(
            db, company, type="adjustment", amount=float(data.amount),
            description=f"Adjustment — {data.reason}",
            client_ref=data.client_ref, user=user,
        )
        db.commit()
        write_audit(db, user, "company.adjustment", "company", company.id,
                    after={"amount": float(data.amount), "reason": data.reason,
                           "ledger_id": row.id},
                    client="web", commit=True)
        return {"status": "recorded", "ledger": _ledger_row(row),
                **company_service.company_summary(db, company)}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"record_adjustment failed: {e}", exc_info=True)
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to record the adjustment")


# ---------------------------------------------------------------- consolidated invoice

def _unbilled_stays(db: Session, company: Company, period_from: date, period_to: date):
    """Transferred stays in the window that no consolidated invoice covers yet."""
    return db.query(CompanyLedger).filter(
        CompanyLedger.company_id == company.id,
        CompanyLedger.type == "charge",
        CompanyLedger.company_invoice_id.is_(None),
        CompanyLedger.folio_id.isnot(None),
        CompanyLedger.created_at >= datetime.combine(period_from, datetime.min.time()),
        CompanyLedger.created_at < datetime.combine(period_to, datetime.max.time()),
    ).order_by(CompanyLedger.created_at, CompanyLedger.id).all()


@router.get("/{company_id}/invoice/preview", dependencies=[Depends(require_admin)])
def preview_company_invoice(company_id: int,
                            from_: str = Query(..., alias="from"),
                            to: str = Query(...),
                            db: Session = Depends(get_db)):
    """What a consolidated invoice for this window WOULD contain — look before you bill."""
    from routers.reports import _parse_date
    company = _get_company(db, company_id)
    dfrom, dto = _parse_date(from_, "from"), _parse_date(to, "to")
    stays = _unbilled_stays(db, company, dfrom, dto)
    totals = _consolidated_totals(db, stays)
    return {
        "company": company_service.company_summary(db, company),
        "period_from": str(dfrom), "period_to": str(dto),
        "stay_count": len(stays),
        "stays": [_ledger_row(s) for s in stays],
        **totals,
    }


def _consolidated_totals(db: Session, stays) -> dict:
    """GST split across every folio line behind the transferred stays.

    Reuses `folio._invoice_totals` on the union of the company-routed lines, so a consolidated
    bill splits CGST/SGST by exactly the same math as a single guest invoice — which is what
    makes the Tally export and /reports/gst reconcile."""
    from routers.folio import _invoice_totals

    folio_ids = [s.folio_id for s in stays if s.folio_id]
    if not folio_ids:
        return {"gst_rows": [], "taxable_total": 0.0, "cgst_total": 0.0,
                "sgst_total": 0.0, "grand_total": 0.0}

    lines = db.query(FolioCharge).filter(
        FolioCharge.folio_id.in_(folio_ids),
        FolioCharge.void == False,                      # noqa: E712
        FolioCharge.bill_to == company_service.BILL_TO_COMPANY,
    ).all()
    totals = _invoice_totals(lines)
    return {
        "gst_rows": totals["gst_rows"],
        "taxable_total": totals["taxable_total"],
        "cgst_total": totals["cgst_total"],
        "sgst_total": totals["sgst_total"],
        "grand_total": totals["grand_total"],
    }


@router.post("/{company_id}/invoice", dependencies=[Depends(require_admin)])
def create_company_invoice(company_id: int, data: CompanyInvoiceCreate,
                           db: Session = Depends(get_db), user=Depends(require_admin)):
    """Raise ONE consolidated GST invoice covering every stay transferred in the period.

    Numbers come from the SAME `invoice_counters` table as guest invoices under a distinct FY
    label prefix ('C2026-27'), so the two series can never collide."""
    from routers.folio import _allocate_invoice_seq, _fy_label
    try:
        company = _get_company(db, company_id)
        stays = _unbilled_stays(db, company, data.period_from, data.period_to)
        if not stays:
            raise HTTPException(status_code=400,
                                detail="No unbilled company stays in that period")

        totals = _consolidated_totals(db, stays)
        today = date.today()
        fy = _fy_label(today)
        prefix = get_backoffice_config(db)["company_invoice_prefix"]
        seq = _allocate_invoice_seq(db, f"C{fy}")   # separate counter row, same table

        cinv = CompanyInvoice(
            company_id=company.id,
            invoice_no=f"{prefix}/{fy}/{seq:05d}",
            fy_label=fy,
            seq=seq,
            invoice_date=today,
            period_from=data.period_from,
            period_to=data.period_to,
            taxable_total=totals["taxable_total"],
            cgst_total=totals["cgst_total"],
            sgst_total=totals["sgst_total"],
            grand_total=totals["grand_total"],
            gst_breakup=json.dumps(totals["gst_rows"]),
            stay_count=len(stays),
            status="issued",
            notes=data.notes,
            created_by=_resolve_user_id(db, user),
        )
        db.add(cinv)
        db.flush()

        # Stamp the ledger rows + the per-stay invoices so a stay is never billed twice.
        folio_ids = []
        for s in stays:
            s.company_invoice_id = cinv.id
            if s.folio_id:
                folio_ids.append(s.folio_id)
        if folio_ids:
            db.query(Invoice).filter(Invoice.folio_id.in_(folio_ids)).update(
                {Invoice.company_invoice_id: cinv.id, Invoice.company_id: company.id},
                synchronize_session=False)

        db.commit()
        db.refresh(cinv)

        write_audit(db, user, "company.invoice_create", "company_invoice", cinv.id,
                    after={"company_id": company.id, "invoice_no": cinv.invoice_no,
                           "grand_total": float(cinv.grand_total or 0),
                           "stay_count": cinv.stay_count,
                           "period": f"{data.period_from} to {data.period_to}"},
                    client="web", commit=True)
        return _company_invoice_dict(cinv)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"create_company_invoice failed: {e}", exc_info=True)
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to create the consolidated invoice")


@router.get("/{company_id}/invoices", dependencies=[Depends(require_admin)])
def list_company_invoices(company_id: int, db: Session = Depends(get_db)):
    company = _get_company(db, company_id)
    rows = db.query(CompanyInvoice).filter(
        CompanyInvoice.company_id == company.id
    ).order_by(CompanyInvoice.created_at.desc()).all()
    return {"total": len(rows), "data": [_company_invoice_dict(r) for r in rows]}


# ---------------------------------------------------------------- consolidated invoice output
# Mounted on its own prefix: these address a company_invoice, not a company.
invoice_router = APIRouter(prefix="/company-invoices", tags=["Companies"])


def _get_cinvoice(db: Session, cinvoice_id: int) -> CompanyInvoice:
    cinv = db.query(CompanyInvoice).filter(CompanyInvoice.id == cinvoice_id).first()
    if not cinv:
        raise HTTPException(status_code=404, detail="Consolidated invoice not found")
    return cinv


def _cinvoice_payload(db: Session, cinv: CompanyInvoice) -> dict:
    """Everything the consolidated-invoice PDF renders: header, per-stay lines, GST split."""
    company = _get_company(db, cinv.company_id)
    ledger_rows = db.query(CompanyLedger).filter(
        CompanyLedger.company_invoice_id == cinv.id,
        CompanyLedger.type == "charge",
    ).order_by(CompanyLedger.created_at, CompanyLedger.id).all()

    stays = []
    for row in ledger_rows:
        booking = db.query(Booking).options(joinedload(Booking.guest)).filter(
            Booking.booking_id == row.booking_id).first() if row.booking_id else None
        invoice = db.query(Invoice).filter(Invoice.folio_id == row.folio_id).first() \
            if row.folio_id else None
        stays.append({
            "booking_id": row.booking_id,
            "guest_name": booking.guest.name if booking and booking.guest else "—",
            "check_in": f"{booking.check_in:%d-%m-%Y}" if booking else "",
            "check_out": f"{booking.check_out:%d-%m-%Y}" if booking else "",
            "invoice_no": invoice.invoice_no if invoice else "",
            "amount": float(row.amount or 0),
        })

    return {
        "invoice_no": cinv.invoice_no,
        "invoice_date": f"{cinv.invoice_date:%d-%m-%Y}" if cinv.invoice_date else "",
        "period": (f"{cinv.period_from:%d-%m-%Y} to {cinv.period_to:%d-%m-%Y}"
                   if cinv.period_from and cinv.period_to else ""),
        "company": {
            "name": company.name, "gstin": company.gstin, "address": company.address,
            "city": company.city, "state": company.state, "state_code": company.state_code,
            "contact_person": company.contact_person, "phone": company.phone,
            "email": company.email,
        },
        "stays": stays,
        "gst_rows": json.loads(cinv.gst_breakup) if cinv.gst_breakup else [],
        "taxable_total": float(cinv.taxable_total or 0),
        "cgst_total": float(cinv.cgst_total or 0),
        "sgst_total": float(cinv.sgst_total or 0),
        "grand_total": float(cinv.grand_total or 0),
        "paid_total": float(cinv.paid_total or 0),
        "balance_due": round(float(cinv.grand_total or 0) - float(cinv.paid_total or 0), 2),
        "status": cinv.status,
        "notes": cinv.notes,
    }


@invoice_router.get("/{cinvoice_id}", dependencies=[Depends(require_admin)])
def get_company_invoice(cinvoice_id: int, db: Session = Depends(get_db)):
    cinv = _get_cinvoice(db, cinvoice_id)
    return {**_company_invoice_dict(cinv), "detail": _cinvoice_payload(db, cinv)}


@invoice_router.get("/{cinvoice_id}/pdf", dependencies=[Depends(require_admin)])
def company_invoice_pdf(cinvoice_id: int, db: Session = Depends(get_db)):
    """Branded consolidated GST invoice PDF — what the accountant emails the company."""
    from utils.pdf_generator import generate_company_invoice_pdf
    cinv = _get_cinvoice(db, cinvoice_id)
    path = generate_company_invoice_pdf(_cinvoice_payload(db, cinv))
    return FileResponse(path, media_type="application/pdf",
                        filename=f"{cinv.invoice_no.replace('/', '_')}.pdf")


@invoice_router.post("/{cinvoice_id}/email", dependencies=[Depends(require_admin)])
def email_company_invoice(cinvoice_id: int, db: Session = Depends(get_db),
                          user=Depends(require_admin)):
    """Email the consolidated invoice to the company's billing contact (reuses Mailjet)."""
    from utils.pdf_generator import generate_company_invoice_pdf
    try:
        cinv = _get_cinvoice(db, cinvoice_id)
        company = _get_company(db, cinv.company_id)
        if not company.email:
            raise HTTPException(status_code=400, detail="This company has no email on file")

        payload = _cinvoice_payload(db, cinv)
        path = generate_company_invoice_pdf(payload)

        from utils.email_service import send_company_invoice_email
        try:
            send_company_invoice_email(payload, path)
        except Exception as e:
            logger.error(f"company invoice email failed: {e}", exc_info=True)
            raise HTTPException(status_code=502, detail="Failed to send the invoice email")

        write_audit(db, user, "company.invoice_email", "company_invoice", cinv.id,
                    after={"to": company.email, "invoice_no": cinv.invoice_no},
                    client="web", commit=True)
        return {"sent": True, "to": company.email, "invoice_no": cinv.invoice_no}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"email_company_invoice failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to email the invoice")


@invoice_router.post("/{cinvoice_id}/cancel", dependencies=[Depends(require_admin)])
def cancel_company_invoice(cinvoice_id: int, reason: str = Query(..., min_length=3),
                           db: Session = Depends(get_db), user=Depends(require_admin)):
    """Cancel a consolidated invoice and release its stays back to unbilled.

    The invoice row and its number are KEPT (a GST series must have no holes) — only the
    stay links are released so the period can be re-billed."""
    try:
        cinv = _get_cinvoice(db, cinvoice_id)
        if cinv.status == "cancelled":
            return _company_invoice_dict(cinv)
        if float(cinv.paid_total or 0) > 0:
            raise HTTPException(status_code=409,
                                detail="Payments are recorded against this invoice — reverse them first")

        db.query(CompanyLedger).filter(
            CompanyLedger.company_invoice_id == cinv.id,
            CompanyLedger.type == "charge",
        ).update({CompanyLedger.company_invoice_id: None}, synchronize_session=False)
        db.query(Invoice).filter(Invoice.company_invoice_id == cinv.id).update(
            {Invoice.company_invoice_id: None}, synchronize_session=False)

        cinv.status = "cancelled"
        cinv.cancelled_at = datetime.utcnow()
        cinv.cancel_reason = reason
        db.commit()

        write_audit(db, user, "company.invoice_cancel", "company_invoice", cinv.id,
                    after={"invoice_no": cinv.invoice_no, "reason": reason},
                    client="web", commit=True)
        return _company_invoice_dict(cinv)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"cancel_company_invoice failed: {e}", exc_info=True)
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to cancel the invoice")
