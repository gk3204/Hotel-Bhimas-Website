from mailjet_rest import Client
import os
import base64
import logging

logger = logging.getLogger(__name__)

MAILJET_API_KEY = os.getenv("MAILJET_API_KEY")
MAILJET_API_SECRET = os.getenv("MAILJET_API_SECRET")
MAIL_FROM = os.getenv("MAIL_FROM", "bookings.hotelbhimas@gmail.com")

mailjet = Client(auth=(MAILJET_API_KEY, MAILJET_API_SECRET), version='v3.1')

ENQUIRY_MAILJET_API_KEY = os.getenv("ENQUIRY_MAILJET_API_KEY")
ENQUIRY_MAILJET_API_SECRET = os.getenv("ENQUIRY_MAILJET_API_SECRET")
ENQUIRY_MAIL_FROM = os.getenv("ENQUIRY_MAIL_FROM", "hotelbhimas.enquiry@gmail.com")

enquiry_mailjet = Client(auth=(ENQUIRY_MAILJET_API_KEY, ENQUIRY_MAILJET_API_SECRET), version='v3.1')


def send_booking_email(booking_data, pdf_path):
    """Send booking confirmation email via Mailjet"""

    if not MAILJET_API_KEY or not MAILJET_API_SECRET:
        logger.error("❌ Mailjet credentials missing")
        raise Exception("Mailjet not configured")

    # ✅ FIX: Send two SEPARATE messages instead of one with 2 recipients
    # This avoids the "bulk mail" signal
    guest_recipients = [{"Email": booking_data['guest_email'], "Name": booking_data['guest_name']}]
    hotel_recipients = [{"Email": "hotelbhimas@gmail.com", "Name": "Hotel Bhimas"}]

    pdf_attachments = []
    if pdf_path and os.path.exists(pdf_path) and os.path.getsize(pdf_path) > 0:
        try:
            with open(pdf_path, "rb") as f:
                pdf_attachments.append({
                    "ContentType": "application/pdf",
                    "Filename": f"booking_{booking_data['booking_id']}.pdf",
                    "Base64Content": base64.b64encode(f.read()).decode()
                })
        except Exception as e:
            logger.warning(f"⚠️ PDF attach failed: {str(e)}")

    # ✅ FIX: Use a stable, direct Cloudinary URL (fetch format, no version timestamp)
    # Go to your Cloudinary console and get the direct /image/upload/ URL without the version
    logo_url = os.getenv(
        "LOGO_URL",
        "https://hotelbhimas.in/logo-final.png"
    )

    check_in_str = booking_data['check_in'].strftime("%d %B %Y")
    check_out_str = booking_data['check_out'].strftime("%d %B %Y")
    arrival_time = booking_data.get('check_in_time')
    arrival_str = arrival_time[:5] if arrival_time else None  # "HH:MM:SS" -> "HH:MM"
    nights = (booking_data['check_out'] - booking_data['check_in']).days

    # ✅ FIX: Richer TextPart that mirrors HTML (reduces spam score)
    text_body = f"""Hotel Bhimas - Booking Confirmation

Dear {booking_data['guest_name']},

Your booking has been successfully confirmed. Please find the details below.

Booking ID   : {booking_data['booking_id']}
Check-in     : {check_in_str}{f" (Arrival ~{arrival_str})" if arrival_str else ""}
Check-out    : {check_out_str}
Nights       : {nights}
Total Paid   : Rs. {booking_data['grand_total']}

A PDF copy of your booking confirmation is attached to this email.

We look forward to welcoming you at Hotel Bhimas!

---
Hotel Bhimas
42, G Car Street, Tirupati - 517501
Landline: +91-877-2225744
Mobile: +91-9347172758
Email: hotelbhimas@gmail.com
"""

    email_body = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Booking Confirmation - Hotel Bhimas</title>
</head>
<body style="margin:0; padding:0; background-color:#f4f4f4; font-family: Arial, sans-serif;">

