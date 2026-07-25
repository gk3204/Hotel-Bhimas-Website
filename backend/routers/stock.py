"""Inventory / stock endpoints (prompt 18c, slice 8).

Stockable items (minibar drinks, toiletries, linen, supplies) with an APPEND-ONLY movement
ledger. `stock_movements` is the source of truth (never updated/deleted, same posture as
`company_ledger` / `folio_charges`); `stock_items.current_qty` is the running snapshot.

Reuse, not rebuild:
  * Receiving stock can post a petty-cash `Expense` (prompt 12) to the OPEN cash shift via
    `routers.cash_shift._open_shift`, attributed to a vendor (prompt 18b `expenses.vendor_id`).
  * Minibar CONSUMPTION is recorded from the existing `housekeeping.minibar_restock` folio path,
    which calls `record_movement(..., type='consume')` here — no second consumption path.
  * Low-stock alerts ride the EXISTING WhatsApp scheduler (`utils/stock_jobs.run_low_stock_sweep`
    registered in `utils/whatsapp_jobs._JOBS`) rather than starting a fifth APScheduler.
  * CSV/PDF export reuses `routers.reports._csv_response` / `_pdf_response`.
"""
import logging
from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from database import SessionLocal
from models import Expense, StockItem, StockMovement, Vendor
from schemas import StockAdjustRequest, StockConfigUpdate, StockItemCreate, StockItemUpdate, StockReceiveRequest
from utils import settings as app_settings
from utils.audit import _resolve_user_id, write_audit
from utils.auth_utils import require_admin, require_reception_or_admin

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/stock", tags=["Inventory"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("/health", dependencies=[Depends(require_reception_or_admin)])
def health():
    return {"status": "ok", "module": "stock"}


# ---------------------------------------------------------------- helpers

