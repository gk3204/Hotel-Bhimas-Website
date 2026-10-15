"""v7e (owner, 13 Oct): every document of one business day, as one ZIP in folders.

"The zip which is auto sent to accounting mail should contain all receipts (advance, refund, food
bill, etc for all payment types, better to have them as separate folders), invoices, reg slip and
also these should be named based on the doc name like RCPT-1". And "in UI I want an option to zip
receipts, invoices, reg slip of 1 for any date and download".

    Receipts/<Cash | UPI | Card | Bank | Online (website) | Other>/RCPT-<n>.pdf
    Refunds/RFND-<n>.pdf
    Prepaid/PRE-<n>.pdf
    Food bills/KOT-<kot no>.pdf           (room-service bills charged to a room)
    Invoices/INV-2026-27-00001.pdf        (tax invoices raised that day)
    Registration slips/REG-<n>.pdf        (rooms that checked in that day; the slip's own number, v7m)

v7o: the same collector serves one STAY (documents_of_booking / build_booking_zip) for the
web "Documents -> By stay" view.
    Expenses.csv                          (petty-cash vouchers)
    index.csv                             (one line per document, with its folder)

Used by the end-of-day accounting e-mail (`receipts_zip`) and GET /reports/documents-zip.
"""
import csv
import io
import logging
import os
import re
import tempfile
import zipfile
from datetime import datetime

from models import Booking, BookingItem, Expense, Folio, GuestRequest, Invoice, Payment, PrepaidReceipt, User
from utils import clock

logger = logging.getLogger(__name__)

PART_BYTES = 8 * 1024 * 1024
_MODE_FOLDERS = {"cash": "Cash", "upi": "UPI", "card": "Card", "bank": "Bank"}


def _safe(name: str) -> str:
    return re.sub(r"[\\/:*?\"<>|]+", "-", str(name)).strip() or "document"


def _user(db, uid):
    if not uid:
        return ""
    u = db.query(User).filter(User.user_id == uid).first()
    return (u.full_name or u.username) if u else ""


def _receipt_folder(p) -> str:
    if (p.gateway or "") in ("razorpay", "phonepe") and not p.collected_by and not getattr(p, "collect_method", None):
        return "Online (website)"
    return _MODE_FOLDERS.get((p.method or "").lower(), "Other")


def documents_of_day(db, day):
    """[(folder, file_name, type, when_local, booking_id, guest, source, mode, amount, reference, by, render)]
    where render() returns a PDF path. Lazy, so a ZIP part only renders what it packs."""
    return _documents(db, day=day)


def documents_of_booking(db, booking_id):
    """v7o (owner): every document of ONE stay - its receipts, refunds, prepaid receipt, food bills,
    invoices (each bill of a per-room stay) and registration slips - the same tuples as a day."""
    return _documents(db, booking_id=booking_id)


