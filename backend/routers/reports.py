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
from utils import ordering
from utils.audit import write_audit
from utils.auth_utils import require_report_access, require_admin
from utils.settings import get_reports_config
from utils import settings as app_settings   # v7p
from services import ota_service
from services import folio_resolver
from utils import clock, gst          # F-03: one clock - see utils/clock.py

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


def _gst_split(pairs, discount=0.0):
    """Split (gst_percent, gross_inclusive) sale lines into taxable/CGST/SGST per slab.

    v6d (F-11): this is now a thin wrapper over `utils/gst.split`, which the invoice uses too.
    It used to be a second copy of the same arithmetic kept "in sync" by a comment — and the
    moment the discount treatment changed, that is exactly the pair that would have drifted and
    left the filing disagreeing with the documents. `discount` is a POSITIVE rupee figure and
    reduces the taxable value, apportioned across the slabs.

    Returns the full `utils.gst.split` dict: rows plus `taxable_total`, `cgst_total`,
    `sgst_total`, `gross_total` (before discount) and `net_total` (after it).
    """
    return gst.split(pairs, discount=discount)


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
    #
    # F-17: this printed 166.7% occupancy. Two things were wrong. It counted every live-status
    # booking overlapping the night, so reservations nobody ever arrived for (F-16) kept consuming
    # inventory for ever; and nothing capped the result, so an impossible number reached the owner
    # with no explanation. For a night ALREADY PAST, count what actually happened - a stay is
    # evidence of occupancy only if somebody checked in. For tonight and the future a reservation IS
    # committed inventory, so keep counting those. `oversold` says plainly when demand genuinely
    # exceeds the rooms, instead of letting the percentage say it illegibly.
    today = clock.business_today()
    rows = []
    for night in _days(dfrom, dto):
        statuses = ("checked_in", "checked_out") if night < today else _LIVE_STATUSES
        occ = (db.query(func.coalesce(func.sum(BookingItem.quantity), 0))
               .join(Booking, Booking.booking_id == BookingItem.booking_id)
               .filter(Booking.status.in_(statuses),
                       Booking.check_in <= night, Booking.check_out > night)).scalar()
        occ = int(occ or 0)
        pct = round(100.0 * min(occ, total_rooms) / total_rooms, 1) if total_rooms else 0.0
        rows.append({"date": str(night), "occupied": min(occ, total_rooms),
                     "available": max(0, total_rooms - occ),
                     "total_rooms": total_rooms, "occupancy_pct": pct,
                     "oversold": max(0, occ - total_rooms)})
    return {"group_by": "day", "from": str(dfrom), "to": str(dto),
            "total_rooms": total_rooms, "rows": rows}


def _charge_pairs(db, day):
    """(gst_percent, amount) for every non-void, non-payment, non-discount FolioCharge posted on
    `day` (the taxable sale lines)."""
    q = (db.query(FolioCharge.gst_percent, FolioCharge.amount)
         .filter(FolioCharge.void == False,  # noqa: E712
                 FolioCharge.type.notin_(("payment", "discount")),
                 clock.business_date_sql(FolioCharge.posted_at) == day))
    return [(g, a) for g, a in q.all()]


def _sum_charges(db, day, types=None, exclude=None):
    q = (db.query(func.coalesce(func.sum(FolioCharge.amount), 0))
         .filter(FolioCharge.void == False,  # noqa: E712
                 clock.business_date_sql(FolioCharge.posted_at) == day))
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
        conds.append(clock.business_date_sql(FolioCharge.posted_at) == day)
    if dfrom is not None:
        conds.append(clock.business_date_sql(FolioCharge.posted_at) >= dfrom)
    if dto is not None:
        conds.append(clock.business_date_sql(FolioCharge.posted_at) <= dto)
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
        sp = _gst_split(_charge_pairs(db, day), discount=discount)
        rows.append({"date": str(day), "room_revenue": room_rev, "other_revenue": other_rev,
                     "room_service": rs_rev,
                     # v6d (F-11): `gross_sales` is still what was billed BEFORE the discount, and
                     # `discount` is still shown separately — the owner reads those two as they
                     # always did. What changed is that taxable/cgst/sgst are now computed on the
                     # DISCOUNTED value, so `net_sales` (not `gross_sales`) is what they add up to.
                     "discount": discount, "gross_sales": sp["gross_total"],
                     "net_sales": sp["net_total"], "taxable": sp["taxable_total"],
                     "cgst": sp["cgst_total"], "sgst": sp["sgst_total"],
                     # v4b6 — see comp_room_value() below. NOT part of gross_sales.
                     "comp_room_value": round(comp_room_value(db, day), 2)})
    totals = {
        "room_revenue": round(sum(r["room_revenue"] for r in rows), 2),
        "other_revenue": round(sum(r["other_revenue"] for r in rows), 2),
        "room_service": round(sum(r["room_service"] for r in rows), 2),
        "discount": round(sum(r["discount"] for r in rows), 2),
        "gross_sales": round(sum(r["gross_sales"] for r in rows), 2),
        "net_sales": round(sum(r["net_sales"] for r in rows), 2),
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
            clock.business_date_sql(Payment.created_at) >= dfrom, clock.business_date_sql(Payment.created_at) <= dto).scalar() or 0)
    cash, card, upi, bank = _m("cash"), _m("card"), _m("upi"), _m("bank")
    total = round(cash + card + upi + bank, 2)
    # v7e: a refund belongs to the day it was PAID OUT, not the day of the payment it refunds
    _ron = func.coalesce(Payment.refunded_at, Payment.created_at)
    refunds = float(db.query(func.coalesce(func.sum(Payment.refund_amount), 0)).filter(
        Payment.refund_status == "completed",
        clock.business_date_sql(_ron) >= dfrom, clock.business_date_sql(_ron) <= dto).scalar() or 0)
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
        # No discount term: a folio-level discount belongs to the whole stay, not to the
        # room-service slice of it, and apportioning part of it here would double-count against
        # the sales report.
        sp = _gst_split(pairs)
        taxable, cgst, sgst, gross = (sp["taxable_total"], sp["cgst_total"],
                                      sp["sgst_total"], sp["gross_total"])
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
                               clock.business_date_sql(FolioCharge.posted_at) == day).subquery())
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
              .filter(clock.business_date_sql(CashShift.opened_at) >= dfrom,
                      clock.business_date_sql(CashShift.opened_at) <= dto)
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
    discount = 0.0
    for day in _days(dfrom, dto):
        pairs.extend(_charge_pairs(db, day))
        # v6d (F-11): the discounts given in the period reduce the taxable value. Summed over the
        # whole range and apportioned once, which is the same answer as apportioning per day only
        # because apportionment is linear in gross — worth stating, because it is why this report
        # and the day-by-day sales report still reconcile.
        discount += abs(_sum_charges(db, day, types=("discount",)))
    sp = _gst_split(pairs, discount=discount)
    return {"from": str(dfrom), "to": str(dto), "slabs": sp["rows"],
            "taxable_total": sp["taxable_total"], "cgst_total": sp["cgst_total"],
            "sgst_total": sp["sgst_total"], "gross_total": sp["gross_total"],
            "discount_total": sp["discount_total"], "net_total": sp["net_total"]}


def arrivals_departures_data(db, dfrom, dto):
    """Arrivals and departures per day, counted BOTH ways (F-17).

    This counted bookings only, so a family taking three rooms was one arrival — and housekeeping and
    the shift roster are planned off this number, where what matters is rooms. Both are reported now
    rather than swapping one for the other: the desk thinks in guests arriving, housekeeping thinks in
    rooms to turn around, and they are genuinely different questions.
    """
    def _count(date_col, day):
        bookings = int(db.query(func.count(Booking.booking_id)).filter(
            date_col == day, Booking.status.in_(_LIVE_STATUSES)).scalar() or 0)
        rooms = int(db.query(func.coalesce(func.sum(BookingItem.quantity), 0))
                    .join(Booking, Booking.booking_id == BookingItem.booking_id)
                    .filter(date_col == day, Booking.status.in_(_LIVE_STATUSES)).scalar() or 0)
        return bookings, rooms

    rows = []
    for day in _days(dfrom, dto):
        arr, arr_rooms = _count(Booking.check_in, day)
        dep, dep_rooms = _count(Booking.check_out, day)
        rows.append({"date": str(day), "arrivals": arr, "departures": dep,
                     "arrival_rooms": arr_rooms, "departure_rooms": dep_rooms})
    totals = {"arrivals": sum(r["arrivals"] for r in rows),
              "departures": sum(r["departures"] for r in rows),
              "arrival_rooms": sum(r["arrival_rooms"] for r in rows),
              "departure_rooms": sum(r["departure_rooms"] for r in rows)}
    return {"from": str(dfrom), "to": str(dto), "rows": rows, "totals": totals}