def _get_item(db: Session, item_id: int) -> StockItem:
    item = db.query(StockItem).filter(StockItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Stock item not found")
    return item


def _effective_threshold(db: Session, item: StockItem, cfg: dict | None = None) -> float:
    """An item's reorder point, falling back to the configured default when it sets none (0)."""
    own = float(item.reorder_threshold or 0)
    if own > 0:
        return own
    cfg = cfg or app_settings.get_stock_config(db)
    return float(cfg["low_stock_default_threshold"])


def is_low(db: Session, item: StockItem, cfg: dict | None = None) -> bool:
    return float(item.current_qty or 0) <= _effective_threshold(db, item, cfg)


def record_movement(db: Session, item: StockItem, mtype: str, delta_qty: float, *,
                    user=None, reason=None, unit_cost=None, folio_charge_id=None,
                    expense_id=None, booking_id=None, client_ref=None, commit=True):
    """Append a stock movement and update the item's running snapshot. THE one place stock
    quantity changes. Idempotent on `client_ref` — a repeat returns the existing movement and
    does not double-count. Callers outside this router (the minibar folio hook) use this too."""
    if client_ref:
        dup = db.query(StockMovement).filter(StockMovement.client_ref == client_ref).first()
        if dup:
            return dup
    new_qty = round(float(item.current_qty or 0) + float(delta_qty), 2)
    mv = StockMovement(
        item_id=item.id, type=mtype, delta_qty=Decimal(str(round(float(delta_qty), 2))),
        qty_after=Decimal(str(new_qty)),
        unit_cost=Decimal(str(round(float(unit_cost), 2))) if unit_cost is not None else None,
        reason=reason, folio_charge_id=folio_charge_id, expense_id=expense_id,
        booking_id=booking_id, client_ref=client_ref, created_by=_resolve_user_id(db, user),
    )
    db.add(mv)
    item.current_qty = Decimal(str(new_qty))
    # Re-arm the low-stock alert when a receive lifts the item back above its reorder point.
    if new_qty > _effective_threshold(db, item):
        item.low_stock_alerted_at = None
    if commit:
        db.commit()
        db.refresh(mv)
    else:
        db.flush()
    return mv


def _movement_dict(m: StockMovement, item_name: str | None = None) -> dict:
    return {
        "id": m.id,
        "item_id": m.item_id,
        "item_name": item_name,
        "type": m.type,
        "delta_qty": float(m.delta_qty or 0),
        "qty_after": float(m.qty_after) if m.qty_after is not None else None,
        "unit_cost": float(m.unit_cost) if m.unit_cost is not None else None,
        "reason": m.reason,
        "folio_charge_id": m.folio_charge_id,
        "expense_id": m.expense_id,
        "booking_id": m.booking_id,
        "created_at": str(m.created_at) if m.created_at else None,
    }


def _item_dict(db: Session, item: StockItem, cfg: dict | None = None,
               vendor_names: dict | None = None) -> dict:
    cfg = cfg or app_settings.get_stock_config(db)
    if vendor_names is None:
        vendor_names = {}
    return {
        "id": item.id,
        "name": item.name,
        "sku": item.sku,
        "category": item.category,
        "unit": item.unit,
        "reorder_threshold": float(item.reorder_threshold or 0),
        "effective_threshold": _effective_threshold(db, item, cfg),
        "current_qty": float(item.current_qty or 0),
        "sale_price": float(item.sale_price) if item.sale_price is not None else None,
        "gst_percent": float(item.gst_percent) if item.gst_percent is not None else None,
        "vendor_id": item.vendor_id,
        "vendor_name": vendor_names.get(item.vendor_id) if item.vendor_id else None,
        "is_active": bool(item.is_active),
        "is_low": is_low(db, item, cfg),
        "notes": item.notes,
        "created_at": str(item.created_at) if item.created_at else None,
    }


# ---------------------------------------------------------------- item CRUD

@router.get("/items")
def list_items(q: str | None = Query(None), category: str | None = Query(None),
               active: bool | None = Query(None), low_only: bool = Query(False),
               db: Session = Depends(get_db), user=Depends(require_reception_or_admin)):
    """Item list with current quantity + low-stock flag. Readable by reception (the desk needs to
    see what's in stock); mutations are admin-only."""
    query = db.query(StockItem)
    if active is not None:
        query = query.filter(StockItem.is_active == active)
    if category:
        query = query.filter(StockItem.category == category)
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(or_(StockItem.name.ilike(like), StockItem.sku.ilike(like)))
    rows = query.order_by(StockItem.name).all()
    cfg = app_settings.get_stock_config(db)
    vendor_names = {v.id: v.name for v in db.query(Vendor).all()}
    data = [_item_dict(db, it, cfg, vendor_names) for it in rows]
    if low_only:
        data = [d for d in data if d["is_low"]]
    return {"total": len(data), "low_count": sum(1 for d in data if d["is_low"]), "data": data}


@router.post("/items", dependencies=[Depends(require_admin)])
def create_item(data: StockItemCreate, db: Session = Depends(get_db), user=Depends(require_admin)):
    try:
        name = data.name.strip()
        if data.sku and db.query(StockItem).filter(StockItem.sku == data.sku.strip()).first():
            raise HTTPException(status_code=400, detail="A stock item with that SKU already exists")
        if data.vendor_id and not db.query(Vendor).filter(Vendor.id == data.vendor_id).first():
            raise HTTPException(status_code=404, detail="Vendor not found")
        payload = data.model_dump(exclude={"opening_qty"})
        payload["name"] = name
        if payload.get("sku"):
            payload["sku"] = payload["sku"].strip()
        item = StockItem(**payload, current_qty=0, created_by=_resolve_user_id(db, user))
        db.add(item)
        db.flush()
        # Seed the opening stock as the first movement so the ledger explains the whole quantity.
        if data.opening_qty and data.opening_qty > 0:
            record_movement(db, item, "adjust", data.opening_qty, user=user,
                            reason="Opening stock", commit=False)
        db.commit()
        db.refresh(item)
        write_audit(db, user, "stock.item_create", "stock_item", item.id,
                    after={"name": item.name, "category": item.category,
                           "opening_qty": data.opening_qty}, client="web", commit=True)
        return _item_dict(db, item)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"create_item failed: {e}", exc_info=True)
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to create the stock item")


@router.get("/low-stock")
def low_stock(db: Session = Depends(get_db), user=Depends(require_reception_or_admin)):
    """Active items at or below their reorder point — the "reorder now" list. Declared before
    /items/{id} so the literal path wins the route match (it doesn't collide, but keep the idiom)."""
    cfg = app_settings.get_stock_config(db)
    vendor_names = {v.id: v.name for v in db.query(Vendor).all()}
    rows = db.query(StockItem).filter(StockItem.is_active == True).order_by(StockItem.name).all()  # noqa: E712
    data = [_item_dict(db, it, cfg, vendor_names) for it in rows if is_low(db, it, cfg)]
    return {"total": len(data), "data": data}


@router.get("/movements")
def list_movements(item_id: int | None = Query(None), type: str | None = Query(None),
                   limit: int = Query(200, ge=1, le=1000), db: Session = Depends(get_db),
                   user=Depends(require_reception_or_admin)):
    query = db.query(StockMovement)
    if item_id:
        query = query.filter(StockMovement.item_id == item_id)
    if type:
        query = query.filter(StockMovement.type == type)
    rows = query.order_by(StockMovement.id.desc()).limit(limit).all()
    names = {i.id: i.name for i in db.query(StockItem).all()}
    return {"total": len(rows),
            "data": [_movement_dict(m, names.get(m.item_id)) for m in rows]}


