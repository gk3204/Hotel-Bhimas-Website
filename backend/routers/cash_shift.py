"""Cash & shift management (prompt 12) — opening float, expense tracking, closing reconciliation.

A reception cash drawer is accountable per shift (or per day). The flow:
  1. open  -> a receptionist opens a shift with an opening float (one open shift per station).
  2. cash payments (routers/payments.py) auto-link to the open shift via Payment.shift_id;
     cash refunds/excess-returns link via Payment.refund_shift_id (built in prompt 08, link-only).
  3. expense -> petty-cash outflows logged against the open shift.
  4. close -> expected_cash = opening + cash collections - expenses - cash payouts; the staff
     enters counted_cash; variance = counted - expected; the shift is locked (append-only —
     there is no reopen/edit endpoint, corrections are new shifts) and, if the variance exceeds
     the admin's threshold, a `cash_variance` FraudAlert is raised for the owner (WhatsApp
     delivery lands with prompt 15; interim = log + the fraud dashboard).

Admin sets the cycle (shift|day) and the variance-alert threshold at runtime via the app_settings
store (utils/settings.py) — GET/PUT /cash-shift/config.

Single-property assumption (locked): a station's open shift is unambiguous; payments.py's
station-agnostic _open_shift and this router's per-station lookup agree when there is one station.
"""
import json
import logging
from datetime import datetime
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from database import SessionLocal
from models import CashShift, Expense, Payment, User, FraudAlert
from schemas import ShiftOpenRequest, ExpenseCreate, ShiftCloseRequest, CashConfigUpdate
from utils.auth_utils import require_reception_or_admin, require_admin
from utils.audit import write_audit, _resolve_user_id
from utils.settings import (get_cash_config, set_setting,
                            CASH_CYCLE_KEY, CASH_VARIANCE_THRESHOLD_KEY, CASH_VARIANCE_ALERT_KEY)
from utils.pdf_generator import generate_shift_report_pdf

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/cash-shift", tags=["Cash & Shift"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# --------------------------------------------------------------------- helpers

def _open_shift(db: Session, station_id: Optional[str] = None):
    """Most recent open shift, optionally scoped to a station. Station-scoped when a station
    is given (the desktop always sends AppConfig.StationId); agnostic otherwise (matches
    payments._open_shift for the single-property case)."""
    q = db.query(CashShift).filter(CashShift.status == "open")
    if station_id is not None:
        q = q.filter(CashShift.station_id == station_id)
    return q.order_by(CashShift.opened_at.desc()).first()


def _sum(db: Session, col, *conds) -> float:
    return float(db.query(func.coalesce(func.sum(col), 0)).filter(*conds).scalar() or 0)


def _compute_totals(db: Session, shift: CashShift) -> dict:
    """Live drawer totals for an open shift, computed from the payment/expense links."""
    collections = _sum(db, Payment.amount, Payment.method == "cash",
                       Payment.status == "paid", Payment.shift_id == shift.id)
    expenses = _sum(db, Expense.amount, Expense.shift_id == shift.id)
    payouts = _sum(db, Payment.refund_amount, Payment.refund_mode == "cash",
                   Payment.refund_status == "completed", Payment.refund_shift_id == shift.id)
    opening = float(shift.opening_balance or 0)
    return {
        "opening_balance": round(opening, 2),
        "collections_cash": round(collections, 2),
        "expenses_total": round(expenses, 2),
        "payouts_total": round(payouts, 2),
        "expected_cash": round(opening + collections - expenses - payouts, 2),
    }


def _user_name(db: Session, uid) -> Optional[str]:
    if not uid:
        return None
    u = db.query(User).filter(User.user_id == uid).first()
    return (u.full_name or u.username) if u else None