<table width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:#f4f4f4; padding:20px 0;">
  <tr>
    <td align="center">

      <table width="600" cellpadding="0" cellspacing="0" border="0"
             style="max-width:600px; width:100%; background:#ffffff; border-radius:8px; overflow:hidden; border:1px solid #e0e0e0;">

        <!-- HEADER -->
        <tr>
          <td align="center" style="background-color:#000000; padding:30px 20px;">

            <!-- ✅ FIX: explicit width/height, alt text, border=0, display:block -->
            <img src="{logo_url}"
              alt="Hotel Bhimas"
              width="150"
              style="display:block; margin:0 auto; border:0; outline:none; text-decoration:none;">
            <h1 style="color:#D4AF37; font-family:Arial,sans-serif; font-size:26px;
                        letter-spacing:3px; margin:0 0 6px 0; padding:0;">
              HOTEL BHIMAS
            </h1>
            <p style="color:#ffffff; font-size:12px; margin:0; letter-spacing:2px;">
              LUXURY &amp; COMFORT
            </p>
          </td>
        </tr>

        <!-- BODY -->
        <tr>
          <td style="padding:30px 30px 20px 30px;">

            <h2 style="color:#222222; font-size:20px; margin:0 0 16px 0;">
              Booking Confirmed
            </h2>

            <p style="color:#444444; font-size:15px; line-height:1.6; margin:0 0 20px 0;">
              Dear <strong>{booking_data['guest_name']}</strong>,<br><br>
              Thank you for choosing Hotel Bhimas. Your booking has been successfully confirmed.
              Please find your booking details below.
            </p>

            <!-- BOOKING DETAILS TABLE -->
            <table width="100%" cellpadding="10" cellspacing="0" border="0"
                   style="background:#f9f9f9; border-radius:6px; border:1px solid #e8e8e8;">
              <tr style="border-bottom:1px solid #e8e8e8;">
                <td style="color:#888888; font-size:13px; width:40%;">Booking ID</td>
                <td style="color:#222222; font-size:14px; font-weight:bold;">{booking_data['booking_id']}</td>
              </tr>
              <tr style="border-bottom:1px solid #e8e8e8; background:#ffffff;">
                <td style="color:#888888; font-size:13px;">Check-in</td>
                <td style="color:#222222; font-size:14px;">{check_in_str}{f" &nbsp;(Arrival ~{arrival_str})" if arrival_str else ""}</td>
              </tr>
              <tr style="border-bottom:1px solid #e8e8e8;">
                <td style="color:#888888; font-size:13px;">Check-out</td>
                <td style="color:#222222; font-size:14px;">{check_out_str}</td>
              </tr>
              <tr style="border-bottom:1px solid #e8e8e8; background:#ffffff;">
                <td style="color:#888888; font-size:13px;">Duration</td>
                <td style="color:#222222; font-size:14px;">{nights} Night{'s' if nights != 1 else ''}</td>
              </tr>
              <tr>
                <td style="color:#888888; font-size:13px;">Amount Paid</td>
                <td style="color:#D4AF37; font-size:16px; font-weight:bold;">Rs. {booking_data['grand_total']}</td>
              </tr>
            </table>

            <p style="color:#444444; font-size:14px; line-height:1.6; margin:24px 0 0 0;">
              Your booking confirmation is attached as a PDF. If you have any questions,
              please contact us at <a href="mailto:hotelbhimas@gmail.com"
              style="color:#D4AF37;">hotelbhimas@gmail.com</a> or call
              <a href="tel:+919347172758" style="color:#D4AF37;">+91-9347172758</a>.
            </p>

            <p style="color:#444444; font-size:14px; margin:16px 0 0 0;">
              We look forward to welcoming you!
            </p>

          </td>
        </tr>

        <!-- FOOTER -->
        <tr>
          <td style="background:#111111; padding:20px; text-align:center;">
            <p style="color:#aaaaaa; font-size:12px; margin:0 0 6px 0; line-height:1.8;">
              Hotel Bhimas &nbsp;|&nbsp; 42, G Car Street, Tirupati - 517501<br>
              Landline: +91-877-2225744 &nbsp;|&nbsp; Mobile: +91-9347172758<br>
              <a href="mailto:hotelbhimas@gmail.com"
                 style="color:#D4AF37; text-decoration:none;">hotelbhimas@gmail.com</a>
            </p>
            <p style="color:#666666; font-size:11px; margin:10px 0 0 0;">
              This is an automated booking confirmation. Please do not reply to this email.
            </p>
          </td>
        </tr>

      </table>

    </td>
  </tr>
</table>

