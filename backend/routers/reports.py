"""Reports & Dashboard (prompt 16) — read-only operational + financial visibility for the owner.

Pure read models over the shared DB (bookings, folios/charges, invoices, payments, cash shifts,
cards, fraud alerts, agents). Nothing here mutates business data except the night-audit trigger,
which delegates to utils/night_audit.py. Admin-only (require_admin) — this is the owner's cockpit.

Design notes (locked):
- Revenue is recognised on the date a FolioCharge is POSTED (`posted_at`), non-void. Folios always
  exist for a stay (opened at check-in), so this is robust and independent of whether an invoice was
  cut. GST is split from the GST-inclusive `amount` per slab exactly like folio._invoice_totals.
- Collections are a separate cash-flow view sourced from `Payment` (status='paid') by method.
- The query helpers (occupancy_data, sales_by_date, gst_data, collections_summary, ...) are module
  level so utils/night_audit.py reuses them for the day-close snapshot.
- Each endpoint accepts ?format=json|csv|pdf (default json). csv -> StreamingResponse, pdf -> a
  generic branded table via utils.pdf_generator.generate_report_pdf.
- OTA commission/payout reconciliation needs the prompt-17 channel model; until then the OTA report
  is revenue-by-source with recon_pending=true.
"""
import csv
import io
import logging
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from database import SessionLocal
from models import (Booking, BookingItem, CardIssuance, CashShift, FolioCharge,
                    FraudAlert, Guest, GuestRequest, MaintenanceTicket, MenuItem,
                    Payment, Room, RoomType, User, DayCloseSummary, PAYMENT_METHODS)
from schemas import OverstayConfigUpdate, ReportsConfigUpdate
from utils.audit import write_audit
from utils.auth_utils import require_admin
from utils.settings import get_reports_config
from services import ota_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/reports", tags=["Reports"])

# Booking statuses that count as a real (non-cancelled) stay.
_LIVE_STATUSES = ("confirmed", "checked_in", "checked_out")
# OTA channels: services.ota_service is the single authority (admin-editable `ota_source`
# family since v3). This module used to keep its own copy of the tuple, which could silently
# disagree with the one the commission maths uses — re-exported here only for back-compat.
OTA_SOURCES = ota_service.OTA_SOURCES


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ============================================================
# shared helpers
# ============================================================

def _parse_date(s, field):
    if not s:
        return None
    try:
        return date.fromisoformat(s)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid {field}. Use YYYY-MM-DD")


def _range(from_, to, default_days=30):
    """Resolve a (dfrom, dto) inclusive date range. Missing both -> last `default_days`.
    dfrom > dto -> 400."""
    dfrom = _parse_date(from_, "from")
    dto = _parse_date(to, "to")
    if dto is None:
        dto = date.today()
    if dfrom is None:
        dfrom = dto - timedelta(days=default_days - 1)
    if dfrom > dto:
        raise HTTPException(status_code=400, detail="'from' must be on or before 'to'")
    return dfrom, dto


def _days(dfrom, dto):
    d = dfrom
    while d <= dto:
        yield d
        d += timedelta(days=1)


def _gst_split(pairs):
    """Split a list of (gst_percent, gross_inclusive_amount) into taxable/cgst/sgst per slab.
    Mirrors folio._invoice_totals: taxable = gross/(1+g/100); cgst = sgst = gst/2 (cgst absorbs
    the odd paisa). Returns (rows_by_slab, taxable_total, cgst_total, sgst_total, gross_total)."""
    slabs = {}
    for g, amt in pairs:
        g = float(g or 0)
        slabs[g] = round(slabs.get(g, 0.0) + float(amt or 0), 2)
    rows = []
    taxable_total = cgst_total = sgst_total = gross_total = 0.0
    for g in sorted(slabs):
        gross = slabs[g]
        taxable = round(gross / (1 + g / 100), 2)
        gst = round(gross - taxable, 2)
        sgst = round(gst / 2, 2)
        cgst = round(gst - sgst, 2)
        rows.append({"gst_percent": g, "taxable": taxable, "cgst": cgst, "sgst": sgst, "gross": gross})
        taxable_total = round(taxable_total + taxable, 2)
        cgst_total = round(cgst_total + cgst, 2)
        sgst_total = round(sgst_total + sgst, 2)
        gross_total = round(gross_total + gross, 2)
    return rows, taxable_total, cgst_total, sgst_total, gross_total


def _active_room_count(db):
    return int(db.query(func.count(Room.room_id)).filter(Room.is_active == True).scalar() or 0)  # noqa: E712


def _user_name(db, uid):
    if not uid:
        return None
    u = db.query(User).filter(User.user_id == uid).first()
    return (u.full_name or u.username) if u else None


# ============================================================
# query helpers (importable — reused by utils/night_audit.py)
# ============================================================

def occupancy_data(db, dfrom, dto, group_by="day"):
    """Occupancy by night. group_by='day' -> per-date occupied/available/%; 'room_type' ->
    per-type room-nights sold vs capacity over the range. Occupied rooms on a night = sum of
    booked-item quantities whose stay overlaps that night (check_in <= night < check_out)."""
    total_rooms = _active_room_count(db)
    if group_by == "room_type":
        num_nights = (dto - dfrom).days + 1
        rows = []
        for rt in db.query(RoomType).filter(RoomType.is_active == True).order_by(RoomType.name).all():  # noqa: E712
            capacity = int(rt.total_rooms or 0) * num_nights
            sold = 0
            for night in _days(dfrom, dto):
                q = (db.query(func.coalesce(func.sum(BookingItem.quantity), 0))
                     .join(Booking, Booking.booking_id == BookingItem.booking_id)
                     .filter(BookingItem.room_type_id == rt.room_type_id,
                             Booking.status.in_(_LIVE_STATUSES),
                             Booking.check_in <= night, Booking.check_out > night))
                sold += int(q.scalar() or 0)
            pct = round(100.0 * sold / capacity, 1) if capacity else 0.0
            rows.append({"room_type": rt.name, "capacity_room_nights": capacity,
                         "room_nights_sold": sold, "occupancy_pct": pct})
        return {"group_by": "room_type", "from": str(dfrom), "to": str(dto),
                "total_rooms": total_rooms, "rows": rows}
    # per-day
    rows = []
    for night in _days(dfrom, dto):
        occ = (db.query(func.coalesce(func.sum(BookingItem.quantity), 0))
               .join(Booking, Booking.booking_id == BookingItem.booking_id)
               .filter(Booking.status.in_(_LIVE_STATUSES),
                       Booking.check_in <= night, Booking.check_out > night)).scalar()
        occ = int(occ or 0)
        pct = round(100.0 * occ / total_rooms, 1) if total_rooms else 0.0
        rows.append({"date": str(night), "occupied": occ, "available": max(0, total_rooms - occ),
                     "total_rooms": total_rooms, "occupancy_pct": pct})
    return {"group_by": "day", "from": str(dfrom), "to": str(dto),
            "total_rooms": total_rooms, "rows": rows}


def _charge_pairs(db, day):
    """(gst_percent, amount) for every non-void, non-payment, non-discount FolioCharge posted on
    `day` (the taxable sale lines)."""
    q = (db.query(FolioCharge.gst_percent, FolioCharge.amount)
         .filter(FolioCharge.void == False,  # noqa: E712
                 FolioCharge.type.notin_(("payment", "discount")),
                 func.date(FolioCharge.posted_at) == day))
    return [(g, a) for g, a in q.all()]


def _sum_charges(db, day, types=None, exclude=None):
    q = (db.query(func.coalesce(func.sum(FolioCharge.amount), 0))
         .filter(FolioCharge.void == False,  # noqa: E712
                 func.date(FolioCharge.posted_at) == day))
    if types is not None:
        q = q.filter(FolioCharge.type.in_(types))
    if exclude is not None:
        q = q.filter(FolioCharge.type.notin_(exclude))
    return float(q.scalar() or 0)


def _room_service_filters(day=None, dfrom=None, dto=None):
    """The room-service revenue predicate (v3 item 4): non-void folio lines carrying a menu
    link. `menu_item_id` is written by services/room_service.post_to_folio and nowhere else,
    so this is exactly the room-service sales — no description matching, and a voided line
    drops out on its own."""
    conds = [FolioCharge.void == False,          # noqa: E712
             FolioCharge.menu_item_id.isnot(None)]
    if day is not None:
        conds.append(func.date(FolioCharge.posted_at) == day)
    if dfrom is not None:
        conds.append(func.date(FolioCharge.posted_at) >= dfrom)
    if dto is not None:
        conds.append(func.date(FolioCharge.posted_at) <= dto)
    return conds


def _room_service_revenue(db, day) -> float:
    return float(db.query(func.coalesce(func.sum(FolioCharge.amount), 0))
                 .filter(*_room_service_filters(day=day)).scalar() or 0)


def sales_by_date(db, dfrom, dto):
    """Per-day recognised sales from non-void folio charges posted that day: room revenue,
    other revenue (food/minibar/laundry/extra_bed/misc), discounts (negative), gross, and the
    GST split. Returns {rows, totals}.

    `room_service` (v3 item 4) is the slice of `other_revenue` that came from the room-service
    menu — it is INCLUDED in other_revenue, not additional to it, so do not add the two.
    """
    rows = []
    for day in _days(dfrom, dto):
        room_rev = round(_sum_charges(db, day, types=("room",)), 2)
        other_rev = round(_sum_charges(db, day, exclude=("room", "payment", "discount")), 2)
        rs_rev = round(_room_service_revenue(db, day), 2)
        discount = round(_sum_charges(db, day, types=("discount",)), 2)  # negative
        _rows, taxable, cgst, sgst, gross = _gst_split(_charge_pairs(db, day))
        rows.append({"date": str(day), "room_revenue": room_rev, "other_revenue": other_rev,
                     "room_service": rs_rev,
                     "discount": discount, "gross_sales": gross, "taxable": taxable,
                     "cgst": cgst, "sgst": sgst,
                     # v4b6 — see comp_room_value() below. NOT part of gross_sales.
                     "comp_room_value": round(comp_room_value(db, day), 2)})
    totals = {
        "room_revenue": round(sum(r["room_revenue"] for r in rows), 2),
        "other_revenue": round(sum(r["other_revenue"] for r in rows), 2),
        "room_service": round(sum(r["room_service"] for r in rows), 2),
        "discount": round(sum(r["discount"] for r in rows), 2),
        "gross_sales": round(sum(r["gross_sales"] for r in rows), 2),
        "taxable": round(sum(r["taxable"] for r in rows), 2),
        "cgst": round(sum(r["cgst"] for r in rows), 2),
        "sgst": round(sum(r["sgst"] for r in rows), 2),
        "comp_room_value": round(sum(r["comp_room_value"] for r in rows), 2),
    }
    return {"from": str(dfrom), "to": str(dto), "rows": rows, "totals": totals}


def comp_room_value(db, day) -> float:
    """What was GIVEN AWAY on this business date (v4b6).

    A complimentary stay posts no charges at all — suppression, not a 100% discount, because
    posting a taxable line and discounting it away would create an output-tax liability on a
    supply with no consideration. That keeps every GST figure honest, but it means the revenue
    simply is not there, so ADR and RevPAR understate.

    ⚠️ This number is reported ALONGSIDE the day's revenue and is never added into
    `gross_sales`, `taxable` or the tax split. It exists purely to answer "how much did we comp
    this month, and to whom?" — otherwise suppression is a blind spot.

    Valued from `Booking.grand_total` (which a comp deliberately does not zero) pro-rated per
    night across the stay, so a 3-night comp shows a third on each day rather than a lump.
    """
    total = 0.0
    rows = (db.query(Booking)
              .filter(Booking.comp_mode.in_(["all", "room"]),
                      Booking.status.in_(["confirmed", "checked_in", "checked_out"]),
                      Booking.check_in <= day, Booking.check_out > day)
              .all())
    for b in rows:
        nights = max(1, (b.check_out - b.check_in).days)
        total += float(b.grand_total or 0) / nights
    return total


def collections_summary(db, dfrom, dto):
    """Cash-flow view from Payment (status='paid') by method over [dfrom, dto], plus refunds."""
    def _m(method):
        return float(db.query(func.coalesce(func.sum(Payment.amount), 0)).filter(
            Payment.status == "paid", Payment.method == method,
            func.date(Payment.created_at) >= dfrom, func.date(Payment.created_at) <= dto).scalar() or 0)
    cash, card, upi, bank = _m("cash"), _m("card"), _m("upi"), _m("bank")
    total = round(cash + card + upi + bank, 2)
    refunds = float(db.query(func.coalesce(func.sum(Payment.refund_amount), 0)).filter(
        Payment.refund_status == "completed",
        func.date(Payment.created_at) >= dfrom, func.date(Payment.created_at) <= dto).scalar() or 0)
    return {"cash": round(cash, 2), "card": round(card, 2), "upi": round(upi, 2),
            "bank": round(bank, 2), "total_collected": total, "refunds": round(refunds, 2)}


def payments_by_day(db, dfrom, dto):
    """Day-wise collections by method (+ refunds, net) over [dfrom, dto] (ALT-4). Reuses
    collections_summary per day so the day rows always reconcile with the range totals."""
    rows = []
    for day in _days(dfrom, dto):
        c = collections_summary(db, day, day)
        rows.append({
            "date": str(day), "cash": c["cash"], "card": c["card"], "upi": c["upi"],
            "bank": c["bank"], "collected": c["total_collected"], "refunds": c["refunds"],
            "net": round(c["total_collected"] - c["refunds"], 2),
        })
    keys = ("cash", "card", "upi", "bank", "collected", "refunds", "net")
    totals = {k: round(sum(r[k] for r in rows), 2) for k in keys}
    return {"from": str(dfrom), "to": str(dto), "rows": rows, "totals": totals}


