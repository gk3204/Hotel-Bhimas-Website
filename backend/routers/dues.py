"""v7s: Dues - travel-agent bills the guest left as "pay later" at check-out (see services/dues.py).

GET  /dues                        the list (open / paid / all, by agent, search) + per-agent totals;
                                  ?format=csv|pdf exports it
POST /dues/{folio_id}/payments    money received against one bill (part or full) - a receipt RCPT-n
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from database import SessionLocal
from models import Folio
from services import dues
from utils.audit import write_audit
from utils.auth_utils import require_admin, require_admin_or_accountant

router = APIRouter(prefix="/dues", tags=["Dues"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class DuePaymentRequest(BaseModel):
    amount: float = Field(..., gt=0, le=10_000_000)
    method: str = Field(..., pattern="^(cash|card|upi|bank)$")
    reference: Optional[str] = Field(None, max_length=100)
    note: Optional[str] = Field(None, max_length=200)


@router.get("", dependencies=[Depends(require_admin_or_accountant)])       # v8a: accountant reads, admin receives
@router.get("/", dependencies=[Depends(require_admin_or_accountant)], include_in_schema=False)
def list_dues(status: str = Query("open", pattern="^(open|paid|all)$"),
              agent_id: Optional[int] = Query(None, gt=0),
              q: Optional[str] = Query(None, max_length=80),
              format: Optional[str] = Query(None),
              db: Session = Depends(get_db)):
    data = dues.list_dues(db, status=status, agent_id=agent_id, q=q)
    from routers.reports import _export_or_json
    cols = ["Bill", "Bkg", "Guest", "Phone", "Rooms", "Agent", "Checked out", "Bill amount", "Due at check-out",
            "Received", "Still due", "Days", "Status"]
    rows = [[r["invoice_no"], r["booking_id"], r["guest"], r["phone"], r["rooms"], r["agent"], r["checked_out_at"],
             r["bill_amount"], r["due_amount"], r["received"], r["outstanding"], r["days_pending"],
             "DUE" if r["status"] == "due" else f"PAID {r['paid_at']}"] for r in data["rows"]]
    t = data["totals"]
    totals = ["TOTAL", "", "", "", "", "", "", "", t["due_amount"], t["received"], t["outstanding"], "", ""]
    return _export_or_json(format, data, title="Dues (pay later)", columns=cols, rows=rows, totals_row=totals,
                           meta={"status": status}, filename=f"dues_{status}")


@router.post("/{folio_id}/payments")
def receive_due(folio_id: int, data: DuePaymentRequest, db: Session = Depends(get_db),
                user=Depends(require_admin)):
    folio = db.query(Folio).filter(Folio.id == folio_id).with_for_update().first()
    if folio is None:
        raise HTTPException(status_code=404, detail="Bill not found")
    owed_before = dues.outstanding(folio)
    pay = dues.record_payment(db, folio, amount=data.amount, method=data.method,
                              reference=(data.reference or "").strip() or None, user=user,
                              note=(data.note or "").strip() or None)
    db.commit()
    write_audit(db, user, "dues.payment_received", "folio", folio.id,
                before={"outstanding": owed_before},
                after={"booking_id": folio.booking_id, "payment_id": pay.payment_id, "amount": float(pay.amount),
                       "method": pay.method, "reference": pay.payment_id_gateway, "note": data.note,
                       "outstanding": dues.outstanding(folio)},
                client="web", commit=True)
    return {"receipt_no": f"RCPT-{pay.payment_id}", "payment_id": pay.payment_id, **dues.row(db, folio)}