</body>
</html>"""

    try:
        # ✅ FIX: Send as TWO separate messages in one API call
        data = {
            "Messages": [
                {
                    "From": {"Email": MAIL_FROM, "Name": "Hotel Bhimas"},
                    "To": guest_recipients,
                    # ✅ FIX: Subject without special currency symbols
                    "Subject": "Booking Confirmation - Hotel Bhimas",
                    "TextPart": text_body,
                    "HTMLPart": email_body,
                    "Attachments": pdf_attachments,
                    "ReplyTo": {"Email": "hotelbhimas@gmail.com", "Name": "Hotel Bhimas"},
                    "Headers": {
                        # ✅ FIX: Proper headers to improve deliverability
                        "X-Entity-Ref-ID": str(booking_data['booking_id']),
                    },
                    "TrackOpen": 1,
                    "TrackClick": 1
                },
                {
                    "From": {"Email": MAIL_FROM, "Name": "Hotel Bhimas Bookings"},
                    "To": hotel_recipients,
                    "Subject": f"[NEW BOOKING] {booking_data['guest_name']} - ID: {booking_data['booking_id']}",
                    "TextPart": text_body,
                    "HTMLPart": email_body,
                    "Attachments": pdf_attachments,
                    "ReplyTo": {"Email": booking_data['guest_email'], "Name": booking_data['guest_name']},
                }
            ]
        }

        result = mailjet.send.create(data=data)

        if result.status_code == 200:
            logger.info(f"✅ Emails sent for booking {booking_data['booking_id']}")
        else:
            logger.error(f"❌ Mailjet failed: {result.status_code} — {result.json()}")
            raise Exception("Email sending failed")

    except Exception as e:
        logger.error(f"❌ Email error: {str(e)}")
        raise


def send_enquiry_email(name: str, email: str, phone: str, message: str):
    """Send contact enquiry email via separate Mailjet account"""

    if not ENQUIRY_MAILJET_API_KEY or not ENQUIRY_MAILJET_API_SECRET:
        logger.error("❌ Enquiry Mailjet credentials missing")
        raise Exception("Enquiry Mailjet not configured")

    try:
        data = {
            "Messages": [
                {
                    "From": {"Email": ENQUIRY_MAIL_FROM, "Name": "Hotel Bhimas Website"},
                    "To": [{"Email": "hotelbhimas@gmail.com", "Name": "Hotel Bhimas"}],
                    "ReplyTo": {"Email": email, "Name": name},
                    "Subject": f"Website Enquiry from {name}",
                    "TextPart": f"Name: {name}\nEmail: {email}\nPhone: {phone}\n\nMessage:\n{message}",
                    "HTMLPart": f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"></head>
<body style="font-family:Arial,sans-serif; background:#f4f4f4; padding:20px; margin:0;">
  <table width="600" cellpadding="0" cellspacing="0" border="0"
         style="max-width:600px; margin:auto; background:white; border-radius:8px;
                padding:25px; border:1px solid #ddd;">
    <tr>
      <td>
        <h2 style="color:#333; border-bottom:2px solid #D4AF37; padding-bottom:10px; margin-top:0;">
          New Website Enquiry
        </h2>

        <table width="100%" cellpadding="8" cellspacing="0" border="0"
               style="background:#f9f9f9; border-radius:6px; margin-bottom:20px;">
          <tr>
            <td style="color:#888; font-size:13px; width:30%;">Name</td>
            <td style="color:#222; font-size:14px;"><strong>{name}</strong></td>
          </tr>
          <tr style="background:#fff;">
            <td style="color:#888; font-size:13px;">Email</td>
            <td style="color:#222; font-size:14px;">
              <a href="mailto:{email}" style="color:#D4AF37;">{email}</a>
            </td>
          </tr>
          <tr>
            <td style="color:#888; font-size:13px;">Phone</td>
            <td style="color:#222; font-size:14px;">
              <a href="tel:{phone}" style="color:#D4AF37;">{phone}</a>
            </td>
          </tr>
        </table>

        <h3 style="color:#D4AF37; margin-bottom:8px;">Message</h3>
        <p style="line-height:1.7; color:#444; white-space:pre-wrap; margin:0;">{message}</p>

        <hr style="margin:24px 0; border:none; border-top:1px solid #eee;">
        <p style="font-size:12px; color:#999; margin:0;">
          Hotel Bhimas &mdash; 42, G Car Street, Tirupati - 517501
        </p>
      </td>
    </tr>
  </table>
</body>
</html>"""
                }
            ]
        }

        result = enquiry_mailjet.send.create(data=data)

        if result.status_code == 200:
            logger.info(f"✅ Enquiry email sent from {email}")
        else:
            logger.error(f"❌ Enquiry Mailjet failed: {result.status_code} — {result.json()}")
            raise Exception("Enquiry email sending failed")

    except Exception as e:
        logger.error(f"❌ Enquiry email error: {str(e)}")
        raise


