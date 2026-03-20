from fastapi_mail import FastMail, MessageSchema, ConnectionConfig
from pydantic import EmailStr
import os

conf = ConnectionConfig(
    MAIL_USERNAME=os.getenv("MAIL_USERNAME"),
    MAIL_PASSWORD=os.getenv("MAIL_PASSWORD"),
    MAIL_FROM=os.getenv("MAIL_FROM"),
    MAIL_SERVER=os.getenv("MAIL_SERVER"),
    MAIL_PORT=587,
    MAIL_STARTTLS=True,
    MAIL_SSL_TLS=False,
    USE_CREDENTIALS=True
)

async def send_booking_email(booking, pdf_path):
    recipients = [
        booking.guest.email,
        "gk200432@gmail.com"
    ]
    
    message = MessageSchema(
        headers={"X-Priority": "1"},
        subject="Hotel Bhimas Booking Confirmation",
        recipients=recipients,
        body=f"""
<html>
<body style="font-family: Arial, sans-serif; background-color:#f9f9f9; padding:20px;">

    <table width="100%" cellpadding="0" cellspacing="0" style="max-width:600px; margin:auto; background:white; border-radius:8px; overflow:hidden;">
        
        <!-- HEADER -->
        <tr>
    <td style="background-color:#000; padding:25px; text-align:center;">
        
       <img src="cid:logo_image"
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
""",attachments=[
        {
            "file": pdf_path,
        },
        {
            "file": "assets/logo-gold.png",
            "headers": {
                "Content-ID": "<logo_image>"
            }
        }
    ],
        subtype="html",
        reply_to=["hotelbhimas@gmail.com"]
    )

    fm = FastMail(conf)
    await fm.send_message(message)