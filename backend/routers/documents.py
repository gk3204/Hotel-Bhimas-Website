"""Admin document index (v5j): one place to search, preview and download every PDF the system
generates — GST invoices, payment receipts, refund vouchers, registration slips, booking
confirmations, company invoices and cash-shift reports.

Nothing is stored here. Every PDF is regenerated on demand by the endpoint that owns it (folio /
payments / reception / company-invoices / cash-shift), so this router is an INDEX over the DB rows
that identify each document, returning the existing `pdf_url` the browser fetches with the admin
token. The one gap it fills is the booking confirmation, which until now was only produced as an
email / WhatsApp attachment at booking time — `GET /documents/bookings/{id}/confirmation/pdf`
regenerates it the same way the desk path does.

Each listed row is guaranteed to open: the index applies the same preconditions the PDF endpoints
enforce (paid payment, completed refund, checked-in/out stay, an Invoice row, a closed shift).
"""
from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import or_
from sqlalchemy.orm import Session

from database import SessionLocal
from models import (Booking, BookingItem, CashShift, Company, CompanyInvoice, Folio, Guest, Invoice,
                    Payment, Room, User)
from utils import ordering
from utils.auth_utils import require_admin

router = APIRouter(prefix="/documents", tags=["Documents"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


DOC_TYPES = ("invoice", "receipt", "refund", "slip", "confirmation", "company_invoice", "shift_report")


def _parse(d: Optional[str], name: str) -> Optional[date]:
    if not d:
        return None
    try:
        return date.fromisoformat(d)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"{name} must be YYYY-MM-DD")


def _like(q: str) -> str:
    return f"%{q.strip()}%"


def _room_labels(db: Session, booking_ids) -> dict:
    """booking_id -> 'room' label (first assigned room), one query for the whole page."""
    ids = [b for b in set(booking_ids) if b]
    if not ids:
        return {}
    rows = (db.query(BookingItem.booking_id, Room.room_number)
            .join(Room, Room.room_id == BookingItem.room_id)
            .filter(BookingItem.booking_id.in_(ids))
            .order_by(BookingItem.booking_id,
                      *ordering.room_number_key(Room.room_number)).all())
    out = {}
    for bid, num in rows:
        out.setdefault(bid, num)
    return out


def _booking_ids_matching(db: Session, q: str) -> list:
    """Booking ids whose guest name / phone, booking id or room number match `q` — the shared
    free-text filter for every booking-backed document type."""
    like = _like(q)
    ids = set()
    for (bid,) in (db.query(Booking.booking_id).join(Guest, Guest.guest_id == Booking.guest_id)
                   .filter(or_(Guest.name.ilike(like), Guest.phone.ilike(like))).all()):
        ids.add(bid)
    for (bid,) in (db.query(BookingItem.booking_id).join(Room, Room.room_id == BookingItem.room_id)
                   .filter(Room.room_number.ilike(like)).all()):
        ids.add(bid)
    digits = q.strip().lstrip("#")
    if digits.isdigit():
        ids.add(int(digits))
    return list(ids)


def _row(type_, number, when, booking, guest, amount, status, pdf_url, room=None, extra=None):
    return {
        "type": type_,
        "number": number,
        "date": (when.isoformat() if isinstance(when, (datetime, date)) else when),
        "booking_id": booking.booking_id if booking else None,
        "guest": guest.name if guest else (extra or {}).get("guest"),
        "phone": guest.phone if guest else None,
        "room": room,
        "amount": round(float(amount), 2) if amount is not None else None,
        "status": status,
        "pdf_url": pdf_url,
    }


def list_documents(db: Session, q: str = "", type_: str = "all", dfrom: Optional[date] = None,
                   dto: Optional[date] = None) -> list:
    """All matching documents, newest first (unsliced). Each source applies its own date column
    and the shared free-text filter; results are merged in Python — the volume (a few thousand
    rows a year) makes that simpler and safer than a 7-way SQL UNION."""
    want = set(DOC_TYPES) if type_ in ("", "all", None) else {type_}
    if not want <= set(DOC_TYPES):
        raise HTTPException(status_code=400, detail=f"type must be one of all, {', '.join(DOC_TYPES)}")
    q = (q or "").strip()
    match_ids = _booking_ids_matching(db, q) if q else None   # None = no text filter
    start = datetime.combine(dfrom, datetime.min.time()) if dfrom else None
    end = datetime.combine(dto + timedelta(days=1), datetime.min.time()) if dto else None

    def _dt_window(col):
        conds = []
        if start is not None:
            conds.append(col >= start)
        if end is not None:
            conds.append(col < end)
        return conds

    def _d_window(col):
        conds = []
        if dfrom is not None:
            conds.append(col >= dfrom)
        if dto is not None:
            conds.append(col <= dto)
        return conds

    def _booking_filter(col, number_col=None, number_prefixes=(), id_col=None):
        """Restrict a booking-backed query to the free-text matches: by booking (guest / phone /
        room / booking id), by a stored number column, or by a synthetic "RCPT-12" style number
        (prefix + the row's own id)."""
        if match_ids is None:
            return []
        conds = [col.in_(match_ids)] if match_ids else []
        if number_col is not None:
            conds.append(number_col.ilike(_like(q)))
        up = q.upper()
        for pre in number_prefixes:
            if up.startswith(pre):
                tail = up[len(pre):].strip("- ")
                if tail.isdigit():
                    conds.append((id_col if id_col is not None else col) == int(tail))
        return [or_(*conds)] if conds else [col == -1]   # nothing matched: empty result

    out = []
    bookings, guests = {}, {}

    def _ctx(booking_id):
        if booking_id not in bookings:
            b = db.query(Booking).filter(Booking.booking_id == booking_id).first() if booking_id else None
            bookings[booking_id] = b
            guests[booking_id] = (db.query(Guest).filter(Guest.guest_id == b.guest_id).first()
                                  if b and b.guest_id else None)
        return bookings[booking_id], guests[booking_id]

    # ---- GST invoices (folio)
    if "invoice" in want:
        qq = db.query(Invoice).filter(*_d_window(Invoice.invoice_date))
        qq = qq.filter(*_booking_filter(Invoice.booking_id, Invoice.invoice_no))
        for inv in qq.order_by(Invoice.invoice_date.desc(), Invoice.id.desc()).all():
            b, g = _ctx(inv.booking_id)
            bal = float(inv.balance_due or 0)
            out.append(_row("invoice", inv.invoice_no, inv.invoice_date, b, g, inv.grand_total,
                            "settled" if bal <= 0.005 else f"balance {bal:,.2f}",
                            f"/folio/{inv.folio_id}/invoice/pdf"))

    # ---- payment receipts / refund vouchers
    if "receipt" in want or "refund" in want:
        base = db.query(Payment).filter(Payment.status == "paid", *_dt_window(Payment.created_at))
        base = base.filter(*_booking_filter(Payment.booking_id, None, ("RCPT", "RFND"), Payment.payment_id))
        for p in base.order_by(Payment.created_at.desc()).all():
            b, g = _ctx(p.booking_id)
            if "receipt" in want and float(p.amount or 0) > 0:
                out.append(_row("receipt", f"RCPT-{p.payment_id}", p.created_at, b, g, p.amount,
                                (p.method or p.gateway or "").upper(),
                                f"/payments/{p.payment_id}/receipt/pdf?kind=payment"))
            if "refund" in want and p.refund_status == "completed" and float(p.refund_amount or 0) > 0:
                out.append(_row("refund", f"RFND-{p.payment_id}", p.created_at, b, g, p.refund_amount,
                                (p.refund_mode or "").upper() or "REFUND",
                                f"/payments/{p.payment_id}/receipt/pdf?kind=refund"))

    # ---- registration slips (checked-in / checked-out stays)
    if "slip" in want:
        qq = (db.query(Booking).filter(Booking.status.in_(("checked_in", "checked_out")),
                                       Booking.checked_in_at.isnot(None),
                                       *_dt_window(Booking.checked_in_at)))
        qq = qq.filter(*_booking_filter(Booking.booking_id, None, ("SLIP",)))
        for b in qq.order_by(Booking.checked_in_at.desc()).all():
            _, g = _ctx(b.booking_id)
            out.append(_row("slip", f"SLIP-{b.booking_id}", b.checked_in_at, b, g, None,
                            b.status.replace("_", " "), f"/reception/checkin/{b.booking_id}/slip"))

    # ---- booking confirmations (every live / completed booking)
    if "confirmation" in want:
        qq = db.query(Booking).filter(Booking.status.in_(("confirmed", "checked_in", "checked_out")),
                                      *_dt_window(Booking.created_at))
        qq = qq.filter(*_booking_filter(Booking.booking_id, None, ("BKG",)))
        for b in qq.order_by(Booking.created_at.desc()).all():
            _, g = _ctx(b.booking_id)
            out.append(_row("confirmation", f"BKG-{b.booking_id}", b.created_at, b, g,
                            b.grand_total or b.total_amount, (b.booking_source or "direct"),
                            f"/documents/bookings/{b.booking_id}/confirmation/pdf"))

    # ---- company (consolidated) invoices — matched on number or company name
    if "company_invoice" in want:
        qq = db.query(CompanyInvoice, Company).join(Company, Company.id == CompanyInvoice.company_id)
        qq = qq.filter(*_d_window(CompanyInvoice.invoice_date))
        if q:
            qq = qq.filter(or_(CompanyInvoice.invoice_no.ilike(_like(q)), Company.name.ilike(_like(q))))
        for ci, co in qq.order_by(CompanyInvoice.invoice_date.desc(), CompanyInvoice.id.desc()).all():
            out.append(_row("company_invoice", ci.invoice_no, ci.invoice_date, None, None, ci.grand_total,
                            ci.status or "issued", f"/company-invoices/{ci.id}/pdf",
                            extra={"guest": co.name}))

    # ---- cash-shift reports (closed shifts) — matched on shift no / station / staff name
    if "shift_report" in want:
        qq = (db.query(CashShift, User).outerjoin(User, User.user_id == CashShift.staff_id)
              .filter(CashShift.status == "closed", *_dt_window(CashShift.closed_at)))
        if q:
            conds = [CashShift.station_id.ilike(_like(q)), User.username.ilike(_like(q)),
                     User.full_name.ilike(_like(q))]
            tail = q.upper().replace("SHIFT-", "").replace("SHIFT", "").strip("- #")
            if tail.isdigit():
                conds.append(CashShift.id == int(tail))
            qq = qq.filter(or_(*conds))
        for sh, u in qq.order_by(CashShift.closed_at.desc()).all():
            who = (u.full_name or u.username) if u else None
            out.append(_row("shift_report", f"SHIFT-{sh.id}", sh.closed_at, None, None,
                            sh.collections_cash, f"variance {float(sh.variance or 0):+,.2f}",
                            f"/cash-shift/{sh.id}/report/pdf",
                            room=sh.station_id, extra={"guest": who}))

    # room labels in one query, then newest first
    rooms = _room_labels(db, [r["booking_id"] for r in out if r["booking_id"]])
    for r in out:
        if r["room"] is None and r["booking_id"]:
            r["room"] = rooms.get(r["booking_id"], "—")
    out.sort(key=lambda r: (r["date"] or ""), reverse=True)
    return out


@router.get("", dependencies=[Depends(require_admin)])
@router.get("/", dependencies=[Depends(require_admin)], include_in_schema=False)
def documents(q: str = Query(""), type: str = Query("all"),
              from_: Optional[str] = Query(None, alias="from"), to: Optional[str] = Query(None),
              limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
              db: Session = Depends(get_db)):
    """Search every generated document. `q` matches document number, guest name / phone,
    booking id or room number (company name / station / staff for company invoices and shift
    reports); `from`/`to` bound the document's own date. Newest first, paged."""
    rows = list_documents(db, q, type, _parse(from_, "from"), _parse(to, "to"))
    return {"total": len(rows), "data": rows[offset:offset + limit],
            "types": list(DOC_TYPES)}


@router.get("/bookings/{booking_id}/confirmation/pdf", dependencies=[Depends(require_admin)])
def booking_confirmation_pdf(booking_id: int, db: Session = Depends(get_db)):
    """Regenerate the booking-confirmation PDF the guest was sent (the desk / online flows only
    ever produced it as an attachment at booking time)."""
    from routers.payments import booking_pdf_payload
    from utils.pdf_generator import generate_booking_pdf
    booking = db.query(Booking).filter(Booking.booking_id == booking_id).first()
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")
    pay = (db.query(Payment).filter(Payment.booking_id == booking_id, Payment.status == "paid")
           .order_by(Payment.created_at).first())
    payment_data = ({"payment_id_gateway": pay.payment_id_gateway, "order_id": pay.order_id,
                     "gateway": pay.gateway, "status": "paid"} if pay
                    else {"gateway": "desk", "status": booking.status})
    path = generate_booking_pdf(booking_pdf_payload(booking), payment_data)
    return FileResponse(path, media_type="application/pdf",
                        filename=f"booking-{booking_id}-confirmation.pdf")