def send_admin_cancellation_email(guest_email: str, guest_name: str, booking_ref: str, 
                                   check_in, check_out, refund_amount: float, 
                                   refund_id: str, reason: str, admin_notes: str = None):
    """Send admin cancellation & refund notification email via Mailjet"""

    if not MAILJET_API_KEY or not MAILJET_API_SECRET:
        logger.error("❌ Mailjet credentials missing for admin cancellation email")
        return False

    try:
        check_in_str = check_in.strftime("%d %B %Y") if hasattr(check_in, 'strftime') else str(check_in)
        check_out_str = check_out.strftime("%d %B %Y") if hasattr(check_out, 'strftime') else str(check_out)
        nights = (check_out - check_in).days

        logo_url = os.getenv(
            "LOGO_URL",
            "https://hotelbhimas.in/logo-final.png"
        )

        text_body = f"""Hotel Bhimas - Booking Cancellation & Refund

Dear {guest_name},

Your booking has been cancelled by Hotel Bhimas management. Please find the refund details below.

Booking ID       : {booking_ref}
Check-in         : {check_in_str}
Check-out        : {check_out_str}
Nights           : {nights}
Cancellation Reason: {reason}
{f'Admin Notes      : {admin_notes}' if admin_notes else ''}

Refund Details:
Refund Amount    : Rs. {refund_amount}
Refund ID        : {refund_id}
Timeline         : The refund will be processed within 5-7 working days to your original payment method.

If you have any questions regarding this cancellation or refund, please contact us at:
Email: hotelbhimas@gmail.com
Phone: +919347172758

We appreciate your understanding and hope to serve you in the future.

---
Hotel Bhimas
42, G Car Street, Tirupati - 517501
"""

        email_body = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Booking Cancellation & Refund - Hotel Bhimas</title>
</head>
<body style="margin:0; padding:0; background-color:#f4f4f4; font-family: Arial, sans-serif;">

<table width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:#f4f4f4; padding:20px 0;">
  <tr>
    <td align="center">

      <table width="600" cellpadding="0" cellspacing="0" border="0"
             style="max-width:600px; width:100%; background:#ffffff; border-radius:8px; overflow:hidden; border:1px solid #e0e0e0;">

        <!-- HEADER -->
        <tr>
          <td align="center" style="background-color:#000000; padding:30px 20px;">
            <img src="{logo_url}"
              alt="Hotel Bhimas"
              width="150"
              style="display:block; margin:0 auto; border:0; outline:none; text-decoration:none;">
            <h1 style="color:#D4AF37; font-family:Arial,sans-serif; font-size:26px;
                        letter-spacing:3px; margin:0 0 6px 0; padding:0;">
              HOTEL BHIMAS
            </h1>
            <p style="color:#ffffff; font-size:12px; margin:0; letter-spacing:2px;">
              LUXURY &amp; COMFORT
            </p>
          </td>
        </tr>

        <!-- BODY -->
        <tr>
          <td style="padding:30px 30px 20px 30px;">

            <h2 style="color:#c71c1c; font-size:20px; margin:0 0 16px 0;">
              Booking Cancellation & Refund
            </h2>

            <p style="color:#444444; font-size:15px; line-height:1.6; margin:0 0 20px 0;">
              Dear <strong>{guest_name}</strong>,<br><br>
              We regret to inform you that your booking has been cancelled by Hotel Bhimas management.
              Your refund is being processed as per the details below.
            </p>

            <!-- BOOKING DETAILS TABLE -->
            <table width="100%" cellpadding="10" cellspacing="0" border="0"
                   style="background:#f9f9f9; border-radius:6px; border:1px solid #e8e8e8; margin-bottom:20px;">
              <tr style="border-bottom:1px solid #e8e8e8;">
                <td style="color:#888888; font-size:13px; width:40%;">Booking ID</td>
                <td style="color:#222222; font-size:14px; font-weight:bold;">{booking_ref}</td>
              </tr>
              <tr style="border-bottom:1px solid #e8e8e8; background:#ffffff;">
                <td style="color:#888888; font-size:13px;">Check-in</td>
                <td style="color:#222222; font-size:14px;">{check_in_str}</td>
              </tr>
              <tr style="border-bottom:1px solid #e8e8e8;">
                <td style="color:#888888; font-size:13px;">Check-out</td>
                <td style="color:#222222; font-size:14px;">{check_out_str}</td>
              </tr>
              <tr style="border-bottom:1px solid #e8e8e8; background:#ffffff;">
                <td style="color:#888888; font-size:13px;">Duration</td>
                <td style="color:#222222; font-size:14px;">{nights} Night{'s' if nights != 1 else ''}</td>
              </tr>
              <tr>
                <td style="color:#888888; font-size:13px;">Cancellation Reason</td>
                <td style="color:#c71c1c; font-size:14px; font-weight:bold;">{reason}</td>
              </tr>
            </table>

            <!-- REFUND SECTION -->
            <div style="background:#ecf5ff; border-left:4px solid #D4AF37; padding:16px; border-radius:4px; margin-bottom:20px;">
              <h3 style="color:#D4AF37; margin:0 0 12px 0; font-size:16px;">Refund Details</h3>
              <table width="100%" cellpadding="0" cellspacing="0" border="0">
                <tr>
                  <td style="color:#444444; font-size:14px; padding:6px 0;">
                    <strong>Refund Amount:</strong>
                  </td>
                  <td style="color:#D4AF37; font-size:16px; font-weight:bold; text-align:right;">
                    Rs. {refund_amount}
                  </td>
                </tr>
                <tr>
                  <td style="color:#444444; font-size:14px; padding:6px 0;">
                    <strong>Refund ID:</strong>
                  </td>
                  <td style="color:#222222; font-size:14px; text-align:right; font-family:'Courier New', monospace;">
                    {refund_id}
                  </td>
                </tr>
                <tr>
                  <td style="color:#444444; font-size:14px; padding:6px 0;">
                    <strong>Timeline:</strong>
                  </td>
                  <td style="color:#222222; font-size:14px; text-align:right;">
                    5-7 working days
                  </td>
                </tr>
              </table>
              <p style="color:#666666; font-size:13px; margin:12px 0 0 0; line-height:1.5;">
                The refund will be credited to your original payment method. Please allow 5-7 working days for the amount to reflect in your account.
              </p>
            </div>

            {f'<p style="color:#666666; font-size:13px; margin:0 0 16px 0;"><strong>Admin Notes:</strong> {admin_notes}</p>' if admin_notes else ''}

            <p style="color:#444444; font-size:14px; line-height:1.6; margin:16px 0 0 0;">
              If you have any questions about this cancellation or refund, please feel free to contact us at
              <a href="mailto:hotelbhimas@gmail.com" style="color:#D4AF37;">hotelbhimas@gmail.com</a> or call
              <a href="tel:+919347172758" style="color:#D4AF37;">+91-9347172758</a>.
            </p>

            <p style="color:#444444; font-size:14px; margin:16px 0 0 0;">
              We appreciate your understanding and look forward to serving you in the future.
            </p>

          </td>
        </tr>

        <!-- FOOTER -->
        <tr>
          <td style="background:#111111; padding:20px; text-align:center;">
            <p style="color:#aaaaaa; font-size:12px; margin:0 0 6px 0; line-height:1.8;">
              Hotel Bhimas &nbsp;|&nbsp; 42, G Car Street, Tirupati - 517501<br>
              Landline: +91-877-2225744 &nbsp;|&nbsp; Mobile: +91-9347172758<br>
              <a href="mailto:hotelbhimas@gmail.com"
                 style="color:#D4AF37; text-decoration:none;">hotelbhimas@gmail.com</a>
            </p>
            <p style="color:#666666; font-size:11px; margin:10px 0 0 0;">
              This is an automated cancellation notification. Please do not reply to this email.
            </p>
          </td>
        </tr>

      </table>

    </td>
  </tr>
