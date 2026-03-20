from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from datetime import datetime

router = APIRouter(prefix="/admin-security", tags=["Admin Security"])


class SecretRequest(BaseModel):
    secret: str


def generate_secret():
    now = datetime.now()
    base = now.strftime("%H%M")
    order = [2, 0, 3, 1]
    return "".join([base[i] for i in order])


@router.post("/validate-secret")
def validate_secret(data: SecretRequest):
    correct_secret = generate_secret()

    if data.secret != correct_secret:
        raise HTTPException(status_code=401, detail="Invalid secret")

    return {"message": "Access granted"}
