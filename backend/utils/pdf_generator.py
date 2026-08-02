from reportlab.lib.pagesizes import A4
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer,
    Table, TableStyle, Image
)
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib import colors
from reportlab.lib.units import inch
from datetime import datetime
import os

room_extra_details = {
    1: {
        "size": "200 sq.ft",
        "capacity": "2 Adults",
        "desc": "Comfortable and budget-friendly room ideal for short stays.",
        "features": ["Non-AC", "24x7 Hot Water", "Free Parking", "TV"],
    },
    2: {
        "size": "250 sq.ft",
        "capacity": "2 Adults + 1 Child",
        "desc": "Spacious deluxe room with AC and modern amenities.",
        "features": ["AC Room", "24x7 Hot Water", "Free Parking", "TV"],
    },
    3: {
        "size": "350 sq.ft",
        "capacity": "4 Adults + 1 Child",
        "desc": "Perfect choice for families and small groups.",
        "features": ["AC", "Family Room", "24x7 Hot Water", "TV"],
    },
    4: {
        "size": "450 sq.ft",
        "capacity": "5 Adults + 2 Children",
        "desc": "Large room suitable for bigger families and group stays.",
        "features": ["AC", "Large Space", "24x7 Hot Water", "TV"],
    },
}

def generate_booking_pdf(booking_data, payment_data):
    """
    Generate booking confirmation PDF
    
    Args:
        booking_data: Dict with booking details (booking_id, guest_*, check_in/out, amounts, booking_items list)
        payment_data: Dict with payment details {'payment_id_gateway', 'order_id', 'gateway', 'status'}
    """
    import tempfile
    # Use temp directory that works on Railway
    temp_dir = tempfile.gettempdir()
    file_path = os.path.join(temp_dir, f"booking_{booking_data['booking_id']}.pdf")

    doc = SimpleDocTemplate(
        file_path,
        pagesize=A4,
        rightMargin=25,
        leftMargin=25,
        topMargin=25,
        bottomMargin=25
    )
    
    # Get details from first room type (for display)
    room_details = {}
    if booking_data['booking_items']:
        first_room_type_id = booking_data['booking_items'][0]['room_type_id']
        room_details = room_extra_details.get(first_room_type_id, {})

    styles = getSampleStyleSheet()

    styles["Normal"].fontSize = 9
    styles["Heading2"].fontSize = 11
    styles["Heading3"].fontSize = 10
    elements = []

    # =====================================================
    # HEADER (Logo + Hotel Details)
    # =====================================================

    logo_path = "assets/logo-gold.png"

    header_data = []

    if os.path.exists(logo_path):
        logo = Image(logo_path, width=1.5 * inch, height=1 * inch)

        hotel_info = Paragraph(
            "<b>HOTEL BHIMAS</b><br/>"
            "Luxury & Comfort Stay<br/>"
            "42, G Car Street, Tirupati - 517501<br/>"
            "Landline: +91877-2225744<br/>"
            "Mobile: +919347172758<br/>"
            "GSTIN: 37AAACK9397F1Z3",
            styles["Normal"]
        )

        header_data.append([logo, hotel_info])

        header_table = Table(header_data, colWidths=[120, 350])
        elements.append(header_table)

    elements.append(Spacer(1, 0.4 * inch))

    # =====================================================
    # TITLE
    # =====================================================

    title_style = ParagraphStyle(
        name="InvoiceTitle",
        parent=styles["Title"],
        textColor=colors.HexColor("#B8860B"),
        fontSize=16,
        alignment=1,  # Center
        spaceAfter=20
    )

    elements.append(Paragraph("BOOKING CONFIRMATION", title_style))

    # =====================================================
    # BOOKING DETAILS
    # =====================================================

    elements.append(Paragraph("<b>Booking Information</b>", styles["Heading2"]))
    elements.append(Spacer(1, 0.2 * inch))

    # Build room types string from all booking items
    room_types_list = [item['room_type_name'] for item in booking_data['booking_items']]
    room_types_str = ", ".join(room_types_list) if room_types_list else "N/A"

    arrival_time = booking_data.get('check_in_time')
    arrival_display = arrival_time[:5] if arrival_time else "N/A"  # "HH:MM:SS" -> "HH:MM"

    booking_table_data = [
        ["Booking ID", str(booking_data['booking_id'])],
        ["Room Type(s)", room_types_str],
        ["Check-in", booking_data['check_in'].strftime("%d-%m-%Y")],
        ["Expected Arrival Time", arrival_display],
        ["Check-out", booking_data['check_out'].strftime("%d-%m-%Y")],
        ["Status", booking_data['status'].upper()],
    ]

    booking_table = Table(booking_table_data, colWidths=[150, 330])
    booking_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (0, -1), colors.HexColor("#f5f5f5")),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('TOPPADDING', (0, 0), (-1, -1), 2)
    ]))

    elements.append(booking_table)
    elements.append(Spacer(1, 0.4 * inch))

    # =====================================================
    # GUEST DETAILS
    # =====================================================

    elements.append(Paragraph("<b>Guest Information</b>", styles["Heading2"]))
    elements.append(Spacer(1, 0.2 * inch))

    guest_table_data = [
        ["Name", booking_data['guest_name']],
        ["Phone", booking_data['guest_phone']],
        ["Email", booking_data['guest_email'] or "N/A"],
    ]

    guest_table = Table(guest_table_data, colWidths=[150, 330])
    guest_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (0, -1), colors.HexColor("#f5f5f5")),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('TOPPADDING', (0, 0), (-1, -1), 2)
    ]))

    elements.append(guest_table)
    elements.append(Spacer(1, 0.15 * inch))

    # =====================================================
    # ROOM ITEMS DETAILS (All booked rooms)
    # =====================================================

    elements.append(Paragraph("<b>Room Items</b>", styles["Heading2"]))
    elements.append(Spacer(1, 0.2 * inch))

    room_items_data = [["Room Type", "Quantity", "Price/Night", "Total"]]
    
    for item in booking_data['booking_items']:
        room_items_data.append([
            item['room_type_name'],
            str(item['quantity']),
            f"Rs. {float(item['price_per_night']):,.2f}",
            f"Rs. {float(item['total_amount']):,.2f}"
        ])

    room_items_table = Table(room_items_data, colWidths=[150, 80, 100, 100])
    room_items_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#B8860B")),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 10),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor("#f5f5f5")])
    ]))

    elements.append(room_items_table)
    elements.append(Spacer(1, 0.3 * inch))

    # =====================================================
    # PAYMENT SUMMARY (Invoice Style)
    # =====================================================

    elements.append(Paragraph("<b>Payment Summary</b>", styles["Heading2"]))
    elements.append(Spacer(1, 0.3 * inch))

    nights = (booking_data['check_out'] - booking_data['check_in']).days
    nights = nights if nights > 0 else 1

    base_amount = booking_data['base_amount']
    gst_amount = booking_data['gst_amount']
    discount_amount = booking_data.get('discount_amount', 0) or 0
    convenience_fee = booking_data['convenience_fee']
    convenience_gst = booking_data['convenience_gst']
    grand_total = booking_data['grand_total']
    total_convnience_fee = convenience_fee + convenience_gst

    payment_summary_data = [
        [f"{nights} Night(s)", f"Rs. {base_amount:,.2f}"],
    ]
    if discount_amount > 0:
        payment_summary_data.append(["Discount", f"- Rs. {discount_amount:,.2f}"])
    payment_summary_data.extend([
        ["GST", f"Rs. {gst_amount:,.2f}"],
        ["Convenience Fee", f"Rs. {total_convnience_fee:,.2f}"],
        ["GRAND TOTAL", f"Rs. {grand_total:,.2f}"],
    ])
    
    payment_summary_table = Table(payment_summary_data, colWidths=[300, 150])
    payment_summary_table.setStyle(TableStyle([
        ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
        ('LINEABOVE', (0, -1), (-1, -1), 1, colors.black),
        ('TEXTCOLOR', (0, -1), (-1, -1), colors.HexColor("#B8860B")),
        ('FONTSIZE', (0, 0), (-1, -1), 11),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('TOPPADDING', (0, 0), (-1, -1), 2)
    ]))

    elements.append(payment_summary_table)
    elements.append(Spacer(1, 0.15 * inch))

    # =====================================================
    # PAYMENT REFERENCE
    # =====================================================

    elements.append(Paragraph("<b>Payment Reference</b>", styles["Heading3"]))
    elements.append(Spacer(1, 0.2 * inch))

    reference_data = [
        ["Payment ID", payment_data.get("payment_id_gateway") or "N/A"],
        ["Order ID", payment_data.get("order_id") or "N/A"],
        ["Gateway", (payment_data.get("gateway") or "N/A").upper()],
        ["Payment Status", (payment_data.get("status") or "N/A").upper()],
    ]

    ref_table = Table(reference_data, colWidths=[150, 330])
    ref_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (0, -1), colors.HexColor("#f5f5f5")),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('TOPPADDING', (0, 0), (-1, -1), 2)
    ]))

    elements.append(ref_table)
    elements.append(Spacer(1, 0.6 * inch))

    # =====================================================
    # HOTEL POLICIES
    # =====================================================

    elements.append(Paragraph("<b>Hotel Policies</b>", styles["Heading2"]))
    elements.append(Spacer(1, 0.2 * inch))

    policy_style = ParagraphStyle(
        name="PolicyStyle",
        parent=styles["Normal"],
        fontSize=9,
        leading=14
    )

    policies = [
        "<b>Check-in & Check-out:</b> 24-hour check-in and check-out is available. Stay duration is calculated from the time of check-in.",
        "Early check-in or late check-out is subject to availability and additional charges.",

        "<b>Cancellation Policy:</b> Free cancellation up to 24 hours before check-in.",
        "Cancellations within 24 hours of check-in are non-refundable.",
        "Refunds (if applicable) will be processed within 5–7 working days.",

        "<b>Identification:</b> Valid government-issued photo ID is mandatory for all guests.",
        "Accepted IDs: Aadhaar Card, Passport, Driving License, Voter ID.",
        "Foreign guests must present Passport and valid Visa.",
        "Guests below 18 years are not allowed to check in alone.",

        "<b>Occupancy Rules:</b> Maximum occupancy per room must be strictly followed.",
        "Extra guests beyond room capacity will be charged.",
    ]

    for policy in policies:
        elements.append(Paragraph("• " + policy, policy_style))
        elements.append(Spacer(1, 0.12 * inch))

    elements.append(Spacer(1, 0.3 * inch))

    # =====================================================
    # FOOTER
    # =====================================================

    footer_style = ParagraphStyle(
        name="FooterStyle",
        parent=styles["Normal"],
        fontSize=9,
        textColor=colors.grey,
        alignment=1
    )

    elements.append(Paragraph(
        "Thank you for choosing Hotel Bhimas. We look forward to welcoming you!",
        footer_style
    ))

    elements.append(Spacer(1, 0.2 * inch))

    elements.append(Paragraph(
        "For support contact: +919347172758 | hotelbhimas@gmail.com",
        footer_style
    ))

    # Build PDF
    doc.build(elements)

    return file_path


