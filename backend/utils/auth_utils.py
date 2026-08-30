from fastapi import Depends, HTTPException
from jose import jwt, JWTError
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import os
from dotenv import load_dotenv

load_dotenv()

SECRET_KEY = os.getenv("SECRET_KEY")
ALGORITHM = "HS256"

security = HTTPBearer()

def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    token = credentials.credentials

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")


def require_admin(user=Depends(get_current_user)):
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


def require_reception_or_admin(user=Depends(get_current_user)):
    if user.get("role") not in ["admin", "reception"]:
        raise HTTPException(status_code=403, detail="Access denied")
    return user


def require_housekeeper_or_admin(user=Depends(get_current_user)):
    if user.get("role") not in ["admin", "housekeeper"]:
        raise HTTPException(status_code=403, detail="Access denied")
    return user


# v4b8: the `supervisor` role is GONE — merged into `housekeeper`, which now both cleans and
# signs off (room inspection + the final approval on a maintenance ticket). The owner did not
# want two separate housekeeping logins.
# The old name is kept as an alias so no call site dangles, and so a future reader searching
# for "supervisor" lands here rather than concluding the gate was dropped.
require_supervisor_or_admin = require_housekeeper_or_admin


def require_maintenance_or_admin(user=Depends(get_current_user)):
    if user.get("role") not in ["admin", "maintenance"]:
        raise HTTPException(status_code=403, detail="Access denied")
    return user


def require_roomservice(user=Depends(get_current_user)):
    """Room-service ordering (TBC-4). A `roomservice` login is a TABLET role scoped to one
    job: take an order, print the KOT, mark it delivered. Reception and admin are included
    because the front desk takes orders too. The role is deliberately NOT added to any
    money, folio, fraud or config gate — delivering an order posts its charges through the
    shared service, which is the only way it can touch a bill at all."""
    if user.get("role") not in ["admin", "reception", "roomservice"]:
        raise HTTPException(status_code=403, detail="Room-service access required")
    return user


def require_roles(*roles):
    """Generic role gate. Usage: dependencies=[Depends(require_roles('admin', 'reception'))]"""
    allowed = set(roles)

    def _dep(user=Depends(get_current_user)):
        if user.get("role") not in allowed:
            raise HTTPException(status_code=403, detail="Access denied")
        return user

    return _dep
