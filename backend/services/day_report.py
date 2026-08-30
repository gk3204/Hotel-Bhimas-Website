"""Owner day/week report (prompt 15 follow-up).

Builds a COMPLETE previous-day picture for the owner — occupancy, revenue (incl. split by booking
source), room-by-room arrivals/departures, maintenance opened/closed, housekeeping cleaned/inspected,
room-service, and a few extras — then renders it to a PDF for WhatsApp + email delivery.

Reuses the recognised-revenue helpers in routers/reports.py so every figure reconciles with the
admin Reports screens. Every section is best-effort: a failing sub-query returns an empty section
rather than breaking the whole report (the digest job must never crash).
"""
import logging
from datetime import date, datetime, timedelta

from sqlalchemy import func

from models import (Booking, Guest, Room, BookingItem, MaintenanceTicket, HousekeepingTask,
                    GuestRequest, FraudAlert, Folio)

logger = logging.getLogger(__name__)

_LIVE = ("confirmed", "checked_in", "checked_out")


def _safe(fn, default):
    try:
        return fn()
    except Exception as e:                       # a bad section must not sink the report
        logger.warning(f"day_report section failed: {e}")
        return default


def _room_labels(db, booking_id) -> str:
    rows = (db.query(Room.room_number)
            .join(BookingItem, BookingItem.room_id == Room.room_id)
            .filter(BookingItem.booking_id == booking_id).all())
    nums = [r[0] for r in rows if r[0]]
    return ", ".join(nums) if nums else "—"


def _pretty_source(src) -> str:
    return (src or "walk_in").replace("_", " ").title()


