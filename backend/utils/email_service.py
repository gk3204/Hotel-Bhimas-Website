from mailjet_rest import Client
import os
import logging

logger = logging.getLogger(__name__)

MAILJET_API_KEY = os.getenv("MJ_APIKEY_PUBLIC")
MAILJET_API_SECRET = os.getenv("MJ_APIKEY_PRIVATE")

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
    "Email": "pilot@mailjet.com",
    "Name": "Hotel Bhimas"
},
                "To": recipients,
                "Subject": "Booking Confirmed",
                "TextPart": f"Booking ID: {booking.booking_id}"
            }
        ]
    }

    result = mailjet.send.create(data={
    "Messages": [
        {
            "From": {
                "Email": "pilot@mailjet.com",
                "Name": "Test"
            },
            "To": [
                {
                    "Email": "bhimasamogh@gmail.com",
                    "Name": "Test"
                }
            ],
            "Subject": "Test Email",
            "TextPart": "Hello from Mailjet"
        }
    ]
})

    if result.status_code == 200:
        logger.info(f"✅ Email sent for booking {booking.booking_id}")
    else:
        logger.error(f"❌ Mailjet failed: {result.status_code}")
        logger.error(result.text)
        raise Exception("Email sending failed")