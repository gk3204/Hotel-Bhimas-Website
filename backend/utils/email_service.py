from mailjet_rest import Client
import os
import logging

logger = logging.getLogger(__name__)

MAILJET_API_KEY = os.getenv("MAILJET_API_KEY")
MAILJET_API_SECRET = os.getenv("MAILJET_API_SECRET")

mailjet = Client(auth=(MAILJET_API_KEY, MAILJET_API_SECRET)) \
    if MAILJET_API_KEY and MAILJET_API_SECRET else None


async def send_booking_email(booking, pdf_path):

    if not mailjet:
        logger.error("Mailjet not configured")
        return

    recipients = [
        {"Email": booking.guest.email, "Name": booking.guest.name}
    ]

    from_email = os.getenv("MAIL_FROM")

    data = {
        "Messages": [
            {
                "From": {
                    "Email": from_email,
                    "Name": "Hotel Bhimas"
                },
                "To": recipients,
                "Subject": "Booking Confirmed",
                "TextPart": f"Booking ID: {booking.booking_id}"
            }
        ]
    }

    result = mailjet.send.create(data=data)

    if result.status_code == 200:
        logger.info("✅ Email sent")
    else:
        logger.error(f"❌ Failed: {result.status_code}")
        logger.error(result.text)