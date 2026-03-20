from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import SessionLocal
from models import User
from schemas import UserCreate, UserUpdate
from passlib.context import CryptContext
from utils.auth_utils import require_admin
router = APIRouter(
    prefix="/users",
    tags=["Users"]
)

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def hash_password(password: str):
    return pwd_context.hash(password)


# ✅ GET all users
@router.get("/",dependencies=[Depends(require_admin)])
def get_users(db: Session = Depends(get_db)):
    return db.query(User).all()


# ✅ CREATE user
@router.post("/",dependencies=[Depends(require_admin)])
def create_user(data: UserCreate, db: Session = Depends(get_db)):

    existing = db.query(User).filter(
        User.username == data.username
    ).first()

    if existing:
        raise HTTPException(status_code=400, detail="Username already exists")

    user = User(
        username=data.username,
        password_hash=hash_password(data.password),
        role=data.role
    )

    db.add(user)
    db.commit()
    db.refresh(user)

    return user


@router.put("/{user_id}",dependencies=[Depends(require_admin)])
def update_user(user_id: int, data: UserUpdate, db: Session = Depends(get_db)):

    user = db.query(User).filter(User.user_id == user_id).first()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if data.password and data.password.strip() != "":
        user.password_hash = hash_password(data.password)

    if data.role:
        user.role = data.role

    db.commit()
    db.refresh(user)

    return user


# ✅ DELETE user
@router.delete("/{user_id}",dependencies=[Depends(require_admin)])
def delete_user(user_id: int, db: Session = Depends(get_db)):

    user = db.query(User).filter(User.user_id == user_id).first()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    db.delete(user)
    db.commit()

    return {"message": "User deleted successfully"}
