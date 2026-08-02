"""Vendor / AMC tracking endpoints (prompt 18, slice 13).

Suppliers and service providers (laundry, lock AMC, linen, electrical, IT...) plus their dated
contracts, with renewal reminders so a lock-servicing AMC never lapses unnoticed.

Reuse, not rebuild:
  * Spend per vendor comes from the EXISTING prompt-12 `expenses` ledger via the additive
    `expenses.vendor_id`. Maintenance part purchases already post an Expense
    (`routers/maintenance.purchase_item`) — they now carry the vendor through.
  * Renewal alerts ride the EXISTING WhatsApp scheduler (`utils/whatsapp_jobs.run_whatsapp_jobs`)
    rather than starting a fifth APScheduler; the sweep itself lives in `utils/vendor_jobs.py`.
"""
import logging
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from database import SessionLocal
from models import Expense, Vendor, VendorContract
from schemas import (VendorContractCreate, VendorContractUpdate, VendorCreate, VendorUpdate)
from utils import settings as app_settings
from utils.audit import _resolve_user_id, write_audit
from utils.auth_utils import require_admin, require_reception_or_admin

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/vendors", tags=["Vendors"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("/health", dependencies=[Depends(require_reception_or_admin)])
def health():
    return {"status": "ok", "module": "vendors"}


# ---------------------------------------------------------------- helpers

def _get_vendor(db: Session, vendor_id: int) -> Vendor:
    vendor = db.query(Vendor).filter(Vendor.id == vendor_id).first()
    if not vendor:
        raise HTTPException(status_code=404, detail="Vendor not found")
    return vendor


def _get_contract(db: Session, contract_id: int) -> VendorContract:
    contract = db.query(VendorContract).filter(VendorContract.id == contract_id).first()
    if not contract:
        raise HTTPException(status_code=404, detail="Contract not found")
    return contract


def _vendor_spend(db: Session, vendor_id: int) -> float:
    """Total petty-cash / purchase outflow attributed to this vendor (prompt-12 ledger)."""
    total = db.query(func.coalesce(func.sum(Expense.amount), 0)).filter(
        Expense.vendor_id == vendor_id).scalar()
    return round(float(total or 0), 2)


def days_to_renewal(contract: VendorContract, as_of: date | None = None) -> int | None:
    """Days until the contract ends. Negative = already expired. None = open-ended."""
    if not contract.end_date:
        return None
    return (contract.end_date - (as_of or date.today())).days


def is_due_for_renewal(contract: VendorContract, as_of: date | None = None,
                       fallback_lead_days: int = 30) -> bool:
    """True when the contract is active and its end date falls inside its reminder window."""
    if contract.status != "active":
        return False
    left = days_to_renewal(contract, as_of)
    if left is None:
        return False
    window = int(contract.renewal_reminder_days or fallback_lead_days)
    return left <= window


def _contract_dict(c: VendorContract, vendor_name: str | None = None,
                   as_of: date | None = None) -> dict:
    left = days_to_renewal(c, as_of)
    return {
        "id": c.id,
        "vendor_id": c.vendor_id,
        "vendor_name": vendor_name,
        "title": c.title,
        "contract_type": c.contract_type,
        "start_date": str(c.start_date) if c.start_date else None,
        "end_date": str(c.end_date) if c.end_date else None,
        "renewal_reminder_days": int(c.renewal_reminder_days or 30),
        "amount": float(c.amount) if c.amount is not None else None,
        "billing_cycle": c.billing_cycle,
        "document_url": c.document_url,
        "status": c.status,
        "days_to_renewal": left,
        "expired": left is not None and left < 0,
        "due_for_renewal": is_due_for_renewal(c, as_of),
        "last_reminder_sent_at": str(c.last_reminder_sent_at) if c.last_reminder_sent_at else None,
        "notes": c.notes,
        "created_at": str(c.created_at) if c.created_at else None,
    }