@router.get("/report")
def stock_report(format: str = Query("json"), db: Session = Depends(get_db),
                 user=Depends(require_admin)):
    """Current stock valuation report (json | csv | pdf). Value = current_qty × sale_price."""
    from routers.reports import _export_or_json, _meta
    cfg = app_settings.get_stock_config(db)
    items = db.query(StockItem).filter(StockItem.is_active == True).order_by(StockItem.name).all()  # noqa: E712
    rows, value_total = [], 0.0
    for it in items:
        qty = float(it.current_qty or 0)
        price = float(it.sale_price) if it.sale_price is not None else 0.0
        value = round(qty * price, 2)
        value_total += value
        rows.append([it.name, it.category, it.unit, qty, _effective_threshold(db, it, cfg),
                     "LOW" if is_low(db, it, cfg) else "", price, value])
    columns = ["Item", "Category", "Unit", "Qty", "Reorder", "Status", "Sale ₹", "Value ₹"]
    totals = ["", "", "", "", "", "", "Total", round(value_total, 2)]
    data = {"generated": datetime.utcnow().isoformat(), "item_count": len(rows),
            "value_total": round(value_total, 2),
            "items": [dict(zip(["name", "category", "unit", "qty", "reorder", "status",
                                "sale_price", "value"], r)) for r in rows]}
    return _export_or_json(format, data, title="Stock Valuation", columns=columns, rows=rows,
                           totals_row=totals, meta=_meta(), filename="stock_valuation")


@router.get("/items/{item_id}")
def get_item(item_id: int, db: Session = Depends(get_db), user=Depends(require_reception_or_admin)):
    item = _get_item(db, item_id)
    recent = db.query(StockMovement).filter(StockMovement.item_id == item.id).order_by(
        StockMovement.id.desc()).limit(30).all()
    return {**_item_dict(db, item),
            "recent_movements": [_movement_dict(m) for m in recent]}


@router.put("/items/{item_id}", dependencies=[Depends(require_admin)])
def update_item(item_id: int, data: StockItemUpdate, db: Session = Depends(get_db),
                user=Depends(require_admin)):
    try:
        item = _get_item(db, item_id)
        before = {"name": item.name, "category": item.category,
                  "reorder_threshold": float(item.reorder_threshold or 0),
                  "is_active": bool(item.is_active)}
        changes = data.model_dump(exclude_unset=True)
        if changes.get("sku"):
            sku = changes["sku"].strip()
            if db.query(StockItem).filter(StockItem.sku == sku, StockItem.id != item.id).first():
                raise HTTPException(status_code=400, detail="A stock item with that SKU already exists")
            changes["sku"] = sku
        if changes.get("name"):
            changes["name"] = changes["name"].strip()
        if changes.get("vendor_id") and not db.query(Vendor).filter(
                Vendor.id == changes["vendor_id"]).first():
            raise HTTPException(status_code=404, detail="Vendor not found")
        for key, value in changes.items():
            setattr(item, key, value)
        item.updated_by = _resolve_user_id(db, user)
        item.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(item)
        write_audit(db, user, "stock.item_update", "stock_item", item.id,
                    before=before, after=changes, client="web", commit=True)
        return _item_dict(db, item)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"update_item failed: {e}", exc_info=True)
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to update the stock item")


@router.delete("/items/{item_id}", dependencies=[Depends(require_admin)])
def deactivate_item(item_id: int, db: Session = Depends(get_db), user=Depends(require_admin)):
    """Deactivate, never hard-delete — past movements reference the item."""
    try:
        item = _get_item(db, item_id)
        item.is_active = False
        item.updated_by = _resolve_user_id(db, user)
        item.updated_at = datetime.utcnow()
        db.commit()
        write_audit(db, user, "stock.item_deactivate", "stock_item", item.id,
                    after={"name": item.name}, client="web", commit=True)
        return {"status": "deactivated", "item_id": item.id, "name": item.name}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"deactivate_item failed: {e}", exc_info=True)
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to deactivate the stock item")


# ---------------------------------------------------------------- movements