def _documents(db, day=None, booking_id=None):
    """Shared by documents_of_day and documents_of_booking, so the two can never list a document
    differently. Exactly one of `day` / `booking_id`."""
    from routers.payments import receipt_payload
    from services.prepaid_receipts import prepaid_receipt_payload, receipt_no
    from utils.pdf_generator import generate_payment_receipt_pdf
    from sqlalchemy import func
    docs = []

    # receipts - every paid payment of the day, by payment type
    def _scope(col_booking, col_date):
        return ([col_booking == booking_id] if booking_id is not None
                else [clock.business_date_sql(col_date) == day])

    for p in (db.query(Payment).filter(Payment.status == "paid", *_scope(Payment.booking_id, Payment.created_at))
              .order_by(Payment.created_at).all()):
        if not float(p.amount or 0):
            continue
        pl = receipt_payload(db, p, "payment")
        docs.append((f"Receipts/{_receipt_folder(p)}", f"RCPT-{p.payment_id}.pdf", "Receipt",
                     clock.to_local(p.created_at), p.booking_id, pl.get("guest_name"), pl.get("source"),
                     (p.method or p.gateway or ""), float(p.amount or 0), p.payment_id_gateway,
                     pl.get("collected_by_name"), (lambda pl=pl: generate_payment_receipt_pdf(pl))))

    # refunds - by the day the refund was paid out
    refunded_on = func.coalesce(Payment.refunded_at, Payment.created_at)
    for p in (db.query(Payment).filter(Payment.refund_status == "completed",
                                       *_scope(Payment.booking_id, refunded_on))
              .order_by(refunded_on).all()):
        if not float(p.refund_amount or 0):
            continue
        pl = receipt_payload(db, p, "refund")
        docs.append(("Refunds", f"RFND-{p.payment_id}.pdf", "Refund voucher",
                     clock.to_local(p.refunded_at or p.created_at), p.booking_id, pl.get("guest_name"),
                     pl.get("source"), (p.refund_mode or p.gateway or ""), float(p.refund_amount or 0),
                     p.refund_reference or p.refund_id, pl.get("refunded_by_name"),
                     (lambda pl=pl: generate_payment_receipt_pdf(pl))))

    # prepaid receipts (OTA / agent / website), issued at check-in
    for pr in (db.query(PrepaidReceipt).filter(*_scope(PrepaidReceipt.booking_id, PrepaidReceipt.issued_at))
               .order_by(PrepaidReceipt.issued_at).all()):
        pl = prepaid_receipt_payload(db, pr)
        docs.append(("Prepaid", f"{receipt_no(pr)}.pdf", "Prepaid receipt", clock.to_local(pr.issued_at),
                     pr.booking_id, pl.get("guest_name"), pl.get("source"), pl.get("method"),
                     float(pr.amount or 0), pr.reference, pl.get("collected_by_name"),
                     (lambda pl=pl: generate_payment_receipt_pdf(pl))))

    # food bills - room-service orders charged to a room that day
    from services import room_service as RS
    for r in (db.query(GuestRequest).filter(GuestRequest.type == "room_service",
                                            GuestRequest.folio_charge_id.isnot(None),
                                            *_scope(GuestRequest.booking_id, GuestRequest.created_at))
              .order_by(GuestRequest.created_at).all()):
        try:
            bp = RS.bill_payload(db, r)
        except Exception as e:                      # one bad order must not lose the rest
            logger.warning(f"documents ZIP: food bill {r.id} skipped: {e}")
            continue
        no = ((r.kot_no if str(r.kot_no).upper().startswith("KOT") else f"KOT-{r.kot_no}")
              if r.kot_no else f"ORDER-{r.id}")
        docs.append(("Food bills", f"{_safe(no)}.pdf", "Food bill", clock.to_local(r.created_at),
                     r.booking_id, bp.get("guest_name"), "", "charged to room", float(bp.get("total") or 0),
                     no, "", (lambda bp=bp, no=no: _food_bill_pdf(bp, no))))

    # tax invoices raised that day
    from routers.folio import _invoice_payload
    from utils.pdf_generator import generate_folio_invoice_pdf
    for inv in (db.query(Invoice).filter(*_scope(Invoice.booking_id, Invoice.created_at))
                .order_by(Invoice.created_at).all()):
        f = db.query(Folio).filter(Folio.id == inv.folio_id).first()
        if f is None:
            continue
        try:
            ip = _invoice_payload(db, f, inv)
        except Exception as e:
            logger.warning(f"documents ZIP: invoice {inv.invoice_no} skipped: {e}")
            continue
        docs.append(("Invoices", f"{_safe(inv.invoice_no)}.pdf", "Tax invoice", clock.to_local(inv.created_at),
                     inv.booking_id, (ip.get("guest") or {}).get("name"), (ip.get("booking") or {}).get("source_label"),
                     "", float(inv.grand_total or 0), inv.invoice_no, ip.get("issued_by_name"),
                     (lambda ip=ip: generate_folio_invoice_pdf(ip))))

    # registration slips - rooms that checked in that day (checked_in_at is the hotel's wall clock)
    from routers.reception import registration_slip_data
    from utils.pdf_generator import generate_registration_slip_pdf
    arrived = {}
    _iq = db.query(BookingItem).filter(BookingItem.checked_in_at.isnot(None), BookingItem.room_id.isnot(None))
    if booking_id is not None:
        # a stay's slips: one per arrival day (a split arrival - two cars on two days - is two slips)
        for it in _iq.filter(BookingItem.booking_id == booking_id).all():
            arrived.setdefault((it.booking_id, it.checked_in_at.date()), []).append(it)
    else:
        for it in (_iq.filter(BookingItem.checked_in_at >= datetime.combine(day, datetime.min.time()),
                              BookingItem.checked_in_at < datetime.combine(day, datetime.max.time()))
                   .all()):
            arrived.setdefault((it.booking_id, day), []).append(it)
    for (bid, _arr_day), items in sorted(arrived.items()):
        b = db.query(Booking).filter(Booking.booking_id == bid).first()
        if b is None or b.status not in ("checked_in", "checked_out"):
            continue
        all_in = [i for i in (b.booking_items or []) if i.checked_in_at and i.room_id]
        part = len(items) < len(all_in)
        ids = ",".join(str(i.room_id) for i in items)
        try:
            sd = registration_slip_data(db, bid, ids if part else None)
        except Exception as e:
            logger.warning(f"documents ZIP: registration slip {bid} skipped: {e}")
            continue
        name = sd.get("slip_no") or f"REG-B{bid}"      # v7m: the slip's own number
        docs.append(("Registration slips", f"{_safe(name)}.pdf", "Registration slip",
                     min(i.checked_in_at for i in items), bid, sd.get("guest_name"), sd.get("source_label"),
                     "", None, ", ".join(sd.get("rooms") or []), "",
                     (lambda sd=sd: generate_registration_slip_pdf(sd))))

    docs.sort(key=lambda d: (d[0], d[3] or datetime.min))
    return docs