# =====================================================================
# ROOM-SERVICE SALES (v3 item 4)
# Both reports read FolioCharge, not the order payload, so they follow the same revenue-
# recognition rule as every other report here (posted_at, non-void) and reconcile against
# sales/daily's `room_service` column. An order that was placed but never delivered has no
# folio line and correctly contributes nothing.
# =====================================================================

def room_service_by_date(db, dfrom, dto):
    """Day-wise room-service sales: orders, items sold, gross, GST split, net."""
    rows = []
    for day in _days(dfrom, dto):
        pairs = [(g, a) for g, a in
                 db.query(FolioCharge.gst_percent, FolioCharge.amount)
                 .filter(*_room_service_filters(day=day)).all()]
        _r, taxable, cgst, sgst, gross = _gst_split(pairs)
        items = float(db.query(func.coalesce(func.sum(FolioCharge.qty), 0))
                      .filter(*_room_service_filters(day=day)).scalar() or 0)
        # An order posts all its lines in one transaction and stores its FIRST charge id, so
        # counting requests whose anchor charge landed today counts orders, not lines.
        # The void filter is deliberately NOT applied here: an order that was delivered and
        # then had a line voided still happened, and coupling the two would report "0 orders,
        # Rs 120 of sales" whenever the voided line happened to be the first one. A wholly
        # voided order therefore shows as 1 order with 0 gross — which is worth seeing.
        day_charges = (db.query(FolioCharge.id)
                       .filter(FolioCharge.menu_item_id.isnot(None),
                               func.date(FolioCharge.posted_at) == day).subquery())
        orders = int(db.query(func.count(GuestRequest.id))
                     .filter(GuestRequest.folio_charge_id.in_(db.query(day_charges.c.id)))
                     .scalar() or 0)
        rows.append({"date": str(day), "orders": orders, "items": round(items, 2),
                     "gross": gross, "taxable": taxable, "cgst": cgst, "sgst": sgst,
                     "net": taxable})
    keys = ("orders", "items", "gross", "taxable", "cgst", "sgst", "net")
    totals = {k: round(sum(r[k] for r in rows), 2) for k in keys}
    totals["orders"] = int(totals["orders"])
    return {"from": str(dfrom), "to": str(dto), "rows": rows, "totals": totals}


def room_service_by_item(db, dfrom, dto):
    """Product-wise room-service sales: what actually sells, best first.

    Grouped on the menu item itself, so renaming a dish keeps its history together. Items
    deleted from the menu keep their sales (the FK is retained); a charge posted before
    migration 027 has no link and is absent — run scripts/backfill_menu_item_id.py for those.
    """
    q = (db.query(MenuItem.id, MenuItem.name, MenuItem.category,
                  func.coalesce(func.sum(FolioCharge.qty), 0),
                  func.coalesce(func.sum(FolioCharge.amount), 0))
         .join(FolioCharge, FolioCharge.menu_item_id == MenuItem.id)
         .filter(*_room_service_filters(dfrom=dfrom, dto=dto))
         .group_by(MenuItem.id, MenuItem.name, MenuItem.category)
         .order_by(func.coalesce(func.sum(FolioCharge.amount), 0).desc()))
    raw = q.all()
    gross_total = round(sum(float(g or 0) for _i, _n, _c, _q, g in raw), 2)
    rows = []
    for item_id, name, category, qty, gross in raw:
        qty = round(float(qty or 0), 2)
        gross = round(float(gross or 0), 2)
        rows.append({
            "menu_item_id": item_id, "item": name, "category": category,
            "qty": qty, "gross": gross,
            "avg_price": round(gross / qty, 2) if qty else 0.0,
            "share_percent": round(gross / gross_total * 100, 2) if gross_total else 0.0,
        })
    totals = {"qty": round(sum(r["qty"] for r in rows), 2), "gross": gross_total,
              "share_percent": 100.0 if rows else 0.0, "items": len(rows)}
    return {"from": str(dfrom), "to": str(dto), "rows": rows, "totals": totals}


# =====================================================================
# SHIFT-WISE PAYMENT SPLIT (v3 item 6)
# =====================================================================

def shift_payments_data(db, dfrom, dto):
    """Per-shift collections split by payment method, alongside the drawer reconciliation.

    Cash appears in BOTH halves and means different things: under `cash` it is money taken by
    that method; under `counted_cash`/`variance` it is what was physically in the drawer. The
    split comes from routers/cash_shift._totals_by_method, so this report and the printed
    shift receipt can never disagree.
    """
    from routers.cash_shift import _serialize
    shifts = (db.query(CashShift)
              .filter(func.date(CashShift.opened_at) >= dfrom,
                      func.date(CashShift.opened_at) <= dto)
              .order_by(CashShift.opened_at, CashShift.id).all())
    rows = []
    for s in shifts:
        d = _serialize(db, s)
        bm = d.get("by_method") or {}
        rows.append({
            "shift_id": d["id"], "station_id": d["station_id"], "status": d["status"],
            "staff_name": d["staff_name"],
            "opened_at": d["opened_at"], "closed_at": d["closed_at"],
            **{m: bm.get(m, 0.0) for m in PAYMENT_METHODS},
            "total_collected": bm.get("total_collected", 0.0),
            "refunds": bm.get("refunds", 0.0),
            "net_collected": bm.get("net_collected", 0.0),
            "counted_cash": d["counted_cash"],
            "variance": d["variance"],
        })
    money_keys = tuple(PAYMENT_METHODS) + ("total_collected", "refunds", "net_collected")
    totals = {k: round(sum(float(r[k] or 0) for r in rows), 2) for k in money_keys}
    totals["variance"] = round(sum(float(r["variance"] or 0) for r in rows), 2)
    totals["shifts"] = len(rows)
    return {"from": str(dfrom), "to": str(dto), "rows": rows, "totals": totals}


def gst_data(db, dfrom, dto):
    """GST filing report: taxable value + CGST/SGST per slab over non-void sale charges posted
    in the range."""
    pairs = []
    for day in _days(dfrom, dto):
        pairs.extend(_charge_pairs(db, day))
    slab_rows, taxable, cgst, sgst, gross = _gst_split(pairs)
    return {"from": str(dfrom), "to": str(dto), "slabs": slab_rows,
            "taxable_total": taxable, "cgst_total": cgst, "sgst_total": sgst,
            "gross_total": gross}


def arrivals_departures_data(db, dfrom, dto):
    rows = []
    for day in _days(dfrom, dto):
        arr = int(db.query(func.count(Booking.booking_id)).filter(
            Booking.check_in == day, Booking.status.in_(_LIVE_STATUSES)).scalar() or 0)
        dep = int(db.query(func.count(Booking.booking_id)).filter(
            Booking.check_out == day, Booking.status.in_(_LIVE_STATUSES)).scalar() or 0)
        rows.append({"date": str(day), "arrivals": arr, "departures": dep})
    totals = {"arrivals": sum(r["arrivals"] for r in rows),
              "departures": sum(r["departures"] for r in rows)}
    return {"from": str(dfrom), "to": str(dto), "rows": rows, "totals": totals}


def in_house_data(db):
    """Current in-house (checked_in) bookings: guest, rooms, nights, folio balance."""
    from models import Folio
    rows = []
    bookings = (db.query(Booking).filter(Booking.status == "checked_in")
                .order_by(Booking.check_out).all())
    for b in bookings:
        guest = db.query(Guest).filter(Guest.guest_id == b.guest_id).first()
        items = db.query(BookingItem).filter(BookingItem.booking_id == b.booking_id).all()
        room_labels = []
        for it in items:
            if it.room_id:
                r = db.query(Room).filter(Room.room_id == it.room_id).first()
                if r:
                    room_labels.append(r.room_number)
        folio = db.query(Folio).filter(Folio.booking_id == b.booking_id).first()
        nights = max(1, (b.check_out - b.check_in).days)
        rows.append({
            "booking_id": b.booking_id,
            "guest_name": guest.name if guest else None,
            "phone": guest.phone if guest else None,
            "rooms": ", ".join(room_labels) if room_labels else "—",
            "check_in": str(b.check_in), "check_out": str(b.check_out), "nights": nights,
            "balance": float(folio.balance or 0) if folio else None,
        })
    return {"count": len(rows), "rows": rows}


def travel_agents_data(db, dfrom, dto):
    """Agent-wise revenue + commission settlement — reuses routers.agents._agent_totals so the
    figures match the settlement screen exactly."""
    from routers.agents import _agent_totals
    from models import TravelAgent
    rows = []
    for a in db.query(TravelAgent).order_by(TravelAgent.name).all():
        count, revenue, accrued, paid = _agent_totals(db, a.id, dfrom, dto)
        rows.append({"agent_id": a.id, "agent_name": a.name,
                     "commission_percent": float(a.commission_percent or 0),
                     "bookings": count, "room_revenue": round(revenue, 2),
                     "commission_accrued": round(accrued, 2), "commission_paid": round(paid, 2),
                     "outstanding": round(accrued - paid, 2)})
    totals = {k: round(sum(r[k] for r in rows), 2)
              for k in ("room_revenue", "commission_accrued", "commission_paid", "outstanding")}
    totals["bookings"] = sum(r["bookings"] for r in rows)
    return {"from": str(dfrom), "to": str(dto), "rows": rows, "totals": totals}


def ota_data(db, dfrom, dto):
    """OTA-wise revenue grouped by booking_source (prompt 17). Now that OTA bookings carry a
    commission % + net-payout snapshot (routers/ota.py, services/ota_service.py), commission and
    expected payout are real, not estimated. An untracked OTA booking (no net-payout snapshot) is
    treated as a full payout (0 commission). Settlement matching (expected vs actual bank payouts)
    lives at GET /ota/reconciliation."""
    rows = []
    for src in ota_service.ota_sources(db):
        # v4b2: commission is owed on what the CHANNEL SOLD, which stopped being
        # total_amount the moment a stay could be extended at the desk. Extra nights sold
        # face-to-face are a DIRECT hotel sale — the guest pays us, the OTA is owed nothing.
        # Deriving commission_est from revenue would have reported the entire extension as
        # commission payable. prepaid_amount is the OTA gross and an extension never touches
        # it; NULLIF(...,0) makes pre-028 rows fall back to total_amount, so historic figures
        # are unchanged. room_revenue stays the REAL revenue, extension included.
        ota_basis = func.coalesce(func.nullif(Booking.prepaid_amount, 0), Booking.total_amount)
        q = db.query(
            func.count(Booking.booking_id),
            func.coalesce(func.sum(Booking.total_amount), 0),
            func.coalesce(func.sum(func.coalesce(Booking.ota_net_payout, Booking.total_amount)), 0),
            func.coalesce(func.sum(ota_basis), 0),
        ).filter(Booking.booking_source == src, Booking.status.in_(_LIVE_STATUSES),
                 Booking.check_in >= dfrom, Booking.check_in <= dto)
        count, revenue, net_payout, sold_via_ota = q.one()
        revenue = round(float(revenue or 0), 2)
        net_payout = round(float(net_payout or 0), 2)
        sold_via_ota = round(float(sold_via_ota or 0), 2)
        rows.append({"ota": src, "bookings": int(count), "room_revenue": revenue,
                     # what the channel sold, vs what the stay ended up being worth
                     "sold_via_ota": sold_via_ota,
                     "commission_est": round(max(0.0, sold_via_ota - net_payout), 2),
                     "net_payout": net_payout})
    totals = {"bookings": sum(r["bookings"] for r in rows),
              "room_revenue": round(sum(r["room_revenue"] for r in rows), 2),
              "sold_via_ota": round(sum(r["sold_via_ota"] for r in rows), 2),
              "commission_est": round(sum(r["commission_est"] for r in rows), 2),
              "net_payout": round(sum(r["net_payout"] for r in rows), 2)}
    return {"from": str(dfrom), "to": str(dto), "rows": rows, "totals": totals,
            "recon_pending": False,
            "note": "Commission + net payout from the OTA snapshot. Settlement reconciliation: GET /ota/reconciliation."}


def cash_shift_data(db, dfrom, dto):
    """Shift/day reconciliation rows for shifts OPENED in the range (open + closed)."""
    from routers.cash_shift import _serialize
    shifts = (db.query(CashShift)
              .filter(func.date(CashShift.opened_at) >= dfrom, func.date(CashShift.opened_at) <= dto)
              .order_by(CashShift.opened_at.desc()).all())
    rows = []
    for s in shifts:
        d = _serialize(db, s)
        rows.append({"shift_id": d["id"], "station_id": d["station_id"], "status": d["status"],
                     "staff_name": d["staff_name"], "opened_at": d["opened_at"], "closed_at": d["closed_at"],
                     "opening_balance": d["opening_balance"], "collections_cash": d["collections_cash"],
                     "expenses_total": d["expenses_total"], "payouts_total": d["payouts_total"],
                     "expected_cash": d["expected_cash"], "counted_cash": d["counted_cash"],
                     "variance": d["variance"]})
    totals = {k: round(sum((r[k] or 0) for r in rows), 2)
              for k in ("opening_balance", "collections_cash", "expenses_total", "payouts_total",
                        "expected_cash")}
    totals["variance"] = round(sum((r["variance"] or 0) for r in rows), 2)
    return {"from": str(dfrom), "to": str(dto), "rows": rows, "totals": totals}


