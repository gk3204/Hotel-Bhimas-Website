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
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from database import SessionLocal
from models import (Booking, BookingItem, CashShift, Company, CompanyInvoice, Folio, Guest, Invoice,
                    Payment, Room, User)
from utils import ordering
from utils.auth_utils import require_admin, require_admin_or_accountant

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
        # F-01: the document index lists invoices, slips and receipts - all of which print the
        # name the booking was made in, so the list must agree with them.
        "guest": (booking.display_guest_name if booking else None)
                 or (guest.name if guest else None) or (extra or {}).get("guest"),
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
            _fo = db.query(Folio).filter(Folio.id == inv.folio_id).first()
            if _fo is not None and _fo.due_amount is not None and float(_fo.due_amount or 0) > 0:
                bal = max(0.0, float(_fo.balance or 0))       # v7s: a pay-later bill - live due
                _st = "due paid" if bal <= 0.005 else f"DUE {bal:,.2f}"
            else:
                _st = "settled" if bal <= 0.005 else f"balance {bal:,.2f}"
            out.append(_row("invoice", inv.invoice_no, inv.invoice_date, b, g, inv.grand_total,
                            _st,
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
    # v7t: refund vouchers by the day the refund was PAID (a cancellation refund is usually days after
    # the payment) - a cancelled booking's says so
    if "refund" in want:
        _ron = func.coalesce(Payment.refunded_at, Payment.created_at)
        rq = db.query(Payment).filter(Payment.refund_status == "completed", *_dt_window(_ron))
        rq = rq.filter(*_booking_filter(Payment.booking_id, None, ("RCPT", "RFND"), Payment.payment_id))
        for p in rq.order_by(_ron.desc()).all():
            if float(p.refund_amount or 0) <= 0:
                continue
            b, g = _ctx(p.booking_id)
            _st = (p.refund_mode or p.gateway or "").upper() or "REFUND"
            if b is not None and b.status in ("cancelled", "admin_cancelled"):
                _st = f"CANCELLED - {_st}"
            out.append(_row("refund", f"RFND-{p.payment_id}", p.refunded_at or p.created_at, b, g, p.refund_amount,
                            _st, f"/payments/{p.payment_id}/receipt/pdf?kind=refund"))

    # ---- registration slips (checked-in / checked-out stays)
    # v7m/v7o: named by the slip's own number (REG-n) when it has been printed; a stay whose slip has
    # never been printed shows "REG (not printed yet)" and gets its number the first time it is opened.
    if "slip" in want:
        from models import RegistrationSlip
        qq = (db.query(Booking).filter(Booking.status.in_(("checked_in", "checked_out")),
                                       Booking.checked_in_at.isnot(None),
                                       *_dt_window(Booking.checked_in_at)))
        qq = qq.filter(*_booking_filter(Booking.booking_id, None, ("SLIP", "REG")))
        for b in qq.order_by(Booking.checked_in_at.desc()).all():
            _, g = _ctx(b.booking_id)
            regs = (db.query(RegistrationSlip).filter(RegistrationSlip.booking_id == b.booking_id)
                    .order_by(RegistrationSlip.id).all())
            number = ", ".join(f"REG-{r.id}" for r in regs) or "REG (not printed yet)"
            out.append(_row("slip", number, b.checked_in_at, b, g, None,
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


@router.get("", dependencies=[Depends(require_admin_or_accountant)])
@router.get("/", dependencies=[Depends(require_admin_or_accountant)], include_in_schema=False)
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


# =====================================================================================================
# v7o (owner): "room booking with its receipts, invoice everything viewable and downloadable at one place
# for each successful check-in". Bookings is cluttered with cancelled website bookings, so this lists
# ONLY stays that were checked in, each with every document it produced.
# =====================================================================================================

def _stay_doc_counts(db: Session, booking_id: int) -> dict:
    """Cheap counts for the list (no PDF payloads)."""
    from models import GuestRequest, PrepaidReceipt
    pays = db.query(Payment).filter(Payment.booking_id == booking_id).all()
    return {
        "receipts": sum(1 for p in pays if p.status == "paid" and float(p.amount or 0) > 0),
        "refunds": sum(1 for p in pays if p.refund_status == "completed" and float(p.refund_amount or 0) > 0),
        "prepaid": db.query(PrepaidReceipt).filter(PrepaidReceipt.booking_id == booking_id).count(),
        "food_bills": db.query(GuestRequest).filter(GuestRequest.booking_id == booking_id,
                                                    GuestRequest.type == "room_service",
                                                    GuestRequest.folio_charge_id.isnot(None)).count(),
        "invoices": db.query(Invoice).filter(Invoice.booking_id == booking_id).count(),
        "slips": len({it.checked_in_at.date() for it in
                      db.query(BookingItem).filter(BookingItem.booking_id == booking_id,
                                                   BookingItem.checked_in_at.isnot(None),
                                                   BookingItem.room_id.isnot(None)).all()}),
    }


def _checked_in_stay(db: Session, booking_id: int) -> Booking:
    b = db.query(Booking).filter(Booking.booking_id == booking_id).first()
    if not b or b.checked_in_at is None or b.status not in ("checked_in", "checked_out"):
        raise HTTPException(status_code=404, detail="No checked-in stay with that booking number")
    return b


@router.get("/stays", dependencies=[Depends(require_admin_or_accountant)])
def stays(q: str = Query(""), from_: Optional[str] = Query(None, alias="from"), to: Optional[str] = Query(None),
          limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0), db: Session = Depends(get_db)):
    """Every stay that was CHECKED IN (in house or checked out) - never a cancelled / no-show / unpaid
    booking - newest check-in first. `from`/`to` bound the check-in date; `q` matches guest name /
    phone, booking number, room number or the OTA booking id."""
    from utils.booking_source import source_label
    from utils.doc_rooms import booking_rooms_label
    dfrom, dto = _parse(from_, "from"), _parse(to, "to")
    qq = db.query(Booking).filter(Booking.status.in_(("checked_in", "checked_out")),
                                  Booking.checked_in_at.isnot(None))
    if dfrom:
        qq = qq.filter(Booking.checked_in_at >= datetime.combine(dfrom, datetime.min.time()))
    if dto:
        qq = qq.filter(Booking.checked_in_at < datetime.combine(dto + timedelta(days=1), datetime.min.time()))
    q = (q or "").strip()
    if q:
        ids = set(_booking_ids_matching(db, q))
        for (bid,) in db.query(Booking.booking_id).filter(Booking.ota_booking_id.ilike(_like(q))).all():
            ids.add(bid)
        qq = qq.filter(Booking.booking_id.in_(ids or [-1]))
    total = qq.count()
    rows = []
    for b in qq.order_by(Booking.checked_in_at.desc()).offset(offset).limit(limit).all():
        g = db.query(Guest).filter(Guest.guest_id == b.guest_id).first() if b.guest_id else None
        rows.append({
            "booking_id": b.booking_id,
            "guest": b.display_guest_name or (g.name if g else None),
            "phone": g.phone if g else None,
            "rooms": booking_rooms_label(db, b.booking_id),
            "source": source_label(db, b),
            "ota_booking_id": b.ota_booking_id,
            "status": b.status,
            "checked_in_at": b.checked_in_at.isoformat() if b.checked_in_at else None,
            "check_out": str(b.check_out) if b.check_out else None,
            "grand_total": float(b.grand_total or 0),
            "documents": _stay_doc_counts(db, b.booking_id),
        })
    return {"total": total, "data": rows}


@router.get("/stays/{booking_id}", dependencies=[Depends(require_admin_or_accountant)])
def stay_documents(booking_id: int, db: Session = Depends(get_db)):
    """The stay's documents (no PDFs rendered here - each opens through .../file)."""
    from services.day_documents import documents_of_booking
    b = _checked_in_stay(db, booking_id)
    docs = []
    for folder, fname, typ, at, bid, guest, source, mode, amount, ref, by, _render in documents_of_booking(db, booking_id):
        docs.append({"key": f"{folder}/{fname}", "folder": folder, "number": fname.rsplit(".", 1)[0],
                     "type": typ, "date": at.isoformat() if at else None,
                     "amount": round(float(amount), 2) if amount is not None else None,
                     "mode": mode or "", "reference": ref or "", "by": by or ""})
    order = ["Registration slips", "Prepaid", "Receipts", "Food bills", "Refunds", "Invoices"]
    docs.sort(key=lambda d: (next((i for i, f in enumerate(order) if d["folder"].startswith(f)), 99),
                             d["date"] or ""))
    return {"booking_id": b.booking_id, "guest": b.display_guest_name, "status": b.status, "documents": docs}


@router.get("/stays/{booking_id}/file", dependencies=[Depends(require_admin_or_accountant)])
def stay_document_file(booking_id: int, key: str = Query(...), download: int = Query(0),
                       db: Session = Depends(get_db)):
    """One document of the stay as a PDF - inline to view, `download=1` to save."""
    from fastapi.responses import Response
    from services.day_documents import documents_of_booking, render_document
    _checked_in_stay(db, booking_id)
    doc = next((d for d in documents_of_booking(db, booking_id) if f"{d[0]}/{d[1]}" == key), None)
    if doc is None:
        raise HTTPException(status_code=404, detail="That document is not part of this stay")
    data = render_document(doc)
    disp = "attachment" if download else "inline"
    return Response(content=data, media_type="application/pdf",
                    headers={"Content-Disposition": f'{disp}; filename="{doc[1]}"'})


@router.get("/stays/{booking_id}/zip", dependencies=[Depends(require_admin_or_accountant)])
def stay_documents_zip(booking_id: int, db: Session = Depends(get_db)):
    """Every document of the stay in one ZIP, in the same folders as the day's ZIP."""
    from services.day_documents import build_booking_zip
    _checked_in_stay(db, booking_id)
    parts = build_booking_zip(db, booking_id)
    if not parts:
        raise HTTPException(status_code=404, detail="This stay has no documents yet")
    path, name = parts[0]
    return FileResponse(path, media_type="application/zip", filename=name)


@router.get("/bookings/{booking_id}/confirmation/pdf", dependencies=[Depends(require_admin_or_accountant)])
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