def _serialize(db: Session, shift: CashShift) -> dict:
    """Shift as a JSON dict. Open shifts use live totals; closed shifts use the snapshot
    frozen on the row at close."""
    if shift.status == "open":
        totals = _compute_totals(db, shift)
    else:
        totals = {
            "opening_balance": float(shift.opening_balance or 0),
            "collections_cash": float(shift.collections_cash or 0),
            "expenses_total": float(shift.expenses_total or 0),
            "payouts_total": float(shift.payouts_total or 0),
            "expected_cash": float(shift.expected_cash or 0),
        }
    return {
        "id": shift.id,
        "station_id": shift.station_id,
        "period": shift.period,
        "status": shift.status,
        "opened_at": shift.opened_at.isoformat() if shift.opened_at else None,
        "closed_at": shift.closed_at.isoformat() if shift.closed_at else None,
        "staff_id": shift.staff_id,
        "staff_name": _user_name(db, shift.staff_id),
        "closed_by_name": _user_name(db, shift.closed_by),
        "counted_cash": float(shift.counted_cash) if shift.counted_cash is not None else None,
        "variance": float(shift.variance) if shift.variance is not None else None,
        **totals,
    }


def _report_data(db: Session, shift: CashShift) -> dict:
    """Flatten a shift into the dict generate_shift_report_pdf expects."""
    s = _serialize(db, shift)
    expenses = (db.query(Expense).filter(Expense.shift_id == shift.id)
                .order_by(Expense.created_at).all())
    denoms = json.loads(shift.denominations) if shift.denominations else None
    s["expenses"] = [{"category": e.category, "description": e.description,
                      "amount": float(e.amount), "receipt_ref": e.receipt_url,
                      "created_at": e.created_at} for e in expenses]
    s["denominations"] = denoms
    s["close_note"] = shift.close_note
    return s


# --------------------------------------------------------------------- config
# (declared before /{shift_id} so the literal path wins the route match)

@router.get("/config", dependencies=[Depends(require_reception_or_admin)])
def get_config_ep(db: Session = Depends(get_db)):
    """Current cash-shift config (cycle + variance threshold). Reception reads it to label the
    period; admin edits it via PUT."""
    return get_cash_config(db)


@router.put("/config")
def update_config_ep(data: CashConfigUpdate, db: Session = Depends(get_db),
                     user=Depends(require_admin)):
    """Admin sets the cycle (shift|day) and/or variance-alert threshold at runtime."""
    before = get_cash_config(db)
    if data.cycle is not None:
        set_setting(db, CASH_CYCLE_KEY, data.cycle, user)
    if data.variance_threshold is not None:
        set_setting(db, CASH_VARIANCE_THRESHOLD_KEY, round(data.variance_threshold, 2), user)
    if data.variance_alert_enabled is not None:
        set_setting(db, CASH_VARIANCE_ALERT_KEY,
                    "true" if data.variance_alert_enabled else "false", user)
    db.commit()
    after = get_cash_config(db)
    write_audit(db, user, "settings.cash_update", "app_settings", None,
                before=before, after=after, client="web", commit=True)
    return after


# --------------------------------------------------------------------- current / list

@router.get("/current", dependencies=[Depends(require_reception_or_admin)])
def current_shift(station_id: Optional[str] = None, db: Session = Depends(get_db)):
    """The station's open shift + live totals, or {open: false}. Drives the desktop's
    login-time open-shift gate."""
    cfg = get_cash_config(db)
    shift = _open_shift(db, station_id)
    if not shift:
        return {"open": False, "station_id": station_id, "config": cfg}
    return {"open": True, "shift": _serialize(db, shift), "config": cfg}


@router.get("/", dependencies=[Depends(require_reception_or_admin)])
def list_shifts(status: Optional[str] = None, station_id: Optional[str] = None,
                limit: int = Query(50, ge=1, le=200), db: Session = Depends(get_db)):
    """Recent shifts (newest first) for the owner/admin review + desktop history."""
    q = db.query(CashShift)
    if status:
        q = q.filter(CashShift.status == status)
    if station_id:
        q = q.filter(CashShift.station_id == station_id)
    rows = q.order_by(CashShift.opened_at.desc()).limit(limit).all()
    return {"data": [_serialize(db, s) for s in rows]}


# --------------------------------------------------------------------- open / expense / close