# =====================================================
# GST TAX INVOICE (prompt 07 — folio billing)
# =====================================================

_ONES = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten",
         "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen", "Seventeen",
         "Eighteen", "Nineteen"]
_TENS = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]


def _two_digits(n):
    return _ONES[n] if n < 20 else (_TENS[n // 10] + (" " + _ONES[n % 10] if n % 10 else ""))


def _amount_in_words(amount):
    """Indian-system (lakh/crore) amount in words, e.g. 'Rupees One Lakh Twenty Three Thousand and Five Paise Only'."""
    rupees = int(amount)
    paise = int(round((amount - rupees) * 100))
    if rupees == 0:
        words = "Zero"
    else:
        parts = []
        crore, rest = divmod(rupees, 10000000)
        lakh, rest = divmod(rest, 100000)
        thousand, rest = divmod(rest, 1000)
        hundred, tens = divmod(rest, 100)
        if crore:
            parts.append(_two_digits(crore) + " Crore")
        if lakh:
            parts.append(_two_digits(lakh) + " Lakh")
        if thousand:
            parts.append(_two_digits(thousand) + " Thousand")
        if hundred:
            parts.append(_ONES[hundred] + " Hundred")
        if tens:
            parts.append(_two_digits(tens))
        words = " ".join(parts)
    out = f"Rupees {words}"
    if paise:
        out += f" and {_two_digits(paise)} Paise"
    return out + " Only"


def generate_folio_invoice_pdf(invoice_data):
    """
    Generate a GST tax invoice PDF for a folio (prompt 07).

    Args:
        invoice_data: dict from routers/folio.py _invoice_payload:
            invoice_no, invoice_date, folio_id, guest{name,phone,email},
            booking{booking_id,check_in,check_out,nights},
            lines[{description,qty,unit_price,gst_percent,amount}],
            discount_lines[], payment_lines[],
            gst_rows[{gst_percent,taxable,cgst,sgst,total}],
            charges_total, discount_total, taxable_total, cgst_total,
            sgst_total, grand_total, payments_total, balance_due
    Returns the generated file path (temp dir).
    """
    import tempfile
    file_path = os.path.join(tempfile.gettempdir(), f"invoice_folio_{invoice_data['folio_id']}.pdf")

    doc = SimpleDocTemplate(file_path, pagesize=A4,
                            rightMargin=25, leftMargin=25, topMargin=25, bottomMargin=25)
    styles = getSampleStyleSheet()
    styles["Normal"].fontSize = 9
    styles["Heading2"].fontSize = 11
    elements = []

    # Header (same brand block as the booking confirmation)
    logo_path = "assets/logo-gold.png"
    hotel_info = Paragraph(
        "<b>HOTEL BHIMAS</b><br/>"
        "Luxury & Comfort Stay<br/>"
        "42, G Car Street, Tirupati - 517501<br/>"
        "Landline: +91877-2225744<br/>"
        "Mobile: +919347172758<br/>"
        "GSTIN: 37AAACK9397F1Z3",
        styles["Normal"]
    )
    if os.path.exists(logo_path):
        logo = Image(logo_path, width=1.5 * inch, height=1 * inch)
        elements.append(Table([[logo, hotel_info]], colWidths=[120, 350]))
    else:
        elements.append(hotel_info)
    elements.append(Spacer(1, 0.3 * inch))

    title_style = ParagraphStyle(
        name="TaxInvoiceTitle", parent=styles["Title"],
        textColor=colors.HexColor("#B8860B"), fontSize=16, alignment=1, spaceAfter=16,
    )
    elements.append(Paragraph("TAX INVOICE", title_style))

    # Invoice / stay meta
    booking = invoice_data["booking"]
    guest = invoice_data["guest"]
    meta_data = [
        ["Invoice No", invoice_data["invoice_no"],
         "Invoice Date", invoice_data["invoice_date"].strftime("%d-%m-%Y")],
        ["Booking ID", str(booking["booking_id"]),
         "Nights", str(booking["nights"])],
        ["Guest", guest["name"], "Phone", guest["phone"]],
        ["Check-in", booking["check_in"].strftime("%d-%m-%Y") if booking["check_in"] else "N/A",
         "Check-out", booking["check_out"].strftime("%d-%m-%Y") if booking["check_out"] else "N/A"],
    ]
    meta_table = Table(meta_data, colWidths=[80, 190, 80, 130])
    meta_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (0, -1), colors.HexColor("#f5f5f5")),
        ('BACKGROUND', (2, 0), (2, -1), colors.HexColor("#f5f5f5")),
        ('FONTNAME', (1, 0), (1, 0), 'Helvetica-Bold'),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('TOPPADDING', (0, 0), (-1, -1), 2),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor("#e0e0e0")),
    ]))
    elements.append(meta_table)
    elements.append(Spacer(1, 0.3 * inch))

    # Line items (GST-inclusive amounts)
    elements.append(Paragraph("<b>Charges</b>", styles["Heading2"]))
    elements.append(Spacer(1, 0.12 * inch))
    items_data = [["Description", "Qty", "Rate", "GST %", "Amount"]]
    for line in invoice_data["lines"]:
        items_data.append([
            Paragraph(line["description"] or "", styles["Normal"]),
            f"{line['qty']:g}",
            f"Rs. {line['unit_price']:,.2f}",
            f"{line['gst_percent']:g}%" if line["gst_percent"] is not None else "-",
            f"Rs. {line['amount']:,.2f}",
        ])
    for line in invoice_data["discount_lines"]:
        items_data.append([
            Paragraph(line["description"] or "Discount", styles["Normal"]),
            "", "", "", f"- Rs. {abs(line['amount']):,.2f}",
        ])
    items_table = Table(items_data, colWidths=[220, 40, 80, 50, 90])
    items_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#B8860B")),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('ALIGN', (1, 0), (-1, -1), 'RIGHT'),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor("#f5f5f5")]),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
    ]))
    elements.append(items_table)
    elements.append(Spacer(1, 0.25 * inch))

    # GST summary per slab (CGST/SGST split)
    elements.append(Paragraph("<b>GST Summary</b>", styles["Heading2"]))
    elements.append(Spacer(1, 0.12 * inch))
    gst_data = [["GST %", "Taxable Value", "CGST", "SGST", "Total"]]
    for row in invoice_data["gst_rows"]:
        half = row["gst_percent"] / 2
        gst_data.append([
            f"{row['gst_percent']:g}%",
            f"Rs. {row['taxable']:,.2f}",
            f"Rs. {row['cgst']:,.2f} ({half:g}%)",
            f"Rs. {row['sgst']:,.2f} ({half:g}%)",
            f"Rs. {row['total']:,.2f}",
        ])
    gst_data.append([
        "Total",
        f"Rs. {invoice_data['taxable_total']:,.2f}",
        f"Rs. {invoice_data['cgst_total']:,.2f}",
        f"Rs. {invoice_data['sgst_total']:,.2f}",
        f"Rs. {invoice_data['charges_total']:,.2f}",
    ])
    gst_table = Table(gst_data, colWidths=[60, 110, 110, 110, 90])
    gst_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#B8860B")),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('ALIGN', (1, 0), (-1, -1), 'RIGHT'),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
    ]))
    elements.append(gst_table)
    elements.append(Spacer(1, 0.25 * inch))

    # Totals
    totals_data = [["Total Charges", f"Rs. {invoice_data['charges_total']:,.2f}"]]
    if invoice_data["discount_total"]:
        totals_data.append(["Discount", f"- Rs. {abs(invoice_data['discount_total']):,.2f}"])
    totals_data.append(["GRAND TOTAL", f"Rs. {invoice_data['grand_total']:,.2f}"])
    if invoice_data["payments_total"]:
        totals_data.append(["Advance / Payments", f"- Rs. {abs(invoice_data['payments_total']):,.2f}"])
    totals_data.append(["BALANCE DUE", f"Rs. {invoice_data['balance_due']:,.2f}"])
    totals_table = Table(totals_data, colWidths=[300, 150])
    totals_table.setStyle(TableStyle([
        ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
        ('FONTSIZE', (0, 0), (-1, -1), 11),
        ('LINEABOVE', (0, 2), (-1, 2), 1, colors.black),
        ('TEXTCOLOR', (0, -1), (-1, -1), colors.HexColor("#B8860B")),
        ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('TOPPADDING', (0, 0), (-1, -1), 2),
    ]))
    elements.append(totals_table)
    elements.append(Spacer(1, 0.12 * inch))

    words_style = ParagraphStyle(name="AmountWords", parent=styles["Normal"], fontSize=9)
    elements.append(Paragraph(
        f"<b>Amount in words:</b> {_amount_in_words(invoice_data['grand_total'])}", words_style))
    elements.append(Spacer(1, 0.2 * inch))

    # --- Payments & refunds, itemised (backlog v2 TBC-1) --------------------
    # These lines were always computed and then discarded — the invoice showed a
    # single "Advance / Payments" figure, so a refund silently shrank that number
    # with no visible credit line, reason or refund id.
    def _settlement_table(title, rows, note=None):
        if not rows:
            return
        elements.append(Paragraph(f"<b>{title}</b>", styles["Heading2"]))
        elements.append(Spacer(1, 0.1 * inch))
        data = [["Date", "Particulars", "Type", "Amount"]]
        for r in rows:
            posted = r.get("posted_at")
            data.append([
                posted.strftime("%d-%m-%Y") if hasattr(posted, "strftime") else "",
                Paragraph(r.get("description") or "", styles["Normal"]),
                "Refund" if r.get("kind") == "refund" else "Payment",
                f"Rs. {abs(r['amount']):,.2f}",
            ])
        table = Table(data, colWidths=[70, 240, 70, 100])
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#B8860B")),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('ALIGN', (3, 0), (3, -1), 'RIGHT'),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor("#f5f5f5")]),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ]))
        elements.append(table)
        if note:
            elements.append(Spacer(1, 0.08 * inch))
            elements.append(Paragraph(
                note, ParagraphStyle(name="SettleNote", parent=styles["Normal"],
                                     fontSize=8, textColor=colors.grey)))
        elements.append(Spacer(1, 0.2 * inch))

    _settlement_table("Payments & Refunds", invoice_data.get("payment_lines") or [])
    _settlement_table(
        "Refunds issued after this invoice",
        invoice_data.get("post_invoice_lines") or [],
        note="Settled after this tax invoice was raised. Shown for reconciliation only — "
             "the taxable value and GST above are unchanged.",
    )

    # --- GST e-invoice IRN + signed QR (prompt 18, drawn only when an e-invoice exists) ---
    einvoice = invoice_data.get("einvoice")
    if einvoice and einvoice.get("signed_qr"):
        try:
            from reportlab.graphics.barcode import qr
            from reportlab.graphics.shapes import Drawing
            widget = qr.QrCodeWidget(str(einvoice["signed_qr"]))
            bounds = widget.getBounds()
            size = 90
            d = Drawing(size, size, transform=[
                size / (bounds[2] - bounds[0]), 0, 0,
                size / (bounds[3] - bounds[1]), 0, 0])
            d.add(widget)
            irn_style = ParagraphStyle(name="Irn", parent=styles["Normal"], fontSize=8)
            irn_para = Paragraph(
                f"<b>e-Invoice</b><br/><b>IRN:</b> {einvoice.get('irn') or ''}<br/>"
                f"<b>Ack No:</b> {einvoice.get('ack_no') or ''} "
                f"<b>Date:</b> {einvoice.get('ack_date') or ''}"
                + ("<br/><i>(stub — GST_EINVOICE_* not configured)</i>"
                   if einvoice.get("status") == "stub" else ""),
                irn_style)
            qr_table = Table([[d, irn_para]], colWidths=[110, 340])
            qr_table.setStyle(TableStyle([
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                ('LEFTPADDING', (0, 0), (-1, -1), 0),
            ]))
            elements.append(qr_table)
        except Exception:
            import logging
            logging.getLogger(__name__).exception("failed to render e-invoice QR")
    elements.append(Spacer(1, 0.3 * inch))

    footer_style = ParagraphStyle(
        name="InvoiceFooter", parent=styles["Normal"],
        fontSize=9, textColor=colors.grey, alignment=1,
    )
    elements.append(Paragraph(
        "Thank you for staying with Hotel Bhimas. This is a computer-generated invoice.",
        footer_style))
    elements.append(Spacer(1, 0.15 * inch))
    elements.append(Paragraph(
        "For support contact: +919347172758 | hotelbhimas@gmail.com",
        footer_style))

    doc.build(elements)
    return file_path