</table>

</body>
</html>"""

        data = {
            "Messages": [
                {
                    "From": {"Email": MAIL_FROM, "Name": "Hotel Bhimas"},
                    "To": [{"Email": guest_email, "Name": guest_name}],
                    "Subject": f"Booking Cancellation & Refund - Booking ID: {booking_ref}",
                    "TextPart": text_body,
                    "HTMLPart": email_body,
                    "ReplyTo": {"Email": "hotelbhimas@gmail.com", "Name": "Hotel Bhimas"},
                    "Headers": {
                        "X-Entity-Ref-ID": str(booking_ref),
                    },
                    "TrackOpen": 1,
                    "TrackClick": 1
                },
                {
                    "From": {"Email": MAIL_FROM, "Name": "Hotel Bhimas Bookings"},
                    "To": [{"Email": "hotelbhimas@gmail.com", "Name": "Hotel Bhimas Admin"}],
                    "Subject": f"[ADMIN CANCELLATION] Booking {booking_ref} - Refund ID: {refund_id}",
                    "TextPart": text_body,
                    "HTMLPart": email_body,
                    "ReplyTo": {"Email": guest_email, "Name": guest_name},
                }
            ]
        }

        result = mailjet.send.create(data=data)

        if result.status_code == 200:
            logger.info(f"✅ Admin cancellation email sent for booking {booking_ref} (Refund ID: {refund_id})")
            return True
        else:
            logger.error(f"❌ Mailjet failed: {result.status_code} — {result.json()}")
            return False

    except Exception as e:
        logger.error(f"❌ Admin cancellation email error: {str(e)}")
        return False

def send_invoice_email(invoice_data, pdf_path):
    """Send a folio GST tax invoice to the guest (+ hotel copy) via Mailjet (prompt 07).

    invoice_data comes from routers/folio.py _invoice_payload:
    invoice_no, invoice_date, guest{name,phone,email}, booking{...}, grand_total, balance_due.
    """
    if not MAILJET_API_KEY or not MAILJET_API_SECRET:
        logger.error("❌ Mailjet credentials missing")
        raise Exception("Mailjet not configured")

    guest = invoice_data["guest"]
    booking = invoice_data["booking"]
    invoice_no = invoice_data["invoice_no"]

    guest_recipients = [{"Email": guest["email"], "Name": guest["name"]}]
    hotel_recipients = [{"Email": "hotelbhimas@gmail.com", "Name": "Hotel Bhimas"}]

    pdf_attachments = []
    if pdf_path and os.path.exists(pdf_path) and os.path.getsize(pdf_path) > 0:
        try:
            with open(pdf_path, "rb") as f:
                pdf_attachments.append({
                    "ContentType": "application/pdf",
                    "Filename": invoice_no.replace("/", "-") + ".pdf",
                    "Base64Content": base64.b64encode(f.read()).decode()
                })
        except Exception as e:
            logger.warning(f"⚠️ Invoice PDF attach failed: {str(e)}")

    check_in_str = booking["check_in"].strftime("%d %B %Y") if booking["check_in"] else "N/A"
    check_out_str = booking["check_out"].strftime("%d %B %Y") if booking["check_out"] else "N/A"
    invoice_date_str = invoice_data["invoice_date"].strftime("%d %B %Y")

    text_body = f"""Hotel Bhimas - Tax Invoice