def card_audit_data(db, dfrom, dto):
    """Card-issuance audit: every key card cut in the range."""
    q = (db.query(CardIssuance)
         .filter(func.date(CardIssuance.issued_at) >= dfrom, func.date(CardIssuance.issued_at) <= dto)
         .order_by(CardIssuance.issued_at.desc()))
    rows = []
    for c in q.all():
        room = db.query(Room).filter(Room.room_id == c.room_id).first() if c.room_id else None
        rows.append({"card_id": c.id, "booking_id": c.booking_id,
                     "room": room.room_number if room else None,
                     "card_uid": c.card_uid, "card_type": c.card_type, "issue_type": c.issue_type,
                     "status": c.status, "issued_by": _user_name(db, c.issued_by),
                     "station_id": c.station_id,
                     "issued_at": c.issued_at.isoformat() if c.issued_at else None,
                     "valid_to": c.valid_to.isoformat() if c.valid_to else None})
    by_type = {}
    for r in rows:
        by_type[r["card_type"]] = by_type.get(r["card_type"], 0) + 1
    return {"from": str(dfrom), "to": str(dto), "count": len(rows), "by_type": by_type, "rows": rows}


def staff_performance_data(db, dfrom, dto):
    """Per-staff activity over the period (prompt 18, slice 9).

    Built ENTIRELY from rows the system already writes as a by-product of normal work — bookings
    created, folio lines posted, cards cut, drawers reconciled, complaints handled — plus the
    prompt-18 attendance/roster tables. Nobody has to fill in a timesheet for this to be true.

    `upsell_revenue` deliberately counts only non-room charges (food / minibar / laundry / extra
    bed): room revenue follows the booking, but an upsell is something the staff member actually
    sold at the desk. Discounts and voids are shown alongside so generosity is visible next to it.
    """
    from models import StaffAttendance, StaffShift

    start_dt = datetime.combine(dfrom, datetime.min.time())
    end_dt = datetime.combine(dto, datetime.max.time())
    UPSELL_TYPES = ("food", "minibar", "laundry", "extra_bed")

    rows = {}

    def _row(uid):
        if uid is None:
            return None
        return rows.setdefault(uid, {
            "user_id": uid, "staff_name": _user_name(db, uid), "role": None,
            "bookings_created": 0, "room_nights": 0,
            "charges_posted": 0, "charge_revenue": 0.0,
            "upsell_lines": 0, "upsell_revenue": 0.0,
            "discounts_given": 0, "discount_value": 0.0,
            "voids": 0, "void_value": 0.0,
            "cards_issued": 0,
            "shifts_closed": 0, "cash_variance": 0.0,
            "complaints_handled": 0,
            "sessions": 0, "hours_worked": 0.0,
            "planned_duties": 0, "no_shows": 0,
        })

    for u in db.query(User).filter(User.role.in_(("admin", "reception", "housekeeper",
                                                  "maintenance"))).all():
        entry = _row(u.user_id)
        entry["role"] = u.role

    # --- bookings created (the audit log is the only record of WHO took a desk booking) ---
    from models import AuditLog
    for a in db.query(AuditLog).filter(
            AuditLog.action == "booking.desk_create",
            AuditLog.created_at >= start_dt, AuditLog.created_at < end_dt).all():
        entry = _row(a.user_id)
        if entry is not None:
            entry["bookings_created"] += 1

    # --- folio lines posted (revenue + room nights + upsells + discounts + voids) ---
    charges = db.query(FolioCharge).filter(
        FolioCharge.posted_at >= start_dt, FolioCharge.posted_at < end_dt,
    ).all()
    for c in charges:
        entry = _row(c.posted_by)
        if entry is None:
            continue
        amount = float(c.amount or 0)
        if c.void:
            entry["voids"] += 1
            entry["void_value"] = round(entry["void_value"] + abs(amount), 2)
            continue
        if c.type == "discount":
            entry["discounts_given"] += 1
            entry["discount_value"] = round(entry["discount_value"] + abs(amount), 2)
            continue
        if c.type == "payment":
            continue
        entry["charges_posted"] += 1
        entry["charge_revenue"] = round(entry["charge_revenue"] + amount, 2)
        if c.type == "room":
            entry["room_nights"] += int(float(c.qty or 1))
        if c.type in UPSELL_TYPES:
            entry["upsell_lines"] += 1
            entry["upsell_revenue"] = round(entry["upsell_revenue"] + amount, 2)

    # --- key cards cut ---
    for c in db.query(CardIssuance).filter(
            CardIssuance.issued_at >= start_dt, CardIssuance.issued_at < end_dt).all():
        entry = _row(c.issued_by)
        if entry is not None:
            entry["cards_issued"] += 1

    # --- cash drawers closed + variance carried (prompt 12) ---
    for s in db.query(CashShift).filter(
            CashShift.status == "closed",
            CashShift.closed_at >= start_dt, CashShift.closed_at < end_dt).all():
        entry = _row(s.staff_id)
        if entry is not None:
            entry["shifts_closed"] += 1
            entry["cash_variance"] = round(entry["cash_variance"] + float(s.variance or 0), 2)

    # --- complaints / tickets resolved (prompt 13 + slice 11) ---
    for t in db.query(MaintenanceTicket).filter(
            MaintenanceTicket.resolved_at.isnot(None),
            MaintenanceTicket.resolved_at >= start_dt,
            MaintenanceTicket.resolved_at < end_dt).all():
        entry = _row(t.assignee)
        if entry is not None:
            entry["complaints_handled"] += 1

    # --- attendance + roster (prompt 18 slices 9 & 12) ---
    for a in db.query(StaffAttendance).filter(
            StaffAttendance.clock_in >= start_dt, StaffAttendance.clock_in < end_dt).all():
        entry = _row(a.user_id)
        if entry is not None:
            entry["sessions"] += 1
            entry["hours_worked"] = round(entry["hours_worked"] + (a.minutes_worked or 0) / 60, 2)
    for s in db.query(StaffShift).filter(
            StaffShift.shift_date >= dfrom, StaffShift.shift_date <= dto).all():
        entry = _row(s.user_id)
        if entry is not None and s.status != "absent":
            entry["planned_duties"] += 1

    for entry in rows.values():
        entry["no_shows"] = max(0, entry["planned_duties"] - entry["sessions"])

    data = sorted(rows.values(), key=lambda r: (-r["charge_revenue"], r["staff_name"] or ""))
    totals = {k: round(sum(float(r[k] or 0) for r in data), 2)
              for k in ("charge_revenue", "upsell_revenue", "discount_value", "void_value",
                        "cash_variance", "hours_worked")}
    for k in ("bookings_created", "charges_posted", "upsell_lines", "cards_issued",
              "shifts_closed", "complaints_handled", "sessions", "planned_duties", "no_shows"):
        totals[k] = sum(int(r[k] or 0) for r in data)
    return {"from": str(dfrom), "to": str(dto), "rows": data, "totals": totals}


def fraud_summary_data(db, dfrom, dto):
    """Fraud-alert summary over alerts detected in the range: counts by type/severity/status + list."""
    q = (db.query(FraudAlert)
         .filter(func.date(FraudAlert.detected_at) >= dfrom, func.date(FraudAlert.detected_at) <= dto)
         .order_by(FraudAlert.detected_at.desc()))
    alerts = q.all()
    by_type, by_severity, by_status = {}, {}, {}
    rows = []
    for a in alerts:
        by_type[a.type] = by_type.get(a.type, 0) + 1
        by_severity[a.severity] = by_severity.get(a.severity, 0) + 1
        by_status[a.status] = by_status.get(a.status, 0) + 1
        rows.append({"id": a.id, "type": a.type, "severity": a.severity, "status": a.status,
                     "booking_id": a.booking_id, "room_id": a.room_id,
                     "detected_at": a.detected_at.isoformat() if a.detected_at else None})
    return {"from": str(dfrom), "to": str(dto), "count": len(alerts),
            "by_type": by_type, "by_severity": by_severity, "by_status": by_status, "rows": rows}


# ============================================================
# v5d — adapted "legacy-style" detailed reports
# ============================================================

def _segment_of(db, b):
    """Business segment for a stay, adapted to OUR sources (owner's choice): complimentary, then
    the OTA channel code, then agent / website, else direct."""
    if getattr(b, "comp_mode", "none") not in (None, "none"):
        return "complimentary"
    src = (b.booking_source or "direct").strip() or "direct"
    if ota_service.is_ota_source(src, db):
        return src
    if src in ("agent", "website"):
        return src
    return "direct"


def occupancy_analysis_data(db, as_on):
    """Snapshot 'as on' occupancy: every currently-occupied room with its stay + prorated revenue,
    grouped by floor, plus floor-wise / by-source / type-wise summaries. Adapted from the legacy
    Occupancy Analysis — a booking's actual base/GST/discount/net are prorated across its rooms by
    rack share so per-room rows still sum to the real totals."""
    try:
        from routers.reception import booking_checkout_moment
    except Exception:  # pragma: no cover - defensive
        booking_checkout_moment = None

    rooms_by_id = {r.room_id: r for r in db.query(Room).all()}
    rt_by_id = {rt.room_type_id: rt for rt in db.query(RoomType).all()}
    bookings = db.query(Booking).filter(Booking.status == "checked_in").all()

    floors, rows = {}, []
    seg_acc, type_acc = {}, {}

    def _acc(d, key, occ=0, pax=0, rns=0, rev=0.0):
        r = d.setdefault(key, {"occ": 0, "pax": 0, "room_nights": 0, "revenue": 0.0})
        r["occ"] += occ; r["pax"] += pax; r["room_nights"] += rns; r["revenue"] += rev

    for b in bookings:
        items = [it for it in db.query(BookingItem).filter(BookingItem.booking_id == b.booking_id).all()
                 if it.room_id]
        if not items:
            continue
        guest = db.query(Guest).filter(Guest.guest_id == b.guest_id).first()
        nights = max(1, (b.check_out - b.check_in).days)
        racks = [(float(rt_by_id[it.room_type_id].price_per_night) * nights
                  if it.room_type_id in rt_by_id else 0.0) for it in items]
        rack_total = sum(racks) or 1.0
        base, gst = float(b.base_amount or 0), float(b.gst_amount or 0)
        disc, net = float(b.discount_amount or 0), float(b.grand_total or b.total_amount or 0)
        seg = _segment_of(db, b)
        pax = int((b.adults or 0) + (b.children or 0))
        arr = b.checked_in_at
        dep = booking_checkout_moment(b) if booking_checkout_moment else None
        for idx, it in enumerate(items):
            r = rooms_by_id.get(it.room_id)
            if not r:
                continue
            share = racks[idx] / rack_total
            rt = rt_by_id.get(it.room_type_id)
            row = {
                "room_no": r.room_number, "floor": int(r.floor or 0),
                "guest": guest.name if guest else None,
                "segment": seg, "source": b.booking_source or "direct",
                "room_type": rt.name if rt else None,
                "arrival": arr.isoformat() if arr else None,
                "departure": dep.isoformat() if dep else str(b.check_out),
                "pax": pax if idx == 0 else 0,
                "discount_pct": round(disc / rack_total * 100, 1) if rack_total else 0.0,
                "rack": round(racks[idx], 2), "room_rent": round(base * share, 2),
                "discount": round(disc * share, 2), "gross_rent": round(base * share, 2),
                "gst": round(gst * share, 2), "net": round(net * share, 2),
            }
            rows.append(row)
            floors.setdefault(int(r.floor or 0), []).append(row)
            _acc(type_acc, (rt.name if rt else "—"), occ=1, pax=(pax if idx == 0 else 0),
                 rns=nights, rev=net * share)
        _acc(seg_acc, seg, occ=len(items), pax=pax, rns=nights * len(items), rev=net)

    # floor-wise vacancy/status counts from the room master (independent of bookings)
    floor_summary = {}
    for r in rooms_by_id.values():
        if not r.is_active:
            continue
        f = int(r.floor or 0)
        fs = floor_summary.setdefault(f, {"occ": 0, "vacant": 0, "not_ready": 0, "blocked": 0})
        st = r.status
        if st == "occupied":
            fs["occ"] += 1
        elif st in ("cleaning", "inspected"):
            fs["not_ready"] += 1
        elif st in ("maintenance", "blocked"):
            fs["blocked"] += 1
        else:
            fs["vacant"] += 1

    def _summarise(acc):
        out = []
        for k in sorted(acc):
            v = acc[k]
            out.append({"key": k, "occ": v["occ"], "pax": v["pax"],
                        "room_nights": v["room_nights"], "revenue": round(v["revenue"], 2),
                        "arr": round(v["revenue"] / v["occ"], 2) if v["occ"] else 0.0})
        return out

    for f, fs in floor_summary.items():
        rev = round(sum(row["net"] for row in floors.get(f, [])), 2)
        pax = sum(row["pax"] for row in floors.get(f, []))
        fs.update({"floor": f, "pax": pax, "revenue": rev,
                   "arr": round(rev / fs["occ"], 2) if fs["occ"] else 0.0})

    total_rev = round(sum(r["net"] for r in rows), 2)
    return {
        "as_on": str(as_on),
        "rooms": sorted(rows, key=lambda x: (x["floor"], x["room_no"])),
        "floor_summary": [floor_summary[f] for f in sorted(floor_summary)],
        "by_source": _summarise(seg_acc),
        "by_type": _summarise(type_acc),
        "totals": {"occ_rooms": len(rows), "pax": sum(r["pax"] for r in rows),
                   "revenue": total_rev,
                   "arr": round(total_rev / len(rows), 2) if rows else 0.0},
    }


_CASHIER_MODE_LABELS = {"cash": "Cash", "card": "C.Card", "upi": "UPI", "bank": "Bank",
                        "other": "Other"}
_CASHIER_SECTIONS = [("checkout", "Guest Check-out Bills"),
                     ("advance", "Advance Receipts"),
                     ("paidout", "Paid-outs")]