def generate_registration_slip_pdf(slip_data):
    """
    Guest registration slip printed at check-in (prompt 06). The guest signs it.

    Args:
        slip_data: dict from routers/reception.py registration_slip:
            booking_id, guest_name, phone, email, id_type, id_number_masked,
            rooms [room_number...], check_in, check_out, checked_in_at,
            booking_source, grand_total, paid_total, balance
    Returns the generated file path (temp dir).
    """
    import tempfile
    file_path = os.path.join(tempfile.gettempdir(), f"registration_slip_{slip_data['booking_id']}.pdf")

    doc = SimpleDocTemplate(file_path, pagesize=A4,
                            rightMargin=25, leftMargin=25, topMargin=25, bottomMargin=25)
    styles = getSampleStyleSheet()
    styles["Normal"].fontSize = 9
    elements = []

    # Header (same brand block as the invoice)
    logo_path = "assets/logo-gold.png"
    hotel_info = Paragraph(
        "<b>HOTEL BHIMAS</b><br/>"
        "Luxury & Comfort Stay<br/>"
        "42, G Car Street, Tirupati - 517501<br/>"
        "Landline: +91877-2225744<br/>"
        "Mobile: +919347172758<br/>"
        "GSTIN: 37AAACK9397F1Z3",
        styles["Normal"]
    )
    if os.path.exists(logo_path):
        logo = Image(logo_path, width=1.5 * inch, height=1 * inch)
        elements.append(Table([[logo, hotel_info]], colWidths=[120, 350]))
    else:
        elements.append(hotel_info)
    elements.append(Spacer(1, 0.3 * inch))

    title_style = ParagraphStyle(
        name="SlipTitle", parent=styles["Title"],
        textColor=colors.HexColor("#B8860B"), fontSize=16, alignment=1, spaceAfter=16,
    )
    elements.append(Paragraph("GUEST REGISTRATION SLIP", title_style))

    def _d(value):
        return value.strftime("%d-%m-%Y") if value else "N/A"

    def _dt(value):
        return value.strftime("%d-%m-%Y %H:%M") if value else "N/A"

    id_label = (slip_data.get("id_type") or "").replace("_", " ").title() or "N/A"
    meta_data = [
        ["Booking ID", str(slip_data["booking_id"]),
         "Checked in", _dt(slip_data.get("checked_in_at"))],
        ["Guest", slip_data.get("guest_name") or "", "Phone", slip_data.get("phone") or ""],
        ["ID Type", id_label, "ID Number", slip_data.get("id_number_masked") or "N/A"],
        ["Room(s)", ", ".join(slip_data.get("rooms") or []) or "N/A",
         "Source", (slip_data.get("booking_source") or "").replace("_", " ").title()],
        ["Check-in", _d(slip_data.get("check_in")), "Check-out", _d(slip_data.get("check_out"))],
    ]
    meta_table = Table(meta_data, colWidths=[80, 190, 80, 130])
    meta_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (0, -1), colors.HexColor("#f5f5f5")),
        ('BACKGROUND', (2, 0), (2, -1), colors.HexColor("#f5f5f5")),
        ('FONTNAME', (1, 1), (1, 1), 'Helvetica-Bold'),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('TOPPADDING', (0, 0), (-1, -1), 2),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor("#e0e0e0")),
    ]))
    elements.append(meta_table)
    elements.append(Spacer(1, 0.25 * inch))

    # Occupant roster (FE-3): list every guest with masked ID when there is more than the lead.
    guests = slip_data.get("guests") or []
    if len(guests) > 1:
        elements.append(Paragraph("<b>Guests in the room</b>", styles["Normal"]))
        elements.append(Spacer(1, 0.06 * inch))
        g_rows = [["#", "Name", "ID Type", "ID Number", "ID on file"]]
        for i, g in enumerate(guests, start=1):
            g_rows.append([
                str(i),
                (g.get("name") or "") + ("  (primary)" if g.get("is_primary") else ""),
                (g.get("id_type") or "").replace("_", " ").title() or "—",
                g.get("id_number_masked") or "—",
                "Yes" if g.get("has_scan") else "—",
            ])
        g_table = Table(g_rows, colWidths=[24, 190, 90, 100, 60])
        g_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#f5f5f5")),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor("#e0e0e0")),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
            ('TOPPADDING', (0, 0), (-1, -1), 2),
        ]))
        elements.append(g_table)
        elements.append(Spacer(1, 0.25 * inch))

    totals_rows = [
        ["Stay total", f"Rs. {slip_data.get('grand_total', 0):,.2f}"],
        ["Paid", f"Rs. {slip_data.get('paid_total', 0):,.2f}"],
    ]
    if slip_data.get("balance") is not None:
        totals_rows.append(["Balance", f"Rs. {slip_data['balance']:,.2f}"])
    totals_table = Table(totals_rows, colWidths=[120, 150], hAlign='LEFT')
    totals_table.setStyle(TableStyle([
        ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('TEXTCOLOR', (0, -1), (-1, -1), colors.HexColor("#B8860B")),
        ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('TOPPADDING', (0, 0), (-1, -1), 2),
    ]))
    elements.append(totals_table)
    elements.append(Spacer(1, 0.35 * inch))

    terms_style = ParagraphStyle(name="SlipTerms", parent=styles["Normal"],
                                 fontSize=8, textColor=colors.grey)
    # Admin-editable rules/terms (FE-2). The caller passes the current text from
    # utils.settings.get_registration_rules(); fall back to a sensible default line.
    rules_text = (slip_data.get("registration_rules") or "").strip()
    if rules_text:
        rules_style = ParagraphStyle(name="SlipRules", parent=styles["Normal"],
                                     fontSize=7.5, textColor=colors.grey, leading=10)
        elements.append(Paragraph("<b>Terms of stay</b>", terms_style))
        for line in rules_text.splitlines():
            line = line.strip()
            if line:
                elements.append(Paragraph(line.replace("&", "&amp;"), rules_style))
        elements.append(Spacer(1, 0.12 * inch))
    elements.append(Paragraph(
        "I confirm the above details are correct and agree to the terms of stay above. "
        f"Checkout by the hotel's checkout time on {_d(slip_data.get('check_out'))}; "
        "key cards remain hotel property; charges signed to the room are payable at checkout.",
        terms_style))
    elements.append(Spacer(1, 0.6 * inch))

    sign_table = Table(
        [["", ""],
         ["Guest signature", "Front desk"]],
        colWidths=[220, 220],
    )
    sign_table.setStyle(TableStyle([
        ('LINEABOVE', (0, 1), (0, 1), 0.7, colors.black),
        ('LINEABOVE', (1, 1), (1, 1), 0.7, colors.black),
        ('TOPPADDING', (0, 0), (-1, 0), 24),
        ('FONTSIZE', (0, 1), (-1, 1), 9),
    ]))
    elements.append(sign_table)

    doc.build(elements)
    return file_path


