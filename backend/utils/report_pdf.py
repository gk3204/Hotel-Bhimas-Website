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
    logo_path = ("assets/logo-gold-print.png" if os.path.exists("assets/logo-gold-print.png")   # v7e: 600px copy - the 4096px original made every PDF ~270 KB and slow
                 else "assets/logo-gold.png")
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

    ae = report.get("arrival_exceptions") or {}
    if ae:
        _section(elements, styles,
                 f"Arrival exceptions — early check-ins {ae.get('early_checkins', 0)} "
                 f"(Rs {float(ae.get('early_charged') or 0):,.0f}), late arrivals {ae.get('late_arrivals', 0)}, "
                 f"hourly extensions {ae.get('hourly_extensions', 0)} (Rs {float(ae.get('extension_charged') or 0):,.0f}), "
                 f"no-shows {ae.get('no_shows', 0)}",
                 ["Guest", "Room", "Kind", "Deviation / hours", "Charge", "Basis"],
                 [[r.get("guest"), r.get("room"), r.get("kind"),
                   (f"{r['hours']} h" if r.get("hours") else
                    (f"{abs(int(r.get('deviation_minutes') or 0)) // 60}h {abs(int(r.get('deviation_minutes') or 0)) % 60:02d}m")),
                   f"Rs {float(r.get('charge') or 0):,.2f}", r.get("basis")] for r in ae.get("rows", [])])

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



def _fit_widths(columns, rows, avail):
    """v7p: column widths that fit the page - a sentence column gets room to read, a time its width."""
    n = len(columns)
    weights = []
    for i in range(n):
        vals = [str(columns[i])] + [_cell(r[i]) for r in rows if isinstance(r, (list, tuple)) and i < len(r)]
        word = max((len(w) for v in vals for w in v.split()), default=4)
        text = max((len(v) for v in vals), default=4)
        # a short value (a time with its "(+4m)") keeps its width; a sentence column is capped
        weights.append(max(7, min(word, 16), min(text, 18) if text <= 40 else 40))
    total = sum(weights) or 1
    return [avail * w / total for w in weights]


def _flag_table(columns, rows, flags, styles, avail):
    """A fitted table whose flagged rows are shaded red (the owner's 'fraud rows in a colour')."""
    data = [[Paragraph(f"<b>{c}</b>", styles["Normal"]) for c in columns]]
    for r in rows:
        data.append([Paragraph(_cell(v), styles["Normal"]) for v in r])
    t = Table(data, repeatRows=1, colWidths=_fit_widths(columns, rows, avail))
    style = [("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F3E7C6")),
             ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#CCCCCC")),
             ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]
    for i, flagged in enumerate(flags or [], start=1):
        if flagged:
            style.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor("#F8D7DA")))
            style.append(("TEXTCOLOR", (-1, i), (-1, i), colors.HexColor("#B00020")))
    t.setStyle(TableStyle(style))
    return t


def _turnaround_sections(el, styles, data, avail):
    """KPIs, room stays and viewing cards of a room_turnaround_data() result."""
    from routers.reports import kpi_pairs, room_turnaround_tables
    head = ParagraphStyle(name="TASec", parent=styles["Normal"], fontSize=10,
                          textColor=colors.HexColor("#B8860B"), spaceBefore=10, spaceAfter=4,
                          fontName="Helvetica-Bold", keepWithNext=1)
    pairs = kpi_pairs(data["kpis"])
    el.append(Paragraph("Room turnaround &amp; cards - KPIs", head))
    cells, row = [], []
    for lbl, val in pairs:
        row.append(Paragraph(f"<font color='#666666'>{lbl}</font><br/><b>{val}</b>", styles["Normal"]))
        if len(row) == 7:
            cells.append(row)
            row = []
    if row:
        cells.append(row + [""] * (7 - len(row)))
    kt = Table(cells, colWidths=[avail / 7] * 7)
    kt.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#E0D3A8")),
                            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#FBF8F0")),
                            ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    el.append(kt)
    (sc, sr, sf), (vc, vr, vf) = room_turnaround_tables(data)
    el.append(Paragraph(f"Room stays - every step and the minutes between (red = a red flag): {len(sr)}", head))
    el.append(_flag_table(sc, sr, sf, styles, avail) if sr else Paragraph("— no room checked out —", styles["Normal"]))
    el.append(Paragraph(f"Viewing cards - and whether the room was checked in after: {len(vr)}", head))
    el.append(_flag_table(vc, vr, vf, styles, avail) if vr else Paragraph("— no viewing card —", styles["Normal"]))


