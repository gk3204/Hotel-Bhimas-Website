from fastapi import APIRouter
from pydantic import BaseModel
from services.email_service import send_enquiry_email
from schemas import Enquiry
router = APIRouter(prefix="/enquiry", tags=["Enquiry"])

@router.post("/send")
def send_enquiry(data: Enquiry):
    send_enquiry_email(
        name=data.name,
        email=data.email,
        phone=data.phone,
        message=data.message
    )
    return {"status": "Enquiry sent successfully"}