def in_house_data(db, dfrom=None, dto=None):
    """Current in-house (checked_in) bookings: guest, rooms, nights, folio balance.
    v8a (owner, "all reports by date range"): with `dfrom`/`dto`, every stay that was in house at any time
    in that range - checked in before it ended and not checked out before it began."""
    from models import Folio
    rows = []
    if dfrom is not None and dto is not None:
        _s, _e = clock.business_day_bounds(dfrom, dto)
        bookings = (db.query(Booking)
                    .filter(Booking.status.in_(("checked_in", "checked_out")),
                            Booking.checked_in_at.isnot(None),
                            Booking.checked_in_at < clock.to_local(_e),          # checked_in_at: hotel clock
                            (Booking.checked_out_at.is_(None)) | (Booking.checked_out_at >= _s))   # UTC
                    .order_by(Booking.checked_in_at).all())
    else:
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
        # v6e: a stay can carry one bill per room; this list names the first of them.
        _folios = folio_resolver.folios_for_booking(db, b.booking_id)
        folio = _folios[0] if _folios else None
        nights = max(1, (b.check_out - b.check_in).days)
        rows.append({
            "booking_id": b.booking_id,
            "guest_name": b.display_guest_name or None,
            "phone": guest.phone if guest else None,
            # v5s: ascending room order — this list IS the fire/safety roll-call
            # (compliance.evacuation renders these rows), so "303, 301" was not acceptable.
            "rooms": ", ".join(ordering.room_labels(room_labels)) if room_labels else "—",
            "check_in": str(b.check_in), "check_out": str(b.check_out), "nights": nights,
            "balance": float(folio.balance or 0) if folio else None,
            "status": b.status,                                                    # v8a: in house / left
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
              .filter(clock.business_date_sql(CashShift.opened_at) >= dfrom, clock.business_date_sql(CashShift.opened_at) <= dto)
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
         .filter(clock.business_date_sql(CardIssuance.issued_at) >= dfrom, clock.business_date_sql(CardIssuance.issued_at) <= dto)
         .order_by(CardIssuance.issued_at.desc()))
    rows = []
    for c in q.all():
        room = db.query(Room).filter(Room.room_id == c.room_id).first() if c.room_id else None
        rows.append({"card_id": c.id, "booking_id": c.booking_id,
                     "room": room.room_number if room else None,
                     "card_uid": c.card_uid, "card_type": c.card_type, "issue_type": c.issue_type,
                     "status": c.status, "issued_by": _user_name(db, c.issued_by),
                     "station_id": c.station_id,
                     "issued_at": clock.to_local(c.issued_at).isoformat() if c.issued_at else None,
                     "valid_to": c.valid_to.isoformat() if c.valid_to else None})
    by_type = {}
    for r in rows:
        by_type[r["card_type"]] = by_type.get(r["card_type"], 0) + 1
    return {"from": str(dfrom), "to": str(dto), "count": len(rows), "by_type": by_type, "rows": rows}


def _lost_card_fee_decision(db, card, fee, amount):
    """v7g (owner: "for lost key card how is reception user able to charge as free / make void"):
    say WHO decided the fee and on what authority. A reception waiver needs the owner's approval
    code; an admin may waive; a charged fee can be voided (owner code for reception)."""
    import json as _json
    from models import AuditLog, OwnerOtp
    if amount:
        return "", ""
    if fee is not None and fee.void:
        who, why = "", (fee.void_reason or "")
        for lg in (db.query(AuditLog).filter(AuditLog.action == "folio.charge_void")
                   .order_by(AuditLog.created_at.desc()).limit(500).all()):
            try:
                bf = _json.loads(lg.before) if isinstance(lg.before, str) else (lg.before or {})
            except Exception:
                bf = {}
            if str(bf.get("charge_id")) == str(fee.id):
                who = _user_name(db, lg.user_id) or ""
                break
        return (f"voided by {who}" if who else "voided"), why
    skipped = ""
    for lg in (db.query(AuditLog).filter(AuditLog.entity_id == str(card.id),
                                         AuditLog.action.like("card%"))
               .order_by(AuditLog.created_at.desc()).all()):
        try:
            af = _json.loads(lg.after) if isinstance(lg.after, str) else (lg.after or {})
        except Exception:
            af = {}
        if af.get("fee_skipped") or af.get("fee_note"):
            skipped = af.get("fee_skipped") or af.get("fee_note") or ""
            break
    issuer = db.query(User).filter(User.user_id == card.issued_by).first() if card.issued_by else None
    if skipped.startswith("waived"):
        otp = None
        if card.issued_at and card.issued_by:
            otp = (db.query(OwnerOtp).filter(OwnerOtp.action == "lost_card_waive", OwnerOtp.used == True,  # noqa: E712
                                             OwnerOtp.used_by == card.issued_by,
                                             OwnerOtp.used_at >= card.issued_at - timedelta(minutes=10),
                                             OwnerOtp.used_at <= card.issued_at + timedelta(minutes=10))
                   .order_by(OwnerOtp.used_at.desc()).first())
        role = (issuer.role if issuer else "") or ""
        auth = (f"owner approval code #{otp.id}" if otp else
                "admin login" if role == "admin" else "before the owner-approval rule (4 Oct)")
        return f"waived by {_user_name(db, card.issued_by) or '?'}", f"{skipped[len('waived:'):].strip() or '-'} - {auth}"
    return "system", skipped or "not charged"


def maintenance_detail_table(d):
    """(columns, rows, totals) of the maintenance detail - the EOD e-mail and the owner's HK report."""
    cols = ["Ticket", "Location", "Issue", "Status", "Fixed", "Inspected", "Raised by", "Parts ₹"]
    rows = [[r["ticket_id"], r["location"], r["issue"], r["status"], "Yes" if r["fixed"] else "No",
             "Yes" if r["inspected"] else "No", r.get("raised_by") or "", r["parts_cost"]] for r in d["rows"]]
    t = d["totals"]
    totals = [f"{t['tickets']} ticket(s)", f"{t['fixed']} fixed", "", "", "", "", "", t["parts_cost"]]
    return cols, rows, totals


def build_hk_owner_report(db, day) -> tuple:
    """v7g: the owner's daily housekeeping report for one business day -> (pdf_path, caption)."""
    from utils.report_pdf import generate_hk_owner_report_pdf
    rd = room_detail_data(db, day, day)
    rd_cols, rd_rows = room_detail_table(rd)
    flags = [bool(r.get("has_fraud")) for r in rd["rows"]]
    mt = maintenance_detail_table(maintenance_detail_data(db, day, day))
    lc = lost_cards_table(lost_cards_data(db, day, day))
    # v7p (owner): the Room Turnaround & Cards KPIs and red flags for the same day, in the same PDF
    ta = room_turnaround_data(db, day, day)
    pdf = generate_hk_owner_report_pdf(day, (rd_cols, rd_rows, flags), mt, lc, turnaround=ta)
    caption = (f"Hotel Bhimas - housekeeping report {day:%d %b}: {len(rd_rows)} room(s) checked out, "
               f"{sum(flags)} with a fraud alert, {ta['kpis']['suspicious_stays']} stay(s) and "
               f"{ta['kpis']['suspicious_viewings']} viewing(s) with a red flag, {len(mt[1])} maintenance ticket(s), "
               f"{len(lc[1])} lost key card(s). PDF attached.")
    return pdf, caption


def lost_cards_table(d):
    """v7g: (columns, rows, totals) - with who decided the fee and why."""
    cols = ["Reissued", "Room", "Guest", "Lost card", "Cut by", "Fee", "Status", "Fee decided by", "Reason / approval"]
    rows = [[(r["reissued_at"] or "")[:16].replace("T", " "), r["room"] or "—", r["guest"] or "—",
             r["lost_card_id"] or "—", r["issued_by"] or "—", r["fee"], r["fee_status"],
             r.get("fee_decided_by") or "", r.get("fee_reason") or ""] for r in d["rows"]]
    totals = ["TOTAL", "", "", "", "", d["charged_total"], f"{d['uncharged']} not charged", "", ""]
    return cols, rows, totals


def lost_cards_data(db, dfrom, dto):
    """Key cards reported lost in the range, and what was charged for them (v6f).

    One row per LOST card, not per replacement: the row the guest lost is the event, and the
    replacement carries the link back (`replaces_card_id`) and the fee (`fee_charge_id`). A fee
    that was waived, or not configured, shows as 0 with the reason the desk gave — the owner is
    reading this to see how often cards go missing AND how often nobody charged for it.
    """
    from models import FolioCharge
    q = (db.query(CardIssuance)
         .filter(CardIssuance.issue_type == "lost_reissue",
                 clock.business_date_sql(CardIssuance.issued_at) >= dfrom,
                 clock.business_date_sql(CardIssuance.issued_at) <= dto)
         .order_by(CardIssuance.issued_at.desc()))
    rows, charged = [], 0.0
    for c in q.all():
        room = db.query(Room).filter(Room.room_id == c.room_id).first() if c.room_id else None
        booking = (db.query(Booking).filter(Booking.booking_id == c.booking_id).first()
                   if c.booking_id else None)
        fee = (db.query(FolioCharge).filter(FolioCharge.id == c.fee_charge_id).first()
               if c.fee_charge_id else None)
        # A voided fee counts as not charged — that is the whole reason the link exists.
        amount = round(float(fee.amount or 0), 2) if (fee and not fee.void) else 0.0
        charged = round(charged + amount, 2)
        decided_by, why = _lost_card_fee_decision(db, c, fee, amount)
        rows.append({
            "fee_decided_by": decided_by,     # v7g (owner): who waived / voided it
            "fee_reason": why,                # ...and why, with the owner's approval code
            "reissued_at": clock.to_local(c.issued_at).isoformat() if c.issued_at else None,
            "room": room.room_number if room else None,
            "guest": (booking.display_guest_name if booking else None),
            "booking_id": c.booking_id,
            "lost_card_id": c.replaces_card_id,
            "new_card_id": c.id,
            "issued_by": _user_name(db, c.issued_by),
            "fee": amount,
            "fee_status": ("charged" if amount else
                           "voided" if (fee and fee.void) else "not charged"),
        })
    return {"from": str(dfrom), "to": str(dto), "count": len(rows),
            "charged_total": charged,
            "uncharged": len([r for r in rows if not r["fee"]]),
            "rows": rows,
            "totals": {"fee": charged}}


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

    # F-03: these filter created_at / posted_at / issued_at / closed_at, which are stored UTC, so
    # the window has to be the UTC span of those BUSINESS days - not a naive local midnight, which
    # moved every figure here by 5.5 h. Half-open, so `< end_dt` below stays correct.
    start_dt, end_dt = clock.business_day_bounds(dfrom, dto)
    UPSELL_TYPES = ("food", "minibar", "laundry", "extra_bed", "extra_person")

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
    """Fraud-alert summary over alerts detected in the range (hotel days): counts by type/severity/status
    + every alert in plain language.

    v7p (owner): "make fraud summary readable - the owner should clearly be able to understand what had
    happened, how". Each row now says what happened (room, times, people, money), why it matters and
    whether anyone has looked at it (utils/fraud_explain); `kinds` groups them, commonest first."""
    from utils.fraud_explain import explain
    q = (db.query(FraudAlert)
         .filter(clock.business_date_sql(FraudAlert.detected_at) >= dfrom,
                 clock.business_date_sql(FraudAlert.detected_at) <= dto)
         .order_by(FraudAlert.detected_at.desc()))
    alerts = q.all()
    by_type, by_severity, by_status = {}, {}, {}
    rows, kinds, cache = [], {}, {}
    for a in alerts:
        by_type[a.type] = by_type.get(a.type, 0) + 1
        by_severity[a.severity] = by_severity.get(a.severity, 0) + 1
        by_status[a.status] = by_status.get(a.status, 0) + 1
        e = explain(db, a, cache)
        rows.append({"id": a.id, "type": a.type, "severity": a.severity, "status": a.status,
                     "booking_id": a.booking_id, "room_id": a.room_id, "room": e["room"],
                     "guest": e["guest"], "staff": e["staff"], "title": e["title"],
                     "what_happened": e["sentence"], "meaning": e["meaning"], "check": e["check"],
                     "status_text": e["status_text"], "when": e["when"],
                     "detected_at": clock.to_local(a.detected_at).isoformat() if a.detected_at else None})
        k = kinds.setdefault(a.type, {"type": a.type, "title": e["title"], "meaning": e["meaning"],
                                      "check": e["check"], "count": 0, "open": 0})
        k["count"] += 1
        k["open"] += 1 if a.status == "open" else 0
    return {"from": str(dfrom), "to": str(dto), "count": len(alerts),
            "open": sum(1 for a in alerts if a.status == "open"),
            "kinds": sorted(kinds.values(), key=lambda k: (-k["count"], k["title"])),
            "by_type": by_type, "by_severity": by_severity, "by_status": by_status, "rows": rows}


def fraud_summary_table(data):
    """v7p: grouped by kind of alert - a band says what it is, how many, why it matters and what to
    check; under it, each alert as a sentence."""
    cols = ["When", "Room", "Guest / booking", "What happened", "Staff", "Status"]
    rows = []
    for k in data["kinds"]:
        # v8a (owner): "display the reason properly, not clumsy" - a short band, then why it matters and what
        # to check on their own lines, then one row per alert without repeating the room in the sentence
        rows.append({"section": f"{k['title']} - {k['count']} alert{'s' if k['count'] != 1 else ''}"
                                f" ({k['open']} not reviewed)"})
        if k.get("meaning") or k.get("check"):
            rows.append(["", "", "", "\n".join(x for x in (
                f"Why it matters: {k['meaning']}" if k.get("meaning") else "",
                f"What to check: {k['check']}" if k.get("check") else "") if x), "", ""])
        for r in data["rows"]:
            if r["type"] != k["type"]:
                continue
            gb = " · ".join(x for x in (r.get("guest"), f"#{r['booking_id']}" if r.get("booking_id") else None) if x)
            what = r["what_happened"] or ""
            _pre = f"Room {r.get('room')}: "
            if r.get("room") and what.startswith(_pre):
                what = what[len(_pre)].upper() + what[len(_pre) + 1:]
            rows.append([r["when"], r.get("room") or "—", gb or "—", what,
                         r.get("staff") or "—", r["status_text"]])
    return cols, rows


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
        for idx, it in enumerate(items):
            r = rooms_by_id.get(it.room_id)
            if not r:
                continue
            # v6f: this ROOM's own arrival and departure. A stay whose rooms arrived in two cars
            # has two of each, and the booking's are the FIRST room's - so every later room was
            # reported as having arrived and left at times it did not.
            arr = it.checked_in_at or b.checked_in_at
            dep = (booking_checkout_moment(b, item=it) if booking_checkout_moment else None)
            share = racks[idx] / rack_total
            rt = rt_by_id.get(it.room_type_id)
            row = {
                "room_no": r.room_number, "floor": int(r.floor or 0),
                "guest": b.display_guest_name or None,
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
        # v5s: room_no is a text label, so this sorted 10 before 9 within a floor.
        "rooms": sorted(rows, key=lambda x: (x["floor"],
                                             ordering.room_number_sort_key(x["room_no"]))),
        "floor_summary": [floor_summary[f] for f in sorted(floor_summary)],
        "by_source": _summarise(seg_acc),
        "by_type": _summarise(type_acc),
        "totals": {"occ_rooms": len(rows), "pax": sum(r["pax"] for r in rows),
                   "revenue": total_rev,
                   "arr": round(total_rev / len(rows), 2) if rows else 0.0},
    }


_CASHIER_MODE_LABELS = {"cash": "Cash", "card": "C.Card", "upi": "UPI", "bank": "Bank",
                        "other": "Other", "online": "Online (website)",
                        "prepaid": "Prepaid before arrival (OTA / agent / website) - MEMO, not in drawer"}
_CASHIER_SECTIONS = [("checkout", "Guest Check-out Bills"),
                     ("due", "Due Received (pay-later bills)"),   # v7s
                     ("advance", "Advance Receipts"),
                     ("paidout", "Paid-outs"),
                     ("prepaid", "Prepaid Receipts (issued at check-in)")]
# v6m.13: modes listed for information only - never added to the grand total or the drawer
_CASHIER_MEMO_MODES = {"prepaid"}


def _online_payment(p) -> bool:
    """A guest's own website payment: an online gateway, and nobody at the desk took it."""
    return ((p.gateway or "") in ("razorpay", "phonepe") and not p.collected_by
            and not getattr(p, "collect_method", None))


def cashier_summary_data(db, day, day_to=None):
    """Front-office cashier summary for one day, in the legacy printout shape: paid Payments grouped
    by payment MODE (cash/card/upi/bank), each split into Guest Check-out Bills / Advance Receipts /
    Paid-outs, with per-row Bill/Vou no, room, guest, receipt, payment, balance, CR no, remarks, user.
    Plus per-mode totals, a grand total, unsettled checkout bills, and nil-amount checkouts. Uses our
    invoice + payment ids for the bill/voucher references."""
    # v7h (owner): "for selected dates" - a range as well as one day
    day_to = day_to or day
    _bd = clock.business_date_sql
    pays = (db.query(Payment).filter(Payment.status == "paid",
                                     _bd(Payment.created_at) >= day, _bd(Payment.created_at) <= day_to)
            .order_by(Payment.created_at).all())
    from models import PrepaidReceipt, Expense, Invoice
    prepaid = (db.query(PrepaidReceipt)
               .filter(_bd(PrepaidReceipt.issued_at) >= day, _bd(PrepaidReceipt.issued_at) <= day_to,
                       PrepaidReceipt.kind != "website")       # v7t: already listed the day it was paid
               .order_by(PrepaidReceipt.issued_at).all())
    expenses = (db.query(Expense).filter(_bd(Expense.created_at) >= day, _bd(Expense.created_at) <= day_to)
                .order_by(Expense.created_at).all())
    _s, _e = clock.business_day_bounds(day, day_to)
    # v7e: paid-outs are the refunds PAID OUT this day - a refund today of yesterday's payment was
    # missing from today and filed under yesterday
    _ron = func.coalesce(Payment.refunded_at, Payment.created_at)
    refunds_today = (db.query(Payment).filter(Payment.refund_status == "completed",
                                              _bd(_ron) >= day, _bd(_ron) <= day_to)
                     .order_by(_ron).all())
    # v7h (owner): "all invoices for the shift should come in shift summary and in cashier summary"
    invoices = (db.query(Invoice).filter(_bd(Invoice.created_at) >= day, _bd(Invoice.created_at) <= day_to)
                .order_by(Invoice.created_at).all())
    out = _cashier_summary_core(db, receipts=pays, refunds=refunds_today,
                                checkouts_day=(day if day_to == day else None),
                                checkouts_window=(None if day_to == day else (_s, _e)),
                                prepaid=prepaid, expenses=expenses, invoices=invoices,
                                in_house_at=min(_e, clock.now_utc()))
    return {"day": str(day), "day_to": str(day_to), **out}


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
    # v6m.13: a guest paying on the website while this shift was open is not money the desk took
    receipts = [p for p in receipts if not _online_payment(p)]
    refunds = (db.query(Payment)
               .filter(Payment.refund_status == "completed", Payment.refund_shift_id == shift.id)
               .order_by(Payment.payment_id).all())
    from models import PrepaidReceipt
    end = shift.closed_at or clock.now_utc()
    prepaid = (db.query(PrepaidReceipt)
               .filter(PrepaidReceipt.issued_at >= shift.opened_at, PrepaidReceipt.issued_at < end,
                       PrepaidReceipt.kind != "website")       # v7t: already listed the day it was paid
               .order_by(PrepaidReceipt.issued_at).all())
    # v7d (owner): the shift report lists the bills checked out during the shift too - unsettled,
    # and settled with no receipt (nil amount) - with who checked them out.
    from models import Expense
    expenses = db.query(Expense).filter(Expense.shift_id == shift.id).order_by(Expense.created_at).all()
    from models import Invoice
    invoices = (db.query(Invoice).filter(Invoice.created_at >= shift.opened_at, Invoice.created_at < end)
                .order_by(Invoice.created_at).all())          # v7h: the invoices raised in this shift
    out = _cashier_summary_core(db, receipts=receipts, refunds=refunds, checkouts_day=None, prepaid=prepaid,
                                checkouts_window=(shift.opened_at, end), expenses=expenses, in_house_at=end,
                                invoices=invoices)
    return {"shift_id": shift.id, **out}


def _checked_out_bills(db, day=None, window=None):
    """v7e: every BILL that closed on `day` (business date) or inside `window` (UTC [start, end)):
    [(booking, folio, closed_at_utc, by_user_id)], oldest first. A per-room bill closes when ITS room
    leaves; a group bill when the booking does."""
    from models import Folio
    if day is None and window is None:
        return []
    out = []
    q1 = (db.query(Folio, BookingItem, Booking)
          .join(BookingItem, BookingItem.booking_item_id == Folio.booking_item_id)
          .join(Booking, Booking.booking_id == Folio.booking_id)
          .filter(BookingItem.checked_out_at.isnot(None)))
    q2 = (db.query(Folio, Booking).join(Booking, Booking.booking_id == Folio.booking_id)
          .filter(Folio.booking_item_id.is_(None), Booking.status == "checked_out"))
    if day is not None:
        q1 = q1.filter(clock.business_date_sql(BookingItem.checked_out_at) == day)
        q2 = q2.filter(clock.business_date_sql(Booking.checked_out_at) == day)
    else:
        ws, we = window
        q1 = q1.filter(BookingItem.checked_out_at >= ws, BookingItem.checked_out_at < we)
        q2 = q2.filter(Booking.checked_out_at >= ws, Booking.checked_out_at < we)
    for f, it, b in q1.all():
        out.append((b, f, it.checked_out_at, getattr(it, "checked_out_by", None) or getattr(b, "checked_out_by", None)))
    for f, b in q2.all():
        out.append((b, f, b.checked_out_at, getattr(b, "checked_out_by", None)))
    out.sort(key=lambda r: (r[2] or datetime.min, r[1].id))
    return out


def _bill_room_label(db, folio):
    """The room a per-room bill belongs to (None for a group bill)."""
    if folio is None or not getattr(folio, "booking_item_id", None):
        return None
    it = db.query(BookingItem).filter(BookingItem.booking_item_id == folio.booking_item_id).first()
    if it is None or not it.room_id:
        return None
    r = db.query(Room).filter(Room.room_id == it.room_id).first()
    return r.room_number if r else None


def _in_house_rows(db, at_utc):
    """v7e: the rooms occupied at `at_utc` (end of the day / of the shift, or now) - one row per room."""
    at_local = clock.to_local(at_utc)
    rows = []
    q = (db.query(BookingItem, Booking, Room)
         .join(Booking, Booking.booking_id == BookingItem.booking_id)
         .join(Room, Room.room_id == BookingItem.room_id)
         .filter(BookingItem.room_id.isnot(None), BookingItem.checked_in_at.isnot(None),
                 BookingItem.checked_in_at <= at_local,          # checked_in_at is the hotel's wall clock
                 (BookingItem.checked_out_at.is_(None)) | (BookingItem.checked_out_at > at_utc),
                 Booking.status.in_(("checked_in", "checked_out"))))
    for it, b, r in q.all():
        from models import Folio as _Folio
        f = (db.query(_Folio).filter(_Folio.booking_item_id == it.booking_item_id).first()
             or folio_resolver.primary_folio(db, b.booking_id))
        rows.append({"room": r.room_number, "booking_id": b.booking_id, "guest": b.display_guest_name or None,
                     "checked_in": it.checked_in_at.strftime("%d/%m/%y %H:%M") if it.checked_in_at else "",
                     "check_out": str(it.check_out or b.check_out or ""),
                     "balance": round(float(f.balance or 0), 2) if f else 0.0})
    rows.sort(key=lambda x: ordering.room_number_sort_key(x["room"]))
    return rows


def _cashier_summary_core(db, receipts, refunds, checkouts_day=None, prepaid=None, checkouts_window=None,
                          expenses=None, in_house_at=None, invoices=None):
    """Shared body of the two cashier summaries. `receipts` = paid Payment rows to list as
    check-out bills / advances; `refunds` = Payment rows whose completed refund is a paid-out.
    `checkouts_day` adds the unsettled / nil-amount checkout sections for that calendar day."""
    from models import Folio, TravelAgent
    from routers.folio import _get_invoice
    from services import dues as _dues          # v7s
    from utils.booking_source import source_label as _srclabel
    rcache = {}   # booking_id -> room label
    scache = {}   # booking_id -> source label (v7n)
    modes = {}    # mode -> {section -> [rows], "receipt","payment"}

    def _src(b):
        """v7n (owner): where the stay came from - Walk-in, MakeMyTrip, Website, Agent ..."""
        if b is None:
            return ""
        if b.booking_id not in scache:
            try:
                scache[b.booking_id] = _srclabel(db, b) or ""
            except Exception:
                scache[b.booking_id] = (b.booking_source or "").replace("_", " ").title()
        return scache[b.booking_id]

    def _mode(m):
        return modes.setdefault(m, {"checkout": [], "due": [], "advance": [], "paidout": [], "prepaid": [],
                                    "receipt": 0.0, "payment": 0.0})

    def _ctx(p):
        b = db.query(Booking).filter(Booking.booking_id == p.booking_id).first() if p.booking_id else None
        guest = db.query(Guest).filter(Guest.guest_id == b.guest_id).first() if b else None
        # A receipt names the stay's main bill; the money itself is tracked on Payment.
        folio = folio_resolver.primary_folio(db, p.booking_id) if p.booking_id else None
        room = _booking_room_label(db, p.booking_id, rcache) if p.booking_id else "—"
        return b, guest, folio, room

    def _base(p, at):
        b, guest, folio, room = _ctx(p)
        return b, folio, {
            "date": at.strftime("%d/%m/%y") if at else "", "time": at.strftime("%H:%M") if at else "",
            "room": room, "guest": (b.display_guest_name if b else None), "booking_id": p.booking_id,
            "source": _src(b),
            "balance": round(float(folio.balance or 0), 2) if folio else 0.0,
            "due": _dues.outstanding(folio) if folio else 0.0,     # v7s: shown as DUE, not balance
            # CR No = the card/UPI transaction reference (gateway txn id), blank for manual cash.
            # (client_ref is the desk's offline-outbox dedupe UUID — internal, not a cashier ref.)
            "cr_no": p.payment_id_gateway or "",
            "user": _user_name(db, p.collected_by)}

    # v6m.12: the printed time is the hotel's clock (payments are stored UTC).
    _loc = clock.to_local
    _win_cache = {}      # v7u: booking_id -> its check-out windows

    for p in receipts:
        # v6m.13: a website payment has no desk method - call it what it is, not "Other"
        m = (p.method or ("online" if _online_payment(p) else "other"))
        at = p.created_at
        b, folio, base = _base(p, _loc(at))
        amt = float(p.amount or 0)
        if amt:
            # A receipt taken at/after the guest's checkout moment settles their bill; earlier
            # ones are advances. (Relaxed from "checked out the same day" so a bill settled the
            # morning after still files as a check-out bill on that day's/shift's summary.)
            # F-03: `at` is Payment.created_at (stored UTC); `checked_out_at` is the hotel's
            # wall-clock checkout moment (local, because the 24 h stay clock and the card expiry
            # are anchored to it). Comparing them raw made the payment look 5.5 h EARLIER than the
            # checkout it settled, so this test was false for every normal checkout and the whole
            # cashier summary filed settlements under ADVANCE.
            # v6m.12: both are UTC instants (checked_out_at is written with now_utc). The desk takes
            # the settling payment BEFORE it calls checkout (checkout refuses a balance), so a strict
            # "paid after checkout" would file every real settlement as ADVANCE. A payment within
            # _SETTLE_WINDOW before the checkout counts as its settlement - the same 5 h 30 min the
            # old local-vs-UTC comparison allowed by accident, now on purpose.
            is_checkout = _is_checkout_receipt(p, b, db, _win_cache)
            mm = _mode(m)
            if _dues.is_due_payment(p):
                # v7s: money received later against a pay-later bill - that bill's own row
                _df = db.query(Folio).filter(Folio.id == _dues.due_payment_folio_id(p)).first()
                _dinv = _get_invoice(db, _df.id) if _df else None
                _ag = (db.query(TravelAgent).filter(TravelAgent.id == b.agent_id).first()
                       if b is not None and b.agent_id else None)
                mm["due"].append({**base, "bill_no": f"RCPT-{p.payment_id}",
                                  "room": (_bill_room_label(db, _df) or base["room"]),
                                  "due": _dues.outstanding(_df) if _df else 0.0,
                                  "balance": round(float(_df.balance or 0), 2) if _df else 0.0,
                                  "receipt": round(amt, 2), "payment": 0.0,
                                  "remarks": (f"DUE RECEIVED - bill {_dinv.invoice_no if _dinv else _df.id if _df else ''}"
                                              + (f" - {_ag.name}" if _ag else ""))})
            elif is_checkout:
                inv = _get_invoice(db, folio.id) if folio else None
                mm["checkout"].append({**base, "bill_no": (inv.invoice_no if inv else f"F{folio.id}" if folio else "—"),
                                       "receipt_no": f"RCPT-{p.payment_id}",   # v8h: listed by receipt number
                                       "receipt": round(amt, 2), "payment": 0.0,
                                       "remarks": (p.collect_method or "").upper() or "CHECKOUT"})
            else:
                # Match the printed receipt document number (routers/payments.receipt_payload).
                mm["advance"].append({**base, "bill_no": f"RCPT-{p.payment_id}",
                                      "receipt": round(amt, 2), "payment": 0.0,
                                      # v7t (owner): the website advance receipt, on the day it was paid
                                      "remarks": ("WEBSITE ADVANCE" + (f" ({p.gateway.title()})" if p.gateway else "")
                                                  if _online_payment(p) else "ADVANCE")})
            mm["receipt"] += amt

    for p in refunds:
        rf = float(p.refund_amount or 0)
        if rf and p.refund_status == "completed":
            # v6m.12: the time and the user of the REFUND, not of the original payment
            _, _, base = _base(p, _loc(getattr(p, "refunded_at", None) or p.created_at))
            if getattr(p, "refunded_by", None):
                base["user"] = _user_name(db, p.refunded_by)
            mm = _mode(p.refund_mode or p.method or ("online" if _online_payment(p) else "other"))
            # Match the printed refund-voucher number; CR No = the refund's own payout reference.
            _bk = db.query(Booking).filter(Booking.booking_id == p.booking_id).first() if p.booking_id else None
            _cancelled = _bk is not None and _bk.status in ("cancelled", "admin_cancelled")
            mm["paidout"].append({**base, "bill_no": f"RFND-{p.payment_id}",
                                  "cr_no": p.refund_reference or p.refund_id or "",
                                  "receipt": 0.0, "payment": round(rf, 2),
                                  # v7t (owner): a cancelled booking's refund, named as one
                                  "remarks": (("CANCELLED BOOKING - REFUND" + (f": {p.refund_reason}" if p.refund_reason else ""))[:120]
                                              if _cancelled else (p.refund_reason or "REFUND PAIDOUT"))})
            mm["payment"] += rf

    # v7e: petty-cash expenses are cash paid OUT of the drawer - paid-outs, like the legacy summary.
    # They were missing from this report entirely (production: ~Rs 8,000 a day).
    for e in (expenses or []):
        amt = float(e.amount or 0)
        if not amt:
            continue
        at = _loc(e.created_at)
        mc = _mode("cash")
        mc["paidout"].append({"date": at.strftime("%d/%m/%y") if at else "", "time": at.strftime("%H:%M") if at else "",
                              "room": "—", "guest": None, "booking_id": None, "balance": 0.0,
                              "source": "Expense",
                              "bill_no": f"EXP-{e.id}", "cr_no": getattr(e, "receipt_url", None) or "",
                              "receipt": 0.0, "payment": round(amt, 2),
                              "remarks": f"EXPENSE - {(e.category or '').upper()}: {e.description or ''}"[:120],
                              "user": _user_name(db, e.created_by)})
        mc["payment"] += amt

    # v6m.13 (owner, item 4): money paid BEFORE arrival, receipted at check-in. A memo - the hotel
    # did not take it at the desk, so it is listed but never added to the totals or the drawer.
    if prepaid:
        from services.prepaid_receipts import mode_label, receipt_no
        mp = _mode("prepaid")
        for pr in prepaid:
            b = db.query(Booking).filter(Booking.booking_id == pr.booking_id).first()
            folio = folio_resolver.primary_folio(db, pr.booking_id)
            at = _loc(pr.issued_at)
            mp["prepaid"].append({
                "date": at.strftime("%d/%m/%y") if at else "", "time": at.strftime("%H:%M") if at else "",
                "room": _booking_room_label(db, pr.booking_id, rcache),
                "guest": (b.display_guest_name if b else None), "booking_id": pr.booking_id,
                "source": _src(b),
                "balance": round(float(folio.balance or 0), 2) if folio else 0.0,
                "bill_no": receipt_no(pr), "cr_no": pr.reference or "",
                "receipt": round(float(pr.amount or 0), 2), "payment": 0.0,
                "remarks": mode_label(pr).upper(), "user": _user_name(db, pr.issued_by)})
            mp["receipt"] += float(pr.amount or 0)

    # v7n (owner): each section in document-number order - RCPT-9 before RCPT-10, RFND, PRE, EXP,
    # the INV/... series - not in the order the money happened to be taken.
    for v in modes.values():
        for k in ("checkout", "due", "advance", "paidout", "prepaid"):
            v[k].sort(key=lambda x: doc_number_key(x.get("bill_no")))
    mode_rows = []
    for m in sorted(modes, key=lambda k: (k in _CASHIER_MEMO_MODES, k)):
        v = modes[m]
        mode_rows.append({
            "mode": m, "label": _CASHIER_MODE_LABELS.get(m, m.title()),
            "sections": [{"key": k, "label": lbl, "rows": v[k]} for k, lbl in _CASHIER_SECTIONS if v[k]],
            "receipt": round(v["receipt"], 2), "payment": round(v["payment"], 2),
            "net": round(v["receipt"] - v["payment"], 2),
            "memo": m in _CASHIER_MEMO_MODES,
        })
    counted = [x for x in mode_rows if not x["memo"]]
    grand = {"receipt": round(sum(x["receipt"] for x in counted), 2),
             "payment": round(sum(x["payment"] for x in counted), 2),
             "net": round(sum(x["net"] for x in counted), 2),
             "prepaid_memo": round(sum(x["receipt"] for x in mode_rows if x["memo"]), 2)}

    # checked out today: unsettled (folio balance != 0) vs nil-amount (settled, no receipt today)
    unsettled, nil_checkouts, due_bills = [], [], []
    receipted_bookings = {p.booking_id for p in receipts if float(p.amount or 0)}
    # v7e: BILL by bill, at the moment that bill closed. A room on a per-room stay that leaves on its
    # own has its own bill, closed when THAT room left - it used to wait for the last room, and so
    # was missing from the day (and shift) it was actually settled in.
    for b, f, out_at, by in _checked_out_bills(db, day=checkouts_day, window=checkouts_window):
        room = (_bill_room_label(db, f) or _booking_room_label(db, b.booking_id, rcache))
        bal = round(float(f.balance or 0), 2) if f else 0.0
        inv = _get_invoice(db, f.id) if f else None
        _out = _loc(out_at)
        rowc = {"bill_no": (inv.invoice_no if inv else f"F{f.id}" if f else "—"),
                "room": room, "guest": (b.display_guest_name or None),
                "booking_id": b.booking_id, "balance": bal, "source": _src(b),
                # v7d (owner): when, and which reception user checked them out
                "date": _out.strftime("%d/%m/%y") if _out else "",
                "time": _out.strftime("%H:%M") if _out else "",
                "user": _user_name(db, by)}
        if f is not None and _dues.is_due_bill(f):
            # v7s (owner): a pay-later bill is shown as DUE (with its agent), never as unsettled
            _ag = db.query(TravelAgent).filter(TravelAgent.id == b.agent_id).first() if b.agent_id else None
            due_bills.append({**rowc, "due": round(float(f.due_amount or 0), 2),
                              "due_outstanding": _dues.outstanding(f), "agent": _ag.name if _ag else ""})
        elif bal != 0:
            unsettled.append(rowc)
        elif b.booking_id not in receipted_bookings:
            nil_checkouts.append(rowc)

    unsettled.sort(key=lambda x: doc_number_key(x.get("bill_no")))
    due_bills.sort(key=lambda x: doc_number_key(x.get("bill_no")))
    nil_checkouts.sort(key=lambda x: doc_number_key(x.get("bill_no")))

    # v7e (owner): "Cashier summary should also have rooms that are in house"
    in_house = _in_house_rows(db, in_house_at) if in_house_at is not None else []
    for x in in_house:
        x["source"] = _src(db.query(Booking).filter(Booking.booking_id == x["booking_id"]).first())

    # v7h (owner): every invoice raised in the period - an OTA stay settled by its channel, a bill
    # with no receipt at the desk, a per-room bill - so none is missing from the summary.
    inv_rows = []
    for inv in (invoices or []):
        b = db.query(Booking).filter(Booking.booking_id == inv.booking_id).first()
        f = db.query(Folio).filter(Folio.id == inv.folio_id).first()
        at = _loc(inv.created_at)
        try:
            from utils.booking_source import source_label as _src
            src = _src(db, b) if b else ""
        except Exception:
            src = ""
        prepaid_amt = round(float(getattr(b, "prepaid_amount", 0) or 0), 2) if b else 0.0
        inv_rows.append({
            "bill_no": inv.invoice_no, "booking_id": inv.booking_id,
            "date": at.strftime("%d/%m/%y") if at else "", "time": at.strftime("%H:%M") if at else "",
            "room": (_bill_room_label(db, f) or _booking_room_label(db, inv.booking_id, rcache)),
            "guest": (b.display_guest_name if b else None),
            "amount": round(float(inv.grand_total or 0), 2),
            "prepaid": prepaid_amt,
            # v7s: a pay-later bill shows what is STILL due (live), not the figure frozen on the invoice
            "balance": (_dues.outstanding(f) if f is not None and _dues.is_due_bill(f)
                        else round(float(inv.balance_due or 0), 2)),
            "due": _dues.outstanding(f) if f else 0.0,
            "source": src,
            "user": _user_name(db, inv.created_by),
        })

    inv_rows.sort(key=lambda x: doc_number_key(x.get("bill_no")))
    return {"modes": mode_rows, "grand": grand,
            "unsettled": unsettled, "nil_checkouts": nil_checkouts, "in_house": in_house,
            "due_bills": due_bills,
            "due_bills_total": round(sum(x["due"] for x in due_bills), 2),
            "invoices": inv_rows,
            "invoices_total": round(sum(x["amount"] for x in inv_rows), 2)}


def doc_number_key(no):
    """v7n: sort document numbers as numbers - RCPT-9 < RCPT-10, INV/2026-27/00009 < .../00010."""
    import re
    parts = re.split(r"(\d+)", str(no or ""))
    return tuple((0, int(p)) if p.isdigit() else (1, p) for p in parts if p != "")


def _bal_cell(x):
    """v7s (owner): "in the cashier summary show it as DUE, not balance" - a pay-later bill's unpaid
    amount prints as DUE."""
    return f"DUE {x['due']:,.2f}" if x.get("due") else x.get("balance")


_EXTENSION_PAY_GRACE = timedelta(minutes=30)   # v7u: the payment taken with an extension


def _checkout_windows(db, b):
    """v7u (owner, 17 Oct): "balance is payment in the 1 h before check-out - if the stay is extended by
    hours or by days, that should be handled". [(start_utc, end_utc)]: the hour before EVERY check-out
    moment the stay had - the original and each extended one (from the extension records) - plus the
    half hour after each extension was made (the guest pays the balance + the extension then), plus
    the hour before the actual check-out (and anything after it, as before)."""
    import json as _json
    from models import AuditLog
    wins = []
    if b.checked_out_at:
        wins.append((b.checked_out_at - _SETTLE_WINDOW, datetime.max))
    if db is None:
        return wins
    rows = (db.query(AuditLog).filter(AuditLog.entity_type == "booking", AuditLog.entity_id == str(b.booking_id),
                                      AuditLog.action.in_(("reception.extend", "reception.extend_hours")))
            .order_by(AuditLog.log_id).all())
    for a in rows:
        try:
            before = _json.loads(a.before) if a.before else {}
        except Exception:
            before = {}
        old = None
        if before.get("checkout_at"):
            try:
                old = clock.to_utc(datetime.fromisoformat(str(before["checkout_at"])[:19]))
            except Exception:
                old = None
        if old is None and before.get("check_out"):
            # an older day-extension record has only the date: the stay's check-out time of day
            try:
                from routers.reception import _checkout_mode, _checkout_hour
                from services import ota_service
                d = date.fromisoformat(str(before["check_out"])[:10])
                if ota_service.is_ota_source(b.booking_source):
                    tod = datetime.min.replace(hour=12).time()
                elif _checkout_mode() == "fixed":
                    tod = datetime.min.replace(hour=_checkout_hour()).time()
                else:
                    tod = (b.checked_in_at or datetime.combine(d, datetime.min.replace(hour=12).time())).time()
                old = clock.to_utc(datetime.combine(d, tod))
            except Exception:
                old = None
        if old is not None:
            wins.append((old - _SETTLE_WINDOW, old))
        if a.created_at is not None:
            wins.append((a.created_at, a.created_at + _EXTENSION_PAY_GRACE))
    return wins


def _is_checkout_receipt(p, b, db=None, _cache=None) -> bool:
    """A receipt that settled the guest's bill at check-out (vs an advance). The desk takes the
    settling payment BEFORE it calls checkout (checkout refuses a balance), so a payment within
    _SETTLE_WINDOW before the ACTUAL check-out moment counts as its settlement (v6m.12). v7s: a
    payment taken in the check-in flow (client_ref "ci...") is an advance however short the stay.
    v7t (owner, 17 Oct): the window is ONE hour (was 5 h 30 min)."""
    if b is None or not b.checked_out_at or not p.created_at:
        return False
    if (p.client_ref or "").startswith("ci"):
        return False
    # v7u: the hour before ANY check-out moment the stay had (extensions), not only the last one
    if _cache is not None:
        if b.booking_id not in _cache:
            _cache[b.booking_id] = _checkout_windows(db, b)
        wins = _cache[b.booking_id]
    else:
        wins = _checkout_windows(db, b)
    return any(s <= p.created_at <= e for s, e in wins)


_SETTLE_WINDOW = timedelta(hours=1)        # v7t (owner): 1 h before the actual check-out


# v8h (owner, 21 Oct): "payment source like UPI, cash, bank as COLUMNS rather than split by rows, and
# order all bills by receipt number". One row per receipt / voucher, its amount in its mode's column
# (paid-outs negative), a Total column, and everything in receipt-number order: RCPT-n first, then
# the refund vouchers (RFND), the expenses (EXP) and the prepaid memos (PRE).
_CASHIER_KIND = {"checkout": "Check-out", "due": "Due received", "advance": "Advance",
                 "paidout": "Paid out", "prepaid": "Prepaid (memo)"}
_SERIES_RANK = {"RCPT": 0, "RFND": 1, "EXP": 2, "PRE": 3}
# the column heading and left-to-right order of each payment mode (memo modes last, never in Total)
_CASHIER_MODE_COLS = {"cash": "Cash", "upi": "UPI", "card": "Card", "bank": "Bank",
                      "online": "Online", "other": "Other", "prepaid": "Prepaid (memo)"}
_CASHIER_MODE_ORDER = list(_CASHIER_MODE_COLS)


def _mode_col(m):
    return _CASHIER_MODE_COLS.get(m["mode"]) or (m["label"] + (" (memo)" if m.get("memo") else ""))


def _receipt_order_key(no):
    import re
    m = re.match(r"([A-Za-z]+)", str(no or ""))
    return (_SERIES_RANK.get(m.group(1).upper() if m else "", 4), doc_number_key(no))


def _cashier_table(data):
    """The cashier summary as (columns, rows, totals) for the PDF / CSV / web / shift report: one row
    per receipt or voucher with the payment modes as COLUMNS (v8h), then the unsettled / due / nil /
    invoices / in-house sections. A memo mode (money prepaid to the channel) has its own column and
    is never added to the Total."""
    modes = sorted(data["modes"], key=lambda m: (bool(m.get("memo")), _CASHIER_MODE_ORDER.index(m["mode"])
                                                 if m["mode"] in _CASHIER_MODE_ORDER else 99, m["mode"]))
    mode_cols = [_mode_col(m) for m in modes]
    lead = ["Receipt / Vou", "Bill", "Date", "Time", "Room", "Guest", "Source", "Type"]
    tail = ["Total", "Balance", "CR No", "Remarks", "User"]
    cols = lead + mode_cols + tail
    ix = {c: i for i, c in enumerate(cols)}

    def _row(**kw):
        r = [""] * len(cols)
        for k, v in kw.items():
            r[ix[k]] = v
        return r

    money = []
    for md in modes:
        col = _mode_col(md)
        for sec in md["sections"]:
            for x in sec["rows"]:
                amt = round(float(x.get("receipt") or 0) - float(x.get("payment") or 0), 2)
                rec = x.get("receipt_no") or x["bill_no"]
                guest = " · ".join(str(v) for v in (x.get("guest"), f"#{x['booking_id']}" if x.get("booking_id") else None) if v)
                r = _row(**{"Receipt / Vou": rec, "Bill": x["bill_no"] if x["bill_no"] != rec else "",
                            "Date": x["date"], "Time": x["time"], "Room": x["room"], "Guest": guest,
                            "Source": x.get("source") or "", "Type": _CASHIER_KIND.get(sec["key"], sec["label"]),
                            col: amt, "Total": "" if md.get("memo") else amt, "Balance": _bal_cell(x),
                            "CR No": x.get("cr_no") or "", "Remarks": x.get("remarks") or "",
                            "User": x.get("user") or ""})
                money.append((_receipt_order_key(rec), r))
    rows = [r for _k, r in sorted(money, key=lambda t: t[0])]

    def _sec(label, items):
        rows.append({"section": label})
        rows.extend(items)

    if data["unsettled"]:
        _sec("Unsettled Checkout Bills", [_row(**{"Bill": x["bill_no"], "Date": x.get("date", ""), "Time": x.get("time", ""),
                                                  "Room": x["room"], "Guest": x["guest"] or "", "Source": x.get("source") or "",
                                                  "Balance": x["balance"], "Remarks": "UNSETTLED", "User": x.get("user") or ""})
                                            for x in data["unsettled"]])
    if data.get("due_bills"):
        # v7s (owner): agent bills the guest will pay later - DUE, not balance
        _sec(f"Due Bills - Pay Later ({len(data['due_bills'])}) - Rs {data.get('due_bills_total', 0):,.2f}",
             [_row(**{"Bill": x["bill_no"], "Date": x.get("date", ""), "Time": x.get("time", ""), "Room": x["room"],
                      "Guest": x["guest"] or "", "Source": x.get("source") or "", "Balance": f"DUE {x['due']:,.2f}",
                      "Remarks": "PAY LATER" + (f" - {x['agent']}" if x.get("agent") else "")
                                 + ("" if x.get("due_outstanding") else " (since paid)"),
                      "User": x.get("user") or ""}) for x in data["due_bills"]])
    if data["nil_checkouts"]:
        _sec("Checkout Bills With Nil Amount", [_row(**{"Bill": x["bill_no"], "Date": x.get("date", ""), "Time": x.get("time", ""),
                                                        "Room": x["room"], "Guest": x["guest"] or "", "Source": x.get("source") or "",
                                                        "Balance": x["balance"], "Remarks": "NIL", "User": x.get("user") or ""})
                                                  for x in data["nil_checkouts"]])
    if data.get("invoices"):
        _sec(f"Invoices Raised ({len(data['invoices'])}) - Rs {data.get('invoices_total', 0):,.2f}",
             [_row(**{"Bill": x["bill_no"], "Date": x["date"], "Time": x["time"], "Room": x["room"],
                      "Guest": x.get("guest") or "", "Source": x.get("source") or "", "Balance": _bal_cell(x),
                      "Remarks": f"INVOICE Rs {x['amount']:,.2f}" + (f" (prepaid {x['prepaid']:,.2f})" if x.get("prepaid") else ""),
                      "User": x.get("user") or ""}) for x in data["invoices"]])
    if data.get("in_house"):
        def _ih(x):
            _d, _t = (x.get("checked_in") or " ").split(" ", 1) if x.get("checked_in") else ("", "")
            return _row(**{"Bill": f"#{x['booking_id']}", "Date": _d, "Time": _t, "Room": x["room"],
                           "Guest": x.get("guest") or "", "Source": x.get("source") or "", "Balance": x["balance"],
                           "Remarks": f"IN HOUSE - out {x.get('check_out') or ''}"})
        _sec(f"Rooms In House ({len(data['in_house'])})", [_ih(x) for x in data["in_house"]])

    g = data["grand"]
    totals = _row(**{"Receipt / Vou": "GRAND TOTAL", "Total": g["net"],
                     "Remarks": f"in {g['receipt']:,.2f} / out {g['payment']:,.2f}"})
    for m in modes:
        totals[ix[_mode_col(m)]] = m["net"] if not m.get("memo") else m["receipt"]
    return cols, rows, totals


def checkout_summary_data(db, day, day_to=None):
    """Per-bill check-out summary for one day (adapted): one row per booking checked out that day —
    room rent, SGST/CGST, food, laundry, misc, discount, advance, refund, and the bill amount."""
    from models import Folio
    from routers.folio import _get_invoice
    from services import dues as _dues
    _win_cache = {}      # v7u
    rows = []
    # v7e: bill by bill, on the day THAT bill closed (a per-room bill closes with its own room)
    # v8a (owner): "all reports by date range" - one day, or every bill closed in [day, day_to]
    if day_to and day_to != day:
        _closed = _checked_out_bills(db, window=clock.business_day_bounds(day, day_to))
    else:
        _closed = _checked_out_bills(db, day=day)
    # v6e: ONE ROW PER BILL. A per-room stay issues a bill per room, each with its own invoice
    # number, and this report is read as the day's list of bills. `advance` and `refund` belong to
    # the BOOKING, so they are shown against its first bill only — a card payment split across
    # bills is recorded in payment_allocations, and inventing a second split here would
    # double-count the money.
    _seen = set()
    _bills = []
    for b, fo, out_at, by in _closed:
        _bills.append((b, 0 if b.booking_id not in _seen else 1, fo, out_at, by))
        _seen.add(b.booking_id)
    for b, _n, f, _out_at, _by in _bills:
        charges = [c for c in db.query(FolioCharge).filter(FolioCharge.folio_id == f.id,
                                                           FolioCharge.void == False).all()]  # noqa: E712

        def _sum(types):
            return round(sum(float(c.amount or 0) for c in charges if c.type in types), 2)
        room_rent = _sum(("room",))
        food = _sum(("food", "minibar"))
        laundry = _sum(("laundry",))
        misc = _sum(("misc", "extra_bed", "extra_person"))   # v6l: extra person
        discount = _sum(("discount",))
        pairs = [(c.gst_percent, c.amount) for c in charges if c.type not in ("payment", "discount")]
        sp = _gst_split(pairs, discount=discount)   # v6d (F-11): tax on the discounted value
        taxable, cgst, sgst = sp["taxable_total"], sp["cgst_total"], sp["sgst_total"]
        # v7m (owner): the CGST / SGST inside the room rent and inside the food, each after its
        # share of the bill's discount (pro-rata by amount, as the invoice applies it).
        _gross = sp["gross_total"]

        def _part(types):
            sel = [(c.gst_percent, c.amount) for c in charges if c.type in types]
            g = round(sum(float(a or 0) for _p, a in sel), 2)
            share = round(abs(discount) * g / _gross, 2) if _gross and discount else 0.0
            return _gst_split(sel, discount=share) if sel else {"cgst_total": 0.0, "sgst_total": 0.0}
        _rs, _fs = _part(("room",)), _part(("food", "minibar"))

        # v8k (owner, 24 Oct): "room rent and food should be their amounts WITHOUT gst, as the gst is
        # shown separately". Each amount column is its taxable value before the discount, and the
        # discount is the taxable value it took off, so on every row
        #   rent + food + laundry + misc - discount + total CGST + total SGST = bill amount.
        def _net(types):
            sel = [(c.gst_percent, c.amount) for c in charges if c.type in types]
            return round(_gst_split(sel)["taxable_total"], 2) if sel else 0.0
        _nets = {"room_rent_net": _net(("room",)), "food_net": _net(("food", "minibar")),
                 "laundry_net": _net(("laundry",)), "misc_net": _net(("misc", "extra_bed", "extra_person"))}
        _all = tuple({c.type for c in charges if c.type not in ("payment", "discount")})
        _nets["discount_net"] = round(max(0.0, _net(_all) - taxable), 2) if discount else 0.0
        # v7s (owner): "a Balance column - when the advance is less, the guest pays the balance at
        # check-out; when more advance was taken the balance is 0 and a refund is done". The money
        # taken before the guest left splits into ADVANCE (at check-in / during the stay) and
        # BALANCE (paid at check-out) by the cashier summary's own rule (_is_checkout_receipt), so
        # the two reports agree. Money received later against a pay-later bill is neither.
        _before = [p for p in db.query(Payment).filter(Payment.booking_id == b.booking_id,
                                                         Payment.status == "paid").all()
                   if not (b.checked_out_at and p.created_at and p.created_at >= b.checked_out_at)
                   and not _dues.is_due_payment(p)] if _n == 0 else []
        _at_out = [p for p in _before if _is_checkout_receipt(p, b, db, _win_cache)]
        advance = round(sum(float(p.amount or 0) for p in _before if p not in _at_out), 2)
        balance_paid = round(sum(float(p.amount or 0) for p in _at_out), 2)
        refund = round(sum(float(p.refund_amount or 0) for p in
                           db.query(Payment).filter(Payment.booking_id == b.booking_id,
                                                    Payment.refund_status == "completed").all()),
                       2) if _n == 0 else 0.0
        # A room's bill names its own room; the group bill names them all.
        _its = ([db.query(BookingItem).filter(
                     BookingItem.booking_item_id == f.booking_item_id).first()]
                if f.booking_item_id else
                db.query(BookingItem).filter(BookingItem.booking_id == b.booking_id).all())
        rooms = [db.query(Room).filter(Room.room_id == it.room_id).first()
                 for it in _its if it is not None and it.room_id]
        inv = _get_invoice(db, f.id)
        _co = clock.to_local(_out_at)
        # v7o (owner): when the room(s) on this bill checked in (local wall clock, the earliest)
        _cin = min((it.checked_in_at for it in _its if it is not None and it.checked_in_at), default=None) \
            or b.checked_in_at
        # v8k (owner): the booking's source, and the OTA's own booking id (others: our booking no)
        from utils import booking_source as _booking_source
        _src = _booking_source.source_label(db, b)
        _ref = (b.ota_booking_id or "").strip() or f"#{b.booking_id}"
        # v8k (owner, INV/2026-27/00331): money the guest paid the OTA before arriving. It settles
        # part of the bill but is not a desk receipt, so without it Advance + Balance looked short.
        prepaid = round(float(b.prepaid_amount or 0), 2) if _n == 0 else 0.0
        rows.append({
            "bill_no": (inv.invoice_no if inv else f"F{f.id}"),
            "source": _src, "booking_ref": _ref, "prepaid": prepaid, **_nets,
            "checked_in_at": _cin.strftime("%d/%m/%y %H:%M") if _cin else "",
            # v7d (owner): when they left and which reception user checked them out
            "checked_out_at": _co.strftime("%d/%m/%y %H:%M") if _co else "",
            "checked_out_by": _user_name(db, _by) or "",
            "rooms": ", ".join(r.room_number for r in rooms if r) or "—",
            "room_rent": room_rent, "sgst": sgst, "cgst": cgst, "food": food, "laundry": laundry,
            "room_cgst": _rs["cgst_total"], "room_sgst": _rs["sgst_total"],
            "food_cgst": _fs["cgst_total"], "food_sgst": _fs["sgst_total"],
            "misc": misc, "discount": discount, "total": round(float(f.total or 0), 2),
            "advance": advance, "balance": balance_paid, "refund": refund,
            # v7s: what this bill left unpaid as "pay later" (agent bookings), and what is still owed
            "due": round(float(f.due_amount or 0), 2) if _dues.is_due_bill(f) else 0.0,
            "due_outstanding": _dues.outstanding(f),
            "bill_amount": round(float(f.total or 0), 2),
        })
    keys = ("room_rent", "sgst", "cgst", "food", "laundry", "misc", "discount", "total",
            "advance", "balance", "refund", "due", "bill_amount", "room_cgst", "room_sgst", "food_cgst", "food_sgst",
            "prepaid", "room_rent_net", "food_net", "laundry_net", "misc_net", "discount_net")
    totals = {k: round(sum(r[k] for r in rows), 2) for k in keys}
    return {"day": str(day), "day_to": str(day_to or day), "rows": rows, "totals": totals}


def checkout_summary_table(data):
    """v7m: one layout for the screen's PDF / CSV and the end-of-day mail. Room rent and food each
    show the CGST and SGST inside them; the bill's total CGST / SGST (every line, after discount)
    stays as it was."""
    # v7o (owner): check-in and check-out date + time beside the rooms
    # v8k (owner, 24 Oct): Source + Booking ID (the OTA's own id); amounts WITHOUT GST (the GST has
    # its own columns); Bill Amt BEFORE the money columns; Prepaid (paid to the OTA) beside Advance.
    # Each row: Rent + Food + Laundry + Misc - Discount + Total CGST + Total SGST = Bill Amt, and
    #           Prepaid + Advance + Balance - Refund + Due = Bill Amt.
    cols = ["Bill", "Rooms", "Source", "Booking ID", "Check-in", "Check-out", "Room Rent", "Room CGST",
            "Room SGST", "Food", "Food CGST", "Food SGST", "Laundry", "Misc", "Discount", "Total CGST",
            "Total SGST", "Bill Amt", "Prepaid (OTA)", "Advance", "Balance", "Refund", "Due", "By"]
    rows = [[r["bill_no"], r["rooms"], r.get("source", ""), r.get("booking_ref", ""), r.get("checked_in_at", ""),
             r.get("checked_out_at", ""), r["room_rent_net"], r["room_cgst"], r["room_sgst"], r["food_net"],
             r["food_cgst"], r["food_sgst"], r["laundry_net"], r["misc_net"], r["discount_net"], r["cgst"], r["sgst"],
             r["bill_amount"], r.get("prepaid", 0.0), r["advance"], r.get("balance", 0.0), r["refund"],
             r.get("due", 0.0), r.get("checked_out_by", "")] for r in data["rows"]]
    t = data["totals"]
    totals = ["TOTAL", "", "", "", "", "", t["room_rent_net"], t["room_cgst"], t["room_sgst"], t["food_net"],
              t["food_cgst"], t["food_sgst"], t["laundry_net"], t["misc_net"], t["discount_net"], t["cgst"], t["sgst"],
              t["bill_amount"], t.get("prepaid", 0.0), t["advance"], t.get("balance", 0.0), t["refund"],
              t.get("due", 0.0), ""]
    return cols, rows, totals


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
    """The room label for a booking (its LOWEST-numbered assigned room; joined lazily + cached).
    Used for charges that name no room of their own (anything posted before v5n).

    v5s: this ordering decides which room a charge is reported against, and it used to be a TEXT
    sort on a varchar label — so a stay in rooms 9 and 10 attributed its pre-v5n room-service to
    room 10, because "10" < "9" as text. Numeric now.
    """
    if booking_id not in _cache:
        row = (db.query(Room.room_number).join(BookingItem, BookingItem.room_id == Room.room_id)
               .filter(BookingItem.booking_id == booking_id)
               .order_by(*ordering.room_number_key(Room.room_number)).first())
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
    # v5s: k[0] is the room label — numerically, so room 9 precedes room 10.
    for key in sorted(groups, key=lambda k: (ordering.room_number_sort_key(k[0]), k[1])):
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
    """Per ROOM that checked out in the range (by that room's own check-out): its check-in/out, the
    cleaning card cut / returned, cleaning started/ended, inspected, cleaned-by / inspected-by, and
    the fraud alerts raised during THAT stay and its cleaning.

    v7g (owner): "how is cleaning start not present" - a room marked clean or inspected without anyone
    pressing Start had no start time; cutting the cleaning card IS the start (it auto-starts cleaning),
    so its time stands in. New columns: cleaning card cut and returned. Frauds are those of this stay
    and room (booking, or the room between check-in and the end of its cleaning) - they used to be
    every alert ever raised on the room, which made each row look fraudulent."""
    from models import HousekeepingTask
    rows = []
    _fx_cache = {}
    out_at = func.coalesce(BookingItem.checked_out_at, Booking.checked_out_at)
    q = (db.query(BookingItem, Booking)
         .join(Booking, Booking.booking_id == BookingItem.booking_id)
         .filter(BookingItem.room_id.isnot(None), out_at.isnot(None),
                 clock.business_date_sql(out_at) >= dfrom, clock.business_date_sql(out_at) <= dto,
                 (BookingItem.checked_out_at.isnot(None)) | (Booking.status == "checked_out"))
         .order_by(out_at))
    for it, b in q.all():
        r = db.query(Room).filter(Room.room_id == it.room_id).first()
        co = it.checked_out_at or b.checked_out_at                       # UTC
        ci_local = it.checked_in_at or b.checked_in_at                   # hotel wall clock
        task = (db.query(HousekeepingTask)
                .filter(HousekeepingTask.room_id == it.room_id,
                        HousekeepingTask.type == "checkout_clean",
                        HousekeepingTask.booking_id == b.booking_id)
                .order_by(HousekeepingTask.created_at.desc()).first())
        if task is None:  # fall back to the room's first cleaning after this checkout
            task = (db.query(HousekeepingTask)
                    .filter(HousekeepingTask.room_id == it.room_id,
                            HousekeepingTask.type == "checkout_clean",
                            HousekeepingTask.created_at >= co - timedelta(minutes=5))
                    .order_by(HousekeepingTask.created_at.asc()).first())
        end_utc = (task.inspected_at or task.done_at) if task else None
        win_end = (end_utc or co) + timedelta(hours=12)
        card = (db.query(CardIssuance)
                .filter(CardIssuance.room_id == it.room_id, CardIssuance.card_type == "cleaning",
                        CardIssuance.status != "cancelled",
                        CardIssuance.issued_at >= co - timedelta(minutes=5),
                        CardIssuance.issued_at <= win_end)
                .order_by(CardIssuance.issued_at.asc()).first())
        card_cut = clock.to_local(card.issued_at) if card else None
        if card is None:
            card_back = None
        elif card.erased_at:
            card_back = clock.to_local(card.erased_at).isoformat()
        elif card.status == "active":
            card_back = "still out"
        else:
            card_back = f"not returned ({card.status})"
        started = clock.to_local(task.started_at) if task and task.started_at else None
        start_from_card = started is None and card_cut is not None
        ci_utc = clock.to_utc(ci_local) if ci_local else co - timedelta(days=30)
        # v8a (owner, 18 Oct: "I see many frauds"): only THIS stay's alerts - this booking's, or the room's
        # own alerts (no booking) about something that happened during the stay or its cleaning; never
        # another booking's, and never one already dismissed. Scoping by DETECTION time had pinned weeks-old
        # alerts (re-raised by the 14 Oct sweep) onto whoever was in the room that day.
        from utils.fraud_explain import explain as _explain, event_time as _event_time
        _cands = (db.query(FraudAlert)
                  .filter(FraudAlert.status != "dismissed",
                          or_(FraudAlert.booking_id == b.booking_id,
                              (FraudAlert.room_id == it.room_id) & FraudAlert.booking_id.is_(None)
                              & (FraudAlert.detected_at >= ci_utc - timedelta(minutes=5))
                              & (FraudAlert.detected_at <= win_end + timedelta(hours=12))))
                  .order_by(FraudAlert.detected_at).all())
        _lo = (ci_local or clock.to_local(co)) - timedelta(minutes=5)
        _hi = clock.to_local((end_utc or co) + timedelta(hours=2))   # v8a: up to 2 h after the room was ready
        frauds = []
        for a in _cands:
            if a.booking_id == b.booking_id:
                frauds.append(a)
                continue
            _ev = _event_time(a)
            if _ev is None or _lo <= _ev <= _hi:
                frauds.append(a)
        # v7p (owner): readable - what happened, not "type(severity)"; v8a: one per line
        _ex = [_explain(db, a, _fx_cache) for a in frauds]
        fraud_list = [{"title": e["title"], "short": e["short"], "status": e["status_text"]} for e in _ex]
        fr = "\n".join(f"{e['title']}: {e['short']}" for e in _ex)
        # v8a (owner): the GUEST key card - cut at check-in (or the room shift) and wiped at check-out
        _gcards = (db.query(CardIssuance)
                   .filter(CardIssuance.booking_id == b.booking_id, CardIssuance.room_id == it.room_id,
                           CardIssuance.card_type == "guest", CardIssuance.status != "cancelled")
                   .order_by(CardIssuance.issued_at).all())
        _first = next((c for c in _gcards if c.issue_type in ("checkin", "shift")), _gcards[0] if _gcards else None)
        key_cut = clock.to_local(_first.issued_at) if _first and _first.issued_at else None
        _wiped = [c.erased_at for c in _gcards if c.erased_at]
        if r is not None and (r.lock_type or "card") == "key":
            key_wiped = "key room"
        elif _wiped:
            key_wiped = clock.to_local(max(_wiped)).isoformat()
        elif any(c.status == "active" for c in _gcards):
            key_wiped = "NOT wiped - still works"
        elif _gcards:
            key_wiped = "not wiped (closed in the system)"
        else:
            key_wiped = "no card cut"
        rows.append({
            "booking_id": b.booking_id, "room": r.room_number if r else "—", "room_id": it.room_id,
            "guest": b.display_guest_name or None,
            "check_in": ci_local.isoformat() if ci_local else None,
            "check_out": clock.to_local(co).isoformat() if co else None,
            "key_card_cut": key_cut.isoformat() if key_cut else None,              # v8a
            "key_card_shift": bool(_first and _first.issue_type == "shift"),
            "key_card_wiped": key_wiped,                                          # v8a
            "card_cut": card_cut.isoformat() if card_cut else None,
            "card_returned": card_back,
            "cleaning_started": (started or card_cut).isoformat() if (started or card_cut) else None,
            "cleaning_started_from_card": start_from_card,
            "cleaning_ended": clock.to_local(task.done_at).isoformat() if task and task.done_at else None,
            "inspected_at": clock.to_local(task.inspected_at).isoformat() if task and task.inspected_at else None,
            "cleaned_by": task.cleaned_by_name if task else None,
            "inspected_by": task.inspected_by_name if task else None,
            "frauds": fr,
            "fraud_list": fraud_list,
            "has_fraud": bool(frauds),
        })
    return {"from": str(dfrom), "to": str(dto), "rows": rows,
            "totals": {"stays": len(rows), "with_fraud": len([x for x in rows if x["has_fraud"]])}}


# =====================================================================================================
# v7p (owner, 15 Oct, items 4 + 5): "one more report to owner - room check-in, check-out, check-in card cut,
# checkout card cut, cleaning card cut, clean start, clean end, clean card return, inspected by - and the
# differences between them, meaningful to understand if any fraud happened; we can use it as a KPI metric
# and it should also show in the UI". Plus "view card cut time, view card return time, then after viewing
# was the room checked in". Built on room_detail_data (one row per room stay that checked out).
# =====================================================================================================

VIEWING_CHECKIN_WINDOW_MIN = 180          # a viewing "converted" when the room was checked in within 3 h
CARD_LATE_AFTER_CHECKIN_MIN = 30


def _iso_local(v):
    """room_detail_data's ISO strings (already the hotel's clock) -> datetime, or None for text."""
    if not v or not isinstance(v, str) or v[:2] != "20":
        return None
    try:
        return datetime.fromisoformat(v[:26])
    except ValueError:
        return None


def _mins(a, b):
    return round((b - a).total_seconds() / 60.0, 1) if a and b else None


def room_turnaround_data(db, dfrom, dto):
    """Per room stay that checked out in the range: every moment from check-in to the room being ready
    again, the minutes between them, and plain-language red flags; the viewing cards cut in the range and
    whether a check-in followed; KPIs over both."""
    from models import HousekeepingTask  # noqa: F401  (room_detail_data imports it too)
    fcfg = app_settings.get_fraud_config(db)
    cmin, cmax = float(fcfg.get("cleaning_min_minutes") or 0), float(fcfg.get("cleaning_max_minutes") or 0)
    rd = room_detail_data(db, dfrom, dto)
    rows = []
    for r in rd["rows"]:
        room = db.query(Room).filter(Room.room_id == r.get("room_id")).first() if r.get("room_id") else None
        is_card_room = not room or (room.lock_type or "card") != "key"
        ci, co = _iso_local(r["check_in"]), _iso_local(r["check_out"])
        guest_cards = (db.query(CardIssuance)
                       .filter(CardIssuance.booking_id == r["booking_id"], CardIssuance.room_id == r.get("room_id"),
                               CardIssuance.card_type == "guest", CardIssuance.status != "cancelled")
                       .order_by(CardIssuance.issued_at).all()) if r.get("room_id") else []
        # v8a: the room's own first card - a guest SHIFTED into this room got theirs at the shift
        first = next((c for c in guest_cards if c.issue_type in ("checkin", "shift")),
                     guest_cards[0] if guest_cards else None)
        shifted_in = bool(first and first.issue_type == "shift")
        card_cut = clock.to_local(first.issued_at) if first and first.issued_at else None
        live = [c for c in guest_cards if c.status == "active"]
        wiped = [c.erased_at for c in guest_cards if c.erased_at]
        wipe_at = clock.to_local(max(wiped)) if wiped else None
        if not is_card_room:
            wipe_txt = "key room"
        elif live:
            wipe_txt = "NOT wiped - still works"
        elif wipe_at:
            wipe_txt = None
        elif guest_cards:
            wipe_txt = "not wiped (closed in the system only)"
        else:
            wipe_txt = "no card"
        cc_cut, cc_back = _iso_local(r.get("card_cut")), _iso_local(r.get("card_returned"))
        st, en, insp = _iso_local(r.get("cleaning_started")), _iso_local(r.get("cleaning_ended")), _iso_local(r.get("inspected_at"))
        d = {
            "ci_to_card": _mins(ci, card_cut),
            "co_to_wipe": _mins(co, wipe_at),
            "co_to_clean_card": _mins(co, cc_cut),
            "co_to_clean_start": _mins(co, st),
            "clean_minutes": _mins(st, en),
            "end_to_card_return": _mins(en, cc_back),
            "end_to_inspected": _mins(en, insp),
            "turnaround": _mins(co, insp or en),
        }
        flags = []
        if is_card_room:
            if card_cut is None:
                flags.append("No key card was cut for this check-in")
            elif d["ci_to_card"] is not None and d["ci_to_card"] < -5:
                flags.append(f"Key card cut {abs(d['ci_to_card']):.0f} min BEFORE check-in")
            elif d["ci_to_card"] is not None and d["ci_to_card"] > CARD_LATE_AFTER_CHECKIN_MIN and not shifted_in:
                flags.append(f"Key card cut {d['ci_to_card']:.0f} min after check-in")
            if wipe_txt and wipe_txt.startswith("NOT"):
                flags.append("Guest's key card was NOT wiped at check-out - it still opens the room")
            elif wipe_txt and wipe_txt.startswith("not wiped"):
                flags.append("Guest's key card was not wiped on the encoder at check-out")
        if d["clean_minutes"] is not None and cmin and d["clean_minutes"] < cmin:
            flags.append(f"Cleaned in only {d['clean_minutes']:.0f} min (minimum {cmin:.0f})")
        if d["clean_minutes"] is not None and cmax and d["clean_minutes"] > cmax:
            flags.append(f"Cleaning took {d['clean_minutes']:.0f} min (limit {cmax:.0f})")
        if r.get("card_cut") and not cc_back:
            flags.append(f"Cleaning card {r.get('card_returned') or 'not returned'}")
        if d["end_to_card_return"] is not None and d["end_to_card_return"] < -5:
            flags.append("Cleaning card returned BEFORE cleaning was marked done")
        if (st or en) and not insp:
            flags.append("Room never inspected after cleaning")
        if not (st or en) and co:
            flags.append("No cleaning recorded after check-out")
        # v8a (owner): each fraud alert of this stay its own flag ("Title: what happened"), not one long line
        for _fa in (r.get("fraud_list") or []):
            flags.append(f"Alert - {_fa['title']}: {_fa['short']}")
        rows.append({
            "booking_id": r["booking_id"], "room": r["room"], "guest": r["guest"], "lock": "card" if is_card_room else "key",
            "check_in": ci.isoformat() if ci else None, "checkin_card": card_cut.isoformat() if card_cut else None,
            "check_out": co.isoformat() if co else None, "checkout_wipe": wipe_at.isoformat() if wipe_at else None,
            "checkout_wipe_note": wipe_txt,
            "clean_card_cut": r.get("card_cut"), "clean_start": r.get("cleaning_started"),
            "clean_end": r.get("cleaning_ended"), "clean_card_returned": r.get("card_returned"),
            "inspected_at": r.get("inspected_at"), "inspected_by": r.get("inspected_by"),
            "cleaned_by": r.get("cleaned_by"), "diff": d, "flags": flags, "suspicious": bool(flags),
        })

    # ---- viewing cards (item 5): cut / returned / then checked in?
    views = []
    vcfg_min = None
    try:
        vcfg_min = int(app_settings.get_frontdesk_config(db).get("viewing_card_minutes") or 0) or None
    except Exception:
        pass
    for c in (db.query(CardIssuance)
              .filter(CardIssuance.card_type == "viewing", CardIssuance.status != "cancelled",
                      clock.business_date_sql(CardIssuance.issued_at) >= dfrom,
                      clock.business_date_sql(CardIssuance.issued_at) <= dto)
              .order_by(CardIssuance.issued_at).all()):
        room = db.query(Room).filter(Room.room_id == c.room_id).first() if c.room_id else None
        cut = clock.to_local(c.issued_at) if c.issued_at else None
        back = clock.to_local(c.erased_at) if c.erased_at else None
        allowed = (round((c.valid_to - c.valid_from).total_seconds() / 60) if c.valid_to and c.valid_from else vcfg_min)
        out_min = _mins(cut, back) if back else (_mins(cut, clock.now_local()) if c.status == "active" else None)
        nxt = None
        if cut and c.room_id:
            nxt = (db.query(BookingItem, Booking).join(Booking, Booking.booking_id == BookingItem.booking_id)
                   .filter(BookingItem.room_id == c.room_id, BookingItem.checked_in_at.isnot(None),
                           BookingItem.checked_in_at >= cut,
                           BookingItem.checked_in_at <= cut + timedelta(minutes=VIEWING_CHECKIN_WINDOW_MIN))
                   .order_by(BookingItem.checked_in_at).first())
        vflags = []
        if c.status == "active" and not back:
            vflags.append(f"Viewing card still out ({out_min:.0f} min)" if out_min is not None else "Viewing card still out")
        elif not back:
            vflags.append(f"Viewing card not returned ({c.status})")
        if out_min is not None and allowed and out_min > allowed + 5:
            vflags.append(f"Kept {out_min:.0f} min (allowed {allowed})")
        if nxt is None and out_min is not None and out_min > 60:
            vflags.append("Out over an hour with no check-in - check whether the room was used")
        views.append({
            "card_id": c.id, "room": room.room_number if room else "—",
            "cut": cut.isoformat() if cut else None, "returned": back.isoformat() if back else None,
            "status": c.status, "minutes_out": out_min, "allowed_minutes": allowed,
            "issued_by": _user_name(db, c.issued_by),
            "checked_in": bool(nxt),
            "checkin_booking_id": nxt[1].booking_id if nxt else None,
            "minutes_to_checkin": _mins(cut, nxt[0].checked_in_at) if nxt else None,
            "flags": vflags, "suspicious": bool(vflags),
        })

    def _avg(key):
        vals = [x["diff"][key] for x in rows if x["diff"].get(key) is not None and x["diff"][key] >= 0]
        return round(sum(vals) / len(vals), 1) if vals else None

    card_rows = [x for x in rows if x["lock"] == "card"]
    cleaned = [x for x in rows if x["diff"]["clean_minutes"] is not None]
    cc = [x for x in rows if x["clean_card_cut"]]
    pct = (lambda n, d: round(100.0 * n / d, 1) if d else None)
    kpis = {
        "stays": len(rows),
        "suspicious_stays": sum(1 for x in rows if x["suspicious"]),
        "avg_checkin_to_card_min": _avg("ci_to_card"),
        "pct_cards_wiped_at_checkout": pct(sum(1 for x in card_rows if x["checkout_wipe"] and not (x["checkout_wipe_note"] or "").startswith("NOT")), len(card_rows)),
        "avg_checkout_to_clean_start_min": _avg("co_to_clean_start"),
        "avg_clean_minutes": _avg("clean_minutes"),
        "pct_cleaned_too_fast": pct(sum(1 for x in cleaned if cmin and x["diff"]["clean_minutes"] < cmin), len(cleaned)),
        "pct_cleaning_cards_returned": pct(sum(1 for x in cc if _iso_local(x["clean_card_returned"])), len(cc)),
        "avg_turnaround_min": _avg("turnaround"),
        "pct_inspected": pct(sum(1 for x in rows if x["inspected_at"]), len(rows)),
        "viewings": len(views),
        "viewing_to_checkin_pct": pct(sum(1 for v in views if v["checked_in"]), len(views)),
        "viewing_cards_returned_pct": pct(sum(1 for v in views if v["returned"]), len(views)),
        "suspicious_viewings": sum(1 for v in views if v["suspicious"]),
    }
    return {"from": str(dfrom), "to": str(dto), "rows": rows, "viewings": views, "kpis": kpis,
            "settings": {"cleaning_min_minutes": cmin, "cleaning_max_minutes": cmax,
                         "viewing_checkin_window_min": VIEWING_CHECKIN_WINDOW_MIN}}


KPI_LABELS = [
    ("stays", "Rooms checked out", ""), ("suspicious_stays", "Stays with a red flag", ""),
    ("avg_checkin_to_card_min", "Avg check-in → key card", " min"),
    ("pct_cards_wiped_at_checkout", "Key cards wiped at check-out", "%"),
    ("avg_checkout_to_clean_start_min", "Avg check-out → cleaning start", " min"),
    ("avg_clean_minutes", "Avg cleaning time", " min"),
    ("pct_cleaned_too_fast", "Cleanings too fast", "%"),
    ("pct_cleaning_cards_returned", "Cleaning cards returned", "%"),
    ("avg_turnaround_min", "Avg check-out → room ready", " min"),
    ("pct_inspected", "Rooms inspected", "%"),
    ("viewings", "Viewing cards", ""), ("viewing_to_checkin_pct", "Viewings → check-in", "%"),
    ("viewing_cards_returned_pct", "Viewing cards returned", "%"),
    ("suspicious_viewings", "Viewings with a red flag", ""),
]


def kpi_pairs(kpis):
    return [(lbl, "—" if kpis.get(k) is None else f"{kpis[k]:g}{unit}") for k, lbl, unit in KPI_LABELS]


def _hm(v, base=None, delta=None):
    """'15/10 14:05 (+12m)' - the time, and minutes from the step before."""
    t = _iso_local(v) if isinstance(v, str) else v
    if not t:
        return v or "—"
    s = t.strftime("%d/%m %H:%M")
    if delta is not None:
        s += f" ({'+' if delta >= 0 else ''}{delta:.0f}m)"
    return s


TURNAROUND_STAY_COLS = ["Room", "Guest", "Check-in", "Key card cut", "Check-out", "Card wiped", "Clean card cut",
                        "Clean start", "Clean end", "Clean card back", "Inspected", "Ready after", "Red flags"]
TURNAROUND_VIEW_COLS = ["Room", "Viewing card cut", "Returned", "Minutes out", "Allowed", "Cut by",
                        "Checked in after?", "Red flags"]


def room_turnaround_tables(data):
    """(stay_cols, stay_rows, stay_flags), (view_cols, view_rows, view_flags) - shared by the screen's PDF / CSV
    and the owner's daily report."""
    srows, sflags = [], []
    for x in data["rows"]:
        d = x["diff"]
        srows.append([
            x["room"], x["guest"] or "", _hm(x["check_in"]), _hm(x["checkin_card"], delta=d["ci_to_card"]),
            _hm(x["check_out"]), x["checkout_wipe_note"] or _hm(x["checkout_wipe"], delta=d["co_to_wipe"]),
            _hm(x["clean_card_cut"], delta=d["co_to_clean_card"]), _hm(x["clean_start"]),
            _hm(x["clean_end"], delta=d["clean_minutes"]),
            _hm(x["clean_card_returned"], delta=d["end_to_card_return"]),
            (_hm(x["inspected_at"], delta=d["end_to_inspected"]) + (f" by {x['inspected_by']}" if x["inspected_by"] else "")
             if x["inspected_at"] else "—"),
            (f"{d['turnaround']:.0f} min" if d["turnaround"] is not None else "—"),
            "\n".join(f"- {f}" for f in x["flags"]) or "OK"])
        sflags.append(x["suspicious"])
    vrows, vflags = [], []
    for v in data["viewings"]:
        vrows.append([v["room"], _hm(v["cut"]), _hm(v["returned"]) if v["returned"] else v["status"],
                      (f"{v['minutes_out']:.0f}" if v["minutes_out"] is not None else "—"),
                      v["allowed_minutes"] or "—", v["issued_by"] or "—",
                      (f"Yes - booking #{v['checkin_booking_id']}, {v['minutes_to_checkin']:.0f} min later"
                       if v["checked_in"] else "No"),
                      "\n".join(f"- {f}" for f in v["flags"]) or "OK"])
        vflags.append(v["suspicious"])
    return (TURNAROUND_STAY_COLS, srows, sflags), (TURNAROUND_VIEW_COLS, vrows, vflags)


@router.get("/room-turnaround", dependencies=[Depends(require_report_access)])
def room_turnaround_report(from_: str | None = Query(None, alias="from"), to: str | None = Query(None),
                           format: str = Query("json"), db: Session = Depends(get_db)):
    """v7p: Room Turnaround & Cards - KPIs, per-stay timeline with differences and red flags, viewing cards."""
    dfrom, dto = _range(from_, to)
    data = room_turnaround_data(db, dfrom, dto)
    fmt = (format or "json").lower()
    if fmt == "pdf":
        from utils.report_pdf import generate_turnaround_pdf
        path = generate_turnaround_pdf(data, f"{dfrom:%d %b %Y}" + ("" if dfrom == dto else f" to {dto:%d %b %Y}"))
        return FileResponse(path, media_type="application/pdf", filename=f"room_turnaround_{dfrom}_{dto}.pdf")
    if fmt == "csv":
        (sc, sr, _sf), (vc, vr, _vf) = room_turnaround_tables(data)
        rows = ([{"section": "KPIs"}] + [[a, b] + [""] * (len(sc) - 2) for a, b in kpi_pairs(data["kpis"])]
                + [{"section": "Room stays"}, sc] + sr + [{"section": "Viewing cards"}, vc + [""] * (len(sc) - len(vc))]
                + [r + [""] * (len(sc) - len(r)) for r in vr])
        return _csv_response("room_turnaround", sc, rows, None)
    return data



# v8a (owner, 18 Oct): the guest's key card cut at check-in and wiped at check-out, beside the times; the
# cleaning card's own columns say so (they were "Card cut / Card returned", read as the guest's card)
ROOM_DETAIL_COLS = ["Room", "Guest", "Check-in", "Key card cut", "Check-out", "Key card wiped", "Cleaning card cut",
                    "Cleaning card back", "Cleaning start", "Cleaning end", "Inspected", "Cleaned by", "Inspected by",
                    "Frauds"]


def room_detail_table(data):
    """(columns, rows) for the screen, the PDF/CSV, the EOD e-mail and the owner's daily HK report."""
    def _t(v):
        return (v or "").replace("T", " ")[:16] if v and v[:2] == "20" else (v or "")
    rows = []
    for r in data["rows"]:
        start = _t(r["cleaning_started"]) + (" (card)" if r.get("cleaning_started_from_card") else "")
        rows.append([r["room"], r["guest"] or "", _t(r["check_in"]),
                     _t(r.get("key_card_cut")) + (" (shift)" if r.get("key_card_shift") else ""),
                     _t(r["check_out"]), _t(r.get("key_card_wiped")), _t(r.get("card_cut")),
                     _t(r.get("card_returned")), start, _t(r["cleaning_ended"]), _t(r["inspected_at"]),
                     r["cleaned_by"] or "", r["inspected_by"] or "", r["frauds"] or ""])
    return ROOM_DETAIL_COLS, rows


def maintenance_detail_data(db, dfrom, dto):
    """Per maintenance ticket created in the range: full details + the items used. `fixed` =
    resolved/verified/closed; `inspected` = verified/closed (signed off)."""
    from models import TicketItem
    tickets = (db.query(MaintenanceTicket)
               .filter(clock.business_date_sql(MaintenanceTicket.created_at) >= dfrom,
                       clock.business_date_sql(MaintenanceTicket.created_at) <= dto)
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
            "created_at": clock.to_local(t.created_at).isoformat() if t.created_at else None,
            "assigned_at": clock.to_local(t.assigned_at).isoformat() if t.assigned_at else None,
            "first_responded_at": clock.to_local(t.first_responded_at).isoformat() if t.first_responded_at else None,
            "resolved_at": clock.to_local(t.resolved_at).isoformat() if t.resolved_at else None,
            "verified_at": clock.to_local(t.verified_at).isoformat() if t.verified_at else None,
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
    "lost_cards": "Lost Key Cards",
    # v6m.13 (owner, item 5): every receipt, prepaid receipt and refund voucher of the day as PDFs
    "receipts_zip": "All documents of the day - receipts, refunds, prepaid, food bills, invoices, registration slips (ZIP)",
}

# Mailjet refuses a message over 15 MB, and base64 grows an attachment by a third. A day's ZIP is
# split into parts of at most this many bytes; the first rides the report email, the rest follow
# one per email ("part 2 of 3").
RECEIPTS_ZIP_PART_BYTES = 8 * 1024 * 1024


def receipts_of_day(db, day):
    """Every money document of one business day, oldest first: (doc_no, type, when_local, payload).
    Payment receipts by when the money came in; refund vouchers by when the refund was PAID OUT
    (v6m.12 refunded_at, else the payment's time); prepaid receipts by when they were issued."""
    from models import PrepaidReceipt
    from routers.payments import receipt_payload
    from services.prepaid_receipts import prepaid_receipt_payload, receipt_no
    out = []
    for p in (db.query(Payment).filter(Payment.status == "paid",
                                       clock.business_date_sql(Payment.created_at) == day)
              .order_by(Payment.created_at).all()):
        if float(p.amount or 0):
            out.append((f"RCPT-{p.payment_id}", "Receipt", clock.to_local(p.created_at),
                        receipt_payload(db, p, "payment")))
    refunded_on = func.coalesce(Payment.refunded_at, Payment.created_at)
    for p in (db.query(Payment).filter(Payment.refund_status == "completed",
                                       clock.business_date_sql(refunded_on) == day)
              .order_by(refunded_on).all()):
        if float(p.refund_amount or 0):
            out.append((f"RFND-{p.payment_id}", "Refund voucher",
                        clock.to_local(p.refunded_at or p.created_at), receipt_payload(db, p, "refund")))
    for pr in (db.query(PrepaidReceipt).filter(clock.business_date_sql(PrepaidReceipt.issued_at) == day)
               .order_by(PrepaidReceipt.issued_at).all()):
        out.append((receipt_no(pr), "Prepaid receipt", clock.to_local(pr.issued_at),
                    prepaid_receipt_payload(db, pr)))
    out.sort(key=lambda r: (r[2] is None, r[2] or datetime.min))
    return out


def build_receipts_zip(db, day, part_bytes=None):
    """v7e: the day's documents ZIP - every receipt (by payment type), refund voucher, prepaid receipt,
    food bill, tax invoice and registration slip, in folders, named by document number
    (services/day_documents.py). Kept under this name: the EOD e-mail and its tests call it."""
    from services.day_documents import build_documents_zip
    return build_documents_zip(db, day, part_bytes=part_bytes)


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
    elif key == "lost_cards":
        d = lost_cards_data(db, day, day)
        cols, rows, totals = lost_cards_table(d)   # v7g
    elif key == "checkout_summary":
        cols, rows, totals = checkout_summary_table(checkout_summary_data(db, day))
    elif key == "room_service_by_room":
        cols, rows, totals = _rs_by_room_table(room_service_by_room_data(db, day, day))
    elif key == "room_detail":
        d = room_detail_data(db, day, day)
        cols, rows = room_detail_table(d)   # v7g
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
    attachments, zip_extra, zip_note = [], [], ""
    for k in keys:
        try:
            if k == "receipts_zip":
                # v6m.13 (owner, item 5): the day's receipts as a ZIP; extra parts follow separately
                parts = build_receipts_zip(db, day)
                if parts:
                    attachments.append(parts[0])
                    zip_extra = parts[1:]
                    zip_note = (f" — in {len(parts)} parts; parts 2–{len(parts)} follow in separate emails"
                                if len(parts) > 1 else "")
                else:
                    zip_note = " — none issued this day"
                continue
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
            + "".join(f"<li>{EOD_REPORT_CHOICES[k]}{zip_note if k == 'receipts_zip' else ''}</li>"
                      for k in keys) + "</ul>")
    ok = send_report_email(cfg["accounting_email"], subject, html, attachments)
    for i, part in enumerate(zip_extra, 2):
        send_report_email(cfg["accounting_email"],
                          f"Hotel Bhimas — receipts {day:%d-%m-%Y} (part {i} of {len(zip_extra) + 1})",
                          f"<p>Receipts for <b>{day:%d-%m-%Y}</b>, part {i} of {len(zip_extra) + 1}.</p>",
                          [part])
    return {"sent": bool(ok), "reports": keys, "recipients": cfg["accounting_email"],
            "receipts_zip_parts": (1 + len(zip_extra)) if "receipts_zip" in keys and attachments else 0}


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
    m["Generated"] = clock.now_local().strftime("%d-%m-%Y %H:%M")   # v6m.14: the hotel's clock
    return m