def generate_payment_receipt_pdf(receipt_data):
    """
    Payment receipt / refund voucher printed at the desk (prompt 08).

    Args:
        receipt_data: dict from routers/payments.py payment_receipt_pdf:
            kind ("payment"|"refund"), payment_id, receipt_no, booking_id,
            guest_name, guest_phone, date, amount, method, reference, gateway,
            collected_by_name, and for refunds: refund_id, refund_amount,
            refund_reason, refund_mode, refund_reference
    Returns the generated file path (temp dir).
    """
    import tempfile
    is_refund = receipt_data.get("kind") == "refund"
    file_path = os.path.join(
        tempfile.gettempdir(),
        f"{'refund_voucher' if is_refund else 'payment_receipt'}_{receipt_data['payment_id']}.pdf")

    doc = SimpleDocTemplate(file_path, pagesize=A4,
                            rightMargin=25, leftMargin=25, topMargin=25, bottomMargin=25)
    styles = getSampleStyleSheet()
    styles["Normal"].fontSize = 9
    elements = []

    # Header (same brand block as the invoice / registration slip)
    logo_path = "assets/logo-gold.png"
    hotel_info = Paragraph(
        "<b>HOTEL BHIMAS</b><br/>"
        "Luxury & Comfort Stay<br/>"
        "42, G Car Street, Tirupati - 517501<br/>"
        "Landline: +91877-2225744<br/>"
        "Mobile: +919347172758<br/>"
        "GSTIN: 37AAACK9397F1Z3",
        styles["Normal"]
    )
    if os.path.exists(logo_path):
        logo = Image(logo_path, width=1.5 * inch, height=1 * inch)
        elements.append(Table([[logo, hotel_info]], colWidths=[120, 350]))
    else:
        elements.append(hotel_info)
    elements.append(Spacer(1, 0.3 * inch))

    title_style = ParagraphStyle(
        name="ReceiptTitle", parent=styles["Title"],
        textColor=colors.HexColor("#B8860B"), fontSize=16, alignment=1, spaceAfter=16,
    )
    elements.append(Paragraph("REFUND VOUCHER" if is_refund else "PAYMENT RECEIPT", title_style))

    def _dt(value):
        return value.strftime("%d-%m-%Y %H:%M") if value else "N/A"

    method_label = (receipt_data.get("method") or "").replace("_", " ").upper() or "N/A"
    meta_data = [
        ["Receipt No", receipt_data.get("receipt_no") or "N/A",
         "Date", _dt(receipt_data.get("date")) if not is_refund else _dt(datetime.now())],
        ["Booking ID", str(receipt_data.get("booking_id") or "N/A"),
         "Guest", receipt_data.get("guest_name") or "N/A"],
        ["Phone", receipt_data.get("guest_phone") or "N/A",
         "Collected by", receipt_data.get("collected_by_name") or "Front desk"],
    ]
    if is_refund:
        meta_data += [
            ["Original payment", f"#{receipt_data['payment_id']} — {method_label} "
                                 f"Rs. {receipt_data.get('amount', 0):,.2f}",
             "Paid on", _dt(receipt_data.get("date"))],
            ["Refund ID", receipt_data.get("refund_id") or "N/A",
             "Refund mode", (receipt_data.get("refund_mode") or receipt_data.get("gateway") or "").upper() or "N/A"],
            ["Refund ref", receipt_data.get("refund_reference") or "N/A",
             "Reason", receipt_data.get("refund_reason") or "N/A"],
        ]
    else:
        meta_data += [
            ["Mode", method_label, "Reference", receipt_data.get("reference") or "N/A"],
        ]
    meta_table = Table(meta_data, colWidths=[80, 190, 80, 130])
    meta_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (0, -1), colors.HexColor("#f5f5f5")),
        ('BACKGROUND', (2, 0), (2, -1), colors.HexColor("#f5f5f5")),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('TOPPADDING', (0, 0), (-1, -1), 2),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor("#e0e0e0")),
    ]))
    elements.append(meta_table)
    elements.append(Spacer(1, 0.3 * inch))

    # Big amount box + amount in words
    amount = (receipt_data.get("refund_amount") if is_refund else receipt_data.get("amount")) or 0
    amount_label = "AMOUNT REFUNDED" if is_refund else "AMOUNT RECEIVED"
    amount_table = Table([[amount_label, f"Rs. {amount:,.2f}"]], colWidths=[220, 220])
    amount_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor("#B8860B")),
        ('TEXTCOLOR', (0, 0), (-1, -1), colors.whitesmoke),
        ('FONTNAME', (0, 0), (-1, -1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 12),
        ('ALIGN', (1, 0), (1, 0), 'RIGHT'),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ('LEFTPADDING', (0, 0), (-1, -1), 10),
        ('RIGHTPADDING', (0, 0), (-1, -1), 10),
    ]))
    elements.append(amount_table)
    words_style = ParagraphStyle(name="ReceiptWords", parent=styles["Normal"],
                                 fontSize=9, textColor=colors.grey, spaceBefore=6)
    elements.append(Paragraph(f"Amount in words: {_amount_in_words(amount)}", words_style))
    elements.append(Spacer(1, 0.15 * inch))

    note_style = ParagraphStyle(name="ReceiptNote", parent=styles["Normal"],
                                fontSize=8, textColor=colors.grey)
    if is_refund:
        elements.append(Paragraph(
            "Refund issued against the original payment above. "
            "This voucher is the guest's acknowledgement of receiving the refund.", note_style))
    else:
        elements.append(Paragraph(
            "This receipt records money received against the booking above. "
            "It is not a tax invoice — the GST tax invoice is issued with the folio.", note_style))
    elements.append(Spacer(1, 0.6 * inch))

    sign_labels = (["Paid out by", "Received by guest"] if is_refund
                   else ["Received by", "Guest"])
    sign_table = Table([["", ""], sign_labels], colWidths=[220, 220])
    sign_table.setStyle(TableStyle([
        ('LINEABOVE', (0, 1), (0, 1), 0.7, colors.black),
        ('LINEABOVE', (1, 1), (1, 1), 0.7, colors.black),
        ('TOPPADDING', (0, 0), (-1, 0), 24),
        ('FONTSIZE', (0, 1), (-1, 1), 9),
    ]))
    elements.append(sign_table)

    footer_style = ParagraphStyle(name="ReceiptFooter", parent=styles["Normal"],
                                  fontSize=8, textColor=colors.grey, alignment=1, spaceBefore=20)
    elements.append(Paragraph(
        "For support contact: +919347172758 | hotelbhimas@gmail.com", footer_style))

    doc.build(elements)
    return file_path