def generate_turnaround_pdf(data, period_label) -> str:
    """v7p: the Room Turnaround & Cards report on its own (Reports tab PDF). Landscape."""
    from reportlab.lib.pagesizes import landscape
    path = os.path.join(tempfile.gettempdir(), f"room_turnaround_{data.get('from')}_{data.get('to')}.pdf")
    doc = SimpleDocTemplate(path, pagesize=landscape(A4), rightMargin=18, leftMargin=18,
                            topMargin=18, bottomMargin=18)
    styles = getSampleStyleSheet()
    styles["Normal"].fontSize = 7
    styles["Normal"].leading = 8.5
    el = []
    _brand_header(el, styles)
    _title(el, styles, f"Room turnaround &amp; cards - {period_label}")
    s = data.get("settings") or {}
    el.append(Paragraph(
        f"Minutes in brackets are from the step before. Cleaning minimum {s.get('cleaning_min_minutes', 0):g} min, "
        f"limit {s.get('cleaning_max_minutes', 0):g} min; a viewing counts as converted when the room is checked in "
        f"within {int(s.get('viewing_checkin_window_min', 180)) // 60} h.", styles["Normal"]))
    _turnaround_sections(el, styles, data, doc.width)
    doc.build(el)
    return path


def generate_hk_owner_report_pdf(day, room_detail, maintenance, lost_cards, turnaround=None) -> str:
    """v7g (owner): "everyday along with daily report need 1 report to owner which will contain Room
    detail (HK + Fraud) with fraud rows marked in a colour, maintenance detail and lost key cards for
    the previous day (12am-12am)". Landscape - Room Detail is 12 columns wide.

    Each argument is (columns, rows[, totals]); `room_detail` also carries a list of booleans - which
    rows had a fraud alert - and those rows are shaded red."""
    from reportlab.lib.pagesizes import landscape
    rd_cols, rd_rows, rd_flags = room_detail
    path = os.path.join(tempfile.gettempdir(), f"hk_owner_report_{day}.pdf")
    doc = SimpleDocTemplate(path, pagesize=landscape(A4), rightMargin=18, leftMargin=18,
                            topMargin=18, bottomMargin=18)
    styles = getSampleStyleSheet()
    styles["Normal"].fontSize = 7
    styles["Normal"].leading = 8.5
    el = []
    _brand_header(el, styles)
    _title(el, styles, f"Housekeeping, frauds, maintenance & lost cards - {day:%d %b %Y} (00:00-24:00)")
    n_fraud = sum(1 for f in rd_flags if f)
    el.append(Paragraph(
        f"Rooms checked out: <b>{len(rd_rows)}</b> &nbsp;·&nbsp; with a fraud alert: "
        f"<font color='#B00020'><b>{n_fraud}</b></font> (shaded red) &nbsp;·&nbsp; maintenance tickets: "
        f"<b>{len(maintenance[1])}</b> &nbsp;·&nbsp; lost key cards: <b>{len(lost_cards[1])}</b>",
        styles["Normal"]))

    head_style = ParagraphStyle(name="HKSec", parent=styles["Normal"], fontSize=10,
                                textColor=colors.HexColor("#B8860B"), spaceBefore=10, spaceAfter=4,
                                fontName="Helvetica-Bold")
    el.append(Paragraph("Room Detail (housekeeping + frauds)", head_style))
    if rd_rows:
        # v7p: fitted to the page - the frauds column is plain sentences now
        el.append(_flag_table(rd_cols, rd_rows, rd_flags, styles, doc.width))
    else:
        el.append(Paragraph("— no room checked out —", styles["Normal"]))

    _section(el, styles, "Maintenance detail", maintenance[0],
             maintenance[1] + ([maintenance[2]] if len(maintenance) > 2 and maintenance[2] else []))
    _section(el, styles, "Lost key cards", lost_cards[0],
             lost_cards[1] + ([lost_cards[2]] if len(lost_cards) > 2 and lost_cards[2] else []))
    if turnaround is not None:      # v7p (owner): turnaround KPIs + red flags in the daily report
        _turnaround_sections(el, styles, turnaround, doc.width)
    doc.build(el)
    return path
