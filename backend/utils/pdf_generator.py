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

    booking_table_data = [
        ["Booking ID", str(booking_data['booking_id'])],
        ["Room Type(s)", room_types_str],
        ["Check-in", booking_data['check_in'].strftime("%d-%m-%Y")],
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
    convenience_fee = booking_data['convenience_fee']
    convenience_gst = booking_data['convenience_gst']
    grand_total = booking_data['grand_total']
    total_convnience_fee = convenience_fee + convenience_gst
    
    payment_summary_data = [
        [f"{nights} Night(s)", f"Rs. {base_amount:,.2f}"],
        ["GST", f"Rs. {gst_amount:,.2f}"],
        ["Convenience Fee", f"Rs. {total_convnience_fee:,.2f}"],
        ["GRAND TOTAL", f"Rs. {grand_total:,.2f}"],
    ]
    
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