def generate_shift_report_pdf(shift_data):
    """
    End-of-shift cash reconciliation report printed at the desk (prompt 12).

    Args:
        shift_data: dict from routers/cash_shift.py _report_data:
            id, station_id, period, status, staff_name, closed_by_name, opened_at, closed_at,
            opening_balance, collections_cash, expenses_total, payouts_total, expected_cash,
            counted_cash, variance, close_note, denominations ({"500": 3, ...} or None),
            expenses ([{category, description, amount, created_at}])
    Returns the generated file path (temp dir).
    """
    import tempfile
    file_path = os.path.join(tempfile.gettempdir(), f"shift_report_{shift_data['id']}.pdf")

    doc = SimpleDocTemplate(file_path, pagesize=A4,
                            rightMargin=25, leftMargin=25, topMargin=25, bottomMargin=25)
    styles = getSampleStyleSheet()
    styles["Normal"].fontSize = 9
    styles["Heading2"].fontSize = 11
    elements = []

    # Header (same brand block as the invoice / receipt).
    logo_path = "assets/logo-gold.png"
    hotel_info = Paragraph(
        "<b>HOTEL BHIMAS</b><br/>"
        "Luxury & Comfort Stay<br/>"
        "42, G Car Street, Tirupati - 517501<br/>"
        "GSTIN: 37AAACK9397F1Z3",
        styles["Normal"]
    )
    if os.path.exists(logo_path):
        logo = Image(logo_path, width=1.5 * inch, height=1 * inch)
        elements.append(Table([[logo, hotel_info]], colWidths=[120, 350]))
    else:
        elements.append(hotel_info)
    elements.append(Spacer(1, 0.3 * inch))

    title_style = ParagraphStyle(
        name="ShiftTitle", parent=styles["Title"],
        textColor=colors.HexColor("#B8860B"), fontSize=16, alignment=1, spaceAfter=16,
    )
    elements.append(Paragraph("CASH SHIFT REPORT", title_style))

    def _dt(value):
        if not value:
            return "N/A"
        if isinstance(value, str):
            try:
                value = datetime.fromisoformat(value)
            except ValueError:
                return value
        return value.strftime("%d-%m-%Y %H:%M")

    def _rs(v):
        return f"Rs. {float(v or 0):,.2f}"

    period_label = (shift_data.get("period") or "shift").capitalize()
    meta_data = [
        ["Shift No", str(shift_data.get("id") or "N/A"), "Cycle", period_label],
        ["Station", shift_data.get("station_id") or "N/A",
         "Status", (shift_data.get("status") or "").upper()],
        ["Opened by", shift_data.get("staff_name") or "N/A", "Opened at", _dt(shift_data.get("opened_at"))],
        ["Closed by", shift_data.get("closed_by_name") or "N/A", "Closed at", _dt(shift_data.get("closed_at"))],
    ]
    meta_table = Table(meta_data, colWidths=[80, 190, 80, 130])
    meta_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (0, -1), colors.HexColor("#f5f5f5")),
        ('BACKGROUND', (2, 0), (2, -1), colors.HexColor("#f5f5f5")),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('TOPPADDING', (0, 0), (-1, -1), 2),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor("#e0e0e0")),
    ]))
    elements.append(meta_table)
    elements.append(Spacer(1, 0.25 * inch))

    # Reconciliation summary
    elements.append(Paragraph("<b>Reconciliation</b>", styles["Heading2"]))
    recon = [
        ["Opening float", _rs(shift_data.get("opening_balance"))],
        ["+ Cash collections", _rs(shift_data.get("collections_cash"))],
        ["- Expenses", _rs(shift_data.get("expenses_total"))],
        ["- Cash payouts / refunds", _rs(shift_data.get("payouts_total"))],
        ["= Expected in drawer", _rs(shift_data.get("expected_cash"))],
        ["Counted", _rs(shift_data.get("counted_cash"))],
    ]
    recon_table = Table(recon, colWidths=[300, 140])
    recon_table.setStyle(TableStyle([
        ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
        ('LINEABOVE', (0, 4), (-1, 4), 0.7, colors.black),
        ('FONTNAME', (0, 4), (-1, 4), 'Helvetica-Bold'),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
    ]))
    elements.append(recon_table)
    elements.append(Spacer(1, 0.1 * inch))

    variance = float(shift_data.get("variance") or 0)
    var_color = colors.HexColor("#1a7f37") if abs(variance) < 0.005 else colors.HexColor("#b42318")
    var_label = "BALANCED" if abs(variance) < 0.005 else ("SHORT" if variance < 0 else "OVER")
    var_table = Table([[f"VARIANCE ({var_label})", _rs(variance)]], colWidths=[220, 220])
    var_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), var_color),
        ('TEXTCOLOR', (0, 0), (-1, -1), colors.whitesmoke),
        ('FONTNAME', (0, 0), (-1, -1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 12),
        ('ALIGN', (1, 0), (1, 0), 'RIGHT'),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ('LEFTPADDING', (0, 0), (-1, -1), 10),
        ('RIGHTPADDING', (0, 0), (-1, -1), 10),
    ]))
    elements.append(var_table)
    elements.append(Spacer(1, 0.25 * inch))

    # Expenses breakdown
    expenses = shift_data.get("expenses") or []
    elements.append(Paragraph("<b>Expenses</b>", styles["Heading2"]))
    if expenses:
        rows = [["#", "Category", "Description", "Amount"]]
        for i, e in enumerate(expenses, 1):
            rows.append([str(i), (e.get("category") or "").capitalize(),
                         Paragraph(e.get("description") or "", styles["Normal"]),
                         _rs(e.get("amount"))])
        rows.append(["", "", "Total", _rs(shift_data.get("expenses_total"))])
        exp_table = Table(rows, colWidths=[25, 90, 245, 80])
        exp_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#f0f0f0")),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
            ('ALIGN', (3, 0), (3, -1), 'RIGHT'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor("#e0e0e0")),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
            ('TOPPADDING', (0, 0), (-1, -1), 2),
        ]))
        elements.append(exp_table)
    else:
        elements.append(Paragraph("No expenses logged this shift.", styles["Normal"]))
    elements.append(Spacer(1, 0.2 * inch))

    # Denomination count (if entered)
    denoms = shift_data.get("denominations")
    if denoms:
        elements.append(Paragraph("<b>Cash counted (denominations)</b>", styles["Heading2"]))
        drows = [["Denomination", "Count", "Value"]]
        try:
            items = sorted(denoms.items(), key=lambda kv: float(kv[0]), reverse=True)
        except (ValueError, AttributeError):
            items = list(denoms.items())
        for face, count in items:
            try:
                value = float(face) * float(count)
            except (ValueError, TypeError):
                value = 0
            drows.append([f"Rs. {face}", str(count), _rs(value)])
        den_table = Table(drows, colWidths=[160, 120, 160])
        den_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#f0f0f0")),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('ALIGN', (1, 0), (-1, -1), 'RIGHT'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor("#e0e0e0")),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
            ('TOPPADDING', (0, 0), (-1, -1), 2),
        ]))
        elements.append(den_table)
        elements.append(Spacer(1, 0.2 * inch))

    if shift_data.get("close_note"):
        note_style = ParagraphStyle(name="ShiftNote", parent=styles["Normal"],
                                    fontSize=9, textColor=colors.grey)
        elements.append(Paragraph(f"<b>Note:</b> {shift_data['close_note']}", note_style))
        elements.append(Spacer(1, 0.3 * inch))
    else:
        elements.append(Spacer(1, 0.4 * inch))

    sign_table = Table([["", ""], ["Counted by", "Verified by (manager)"]], colWidths=[220, 220])
    sign_table.setStyle(TableStyle([
        ('LINEABOVE', (0, 1), (0, 1), 0.7, colors.black),
        ('LINEABOVE', (1, 1), (1, 1), 0.7, colors.black),
        ('TOPPADDING', (0, 0), (-1, 0), 24),
        ('FONTSIZE', (0, 1), (-1, 1), 9),
    ]))
    elements.append(sign_table)

    footer_style = ParagraphStyle(name="ShiftFooter", parent=styles["Normal"],
                                  fontSize=8, textColor=colors.grey, alignment=1, spaceBefore=20)
    elements.append(Paragraph(
        "Cash shift reconciliation — retained for internal audit.", footer_style))

    doc.build(elements)
    return file_path