# ============================================================
# report endpoints
# ============================================================

@router.get("/arrival-exceptions", dependencies=[Depends(require_report_access)])
def arrival_exceptions_report(from_: str | None = Query(None, alias="from"), to: str | None = Query(None),
                              kind: str | None = Query(None), format: str = Query("json"),
                              db: Session = Depends(get_db)):
    """v5m — early check-ins, late arrivals and hourly extensions (from stay_events), with what
    was charged, on what basis and who approved a change."""
    from utils.arrival_rules import stay_events_report
    dfrom, dto = _range(from_, to)
    kind = kind if kind in ("early_checkin", "late_arrival", "hourly_extension") else None
    data = stay_events_report(db, dfrom, dto, kind)
    _ns_start, _ns_end = clock.business_day_bounds(dfrom, dto)
    no_shows = db.query(func.count(Booking.booking_id)).filter(
        Booking.status == "no_show",
        # F-03: no_show_at is an instant (UTC); the report is by business day.
        Booking.no_show_at >= _ns_start, Booking.no_show_at < _ns_end).scalar() or 0
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


@router.get("/occupancy", dependencies=[Depends(require_report_access)])
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


@router.get("/sales/daily", dependencies=[Depends(require_report_access)])
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


@router.get("/room-service/daily", dependencies=[Depends(require_report_access)])
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


