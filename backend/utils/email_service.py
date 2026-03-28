from mailjet_rest import Client
import base64
import os
import logging

logger = logging.getLogger(__name__)

# Initialize Mailjet client
MAILJET_API_KEY = os.getenv("MAILJET_API_KEY")
MAILJET_API_SECRET = os.getenv("MAILJET_API_SECRET")

if not MAILJET_API_KEY or not MAILJET_API_SECRET:
    logger.warning("Mailjet credentials not fully configured. Email sending will fail. Set MAILJET_API_KEY and MAILJET_API_SECRET in environment variables.")

mailjet = Client(auth=(MAILJET_API_KEY, MAILJET_API_SECRET)) if MAILJET_API_KEY and MAILJET_API_SECRET else None


async def send_booking_email(booking, pdf_path):
    """Send booking confirmation email via Mailjet"""

    if not MAILJET_API_KEY or not MAILJET_API_SECRET or not mailjet:
        logger.error("Mailjet credentials not configured (MAILJET_API_KEY or MAILJET_API_SECRET missing)")
        return

    recipients = [
        {"Email": booking.guest.email, "Name": booking.guest.name},
        {"Email": "gk200432@gmail.com", "Name": "Hotel Bhimas"}
    ]

    # Get absolute path to logo
    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    logo_path = os.path.join(backend_dir, "assets", "logo-gold.png")

    email_body = f"""
<html>
<body style="font-family: Arial, sans-serif; background-color:#f9f9f9; padding:20px;">

    <table width="100%" cellpadding="0" cellspacing="0" style="max-width:600px; margin:auto; background:white; border-radius:8px; overflow:hidden;">

        <!-- HEADER -->
        <tr>
    <td style="background-color:#000; padding:25px; text-align:center;">

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

        <!-- BODY -->
        <tr>
            <td style="padding:25px;">
                <h2 style="color:#333;">Booking Confirmed</h2>

                <p>Dear <strong>{booking.guest.name}</strong>,</p>

                <p>Your booking has been successfully confirmed.</p>

                <table width="100%" style="margin-top:15px;">
                    <tr>
                        <td><strong>Booking ID:</strong></td>
                        <td>{booking.booking_id}</td>
                    </tr>
                    <tr>
                        <td><strong>Check-in:</strong></td>
                        <td>{booking.check_in.strftime("%d-%m-%Y")}</td>
                    </tr>
                    <tr>
                        <td><strong>Check-out:</strong></td>
                        <td>{booking.check_out.strftime("%d-%m-%Y")}</td>
                    </tr>
                    <tr>
                        <td><strong>Total Paid:</strong></td>
                        <td>₹ {booking.grand_total}</td>
                    </tr>
                </table>

                <p style="margin-top:20px;">
                    We look forward to welcoming you!
                </p>
            </td>
        </tr>

        <!-- FOOTER -->
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

    # Build attachments list
    attachments = []

    # For now, skip attachments to test basic functionality
    # TODO: Re-enable attachments after basic email works

    # # Attach PDF
    # try:
    #     with open(pdf_path, 'rb') as pdf_file:
    #         pdf_content = base64.b64encode(pdf_file.read()).decode()
    #         attachments.append({
    #             "ContentType": "application/pdf",
    #             "Filename": "booking_confirmation.pdf",
    #             "Base64Content": pdf_content
    #         })
    #         logger.debug(f"PDF attached: {pdf_path}")
    # except Exception as e:
    #     logger.warning(f"Failed to attach PDF: {str(e)}")

    # # Attach logo
    # if os.path.exists(logo_path):
    #     try:
    #         with open(logo_path, 'rb') as logo_file:
    #             logo_content = base64.b64encode(logo_file.read()).decode()
    #             attachments.append({
    #                 "ContentType": "image/png",
    #                 "Filename": "logo-gold.png",
    #                 "Base64Content": logo_content,
    #                 "ContentID": "logo_image"
    #             })
    #             logger.debug(f"Logo attached: {logo_path}")
    #     except Exception as e:
    #         logger.warning(f"Failed to attach logo: {str(e)}")
    # else:
    #     logger.warning(f"Logo not found at: {logo_path}")

    try:
        # Build Mailjet message
        from_email = os.getenv("MAIL_FROM", "noreply@hotelbhimas.com")
        logger.debug(f"Preparing email from: {from_email}")

        data = {
            "Messages": [
                {
                    "From": {
                        "Email": from_email,
                        "Name": "Hotel Bhimas"
                    },
                    "To": recipients,
                    "Subject": "Hotel Bhimas Booking Confirmation",
                    "HTMLPart": email_body,
                    "Attachments": attachments,
                    "ReplyTo": {
                        "Email": "hotelbhimas@gmail.com",
                        "Name": "Hotel Bhimas"
                    }
                }
            ]
        }

        # Send email via Mailjet
        logger.info(f"Sending email to {len(recipients)} recipients via Mailjet")
        logger.info(f"Message recipients: {recipients}")
        logger.info(f"From email: {from_email}")
        logger.info(f"Attachments count: {len(attachments)}")

        result = mailjet.send.create(data=data)

        logger.info(f"Mailjet response status: {result.status_code}")

        if result.status_code == 200:
            logger.info(f"Email sent successfully for booking {booking.booking_id}")
        else:
            # Get full response for debugging - Mailjet response object
            logger.error(f"Mailjet API Error {result.status_code}")

            # Try different ways to get the error message
            if hasattr(result, 'json') and callable(result.json):
                try:
                    error_data = result.json()
                    logger.error(f"Error JSON: {error_data}")
                except Exception as e:
                    logger.error(f"Could not parse JSON response: {str(e)}")

            if hasattr(result, 'text'):
                logger.error(f"Error Text: {result.text}")

            if hasattr(result, 'content'):
                logger.error(f"Error Content: {result.content}")

            raise Exception(f"Mailjet failed with status {result.status_code}")

    except Exception as e:
        logger.error(f"Failed to send email for booking {booking.booking_id}: {str(e)}", exc_info=True)
        raise