def cashier_summary_data(db, day):
    """Front-office cashier summary for one day, in the legacy printout shape: paid Payments grouped
    by payment MODE (cash/card/upi/bank), each split into Guest Check-out Bills / Advance Receipts /
    Paid-outs, with per-row Bill/Vou no, room, guest, receipt, payment, balance, CR no, remarks, user.
    Plus per-mode totals, a grand total, unsettled checkout bills, and nil-amount checkouts. Uses our
    invoice + payment ids for the bill/voucher references."""
    pays = (db.query(Payment).filter(Payment.status == "paid",
                                     func.date(Payment.created_at) == day)
            .order_by(Payment.created_at).all())
    out = _cashier_summary_core(db, receipts=pays, refunds=pays, checkouts_day=day)
    return {"day": str(day), **out}


def cashier_summary_for_shift(db, shift):
    """v5i: the same cashier rows scoped to ONE cash shift, for the close-shift report — every
    receipt and paid-out the shift handled, whatever the payment mode. Money-IN is attributed by
    Payment.shift_id (plus cash_shift's time-window fallback for pre-v3 rows without one), money-OUT
    by Payment.refund_shift_id — the same rules _totals_by_method uses, so the rows reconcile with
    the method totals printed above them. No unsettled/nil sections (those are day concepts)."""
    from routers.cash_shift import _method_window
    from sqlalchemy import and_, or_
    receipts = (db.query(Payment)
                .filter(Payment.status == "paid",
                        or_(Payment.shift_id == shift.id, and_(*_method_window(shift))))
                .order_by(Payment.created_at).all())
    refunds = (db.query(Payment)
               .filter(Payment.refund_status == "completed", Payment.refund_shift_id == shift.id)
               .order_by(Payment.payment_id).all())
    out = _cashier_summary_core(db, receipts=receipts, refunds=refunds, checkouts_day=None)
    return {"shift_id": shift.id, **out}


def _cashier_summary_core(db, receipts, refunds, checkouts_day=None):
    """Shared body of the two cashier summaries. `receipts` = paid Payment rows to list as
    check-out bills / advances; `refunds` = Payment rows whose completed refund is a paid-out.
    `checkouts_day` adds the unsettled / nil-amount checkout sections for that calendar day."""
    from models import Folio
    from routers.folio import _get_invoice
    rcache = {}   # booking_id -> room label
    modes = {}    # mode -> {section -> [rows], "receipt","payment"}

    def _mode(m):
        return modes.setdefault(m, {"checkout": [], "advance": [], "paidout": [],
                                    "receipt": 0.0, "payment": 0.0})

    def _ctx(p):
        b = db.query(Booking).filter(Booking.booking_id == p.booking_id).first() if p.booking_id else None
        guest = db.query(Guest).filter(Guest.guest_id == b.guest_id).first() if b else None
        folio = db.query(Folio).filter(Folio.booking_id == p.booking_id).first() if p.booking_id else None
        room = _booking_room_label(db, p.booking_id, rcache) if p.booking_id else "—"
        return b, guest, folio, room

    def _base(p, at):
        b, guest, folio, room = _ctx(p)
        return b, folio, {
            "date": at.strftime("%d/%m/%y") if at else "", "time": at.strftime("%H:%M") if at else "",
            "room": room, "guest": guest.name if guest else None, "booking_id": p.booking_id,
            "balance": round(float(folio.balance or 0), 2) if folio else 0.0,
            # CR No = the card/UPI transaction reference (gateway txn id), blank for manual cash.
            # (client_ref is the desk's offline-outbox dedupe UUID — internal, not a cashier ref.)
            "cr_no": p.payment_id_gateway or "",
            "user": _user_name(db, p.collected_by)}

    for p in receipts:
        m = (p.method or "other")
        at = p.created_at
        b, folio, base = _base(p, at)
        amt = float(p.amount or 0)
        if amt:
            # A receipt taken at/after the guest's checkout moment settles their bill; earlier
            # ones are advances. (Relaxed from "checked out the same day" so a bill settled the
            # morning after still files as a check-out bill on that day's/shift's summary.)
            is_checkout = bool(b and b.checked_out_at and at and at >= b.checked_out_at)
            mm = _mode(m)
            if is_checkout:
                inv = _get_invoice(db, folio.id) if folio else None
                mm["checkout"].append({**base, "bill_no": (inv.invoice_no if inv else f"F{folio.id}" if folio else "—"),
                                       "receipt": round(amt, 2), "payment": 0.0,
                                       "remarks": (p.collect_method or "").upper() or "CHECKOUT"})
            else:
                # Match the printed receipt document number (routers/payments.receipt_payload).
                mm["advance"].append({**base, "bill_no": f"RCPT-{p.payment_id}",
                                      "receipt": round(amt, 2), "payment": 0.0, "remarks": "ADVANCE"})
            mm["receipt"] += amt

    for p in refunds:
        rf = float(p.refund_amount or 0)
        if rf and p.refund_status == "completed":
            _, _, base = _base(p, p.created_at)
            mm = _mode(p.refund_mode or (p.method or "other"))
            # Match the printed refund-voucher number; CR No = the refund's own payout reference.
            mm["paidout"].append({**base, "bill_no": f"RFND-{p.payment_id}",
                                  "cr_no": p.refund_reference or "",
                                  "receipt": 0.0, "payment": round(rf, 2),
                                  "remarks": (p.refund_reason or "REFUND PAIDOUT")})
            mm["payment"] += rf

    mode_rows = []
    for m in sorted(modes):
        v = modes[m]
        mode_rows.append({
            "mode": m, "label": _CASHIER_MODE_LABELS.get(m, m.title()),
            "sections": [{"key": k, "label": lbl, "rows": v[k]} for k, lbl in _CASHIER_SECTIONS if v[k]],
            "receipt": round(v["receipt"], 2), "payment": round(v["payment"], 2),
            "net": round(v["receipt"] - v["payment"], 2),
        })
    grand = {"receipt": round(sum(x["receipt"] for x in mode_rows), 2),
             "payment": round(sum(x["payment"] for x in mode_rows), 2),
             "net": round(sum(x["net"] for x in mode_rows), 2)}

    # checked out today: unsettled (folio balance != 0) vs nil-amount (settled, no receipt today)
    unsettled, nil_checkouts = [], []
    receipted_bookings = {p.booking_id for p in receipts if float(p.amount or 0)}
    checkouts = (db.query(Booking).filter(Booking.status == "checked_out",
                                          func.date(Booking.checked_out_at) == checkouts_day).all()
                 if checkouts_day else [])
    for b in checkouts:
        f = db.query(Folio).filter(Folio.booking_id == b.booking_id).first()
        guest = db.query(Guest).filter(Guest.guest_id == b.guest_id).first()
        room = _booking_room_label(db, b.booking_id, rcache)
        bal = round(float(f.balance or 0), 2) if f else 0.0
        inv = _get_invoice(db, f.id) if f else None
        rowc = {"bill_no": (inv.invoice_no if inv else f"F{f.id}" if f else "—"),
                "room": room, "guest": guest.name if guest else None,
                "booking_id": b.booking_id, "balance": bal}
        if bal != 0:
            unsettled.append(rowc)
        elif b.booking_id not in receipted_bookings:
            nil_checkouts.append(rowc)

    return {"modes": mode_rows, "grand": grand,
            "unsettled": unsettled, "nil_checkouts": nil_checkouts}


def _cashier_table(data):
    """Flatten the grouped cashier summary into (columns, rows, totals) for the CSV/PDF export —
    mode + section header rows, then detail rows, then a per-mode total, then unsettled / nil sections."""
    cols = ["Bill/Vou", "Date", "Time", "Room", "Guest", "Receipt", "Payment", "Balance",
            "CR No", "Remarks", "User"]

    def _sub(label):
        r = [""] * len(cols)
        r[0] = label
        return r
    rows = []
    for md in data["modes"]:
        rows.append({"section": md["label"]})
        for sec in md["sections"]:
            rows.append({"section": f"   {sec['label']}"})
            for x in sec["rows"]:
                guest = " · ".join(str(v) for v in (x.get("guest"), f"#{x['booking_id']}" if x.get("booking_id") else None) if v)
                rows.append([x["bill_no"], x["date"], x["time"], x["room"], guest,
                             x["receipt"] or "", x["payment"] or "", x["balance"], x["cr_no"],
                             x["remarks"] or "", x["user"] or ""])
        tr = _sub(f"Total ({md['label']})")
        tr[5], tr[6] = md["receipt"], md["payment"]
        rows.append({"subtotal": tr})
    if data["unsettled"]:
        rows.append({"section": "Unsettled Checkout Bills"})
        for x in data["unsettled"]:
            rows.append([x["bill_no"], "", "", x["room"], x["guest"] or "", "", "", x["balance"],
                         "", "UNSETTLED", ""])
    if data["nil_checkouts"]:
        rows.append({"section": "Checkout Bills With Nil Amount"})
        for x in data["nil_checkouts"]:
            rows.append([x["bill_no"], "", "", x["room"], x["guest"] or "", "", "", x["balance"],
                         "", "NIL", ""])
    g = data["grand"]
    totals = ["GRAND TOTAL", "", "", "", "", g["receipt"], g["payment"], "", "", f"net {g['net']}", ""]
    return cols, rows, totals


def checkout_summary_data(db, day):
    """Per-bill check-out summary for one day (adapted): one row per booking checked out that day —
    room rent, SGST/CGST, food, laundry, misc, discount, advance, refund, and the bill amount."""
    from models import Folio
    from routers.folio import _get_invoice
    rows = []
    bookings = (db.query(Booking).filter(Booking.status == "checked_out",
                                         func.date(Booking.checked_out_at) == day)
                .order_by(Booking.checked_out_at).all())
    for b in bookings:
        f = db.query(Folio).filter(Folio.booking_id == b.booking_id).first()
        if not f:
            continue
        charges = [c for c in db.query(FolioCharge).filter(FolioCharge.folio_id == f.id,
                                                           FolioCharge.void == False).all()]  # noqa: E712

        def _sum(types):
            return round(sum(float(c.amount or 0) for c in charges if c.type in types), 2)
        room_rent = _sum(("room",))
        food = _sum(("food", "minibar"))
        laundry = _sum(("laundry",))
        misc = _sum(("misc", "extra_bed"))
        discount = _sum(("discount",))
        pairs = [(c.gst_percent, c.amount) for c in charges if c.type not in ("payment", "discount")]
        _r, taxable, cgst, sgst, gross = _gst_split(pairs)
        advance = round(sum(float(p.amount or 0) for p in
                            db.query(Payment).filter(Payment.booking_id == b.booking_id,
                                                     Payment.status == "paid").all()
                            if not (b.checked_out_at and p.created_at and p.created_at >= b.checked_out_at)), 2)
        refund = round(sum(float(p.refund_amount or 0) for p in
                           db.query(Payment).filter(Payment.booking_id == b.booking_id,
                                                    Payment.refund_status == "completed").all()), 2)
        rooms = [db.query(Room).filter(Room.room_id == it.room_id).first()
                 for it in db.query(BookingItem).filter(BookingItem.booking_id == b.booking_id).all()
                 if it.room_id]
        inv = _get_invoice(db, f.id)
        rows.append({
            "bill_no": (inv.invoice_no if inv else f"F{f.id}"),
            "rooms": ", ".join(r.room_number for r in rooms if r) or "—",
            "room_rent": room_rent, "sgst": sgst, "cgst": cgst, "food": food, "laundry": laundry,
            "misc": misc, "discount": discount, "total": round(float(f.total or 0), 2),
            "advance": advance, "refund": refund, "bill_amount": round(float(f.total or 0), 2),
        })
    keys = ("room_rent", "sgst", "cgst", "food", "laundry", "misc", "discount", "total",
            "advance", "refund", "bill_amount")
    totals = {k: round(sum(r[k] for r in rows), 2) for k in keys}
    return {"day": str(day), "rows": rows, "totals": totals}


def _charge_room_label(db, charge, booking_id, _cache, _room_cache):
    """The room a room-service charge belongs to.

    v5n: `folio_charges.room_id` records the ordering room, so a three-room family's food is
    attributed room by room instead of all landing on whichever room came first on the booking.
    Charges posted before v5n carry no room, so those still fall back to the stay's first room."""
    rid = getattr(charge, "room_id", None)
    if rid:
        if rid not in _room_cache:
            _room_cache[rid] = db.query(Room.room_number).filter(Room.room_id == rid).scalar() or "—"
        return _room_cache[rid]
    return _booking_room_label(db, booking_id, _cache)


def _booking_room_label(db, booking_id, _cache):
    """The room label for a booking (its first assigned room; joined lazily + cached). Used for
    charges that name no room of their own (anything posted before v5n)."""
    if booking_id not in _cache:
        row = (db.query(Room.room_number).join(BookingItem, BookingItem.room_id == Room.room_id)
               .filter(BookingItem.booking_id == booking_id).order_by(Room.room_number).first())
        _cache[booking_id] = row[0] if row else "—"
    return _cache[booking_id]


