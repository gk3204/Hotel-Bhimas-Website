"""Go-MMT monthly TDS / TCS / commission statements (P1 batch 3, owner 4-5 Nov 2026).

feedback@zen2-makemytrip.com sends, as .xlsx attachments, one booking row per line:

  "MakeMyTrip (India) Limited-TDS statement for Bookings in, JULY,2026 at Hotel Bhimas Tirupati"
        TDS_REPORT.xlsx - Total TDS (0.1%, section 194-O) is NEGATIVE when deducted and a cancelled
        booking adds the same amount back as a positive row
  "MakeMyTrip - TCS statement for Bookings in AUGUST 2026 at Hotel Bhimas, Tirupati"
        HTLDOM014889_TCS-1.xlsx - Total TCS (0.5%, GST TCS) is POSITIVE when collected
  "Commission Income for the month of Oct 2026"
        <invoice>_Report.xlsx + <invoice>_Invoice.pdf - commission + IGST per booking
  "MMTINDIA-Monthly Commission Statement AUGUST,2026 at K R G HOTELS PRIVATE LTD null"
        _Report.xlsx + _Credit Memo.pdf - commission given back for cancellations (negative)

Each mail -> one OtaTaxStatement (files kept, for the CA) + one OtaTaxStatementLine per row of THIS
hotel's account (the Go-MMT account map in settings; Bhimas Paradise rows are counted, not stored).
TDS and TCS are stored as positive "deducted" amounts so the two read the same way.

The statements do not arrive every month (April's TDS and February's TCS never came), so nothing
here is required for a booking's payment state. They are a cross-check: per booking, the statement's
TDS/TCS against the voucher's (ota_draft_bookings.tcs_amount / tds_amount), and per month, which
statements are still missing. The authoritative sources for filing remain Form 26AS (TDS) and the
GST portal's TCS credit (GSTR-2B).
"""
import email
import email.utils
import logging
import re
from datetime import date, datetime, timedelta

from models import Booking, OtaDraftBooking, OtaTaxStatement, OtaTaxStatementLine
from utils import clock
from utils.settings import get_ota_payment_config
from utils.xlsx_reader import read_dicts

logger = logging.getLogger(__name__)

STATEMENT_SENDER = "feedback@zen2-makemytrip.com"     # IMAP FROM search for the history import
SENDER_DOMAINS = ("zen2-makemytrip.com", "makemytrip.com", "go-mmt.com")
KINDS = ("tds", "tcs", "commission", "credit_memo")
KIND_LABEL = {"tds": "TDS statement", "tcs": "TCS statement", "commission": "Commission invoice",
              "credit_memo": "Commission credit memo"}
DUE_DAY = 15            # a month's statement is "missing" from the 15th of the following month
_TOL = 1.0              # ₹ - statement vs voucher, per booking

_MONTHS = {m: i for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), start=1)}
_SUBJECTS = (
    ("tds", re.compile(r"\bTDS\s+statement\s+for\s+bookings\s+in\s*,?\s*([A-Za-z]+)\s*,?\s*(\d{4})", re.I)),
    ("tcs", re.compile(r"\bTCS\s+statement\s+for\s+bookings\s+in\s*,?\s*([A-Za-z]+)\s*,?\s*(\d{4})", re.I)),
    ("commission", re.compile(r"\bcommission\s+income\s+for\s+the\s+month\s+of\s+([A-Za-z]+)\s*,?\s*(\d{4})", re.I)),
    ("credit_memo", re.compile(r"\bmonthly\s+commission\s+statement\s+([A-Za-z]+)\s*,?\s*(\d{4})", re.I)),
)


def _norm(s) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", str(s or "").lower())).strip()


# Column names per statement, normalised (see _norm). `sign` turns the file's figure into ours.
_SPEC = {
    "tds": {"id": "booking no", "pnr": "pnr no", "account": "vendor code", "guest": "guest name",
            "ci": "chk in date", "co": "chk out date", "booked": "booking date",
            "base": "tds base amount", "amount": "total tds", "sign": -1},
    "tcs": {"id": "booking no", "pnr": "pnr", "account": "mmt vendor code", "guest": "guest name",
            "ci": "chk in date", "co": "chk out date", "booked": "booking date", "brand": "brand",
            "base": "tcs base amount", "amount": "total tcs", "sign": 1},
    "commission": {"id": "booking id", "pnr": "pnr", "account": "vendor no", "guest": "guest name",
                   "stay": "checkin checkout date", "brand": "company code", "doc": "commission invoice no",
                   "base": "commission", "gst": ("cgst sgst", "igst"), "amount": "total invoice value", "sign": 1},
    "credit_memo": {"id": "booking id", "pnr": "pnr", "account": "vendor no", "guest": "guest name",
                    "stay": "checkin checkout date", "brand": "company code", "doc": "credit memo no",
                    "base": "credit memo", "gst": ("cgst sgst", "igst"), "amount": "total invoice value", "sign": 1},
}


