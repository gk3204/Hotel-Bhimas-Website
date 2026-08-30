"""Rich multi-section owner day/week report PDFs (reportlab).

Kept separate from utils/pdf_generator.py (which is single-table) so the sectioned layout has room
to grow. Reuses the same brand header block.
"""
import os
import tempfile
from datetime import datetime

from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib import colors
from reportlab.lib.units import inch


def _brand_header(elements, styles):
    hotel_info = Paragraph(
        "<b>HOTEL BHIMAS</b><br/>Luxury &amp; Comfort Stay<br/>"
        "42, G Car Street, Tirupati - 517501<br/>GSTIN: 37AAACK9397F1Z3",
        styles["Normal"])
    logo_path = "assets/logo-gold.png"
    if os.path.exists(logo_path):
        elements.append(Table([[Image(logo_path, width=1.4 * inch, height=0.95 * inch), hotel_info]],
                              colWidths=[120, 350]))
    else:
        elements.append(hotel_info)
    elements.append(Spacer(1, 0.18 * inch))


def _cell(v):
    if v is None:
        return ""
    if isinstance(v, float):
        return f"{v:,.2f}"
    return str(v)


def _section(elements, styles, heading, columns, rows, note=None):
    head_style = ParagraphStyle(name="SecHead", parent=styles["Normal"], fontSize=10,
                                textColor=colors.HexColor("#B8860B"), spaceBefore=10, spaceAfter=4,
                                fontName="Helvetica-Bold")
    elements.append(Paragraph(heading, head_style))
    if not rows:
        elements.append(Paragraph(note or "— none —",
                                  ParagraphStyle(name="SecEmpty", parent=styles["Normal"],
                                                 fontSize=8, textColor=colors.grey)))
        return
    data = [[Paragraph(f"<b>{c}</b>", styles["Normal"]) for c in columns]]
    for r in rows:
        data.append([Paragraph(_cell(v), styles["Normal"]) for v in r])
    t = Table(data, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F3E7C6")),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#CCCCCC")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#FBF8F0")]),
    ]))
    elements.append(t)


def _title(elements, styles, text):
    elements.append(Paragraph(text, ParagraphStyle(
        name="RTitle", parent=styles["Title"], textColor=colors.HexColor("#B8860B"),
        fontSize=15, alignment=1, spaceAfter=8)))


