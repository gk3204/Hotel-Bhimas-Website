from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import date
import logging

from database import SessionLocal
from models import Promotion, RoomType
from schemas import PromotionCreate, PromotionUpdate, PromotionToggle
from utils.auth_utils import require_admin

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/promotions",
    tags=["Promotions"]
)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ============================================================
# Shared discount logic (imported by bookings router)
# ============================================================

def get_active_promotions(db: Session):
    """All promotions with is_active=True (date window is evaluated per-booking)."""
    return db.query(Promotion).filter(Promotion.is_active == True).all()


def _within_window(promo: Promotion, on_date: date) -> bool:
    if promo.valid_from and on_date < promo.valid_from:
        return False
    if promo.valid_to and on_date > promo.valid_to:
        return False
    return True


def compute_item_discount(promo: Promotion, base_amount: float) -> float:
    """Discount amount this promotion yields on a line item's base amount."""
    if promo.discount_type == "percent":
        return round(base_amount * float(promo.discount_value) / 100, 2)
    # flat: never discount more than the base amount
    return round(min(float(promo.discount_value), base_amount), 2)


def best_promotion_for_item(promotions, room_type_id: int, base_amount: float, on_date: date):
    """
    From the active promotions, pick the one giving the largest discount for this
    room-type line item on the given date. Returns (promotion | None, discount_amount).
    """
    best_promo = None
    best_discount = 0.0
    for p in promotions:
        if p.room_type_id is not None and p.room_type_id != room_type_id:
            continue
        if not _within_window(p, on_date):
            continue
        discount = compute_item_discount(p, base_amount)
        if discount > best_discount:
            best_discount = discount
            best_promo = p
    return best_promo, best_discount


def _serialize(promo: Promotion) -> dict:
    return {
        "promotion_id": promo.promotion_id,
        "name": promo.name,
        "discount_type": promo.discount_type,
        "discount_value": float(promo.discount_value),
        "room_type_id": promo.room_type_id,
        "is_active": promo.is_active,
        "valid_from": promo.valid_from,
        "valid_to": promo.valid_to,
    }


# ============================================================
# Public endpoint (used by the booking page to preview discounts)
# ============================================================

@router.get("/active")
def list_active_promotions(db: Session = Depends(get_db)):
    """Active promotions with their date windows; the client evaluates the window
    against the selected check-in date (matching the booking backend)."""
    return [_serialize(p) for p in get_active_promotions(db)]


# ============================================================
# Admin CRUD
# ============================================================

@router.get("/", dependencies=[Depends(require_admin)])
def list_promotions(db: Session = Depends(get_db)):
    promos = db.query(Promotion).order_by(Promotion.created_at.desc()).all()
    return [_serialize(p) for p in promos]


@router.post("/", dependencies=[Depends(require_admin)])
def create_promotion(data: PromotionCreate, db: Session = Depends(get_db)):
    if data.room_type_id is not None:
        room_type = db.query(RoomType).filter(
            RoomType.room_type_id == data.room_type_id
        ).first()
        if not room_type:
            raise HTTPException(status_code=400, detail="Room type not found")

    promo = Promotion(
        name=data.name,
        discount_type=data.discount_type,
        discount_value=data.discount_value,
        room_type_id=data.room_type_id,
        is_active=data.is_active,
        valid_from=data.valid_from,
        valid_to=data.valid_to,
    )
    db.add(promo)
    db.commit()
    db.refresh(promo)
    logger.info(f"Promotion {promo.promotion_id} '{promo.name}' created")
    return _serialize(promo)


@router.put("/{promotion_id}", dependencies=[Depends(require_admin)])
def update_promotion(promotion_id: int, data: PromotionUpdate, db: Session = Depends(get_db)):
    promo = db.query(Promotion).filter(
        Promotion.promotion_id == promotion_id
    ).first()
    if not promo:
        raise HTTPException(status_code=404, detail="Promotion not found")

    update_data = data.dict(exclude_unset=True)

    if update_data.get("room_type_id") is not None:
        room_type = db.query(RoomType).filter(
            RoomType.room_type_id == update_data["room_type_id"]
        ).first()
        if not room_type:
            raise HTTPException(status_code=400, detail="Room type not found")

    for key, value in update_data.items():
        setattr(promo, key, value)

    db.commit()
    db.refresh(promo)
    return _serialize(promo)


@router.patch("/{promotion_id}/toggle", dependencies=[Depends(require_admin)])
def toggle_promotion(promotion_id: int, data: PromotionToggle, db: Session = Depends(get_db)):
    promo = db.query(Promotion).filter(
        Promotion.promotion_id == promotion_id
    ).first()
    if not promo:
        raise HTTPException(status_code=404, detail="Promotion not found")

    promo.is_active = data.is_active
    db.commit()
    return {"promotion_id": promotion_id, "is_active": promo.is_active}


@router.delete("/{promotion_id}", dependencies=[Depends(require_admin)])
def delete_promotion(promotion_id: int, db: Session = Depends(get_db)):
    promo = db.query(Promotion).filter(
        Promotion.promotion_id == promotion_id
    ).first()
    if not promo:
        raise HTTPException(status_code=404, detail="Promotion not found")

    db.delete(promo)
    db.commit()
    return {"message": "Promotion deleted", "promotion_id": promotion_id}