def _food_bill_pdf(bp: dict, no: str) -> str:
    from utils.pdf_generator import generate_report_pdf
    rows = [[i.get("name"), i.get("qty"), i.get("unit_price"), i.get("gst_percent") or 0, i.get("line_total")]
            for i in (bp.get("items") or [])]
    totals = ["TOTAL (charged to the room)", "", "", bp.get("gst_total"), bp.get("total")]
    placed = (bp.get("placed_at") or "")[:16].replace("T", " ")
    meta = {"Bill": no, "Room": bp.get("room_number") or "—", "Guest": bp.get("guest_name") or "—",
            "Booking": bp.get("booking_id") or "—", "Ordered": placed}
    return generate_report_pdf(f"Room service bill {no}", ["Item", "Qty", "Rate", "GST %", "Amount"],
                               rows, totals, meta)


def _expenses_csv(db, day) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Voucher", "Date time", "Category", "Description", "Amount", "Receipt ref", "By", "Shift"])
    for e in (db.query(Expense).filter(clock.business_date_sql(Expense.created_at) == day)
              .order_by(Expense.created_at).all()):
        at = clock.to_local(e.created_at)
        w.writerow([f"EXP-{e.id}", at.strftime("%d-%m-%Y %H:%M") if at else "", e.category or "",
                    e.description or "", f"{float(e.amount or 0):.2f}", e.receipt_url or "",
                    _user(db, e.created_by), e.shift_id or ""])
    return buf.getvalue()


def build_booking_zip(db, booking_id) -> list:
    """v7o: every document of one stay as a ZIP in the same folders (no Expenses.csv)."""
    docs = documents_of_booking(db, booking_id)
    if not docs:
        return []
    return _zip_docs(docs, f"stay_{booking_id}", None, PART_BYTES * 4)


def build_documents_zip(db, day, part_bytes=None) -> list:
    """The day's documents as ZIP file(s) in folders; each part carries index.csv and Expenses.csv.
    Returns [(path, filename)] - empty when the day had nothing."""
    limit = part_bytes or PART_BYTES
    docs = documents_of_day(db, day)
    expenses = _expenses_csv(db, day)
    if not docs and expenses.count("\n") <= 1:
        return []
    return _zip_docs(docs, f"documents_{day}", expenses, limit)


def render_document(doc) -> bytes:
    """v7o: one document's PDF bytes (the writers reuse temp file names - read before the next render)."""
    path = doc[-1]()
    with open(path, "rb") as fh:
        return fh.read()


def _zip_docs(docs, base_name, expenses, limit) -> list:
    rendered = []
    for d in docs:
        folder, fname, typ, at, bid, guest, source, mode, amount, ref, by, render = d
        try:
            path = render()
        except Exception as e:                      # one bad document must not lose the day
            logger.error(f"documents ZIP: {fname} failed: {e}")
            continue
        size = os.path.getsize(path) if path and os.path.exists(path) else 0
        # the PDF writers reuse temp file names - keep this one's bytes before the next render
        with open(path, "rb") as fh:
            data = fh.read()
        rendered.append((f"{folder}/{fname}", data, size,
                         [folder, fname.rsplit(".", 1)[0], typ, at.strftime("%d-%m-%Y %H:%M") if at else "",
                          bid or "", guest or "", source or "", mode or "",
                          (f"{float(amount):.2f}" if amount is not None else ""), ref or "", by or ""]))
    head = ["Folder", "Doc no", "Type", "Date time", "Booking", "Guest", "Source", "Mode", "Amount",
            "Reference", "By"]
    parts, cur, size = [], [], 0
    for item in rendered:
        if cur and size + item[2] > limit:
            parts.append(cur)
            cur, size = [], 0
        cur.append(item)
        size += item[2]
    parts.append(cur)
    out = []
    for i, part in enumerate(parts, 1):
        suffix = f"_part{i}of{len(parts)}" if len(parts) > 1 else ""
        name = f"{base_name}{suffix}.zip"
        zpath = os.path.join(tempfile.gettempdir(), name)
        idx = io.StringIO()
        w = csv.writer(idx)
        w.writerow(head)
        for _arc, _data, _sz, row in part:
            w.writerow(row)
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("index.csv", idx.getvalue())
            if expenses is not None:
                z.writestr("Expenses.csv", expenses)
            for arc, data, _sz, _row in part:
                z.writestr(arc, data)
        out.append((zpath, name))
    return out