def room_service_by_room_data(db, dfrom, dto):
    """Room-service sales grouped BY ROOM, product-wise within each room. v5n: attributed to the
    room that actually ordered (folio_charges.room_id); pre-v5n charges fall back to the stay's first
    room. Reuses `_room_service_filters`."""
    from models import Folio
    q = (db.query(FolioCharge, Booking, MenuItem)
         .join(Folio, Folio.id == FolioCharge.folio_id)
         .join(Booking, Booking.booking_id == Folio.booking_id)
         .outerjoin(MenuItem, MenuItem.id == FolioCharge.menu_item_id)
         .filter(*_room_service_filters(dfrom=dfrom, dto=dto)))
    cache, groups, gcache, rcache = {}, {}, {}, {}
    for fc, b, mi in q.all():
        # Group by (room, booking) so a room used by two stays on the same day stays separate.
        room = _charge_room_label(db, fc, b.booking_id, cache, rcache)
        key = (room, b.booking_id)
        if b.booking_id not in gcache:
            gcache[b.booking_id] = (db.query(Guest.name)
                                    .filter(Guest.guest_id == b.guest_id).scalar())
        g = groups.setdefault(key, {"room": room, "booking_id": b.booking_id,
                                    "guest": gcache[b.booking_id],
                                    "check_in": str(b.check_in), "check_out": str(b.check_out),
                                    "items": {}, "subtotal": 0.0, "qty": 0.0})
        name = (mi.name if mi else (fc.description or "Item"))
        it = g["items"].setdefault(name, {"qty": 0.0, "amount": 0.0})
        it["qty"] += float(fc.qty or 0)
        it["amount"] += float(fc.amount or 0)
        g["subtotal"] += float(fc.amount or 0)
        g["qty"] += float(fc.qty or 0)
    rows = []
    for key in sorted(groups, key=lambda k: (k[0], k[1])):
        g = groups[key]
        items = [{"item": n, "qty": round(v["qty"], 2), "amount": round(v["amount"], 2)}
                 for n, v in sorted(g["items"].items(), key=lambda x: -x[1]["amount"])]
        rows.append({"room": g["room"], "booking_id": g["booking_id"], "guest": g["guest"],
                     "check_in": g["check_in"], "check_out": g["check_out"],
                     "items": items, "qty": round(g["qty"], 2), "subtotal": round(g["subtotal"], 2)})
    grand = round(sum(r["subtotal"] for r in rows), 2)
    return {"from": str(dfrom), "to": str(dto), "rows": rows,
            "totals": {"groups": len(rows), "rooms": len({r["room"] for r in rows}), "gross": grand}}


def _rs_by_room_table(data):
    """Flatten room-service-by-room into (cols, rows, totals): a section header per (room, booking),
    its item lines, and a subtotal marker. Shared by the endpoint + the EOD PDF."""
    cols = ["Item", "Qty", "Amount"]
    rows = []
    for g in data["rows"]:
        bk = f"#{g['booking_id']} {g.get('guest') or ''}".strip()
        rows.append({"section": f"Room {g['room']} · {bk}"})
        for it in g["items"]:
            rows.append([it["item"], it["qty"], it["amount"]])
        rows.append({"subtotal": ["Subtotal", g["qty"], g["subtotal"]]})
    t = data["totals"]
    totals = [f"TOTAL — {t['groups']} stay(s) · {t['rooms']} room(s)", "", t["gross"]]
    return cols, rows, totals


def room_detail_data(db, dfrom, dto):
    """Per checked-out stay in the range, per room: check-in/out times, cleaning started/ended,
    inspected time, cleaned-by / inspected-by (from the room's HousekeepingTask), and any frauds."""
    from models import HousekeepingTask
    rows = []
    bookings = (db.query(Booking)
                .filter(Booking.checked_out_at.isnot(None),
                        func.date(Booking.checked_out_at) >= dfrom,
                        func.date(Booking.checked_out_at) <= dto)
                .order_by(Booking.checked_out_at).all())
    for b in bookings:
        guest = db.query(Guest).filter(Guest.guest_id == b.guest_id).first()
        items = [it for it in db.query(BookingItem).filter(BookingItem.booking_id == b.booking_id).all()
                 if it.room_id]
        for it in items:
            r = db.query(Room).filter(Room.room_id == it.room_id).first()
            task = (db.query(HousekeepingTask)
                    .filter(HousekeepingTask.room_id == it.room_id,
                            HousekeepingTask.type == "checkout_clean",
                            HousekeepingTask.booking_id == b.booking_id)
                    .order_by(HousekeepingTask.created_at.desc()).first())
            if task is None:  # fall back to the room's most recent cleaning after this checkout
                task = (db.query(HousekeepingTask)
                        .filter(HousekeepingTask.room_id == it.room_id,
                                HousekeepingTask.type == "checkout_clean",
                                HousekeepingTask.created_at >= b.checked_out_at)
                        .order_by(HousekeepingTask.created_at.asc()).first())
            frauds = (db.query(FraudAlert)
                      .filter(or_(FraudAlert.booking_id == b.booking_id,
                                  FraudAlert.room_id == it.room_id)).all())
            fr = "; ".join(f"{a.type}({a.severity})" for a in frauds)
            rows.append({
                "booking_id": b.booking_id, "room": r.room_number if r else "—",
                "guest": guest.name if guest else None,
                "check_in": b.checked_in_at.isoformat() if b.checked_in_at else None,
                "check_out": b.checked_out_at.isoformat() if b.checked_out_at else None,
                "cleaning_started": task.started_at.isoformat() if task and task.started_at else None,
                "cleaning_ended": task.done_at.isoformat() if task and task.done_at else None,
                "inspected_at": task.inspected_at.isoformat() if task and task.inspected_at else None,
                "cleaned_by": task.cleaned_by_name if task else None,
                "inspected_by": task.inspected_by_name if task else None,
                "frauds": fr,
            })
    return {"from": str(dfrom), "to": str(dto), "rows": rows, "totals": {"stays": len(rows)}}


def maintenance_detail_data(db, dfrom, dto):
    """Per maintenance ticket created in the range: full details + the items used. `fixed` =
    resolved/verified/closed; `inspected` = verified/closed (signed off)."""
    from models import TicketItem
    tickets = (db.query(MaintenanceTicket)
               .filter(func.date(MaintenanceTicket.created_at) >= dfrom,
                       func.date(MaintenanceTicket.created_at) <= dto)
               .order_by(MaintenanceTicket.created_at).all())
    rows = []
    for t in tickets:
        r = db.query(Room).filter(Room.room_id == t.room_id).first() if t.room_id else None
        items = db.query(TicketItem).filter(TicketItem.ticket_id == t.id).all()
        item_list = [{"item": i.item, "qty": float(i.qty or 0),
                      "est_cost": float(i.est_cost or 0), "status": i.status,
                      "posted_to_ledger": bool(i.expense_id)} for i in items]
        parts_cost = round(sum(float(i.est_cost or 0) for i in items), 2)
        fixed = t.status in ("resolved", "verified", "closed")
        inspected = t.status in ("verified", "closed") or t.verified_at is not None
        sla_breached = bool(t.escalation_level and t.escalation_level > 0)
        rows.append({
            "ticket_id": t.id,
            "location": (r.room_number if r else (t.area or "Common area")),
            "category": t.category, "priority": t.priority, "issue": t.issue, "source": t.source,
            "status": t.status, "fixed": fixed, "inspected": inspected,
            "raised_by": _user_name(db, t.raised_by),
            "assignee": _user_name(db, t.assignee),
            "verified_by": _user_name(db, t.verified_by),
            "resolution_notes": t.resolution_notes,
            "created_at": t.created_at.isoformat() if t.created_at else None,
            "assigned_at": t.assigned_at.isoformat() if t.assigned_at else None,
            "first_responded_at": t.first_responded_at.isoformat() if t.first_responded_at else None,
            "resolved_at": t.resolved_at.isoformat() if t.resolved_at else None,
            "verified_at": t.verified_at.isoformat() if t.verified_at else None,
            "sla_breached": sla_breached, "escalation_level": int(t.escalation_level or 0),
            "compensation_amount": float(t.compensation_amount or 0),
            "items": item_list, "parts_cost": parts_cost,
        })
    totals = {"tickets": len(rows),
              "fixed": sum(1 for r in rows if r["fixed"]),
              "open": sum(1 for r in rows if not r["fixed"]),
              "inspected": sum(1 for r in rows if r["inspected"]),
              "parts_cost": round(sum(r["parts_cost"] for r in rows), 2)}
    return {"from": str(dfrom), "to": str(dto), "rows": rows, "totals": totals}


# ============================================================
# v5d-C — end-of-day report bundle (emailed to the accounting address)
# ============================================================

# The reports that may be included in the end-of-day accounting email. Each renders for a single
# business date. Keys are what get stored in the `eod_report_keys` setting.
EOD_REPORT_CHOICES = {
    "occupancy_analysis": "Occupancy Analysis",
    "cashier_summary": "Front-Office Cashier Summary",
    "checkout_summary": "Check-Out Summary",
    "room_service_by_room": "Room Service by Room",
    "room_detail": "Room Detail (HK + frauds)",
    "maintenance_detail": "Maintenance Detail",
    "daily_sales": "Daily Sales",
}


def _eod_report_pdf(db, key, day):
    """Render one report for a single business date to a PDF file, returning its path (or None).
    Reuses the same data fns + generic table PDF as the on-screen reports."""
    from utils.pdf_generator import generate_report_pdf
    meta = {"Business date": f"{day:%d-%m-%Y}", **_meta()}
    title = EOD_REPORT_CHOICES.get(key, key)
    if key == "occupancy_analysis":
        d = occupancy_analysis_data(db, day)
        cols = ["Floor", "Room", "Guest", "Source", "Type", "Pax", "Rack", "Room Rent", "GST", "Net"]
        rows = [[r["floor"], r["room_no"], r["guest"], r["segment"], r["room_type"], r["pax"],
                 r["rack"], r["room_rent"], r["gst"], r["net"]] for r in d["rooms"]]
        totals = ["TOTAL", "", "", "", "", d["totals"]["pax"], "", "", "", d["totals"]["revenue"]]
    elif key == "cashier_summary":
        cols, rows, totals = _cashier_table(cashier_summary_data(db, day))
    elif key == "checkout_summary":
        d = checkout_summary_data(db, day)
        cols = ["Bill", "Rooms", "Room Rent", "SGST", "CGST", "Food", "Laundry", "Misc", "Discount",
                "Advance", "Refund", "Bill Amt"]
        rows = [[r["bill_no"], r["rooms"], r["room_rent"], r["sgst"], r["cgst"], r["food"], r["laundry"],
                 r["misc"], r["discount"], r["advance"], r["refund"], r["bill_amount"]] for r in d["rows"]]
        t = d["totals"]; totals = ["TOTAL", "", t["room_rent"], t["sgst"], t["cgst"], t["food"],
                                   t["laundry"], t["misc"], t["discount"], t["advance"], t["refund"],
                                   t["bill_amount"]]
    elif key == "room_service_by_room":
        cols, rows, totals = _rs_by_room_table(room_service_by_room_data(db, day, day))
    elif key == "room_detail":
        d = room_detail_data(db, day, day)
        cols = ["Room", "Guest", "Check-in", "Check-out", "Cleaned", "Inspected", "Cleaned by",
                "Inspected by", "Frauds"]
        rows = [[r["room"], r["guest"], (r["check_in"] or "")[:16].replace("T", " "),
                 (r["check_out"] or "")[:16].replace("T", " "),
                 (r["cleaning_ended"] or "")[:16].replace("T", " "),
                 (r["inspected_at"] or "")[:16].replace("T", " "), r["cleaned_by"] or "",
                 r["inspected_by"] or "", r["frauds"]] for r in d["rows"]]
        totals = None
    elif key == "maintenance_detail":
        d = maintenance_detail_data(db, day, day)
        cols = ["Ticket", "Location", "Issue", "Status", "Fixed", "Inspected", "Parts ₹"]
        rows = [[r["ticket_id"], r["location"], r["issue"], r["status"], "Yes" if r["fixed"] else "No",
                 "Yes" if r["inspected"] else "No", r["parts_cost"]] for r in d["rows"]]
        t = d["totals"]; totals = [f"{t['tickets']} ticket(s)", f"{t['fixed']} fixed", "", "", "", "",
                                   t["parts_cost"]]
    elif key == "daily_sales":
        d = sales_by_date(db, day, day)
        cols = ["Date", "Room Rev", "Other", "Room Svc", "Discount", "Gross", "Taxable", "CGST", "SGST"]
        rows = [[r["date"], r["room_revenue"], r["other_revenue"], r["room_service"], r["discount"],
                 r["gross_sales"], r["taxable"], r["cgst"], r["sgst"]] for r in d["rows"]]
        t = d["totals"]; totals = ["TOTAL", t["room_revenue"], t["other_revenue"], t["room_service"],
                                   t["discount"], t["gross_sales"], t["taxable"], t["cgst"], t["sgst"]]
    else:
        return None
    return generate_report_pdf(title, cols, rows, totals, meta)


def deliver_eod_reports(db, day):
    """Render the configured end-of-day reports for `day` and email them to the accounting address.
    Best-effort; returns a summary dict. Called from the night-audit after the day-close."""
    cfg = get_reports_config(db)
    if not cfg["eod_email_enabled"] or not cfg["accounting_email"]:
        return {"sent": False, "reason": "disabled or no accounting email"}
    keys = [k for k in cfg["eod_report_keys"] if k in EOD_REPORT_CHOICES]
    attachments = []
    for k in keys:
        try:
            path = _eod_report_pdf(db, k, day)
            if path:
                attachments.append((path, f"{k}_{day}.pdf"))
        except Exception as e:  # one bad report must not sink the whole email
            logger.error(f"EOD report {k} for {day} failed: {e}")
    if not attachments:
        return {"sent": False, "reason": "no reports rendered"}
    from utils.email_service import send_report_email
    subject = f"Hotel Bhimas — end-of-day reports {day:%d-%m-%Y}"
    html = (f"<p>End-of-day reports for <b>{day:%d-%m-%Y}</b> (00:00–23:59) attached:</p><ul>"
            + "".join(f"<li>{EOD_REPORT_CHOICES[k]}</li>" for k in keys) + "</ul>")
    ok = send_report_email(cfg["accounting_email"], subject, html, attachments)
    return {"sent": bool(ok), "reports": keys, "recipients": cfg["accounting_email"]}


