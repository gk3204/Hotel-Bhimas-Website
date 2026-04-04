from mailjet_rest import Client
import os
import base64
import logging

logger = logging.getLogger(__name__)

# ✅ Booking Email Configuration
MAILJET_API_KEY = os.getenv("MAILJET_API_KEY")
MAILJET_API_SECRET = os.getenv("MAILJET_API_SECRET")
MAIL_FROM = os.getenv("MAIL_FROM", "bookings.hotelbhimas@gmail.com")

mailjet = Client(auth=(MAILJET_API_KEY, MAILJET_API_SECRET), version='v3.1')

# ✅ Enquiry Email Configuration (Separate Account)
ENQUIRY_MAILJET_API_KEY = os.getenv("ENQUIRY_MAILJET_API_KEY")
ENQUIRY_MAILJET_API_SECRET = os.getenv("ENQUIRY_MAILJET_API_SECRET")
ENQUIRY_MAIL_FROM = os.getenv("ENQUIRY_MAIL_FROM", "hotelbhimas.enquiry@gmail.com")

enquiry_mailjet = Client(auth=(ENQUIRY_MAILJET_API_KEY, ENQUIRY_MAILJET_API_SECRET), version='v3.1')


def send_booking_email(booking_data, pdf_path):
    """Send booking confirmation email via Mailjet"""

    if not MAILJET_API_KEY or not MAILJET_API_SECRET:
        logger.error("❌ Mailjet credentials missing")
        raise Exception("Mailjet not configured")

    recipients = [
        {
            "Email": booking_data['guest_email'],
            "Name": booking_data['guest_name']
        },
        {
            "Email": "gk200432@gmail.com",
            "Name": "Hotel Bhimas"
        }
    ]

    # ✅ PDF attachment
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

    # ✅ PUBLIC LOGO URL (NO CID)
    logo_url = os.getenv(
        "LOGO_URL",
        "https://res.cloudinary.com/dgjqjcowj/image/upload/v1775060597/logo-gold_fue1rl.png"
    )

    # 🔥 HTML (UNCHANGED except logo)
    email_body = f"""
<html>
<body style="font-family: Arial, sans-serif; background-color:#f9f9f9; padding:20px;">

    <table width="100%" cellpadding="0" cellspacing="0" style="max-width:600px; margin:auto; background:white; border-radius:8px; overflow:hidden;">

        <tr>
    <td style="background-color:#000; padding:25px; text-align:center;">

       <img src="{logo_url}"
        width="100"
        style="display:block; margin:0 auto 10px auto;" />

        <h1 style="
            color:#D4AF37;
            font-family: Arial, sans-serif;
            font-size:24px;
            letter-spacing:2px;
            margin:0;
        ">
            HOTEL BHIMAS
        </h1>

        <p style="
            color:#ffffff;
            font-size:12px;
            margin-top:5px;
            letter-spacing:1px;
        ">
            Luxury & Comfort
        </p>

    </td>
</tr>

        <tr>
            <td style="padding:25px;">
                <h2 style="color:#333;">Booking Confirmed</h2>

                <p>Dear <strong>{booking_data['guest_name']}</strong>,</p>

                <p>Your booking has been successfully confirmed.</p>

                <table width="100%" style="margin-top:15px;">
                    <tr>
                        <td><strong>Booking ID:</strong></td>
                        <td>{booking_data['booking_id']}</td>
                    </tr>
                    <tr>
                        <td><strong>Check-in:</strong></td>
                        <td>{booking_data['check_in'].strftime("%d-%m-%Y")}</td>
                    </tr>
                    <tr>
                        <td><strong>Check-out:</strong></td>
                        <td>{booking_data['check_out'].strftime("%d-%m-%Y")}</td>
                    </tr>
                    <tr>
                        <td><strong>Total Paid:</strong></td>
                        <td>₹ {booking_data['grand_total']}</td>
                    </tr>
                </table>

                <p style="margin-top:20px;">
                    We look forward to welcoming you!
                </p>
            </td>
        </tr>

        <tr>
            <td style="background:#f2f2f2; padding:15px; text-align:center; font-size:12px; color:#666;">
                Hotel Bhimas<br>
                42, G Car Street, Tirupati - 517501<br>
                Landline: +91877-2225744<br>
                Mobile: +919347172758<br>
                hotelbhimas@gmail.com
            </td>
        </tr>

    </table>

</body>
</html>
"""

    try:
        data = {
            "Messages": [
                {
                    "From": {
                        "Email": MAIL_FROM,
                        "Name": "Hotel Bhimas"
                    },
                    "To": recipients,
                    "Subject": f"Hotel Bhimas Booking Confirmation",
                    "TextPart": "Your booking is confirmed",
                    "HTMLPart": email_body,
                    "Attachments": pdf_attachments,
                    "TrackOpen": 1,
                    "TrackClick": 1
                }
            ]
        }

        result = mailjet.send.create(data=data)

        if result.status_code == 200:
            logger.info(f"✅ Email sent for booking {booking_data['booking_id']}")
        else:
            logger.error(f"❌ Mailjet failed: {result.status_code}")
            logger.error(result.json())
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
                    "From": {
                        "Email": ENQUIRY_MAIL_FROM,
                        "Name": "Hotel Bhimas Website"
                    },
                    "To": [
                        {
                            "Email": "gk200432@gmail.com",
                            "Name": "Hotel Bhimas"
                        }
                    ],
                    "ReplyTo": {
                        "Email": email,
                        "Name": name
                    },
                    "Subject": f"New Contact Enquiry from {name}",
                    "TextPart": f"Name: {name}\nEmail: {email}\nPhone: {phone}\n\nMessage:\n{message}",
                    "HTMLPart": f"""
<html>
<body style="font-family: Arial, sans-serif; background-color:#f9f9f9; padding:20px;">
    <div style="max-width:600px; margin:auto; background:white; border-radius:8px; padding:20px; border:1px solid #ddd;">
        <h2 style="color:#333; border-bottom:2px solid #E5C07B; padding-bottom:10px;">New Contact Enquiry</h2>
        
        <p><strong>From:</strong> {name}</p>
        <p><strong>Email:</strong> <a href="mailto:{email}">{email}</a></p>
        <p><strong>Phone:</strong> {phone}</p>
        
        <hr style="margin:20px 0;">
        
        <h3 style="color:#E5C07B;">Message:</h3>
        <p style="line-height:1.6; white-space:pre-wrap;">{message}</p>
        
        <hr style="margin:20px 0;">
        
        <p style="font-size:12px; color:#666;">
            <strong>Hotel Bhimas</strong><br>
            42, G Car Street, Tirupati - 517501<br>
            Landline: +91877-2225744<br>
            Mobile: +919347172758<br>
            Email: hotelbhimas@gmail.com
        </p>
    </div>
</body>
</html>
"""
                }
            ]
        }

        result = enquiry_mailjet.send.create(data=data)

        if result.status_code == 200:
            logger.info(f"✅ Enquiry email sent from {email} via separate Mailjet account")
        else:
            logger.error(f"❌ Enquiry Mailjet failed: {result.status_code}")
            logger.error(result.json())
            raise Exception("Enquiry email sending failed")

    except Exception as e:
        logger.error(f"❌ Enquiry email error: {str(e)}")
        raise