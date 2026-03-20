from sqlalchemy.orm import Session
from database import SessionLocal, engine
from models import Base, User
from passlib.context import CryptContext

pwd_context = CryptContext(
    schemes=["bcrypt"],
    deprecated="auto",
)

def hash_password(password: str) -> str:
    # bcrypt safety: max 72 bytes
    password = password.encode("utf-8")[:72]
    return pwd_context.hash(password)

Base.metadata.create_all(bind=engine)

db = SessionLocal()

username = "admin"
password = "Admin@123"   # KEEP IT SHORT
role = "admin"

existing = db.query(User).filter(User.username == username).first()

if existing:
    print("Admin already exists")
else:
    hashed_password = hash_password(password)
    admin = User(
        username=username,
        password_hash=hashed_password,
        role=role
    )
    db.add(admin)
    db.commit()
    print("Admin created successfully")

db.close()