@router.get("/room-service/items", dependencies=[Depends(require_report_access)])
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


@router.get("/shift-payments", dependencies=[Depends(require_report_access)])
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


@router.get("/payments-daily", dependencies=[Depends(require_report_access)])
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


@router.get("/arrivals-departures", dependencies=[Depends(require_report_access)])
def arrivals_departures_report(from_: str | None = Query(None, alias="from"), to: str | None = Query(None),
                               format: str = Query("json"), db: Session = Depends(get_db)):
    dfrom, dto = _range(from_, to)
    data = arrivals_departures_data(db, dfrom, dto)
    cols = ["Date", "Arrivals", "Departures"]
    rows = [[r["date"], r["arrivals"], r["departures"]] for r in data["rows"]]
    totals = ["TOTAL", data["totals"]["arrivals"], data["totals"]["departures"]]
    return _export_or_json(format, data, title="Arrivals & Departures", columns=cols, rows=rows,
                           totals_row=totals, meta=_meta(dfrom, dto), filename="arrivals_departures")


@router.get("/in-house", dependencies=[Depends(require_report_access)])
def in_house_report(from_: str | None = Query(None, alias="from"), to: str | None = Query(None),
                    format: str = Query("json"), db: Session = Depends(get_db)):
    """In-house guests now - or v8a every stay in house at any time between `from` and `to`."""
    if from_ or to:
        dfrom, dto = _range(from_, to)
        data = in_house_data(db, dfrom, dto)
        meta = _meta(dfrom, dto)
    else:
        data = in_house_data(db)
        meta = _meta()
    cols = ["Booking", "Guest", "Phone", "Rooms", "Check-in", "Check-out", "Nights", "Balance", "Status"]
    rows = [[r["booking_id"], r["guest_name"], r["phone"], r["rooms"], r["check_in"],
             r["check_out"], r["nights"], r["balance"],
             "in house" if r.get("status") == "checked_in" else "checked out"] for r in data["rows"]]
    return _export_or_json(format, data, title="In-House Guests", columns=cols, rows=rows,
                           meta=meta, filename="in_house")


