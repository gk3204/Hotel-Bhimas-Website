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
from sqlalchemy import func
from sqlalchemy.orm import Session

from database import SessionLocal
from models import (Booking, BookingItem, CardIssuance, CashShift, FolioCharge,
                    FraudAlert, Guest, MaintenanceTicket, Payment, Room, RoomType,
                    User, DayCloseSummary)
from utils.auth_utils import require_admin
from utils.settings import get_reports_config

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/reports", tags=["Reports"])

# Booking statuses that count as a real (non-cancelled) stay.
_LIVE_STATUSES = ("confirmed", "checked_in", "checked_out")
# OTA channels recognised today (prompt 05 booking_source vocab; prompt 17 adds the commission model).
OTA_SOURCES = ("makemytrip", "goibibo", "booking_com", "agoda", "other_ota")


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


def sales_by_date(db, dfrom, dto):
    """Per-day recognised sales from non-void folio charges posted that day: room revenue,
    other revenue (food/minibar/laundry/extra_bed/misc), discounts (negative), gross, and the
    GST split. Returns {rows, totals}."""
    rows = []
    for day in _days(dfrom, dto):
        room_rev = round(_sum_charges(db, day, types=("room",)), 2)
        other_rev = round(_sum_charges(db, day, exclude=("room", "payment", "discount")), 2)
        discount = round(_sum_charges(db, day, types=("discount",)), 2)  # negative
        _rows, taxable, cgst, sgst, gross = _gst_split(_charge_pairs(db, day))
        rows.append({"date": str(day), "room_revenue": room_rev, "other_revenue": other_rev,
                     "discount": discount, "gross_sales": gross, "taxable": taxable,
                     "cgst": cgst, "sgst": sgst})
    totals = {
        "room_revenue": round(sum(r["room_revenue"] for r in rows), 2),
        "other_revenue": round(sum(r["other_revenue"] for r in rows), 2),
        "discount": round(sum(r["discount"] for r in rows), 2),
        "gross_sales": round(sum(r["gross_sales"] for r in rows), 2),
        "taxable": round(sum(r["taxable"] for r in rows), 2),
        "cgst": round(sum(r["cgst"] for r in rows), 2),
        "sgst": round(sum(r["sgst"] for r in rows), 2),
    }
    return {"from": str(dfrom), "to": str(dto), "rows": rows, "totals": totals}


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
    for src in OTA_SOURCES:
        q = db.query(
            func.count(Booking.booking_id),
            func.coalesce(func.sum(Booking.total_amount), 0),
            func.coalesce(func.sum(func.coalesce(Booking.ota_net_payout, Booking.total_amount)), 0),
        ).filter(Booking.booking_source == src, Booking.status.in_(_LIVE_STATUSES),
                 Booking.check_in >= dfrom, Booking.check_in <= dto)
        count, revenue, net_payout = q.one()
        revenue = round(float(revenue or 0), 2)
        net_payout = round(float(net_payout or 0), 2)
        rows.append({"ota": src, "bookings": int(count), "room_revenue": revenue,
                     "commission_est": round(revenue - net_payout, 2),
                     "net_payout": net_payout})
    totals = {"bookings": sum(r["bookings"] for r in rows),
              "room_revenue": round(sum(r["room_revenue"] for r in rows), 2),
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
# export dispatch (csv / pdf / json)
# ============================================================

def _csv_response(filename, columns, rows, totals_row=None):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(columns)
    for r in rows:
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
    cols = ["Date", "Room Revenue", "Other Revenue", "Discount", "Gross Sales", "Taxable", "CGST", "SGST"]
    rows = [[r["date"], r["room_revenue"], r["other_revenue"], r["discount"], r["gross_sales"],
             r["taxable"], r["cgst"], r["sgst"]] for r in data["rows"]]
    t = data["totals"]
    totals = ["TOTAL", t["room_revenue"], t["other_revenue"], t["discount"], t["gross_sales"],
              t["taxable"], t["cgst"], t["sgst"]]
    return _export_or_json(format, data, title="Daily Sales Report", columns=cols, rows=rows,
                           totals_row=totals, meta=_meta(dfrom, dto), filename="daily_sales")


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

    return {
        "date": str(today),
        "occupancy": digest["occupancy"],
        "revenue_today": digest["revenue_today"],
        "expected_cash_today": digest["expected_cash_today"],
        "collections_today": collections,
        "arrivals": digest["arrivals"],
        "departures": digest["departures"],
        "pending_arrivals": pending,
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
    open_tickets = db.query(MaintenanceTicket).filter(
        MaintenanceTicket.status.notin_(("verified", "closed"))).all()
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
        "cash_collected": _f(row.cash_collected), "cash_expected": _f(row.cash_expected),
        "cash_variance": _f(row.cash_variance),
        "arrivals": row.arrivals, "departures": row.departures, "open_alerts": row.open_alerts,
        "folios_posted": row.folios_posted, "generated_by": row.generated_by,
        "generated_at": row.generated_at.isoformat() if row.generated_at else None,
    }