# ============================================================
# export dispatch (csv / pdf / json)
# ============================================================

def _csv_response(filename, columns, rows, totals_row=None):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(columns)
    for r in rows:
        # Grouped reports may pass marker rows; flatten them to plain CSV lines.
        if isinstance(r, dict) and "section" in r:
            w.writerow([r["section"]])
        elif isinstance(r, dict) and "subtotal" in r:
            w.writerow(r["subtotal"])
        else:
            w.writerow(r)
    if totals_row is not None:
        w.writerow(totals_row)
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]), media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}.csv"'})


def _pdf_response(title, columns, rows, totals_row, meta, filename):
    from utils.pdf_generator import generate_report_pdf
    path = generate_report_pdf(title, columns, rows, totals_row, meta)
    return FileResponse(path, media_type="application/pdf", filename=f"{filename}.pdf")


def _export_or_json(fmt, data, *, title, columns, rows, totals_row=None, meta=None, filename):
    """Return `data` as JSON (default), or a CSV/PDF rendering of the (columns, rows, totals)
    tabular projection."""
    fmt = (fmt or "json").strip().lower()
    if fmt == "csv":
        return _csv_response(filename, columns, rows, totals_row)
    if fmt == "pdf":
        return _pdf_response(title, columns, rows, totals_row, meta or {}, filename)
    if fmt != "json":
        raise HTTPException(status_code=400, detail="format must be json, csv or pdf")
    return data


def _meta(dfrom=None, dto=None):
    m = {}
    if dfrom is not None and dto is not None:
        m["Period"] = f"{dfrom:%d-%m-%Y} to {dto:%d-%m-%Y}"
    m["Generated"] = datetime.utcnow().strftime("%d-%m-%Y %H:%M UTC")
    return m


# ============================================================
# report endpoints
# ============================================================

@router.get("/arrival-exceptions", dependencies=[Depends(require_admin)])
def arrival_exceptions_report(from_: str | None = Query(None, alias="from"), to: str | None = Query(None),
                              kind: str | None = Query(None), format: str = Query("json"),
                              db: Session = Depends(get_db)):
    """v5m — early check-ins, late arrivals and hourly extensions (from stay_events), with what
    was charged, on what basis and who approved a change."""
    from utils.arrival_rules import stay_events_report
    dfrom, dto = _range(from_, to)
    kind = kind if kind in ("early_checkin", "late_arrival", "hourly_extension") else None
    data = stay_events_report(db, dfrom, dto, kind)
    no_shows = db.query(func.count(Booking.booking_id)).filter(
        Booking.status == "no_show", func.date(Booking.no_show_at) >= dfrom,
        func.date(Booking.no_show_at) <= dto).scalar() or 0
    data["no_shows"] = int(no_shows)
    cols = ["Date", "Booking", "Guest", "Room", "Source", "Kind", "Expected", "Actual", "Deviation",
            "Hours", "Charge", "Basis", "Approved by"]
    def _dev(m):
        if m is None:
            return ""
        h, mm = divmod(abs(int(m)), 60)
        return f"{h}h {mm:02d}m"
    rows = [[r["date"], r["booking_id"], r["guest"], r["rooms"], r["source"], r["kind_label"],
             r["expected_at"] or "", r["actual_at"] or "", _dev(r["deviation_minutes"]),
             r["hours"] or "", r["charge"], r["basis_label"], r["approved_by"] or ""] for r in data["rows"]]
    t = data["totals"]
    totals = ["Total", "", "", "", "", f"E {t['early_checkin']['count']} / L {t['late_arrival']['count']} / "
              f"H {t['hourly_extension']['count']} / no-shows {no_shows}", "", "", "", "",
              data["charged_total"], "", ""]
    return _export_or_json(format, data, title="Arrival Exceptions", columns=cols, rows=rows,
                           totals_row=totals, meta=_meta(dfrom, dto), filename="arrival-exceptions")


@router.get("/occupancy", dependencies=[Depends(require_admin)])
def occupancy_report(from_: str | None = Query(None, alias="from"), to: str | None = Query(None),
                     group_by: str = Query("day"), format: str = Query("json"),
                     db: Session = Depends(get_db)):
    dfrom, dto = _range(from_, to)
    group_by = group_by if group_by in ("day", "room_type") else "day"
    data = occupancy_data(db, dfrom, dto, group_by)
    if group_by == "room_type":
        cols = ["Room Type", "Capacity (room-nights)", "Sold", "Occupancy %"]
        rows = [[r["room_type"], r["capacity_room_nights"], r["room_nights_sold"], r["occupancy_pct"]]
                for r in data["rows"]]
        totals = None
    else:
        cols = ["Date", "Occupied", "Available", "Total Rooms", "Occupancy %"]
        rows = [[r["date"], r["occupied"], r["available"], r["total_rooms"], r["occupancy_pct"]]
                for r in data["rows"]]
        totals = None
    return _export_or_json(format, data, title="Occupancy Report", columns=cols, rows=rows,
                           totals_row=totals, meta=_meta(dfrom, dto), filename="occupancy")


@router.get("/sales/daily", dependencies=[Depends(require_admin)])
def daily_sales_report(from_: str | None = Query(None, alias="from"), to: str | None = Query(None),
                       format: str = Query("json"), db: Session = Depends(get_db)):
    dfrom, dto = _range(from_, to)
    data = sales_by_date(db, dfrom, dto)
    # "Room Service" is a breakdown OF "Other Revenue", not a further column of income —
    # placed straight after it and labelled so, because summing the row would double-count.
    cols = ["Date", "Room Revenue", "Other Revenue", "(of which Room Service)", "Discount",
            "Gross Sales", "Taxable", "CGST", "SGST"]
    rows = [[r["date"], r["room_revenue"], r["other_revenue"], r["room_service"], r["discount"],
             r["gross_sales"], r["taxable"], r["cgst"], r["sgst"]] for r in data["rows"]]
    t = data["totals"]
    totals = ["TOTAL", t["room_revenue"], t["other_revenue"], t["room_service"], t["discount"],
              t["gross_sales"], t["taxable"], t["cgst"], t["sgst"]]
    return _export_or_json(format, data, title="Daily Sales Report", columns=cols, rows=rows,
                           totals_row=totals, meta=_meta(dfrom, dto), filename="daily_sales")


@router.get("/room-service/daily", dependencies=[Depends(require_admin)])
def room_service_daily_report(from_: str | None = Query(None, alias="from"), to: str | None = Query(None),
                              format: str = Query("json"), db: Session = Depends(get_db)):
    """Day-wise room-service sales (v3 item 4). Reconciles with the Room Service column on
    the Daily Sales report — both read the same non-void, menu-linked folio lines."""
    dfrom, dto = _range(from_, to)
    data = room_service_by_date(db, dfrom, dto)
    cols = ["Date", "Orders", "Items", "Gross", "Taxable", "CGST", "SGST"]
    rows = [[r["date"], r["orders"], r["items"], r["gross"], r["taxable"], r["cgst"], r["sgst"]]
            for r in data["rows"]]
    t = data["totals"]
    totals = ["TOTAL", t["orders"], t["items"], t["gross"], t["taxable"], t["cgst"], t["sgst"]]
    return _export_or_json(format, data, title="Room Service Sales — Day-wise", columns=cols,
                           rows=rows, totals_row=totals, meta=_meta(dfrom, dto),
                           filename="room_service_daily")


@router.get("/room-service/items", dependencies=[Depends(require_admin)])
def room_service_items_report(from_: str | None = Query(None, alias="from"), to: str | None = Query(None),
                              format: str = Query("json"), db: Session = Depends(get_db)):
    """Product-wise room-service sales (v3 item 4) — which dishes sell, best first."""
    dfrom, dto = _range(from_, to)
    data = room_service_by_item(db, dfrom, dto)
    cols = ["Item", "Category", "Qty Sold", "Gross", "Avg Price", "% of RS Sales"]
    rows = [[r["item"], r["category"], r["qty"], r["gross"], r["avg_price"], r["share_percent"]]
            for r in data["rows"]]
    t = data["totals"]
    totals = ["TOTAL", "", t["qty"], t["gross"], "", t["share_percent"]]
    return _export_or_json(format, data, title="Room Service Sales — Product-wise", columns=cols,
                           rows=rows, totals_row=totals, meta=_meta(dfrom, dto),
                           filename="room_service_items")


@router.get("/shift-payments", dependencies=[Depends(require_admin)])
def shift_payments_report(from_: str | None = Query(None, alias="from"), to: str | None = Query(None),
                          format: str = Query("json"), db: Session = Depends(get_db)):
    """Shift-wise collections split by payment method (v3 item 6). Shifts are selected by the
    date they were OPENED, so an overnight shift reports under the day it started."""
    dfrom, dto = _range(from_, to)
    data = shift_payments_data(db, dfrom, dto)
    cols = ["Shift", "Station", "Staff", "Opened", "Closed", "Cash", "Card", "UPI", "Bank",
            "Collected", "Refunds", "Net", "Counted Cash", "Variance"]
    rows = [[r["shift_id"], r["station_id"], r["staff_name"], r["opened_at"], r["closed_at"],
             r["cash"], r["card"], r["upi"], r["bank"], r["total_collected"], r["refunds"],
             r["net_collected"], r["counted_cash"], r["variance"]] for r in data["rows"]]
    t = data["totals"]
    totals = ["TOTAL", "", "", "", "", t["cash"], t["card"], t["upi"], t["bank"],
              t["total_collected"], t["refunds"], t["net_collected"], "", t["variance"]]
    return _export_or_json(format, data, title="Shift-wise Payment Split", columns=cols, rows=rows,
                           totals_row=totals, meta=_meta(dfrom, dto), filename="shift_payments")


@router.get("/payments-daily", dependencies=[Depends(require_admin)])
def payments_daily_report(from_: str | None = Query(None, alias="from"), to: str | None = Query(None),
                          format: str = Query("json"), db: Session = Depends(get_db)):
    """Day-wise payments collected by method (cash/card/UPI/bank), refunds, and net (ALT-4)."""
    dfrom, dto = _range(from_, to)
    data = payments_by_day(db, dfrom, dto)
    cols = ["Date", "Cash", "Card", "UPI", "Bank", "Collected", "Refunds", "Net"]
    rows = [[r["date"], r["cash"], r["card"], r["upi"], r["bank"], r["collected"], r["refunds"], r["net"]]
            for r in data["rows"]]
    t = data["totals"]
    totals = ["TOTAL", t["cash"], t["card"], t["upi"], t["bank"], t["collected"], t["refunds"], t["net"]]
    return _export_or_json(format, data, title="Day-wise Payments & Refunds", columns=cols, rows=rows,
                           totals_row=totals, meta=_meta(dfrom, dto), filename="payments_daily")


@router.get("/arrivals-departures", dependencies=[Depends(require_admin)])
def arrivals_departures_report(from_: str | None = Query(None, alias="from"), to: str | None = Query(None),
                               format: str = Query("json"), db: Session = Depends(get_db)):
    dfrom, dto = _range(from_, to)
    data = arrivals_departures_data(db, dfrom, dto)
    cols = ["Date", "Arrivals", "Departures"]
    rows = [[r["date"], r["arrivals"], r["departures"]] for r in data["rows"]]
    totals = ["TOTAL", data["totals"]["arrivals"], data["totals"]["departures"]]
    return _export_or_json(format, data, title="Arrivals & Departures", columns=cols, rows=rows,
                           totals_row=totals, meta=_meta(dfrom, dto), filename="arrivals_departures")


@router.get("/in-house", dependencies=[Depends(require_admin)])
def in_house_report(format: str = Query("json"), db: Session = Depends(get_db)):
    data = in_house_data(db)
    cols = ["Booking", "Guest", "Phone", "Rooms", "Check-in", "Check-out", "Nights", "Balance"]
    rows = [[r["booking_id"], r["guest_name"], r["phone"], r["rooms"], r["check_in"],
             r["check_out"], r["nights"], r["balance"]] for r in data["rows"]]
    return _export_or_json(format, data, title="In-House Guests", columns=cols, rows=rows,
                           meta=_meta(), filename="in_house")


@router.get("/occupancy-analysis", dependencies=[Depends(require_admin)])
def occupancy_analysis_report(as_on: str | None = Query(None), format: str = Query("json"),
                              db: Session = Depends(get_db)):
    """Adapted Occupancy Analysis 'as on' snapshot: per-room stay + revenue, grouped by floor, with
    floor-wise / by-source / by-type summaries."""
    d = _parse_date(as_on, "as_on") if as_on else date.today()
    data = occupancy_analysis_data(db, d)
    cols = ["Floor", "Room", "Guest", "Source", "Type", "Arrival", "Departure", "Pax",
            "Dis %", "Rack", "Room Rent", "Discount", "Gross Rent", "GST", "Net"]
    rows = [[r["floor"], r["room_no"], r["guest"], r["segment"], r["room_type"], r["arrival"],
             r["departure"], r["pax"], r["discount_pct"], r["rack"], r["room_rent"], r["discount"],
             r["gross_rent"], r["gst"], r["net"]] for r in data["rooms"]]
    t = data["totals"]
    # Append the floor-wise / type-wise / source (recapitulation) summaries as labelled sections after
    # the room table, so the CSV/PDF (and the EOD email) carry them like the legacy printout.
    n = len(cols)

    def _summary_block(title, items):
        rows.append({"section": title})
        rows.append({"subtotal": ["Group", "Occ", "Pax", "Room-nights", "Revenue", "ARR"] + [""] * (n - 6)})
        for s in items:
            # .get(): floor-wise rows don't track room-nights (only by-type/by-source do), so a missing
            # key renders as a blank cell instead of raising KeyError and 500-ing the whole report.
            rows.append([s.get("key", ""), s.get("occ", ""), s.get("pax", ""), s.get("room_nights", ""),
                         s.get("revenue", ""), s.get("arr", "")] + [""] * (n - 6))
    _summary_block("FLOOR-WISE SUMMARY", [{**f, "key": f"Floor {f['floor']}"} for f in data["floor_summary"]])
    _summary_block("TYPE-WISE REVENUE", data["by_type"])
    _summary_block("RECAPITULATION (by source)", data["by_source"])
    totals = ["TOTAL", "", "", "", "", "", "", t["pax"], "", "", "", "", "", "", t["revenue"]]
    meta = {"As on": f"{d:%d-%m-%Y}", "Occupied rooms": t["occ_rooms"], "ARR": t["arr"],
            **_meta()}
    return _export_or_json(format, data, title="Occupancy Analysis", columns=cols, rows=rows,
                           totals_row=totals, meta=meta, filename="occupancy_analysis")


