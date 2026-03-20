import smtplib
from email.message import EmailMessage
from dotenv import load_dotenv
import os

load_dotenv()

EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASS = os.getenv("EMAIL_PASS")
def send_enquiry_email(name: str, email: str, phone: str, message: str):
    msg = EmailMessage()
    msg["Subject"] = "New Enquiry - Hotel Bhimas"
    msg["From"] = EMAIL_USER
    msg["To"] = "hotelbhimas@gmail.com"

    msg.set_content(f"""
Name: {name}
Email: {email}
Phone: {phone}

Message:
{message}
""")

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(EMAIL_USER,EMAIL_PASS)  # Gmail App Password
        server.send_message(msg)