@router.post("/open")
def open_shift(data: ShiftOpenRequest, db: Session = Depends(get_db),
               user=Depends(require_reception_or_admin)):
    """Open a cash drawer with an opening float. 409 if a shift is already open for the station."""
    existing = _open_shift(db, data.station_id)
    if existing:
        raise HTTPException(status_code=409,
                            detail=f"A cash shift is already open for this station (shift #{existing.id}).")
    staff_id = _resolve_user_id(db, user)
    if not staff_id:
        raise HTTPException(status_code=400, detail="Could not resolve the logged-in staff member.")
    cfg = get_cash_config(db)
    # Duty-roster link (prompt 18 slice 12): best-effort match to this staff member's planned duty
    # today, so the staff report can compare the drawer actually opened against the duty planned.
    # A missing roster row is fine - the link is nullable and nothing depends on it existing.
    try:
        from routers.roster import match_roster_shift
        planned = match_roster_shift(db, staff_id)
    except Exception as e:
        logger.warning(f"roster match failed for staff {staff_id}: {e}")
        planned = None
    shift = CashShift(
        staff_id=staff_id,
        station_id=data.station_id,
        period=data.period or cfg["cycle"],
        opening_balance=Decimal(str(round(data.opening_balance, 2))),
        status="open",
        roster_shift_id=planned.id if planned else None,
    )
    db.add(shift)
    db.commit()
    db.refresh(shift)
    write_audit(db, user, "shift.open", "cash_shift", shift.id,
                after={"station_id": shift.station_id, "period": shift.period,
                       "opening_balance": float(shift.opening_balance)},
                client="desktop", commit=True)
    logger.info(f"✅ Cash shift #{shift.id} opened (station={shift.station_id}, "
                f"float=₹{shift.opening_balance}, period={shift.period})")
    return {"open": True, "shift": _serialize(db, shift), "config": cfg}


@router.post("/expense")
def add_expense(data: ExpenseCreate, db: Session = Depends(get_db),
                user=Depends(require_reception_or_admin)):
    """Log a petty-cash expense against the station's open shift. Idempotent on client_ref."""
    if data.client_ref:
        existing = db.query(Expense).filter(Expense.client_ref == data.client_ref).first()
        if existing:
            shift = db.query(CashShift).filter(CashShift.id == existing.shift_id).first()
            return {"expense_id": existing.id, "duplicate": True,
                    "shift": _serialize(db, shift) if shift else None}
    shift = _open_shift(db, data.station_id)
    if not shift:
        raise HTTPException(status_code=409,
                            detail="No cash shift is open — open a shift before logging an expense.")
    exp = Expense(
        shift_id=shift.id,
        category=data.category,
        description=data.description,
        amount=Decimal(str(round(data.amount, 2))),
        receipt_url=data.receipt_ref,
        client_ref=data.client_ref,
        created_by=_resolve_user_id(db, user),
    )
    db.add(exp)
    db.commit()
    db.refresh(exp)
    write_audit(db, user, "expense.add", "expense", exp.id,
                after={"shift_id": shift.id, "amount": float(exp.amount),
                       "category": exp.category, "description": exp.description},
                client="desktop", commit=True)
    logger.info(f"✅ Expense ₹{exp.amount} ({exp.category}) on shift #{shift.id}")
    return {"expense_id": exp.id, "duplicate": False, "shift": _serialize(db, shift)}