@router.get("/cashier-summary", dependencies=[Depends(require_admin)])
def cashier_summary_report(day: str | None = Query(None), format: str = Query("json"),
                           db: Session = Depends(get_db)):
    """Adapted Front-Office Cashier Summary for one day: receipts by method (advance vs checkout),
    paid-outs, and unsettled checkout bills."""
    d = _parse_date(day, "day") if day else date.today()
    data = cashier_summary_data(db, d)
    cols, rows, totals = _cashier_table(data)
    meta = {"Day": f"{d:%d-%m-%Y}", "Unsettled bills": len(data["unsettled"]), **_meta()}
    return _export_or_json(format, data, title="Front-Office Cashier Summary", columns=cols,
                           rows=rows, totals_row=totals, meta=meta, filename="cashier_summary")


@router.get("/checkout-summary", dependencies=[Depends(require_admin)])
def checkout_summary_report(day: str | None = Query(None), format: str = Query("json"),
                            db: Session = Depends(get_db)):
    """Adapted Check-Out Summary for one day: per-bill room rent, SGST/CGST, food, laundry, misc,
    discount, advance, refund, bill amount."""
    d = _parse_date(day, "day") if day else date.today()
    data = checkout_summary_data(db, d)
    cols = ["Bill", "Rooms", "Room Rent", "SGST", "CGST", "Food", "Laundry", "Misc", "Discount",
            "Advance", "Refund", "Bill Amount"]
    rows = [[r["bill_no"], r["rooms"], r["room_rent"], r["sgst"], r["cgst"], r["food"],
             r["laundry"], r["misc"], r["discount"], r["advance"], r["refund"], r["bill_amount"]]
            for r in data["rows"]]
    t = data["totals"]
    totals = ["TOTAL", "", t["room_rent"], t["sgst"], t["cgst"], t["food"], t["laundry"],
              t["misc"], t["discount"], t["advance"], t["refund"], t["bill_amount"]]
    meta = {"Day": f"{d:%d-%m-%Y}", **_meta()}
    return _export_or_json(format, data, title="Check-Out Summary", columns=cols, rows=rows,
                           totals_row=totals, meta=meta, filename="checkout_summary")


@router.get("/room-service/by-room", dependencies=[Depends(require_admin)])
def room_service_by_room_report(from_: str | None = Query(None, alias="from"), to: str | None = Query(None),
                                format: str = Query("json"), db: Session = Depends(get_db)):
    """Room-service sales grouped by room, product-wise within each room."""
    dfrom, dto = _range(from_, to)
    data = room_service_by_room_data(db, dfrom, dto)
    cols, rows, totals = _rs_by_room_table(data)
    return _export_or_json(format, data, title="Room Service by Room", columns=cols, rows=rows,
                           totals_row=totals, meta=_meta(dfrom, dto), filename="room_service_by_room")


@router.get("/room-detail", dependencies=[Depends(require_admin)])
def room_detail_report(from_: str | None = Query(None, alias="from"), to: str | None = Query(None),
                       format: str = Query("json"), db: Session = Depends(get_db)):
    """Per checked-out stay: check-in/out + cleaning/inspection timestamps, cleaned-by/inspected-by,
    and any frauds. Range is by check-out date."""
    dfrom, dto = _range(from_, to)
    data = room_detail_data(db, dfrom, dto)
    cols = ["Room", "Guest", "Check-in", "Check-out", "Cleaning start", "Cleaning end",
            "Inspected", "Cleaned by", "Inspected by", "Frauds"]

    def _t(s):
        return (s or "").replace("T", " ")[:16]
    rows = [[r["room"], r["guest"], _t(r["check_in"]), _t(r["check_out"]), _t(r["cleaning_started"]),
             _t(r["cleaning_ended"]), _t(r["inspected_at"]), r["cleaned_by"] or "", r["inspected_by"] or "",
             r["frauds"]] for r in data["rows"]]
    return _export_or_json(format, data, title="Room Detail (housekeeping + frauds)", columns=cols,
                           rows=rows, meta=_meta(dfrom, dto), filename="room_detail")


@router.get("/maintenance-detail", dependencies=[Depends(require_admin)])
def maintenance_detail_report(from_: str | None = Query(None, alias="from"), to: str | None = Query(None),
                              format: str = Query("json"), db: Session = Depends(get_db)):
    """Per maintenance ticket created in the range: details + items, fixed?/inspected?, parts cost."""
    dfrom, dto = _range(from_, to)
    data = maintenance_detail_data(db, dfrom, dto)
    cols = ["Ticket", "Location", "Category", "Priority", "Issue", "Status", "Fixed", "Inspected",
            "Assignee", "Verified by", "Items", "Parts ₹"]
    rows = []
    for r in data["rows"]:
        item_str = "; ".join(f"{i['item']}×{i['qty']:g} [{i['status']}]" for i in r["items"]) or "—"
        rows.append([r["ticket_id"], r["location"], r["category"], r["priority"], r["issue"],
                     r["status"], "Yes" if r["fixed"] else "No", "Yes" if r["inspected"] else "No",
                     r["assignee"] or "", r["verified_by"] or "", item_str, r["parts_cost"]])
    t = data["totals"]
    totals = [f"{t['tickets']} ticket(s)", f"{t['fixed']} fixed / {t['open']} open", "", "", "",
              "", "", f"{t['inspected']} insp.", "", "", "", t["parts_cost"]]
    return _export_or_json(format, data, title="Maintenance Detail", columns=cols, rows=rows,
                           totals_row=totals, meta=_meta(dfrom, dto), filename="maintenance_detail")


@router.get("/travel-agents", dependencies=[Depends(require_admin)])
def travel_agents_report(from_: str | None = Query(None, alias="from"), to: str | None = Query(None),
                         format: str = Query("json"), db: Session = Depends(get_db)):
    dfrom, dto = _range(from_, to)
    data = travel_agents_data(db, dfrom, dto)
    cols = ["Agent", "Comm %", "Bookings", "Room Revenue", "Accrued", "Paid", "Outstanding"]
    rows = [[r["agent_name"], r["commission_percent"], r["bookings"], r["room_revenue"],
             r["commission_accrued"], r["commission_paid"], r["outstanding"]] for r in data["rows"]]
    t = data["totals"]
    totals = ["TOTAL", "", t["bookings"], t["room_revenue"], t["commission_accrued"],
              t["commission_paid"], t["outstanding"]]
    return _export_or_json(format, data, title="Travel-Agent Settlement", columns=cols, rows=rows,
                           totals_row=totals, meta=_meta(dfrom, dto), filename="travel_agents")


@router.get("/ota", dependencies=[Depends(require_admin)])
def ota_report(from_: str | None = Query(None, alias="from"), to: str | None = Query(None),
               format: str = Query("json"), db: Session = Depends(get_db)):
    dfrom, dto = _range(from_, to)
    data = ota_data(db, dfrom, dto)
    cols = ["OTA", "Bookings", "Room Revenue", "Commission (est.)"]
    rows = [[r["ota"], r["bookings"], r["room_revenue"], r["commission_est"]] for r in data["rows"]]
    t = data["totals"]
    totals = ["TOTAL", t["bookings"], t["room_revenue"], t["commission_est"]]
    return _export_or_json(format, data, title="OTA-Wise Revenue", columns=cols, rows=rows,
                           totals_row=totals, meta=_meta(dfrom, dto), filename="ota_revenue")


@router.get("/gst", dependencies=[Depends(require_admin)])
def gst_report(from_: str | None = Query(None, alias="from"), to: str | None = Query(None),
               format: str = Query("json"), db: Session = Depends(get_db)):
    dfrom, dto = _range(from_, to)
    data = gst_data(db, dfrom, dto)
    cols = ["GST Slab %", "Taxable Value", "CGST", "SGST", "Gross"]
    rows = [[r["gst_percent"], r["taxable"], r["cgst"], r["sgst"], r["gross"]] for r in data["slabs"]]
    totals = ["TOTAL", data["taxable_total"], data["cgst_total"], data["sgst_total"], data["gross_total"]]
    return _export_or_json(format, data, title="GST Report", columns=cols, rows=rows,
                           totals_row=totals, meta=_meta(dfrom, dto), filename="gst_report")


@router.get("/cash-shift", dependencies=[Depends(require_admin)])
def cash_shift_report(from_: str | None = Query(None, alias="from"), to: str | None = Query(None),
                      format: str = Query("json"), db: Session = Depends(get_db)):
    dfrom, dto = _range(from_, to)
    data = cash_shift_data(db, dfrom, dto)
    cols = ["Shift", "Station", "Status", "Staff", "Opening", "Collections", "Expenses",
            "Payouts", "Expected", "Counted", "Variance"]
    rows = [[r["shift_id"], r["station_id"], r["status"], r["staff_name"], r["opening_balance"],
             r["collections_cash"], r["expenses_total"], r["payouts_total"], r["expected_cash"],
             r["counted_cash"], r["variance"]] for r in data["rows"]]
    t = data["totals"]
    totals = ["TOTAL", "", "", "", t["opening_balance"], t["collections_cash"], t["expenses_total"],
              t["payouts_total"], t["expected_cash"], "", t["variance"]]
    return _export_or_json(format, data, title="Cash / Shift Report", columns=cols, rows=rows,
                           totals_row=totals, meta=_meta(dfrom, dto), filename="cash_shift")


@router.get("/card-audit", dependencies=[Depends(require_admin)])
def card_audit_report(from_: str | None = Query(None, alias="from"), to: str | None = Query(None),
                      format: str = Query("json"), db: Session = Depends(get_db)):
    dfrom, dto = _range(from_, to)
    data = card_audit_data(db, dfrom, dto)
    cols = ["Card", "Booking", "Room", "Card UID", "Type", "Issue Type", "Status", "Issued By",
            "Station", "Issued At"]
    rows = [[r["card_id"], r["booking_id"], r["room"], r["card_uid"], r["card_type"],
             r["issue_type"], r["status"], r["issued_by"], r["station_id"], r["issued_at"]]
            for r in data["rows"]]
    return _export_or_json(format, data, title="Card-Issuance Audit", columns=cols, rows=rows,
                           meta=_meta(dfrom, dto), filename="card_audit")


@router.get("/fraud-summary", dependencies=[Depends(require_admin)])
def fraud_summary_report(from_: str | None = Query(None, alias="from"), to: str | None = Query(None),
                         format: str = Query("json"), db: Session = Depends(get_db)):
    dfrom, dto = _range(from_, to)
    data = fraud_summary_data(db, dfrom, dto)
    cols = ["Alert", "Type", "Severity", "Status", "Booking", "Room", "Detected At"]
    rows = [[r["id"], r["type"], r["severity"], r["status"], r["booking_id"], r["room_id"],
             r["detected_at"]] for r in data["rows"]]
    return _export_or_json(format, data, title="Fraud-Alert Summary", columns=cols, rows=rows,
                           meta=_meta(dfrom, dto), filename="fraud_summary")


@router.get("/staff-performance", dependencies=[Depends(require_admin)])
def staff_performance_report(from_: str | None = Query(None, alias="from"),
                             to: str | None = Query(None),
                             format: str = Query("json"), db: Session = Depends(get_db)):
    """Per-staff activity (prompt 18, slice 9): what each staff member sold, gave away, and worked.

    Everything here is a by-product of normal work already recorded by the system — no timesheets,
    no self-reporting. Read discounts/voids/variance next to revenue: generosity and shortfalls
    belong in the same view as sales."""
    dfrom, dto = _range(from_, to)
    data = staff_performance_data(db, dfrom, dto)
    cols = ["Staff", "Role", "Bookings", "Room nights", "Revenue posted (₹)", "Upsells (₹)",
            "Discounts (₹)", "Voids (₹)", "Cards", "Shifts", "Cash variance (₹)",
            "Complaints", "Hours", "Planned", "No-shows"]
    rows = [[r["staff_name"], r["role"], r["bookings_created"], r["room_nights"],
             r["charge_revenue"], r["upsell_revenue"], r["discount_value"], r["void_value"],
             r["cards_issued"], r["shifts_closed"], r["cash_variance"], r["complaints_handled"],
             r["hours_worked"], r["planned_duties"], r["no_shows"]] for r in data["rows"]]
    t = data["totals"]
    totals_row = ["TOTAL", "", t["bookings_created"], "", t["charge_revenue"], t["upsell_revenue"],
                  t["discount_value"], t["void_value"], t["cards_issued"], t["shifts_closed"],
                  t["cash_variance"], t["complaints_handled"], t["hours_worked"],
                  t["planned_duties"], t["no_shows"]]
    return _export_or_json(format, data, title="Staff Performance", columns=cols, rows=rows,
                           totals_row=totals_row, meta=_meta(dfrom, dto),
                           filename="staff_performance")


# ============================================================
# dashboard + trends
# ============================================================