def _occupancy_analysis_range(db, dfrom, dto, format):
    """v8a: one 'as on' snapshot per day of the range, each under its own heading."""
    cols = ["Floor", "Room", "Guest", "Source", "Type", "Arrival", "Departure", "Pax",
            "Dis %", "Rack", "Room Rent", "Discount", "Gross Rent", "GST", "Net"]
    days, rows = [], []
    d = dfrom
    while d <= dto:
        data = occupancy_analysis_data(db, d)
        days.append(data)
        t = data["totals"]
        rows.append({"section": f"As on {d:%d-%m-%Y} - {t['occ_rooms']} room(s) occupied, {t['pax']} pax, "
                                f"revenue Rs {t['revenue']:,.2f}"})
        for r in data["rooms"]:
            rows.append([r["floor"], r["room_no"], r["guest"], r["segment"], r["room_type"], r["arrival"],
                         r["departure"], r["pax"], r["discount_pct"], r["rack"], r["room_rent"], r["discount"],
                         r["gross_rent"], r["gst"], r["net"]])
        d = d + timedelta(days=1)
    out = {"from": str(dfrom), "to": str(dto), "days": days}
    return _export_or_json(format, out, title="Occupancy Analysis", columns=cols, rows=rows,
                           meta=_meta(dfrom, dto), filename="occupancy_analysis")