Dear {guest['name']},

Thank you for staying with us. Please find your GST tax invoice attached.

Invoice No   : {invoice_no}
Invoice Date : {invoice_date_str}
Booking ID   : {booking['booking_id']}
Check-in     : {check_in_str}
Check-out    : {check_out_str}
Grand Total  : Rs. {invoice_data['grand_total']:,.2f}
Balance Due  : Rs. {invoice_data['balance_due']:,.2f}

A PDF copy of the invoice is attached to this email.

---
Hotel Bhimas
42, G Car Street, Tirupati - 517501
Landline: +91-877-2225744
Mobile: +91-9347172758
Email: hotelbhimas@gmail.com
GSTIN: 37AAACK9397F1Z3
"""

    email_body = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Tax Invoice - Hotel Bhimas</title>
</head>
<body style="margin:0; padding:0; background-color:#f4f4f4; font-family: Arial, sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:#f4f4f4; padding:20px 0;">
  <tr><td align="center">
    <table width="600" cellpadding="0" cellspacing="0" border="0"
           style="max-width:600px; width:100%; background:#ffffff; border-radius:8px; overflow:hidden; border:1px solid #e0e0e0;">
      <tr>
        <td align="center" style="background-color:#000000; padding:30px 20px;">
          <h1 style="color:#D4AF37; font-size:26px; letter-spacing:3px; margin:0 0 6px 0;">HOTEL BHIMAS</h1>
          <p style="color:#ffffff; font-size:12px; margin:0; letter-spacing:2px;">LUXURY &amp; COMFORT</p>
        </td>
      </tr>
      <tr>
        <td style="padding:30px 30px 20px 30px;">
          <h2 style="color:#222222; font-size:20px; margin:0 0 16px 0;">Tax Invoice</h2>
          <p style="color:#444444; font-size:15px; line-height:1.6; margin:0 0 20px 0;">
            Dear <strong>{guest['name']}</strong>,<br><br>
            Thank you for staying with Hotel Bhimas. Your GST tax invoice is attached as a PDF.
          </p>
          <table width="100%" cellpadding="10" cellspacing="0" border="0"
                 style="background:#f9f9f9; border-radius:6px; border:1px solid #e8e8e8;">
            <tr style="border-bottom:1px solid #e8e8e8;">
              <td style="color:#888888; font-size:13px; width:40%;">Invoice No</td>
              <td style="color:#222222; font-size:14px; font-weight:bold;">{invoice_no}</td>
            </tr>
            <tr style="border-bottom:1px solid #e8e8e8; background:#ffffff;">
              <td style="color:#888888; font-size:13px;">Invoice Date</td>
              <td style="color:#222222; font-size:14px;">{invoice_date_str}</td>
            </tr>
            <tr style="border-bottom:1px solid #e8e8e8;">
              <td style="color:#888888; font-size:13px;">Stay</td>
              <td style="color:#222222; font-size:14px;">{check_in_str} to {check_out_str}</td>
            </tr>
            <tr style="border-bottom:1px solid #e8e8e8; background:#ffffff;">
              <td style="color:#888888; font-size:13px;">Grand Total</td>
              <td style="color:#222222; font-size:14px; font-weight:bold;">Rs. {invoice_data['grand_total']:,.2f}</td>
            </tr>
            <tr>
              <td style="color:#888888; font-size:13px;">Balance Due</td>
              <td style="color:#222222; font-size:14px; font-weight:bold;">Rs. {invoice_data['balance_due']:,.2f}</td>
            </tr>
          </table>
          <p style="color:#888888; font-size:12px; line-height:1.6; margin:20px 0 0 0;">
            Hotel Bhimas · 42, G Car Street, Tirupati - 517501 · GSTIN: 37AAACK9397F1Z3<br>
            +91-877-2225744 · +91-9347172758 · hotelbhimas@gmail.com
          </p>
        </td>
      </tr>
    </table>
  </td></tr>
</table>
</body>
</html>"""

    try:
        data = {
            "Messages": [
                {
                    "From": {"Email": MAIL_FROM, "Name": "Hotel Bhimas"},
                    "To": guest_recipients,
                    "Subject": f"Tax Invoice {invoice_no} - Hotel Bhimas",
                    "TextPart": text_body,
                    "HTMLPart": email_body,
                    "Attachments": pdf_attachments,
                    "ReplyTo": {"Email": "hotelbhimas@gmail.com", "Name": "Hotel Bhimas"},
                    "Headers": {"X-Entity-Ref-ID": invoice_no},
                },
                {
                    "From": {"Email": MAIL_FROM, "Name": "Hotel Bhimas Bookings"},
                    "To": hotel_recipients,
                    "Subject": f"[INVOICE SENT] {invoice_no} - {guest['name']} (Booking {booking['booking_id']})",
                    "TextPart": text_body,
                    "HTMLPart": email_body,
                    "Attachments": pdf_attachments,
                }
            ]
        }

        result = mailjet.send.create(data=data)

        if result.status_code == 200:
            logger.info(f"✅ Invoice email sent: {invoice_no}")
        else:
            logger.error(f"❌ Mailjet failed: {result.status_code} — {result.json()}")
            raise Exception("Invoice email sending failed")

    except Exception as e:
        logger.error(f"❌ Invoice email error: {str(e)}")
        raise