def _vendor_dict(db: Session, v: Vendor, with_contracts: bool = False) -> dict:
    contracts = db.query(VendorContract).filter(
        VendorContract.vendor_id == v.id).order_by(VendorContract.end_date).all()
    data = {
        "id": v.id,
        "name": v.name,
        "category": v.category,
        "contact_person": v.contact_person,
        "phone": v.phone,
        "email": v.email,
        "gstin": v.gstin,
        "address": v.address,
        "is_active": bool(v.is_active),
        "notes": v.notes,
        "total_spend": _vendor_spend(db, v.id),
        "contract_count": len(contracts),
        "active_contracts": sum(1 for c in contracts if c.status == "active"),
        "renewals_due": sum(1 for c in contracts if is_due_for_renewal(c)),
        "created_at": str(v.created_at) if v.created_at else None,
    }
    if with_contracts:
        data["contracts"] = [_contract_dict(c, v.name) for c in contracts]
    return data


# ---------------------------------------------------------------- vendor CRUD

@router.get("/")
def list_vendors(q: str | None = Query(None), category: str | None = Query(None),
                 active: bool | None = Query(None), db: Session = Depends(get_db),
                 user=Depends(require_reception_or_admin)):
    """Vendor list with spend + contract counts. Readable by reception so the desk can look up
    a laundry/lock contact without an admin login."""
    query = db.query(Vendor)
    if active is not None:
        query = query.filter(Vendor.is_active == active)
    if category:
        query = query.filter(Vendor.category == category)
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(or_(Vendor.name.ilike(like), Vendor.contact_person.ilike(like),
                                 Vendor.phone.ilike(like), Vendor.gstin.ilike(like)))
    rows = query.order_by(Vendor.name).all()
    return {"total": len(rows), "data": [_vendor_dict(db, v) for v in rows]}


@router.post("/", dependencies=[Depends(require_admin)])
def create_vendor(data: VendorCreate, db: Session = Depends(get_db), user=Depends(require_admin)):
    try:
        name = data.name.strip()
        if db.query(Vendor).filter(Vendor.name.ilike(name)).first():
            raise HTTPException(status_code=400, detail="A vendor with that name already exists")
        payload = data.model_dump()
        payload["name"] = name
        # The schema now accepts any slug; the admin-editable list is the real constraint (FE-6).
        payload["category"] = app_settings.validate_category(db, "vendor", payload.get("category"))
        vendor = Vendor(**payload, created_by=_resolve_user_id(db, user))
        db.add(vendor)
        db.commit()
        db.refresh(vendor)
        write_audit(db, user, "vendor.create", "vendor", vendor.id,
                    after={"name": vendor.name, "category": vendor.category},
                    client="web", commit=True)
        return _vendor_dict(db, vendor)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"create_vendor failed: {e}", exc_info=True)
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to create the vendor")


@router.get("/renewals")
def upcoming_renewals(within_days: int = Query(60, ge=1, le=365),
                      db: Session = Depends(get_db),
                      user=Depends(require_reception_or_admin)):
    """Contracts ending inside the window (plus anything already expired) — the "what needs
    attention" list. Declared before /{vendor_id} so the literal path wins the route match."""
    today = date.today()
    contracts = db.query(VendorContract).filter(
        VendorContract.status == "active",
        VendorContract.end_date.isnot(None),
    ).order_by(VendorContract.end_date).all()

    names = {v.id: v.name for v in db.query(Vendor).all()}
    rows = [_contract_dict(c, names.get(c.vendor_id), today) for c in contracts
            if (c.end_date - today).days <= within_days]
    return {
        "within_days": within_days,
        "total": len(rows),
        "expired": sum(1 for r in rows if r["expired"]),
        "due_soon": sum(1 for r in rows if not r["expired"]),
        "data": rows,
    }