@router.get("/occupancy-analysis", dependencies=[Depends(require_report_access)])
def occupancy_analysis_report(as_on: str | None = Query(None), from_: str | None = Query(None, alias="from"),
                              to: str | None = Query(None), format: str = Query("json"),
                              db: Session = Depends(get_db)):
    """Adapted Occupancy Analysis 'as on' snapshot: per-room stay + revenue, grouped by floor, with
    floor-wise / by-source / by-type summaries. v8a (owner, "all reports by date range"): with `from`/`to`,
    one snapshot per day (at most 31), each under its own heading."""
    if from_ or to:
        dfrom, dto = _range(from_, to)
        if (dto - dfrom).days > 30:
            raise HTTPException(status_code=400, detail="Pick at most 31 days")
        if dfrom != dto:
            return _occupancy_analysis_range(db, dfrom, dto, format)
        as_on = str(dfrom)
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


@router.get("/cashier-summary", dependencies=[Depends(require_report_access)])
def cashier_summary_report(day: str | None = Query(None), from_: str | None = Query(None, alias="from"),
                           to: str | None = Query(None), format: str = Query("json"),
                           db: Session = Depends(get_db)):
    """Adapted Front-Office Cashier Summary for one day - or v7h, a range (from / to): receipts by method
    (advance vs checkout), paid-outs, unsettled / nil checkout bills, the invoices raised and the rooms
    in house."""
    if from_ or to:
        d, d2 = _range(from_, to)
    else:
        d = _parse_date(day, "day") if day else date.today()
        d2 = d
    data = cashier_summary_data(db, d, d2)
    cols, rows, totals = _cashier_table(data)
    data["table"] = {"columns": cols, "rows": rows, "totals": totals}   # v8h: the web shows this exact table
    meta = {("Day" if d == d2 else "Period"): (f"{d:%d-%m-%Y}" if d == d2 else f"{d:%d-%m-%Y} to {d2:%d-%m-%Y}"),
            "Unsettled bills": len(data["unsettled"]), "Invoices": len(data.get("invoices", [])), **_meta()}
    return _export_or_json(format, data, title="Front-Office Cashier Summary", columns=cols,
                           rows=rows, totals_row=totals, meta=meta, filename="cashier_summary")