# ---------------------------------------------------------------------------
# Parsing (pure)
# ---------------------------------------------------------------------------
def detect(from_addr: str, subject: str):
    """(kind, period) for a Go-MMT statement mail, else None."""
    addr = email.utils.parseaddr(from_addr or "")[1].lower()
    if not any(addr.endswith("@" + d) or addr.endswith("." + d) for d in SENDER_DOMAINS):
        return None
    subj = re.sub(r"\s+", " ", subject or "")
    for kind, rx in _SUBJECTS:
        m = rx.search(subj)
        if m:
            mon = _MONTHS.get(m.group(1)[:3].lower())
            if mon:
                return kind, date(int(m.group(2)), mon, 1)
    return None


def _num(v):
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return round(float(v), 2)
    s = re.sub(r"[^\d.\-]", "", str(v))
    try:
        return round(float(s), 2) if s not in ("", "-", ".") else None
    except ValueError:
        return None


def _day(v):
    s = str(v or "").strip()
    for fmt in ("%d/%m/%Y", "%d/%b/%Y", "%d-%b-%Y", "%d-%m-%Y", "%Y-%m-%d", "%d %b %Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _txt(v):
    s = re.sub(r"\s+", " ", str(v if v is not None else "")).strip()
    return s or None


def parse_rows(kind: str, data: bytes) -> list[dict]:
    """Booking rows of one statement file, normalised. PURE. Rows without a booking id are skipped."""
    spec = _SPEC[kind]
    out = []
    for raw in read_dicts(data):
        r = {_norm(k): v for k, v in raw.items()}
        bid = re.sub(r"\s+", "", str(r.get(spec["id"]) or "")).upper()
        if not bid:
            continue
        amount = _num(r.get(spec["amount"]))
        if amount is None:
            continue
        ci = co = None
        if "stay" in spec:
            parts = str(r.get(spec["stay"]) or "").split()
            ci = _day(parts[0]) if parts else None
            co = _day(parts[1]) if len(parts) > 1 else None
        else:
            ci, co = _day(r.get(spec["ci"])), _day(r.get(spec["co"]))
        gst = None
        if "gst" in spec:
            gst = round(sum(_num(r.get(k)) or 0 for k in spec["gst"]), 2)
        base = _num(r.get(spec["base"]))
        out.append({
            "ota_booking_id": bid,
            "pnr": (_txt(r.get(spec["pnr"])) or "").replace(" ", "") or None,
            "account": (_txt(r.get(spec["account"])) or "").upper() or None,
            "guest_name": _txt(r.get(spec["guest"])),
            "booking_date": _day(r.get(spec["booked"])) if "booked" in spec else None,
            "check_in": ci, "check_out": co,
            "brand": (_txt(r.get(spec["brand"])) or "").upper() or None if "brand" in spec else None,
            "doc_no": _txt(r.get(spec["doc"])) if "doc" in spec else None,
            "base_amount": round(base * spec["sign"], 2) if base is not None else None,
            "gst_amount": gst,
            "amount": round(amount * spec["sign"], 2),
        })
    return out


def _attachments(msg):
    """(xlsx [(name, bytes)], pdf [(name, bytes)]) - Go-MMT labels the .xlsx application/zip."""
    xlsx, pdf = [], []
    for part in msg.walk():
        if part.is_multipart():
            continue
        name = part.get_filename() or ""
        data = part.get_payload(decode=True) or b""
        low = name.lower()
        if low.endswith(".xlsx") or (data[:2] == b"PK" and part.get_content_type() in (
                "application/zip", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")):
            xlsx.append((name.split("/")[-1] or "statement.xlsx", data))
        elif low.endswith(".pdf") or part.get_content_type() == "application/pdf":
            pdf.append((name.split("/")[-1] or "document.pdf", data))
    return xlsx, pdf


def _decode(value) -> str:
    from email.header import decode_header, make_header
    try:
        return str(make_header(decode_header(value))) if value else ""
    except Exception:
        return str(value or "")


# ---------------------------------------------------------------------------
# Intake
# ---------------------------------------------------------------------------
def ingest_statement_email(db, raw: bytes, *, source="mail", commit=False) -> dict | None:
    """Store one statement mail. None when the mail is not a Go-MMT statement. Never raises."""
    msg = email.message_from_bytes(raw)
    subject = _decode(msg.get("Subject"))
    found = detect(_decode(msg.get("From")), subject)
    if found is None:
        return None
    kind, period = found
    base = {"statement": True, "kind": kind, "period": str(period)}
    try:
        return _store(db, msg, kind, period, subject, source, base, commit)
    except Exception as e:     # an unreadable attachment must not stop the poll
        logger.exception("OTA statement: could not read %r", subject)
        return {**base, "stored": False, "reason": f"unreadable ({e})"}


def _store(db, msg, kind, period, subject, source, base, commit):
    message_id = (msg.get("Message-ID") or msg.get("Message-Id") or "").strip() or None
    if message_id and db.query(OtaTaxStatement).filter(OtaTaxStatement.message_id == message_id).first():
        return {**base, "stored": False, "reason": "already recorded"}
    xlsx, pdf = _attachments(msg)
    if not xlsx:
        return {**base, "stored": False, "reason": "no spreadsheet attached"}
    name, data = xlsx[0]
    rows = parse_rows(kind, data)
    mine = {k for k, role in get_ota_payment_config(db)["accounts"].items() if role == "this"}
    keep = [r for r in rows if not r["account"] or r["account"] in mine]
    other = len(rows) - len(keep)
    if rows and not keep:
        return {**base, "stored": False, "reason": "other property", "rows": len(rows)}
    doc_no = next((r["doc_no"] for r in keep if r["doc_no"]), None)
    if kind in ("commission", "credit_memo") and doc_no and db.query(OtaTaxStatement).filter(
            OtaTaxStatement.kind == kind, OtaTaxStatement.doc_no == doc_no).first():
        return {**base, "stored": False, "reason": "already recorded", "doc_no": doc_no}
    if kind in ("tds", "tcs"):
        # a re-sent statement for the same month replaces the earlier one (both kept, the old one marked)
        for old in db.query(OtaTaxStatement).filter(OtaTaxStatement.kind == kind, OtaTaxStatement.period == period,
                                                    OtaTaxStatement.superseded.is_(False)).all():
            old.superseded = True
    try:
        mail_date = email.utils.parsedate_to_datetime(msg.get("Date")).astimezone(clock.UTC).replace(tzinfo=None) \
            if msg.get("Date") else None
    except (TypeError, ValueError, AttributeError):
        mail_date = None
    s = OtaTaxStatement(
        kind=kind, period=period, ota_account_code=next(iter(mine), None) if len(mine) == 1 else None,
        message_id=message_id, subject=subject[:300], mail_date=mail_date, source=source,
        file_name=name[:200], file_data=data,
        pdf_name=pdf[0][0][:200] if pdf else None, pdf_data=pdf[0][1] if pdf else None,
        doc_no=doc_no, row_count=len(keep), other_rows=other,
        total_base=round(sum(r["base_amount"] or 0 for r in keep), 2),
        total_gst=round(sum(r["gst_amount"] or 0 for r in keep), 2) if kind in ("commission", "credit_memo") else None,
        total_amount=round(sum(r["amount"] for r in keep), 2))
    db.add(s)
    db.flush()
    ids = {r["ota_booking_id"] for r in keep}
    booking_of = {}
    if ids:
        for b in db.query(Booking).filter(Booking.ota_booking_id.in_(ids)).all():
            booking_of.setdefault(b.ota_booking_id, b.booking_id)
    for r in keep:
        db.add(OtaTaxStatementLine(
            statement_id=s.id, booking_id=booking_of.get(r["ota_booking_id"]),
            **{k: r[k] for k in ("ota_booking_id", "pnr", "guest_name", "booking_date", "check_in", "check_out",
                                 "brand", "doc_no", "base_amount", "gst_amount", "amount")}))
    db.flush()
    if commit:
        db.commit()
    return {**base, "stored": True, "statement_id": s.id, "rows": len(keep), "other_rows": other,
            "total": float(s.total_amount or 0)}


# ---------------------------------------------------------------------------
# Month report
# ---------------------------------------------------------------------------
def _month_add(d: date, n: int) -> date:
    y, m = divmod(d.month - 1 + n, 12)
    return date(d.year + y, m + 1, 1)


def due_date(period: date) -> date:
    return _month_add(period, 1).replace(day=DUE_DAY)


def _first_month(db):
    """The first month statements are expected for: the month of the first Go-MMT stay the PMS
    tracks (or the tracking-start setting), else the oldest statement held."""
    from services.ota_payments import tracking_start
    from sqlalchemy import func
    candidates = [d for d in (tracking_start(db),
                              db.query(func.min(OtaTaxStatement.period)).scalar()) if d]
    return min(candidates).replace(day=1) if candidates else None


def _voucher_taxes(db, ota_ids) -> dict:
    """{ota_booking_id: (tcs, tds)} from the newest confirmation draft of each booking."""
    out = {}
    if not ota_ids:
        return out
    for d in (db.query(OtaDraftBooking)
              .filter(OtaDraftBooking.ota_booking_id.in_(ota_ids), OtaDraftBooking.kind == "confirmation")
              .order_by(OtaDraftBooking.id).all()):
        out[d.ota_booking_id] = (float(d.tcs_amount) if d.tcs_amount is not None else None,
                                 float(d.tds_amount) if d.tds_amount is not None else None)
    return out


def _current(db, periods):
    """Statements in force for the periods: the latest TDS/TCS per month, every commission doc."""
    stmts = (db.query(OtaTaxStatement)
             .filter(OtaTaxStatement.period.in_(periods), OtaTaxStatement.superseded.is_(False))
             .order_by(OtaTaxStatement.id).all())
    by = {}
    for s in stmts:
        by.setdefault((s.period, s.kind), []).append(s)
    return by


def _compare(kind, net, voucher, booking_status):
    """Per booking: what the voucher says should have been deducted vs the statement's net."""
    if booking_status == "cancelled":
        expected = 0.0
    else:
        expected = voucher
    if expected is None:
        return expected, "no_voucher"
    return expected, ("ok" if abs(net - expected) <= _TOL else "differs")


def month_detail(db, period: date) -> dict:
    """One month, booking by booking: TDS and TCS (statement vs voucher), commission, credit memo."""
    by = _current(db, [period])
    stmts = [s for k in KINDS for s in by.get((period, k), [])]
    lines = (db.query(OtaTaxStatementLine)
             .filter(OtaTaxStatementLine.statement_id.in_([s.id for s in stmts])).all()) if stmts else []
    kind_of = {s.id: s.kind for s in stmts}
    rows = {}
    for x in lines:
        r = rows.setdefault(x.ota_booking_id, {
            "ota_booking_id": x.ota_booking_id, "guest_name": None, "check_in": None, "check_out": None,
            "booking_id": None, "tds": 0.0, "tcs": 0.0, "commission": 0.0, "commission_gst": 0.0,
            "credit_memo": 0.0, "in": set()})
        k = kind_of[x.statement_id]
        r["in"].add(k)
        r["guest_name"] = r["guest_name"] or x.guest_name
        r["check_in"] = r["check_in"] or (str(x.check_in) if x.check_in else None)
        r["check_out"] = r["check_out"] or (str(x.check_out) if x.check_out else None)
        r["booking_id"] = r["booking_id"] or x.booking_id
        if k in ("tds", "tcs"):
            r[k] = round(r[k] + float(x.amount), 2)
        elif k == "commission":
            r["commission"] = round(r["commission"] + float(x.amount), 2)
            r["commission_gst"] = round(r["commission_gst"] + float(x.gst_amount or 0), 2)
        else:
            r["credit_memo"] = round(r["credit_memo"] + float(x.amount), 2)
    vouchers = _voucher_taxes(db, list(rows))
    status = {}
    if rows:
        for b in db.query(Booking).filter(Booking.ota_booking_id.in_(list(rows))).all():
            status.setdefault(b.ota_booking_id, (b.booking_id, b.status))
    out = []
    for oid, r in sorted(rows.items(), key=lambda kv: (kv[1]["check_in"] or "", kv[0])):
        bid, bstatus = status.get(oid, (r["booking_id"], None))
        tcs_v, tds_v = vouchers.get(oid, (None, None))
        flags = []
        row = {**{k: v for k, v in r.items() if k != "in"}, "booking_id": bid, "booking_status": bstatus}
        for k, v in (("tds", tds_v), ("tcs", tcs_v)):
            if k not in r["in"]:
                row[f"{k}_voucher"], row[f"{k}_check"] = v, "not_in_statement"
                continue
            row[f"{k}_voucher"], row[f"{k}_check"] = _compare(k, r[k], v, bstatus)
            if row[f"{k}_check"] == "differs":
                flags.append(f"{k}_differs")
        if bid is None:
            flags.append("not_in_pms")
        row["flags"] = flags
        out.append(row)
    return {"period": str(period), "rows": out,
            "statements": [serialize_statement(s) for s in stmts]}


def month_report(db, months=12, today=None) -> dict:
    """One row per month, newest first: each statement's total, the voucher-based figure for the same
    bookings, how many bookings differ, and which statements are still missing."""
    today = today or clock.business_today()
    first = _first_month(db)
    if first is None:
        return {"months": [], "today": str(today)}
    this_month = today.replace(day=1)
    periods = [p for p in (_month_add(this_month, -i) for i in range(months)) if p >= first]
    by = _current(db, periods)
    out = []
    for p in periods:
        detail = month_detail(db, p) if any((p, k) in by for k in KINDS) else {"rows": [], "statements": []}
        rows = detail["rows"]
        due = today >= due_date(p)
        row = {"period": str(p), "label": p.strftime("%b %Y"), "due_on": str(due_date(p)), "due": due,
               "missing": [], "files": detail["statements"]}
        for k in ("tds", "tcs"):
            has = (p, k) in by
            in_k = [r for r in rows if r[f"{k}_check"] != "not_in_statement"]
            known = [r for r in in_k if r[f"{k}_check"] != "no_voucher"]
            row[k] = {
                "received": has,
                "statement": round(sum(r[k] for r in in_k), 2) if has else None,
                # the same bookings, as their vouchers state it - only where a voucher figure exists
                "statement_with_voucher": round(sum(r[k] for r in known), 2) if known else None,
                "voucher": round(sum(r[f"{k}_voucher"] or 0 for r in known), 2) if known else None,
                "with_voucher": len(known),
                "bookings": len(in_k),
                "differs": sum(1 for r in in_k if r[f"{k}_check"] == "differs"),
                "no_voucher": sum(1 for r in in_k if r[f"{k}_check"] == "no_voucher"),
            }
            if due and not has:
                row["missing"].append(k)
        com = by.get((p, "commission"), [])
        crm = by.get((p, "credit_memo"), [])
        row["commission"] = {
            "received": bool(com),
            "invoices": [s.doc_no for s in com if s.doc_no],
            "commission": round(sum(float(s.total_base or 0) for s in com), 2),
            "gst": round(sum(float(s.total_gst or 0) for s in com), 2),
            "total": round(sum(float(s.total_amount or 0) for s in com), 2),
            "credit_memos": round(sum(float(s.total_amount or 0) for s in crm), 2),
        }
        if due and not com:
            row["missing"].append("commission")
        out.append(row)
    return {"months": out, "today": str(today)}


def serialize_statement(s: OtaTaxStatement) -> dict:
    return {
        "id": s.id, "kind": s.kind, "label": KIND_LABEL.get(s.kind, s.kind), "period": str(s.period),
        "doc_no": s.doc_no, "rows": s.row_count, "other_rows": s.other_rows,
        "total_base": float(s.total_base) if s.total_base is not None else None,
        "total_gst": float(s.total_gst) if s.total_gst is not None else None,
        "total_amount": float(s.total_amount) if s.total_amount is not None else None,
        "file_name": s.file_name, "pdf_name": s.pdf_name, "source": s.source,
        "mail_date": s.mail_date.isoformat() if s.mail_date else None, "superseded": s.superseded,
    }


# ---------------------------------------------------------------------------
# Alerts (called from ota_payments.detect_alerts)
# ---------------------------------------------------------------------------
ALERT_MISSING = "ota_statement_missing"     # low - a month's TDS / TCS / commission statement never came


def detect_alerts(db, make_alert, today=None, months=6) -> set:
    """One low alert per missing statement in the last `months` months; returns the live dedupe keys
    so the caller can close the ones that have since arrived."""
    live = set()
    for m in month_report(db, months=months, today=today)["months"]:
        for k in m["missing"]:
            key = f"{ALERT_MISSING}:{k}:{m['period']}"
            live.add(key)
            make_alert(type=ALERT_MISSING, severity="low", dedupe_key=key,
                       detail={"kind": k, "period": m["period"],
                               "note": f"Go-MMT's {KIND_LABEL[k]} for {m['label']} has not arrived - ask your "
                                       f"Go-MMT account manager (OTA Payments -> TDS / TCS)"})
    return live