def compute_day_report(db, day=None) -> dict:
    """The full previous-day report as a structured dict (day defaults to yesterday)."""
    if day is None:
        day = date.today() - timedelta(days=1)
    if isinstance(day, datetime):
        day = day.date()

    from routers.reports import (occupancy_data, sales_by_date, collections_summary,
                                 room_service_by_date, room_service_by_item, comp_room_value)

    # ---- headline: occupancy + revenue + cash ----
    def _headline():
        occ = occupancy_data(db, day, day, "day")
        row = occ["rows"][0] if occ["rows"] else {"occupied": 0, "total_rooms": 0, "occupancy_pct": 0.0}
        sales = sales_by_date(db, day, day)["totals"]
        coll = collections_summary(db, day, day)
        occupied = int(row["occupied"])
        total_rooms = int(row["total_rooms"])
        room_rev = float(sales.get("room_revenue", 0))
        gross = float(sales.get("gross_sales", 0))
        adr = round(room_rev / occupied, 2) if occupied else 0.0
        revpar = round(room_rev / total_rooms, 2) if total_rooms else 0.0
        return {
            "occupied": occupied, "total_rooms": total_rooms,
            "occupancy_pct": row["occupancy_pct"],
            "room_revenue": round(room_rev, 2), "gross_sales": round(gross, 2),
            "other_revenue": round(float(sales.get("other_revenue", 0)), 2),
            "adr": adr, "revpar": revpar,
            "comp_value": round(comp_room_value(db, day), 2),
            "collected": coll["total_collected"], "cash": coll["cash"], "card": coll["card"],
            "upi": coll["upi"], "bank": coll["bank"], "refunds": coll["refunds"],
        }

    # ---- occupancy + revenue split by booking source ----
    def _by_source():
        # room-nights occupied on `day` per source
        occ = dict(db.query(Booking.booking_source, func.coalesce(func.sum(BookingItem.quantity), 0))
                   .join(BookingItem, BookingItem.booking_id == Booking.booking_id)
                   .filter(Booking.status.in_(_LIVE), Booking.check_in <= day, Booking.check_out > day)
                   .group_by(Booking.booking_source).all())
        # collections on `day` per source (paid payments joined to the booking's source)
        from models import Payment
        rev = dict(db.query(Booking.booking_source, func.coalesce(func.sum(Payment.amount), 0))
                   .join(Booking, Booking.booking_id == Payment.booking_id)
                   .filter(Payment.status == "paid", func.date(Payment.created_at) == day)
                   .group_by(Booking.booking_source).all())
        sources = set(occ) | set(rev)
        occ_total = sum(int(v or 0) for v in occ.values()) or 0
        rev_total = sum(float(v or 0) for v in rev.values()) or 0.0
        rows = []
        for s in sorted(sources, key=lambda x: -float(rev.get(x, 0) or 0)):
            nights = int(occ.get(s, 0) or 0)
            revenue = round(float(rev.get(s, 0) or 0), 2)
            rows.append({
                "source": _pretty_source(s),
                "room_nights": nights,
                "occ_pct": round(100.0 * nights / occ_total, 1) if occ_total else 0.0,
                "revenue": revenue,
                "revenue_pct": round(100.0 * revenue / rev_total, 1) if rev_total else 0.0,
            })
        return rows

    # ---- arrivals / departures (room + guest) ----
    def _arrivals():
        out = []
        for b in (db.query(Booking).filter(func.date(Booking.checked_in_at) == day)
                  .order_by(Booking.checked_in_at).all()):
            g = db.query(Guest).filter(Guest.guest_id == b.guest_id).first()
            out.append({"room": _room_labels(db, b.booking_id),
                        "guest": g.name if g else "—", "source": _pretty_source(b.booking_source)})
        return out

    def _departures():
        out = []
        for b in (db.query(Booking).filter(func.date(Booking.checked_out_at) == day)
                  .order_by(Booking.checked_out_at).all()):
            g = db.query(Guest).filter(Guest.guest_id == b.guest_id).first()
            out.append({"room": _room_labels(db, b.booking_id), "guest": g.name if g else "—"})
        return out

    # ---- maintenance opened / closed on the day ----
    def _maintenance():
        def _fmt(t):
            rn = db.query(Room.room_number).filter(Room.room_id == t.room_id).scalar() if t.room_id else None
            return {"id": t.id, "where": rn or t.area or "common", "issue": (t.issue or "")[:80],
                    "priority": t.priority, "status": t.status, "source": t.source}
        opened = [_fmt(t) for t in db.query(MaintenanceTicket)
                  .filter(func.date(MaintenanceTicket.created_at) == day).all()]
        closed = [_fmt(t) for t in db.query(MaintenanceTicket)
                  .filter(MaintenanceTicket.status.in_(("resolved", "verified", "closed")),
                          func.coalesce(func.date(MaintenanceTicket.verified_at),
                                        func.date(MaintenanceTicket.resolved_at)) == day).all()]
        open_now = int(db.query(func.count(MaintenanceTicket.id))
                       .filter(MaintenanceTicket.status.notin_(("resolved", "verified", "closed"))).scalar() or 0)
        return {"opened": opened, "closed": closed, "opened_count": len(opened),
                "closed_count": len(closed), "open_now": open_now}

    # ---- housekeeping cleaned / inspected on the day ----
    def _housekeeping():
        def _rooms(q):
            labels = []
            for t in q.all():
                rn = db.query(Room.room_number).filter(Room.room_id == t.room_id).scalar()
                if rn:
                    labels.append(rn)
            return labels
        cleaned = _rooms(db.query(HousekeepingTask)
                         .filter(func.date(HousekeepingTask.done_at) == day))
        inspected = _rooms(db.query(HousekeepingTask)
                           .filter(func.date(HousekeepingTask.inspected_at) == day))
        pending_clean = int(db.query(func.count(HousekeepingTask.id))
                            .filter(HousekeepingTask.status != "done").scalar() or 0)
        return {"cleaned": cleaned, "inspected": inspected,
                "cleaned_count": len(cleaned), "inspected_count": len(inspected),
                "pending_clean": pending_clean}

    # ---- room service ----
    def _room_service():
        rs = room_service_by_date(db, day, day)["totals"]
        items = room_service_by_item(db, day, day)
        top = [{"name": r.get("name"), "qty": r.get("qty"), "amount": r.get("amount")}
               for r in (items.get("rows", [])[:5])]
        return {"orders": int(rs.get("orders", 0)), "revenue": round(float(rs.get("gross", 0)), 2),
                "top_items": top}

    # ---- extras ----
    def _extras():
        new_bookings = int(db.query(func.count(Booking.booking_id))
                           .filter(func.date(Booking.created_at) == day).scalar() or 0)
        cancellations = int(db.query(func.count(Booking.booking_id))
                            .filter(func.date(Booking.cancelled_at) == day).scalar() or 0)
        today = date.today()
        overstays = int(db.query(func.count(Booking.booking_id))
                        .filter(Booking.status == "checked_in", Booking.check_out < today).scalar() or 0)
        open_alerts = int(db.query(func.count(FraudAlert.id))
                          .filter(FraudAlert.status == "open").scalar() or 0)
        open_complaints = int(db.query(func.count(MaintenanceTicket.id))
                              .filter(MaintenanceTicket.source == "guest",
                                      MaintenanceTicket.status.notin_(("resolved", "verified", "closed"))).scalar() or 0)
        return {"new_bookings": new_bookings, "cancellations": cancellations, "overstays": overstays,
                "open_alerts": open_alerts, "open_complaints": open_complaints}

    return {
        "day": str(day),
        "headline": _safe(_headline, {}),
        "by_source": _safe(_by_source, []),
        "arrivals": _safe(_arrivals, []),
        "departures": _safe(_departures, []),
        "maintenance": _safe(_maintenance, {"opened": [], "closed": [], "opened_count": 0, "closed_count": 0, "open_now": 0}),
        "housekeeping": _safe(_housekeeping, {"cleaned": [], "inspected": [], "cleaned_count": 0, "inspected_count": 0, "pending_clean": 0}),
        "room_service": _safe(_room_service, {"orders": 0, "revenue": 0.0, "top_items": []}),
        "extras": _safe(_extras, {}),
    }