@router.get("/lost-cards", dependencies=[Depends(require_report_access)])
def lost_cards_report(from_: str | None = Query(None, alias="from"), to: str | None = Query(None),
                      format: str = Query("json"), db: Session = Depends(get_db)):
    """Key cards reported lost, and what was charged for each."""
    dfrom, dto = _range(from_, to)
    data = lost_cards_data(db, dfrom, dto)
    cols = ["Reissued", "Room", "Guest", "Booking", "Lost card", "New card", "Cut by", "Fee", "Status",
            "Fee decided by", "Reason / approval"]   # v7g
    rows = [[(r["reissued_at"] or "")[:16].replace("T", " "), r["room"] or "—",
             r["guest"] or "—", r["booking_id"] or "—", r["lost_card_id"] or "—",
             r["new_card_id"], r["issued_by"] or "—", r["fee"], r["fee_status"],
             r.get("fee_decided_by") or "", r.get("fee_reason") or ""]
            for r in data["rows"]]
    totals = ["TOTAL", "", "", "", "", "", "", data["charged_total"],
              f"{data['uncharged']} not charged", "", ""]
    return _export_or_json(format, data, title="Lost Key Cards", columns=cols, rows=rows,
                           totals_row=totals, meta=_meta(dfrom, dto), filename="lost_cards")