def generate_day_report_pdf(report):
    """Rich owner day report. `report` = services.day_report.compute_day_report()."""
    day = report.get("day", "")
    file_path = os.path.join(tempfile.gettempdir(), f"day_report_{day}.pdf")
    doc = SimpleDocTemplate(file_path, pagesize=A4, rightMargin=22, leftMargin=22,
                            topMargin=22, bottomMargin=22)
    styles = getSampleStyleSheet()
    styles["Normal"].fontSize = 8
    elements = []
    _brand_header(elements, styles)
    _title(elements, styles, f"Daily Report — {day}")

    h = report.get("headline", {})
    _section(elements, styles, "Overview", ["Metric", "Value"], [
        ["Occupancy", f"{h.get('occupancy_pct', 0)}%  ({h.get('occupied', 0)}/{h.get('total_rooms', 0)} rooms)"],
        ["Room revenue", f"Rs {h.get('room_revenue', 0):,.2f}"],
        ["Gross sales", f"Rs {h.get('gross_sales', 0):,.2f}"],
        ["Other revenue", f"Rs {h.get('other_revenue', 0):,.2f}"],
        ["ADR / RevPAR", f"Rs {h.get('adr', 0):,.2f}  /  Rs {h.get('revpar', 0):,.2f}"],
        ["Comp value given", f"Rs {h.get('comp_value', 0):,.2f}"],
        ["Collected (cash/card/upi/bank)",
         f"Rs {h.get('collected', 0):,.2f}  ({h.get('cash', 0):,.0f}/{h.get('card', 0):,.0f}/{h.get('upi', 0):,.0f}/{h.get('bank', 0):,.0f})"],
        ["Refunds", f"Rs {h.get('refunds', 0):,.2f}"],
    ])

    _section(elements, styles, "Revenue & occupancy by source",
             ["Source", "Room-nights", "Occ %", "Collected", "Rev %"],
             [[r["source"], r["room_nights"], f"{r['occ_pct']}%", f"Rs {r['revenue']:,.2f}", f"{r['revenue_pct']}%"]
              for r in report.get("by_source", [])])

    _section(elements, styles, f"Arrivals ({len(report.get('arrivals', []))})",
             ["Room", "Guest", "Source"],
             [[a["room"], a["guest"], a["source"]] for a in report.get("arrivals", [])])
    _section(elements, styles, f"Departures ({len(report.get('departures', []))})",
             ["Room", "Guest"],
             [[d["room"], d["guest"]] for d in report.get("departures", [])])

    mnt = report.get("maintenance", {})
    _section(elements, styles,
             f"Maintenance opened ({mnt.get('opened_count', 0)})  ·  open now: {mnt.get('open_now', 0)}",
             ["#", "Where", "Issue", "Priority", "Source"],
             [[t["id"], t["where"], t["issue"], t["priority"], t["source"]] for t in mnt.get("opened", [])])
    _section(elements, styles, f"Maintenance closed yesterday ({mnt.get('closed_count', 0)})",
             ["#", "Where", "Issue"],
             [[t["id"], t["where"], t["issue"]] for t in mnt.get("closed", [])])

    hk = report.get("housekeeping", {})
    _section(elements, styles, "Housekeeping", ["Activity", "Rooms"], [
        [f"Cleaned ({hk.get('cleaned_count', 0)})", ", ".join(hk.get("cleaned", [])) or "—"],
        [f"Inspected ({hk.get('inspected_count', 0)})", ", ".join(hk.get("inspected", [])) or "—"],
        ["Pending clean/inspect", str(hk.get("pending_clean", 0))],
    ])

    rs = report.get("room_service", {})
    _section(elements, styles,
             f"Room service — {rs.get('orders', 0)} order(s), Rs {rs.get('revenue', 0):,.2f}",
             ["Item", "Qty", "Amount"],
             [[i.get("name"), i.get("qty"), f"Rs {float(i.get('amount') or 0):,.2f}"] for i in rs.get("top_items", [])])

    ex = report.get("extras", {})
    _section(elements, styles, "Other", ["Metric", "Count"], [
        ["New bookings made", ex.get("new_bookings", 0)],
        ["Cancellations", ex.get("cancellations", 0)],
        ["Current overstays", ex.get("overstays", 0)],
        ["Open fraud/ops alerts", ex.get("open_alerts", 0)],
        ["Open guest complaints", ex.get("open_complaints", 0)],
    ])

    elements.append(Spacer(1, 0.15 * inch))
    elements.append(Paragraph(f"Generated {datetime.now().strftime('%d-%m-%Y %H:%M')}",
                              ParagraphStyle(name="RFoot", parent=styles["Normal"], fontSize=7,
                                             textColor=colors.grey, alignment=1)))
    doc.build(elements)
    return file_path


def generate_week_report_pdf(report):
    """Weekly owner report: week totals + per-day table."""
    file_path = os.path.join(tempfile.gettempdir(), f"week_report_{report.get('to', '')}.pdf")
    doc = SimpleDocTemplate(file_path, pagesize=A4, rightMargin=22, leftMargin=22,
                            topMargin=22, bottomMargin=22)
    styles = getSampleStyleSheet()
    styles["Normal"].fontSize = 8
    elements = []
    _brand_header(elements, styles)
    _title(elements, styles, f"Weekly Report — {report.get('from')} to {report.get('to')}")
    t = report.get("totals", {})
    _section(elements, styles, "Week totals", ["Metric", "Value"], [
        ["Avg occupancy", f"{t.get('avg_occupancy_pct', 0)}%"],
        ["Room revenue", f"Rs {t.get('room_revenue', 0):,.2f}"],
        ["Gross sales", f"Rs {t.get('gross_sales', 0):,.2f}"],
        ["Collected", f"Rs {t.get('collected', 0):,.2f}"],
        ["Arrivals / Departures", f"{t.get('arrivals', 0)} / {t.get('departures', 0)}"],
        ["Tickets opened / closed", f"{t.get('tickets_opened', 0)} / {t.get('tickets_closed', 0)}"],
        ["Room-service revenue", f"Rs {t.get('rs_revenue', 0):,.2f}"],
    ])
    _section(elements, styles, "Day by day",
             ["Day", "Occ %", "Room rev", "Collected", "Arr", "Dep"],
             [[d["day"], f"{d['occupancy_pct']}%", f"Rs {d['room_revenue']:,.0f}",
               f"Rs {d['collected']:,.0f}", d["arrivals"], d["departures"]] for d in report.get("days", [])])
    doc.build(elements)
    return file_path
