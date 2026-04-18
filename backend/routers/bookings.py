from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import and_, func
from datetime import date, datetime
import logging

from database import SessionLocal
from models import Room, RoomType, Booking, Guest, RoomTypeAvailability, BookingItem, Payment
from schemas import BookingCreate
from sqlalchemy.orm import joinedload
from utils.auth_utils import require_reception_or_admin, require_admin
from scripts.expire_booking_jobs import expire_pending_bookings
from routers.payments import process_razorpay_refund
from utils.email_service import send_admin_cancellation_email

# Request model for admin cancellation
class AdminCancelRequest(BaseModel):
    reason: str  # "Double booking error", "Guest request", "Technical issue", "Other"
    refund_amount: float  # Amount to refund
    admin_notes: str | None = None  # Optional admin notes

router = APIRouter(
    prefix="/bookings",
    tags=["Bookings"]
)

logger = logging.getLogger(__name__)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.post("/")
def create_booking(
    data: BookingCreate,
    db: Session = Depends(get_db)
):
    """Create multi-room booking with proper validation and error handling"""
    try:
        # Cancel expired bookings first
        expired = expire_pending_bookings(db)
        logger.info(f"{expired} expired bookings cleaned")
        
        # 1️⃣ Validate dates
        today = date.today()

        if data.check_in < today:
            logger.warning(f"Booking attempt with past check-in date: {data.check_in}")
            raise HTTPException(
                status_code=400,
                detail="Check-in date cannot be in the past"
            )

        if data.check_out <= data.check_in:
            logger.warning(f"Invalid date range: check-in={data.check_in}, check-out={data.check_out}")
            raise HTTPException(
                status_code=400,
                detail="Check-out must be after check-in"
            )

        # 2️⃣ Validate and collect room types
        room_types_map = {}  # room_type_id -> RoomType object
        for room_item in data.rooms:
            room_type = db.query(RoomType).filter(
                RoomType.room_type_id == room_item.room_type_id,
                RoomType.is_active == True
            ).first()

            if not room_type:
                logger.warning(f"Room type {room_item.room_type_id} not available")
                raise HTTPException(
                    status_code=400,
                    detail=f"Room type {room_item.room_type_id} not available"
                )
            
            # 3️⃣ Check date-based availability for each room type
            blocked = db.query(RoomTypeAvailability).filter(
                RoomTypeAvailability.room_type_id == room_item.room_type_id,
                RoomTypeAvailability.date >= data.check_in,
                RoomTypeAvailability.date < data.check_out,
                RoomTypeAvailability.is_available == False
            ).first()

            if blocked:
                logger.warning(
                    f"Room type {room_item.room_type_id} blocked for dates {data.check_in}-{data.check_out}. "
                    f"Reason: {blocked.reason}"
                )
                raise HTTPException(
                    status_code=409,
                    detail=f"{room_type.name} not available for selected dates. "
                            f"Reason: {blocked.reason or 'Rooms fully booked'}"
                )
            
            # 4️⃣ Check room count availability - ensure enough rooms exist
            # Only count confirmed, pending_payment & payment_pending (reserved bookings)
            booked_rooms = db.query(func.sum(BookingItem.quantity)).filter(
                BookingItem.room_type_id == room_item.room_type_id,
                BookingItem.booking_id.in_(
                    db.query(Booking.booking_id).filter(
                        Booking.status.in_(["confirmed", "pending_payment", "payment_pending"]),
                        Booking.check_in < data.check_out,
                        Booking.check_out > data.check_in
                    ).with_for_update()  # 🔒 Lock rows to prevent concurrent overbooking
                )
            ).scalar() or 0

            available_rooms = room_type.total_rooms - booked_rooms
            requested_rooms = room_item.quantity

            if requested_rooms > available_rooms:
                logger.warning(
                    f"Insufficient rooms: Room type {room_item.room_type_id} has {available_rooms} available "
                    f"but {requested_rooms} requested (Total: {room_type.total_rooms}, Booked: {booked_rooms})"
                )
                raise HTTPException(
                    status_code=409,
                    detail=f"Not enough {room_type.name}s available. "
                            f"Available: {available_rooms}/{room_type.total_rooms}, Requested: {requested_rooms}"
                )
            
            room_types_map[room_item.room_type_id] = room_type
            logger.debug(
                f"Room type {room_item.room_type_id} availability check passed "
                f"for {data.check_in}-{data.check_out} ({requested_rooms} rooms selected, {available_rooms} available)"
            )

        # 4️⃣ Create guest
        guest = Guest(
            name=data.guest_name,
            phone=data.phone,
            email=data.email
        )
        db.add(guest)
        db.flush()  # Get guest_id without committing

        # 5️⃣ Calculate amounts for each room and total
        nights = (data.check_out - data.check_in).days
        total_base = 0
        total_gst = 0
        total_room_amount = 0
        
        booking_items_data = []

        for room_item in data.rooms:
            room_type = room_types_map[room_item.room_type_id]
            
            # Calculate for this room type & quantity
            base_amount = round(
                nights * float(room_type.price_per_night) * room_item.quantity,
                2
            )

            gst_amount = round(
                base_amount * float(room_type.gst_percent) / 100,
                2
            )

            item_total = round(base_amount + gst_amount, 2)
            
            total_base += base_amount
            total_gst += gst_amount
            total_room_amount += item_total
            
            booking_items_data.append({
                "room_type_id": room_item.room_type_id,
                "quantity": room_item.quantity,
                "base_amount": base_amount,
                "gst_amount": gst_amount,
                "total_amount": item_total
            })

        # Razorpay convenience fee (2% + 18% GST on total room amount)
        convenience_base = round(total_room_amount * 0.02, 2)
        convenience_gst = round(convenience_base * 0.18, 2)
        convenience_fee_total = round(convenience_base + convenience_gst, 2)

        grand_total = round(total_room_amount + convenience_fee_total, 2)

        # 6️⃣ Create booking (without room assignment yet)
        booking = Booking(
            guest_id=guest.guest_id,
            check_in=data.check_in,
            check_out=data.check_out,
            booking_source=data.booking_source,
            status="pending_payment",
            base_amount=total_base,
            gst_amount=total_gst,
            total_amount=total_room_amount,
            convenience_fee=convenience_base,
            convenience_gst=convenience_gst,
            grand_total=grand_total
        )
        
        db.add(booking)
        db.flush()  # Get booking_id without committing

        # 7️⃣ Create booking items for each room
        for item_data in booking_items_data:
            booking_item = BookingItem(
                booking_id=booking.booking_id,
                room_type_id=item_data["room_type_id"],
                quantity=item_data["quantity"],
                base_amount=item_data["base_amount"],
                gst_amount=item_data["gst_amount"],
                total_amount=item_data["total_amount"]
            )
            db.add(booking_item)
        
        db.commit()
        db.refresh(booking)
        
        logger.info(f"Booking {booking.booking_id} created with {len(booking_items_data)} room(s) for guest {guest.guest_id}")
        
        return {
            "booking_id": booking.booking_id,
            "guest_id": guest.guest_id,
            "check_in": booking.check_in,
            "check_out": booking.check_out,
            "status": booking.status,
            "base_amount": float(booking.base_amount),
            "gst_amount": float(booking.gst_amount),
            "total_amount": float(booking.total_amount),
            "convenience_fee": float(booking.convenience_fee),
            "convenience_gst": float(booking.convenience_gst),
            "grand_total": float(booking.grand_total),
            "room_count": len(booking_items_data)
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating booking: {str(e)}", exc_info=True)
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to create booking")


@router.patch("/{booking_id}/cancel",dependencies=[Depends(require_admin)])
def cancel_booking(
    booking_id: int,
    db: Session = Depends(get_db)
):
    booking = db.query(Booking).filter(
        Booking.booking_id == booking_id
    ).first()

    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")

    booking.status = "cancelled"
    db.commit()

    return {"message": "Booking cancelled"}

@router.get("/",dependencies=[Depends(require_reception_or_admin)])
def read_all_bookings(
    from_date: date | None = None,
    skip: int = 0,
    limit: int = 15,
    db: Session = Depends(get_db)
):
    # 🔒 Clean up expired bookings before fetching
    expire_pending_bookings(db)
    
    query = db.query(Booking).join(Guest, Booking.guest_id == Guest.guest_id)

    # ✅ Filter by from_date only
    if from_date:
        query = query.filter(Booking.check_in >= from_date)

    total_count = query.count()

    bookings = (
        query
        .options(joinedload(Booking.booking_items).joinedload(BookingItem.room_type))
        .order_by(Booking.check_in.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )

    result = []

    for booking in bookings:
        room_types = [item.room_type.name for item in booking.booking_items]
        result.append({
            "booking_id": booking.booking_id,
            "guest_name": booking.guest.name,
            "room_types": ", ".join(room_types),
            "room_count": len(booking.booking_items),
            "check_in": booking.check_in,
            "check_out": booking.check_out,
            "status": booking.status,
            "payable_amount": float(booking.grand_total),
        })

    return {
        "total": total_count,
        "data": result
    }

# @router.get("/")
# def read_all_bookings(db: Session = Depends(get_db)):
#     bookings = (
#         db.query(Booking, Guest, Room, RoomType)
#         .join(Guest, Booking.guest_id == Guest.guest_id)
#         .join(RoomType, Booking.room_type_id == RoomType.room_type_id)
#         .outerjoin(Room, Booking.room_id == Room.room_id)
#         .order_by(Booking.check_in.desc())
#         .all()
#     )

#     result = []

#     for booking, guest, room, room_type in bookings:
#         result.append({
#             "booking_id": booking.booking_id,
#             "guest_name": guest.name,
#             "phone": guest.phone,
#             "email": guest.email,

#             "room_number": room.room_number if room else None,
#             "room_type": room_type.name if room_type else None,

#             "check_in": booking.check_in,
#             "check_out": booking.check_out,
#             "status": booking.status,

#             "room_total": booking.total_amount,
#             "convenience_fee": booking.convenience_fee,
#             "convenience_gst": booking.convenience_gst,
#             "payable_amount": booking.grand_total,

#             "booking_source": booking.booking_source,
#         })


#     return result


@router.get("/{booking_id}")
def read_booking(booking_id: int, db: Session = Depends(get_db)):
    booking = (
        db.query(Booking)
        .options(
            joinedload(Booking.guest),
            joinedload(Booking.booking_items).joinedload(BookingItem.room_type)
        )
        .filter(Booking.booking_id == booking_id)
        .first()
    )

    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")

    # Prepare room items
    rooms = []
    for item in booking.booking_items:
        rooms.append({
            "room_type_id": item.room_type.room_type_id,
            "room_type_name": item.room_type.name,
            "price_per_night": float(item.room_type.price_per_night),
            "quantity": item.quantity,
            "base_amount": float(item.base_amount),
            "gst_amount": float(item.gst_amount),
            "total_amount": float(item.total_amount)
        })

    return {
        "booking_id": booking.booking_id,

        "guest": {
            "name": booking.guest.name,
            "phone": booking.guest.phone,
            "email": booking.guest.email,
        },

        "rooms": rooms,

        "stay": {
            "check_in": booking.check_in,
            "check_out": booking.check_out,
            "nights": (booking.check_out - booking.check_in).days,
        },

        "charges": {
            "room_base": float(booking.base_amount),
            "room_gst": float(booking.gst_amount),
            "room_total": float(booking.total_amount),

            "convenience_fee": float(booking.convenience_fee),
            "convenience_gst": float(booking.convenience_gst),

            "payable_amount": float(booking.grand_total),
        },

        "status": booking.status,
        "booking_source": booking.booking_source,
    }



@router.get("/filter/by-date",dependencies=[Depends(require_reception_or_admin)])
def bookings_by_date(
    from_date: date,
    to_date: date,
    db: Session = Depends(get_db)
):
    # 🔒 Clean up expired bookings before fetching
    expire_pending_bookings(db)
    
    bookings = db.query(Booking).filter(
        Booking.check_in < to_date,
        Booking.check_out > from_date
    ).all()

    return bookings

@router.get("/filter/by-status/{status}")
def bookings_by_status(status: str, db: Session = Depends(get_db)):
    # 🔒 Clean up expired bookings before fetching
    expire_pending_bookings(db)
    
    bookings = db.query(Booking).filter(
        Booking.status == status
    ).all()

    return bookings

@router.post("/{booking_id}/admin-cancel", dependencies=[Depends(require_admin)])
def admin_cancel_booking(
    booking_id: int,
    request: AdminCancelRequest,
    db: Session = Depends(get_db)
):
    """
    Admin endpoint to cancel a booking and process refund
    Required fields in request body:
    - reason: "Double booking error", "Guest request", "Technical issue", "Other"
    - refund_amount: Amount to refund (e.g., full or full - convenience fee)
    - admin_notes: Optional notes
    """
    try:
        # 1️⃣ Fetch and lock booking
        booking = db.query(Booking).with_for_update().filter(
            Booking.booking_id == booking_id
        ).first()
        
        if not booking:
            raise HTTPException(status_code=404, detail="Booking not found")
        
        # 2️⃣ Check if booking can be cancelled (must be confirmed or payment_pending)
        if booking.status not in ["confirmed", "payment_pending"]:
            raise HTTPException(
                status_code=400, 
                detail=f"Cannot cancel booking with status: {booking.status}"
            )
        
        # 3️⃣ Fetch payment record
        payment = db.query(Payment).filter(
            Payment.booking_id == booking_id,
            Payment.status == "paid"
        ).first()
        
        if not payment:
            raise HTTPException(status_code=404, detail="Payment record not found")
        
        # 4️⃣ Process refund via Razorpay
        success, refund_id, refund_msg = process_razorpay_refund(
            db, 
            payment.payment_id, 
            request.refund_amount, 
            request.reason
        )
        
        if not success:
            raise HTTPException(status_code=400, detail=f"Refund failed: {refund_msg}")
        
        # 5️⃣ Update booking status
        booking.status = "admin_cancelled"
        booking.admin_cancelled = True
        booking.admin_cancelled_reason = request.reason
        booking.admin_notes = request.admin_notes
        booking.admin_cancelled_by = "admin"  # In production, get from JWT token
        booking.admin_cancelled_at = datetime.now()
        booking.cancelled_at = datetime.now()
        db.commit()
        
        # 6️⃣ Send notification email to guest
        try:
            send_admin_cancellation_email(
                guest_email=booking.guest.email,
                guest_name=booking.guest.name,
                booking_ref=booking_id,
                check_in=booking.check_in,
                check_out=booking.check_out,
                refund_amount=request.refund_amount,
                refund_id=refund_id,
                reason=request.reason,
                admin_notes=request.admin_notes
            )
        except Exception as e:
            logger.error(f"Failed to send cancellation email: {str(e)}")
        
        logger.info(f"✅ Booking {booking_id} cancelled by admin. Refund ID: {refund_id}, Amount: ₹{request.refund_amount}")
        
        return {
            "booking_id": booking_id,
            "status": "admin_cancelled",
            "refund_id": refund_id,
            "refund_amount": request.refund_amount,
            "refund_status": "completed",
            "message": "Booking cancelled and refund processed successfully"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error cancelling booking {booking_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Server error: {str(e)}")