@router.get("/documents-zip", dependencies=[Depends(require_report_access)])
def documents_zip(day: str | None = Query(None), db: Session = Depends(get_db)):
    """v7e (owner): "an option to zip receipts, invoices, reg slip of 1 for any date and download"."""
    from fastapi.responses import FileResponse
    from services.day_documents import build_documents_zip
    d = _parse_date(day, "day") if day else clock.business_today()
    parts = build_documents_zip(db, d, part_bytes=10 ** 12)     # one file for a download
    if not parts:
        raise HTTPException(status_code=404, detail=f"No documents for {d:%d-%m-%Y}")
    path, name = parts[0]
    return FileResponse(path, media_type="application/zip", filename=name)


@router.get("/checkout-summary", dependencies=[Depends(require_report_access)])
def checkout_summary_report(day: str | None = Query(None), from_: str | None = Query(None, alias="from"),
                            to: str | None = Query(None), format: str = Query("json"),
                            db: Session = Depends(get_db)):
    """Adapted Check-Out Summary: per-bill room rent, SGST/CGST, food, laundry, misc, discount, advance,
    balance, refund, due, bill amount. One `day`, or v8a a `from`/`to` range."""
    if from_ or to:
        d, d_to = _range(from_, to)
    else:
        d = _parse_date(day, "day") if day else date.today()
        d_to = d
    if (d_to - d).days > 92:
        raise HTTPException(status_code=400, detail="Pick at most 92 days")
    data = checkout_summary_data(db, d, d_to)
    cols, rows, totals = checkout_summary_table(data)
    data["table"] = {"columns": cols, "rows": rows, "totals": totals}   # v8k: the web shows this exact table
    meta = ({"Day": f"{d:%d-%m-%Y}"} if d == d_to else {"Period": f"{d:%d-%m-%Y} to {d_to:%d-%m-%Y}"})
    meta.update(_meta())
    return _export_or_json(format, data, title="Check-Out Summary", columns=cols, rows=rows,
                           totals_row=totals, meta=meta, filename="checkout_summary")


@router.get("/room-service/by-room", dependencies=[Depends(require_report_access)])
def room_service_by_room_report(from_: str | None = Query(None, alias="from"), to: str | None = Query(None),
                                format: str = Query("json"), db: Session = Depends(get_db)):
    """Room-service sales grouped by room, product-wise within each room."""
    dfrom, dto = _range(from_, to)
    data = room_service_by_room_data(db, dfrom, dto)
    cols, rows, totals = _rs_by_room_table(data)
    return _export_or_json(format, data, title="Room Service by Room", columns=cols, rows=rows,
                           totals_row=totals, meta=_meta(dfrom, dto), filename="room_service_by_room")


@router.get("/room-detail", dependencies=[Depends(require_report_access)])
def room_detail_report(from_: str | None = Query(None, alias="from"), to: str | None = Query(None),
                       format: str = Query("json"), db: Session = Depends(get_db)):
    """Per checked-out stay: check-in/out + cleaning/inspection timestamps, cleaned-by/inspected-by,
    and any frauds. Range is by check-out date."""
    dfrom, dto = _range(from_, to)
    data = room_detail_data(db, dfrom, dto)
    cols, rows = room_detail_table(data)   # v7g
    return _export_or_json(format, data, title="Room Detail (housekeeping + frauds)", columns=cols,
                           rows=rows, meta=_meta(dfrom, dto), filename="room_detail")


@router.get("/maintenance-detail", dependencies=[Depends(require_report_access)])
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


@router.get("/travel-agents", dependencies=[Depends(require_report_access)])
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


@router.get("/ota", dependencies=[Depends(require_report_access)])
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


@router.get("/gst", dependencies=[Depends(require_report_access)])
def gst_report(from_: str | None = Query(None, alias="from"), to: str | None = Query(None),
               format: str = Query("json"), db: Session = Depends(get_db)):
    dfrom, dto = _range(from_, to)
    data = gst_data(db, dfrom, dto)
    cols = ["GST Slab %", "Taxable Value", "CGST", "SGST", "Gross"]
    rows = [[r["gst_percent"], r["taxable"], r["cgst"], r["sgst"], r["gross"]] for r in data["slabs"]]
    totals = ["TOTAL", data["taxable_total"], data["cgst_total"], data["sgst_total"], data["gross_total"]]
    return _export_or_json(format, data, title="GST Report", columns=cols, rows=rows,
                           totals_row=totals, meta=_meta(dfrom, dto), filename="gst_report")


@router.get("/cash-shift", dependencies=[Depends(require_report_access)])
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


@router.get("/card-audit", dependencies=[Depends(require_report_access)])
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


@router.get("/fraud-summary", dependencies=[Depends(require_report_access)])
def fraud_summary_report(from_: str | None = Query(None, alias="from"), to: str | None = Query(None),
                         format: str = Query("json"), db: Session = Depends(get_db)):
    dfrom, dto = _range(from_, to)
    data = fraud_summary_data(db, dfrom, dto)
    cols, rows = fraud_summary_table(data)
    meta = {**_meta(dfrom, dto), "Alerts": data["count"], "Not reviewed": data["open"]}
    return _export_or_json(format, data, title="Fraud Summary - what happened", columns=cols, rows=rows,
                           meta=meta, filename="fraud_summary")


@router.get("/staff-performance", dependencies=[Depends(require_report_access)])
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
        pending.append({"booking_id": b.booking_id, "guest_name": b.display_guest_name or None,
                        "check_in_time": str(b.check_in_time) if b.check_in_time else None,
                        "check_out": str(b.check_out)})

    open_alerts = []
    for a in (db.query(FraudAlert).filter(FraudAlert.status == "open")
              .order_by(FraudAlert.detected_at.desc()).limit(10).all()):
        open_alerts.append({"id": a.id, "type": a.type, "severity": a.severity,
                            "detected_at": clock.to_local(a.detected_at).isoformat() if a.detected_at else None})

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
        overdue.append({"booking_id": b.booking_id, "guest_name": b.display_guest_name or None,
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
        clock.business_date_sql(FolioCharge.posted_at) >= wstart,
        clock.business_date_sql(FolioCharge.posted_at) <= today).scalar() or 0)
    _ron = func.coalesce(Payment.refunded_at, Payment.created_at)   # v7e: the refund's own day
    refund_total = float(db.query(func.coalesce(func.sum(Payment.refund_amount), 0)).filter(
        Payment.refund_status == "completed",
        clock.business_date_sql(_ron) >= wstart, clock.business_date_sql(_ron) <= today).scalar() or 0)

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
        "generated_at": clock.to_local(row.generated_at).isoformat() if row.generated_at else None,
    }
