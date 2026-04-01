from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import SessionLocal
from models import ContactEnquiry
from utils.email_service import send_enquiry_email
from schemas import Enquiry
from utils.auth_utils import require_reception_or_admin
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/enquiry", tags=["Enquiry"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.post("/send")
def send_enquiry(data: Enquiry, db: Session = Depends(get_db)):
    """Submit a contact enquiry"""
    # Save to database
    enquiry = ContactEnquiry(
        name=data.name,
        email=data.email,
        phone=data.phone,
        message=data.message
    )
    db.add(enquiry)
    db.commit()
    db.refresh(enquiry)

    # Send email via Mailjet (Railway-compatible)
    try:
        send_enquiry_email(
            name=data.name,
            email=data.email,
            phone=data.phone,
            message=data.message
        )
        logger.info(f"Enquiry email sent for {data.email}")
    except Exception as e:
        # Log but don't fail if email fails
        logger.error(f"Email sending failed for enquiry: {str(e)}", exc_info=True)

    return {
        "status": "Enquiry sent successfully",
        "enquiry_id": enquiry.enquiry_id
    }


# 📧 GET all enquiries (admin/reception only)
@router.get("/", dependencies=[Depends(require_reception_or_admin)])
def get_all_enquiries(db: Session = Depends(get_db)):
    """Get all contact enquiries"""
    enquiries = db.query(ContactEnquiry).order_by(
        ContactEnquiry.created_at.desc()
    ).all()
    return enquiries


# 📧 GET single enquiry by ID
@router.get("/{enquiry_id}", dependencies=[Depends(require_reception_or_admin)])
def get_enquiry(enquiry_id: int, db: Session = Depends(get_db)):
    """Get specific enquiry details"""
    enquiry = db.query(ContactEnquiry).filter(
        ContactEnquiry.enquiry_id == enquiry_id
    ).first()

    if not enquiry:
        raise HTTPException(status_code=404, detail="Enquiry not found")

    return enquiry


# 🔄 UPDATE enquiry status
@router.patch("/{enquiry_id}/status")
def update_enquiry_status(
    enquiry_id: int,
    status: str,
    db: Session = Depends(get_db),
    _=Depends(require_reception_or_admin)
):
    """Update enquiry status (pending, replied, spam)"""
    if status not in ["pending", "replied", "spam"]:
        raise HTTPException(status_code=400, detail="Invalid status")

    enquiry = db.query(ContactEnquiry).filter(
        ContactEnquiry.enquiry_id == enquiry_id
    ).first()

    if not enquiry:
        raise HTTPException(status_code=404, detail="Enquiry not found")

    enquiry.status = status
    db.commit()
    db.refresh(enquiry)

    return enquiry