def compute_week_report(db, week_end=None) -> dict:
    """7-day owner report ending on `week_end` (default yesterday): per-day headline + week totals."""
    if week_end is None:
        week_end = date.today() - timedelta(days=1)
    days = [week_end - timedelta(days=i) for i in range(6, -1, -1)]
    daily = [compute_day_report(db, d) for d in days]
    totals = {
        "room_revenue": round(sum(d["headline"].get("room_revenue", 0) for d in daily), 2),
        "gross_sales": round(sum(d["headline"].get("gross_sales", 0) for d in daily), 2),
        "collected": round(sum(d["headline"].get("collected", 0) for d in daily), 2),
        "arrivals": sum(len(d["arrivals"]) for d in daily),
        "departures": sum(len(d["departures"]) for d in daily),
        "tickets_opened": sum(d["maintenance"]["opened_count"] for d in daily),
        "tickets_closed": sum(d["maintenance"]["closed_count"] for d in daily),
        "rs_revenue": round(sum(d["room_service"]["revenue"] for d in daily), 2),
        "avg_occupancy_pct": round(sum(d["headline"].get("occupancy_pct", 0) for d in daily) / len(daily), 1) if daily else 0.0,
    }
    return {"from": str(days[0]), "to": str(week_end), "totals": totals,
            "days": [{"day": d["day"], "occupancy_pct": d["headline"].get("occupancy_pct", 0),
                      "room_revenue": d["headline"].get("room_revenue", 0),
                      "collected": d["headline"].get("collected", 0),
                      "arrivals": len(d["arrivals"]), "departures": len(d["departures"])}
                     for d in daily]}


def summary_text(report: dict) -> str:
    """The short WhatsApp caption / text ping accompanying the PDF."""
    h = report.get("headline", {})
    return (f"Hotel Bhimas — {report.get('day')}\n"
            f"Occupancy {h.get('occupancy_pct', 0)}% ({h.get('occupied', 0)}/{h.get('total_rooms', 0)})\n"
            f"Revenue ₹{h.get('gross_sales', 0):,.0f} · Collected ₹{h.get('collected', 0):,.0f}\n"
            f"Arrivals {len(report.get('arrivals', []))} · Departures {len(report.get('departures', []))}\n"
            f"Tickets +{report.get('maintenance', {}).get('opened_count', 0)}/"
            f"-{report.get('maintenance', {}).get('closed_count', 0)} · "
            f"Alerts {report.get('extras', {}).get('open_alerts', 0)}\n"
            f"Full report attached (PDF).")


def render_day_report_pdf(report: dict) -> str:
    """Render the day report dict to a PDF file; returns the path."""
    from utils.report_pdf import generate_day_report_pdf
    return generate_day_report_pdf(report)


def render_week_report_pdf(report: dict) -> str:
    from utils.report_pdf import generate_week_report_pdf
    return generate_week_report_pdf(report)