@router.post("/close")
def close_shift(data: ShiftCloseRequest, db: Session = Depends(get_db),
                user=Depends(require_reception_or_admin)):
    """Close the station's open shift: snapshot totals, compute variance, lock (append-only),
    and raise an owner alert if the variance exceeds the configured threshold."""
    shift = _open_shift(db, data.station_id)
    if not shift:
        raise HTTPException(status_code=409, detail="No open cash shift to close for this station.")

    totals = _compute_totals(db, shift)
    counted = round(data.counted_cash, 2)
    variance = round(counted - totals["expected_cash"], 2)

    # Snapshot the drawer totals + lock the shift (there is no reopen/edit endpoint).
    shift.collections_cash = Decimal(str(totals["collections_cash"]))
    shift.expenses_total = Decimal(str(totals["expenses_total"]))
    shift.payouts_total = Decimal(str(totals["payouts_total"]))
    shift.expected_cash = Decimal(str(totals["expected_cash"]))
    shift.counted_cash = Decimal(str(counted))
    shift.variance = Decimal(str(variance))
    shift.denominations = json.dumps(data.denominations) if data.denominations else None
    shift.close_note = data.note
    shift.status = "closed"
    shift.closed_by = _resolve_user_id(db, user)
    shift.closed_at = datetime.utcnow()

    cfg = get_cash_config(db)
    alerted = False
    if cfg["variance_alert_enabled"] and abs(variance) > cfg["variance_threshold"]:
        severity = "high" if abs(variance) >= cfg["variance_threshold"] * 3 else "med"
        detail = {
            "shift_id": shift.id, "station_id": shift.station_id, "staff_id": shift.staff_id,
            "expected_cash": totals["expected_cash"], "counted_cash": counted,
            "variance": variance, "threshold": cfg["variance_threshold"],
            "dedupe_key": f"cash_variance:shift:{shift.id}",
        }
        db.add(FraudAlert(type="cash_variance", severity=severity,
                          detail=json.dumps(detail, default=str), status="open"))
        alerted = True

    db.commit()
    db.refresh(shift)
    write_audit(db, user, "shift.close", "cash_shift", shift.id,
                after={"expected_cash": totals["expected_cash"], "counted_cash": counted,
                       "variance": variance, "alerted": alerted},
                client="desktop", commit=True)
    if alerted:
        logger.warning(f"⚠️ CASH VARIANCE shift #{shift.id} station={shift.station_id} "
                       f"expected=₹{totals['expected_cash']} counted=₹{counted} variance=₹{variance} "
                       f"(owner alert raised)")
        # Best-effort WhatsApp to the owner (prompt 15) — must not break the close.
        try:
            from utils import whatsapp_service
            whatsapp_service.send_cash_variance_alert(db, shift, variance)
        except Exception as e:
            logger.warning(f"cash variance WhatsApp alert failed: {e}")
    return {"shift": _serialize(db, shift), "variance_flagged": alerted,
            "threshold": cfg["variance_threshold"]}


# --------------------------------------------------------------------- detail / report

@router.get("/{shift_id}", dependencies=[Depends(require_reception_or_admin)])
def shift_detail(shift_id: int, db: Session = Depends(get_db)):
    """Full shift detail: totals + expenses + the cash payments/payouts that rolled into it."""
    shift = db.query(CashShift).filter(CashShift.id == shift_id).first()
    if not shift:
        raise HTTPException(status_code=404, detail="Shift not found")
    expenses = (db.query(Expense).filter(Expense.shift_id == shift_id)
                .order_by(Expense.created_at).all())
    cash_in = (db.query(Payment).filter(Payment.method == "cash", Payment.status == "paid",
                                        Payment.shift_id == shift_id).all())
    cash_out = (db.query(Payment).filter(Payment.refund_mode == "cash",
                                         Payment.refund_status == "completed",
                                         Payment.refund_shift_id == shift_id).all())
    return {
        "shift": _serialize(db, shift),
        "denominations": json.loads(shift.denominations) if shift.denominations else None,
        "close_note": shift.close_note,
        "expenses": [{"id": e.id, "category": e.category, "description": e.description,
                      "amount": float(e.amount), "receipt_ref": e.receipt_url,
                      "created_at": e.created_at.isoformat() if e.created_at else None}
                     for e in expenses],
        "cash_payments": [{"payment_id": p.payment_id, "booking_id": p.booking_id,
                           "amount": float(p.amount),
                           "created_at": p.created_at.isoformat() if p.created_at else None}
                          for p in cash_in],
        "cash_payouts": [{"payment_id": p.payment_id, "booking_id": p.booking_id,
                          "amount": float(p.refund_amount or 0), "reason": p.refund_reason}
                         for p in cash_out],
    }


@router.get("/{shift_id}/report/pdf", dependencies=[Depends(require_reception_or_admin)])
def shift_report_pdf(shift_id: int, db: Session = Depends(get_db)):
    """Printable shift report (opening float, collections, expenses, payouts, expected vs
    counted, variance, denomination breakdown)."""
    shift = db.query(CashShift).filter(CashShift.id == shift_id).first()
    if not shift:
        raise HTTPException(status_code=404, detail="Shift not found")
    path = generate_shift_report_pdf(_report_data(db, shift))
    return FileResponse(path, media_type="application/pdf",
                        filename=f"shift_report_{shift_id}.pdf")