@router.post("/items/{item_id}/receive", dependencies=[Depends(require_admin)])
def receive_stock(item_id: int, data: StockReceiveRequest, db: Session = Depends(get_db),
                  user=Depends(require_admin)):
    """Receive stock in (delta > 0). Optionally posts a petty-cash Expense to the OPEN cash shift,
    attributed to a vendor, so the purchase hits the shift ledger / P&L."""
    try:
        item = _get_item(db, item_id)
        if data.client_ref:
            dup = db.query(StockMovement).filter(StockMovement.client_ref == data.client_ref).first()
            if dup:
                return {**_item_dict(db, item), "duplicate": True, "movement_id": dup.id}

        expense_id = None
        if data.post_expense and data.unit_cost:
            from routers.cash_shift import _open_shift
            vendor_id = data.vendor_id or item.vendor_id
            shift = _open_shift(db, None)
            exp = Expense(
                shift_id=shift.id if shift else None,
                category="supplies",
                description=f"Stock: {item.name} × {data.qty} {item.unit}",
                amount=Decimal(str(round(data.qty * data.unit_cost, 2))),
                client_ref=f"stock_recv:{data.client_ref}" if data.client_ref else None,
                vendor_id=vendor_id,
                created_by=_resolve_user_id(db, user),
            )
            db.add(exp)
            db.flush()
            expense_id = exp.id

        mv = record_movement(db, item, "receive", data.qty, user=user,
                             reason=data.reason or "Stock received", unit_cost=data.unit_cost,
                             expense_id=expense_id, client_ref=data.client_ref, commit=False)
        db.commit()
        db.refresh(item)
        write_audit(db, user, "stock.receive", "stock_item", item.id,
                    after={"qty": data.qty, "unit_cost": data.unit_cost,
                           "expense_id": expense_id, "qty_after": float(item.current_qty or 0)},
                    client="web", commit=True)
        return {**_item_dict(db, item), "duplicate": False, "movement_id": mv.id,
                "expense_id": expense_id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"receive_stock failed: {e}", exc_info=True)
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to receive stock")


@router.post("/items/{item_id}/adjust", dependencies=[Depends(require_admin)])
def adjust_stock(item_id: int, data: StockAdjustRequest, db: Session = Depends(get_db),
                 user=Depends(require_admin)):
    """Correct stock (a stock-count fix, signed delta) or write off wastage (delta < 0).
    Reason is mandatory; the movement is append-only and audited."""
    try:
        item = _get_item(db, item_id)
        if data.client_ref:
            dup = db.query(StockMovement).filter(StockMovement.client_ref == data.client_ref).first()
            if dup:
                return {**_item_dict(db, item), "duplicate": True, "movement_id": dup.id}
        if data.type == "wastage" and data.delta_qty > 0:
            raise HTTPException(status_code=400, detail="Wastage must reduce stock (delta < 0)")
        new_qty = float(item.current_qty or 0) + data.delta_qty
        if new_qty < 0:
            raise HTTPException(status_code=400,
                                detail="Adjustment would drive stock below zero")
        mv = record_movement(db, item, data.type, data.delta_qty, user=user,
                             reason=data.reason, client_ref=data.client_ref, commit=False)
        db.commit()
        db.refresh(item)
        write_audit(db, user, f"stock.{data.type}", "stock_item", item.id,
                    after={"delta_qty": data.delta_qty, "reason": data.reason,
                           "qty_after": float(item.current_qty or 0)}, client="web", commit=True)
        return {**_item_dict(db, item), "duplicate": False, "movement_id": mv.id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"adjust_stock failed: {e}", exc_info=True)
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to adjust stock")


# ---------------------------------------------------------------- alerts + config

@router.post("/alerts/run", dependencies=[Depends(require_admin)])
def run_alerts(db: Session = Depends(get_db), user=Depends(require_admin)):
    """Fire the low-stock sweep now instead of waiting for the scheduled run. The SAME function
    the WhatsApp scheduler calls, so manual and automatic runs never drift."""
    from utils.stock_jobs import run_low_stock_sweep
    result = run_low_stock_sweep(db)
    write_audit(db, user, "stock.alerts_run", "stock_item", None,
                after=result, client="web", commit=True)
    return result


@router.get("/config")
def get_config(db: Session = Depends(get_db), user=Depends(require_reception_or_admin)):
    return app_settings.get_stock_config(db)


@router.put("/config", dependencies=[Depends(require_admin)])
def update_config(data: StockConfigUpdate, db: Session = Depends(get_db), user=Depends(require_admin)):
    changes = data.model_dump(exclude_unset=True)
    applied = {}
    for key, value in changes.items():
        if key in app_settings.STOCK_EDITABLE_KEYS:
            app_settings.set_setting(db, key, value, user=user)
            applied[key] = value
    db.commit()
    write_audit(db, user, "stock.config_update", "app_settings", None,
                after=applied, client="web", commit=True)
    return app_settings.get_stock_config(db)
