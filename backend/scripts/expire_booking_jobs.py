from database import SessionLocal
from utils.booking_cleanup import expire_pending_bookings

def main():
    db = SessionLocal()
    try:
        expire_pending_bookings(db)
    finally:
        db.close()

if __name__ == "__main__":
    main()
