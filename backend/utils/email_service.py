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
    nights = (booking_data['check_out'] - booking_data['check_in']).days

    # ✅ FIX: Richer TextPart that mirrors HTML (reduces spam score)
    text_body = f"""Hotel Bhimas - Booking Confirmation

Dear {booking_data['guest_name']},

Your booking has been successfully confirmed. Please find the details below.

Booking ID   : {booking_data['booking_id']}
Check-in     : {check_in_str}
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