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

def generate_booking_pdf(booking, payment):
    os.makedirs("booking_data", exist_ok=True)
    file_path = f"booking_data/booking_{booking.booking_id}.pdf"

    doc = SimpleDocTemplate(
        file_path,
        pagesize=A4,
        rightMargin=25,
        leftMargin=25,
        topMargin=25,
        bottomMargin=25
    )
    room_details = room_extra_details.get(
        booking.room_type.room_type_id,
        {}
    )

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

    booking_data = [
        ["Booking ID", booking.booking_id],
        ["Booking Date", booking.created_at.strftime("%d-%m-%Y %H:%M")],
        ["Room Type", booking.room_type.name],
        ["Check-in", booking.check_in.strftime("%d-%m-%Y")],
        ["Check-out", booking.check_out.strftime("%d-%m-%Y")],
        ["Status", booking.status.upper()],
    ]

    booking_table = Table(booking_data, colWidths=[150, 330])
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

    guest_data = [
        ["Name", booking.guest.name],
        ["Phone", booking.guest.phone],
        ["Email", booking.guest.email or "N/A"],
    ]

    guest_table = Table(guest_data, colWidths=[150, 330])
    guest_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (0, -1), colors.HexColor("#f5f5f5")),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('TOPPADDING', (0, 0), (-1, -1), 2)
    ]))

    elements.append(guest_table)
    elements.append(Spacer(1, 0.15 * inch))

    # =====================================================
    # ROOM DETAILS
    # =====================================================

    elements.append(Paragraph("<b>Room Details</b>", styles["Heading2"]))
    elements.append(Spacer(1, 0.2 * inch))

    features_text = ", ".join(room_details.get("features", []))

    room_data = [
        ["Room Size", room_details.get("size", "N/A")],
        ["Capacity", room_details.get("capacity", "N/A")],
        ["Description", room_details.get("desc", "N/A")],
        ["Features", features_text or "N/A"],
    ]

    room_table = Table(room_data, colWidths=[150, 330])
    room_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (0, -1), colors.HexColor("#f5f5f5")),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('TOPPADDING', (0, 0), (-1, -1), 2)
    ]))

    elements.append(room_table)
    elements.append(Spacer(1, 0.4 * inch))

    # =====================================================
    # PAYMENT SUMMARY (Invoice Style)
    # =====================================================

    elements.append(Paragraph("<b>Payment Summary</b>", styles["Heading2"]))
    elements.append(Spacer(1, 0.3 * inch))

    nights = (booking.check_out - booking.check_in).days
    nights = nights if nights > 0 else 1

    base_amount = float(booking.base_amount)
    price_per_night = base_amount / nights

    gst_amount = float(booking.gst_amount or 0)
    convenience_fee = float(booking.convenience_fee or 0)
    convenience_gst = float(booking.convenience_gst or 0)
    grand_total = float(booking.grand_total)
    total_convnience_fee = convenience_fee + convenience_gst
    payment_data = [
        [f"{nights} Night(s) × Rs. {price_per_night:.2f}", f"Rs. {base_amount:.2f}"],
        ["GST", f"Rs. {gst_amount:.2f}"],
        ["Convenience Fee", f"Rs. {total_convnience_fee:.2f}"],
        ["GRAND TOTAL", f"Rs. {grand_total:.2f}"],
    ]

    payment_table = Table(payment_data, colWidths=[300, 150])
    payment_table.setStyle(TableStyle([
        ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
        ('LINEABOVE', (0, -1), (-1, -1), 1, colors.black),
        ('TEXTCOLOR', (0, -1), (-1, -1), colors.HexColor("#B8860B")),
        ('FONTSIZE', (0, 0), (-1, -1), 11),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('TOPPADDING', (0, 0), (-1, -1), 2)
    ]))

    elements.append(payment_table)
    elements.append(Spacer(1, 0.15 * inch))

    # =====================================================
    # PAYMENT REFERENCE
    # =====================================================

    elements.append(Paragraph("<b>Payment Reference</b>", styles["Heading3"]))
    elements.append(Spacer(1, 0.2 * inch))

    reference_data = [
        ["Payment ID", payment.payment_id_gateway or "N/A"],
        ["Order ID", payment.order_id or "N/A"],
        ["Gateway", payment.gateway.upper()],
        ["Payment Status", payment.status.upper()],
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