def send_company_invoice_email(payload, pdf_path):
    """Send a consolidated corporate GST invoice to the company's billing contact (prompt 18,
    slice 7). Mirrors send_invoice_email's Mailjet payload (guest copy -> company copy, plus the
    hotel copy) and raises on failure so the caller can surface a 502."""
    if not MAILJET_API_KEY or not MAILJET_API_SECRET:
        logger.error("❌ Mailjet credentials missing")
        raise Exception("Mailjet not configured")

    company = payload["company"]
    invoice_no = payload["invoice_no"]

    pdf_attachments = []
    if pdf_path and os.path.exists(pdf_path) and os.path.getsize(pdf_path) > 0:
        try:
            with open(pdf_path, "rb") as f:
                pdf_attachments.append({
                    "ContentType": "application/pdf",
                    "Filename": invoice_no.replace("/", "-") + ".pdf",
                    "Base64Content": base64.b64encode(f.read()).decode()
                })
        except Exception as e:
            logger.warning(f"⚠️ Company invoice PDF attach failed: {str(e)}")

    contact = company.get("contact_person") or company["name"]
    text_body = f"""Hotel Bhimas - Tax Invoice (Corporate)

Dear {contact},

Please find attached the consolidated GST tax invoice for your organisation's stays with us.

Invoice No   : {invoice_no}
Invoice Date : {payload['invoice_date']}
Period       : {payload['period']}
Stays        : {len(payload['stays'])}
Taxable      : Rs. {payload['taxable_total']:,.2f}
CGST         : Rs. {payload['cgst_total']:,.2f}
SGST         : Rs. {payload['sgst_total']:,.2f}
Grand Total  : Rs. {payload['grand_total']:,.2f}
Balance Due  : Rs. {payload['balance_due']:,.2f}

A PDF copy of the invoice is attached to this email.

---
Hotel Bhimas
42, G Car Street, Tirupati - 517501
Landline: +91-877-2225744
Email: hotelbhimas@gmail.com
GSTIN: 37AAACK9397F1Z3
"""

    rows_html = "".join(
        f"<tr><td style='padding:6px 8px;border-bottom:1px solid #eee'>{s['guest_name']}</td>"
        f"<td style='padding:6px 8px;border-bottom:1px solid #eee'>{s['check_in']} – {s['check_out']}</td>"
        f"<td style='padding:6px 8px;border-bottom:1px solid #eee'>{s['invoice_no']}</td>"
        f"<td style='padding:6px 8px;border-bottom:1px solid #eee;text-align:right'>"
        f"Rs. {s['amount']:,.2f}</td></tr>"
        for s in payload["stays"]
    )
    html_body = f"""
    <div style="font-family:Arial,sans-serif;max-width:640px;margin:auto;color:#1f2937">
      <h2 style="color:#B8860B">Tax Invoice {invoice_no}</h2>
      <p>Dear {contact},</p>
      <p>Please find attached the consolidated GST tax invoice for
         <b>{company['name']}</b> covering <b>{payload['period']}</b>.</p>
      <table style="width:100%;border-collapse:collapse;font-size:14px;margin:18px 0">
        <thead><tr style="background:#f4f4f4">
          <th style="padding:8px;text-align:left">Guest</th>
          <th style="padding:8px;text-align:left">Stay</th>
          <th style="padding:8px;text-align:left">Invoice</th>
          <th style="padding:8px;text-align:right">Amount</th>
        </tr></thead>
        <tbody>{rows_html}</tbody>
      </table>
      <p style="font-size:15px"><b>Grand total: Rs. {payload['grand_total']:,.2f}</b><br>
         Balance due: Rs. {payload['balance_due']:,.2f}</p>
      <p>— Hotel Bhimas</p>
    </div>
    """

    recipients = [{"Email": company["email"], "Name": company["name"]}]
    hotel_recipients = [{"Email": "hotelbhimas@gmail.com", "Name": "Hotel Bhimas"}]

    try:
        data = {
            "Messages": [
                {
                    "From": {"Email": MAIL_FROM, "Name": "Hotel Bhimas"},
                    "To": recipients,
                    "Subject": f"Tax Invoice {invoice_no} - Hotel Bhimas",
                    "TextPart": text_body,
                    "HTMLPart": html_body,
                    "Attachments": pdf_attachments,
                    "ReplyTo": {"Email": "hotelbhimas@gmail.com", "Name": "Hotel Bhimas"},
                    "Headers": {"X-Entity-Ref-ID": invoice_no},
                },
                {
                    "From": {"Email": MAIL_FROM, "Name": "Hotel Bhimas Accounts"},
                    "To": hotel_recipients,
                    "Subject": f"[CORPORATE INVOICE SENT] {invoice_no} - {company['name']}",
                    "TextPart": text_body,
                    "HTMLPart": html_body,
                    "Attachments": pdf_attachments,
                },
            ]
        }
        result = mailjet.send.create(data=data)
        if result.status_code == 200:
            logger.info(f"✅ Company invoice email sent: {invoice_no}")
        else:
            logger.error(f"❌ Mailjet failed: {result.status_code} — {result.json()}")
            raise Exception("Company invoice email sending failed")
    except Exception as e:
        logger.error(f"❌ Company invoice email error: {str(e)}")
        raise