@router.post("/reminders/run", dependencies=[Depends(require_admin)])
def run_reminders(db: Session = Depends(get_db), user=Depends(require_admin)):
    """Fire the renewal sweep now instead of waiting for the scheduled run.

    The same function the WhatsApp scheduler calls — so a manual run and the automatic one can
    never drift apart."""
    from utils.vendor_jobs import run_vendor_renewal_sweep
    result = run_vendor_renewal_sweep(db)
    write_audit(db, user, "vendor.reminders_run", "vendor_contract", None,
                after=result, client="web", commit=True)
    return result


@router.get("/{vendor_id}")
def get_vendor(vendor_id: int, db: Session = Depends(get_db),
               user=Depends(require_reception_or_admin)):
    vendor = _get_vendor(db, vendor_id)
    recent = db.query(Expense).filter(Expense.vendor_id == vendor.id).order_by(
        Expense.created_at.desc()).limit(20).all()
    return {
        **_vendor_dict(db, vendor, with_contracts=True),
        "recent_expenses": [{
            "id": e.id, "when": str(e.created_at) if e.created_at else None,
            "category": e.category, "description": e.description,
            "amount": float(e.amount or 0),
        } for e in recent],
    }


@router.put("/{vendor_id}", dependencies=[Depends(require_admin)])
def update_vendor(vendor_id: int, data: VendorUpdate, db: Session = Depends(get_db),
                  user=Depends(require_admin)):
    try:
        vendor = _get_vendor(db, vendor_id)
        before = {"name": vendor.name, "category": vendor.category,
                  "is_active": bool(vendor.is_active)}
        changes = data.model_dump(exclude_unset=True)
        if changes.get("name"):
            name = changes["name"].strip()
            if db.query(Vendor).filter(Vendor.name.ilike(name), Vendor.id != vendor.id).first():
                raise HTTPException(status_code=400,
                                    detail="A vendor with that name already exists")
            changes["name"] = name
        if changes.get("category"):
            changes["category"] = app_settings.validate_category(db, "vendor", changes["category"])
        for key, value in changes.items():
            setattr(vendor, key, value)
        vendor.updated_by = _resolve_user_id(db, user)
        vendor.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(vendor)
        write_audit(db, user, "vendor.update", "vendor", vendor.id,
                    before=before, after=changes, client="web", commit=True)
        return _vendor_dict(db, vendor)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"update_vendor failed: {e}", exc_info=True)
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to update the vendor")


@router.delete("/{vendor_id}", dependencies=[Depends(require_admin)])
def deactivate_vendor(vendor_id: int, db: Session = Depends(get_db), user=Depends(require_admin)):
    """Deactivate, never hard-delete — past expenses reference the vendor."""
    try:
        vendor = _get_vendor(db, vendor_id)
        active = db.query(VendorContract).filter(
            VendorContract.vendor_id == vendor.id,
            VendorContract.status == "active").count()
        if active:
            raise HTTPException(
                status_code=409,
                detail=f"{vendor.name} still has {active} active contract(s) — close them first")
        vendor.is_active = False
        vendor.updated_by = _resolve_user_id(db, user)
        vendor.updated_at = datetime.utcnow()
        db.commit()
        write_audit(db, user, "vendor.deactivate", "vendor", vendor.id,
                    after={"name": vendor.name}, client="web", commit=True)
        return {"status": "deactivated", "vendor_id": vendor.id, "name": vendor.name}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"deactivate_vendor failed: {e}", exc_info=True)
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to deactivate the vendor")


# ---------------------------------------------------------------- contracts

@router.get("/{vendor_id}/contracts")
def list_contracts(vendor_id: int, db: Session = Depends(get_db),
                   user=Depends(require_reception_or_admin)):
    vendor = _get_vendor(db, vendor_id)
    rows = db.query(VendorContract).filter(
        VendorContract.vendor_id == vendor.id).order_by(VendorContract.end_date).all()
    return {"total": len(rows), "data": [_contract_dict(c, vendor.name) for c in rows]}