@router.get("/dashboard", dependencies=[Depends(require_admin)])
def owner_dashboard(db: Session = Depends(get_db)):
    """Live owner KPIs for today (reuses the fraud daily-digest math) + pending-arrivals list +
    open-alerts list + today's collections. Sourced from the shared DB (desktop + website)."""
    from utils.fraud_detection import compute_daily_digest
    today = date.today()
    digest = compute_daily_digest(db, today)
    collections = collections_summary(db, today, today)

    # Pending arrivals today = confirmed bookings arriving today, not yet checked in.
    pending = []
    for b in (db.query(Booking).filter(Booking.check_in == today, Booking.status == "confirmed")
              .order_by(Booking.check_in_time).all()):
        guest = db.query(Guest).filter(Guest.guest_id == b.guest_id).first()
        pending.append({"booking_id": b.booking_id, "guest_name": guest.name if guest else None,
                        "check_in_time": str(b.check_in_time) if b.check_in_time else None,
                        "check_out": str(b.check_out)})

    open_alerts = []
    for a in (db.query(FraudAlert).filter(FraudAlert.status == "open")
              .order_by(FraudAlert.detected_at.desc()).limit(10).all()):
        open_alerts.append({"id": a.id, "type": a.type, "severity": a.severity,
                            "detected_at": a.detected_at.isoformat() if a.detected_at else None})

    # Overdue = past the actual checkout MOMENT plus the grace window.
    # v4b3: this used to be `check_out < today`, which in 24h mode flags a 22:00 check-in a
    # full 22 hours early — a guest with hours left to run showed as overdue on the owner's
    # dashboard. It now uses the same read-model the automatic biller does, so the dashboard,
    # the desk board and the job can never disagree about who is overdue.
    from services.overstay_billing import overstay_state
    from utils.settings import get_overstay_config
    ov_cfg = get_overstay_config(db)
    overdue = []
    for b in (db.query(Booking).filter(Booking.status == "checked_in",
                                       Booking.check_out <= today)
              .order_by(Booking.check_out).all()):
        st = overstay_state(db, b, cfg=ov_cfg)
        if not st["overdue"]:
            continue
        guest = db.query(Guest).filter(Guest.guest_id == b.guest_id).first()
        overdue.append({"booking_id": b.booking_id, "guest_name": guest.name if guest else None,
                        "check_out": str(b.check_out), "due_at": st["due_at"],
                        "nights_overdue": st["nights_overdue"],
                        "auto_charged_nights": st["auto_nights"],
                        "auto_charged_amount": st["auto_amount"]})

    return {
        "date": str(today),
        "occupancy": digest["occupancy"],
        "revenue_today": digest["revenue_today"],
        "expected_cash_today": digest["expected_cash_today"],
        "collections_today": collections,
        "arrivals": digest["arrivals"],
        "departures": digest["departures"],
        "pending_arrivals": pending,
        "overdue": overdue,
        "overdue_count": len(overdue),
        "open_alerts_count": digest["open_alerts"],
        "high_severity_alerts": digest["high_severity_alerts"],
        "open_alerts": open_alerts,
    }


def _period_metrics(db, end_day, days=7):
    """Occupancy % (avg over the window) + gross revenue for the `days`-long window ending on
    `end_day` (inclusive)."""
    start = end_day - timedelta(days=days - 1)
    occ = occupancy_data(db, start, end_day, "day")
    pcts = [r["occupancy_pct"] for r in occ["rows"]]
    avg_occ = round(sum(pcts) / len(pcts), 1) if pcts else 0.0
    sales = sales_by_date(db, start, end_day)
    return {"from": str(start), "to": str(end_day),
            "avg_occupancy_pct": avg_occ, "revenue": sales["totals"]["gross_sales"]}


@router.get("/trends/weekly", dependencies=[Depends(require_admin)])
def weekly_trends(db: Session = Depends(get_db)):
    """This week vs the same week last month / last year, plus agent/source mix, discount/void/
    refund totals, and open maintenance tickets by age. Powers the weekly owner digest (11/15)."""
    today = date.today()
    this_week = _period_metrics(db, today, 7)
    last_month = _period_metrics(db, today - timedelta(days=28), 7)
    last_year = _period_metrics(db, today - timedelta(days=364), 7)

    wstart = today - timedelta(days=6)
    # Source/agent mix this week (by check_in).
    mix = {}
    for src, cnt in (db.query(Booking.booking_source, func.count(Booking.booking_id))
                     .filter(Booking.check_in >= wstart, Booking.check_in <= today,
                             Booking.status.in_(_LIVE_STATUSES))
                     .group_by(Booking.booking_source).all()):
        mix[src or "unknown"] = int(cnt)

    # Discount / void / refund totals this week.
    discount_total = 0.0
    for day in _days(wstart, today):
        discount_total += _sum_charges(db, day, types=("discount",))
    void_total = float(db.query(func.coalesce(func.sum(FolioCharge.amount), 0)).filter(
        FolioCharge.void == True,  # noqa: E712
        FolioCharge.reversal_of_id.is_(None),
        func.date(FolioCharge.posted_at) >= wstart,
        func.date(FolioCharge.posted_at) <= today).scalar() or 0)
    refund_total = float(db.query(func.coalesce(func.sum(Payment.refund_amount), 0)).filter(
        Payment.refund_status == "completed",
        func.date(Payment.created_at) >= wstart, func.date(Payment.created_at) <= today).scalar() or 0)

    # Open maintenance tickets by age bucket.
    buckets = {"0-2d": 0, "3-7d": 0, "8-30d": 0, "30d+": 0}
    from routers.maintenance import TERMINAL_STATUSES   # local: routers import each other
    # v4b8: `verified` no longer exists (migration 031 renamed it to `resolved`), and
    # `work_done` is NOT closed — the job is finished but nobody has signed it off yet, so it
    # belongs in this ageing report. One source of truth for what "done" means.
    open_tickets = db.query(MaintenanceTicket).filter(
        MaintenanceTicket.status.notin_(TERMINAL_STATUSES)).all()
    now = datetime.utcnow()
    for t in open_tickets:
        age_days = (now - t.created_at).days if t.created_at else 0
        if age_days <= 2:
            buckets["0-2d"] += 1
        elif age_days <= 7:
            buckets["3-7d"] += 1
        elif age_days <= 30:
            buckets["8-30d"] += 1
        else:
            buckets["30d+"] += 1

    return {
        "this_week": this_week,
        "vs_last_month": last_month,
        "vs_last_year": last_year,
        "source_mix": mix,
        "discount_total": round(abs(discount_total), 2),
        "void_total": round(abs(void_total), 2),
        "refund_total": round(refund_total, 2),
        "open_maintenance_by_age": buckets,
        "open_maintenance_total": len(open_tickets),
    }


# ============================================================
# night audit / day-close
# ============================================================

@router.get("/day-close", dependencies=[Depends(require_admin)])
def get_day_close(date_: str | None = Query(None, alias="date"), db: Session = Depends(get_db)):
    """The persisted day-close snapshot for a business date (default: the current business date,
    or today if none rolled yet). 404 if that date has not been closed."""
    if date_:
        d = _parse_date(date_, "date")
    else:
        cfg = get_reports_config(db)
        d = _parse_date(cfg["business_date"], "business_date") if cfg["business_date"] else date.today()
    row = db.query(DayCloseSummary).filter(DayCloseSummary.business_date == d).first()
    if not row:
        raise HTTPException(status_code=404, detail=f"No day-close snapshot for {d}. Run the night audit first.")
    return _serialize_day_close(row)


@router.get("/day-close/history", dependencies=[Depends(require_admin)])
def day_close_history(limit: int = Query(30, ge=1, le=365), db: Session = Depends(get_db)):
    rows = (db.query(DayCloseSummary).order_by(DayCloseSummary.business_date.desc())
            .limit(limit).all())
    return {"data": [_serialize_day_close(r) for r in rows]}


@router.post("/day-close/run", dependencies=[Depends(require_admin)])
def run_day_close(date_: str | None = Query(None, alias="date"), user=Depends(require_admin),
                  db: Session = Depends(get_db)):
    """Manually trigger the night-audit / day-close for a business date (default: yesterday if a
    business date has rolled, else today). Idempotent — re-running overwrites that date's snapshot.
    Works regardless of the scheduler's enabled flag."""
    from utils.night_audit import run_night_audit
    d = _parse_date(date_, "date") if date_ else None
    result = run_night_audit(db, business_date=d, user=user, generated_by="manual")
    return result


@router.get("/config", dependencies=[Depends(require_admin)])
def reports_config(db: Session = Depends(get_db)):
    """Reports & scheduled-delivery settings (v5d-C), plus the valid EOD report choices."""
    cfg = get_reports_config(db)
    cfg["eod_report_choices"] = EOD_REPORT_CHOICES
    return cfg


@router.put("/config")
def update_reports_config(data: ReportsConfigUpdate, db: Session = Depends(get_db),
                         user=Depends(require_admin)):
    """Edit the reports & delivery settings (accounting email, EOD report selection, 6-hourly
    owner-WhatsApp summary, night-audit hour/enabled). Audited."""
    from utils import settings as s
    before = get_reports_config(db)
    changes = data.model_dump(exclude_unset=True)
    key_map = {
        "night_audit_hour": s.NIGHT_AUDIT_HOUR_KEY,
        "night_audit_enabled": s.NIGHT_AUDIT_ENABLED_KEY,
        "accounting_email": s.ACCOUNTING_EMAIL_KEY,
        "eod_email_enabled": s.EOD_EMAIL_ENABLED_KEY,
        "ops_summary_enabled": s.OPS_SUMMARY_ENABLED_KEY,
    }
    for field, value in changes.items():
        if field == "eod_report_keys":
            keys = value if isinstance(value, list) else str(value).split(",")
            keys = [k.strip() for k in keys if k.strip() in EOD_REPORT_CHOICES]
            s.set_setting(db, s.EOD_REPORT_KEYS_KEY, ",".join(keys), user=user, commit=False)
            continue
        key = key_map.get(field)
        if not key:
            continue
        stored = ("true" if value else "false") if isinstance(value, bool) else str(value)
        s.set_setting(db, key, stored, user=user, commit=False)
    db.commit()
    after = get_reports_config(db)
    write_audit(db, user, "settings.reports_update", "app_settings", None,
                before=before, after=after, client="web", commit=True)
    after["eod_report_choices"] = EOD_REPORT_CHOICES
    return after


@router.get("/overstay/config", dependencies=[Depends(require_admin)])
def overstay_config(db: Session = Depends(get_db)):
    """Automatic overstay-billing settings (v4b3)."""
    from utils.settings import get_overstay_config
    return get_overstay_config(db)


@router.put("/overstay/config")
def update_overstay_config(data: OverstayConfigUpdate, db: Session = Depends(get_db),
                           user=Depends(require_admin)):
    """Edit the overstay-billing settings. Audited — arming an unattended charge is exactly
    the kind of change an owner should be able to see who made and when."""
    from utils import settings as s
    before = s.get_overstay_config(db)
    changes = data.model_dump(exclude_unset=True)
    if not changes:
        return before
    key_map = {
        "enabled": s.OVERSTAY_ENABLED_KEY,
        "grace_minutes": s.OVERSTAY_GRACE_KEY,
        "sweep_interval_minutes": s.OVERSTAY_INTERVAL_KEY,
        "max_auto_days": s.OVERSTAY_MAX_DAYS_KEY,
    }
    for field, value in changes.items():
        key = key_map.get(field)
        if not key:
            continue
        stored = ("true" if value else "false") if isinstance(value, bool) else str(value)
        s.set_setting(db, key, stored, user=user, commit=False)
    db.commit()
    after = s.get_overstay_config(db)
    write_audit(db, user, "settings.overstay_update", "app_settings", None,
                before=before, after=after, client="web", commit=True)
    # The interval is read once at startup, so a change to it needs a restart to take effect.
    after["note"] = ("Interval changes apply after the next backend restart; the enabled "
                     "flag and grace take effect on the very next sweep.")
    return after


@router.post("/overstay/run", dependencies=[Depends(require_admin)])
def run_overstay_sweep(dry_run: bool = Query(True), db: Session = Depends(get_db)):
    """Run the automatic overstay sweep now (v4b3).

    **Defaults to a DRY RUN** — it reports who is past their checkout moment + grace and what
    each would be charged, without touching anything. That is how the owner should watch this
    for a week before arming `overstay_auto_charge_enabled`.

    A dry run works even while auto-charge is switched off; a real run does not."""
    from services.overstay_billing import sweep_overstays
    return sweep_overstays(db, dry_run=dry_run, generated_by="manual")


def _serialize_day_close(row: DayCloseSummary) -> dict:
    def _f(v):
        return float(v) if v is not None else None
    return {
        "business_date": str(row.business_date),
        "rooms_total": row.rooms_total, "rooms_occupied": row.rooms_occupied,
        "occupancy_pct": row.occupancy_pct,
        "room_revenue": _f(row.room_revenue), "other_revenue": _f(row.other_revenue),
        "total_sales": _f(row.total_sales), "taxable_total": _f(row.taxable_total),
        "cgst_total": _f(row.cgst_total), "sgst_total": _f(row.sgst_total),
        # v4b6: given away, reported alongside the revenue above — never inside it.
        "comp_room_value": _f(row.comp_room_value),
        "cash_collected": _f(row.cash_collected), "cash_expected": _f(row.cash_expected),
        "cash_variance": _f(row.cash_variance),
        "arrivals": row.arrivals, "departures": row.departures, "open_alerts": row.open_alerts,
        "folios_posted": row.folios_posted, "generated_by": row.generated_by,
        "generated_at": row.generated_at.isoformat() if row.generated_at else None,
    }