def send_prearrival_link_email(guest_email, guest_name, link, hotel_name="Hotel Bhimas"):
    """Send the pre-arrival digital-registration link to a guest (prompt 14).
    Mirrors the Mailjet payload style of the other senders. Raises on failure so the
    caller can surface a 502 (like send_invoice_email)."""
    if not MAILJET_API_KEY or not MAILJET_API_SECRET:
        logger.error("❌ Mailjet credentials missing")
        raise Exception("Mailjet not configured")

    text_body = (
        f"Dear {guest_name or 'Guest'},\n\n"
        f"Welcome to {hotel_name}! To make your check-in quick and contactless, please "
        f"complete your pre-arrival registration using the secure link below. It only takes "
        f"a minute — fill in your details and upload a photo of your ID.\n\n"
        f"{link}\n\n"
        f"On arrival our front desk will simply verify your details. We look forward to hosting you.\n\n"
        f"— {hotel_name}"
    )
    html_body = f"""
    <div style="font-family:Arial,sans-serif;max-width:560px;margin:auto;color:#1f2937">
      <h2 style="color:#B8860B">Pre-arrival registration</h2>
      <p>Dear {guest_name or 'Guest'},</p>
      <p>Welcome to <b>{hotel_name}</b>! To make your check-in quick and contactless, please
      complete your pre-arrival registration. It only takes a minute — fill in your details
      and upload a photo of your ID.</p>
      <p style="text-align:center;margin:28px 0">
        <a href="{link}" style="background:#D4AF37;color:#1f2937;font-weight:bold;
        padding:12px 28px;border-radius:8px;text-decoration:none">Complete registration</a>
      </p>
      <p style="font-size:12px;color:#6b7280">If the button doesn't work, copy this link:<br>{link}</p>
      <p>On arrival our front desk will simply verify your details. We look forward to hosting you.</p>
      <p>— {hotel_name}</p>
    </div>
    """

    data = {
        'Messages': [
            {
                "From": {"Email": MAIL_FROM, "Name": hotel_name},
                "To": [{"Email": guest_email, "Name": guest_name or "Guest"}],
                "Subject": f"Complete your pre-arrival registration — {hotel_name}",
                "TextPart": text_body,
                "HTMLPart": html_body,
                "ReplyTo": {"Email": "hotelbhimas@gmail.com", "Name": hotel_name},
            }
        ]
    }
    try:
        result = mailjet.send.create(data=data)
        if result.status_code == 200:
            logger.info(f"✅ Pre-arrival link email sent to {guest_email}")
        else:
            logger.error(f"❌ Mailjet failed: {result.status_code} — {result.json()}")
            raise Exception("Pre-arrival email sending failed")
    except Exception as e:
        logger.error(f"❌ Pre-arrival email error: {str(e)}")
        raise