@router.post("/{vendor_id}/contracts", dependencies=[Depends(require_admin)])
def create_contract(vendor_id: int, data: VendorContractCreate, db: Session = Depends(get_db),
                    user=Depends(require_admin)):
    try:
        vendor = _get_vendor(db, vendor_id)
        contract = VendorContract(vendor_id=vendor.id, **data.model_dump(),
                                  created_by=_resolve_user_id(db, user))
        db.add(contract)
        db.commit()
        db.refresh(contract)
        write_audit(db, user, "vendor.contract_create", "vendor_contract", contract.id,
                    after={"vendor_id": vendor.id, "title": contract.title,
                           "contract_type": contract.contract_type,
                           "end_date": str(contract.end_date) if contract.end_date else None},
                    client="web", commit=True)
        return _contract_dict(contract, vendor.name)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"create_contract failed: {e}", exc_info=True)
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to create the contract")


# Contract-scoped operations get their own prefix segment so they never collide with /{vendor_id}.
@router.put("/contracts/{contract_id}", dependencies=[Depends(require_admin)])
def update_contract(contract_id: int, data: VendorContractUpdate, db: Session = Depends(get_db),
                    user=Depends(require_admin)):
    try:
        contract = _get_contract(db, contract_id)
        before = {"title": contract.title, "status": contract.status,
                  "end_date": str(contract.end_date) if contract.end_date else None}
        changes = data.model_dump(exclude_unset=True)
        for key, value in changes.items():
            setattr(contract, key, value)
        contract.updated_by = _resolve_user_id(db, user)
        contract.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(contract)
        write_audit(db, user, "vendor.contract_update", "vendor_contract", contract.id,
                    before=before, after=changes, client="web", commit=True)
        vendor = _get_vendor(db, contract.vendor_id)
        return _contract_dict(contract, vendor.name)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"update_contract failed: {e}", exc_info=True)
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to update the contract")


@router.post("/contracts/{contract_id}/renew", dependencies=[Depends(require_admin)])
def renew_contract(contract_id: int, new_end_date: date = Query(...),
                   amount: float | None = Query(None, ge=0),
                   db: Session = Depends(get_db), user=Depends(require_admin)):
    """Extend a contract and clear its reminder flag so the next window alerts again."""
    try:
        contract = _get_contract(db, contract_id)
        before = {"end_date": str(contract.end_date) if contract.end_date else None,
                  "amount": float(contract.amount) if contract.amount is not None else None,
                  "status": contract.status}
        if contract.end_date and new_end_date <= contract.end_date:
            raise HTTPException(status_code=400,
                                detail="The new end date must be after the current one")
        contract.end_date = new_end_date
        if amount is not None:
            contract.amount = amount
        contract.status = "active"
        contract.last_reminder_sent_at = None      # re-arm the reminder for the new window
        contract.updated_by = _resolve_user_id(db, user)
        contract.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(contract)
        write_audit(db, user, "vendor.contract_renew", "vendor_contract", contract.id,
                    before=before, after={"end_date": str(new_end_date), "amount": amount},
                    client="web", commit=True)
        vendor = _get_vendor(db, contract.vendor_id)
        return _contract_dict(contract, vendor.name)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"renew_contract failed: {e}", exc_info=True)
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to renew the contract")


@router.delete("/contracts/{contract_id}", dependencies=[Depends(require_admin)])
def cancel_contract(contract_id: int, reason: str = Query(..., min_length=3),
                    db: Session = Depends(get_db), user=Depends(require_admin)):
    """Cancel a contract (kept for history, never hard-deleted)."""
    try:
        contract = _get_contract(db, contract_id)
        contract.status = "cancelled"
        contract.notes = f"{contract.notes + chr(10) if contract.notes else ''}Cancelled: {reason}"
        contract.updated_by = _resolve_user_id(db, user)
        contract.updated_at = datetime.utcnow()
        db.commit()
        write_audit(db, user, "vendor.contract_cancel", "vendor_contract", contract.id,
                    after={"reason": reason}, client="web", commit=True)
        return {"status": "cancelled", "contract_id": contract.id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"cancel_contract failed: {e}", exc_info=True)
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to cancel the contract")