def generate_company_invoice_pdf(payload):
    """Consolidated corporate GST tax invoice (prompt 18, slice 7).

    One invoice covering every stay transferred to a company in the period, instead of handing
    the employer a stack of per-stay bills. Same brand block + GST-split table as
    generate_folio_invoice_pdf; the line table is per STAY rather than per charge.

    `payload` comes from routers/companies._cinvoice_payload.
    Returns the generated file path (temp dir).
    """
    import re
    import tempfile
    safe = re.sub(r"[^A-Za-z0-9]+", "_", payload.get("invoice_no") or "company_invoice").strip("_")
    file_path = os.path.join(tempfile.gettempdir(), f"{safe.lower()}.pdf")

    doc = SimpleDocTemplate(file_path, pagesize=A4,
                            rightMargin=24, leftMargin=24, topMargin=24, bottomMargin=24)
    styles = getSampleStyleSheet()
    styles["Normal"].fontSize = 9
    elements = []
    gold = colors.HexColor("#B8860B")

    # --- brand header (same block as the folio invoice / report) ---
    logo_path = "assets/logo-gold.png"
    hotel_info = Paragraph(
        "<b>HOTEL BHIMAS</b><br/>"
        "Luxury &amp; Comfort Stay<br/>"
        "42, G Car Street, Tirupati - 517501<br/>"
        "Ph: +91-877-2225744 &nbsp;|&nbsp; hotelbhimas@gmail.com<br/>"
        "GSTIN: 37AAACK9397F1Z3",
        styles["Normal"]
    )
    if os.path.exists(logo_path):
        logo = Image(logo_path, width=1.5 * inch, height=1 * inch)
        elements.append(Table([[logo, hotel_info]], colWidths=[120, 350]))
    else:
        elements.append(hotel_info)
    elements.append(Spacer(1, 0.2 * inch))

    title_style = ParagraphStyle(name="CInvTitle", parent=styles["Title"],
                                 textColor=gold, fontSize=15, alignment=1, spaceAfter=4)
    elements.append(Paragraph("TAX INVOICE — CORPORATE", title_style))
    if (payload.get("status") or "") == "cancelled":
        cancel_style = ParagraphStyle(name="CInvCancelled", parent=styles["Normal"],
                                      fontSize=11, alignment=1,
                                      textColor=colors.HexColor("#B00020"), spaceAfter=6)
        elements.append(Paragraph("<b>** CANCELLED **</b>", cancel_style))
    elements.append(Spacer(1, 0.08 * inch))

    # --- bill-to + invoice meta, side by side ---
    company = payload.get("company") or {}
    bill_to_lines = [f"<b>{company.get('name') or '—'}</b>"]
    for key in ("address", "city"):
        if company.get(key):
            bill_to_lines.append(str(company[key]))
    if company.get("state"):
        state = str(company["state"])
        if company.get("state_code"):
            state += f" ({company['state_code']})"
        bill_to_lines.append(state)
    if company.get("gstin"):
        bill_to_lines.append(f"GSTIN: {company['gstin']}")
    if company.get("contact_person"):
        bill_to_lines.append(f"Attn: {company['contact_person']}")
    if company.get("phone"):
        bill_to_lines.append(str(company["phone"]))

    bill_to = Paragraph("<b>Bill to</b><br/>" + "<br/>".join(bill_to_lines), styles["Normal"])
    meta = Paragraph(
        f"<b>Invoice No:</b> {payload.get('invoice_no') or ''}<br/>"
        f"<b>Invoice Date:</b> {payload.get('invoice_date') or ''}<br/>"
        f"<b>Period:</b> {payload.get('period') or ''}<br/>"
        f"<b>Stays:</b> {len(payload.get('stays') or [])}",
        styles["Normal"])
    meta_table = Table([[bill_to, meta]], colWidths=[280, 265])
    meta_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor("#e0e0e0")),
        ('INNERGRID', (0, 0), (-1, -1), 0.5, colors.HexColor("#e0e0e0")),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
    ]))
    elements.append(meta_table)
    elements.append(Spacer(1, 0.18 * inch))

    # --- per-stay lines ---
    stay_rows = [[Paragraph(f"<b>{h}</b>", styles["Normal"])
                  for h in ["#", "Guest", "Check-in", "Check-out", "Stay invoice", "Amount (Rs.)"]]]
    for i, s in enumerate(payload.get("stays") or [], start=1):
        stay_rows.append([
            Paragraph(str(i), styles["Normal"]),
            Paragraph(str(s.get("guest_name") or "—"), styles["Normal"]),
            Paragraph(str(s.get("check_in") or ""), styles["Normal"]),
            Paragraph(str(s.get("check_out") or ""), styles["Normal"]),
            Paragraph(str(s.get("invoice_no") or ""), styles["Normal"]),
            Paragraph(f"{float(s.get('amount') or 0):,.2f}", styles["Normal"]),
        ])
    stay_table = Table(stay_rows, colWidths=[24, 150, 74, 74, 118, 105], repeatRows=1)
    stay_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), gold),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor("#f5f5f5")]),
        ('GRID', (0, 0), (-1, -1), 0.4, colors.HexColor("#e0e0e0")),
        ('ALIGN', (5, 1), (5, -1), 'RIGHT'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))
    elements.append(stay_table)
    elements.append(Spacer(1, 0.18 * inch))

    # --- GST split (identical math to the guest invoice: CGST = SGST = slab/2) ---
    gst_rows = payload.get("gst_rows") or []
    if gst_rows:
        gst_table_rows = [[Paragraph(f"<b>{h}</b>", styles["Normal"])
                           for h in ["GST %", "Taxable (Rs.)", "CGST (Rs.)", "SGST (Rs.)", "Total (Rs.)"]]]
        for g in gst_rows:
            gst_table_rows.append([
                Paragraph(f"{float(g.get('gst_percent') or 0):g}%", styles["Normal"]),
                Paragraph(f"{float(g.get('taxable') or 0):,.2f}", styles["Normal"]),
                Paragraph(f"{float(g.get('cgst') or 0):,.2f}", styles["Normal"]),
                Paragraph(f"{float(g.get('sgst') or 0):,.2f}", styles["Normal"]),
                Paragraph(f"{float(g.get('total') or 0):,.2f}", styles["Normal"]),
            ])
        gst_table = Table(gst_table_rows, colWidths=[70, 120, 110, 110, 135], repeatRows=1)
        gst_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#f0e6cc")),
            ('GRID', (0, 0), (-1, -1), 0.4, colors.HexColor("#e0e0e0")),
            ('ALIGN', (1, 1), (-1, -1), 'RIGHT'),
            ('TOPPADDING', (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ]))
        elements.append(gst_table)
        elements.append(Spacer(1, 0.15 * inch))

    # --- totals ---
    grand = float(payload.get("grand_total") or 0)
    totals_rows = [
        ["Taxable value", f"{float(payload.get('taxable_total') or 0):,.2f}"],
        ["CGST", f"{float(payload.get('cgst_total') or 0):,.2f}"],
        ["SGST", f"{float(payload.get('sgst_total') or 0):,.2f}"],
        ["Grand total", f"{grand:,.2f}"],
        ["Received", f"{float(payload.get('paid_total') or 0):,.2f}"],
        ["Balance due", f"{float(payload.get('balance_due') or 0):,.2f}"],
    ]
    totals_table = Table([[Paragraph(f"<b>{a}</b>" if a in ("Grand total", "Balance due") else a,
                                     styles["Normal"]),
                           Paragraph(f"<b>{b}</b>" if a in ("Grand total", "Balance due") else b,
                                     styles["Normal"])]
                          for a, b in totals_rows],
                         colWidths=[150, 120], hAlign='RIGHT')
    totals_table.setStyle(TableStyle([
        ('GRID', (0, 0), (-1, -1), 0.4, colors.HexColor("#e0e0e0")),
        ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
        ('BACKGROUND', (0, 3), (-1, 3), colors.HexColor("#f0e6cc")),
        ('BACKGROUND', (0, 5), (-1, 5), colors.HexColor("#f0e6cc")),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))
    elements.append(totals_table)
    elements.append(Spacer(1, 0.12 * inch))

    words_style = ParagraphStyle(name="CInvWords", parent=styles["Normal"], fontSize=9)
    elements.append(Paragraph(f"<b>Amount in words:</b> {_amount_in_words(grand)}", words_style))

    if payload.get("notes"):
        elements.append(Spacer(1, 0.08 * inch))
        elements.append(Paragraph(f"<b>Notes:</b> {payload['notes']}", styles["Normal"]))

    footer_style = ParagraphStyle(name="CInvFooter", parent=styles["Normal"],
                                  fontSize=8, textColor=colors.grey, alignment=1, spaceBefore=20)
    elements.append(Paragraph(
        "This is a computer-generated tax invoice. Subject to Tirupati jurisdiction.<br/>"
        "Hotel Bhimas — GSTIN 37AAACK9397F1Z3", footer_style))

    doc.build(elements)
    return file_path


def generate_report_pdf(title, columns, rows, totals_row=None, meta=None):
    """Generic branded tabular report (prompt 16 Reports & Dashboard).

    Args:
        title:      report title (e.g. "Daily Sales Report").
        columns:    list of column header strings.
        rows:       list of row lists (values; None -> blank).
        totals_row: optional final bold row (e.g. ["TOTAL", 123.0, ...]).
        meta:       optional dict of label->value shown under the title (e.g. {"Period": "...",
                    "Generated": "..."}).
    Returns the generated file path (temp dir). Reuses the same brand header block as the
    invoice / shift report.
    """
    import re
    import tempfile
    safe = re.sub(r"[^A-Za-z0-9]+", "_", (title or "report")).strip("_").lower() or "report"
    file_path = os.path.join(tempfile.gettempdir(), f"report_{safe}.pdf")

    doc = SimpleDocTemplate(file_path, pagesize=A4,
                            rightMargin=22, leftMargin=22, topMargin=22, bottomMargin=22)
    styles = getSampleStyleSheet()
    styles["Normal"].fontSize = 8
    elements = []

    # Header (same brand block as the invoice / shift report).
    logo_path = "assets/logo-gold.png"
    hotel_info = Paragraph(
        "<b>HOTEL BHIMAS</b><br/>"
        "Luxury &amp; Comfort Stay<br/>"
        "42, G Car Street, Tirupati - 517501<br/>"
        "GSTIN: 37AAACK9397F1Z3",
        styles["Normal"]
    )
    if os.path.exists(logo_path):
        logo = Image(logo_path, width=1.5 * inch, height=1 * inch)
        elements.append(Table([[logo, hotel_info]], colWidths=[120, 350]))
    else:
        elements.append(hotel_info)
    elements.append(Spacer(1, 0.25 * inch))

    title_style = ParagraphStyle(
        name="ReportTitle", parent=styles["Title"],
        textColor=colors.HexColor("#B8860B"), fontSize=15, alignment=1, spaceAfter=6,
    )
    elements.append(Paragraph(title or "Report", title_style))

    if meta:
        meta_style = ParagraphStyle(name="ReportMeta", parent=styles["Normal"],
                                    fontSize=8, textColor=colors.grey, alignment=1, spaceAfter=10)
        elements.append(Paragraph(
            " &nbsp;|&nbsp; ".join(f"{k}: {v}" for k, v in meta.items()), meta_style))
    elements.append(Spacer(1, 0.08 * inch))

    def _cell(v):
        if v is None:
            return ""
        if isinstance(v, float):
            return f"{v:,.2f}"
        return str(v)

    header = [str(c) for c in columns]
    table_rows = [[Paragraph(f"<b>{h}</b>", styles["Normal"]) for h in header]]
    for r in rows:
        table_rows.append([Paragraph(_cell(v), styles["Normal"]) for v in r])
    has_totals = totals_row is not None
    if has_totals:
        table_rows.append([Paragraph(f"<b>{_cell(v)}</b>", styles["Normal"]) for v in totals_row])

    table = Table(table_rows, repeatRows=1)
    style = [
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#B8860B")),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ROWBACKGROUNDS', (0, 1), (-1, -2 if has_totals else -1),
         [colors.white, colors.HexColor("#f5f5f5")]),
        ('GRID', (0, 0), (-1, -1), 0.4, colors.HexColor("#e0e0e0")),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ('LEFTPADDING', (0, 0), (-1, -1), 4),
        ('RIGHTPADDING', (0, 0), (-1, -1), 4),
    ]
    if has_totals:
        style.append(('BACKGROUND', (0, -1), (-1, -1), colors.HexColor("#f0e6cc")))
        style.append(('LINEABOVE', (0, -1), (-1, -1), 0.7, colors.HexColor("#B8860B")))
    table.setStyle(TableStyle(style))
    elements.append(table)

    if not rows:
        elements.append(Spacer(1, 0.2 * inch))
        empty_style = ParagraphStyle(name="ReportEmpty", parent=styles["Normal"],
                                     fontSize=9, textColor=colors.grey, alignment=1)
        elements.append(Paragraph("No data for the selected period.", empty_style))

    footer_style = ParagraphStyle(name="ReportFooter", parent=styles["Normal"],
                                  fontSize=8, textColor=colors.grey, alignment=1, spaceBefore=18)
    elements.append(Paragraph("Hotel Bhimas — internal management report.", footer_style))

    doc.build(elements)
    return file_path
