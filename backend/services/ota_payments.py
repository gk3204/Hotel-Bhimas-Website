"""Go-MMT payment tracking (P1, owner 4-5 Nov 2026) - which Goibibo / MakeMyTrip booking was paid in
which bank transfer, and whether that money reached the bank.

Go-MMT sends "MMT Payment(Wire) Confirmation For HTLDOM014889 And Payment Date : 2026-10-12" from
hotelpayments1@makemytrip.com for every transfer. The body names the transfer (bank, account, total,
date, "Transaction No" = the UTR) and lists EVERY booking it covers: Booking ID (NH... MakeMyTrip /
GH... Goibibo, the same id the voucher parser stores in bookings.ota_booking_id), PNR, guest, hotel,
stay, original cost, payable, booking type, brand. One mail -> one OtaSettlement (source='mail') and
one OtaSettlementLine per booking.

A booking's payment state is computed here and never stored (the one-implementation rule - see
utils/clock.py, utils/gst.py, services/folio_resolver.py):

    pending   no payment line yet          (overdue once N days past checkout)
    done      a payment lists it           (the OTA says it paid)
    received  that payment was confirmed   (an admin saw the credit in the bank - batch 2)

Two properties share the mailbox. HTLDOM014889 is Hotel Bhimas; HTLDOM014793 is Bhimas Paradise and
its payments are never stored (settings.get_ota_payment_config). Design and the samples it was built
on: HotelLock/docs/ota-payment-tracking-plan.md, HotelLock/qa-harness/ota/payments/samples/.

Like the rest of the OTA intake, nothing here raises into the poller: a payment mail that cannot be
read is reported back, never half-saved.
"""
import email
import email.utils
import logging
import re
from datetime import date, datetime, timedelta
from html.parser import HTMLParser

from models import Booking, OtaDraftBooking, OtaSettlement, OtaSettlementLine
from utils import clock
from utils.settings import get_ota_payment_config

logger = logging.getLogger(__name__)

PAYMENT_SENDER = "hotelpayments1@makemytrip.com"
_SUBJECT_RE = re.compile(r"payment\s*\(\s*wire\s*\)\s*confirmation\s+for\s+([A-Z]{3,}\d+)", re.IGNORECASE)
_SUBJECT_DATE_RE = re.compile(r"payment\s+date\s*:\s*(\d{4}-\d{2}-\d{2})", re.IGNORECASE)
_BOOKING_ID_RE = re.compile(r"^[A-Z]{2}\d{10,}$")

# Brand column -> the PMS channel code. A payment mixes both brands (one Go-MMT account).
_BRAND_CHANNEL = {"MMTINDIA": "makemytrip", "MAKEMYTRIP": "makemytrip", "GOIBIBO": "goibibo"}
GO_MMT_CHANNELS = ("makemytrip", "goibibo")
# P2a: every channel whose payments are tracked here. Yatra pays through YES Bank, one booking per advice.
TRACKED_CHANNELS = GO_MMT_CHANNELS + ("yatra",)
CHANNEL_LABEL = {"makemytrip": "MakeMyTrip", "goibibo": "Goibibo", "yatra": "Yatra"}
# Overdue counts from check-out for Go-MMT (it pays after the stay starts) but from CHECK-IN for Yatra,
# which pays the day before arrival (owner, 5 Nov: "Yatra - check-in + 3 days").
OVERDUE_FROM_CHECK_IN = ("yatra",)

# Yatra's payment advice: a YES Bank net-banking alert with a PDF (owner's samples, Oct-Nov 2026).
YES_BANK_SENDER = "Corporate.NetBankingAlerts@YES.BANK.IN"
YATRA_PAYER = "yatra online limited"


def channels_of(channel=None) -> tuple:
    """The channel filter of the screens: None / "all" = every tracked channel, else just that one."""
    if not channel or channel == "all":
        return TRACKED_CHANNELS
    if channel not in TRACKED_CHANNELS:
        raise ValueError(f"Unknown channel: {channel}")
    return (channel,)
# The header cells of the two tables, normalised (lower-case, single spaces, no punctuation).
_XFER_COLS = {"bank name": "bank_name", "beneficiary name": "beneficiary",
              "account number": "bank_account", "total amount transferred rs": "total",
              "transaction date": "txn_date", "transaction no": "utr"}
_ROW_COLS = {"booking id": "ota_booking_id", "booking pnr": "pnr", "client name": "guest_name",
             "hotel name": "hotel_name", "hotel city": "city", "check in": "check_in",
             "check out": "check_out", "original cost": "original_cost", "payable": "amount_paid",
             "booking type": "booking_type", "brand": "brand"}
# Booking statuses MMT can owe money on. A prepaid no-show is still paid; a cancellation is not.
_PAYABLE_STATUSES = ("confirmed", "checked_in", "checked_out", "no_show")
# Lines that count as money paid against a booking.
_LINKED = ("matched", "amount_diff", "cancelled_booking")


# ---------------------------------------------------------------------------
# Parsing (pure - no DB)
# ---------------------------------------------------------------------------
class _Rows(HTMLParser):
    """Every innermost <tr> as the list of its innermost <td> texts, empty cells kept - so a blank
    guest name can never shift the payable into the wrong column. Outer layout cells that only
    wrap other tables come out as empty strings and are harmless."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows, self._tr, self._td = [], [], []

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._tr.append([])
        elif tag in ("td", "th"):
            self._td.append([])
        elif tag == "br" and self._td:
            self._td[-1].append(" ")

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._td:
            cell = re.sub(r"\s+", " ", "".join(self._td.pop()).replace("\xa0", " ")).strip()
            if self._tr:
                self._tr[-1].append(cell)
        elif tag == "tr" and self._tr:
            row = self._tr.pop()
            if any(row):
                self.rows.append(row)

    def handle_data(self, data):
        if self._td:
            self._td[-1].append(data)


def _norm(s) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", str(s or "").lower())).strip()


def _money(s):
    s = re.sub(r"[^\d.\-]", "", str(s or ""))
    try:
        return round(float(s), 2) if s not in ("", "-", ".") else None
    except ValueError:
        return None


def _dmy(s):
    try:
        return datetime.strptime(str(s or "").strip(), "%d/%m/%Y").date()
    except ValueError:
        return None


def _header_map(row, cols) -> dict:
    """{field: column index} when `row` is the header row of a table described by `cols`."""
    found = {}
    for i, cell in enumerate(row):
        key = cols.get(_norm(cell))
        if key and key not in found:
            found[key] = i
    return found


def _html_of(msg) -> str:
    for part in (msg.walk() if msg.is_multipart() else [msg]):
        if part.get_content_type() == "text/html" and "attachment" not in str(part.get("Content-Disposition") or "").lower():
            raw = part.get_payload(decode=True) or b""
            return raw.decode(part.get_content_charset() or "utf-8", errors="replace")
    return ""


def is_payment_mail(from_addr: str, subject: str) -> bool:
    """A Go-MMT bank-transfer advice. Both sender and subject must agree, so a forwarded copy or a
    guest writing "payment confirmation" never becomes money."""
    return PAYMENT_SENDER in (from_addr or "").lower() and bool(_SUBJECT_RE.search(subject or ""))


def parse_payment_html(html: str) -> dict:
    """The transfer block and the booking rows of one payment mail body. PURE.

    Returns {bank_name, beneficiary, bank_account, total, txn_date, utr, mail_total, lines: [...]}
    with money as floats and dates as `date`. Missing pieces are simply absent; the caller decides."""
    p = _Rows()
    p.feed(html or "")
    rows = p.rows
    out = {"lines": []}
    for i, row in enumerate(rows):
        hx = _header_map(row, _XFER_COLS)
        if "utr" in hx and "total" in hx and i + 1 < len(rows):
            vals = rows[i + 1]
            for key, idx in hx.items():
                if idx < len(vals):
                    out[key] = vals[idx]
            break
    for key in ("bank_name", "beneficiary", "utr"):
        if out.get(key):
            out[key] = re.sub(r"\s+", " ", out[key]).strip()
    if out.get("bank_account"):
        out["bank_account"] = re.sub(r"\D", "", out["bank_account"])
    out["total"] = _money(out.get("total"))
    out["txn_date"] = _dmy(out.get("txn_date"))

    header = None
    for row in rows:
        hm = _header_map(row, _ROW_COLS)
        if header is None:
            if "ota_booking_id" in hm and "amount_paid" in hm:
                header = hm
            continue
        bid_idx = header["ota_booking_id"]
        bid = row[bid_idx].replace(" ", "").upper() if bid_idx < len(row) else ""
        if not _BOOKING_ID_RE.match(bid):
            m = re.search(r"total\s*:\s*([\d,]+(?:\.\d+)?)", " ".join(row), re.IGNORECASE)
            if m:
                out["mail_total"] = _money(m.group(1))
            continue
        cell = lambda k: row[header[k]] if k in header and header[k] < len(row) else None  # noqa: E731
        amount = _money(cell("amount_paid"))
        if amount is None:
            continue
        brand = (cell("brand") or "").strip().upper()
        out["lines"].append({
            "ota_booking_id": bid,
            "pnr": (cell("pnr") or "").replace(" ", "") or None,
            "guest_name": (cell("guest_name") or "").strip() or None,
            "hotel_name": (cell("hotel_name") or "").strip() or None,
            "check_in": _dmy(cell("check_in")),
            "check_out": _dmy(cell("check_out")),
            "original_cost": _money(cell("original_cost")),
            "amount_paid": amount,
            "booking_type": (cell("booking_type") or "").strip() or None,
            "brand": brand or None,
            "channel_code": _BRAND_CHANNEL.get(brand, "makemytrip"),
        })
    return out


def _decode(value) -> str:
    from email.header import decode_header, make_header
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return str(value)


def parse_payment_email(raw: bytes) -> dict | None:
    """A whole payment mail -> dict, or None when it is not a Go-MMT payment advice. PURE."""
    msg = email.message_from_bytes(raw)
    from_addr, subject = _decode(msg.get("From")), _decode(msg.get("Subject"))
    if not is_payment_mail(from_addr, subject):
        return None
    out = parse_payment_html(_html_of(msg))
    out["account_code"] = _SUBJECT_RE.search(subject).group(1).upper()
    m = _SUBJECT_DATE_RE.search(subject)
    try:
        out["subject_date"] = datetime.strptime(m.group(1), "%Y-%m-%d").date() if m else None
    except ValueError:
        out["subject_date"] = None
    out["message_id"] = (msg.get("Message-ID") or msg.get("Message-Id") or "").strip() or None
    out["subject"] = subject
    return out


# --- Yatra: YES Bank "BENEFICIARY PAYMENT ADVICE" (P2a) -------------------------------------------
# The mail body only says "Domestic Fund Transfer ... favoring K R G HOTELS PVT LTD on 05-11-2026 for
# INR 2789"; the attached BT<n>.pdf carries the rest, but its text comes out with every label in one run
# and every value in another, so the values are picked by their own shape:
#   for the value of Rs. 2,789.00 | 05/11/26 (value date) | BT26110593758257 (customer ref)
#   YESIG63090210754 (bank reference = the UTR) | HTLPYT257988142 (Yatra payment document)
#   58270 (vendor ID) | YATMB0012288247 / IXIJP0002555287 (booking) | XXXXXX9778 (beneficiary a/c)
#   06-Nov-26 09-Nov-26 (stay)
_YB_AMOUNT = re.compile(r"value\s+of\s+Rs\.?\s*([\d,]+\.\d{2})", re.I)
_YB_VALUE_DATE = re.compile(r"\b(\d{2}/\d{2}/\d{2})\b")
_YB_CUSTOMER_REF = re.compile(r"\b(BT\d{10,})\b")
_YB_BANK_REF = re.compile(r"\b(YES[A-Z]{0,4}\d{8,})\b")
_YB_DOC = re.compile(r"\b(HTLPYT\d+)\b")
_YB_BOOKING = re.compile(r"\b((?:YAT[A-Z]{0,3}|IXIJ)\s?P?\d{7,}|P\d{9,})\b")
_YB_ACCOUNT = re.compile(r"\b(X{4,}\d{3,4})\b")
_YB_STAY = re.compile(r"\b(\d{2}-[A-Za-z]{3}-\d{2})\b")


def _pdf_attachments(msg) -> list[bytes]:
    out = []
    for part in msg.walk():
        if part.is_multipart():
            continue
        name = (part.get_filename() or "").lower()
        if name.endswith(".pdf") or part.get_content_type() == "application/pdf":
            data = part.get_payload(decode=True) or b""
            if data[:5].startswith(b"%PDF"):
                out.append(data)
    return out


def parse_yatra_advice_text(text: str) -> dict | None:
    """The fields of one Yatra payment advice, from the PDF's text. PURE. None if it is not Yatra's."""
    t = text or ""
    if YATRA_PAYER not in t.lower() or "payment advice" not in t.lower():
        return None
    from services.ota_service import canonical_ota_id
    amount = _money((_YB_AMOUNT.search(t) or [None, None])[1])
    vd = _YB_VALUE_DATE.search(t)
    try:
        value_date = datetime.strptime(vd.group(1), "%d/%m/%y").date() if vd else None
    except ValueError:
        value_date = None
    # The vendor ID sits between the payment document and the booking ("HTLPYT257988142 58270
    # YATMB0012288247"); fall back to the only standalone 5-digit number if the layout ever shifts.
    m = re.search(r"HTLPYT\d+\s+(\d{3,8})\s+(?:YAT|IXIJ|P\d)", t)
    loose = re.findall(r"(?<![\w/.,-])(\d{5})(?![\w/.,-])", t)
    vendor = m.group(1) if m else (loose[0] if len(set(loose)) == 1 else None)
    accounts = _YB_ACCOUNT.findall(t)
    bookings = list(dict.fromkeys(canonical_ota_id("yatra", re.sub(r"\s+", "", b)) for b in _YB_BOOKING.findall(t)))
    stay = [_d for _d in (_dmy_short(s) for s in _YB_STAY.findall(t)) if _d]
    bank_ref = _YB_BANK_REF.search(t)
    return {
        "vendor_id": vendor,
        "total": amount,
        "txn_date": value_date,
        "utr": bank_ref.group(1) if bank_ref else None,
        "reference": (_YB_CUSTOMER_REF.search(t) or [None, None])[1],
        "doc_no": (_YB_DOC.search(t) or [None, None])[1],
        # the beneficiary account is printed masked; the first masked number on the advice is ours
        # (the second, XXXXXXXXXXX1303, is Yatra's own debit account)
        "bank_account": accounts[0] if accounts else None,
        "bookings": bookings,
        "check_in": stay[0] if stay else None,
        "check_out": stay[1] if len(stay) > 1 else None,
    }


def _dmy_short(s):
    try:
        return datetime.strptime(s, "%d-%b-%y").date()
    except ValueError:
        return None


def parse_yatra_advice_email(raw: bytes) -> dict | None:
    """A YES Bank advice mail -> the same shape parse_payment_email returns, or None when it is not a
    payment from Yatra (YES Bank sends these for every corporate payer that uses it). PURE."""
    msg = email.message_from_bytes(raw)
    sender = email.utils.parseaddr(_decode(msg.get("From")))[1].lower()
    if not sender.endswith("@yes.bank.in"):
        return None
    from utils.pdf_text import pdf_text
    a = None
    for data in _pdf_attachments(msg):
        a = parse_yatra_advice_text(pdf_text(data))
        if a:
            break
    if not a:
        return None
    lines = []
    if a["total"] is not None:
        if len(a["bookings"]) == 1:
            lines = [{"ota_booking_id": a["bookings"][0], "amount_paid": a["total"]}]
        else:
            # never seen; the amount cannot be split by guessing, so every booking gets a 0 line and the
            # totals check flags the payment for a person to look at
            lines = [{"ota_booking_id": b, "amount_paid": 0.0} for b in a["bookings"]]
    for ln in lines:
        ln.update({"pnr": a["doc_no"], "guest_name": None, "hotel_name": None, "check_in": a["check_in"],
                   "check_out": a["check_out"], "original_cost": None, "booking_type": "Paid",
                   "brand": "YATRA", "channel_code": "yatra"})
    return {
        "kind": "yatra", "account_code": f"YATRA:{a['vendor_id'] or '?'}", "vendor_id": a["vendor_id"],
        "utr": a["utr"] or a["reference"], "reference": a["reference"], "total": a["total"],
        "mail_total": a["total"], "txn_date": a["txn_date"], "bank_name": "YES Bank (Yatra Online Limited)",
        "bank_account": a["bank_account"], "lines": lines,
        "flags": ["several_bookings"] if len(a["bookings"]) > 1 else [],
        "message_id": (msg.get("Message-ID") or msg.get("Message-Id") or "").strip() or None,
        "subject": _decode(msg.get("Subject")) or "BENEFICIARY PAYMENT ADVICE",
    }


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------
def tracking_start(db, cfg=None):
    """Stays before this date are kept but never shown as 'unknown booking' - they happened before
    the PMS took Go-MMT bookings. The setting wins; otherwise the first Go-MMT stay the PMS holds."""
    cfg = cfg or get_ota_payment_config(db)
    if cfg.get("tracking_start"):
        return cfg["tracking_start"]
    from sqlalchemy import func
    return (db.query(func.min(Booking.check_in))
            .filter(Booking.booking_source.in_(TRACKED_CHANNELS), Booking.ota_booking_id.isnot(None))
            .scalar())


def _find_booking(db, ota_booking_id):
    rows = db.query(Booking).filter(Booking.ota_booking_id == ota_booking_id).all()
    if not rows:
        return None
    # one id should be one booking; if a duplicate ever exists, prefer the live one
    rows.sort(key=lambda b: (b.status not in _PAYABLE_STATUSES, b.booking_id))
    return rows[0]


def _paid_elsewhere(db, booking_id, exclude_line_id=None) -> float:
    q = db.query(OtaSettlementLine).filter(OtaSettlementLine.booking_id == booking_id,
                                           OtaSettlementLine.match_status.in_(_LINKED))
    if exclude_line_id is not None:
        q = q.filter(OtaSettlementLine.id != exclude_line_id)
    return round(sum(float(x.amount_paid or 0) for x in q.all()), 2)


def match_line(db, line: OtaSettlementLine, cfg=None, start=None) -> str:
    """(Re)decide one line's booking and status. Lines an admin linked by hand are left alone."""
    if line.linked_by is not None:
        return line.match_status
    cfg = cfg or get_ota_payment_config(db)
    if line.hotel_name and _norm(line.hotel_name) != _norm(cfg["hotel_name"]):
        line.booking_id, line.match_status = None, "other_property"
        return line.match_status
    b = _find_booking(db, line.ota_booking_id)
    if b is None:
        line.booking_id, line.expected_amount = None, None
        start = start if start is not None else tracking_start(db, cfg)
        early = start is not None and line.check_in is not None and line.check_in < start
        line.match_status = "before_tracking" if early else "unknown_booking"
        return line.match_status
    line.booking_id = b.booking_id
    line.expected_amount = b.ota_net_payout
    if b.status == "cancelled":
        line.match_status = "cancelled_booking"
    elif b.ota_net_payout is None:
        line.match_status = "matched"          # no voucher figure to compare; the state flags it
    else:
        owed = float(b.ota_net_payout) - _paid_elsewhere(db, b.booking_id, exclude_line_id=line.id)
        line.match_status = "matched" if abs(float(line.amount_paid) - owed) <= cfg["tolerance"] else "amount_diff"
    return line.match_status


def rematch_open_lines(db, cfg=None) -> int:
    """Retry every line that found no booking. MMT pays within a day or two of check-in, so a
    payment can arrive before the desk has confirmed that voucher's draft; the next poll links it."""
    cfg = cfg or get_ota_payment_config(db)
    start = tracking_start(db, cfg)
    n = 0
    for line in (db.query(OtaSettlementLine)
                 .filter(OtaSettlementLine.match_status.in_(("unknown_booking", "before_tracking")),
                         OtaSettlementLine.linked_by.is_(None))
                 .all()):
        if match_line(db, line, cfg, start) not in ("unknown_booking", "before_tracking"):
            n += 1
    db.flush()
    return n


# ---------------------------------------------------------------------------
# Intake
# ---------------------------------------------------------------------------
def _ingest_one(db, raw: bytes, commit=False, source="mail") -> dict:
    """A Go-MMT payment mail, else a Go-MMT monthly statement (batch 3), else {"payment": False}."""
    res = ingest_payment_email(db, raw, commit=commit)
    if res.get("payment"):
        return res
    from services import ota_statements
    st = ota_statements.ingest_statement_email(db, raw, source=source, commit=commit)
    return st if st is not None else res


def ingest_mail(db, raw: bytes, commit=False, source="mail") -> dict:
    """The poller's entry point: a payment mail, a statement mail, or a mail carrying either as
    attachments. Returns {"payment": False} for anything else so the voucher logic carries on.
    A result with "payment" or "statement" set means the mail was ours."""
    res = _ingest_one(db, raw, commit=commit, source=source)
    if res.get("payment") or res.get("statement"):
        return res
    return ingest_forwarded_payments(db, raw, commit=commit) or res


def ingest_forwarded_payments(db, raw: bytes, commit=False) -> dict | None:
    """Gmail "Forward as attachment" from the hotel's main inbox - the way the payment history gets in
    (the PMS mailbox was registered with Go-MMT for bookings only; payment advice goes to the main
    inbox, and a Gmail forwarding filter never applies to mail already received). Each attached
    original keeps its own From / Subject / Message-ID, so it goes through ingest_payment_email
    exactly as if it had been delivered. Only read when the outer mail is from an address the owner
    listed in `ota_payment_forwarders`; otherwise None and the mail is treated as any other."""
    msg = email.message_from_bytes(raw)
    inner = [p.get_payload()[0] for p in msg.walk()
             if p.get_content_type() == "message/rfc822" and isinstance(p.get_payload(), list) and p.get_payload()]
    if not inner:
        return None
    sender = email.utils.parseaddr(_decode(msg.get("From")))[1].lower()
    if sender not in get_ota_payment_config(db)["forwarders"]:
        logger.info("OTA payment: attachments from %s ignored (not an allowed forwarder)", sender or "?")
        return None
    out = {"payment": True, "forwarded": 0, "stored": False, "stored_count": 0, "skipped_count": 0,
           "statements_stored": 0, "statements_skipped": 0, "results": []}
    for m in inner:
        r = _ingest_one(db, m.as_bytes(), commit=commit)
        if r.get("payment"):
            out["stored_count" if r.get("stored") else "skipped_count"] += 1
        elif r.get("statement"):
            out["statements_stored" if r.get("stored") else "statements_skipped"] += 1
        else:
            continue
        out["forwarded"] += 1
        out["results"].append({k: r.get(k) for k in ("utr", "amount", "kind", "period", "stored", "reason", "lines")})
    out["stored"] = out["stored_count"] > 0
    if not out["forwarded"]:
        return None
    return out


def _same_account(seen, ours) -> bool:
    """Go-MMT prints the full account number; YES Bank masks all but the last four (XXXXXX9778)."""
    seen_digits, ours = re.sub(r"\D", "", seen or ""), re.sub(r"\D", "", ours or "")
    if not seen_digits or not ours:
        return True                     # nothing to compare - no flag
    if "X" in (seen or "").upper():
        return ours.endswith(seen_digits)
    return seen_digits == ours


def ingest_payment_email(db, raw: bytes, commit=False) -> dict:
    """Store one payment mail - a Go-MMT wire confirmation or (P2a) a Yatra YES Bank advice.
    Idempotent on Message-ID and on UTR. Never raises."""
    try:
        p = parse_payment_email(raw) or parse_yatra_advice_email(raw)
    except Exception as e:  # a malformed mail must not stop the poll
        logger.exception("OTA payment: could not parse mail")
        return {"payment": True, "stored": False, "reason": f"unreadable ({e})"}
    if p is None:
        return {"payment": False}
    cfg = get_ota_payment_config(db)
    code = p["account_code"]
    yatra = p.get("kind") == "yatra"
    if yatra:
        role = "this" if p.get("vendor_id") and p["vendor_id"] == cfg["yatra_vendor_id"] else None
    else:
        role = cfg["accounts"].get(code)
    base = {"payment": True, "account_code": code, "utr": p.get("utr"), "amount": p.get("total"),
            "channel": "yatra" if yatra else "go_mmt"}
    if role != "this":
        # Bhimas Paradise (or an account / Yatra vendor nobody has mapped) - its money is not this hotel's.
        return {**base, "stored": False, "reason": "other property" if role == "ignore" else "unmapped account code"}
    if not p.get("utr") or p.get("total") is None or not p["lines"]:
        logger.warning("OTA payment %s: no UTR/total/bookings read from %r", code, p.get("subject"))
        return {**base, "stored": False, "reason": "transfer details or bookings not found in the mail"}

    existing = None
    if p.get("message_id"):
        existing = db.query(OtaSettlement).filter(OtaSettlement.message_id == p["message_id"]).first()
    if existing is None:
        existing = db.query(OtaSettlement).filter(OtaSettlement.utr == p["utr"]).first()
    if existing is not None:
        return {**base, "stored": False, "reason": "already recorded", "settlement_id": existing.id}

    lines_total = round(sum(x["amount_paid"] for x in p["lines"]), 2)
    flags = list(p.get("flags") or [])
    if abs(lines_total - p["total"]) > 0.5 or (p.get("mail_total") is not None and abs(p["mail_total"] - p["total"]) > 0.5):
        flags.append("totals_mismatch")
    if cfg["bank_account"] and p.get("bank_account") and not _same_account(p["bank_account"], cfg["bank_account"]):
        flags.append("bank_account_mismatch")
    ins = [x["check_in"] for x in p["lines"] if x.get("check_in")]
    s = OtaSettlement(
        # Go-MMT: one transfer covers MakeMyTrip and Goibibo, each line carries its own brand/channel
        channel_code="yatra" if yatra else "makemytrip",
        amount=p["total"], lines_total=lines_total, reference=p.get("reference") or p["utr"], utr=p["utr"],
        payment_date=p.get("txn_date") or p.get("subject_date"),
        period_start=min(ins) if ins else None, period_end=max(ins) if ins else None,
        ota_account_code=code, bank_name=p.get("bank_name"), bank_account=p.get("bank_account"),
        source="mail", message_id=p.get("message_id"), state="done", flags=",".join(flags) or None,
        raw_source=(p.get("subject") or "") + "\n" + _summary(p),
        notes=(f"Yatra payment advice (YES Bank), {len(p['lines'])} booking(s)" if yatra
               else f"Go-MMT payment mail, {len(p['lines'])} booking(s)"),
    )
    db.add(s)
    db.flush()
    start = tracking_start(db, cfg)
    counts = {}
    for x in p["lines"]:
        line = OtaSettlementLine(settlement_id=s.id, **{k: x[k] for k in (
            "channel_code", "ota_booking_id", "pnr", "guest_name", "hotel_name", "check_in",
            "check_out", "original_cost", "amount_paid", "booking_type", "brand")})
        db.add(line)
        db.flush()
        st = match_line(db, line, cfg, start)
        counts[st] = counts.get(st, 0) + 1
    db.flush()
    if flags:
        logger.warning("OTA payment %s %s: %s", code, p["utr"], ",".join(flags))
    if commit:
        db.commit()
    return {**base, "stored": True, "settlement_id": s.id, "lines": len(p["lines"]),
            "match": counts, "flags": flags}


def _summary(p) -> str:
    """A readable copy of what was read, kept on the settlement for audit."""
    head = (f"{p.get('bank_name')} a/c {p.get('bank_account')} | {p.get('total')} | {p.get('txn_date')} | "
            f"UTR {p.get('utr')}")
    rows = [f"{x['ota_booking_id']} | {x.get('pnr')} | {x.get('guest_name')} | {x.get('hotel_name')} | "
            f"{x.get('check_in')} - {x.get('check_out')} | {x['amount_paid']} | {x.get('booking_type')} | "
            f"{x.get('brand')}" for x in p["lines"]]
    return "\n".join([head] + rows)[:20000]


# ---------------------------------------------------------------------------
# Booking payment state - the ONE implementation
# ---------------------------------------------------------------------------
def is_tracked(b) -> bool:
    """A booking whose OTA payment this module tracks: Goibibo, MakeMyTrip or (P2a) Yatra."""
    return (b.booking_source or "") in TRACKED_CHANNELS


is_go_mmt = is_tracked      # P1 name, kept for callers outside this module


def _state_of(b, lines, settlements, cfg, today) -> dict:
    expected = float(b.ota_net_payout) if b.ota_net_payout is not None else None
    paid_lines = [x for x in lines if x.match_status in _LINKED]
    paid = round(sum(float(x.amount_paid or 0) for x in paid_lines), 2)
    sts = [settlements[x.settlement_id] for x in paid_lines if x.settlement_id in settlements]
    flags = []
    if paid_lines:
        state = "received" if sts and all(s.state == "received" for s in sts) else "done"
        if b.status == "cancelled":
            flags.append("paid_but_cancelled")
        if expected is None:
            flags.append("no_voucher_amount")
        elif paid < expected - cfg["tolerance"]:
            flags.append("short_paid")
        elif paid > expected + cfg["tolerance"]:
            flags.append("over_paid")
        if len(paid_lines) > 1:
            flags.append("paid_in_parts")
        for s in sts:
            for f in (s.flags or "").split(","):
                if f and f not in flags:
                    flags.append(f)
    elif b.status not in _PAYABLE_STATUSES:
        state = "not_applicable"
    else:
        state = "pending"
    # Go-MMT counts from check-out, Yatra (pays the day before arrival) from check-in - P2a
    due_from = b.check_in if (b.booking_source or "") in OVERDUE_FROM_CHECK_IN else b.check_out
    overdue = (state == "pending" and due_from is not None
               and today > due_from + timedelta(days=cfg["overdue_days"]))
    last = max(sts, key=lambda s: (s.payment_date or date.min, s.id)) if sts else None
    return {
        "booking_id": b.booking_id,
        "ota_booking_id": b.ota_booking_id,
        "channel": b.booking_source,
        "state": state,
        "overdue": overdue,
        "overdue_from": "check_in" if (b.booking_source or "") in OVERDUE_FROM_CHECK_IN else "check_out",
        "expected": expected,
        "paid": paid,
        "difference": round(paid - expected, 2) if (paid_lines and expected is not None) else None,
        "utrs": sorted({s.utr for s in sts if s.utr}),
        "payment_date": str(last.payment_date) if last and last.payment_date else None,
        "received_on": str(last.received_on) if last and last.received_on else None,
        "settlement_ids": sorted({s.id for s in sts}),
        "flags": flags,
    }


def ota_payment_states(db, bookings, cfg=None, today=None) -> dict:
    """{booking_id: state dict} for many bookings in three queries. Non-Go-MMT bookings are left out."""
    cfg = cfg or get_ota_payment_config(db)
    today = today or clock.business_today()
    bs = [b for b in bookings if is_tracked(b)]
    if not bs:
        return {}
    ids = [b.booking_id for b in bs]
    lines = db.query(OtaSettlementLine).filter(OtaSettlementLine.booking_id.in_(ids)).all()
    sids = {x.settlement_id for x in lines}
    settlements = ({s.id: s for s in db.query(OtaSettlement).filter(OtaSettlement.id.in_(sids)).all()}
                   if sids else {})
    by_b = {}
    for x in lines:
        by_b.setdefault(x.booking_id, []).append(x)
    return {b.booking_id: _state_of(b, by_b.get(b.booking_id, []), settlements, cfg, today) for b in bs}


def ota_payment_state(db, booking, cfg=None, today=None) -> dict | None:
    """One booking's Go-MMT payment state, or None for a booking that is not Goibibo/MakeMyTrip."""
    return ota_payment_states(db, [booking], cfg=cfg, today=today).get(booking.booking_id)


def booking_chain(db, booking, cfg=None, today=None) -> dict | None:
    """P2a: one booking's money from the guest's gross to the hotel's bank, step by step, each step as
    the voucher states it beside what the OTA's own documents say - so a break shows where it is.

        gross -> commission incl. GST (voucher vs invoice, net of credit notes)
              -> TCS (voucher vs statement; Yatra: voucher only) -> TDS (same)
              -> payable (voucher; also checked as gross - commission - TCS - TDS)
              -> paid (payment lines, UTRs) -> received (bank confirmation)

    None for a booking whose payments are not tracked."""
    if not is_tracked(booking):
        return None
    from models import OtaTaxStatement, OtaTaxStatementLine
    from services.ota_statements import voucher_figures
    cfg = cfg or get_ota_payment_config(db)
    tol = cfg["tolerance"]
    oid = booking.ota_booking_id
    yatra = booking.booking_source == "yatra"
    v = voucher_figures(db, [oid]).get(oid, {}) if oid else {}
    docs = []
    if oid:
        docs = (db.query(OtaTaxStatementLine, OtaTaxStatement)
                .join(OtaTaxStatement, OtaTaxStatement.id == OtaTaxStatementLine.statement_id)
                .filter(OtaTaxStatementLine.ota_booking_id == oid, OtaTaxStatement.superseded.is_(False),
                        OtaTaxStatement.channel == ("yatra" if yatra else "go_mmt")).all())
    by_kind = {}
    for line, st in docs:
        by_kind.setdefault(st.kind, []).append((line, st))
    cancelled = booking.status == "cancelled"

    def total(kinds):
        rows = [ln for k in kinds for ln, _ in by_kind.get(k, [])]
        return round(sum(float(x.amount) for x in rows), 2) if rows else None

    def docs_of(kinds):
        return sorted({st.doc_no or st.period.strftime("%b %Y") for k in kinds for _, st in by_kind.get(k, [])})

    def status(voucher, actual, expected_zero=False):
        want = 0.0 if expected_zero else voucher
        if actual is None:
            return "missing"
        if want is None:
            return "no_voucher"
        return "ok" if abs(actual - want) <= tol else "differs"

    steps = []
    gross = v.get("gross")
    if gross is None and booking.ota_net_payout is not None:
        from services.ota_service import ota_gross_basis
        gross = ota_gross_basis(booking)
    steps.append({"key": "gross", "label": "Guest paid the OTA (gross)", "voucher": gross, "actual": None,
                  "status": "info", "documents": []})
    com_actual = total(("commission", "credit_memo"))
    steps.append({"key": "commission", "label": "Commission incl. GST", "voucher": v.get("commission"),
                  "actual": com_actual, "status": status(v.get("commission"), com_actual, cancelled),
                  "documents": docs_of(("commission", "credit_memo"))})
    for k, label in (("tcs", "TCS (GST, 0.5%)"), ("tds", "TDS (194-O, 0.1%)")):
        if yatra:
            steps.append({"key": k, "label": label, "voucher": v.get(k), "actual": None, "status": "voucher_only",
                          "documents": []})
        else:
            act = total((k,))
            steps.append({"key": k, "label": label, "voucher": v.get(k), "actual": act,
                          "status": status(v.get(k), act, cancelled), "documents": docs_of((k,))})
    payable = float(booking.ota_net_payout) if booking.ota_net_payout is not None else v.get("payable")
    parts = [gross, v.get("commission"), v.get("tcs"), v.get("tds")]
    arith = (round(parts[0] - parts[1] - parts[2] - parts[3], 2) if all(x is not None for x in parts) else None)
    steps.append({"key": "payable", "label": "Payable to the hotel (voucher)", "voucher": payable,
                  "actual": arith, "status": ("info" if arith is None or payable is None else
                                              "ok" if abs(arith - payable) <= tol else "differs"),
                  "documents": [], "note": "actual = gross - commission - TCS - TDS from the voucher"})
    st = ota_payment_state(db, booking, cfg=cfg, today=today) or {}
    paid = st.get("paid") if st.get("state") in ("done", "received") else None
    steps.append({"key": "paid", "label": "Paid by the OTA", "voucher": payable, "actual": paid,
                  "status": ("missing" if paid is None else
                             "no_voucher" if payable is None else
                             "ok" if abs(paid - (0.0 if cancelled else payable)) <= tol else "differs"),
                  "documents": st.get("utrs", []), "date": st.get("payment_date"),
                  "overdue": st.get("overdue", False)})
    steps.append({"key": "received", "label": "Seen in the bank", "voucher": None,
                  "actual": paid if st.get("state") == "received" else None,
                  "status": ("ok" if st.get("state") == "received" else
                             "pending" if st.get("state") == "done" else "missing"),
                  "documents": [], "date": st.get("received_on")})
    return {"booking_id": booking.booking_id, "ota_booking_id": oid, "channel": booking.booking_source,
            "status": booking.status, "state": st, "steps": steps,
            "breaks": [x["key"] for x in steps if x["status"] == "differs"]}


def voucher_taxes(db, booking) -> dict:
    """The voucher's TCS and TDS for a booking, read from its confirmation draft (the voucher's home)."""
    d = None
    if booking.ota_booking_id:
        d = (db.query(OtaDraftBooking)
             .filter(OtaDraftBooking.ota_booking_id == booking.ota_booking_id,
                     OtaDraftBooking.kind == "confirmation")
             .order_by(OtaDraftBooking.id.desc()).first())
    return {"tcs": float(d.tcs_amount) if d is not None and d.tcs_amount is not None else None,
            "tds": float(d.tds_amount) if d is not None and d.tds_amount is not None else None}


def fill_voucher_taxes(db, dry_run=False) -> int:
    """Read TCS/TDS for drafts saved before P1 from the voucher text they already keep. Idempotent."""
    from services.ota_service import _commission_net
    n = 0
    for d in (db.query(OtaDraftBooking)
              .filter(OtaDraftBooking.kind == "confirmation", OtaDraftBooking.tcs_amount.is_(None),
                      OtaDraftBooking.raw_source.isnot(None))
              .all()):
        got = _commission_net(d.raw_source or "")
        if got.get("tcs_amount") is not None or got.get("tds_amount") is not None:
            n += 1
            if not dry_run:
                d.tcs_amount, d.tds_amount = got.get("tcs_amount"), got.get("tds_amount")
    if not dry_run:
        db.flush()
    return n


# ---------------------------------------------------------------------------
# Read models for the screens / API
# ---------------------------------------------------------------------------
def serialize_line(x: OtaSettlementLine) -> dict:
    return {
        "id": x.id, "settlement_id": x.settlement_id, "channel_code": x.channel_code,
        "ota_booking_id": x.ota_booking_id, "pnr": x.pnr, "guest_name": x.guest_name,
        "hotel_name": x.hotel_name,
        "check_in": str(x.check_in) if x.check_in else None,
        "check_out": str(x.check_out) if x.check_out else None,
        "original_cost": float(x.original_cost) if x.original_cost is not None else None,
        "amount_paid": float(x.amount_paid or 0),
        "expected_amount": float(x.expected_amount) if x.expected_amount is not None else None,
        "booking_type": x.booking_type, "brand": x.brand, "booking_id": x.booking_id,
        "match_status": x.match_status, "linked_manually": x.linked_by is not None,
    }


def serialize_payment(s: OtaSettlement, lines=None) -> dict:
    out = {
        "id": s.id, "source": s.source, "state": s.state,
        "payment_date": str(s.payment_date) if s.payment_date else None,
        "utr": s.utr, "amount": float(s.amount or 0),
        "lines_total": float(s.lines_total) if s.lines_total is not None else None,
        "ota_account_code": s.ota_account_code, "bank_name": s.bank_name, "bank_account": s.bank_account,
        "flags": [f for f in (s.flags or "").split(",") if f],
        "received_on": str(s.received_on) if s.received_on else None,
        "received_amount": float(s.received_amount) if s.received_amount is not None else None,
        "received_note": s.received_note,
        # what the bank showed minus what Go-MMT said it sent; None until received
        "received_difference": (round(float(s.received_amount) - float(s.amount or 0), 2)
                                 if s.received_amount is not None else None),
        "undo_reason": s.undo_reason,
        "undone_at": s.undone_at.isoformat() if s.undone_at else None,
        "notes": s.notes,
        "created_at": s.created_at.isoformat() if s.created_at else None,
    }
    if lines is not None:
        out["lines"] = [serialize_line(x) for x in lines]
        out["bookings"] = len(lines)
        out["unmatched"] = sum(1 for x in lines if x.match_status == "unknown_booking")
        out["differences"] = sum(1 for x in lines if x.match_status in ("amount_diff", "cancelled_booking"))
        out["channels"] = sorted({x.channel_code for x in lines if x.channel_code})
        # P2a: what the vouchers say this transfer should have been, beside what was sent. Only a full
        # figure when every line found its booking and that booking's voucher has a payable.
        known = [x for x in lines if x.expected_amount is not None and x.match_status in _LINKED]
        out["expected_total"] = round(sum(float(x.expected_amount) for x in known), 2) if known else None
        out["expected_missing"] = len(lines) - len(known)
        out["expected_difference"] = (round(float(s.amount or 0) - out["expected_total"], 2)
                                      if known and not out["expected_missing"] else None)
    return out


def payment_channels(db, settlement_ids) -> dict:
    """{settlement_id: {channels}} - a Go-MMT transfer can hold MakeMyTrip and Goibibo lines."""
    out = {}
    if not settlement_ids:
        return out
    for sid, ch in (db.query(OtaSettlementLine.settlement_id, OtaSettlementLine.channel_code)
                    .filter(OtaSettlementLine.settlement_id.in_(list(settlement_ids))).distinct()):
        out.setdefault(sid, set()).add(ch)
    return out


def in_channels(db, settlements, chans) -> list:
    """The settlements that pay at least one booking of `chans` (by their lines, else their own code)."""
    if set(chans) == set(TRACKED_CHANNELS):
        return list(settlements)
    by = payment_channels(db, [s.id for s in settlements])
    return [s for s in settlements if (by.get(s.id) or {s.channel_code}) & set(chans)]


# ---------------------------------------------------------------------------
# Batch 2 - the admin's actions (owner, 5 Nov: confirm per UTR, admin/owner only, undo with a reason)
# ---------------------------------------------------------------------------
class PaymentError(ValueError):
    """A refused admin action; the message is shown to the admin as-is."""


UNCONFIRMED_AFTER_DAYS = 7      # a 'done' payment nobody has checked in the bank for a week -> alert


def _now_naive():
    return clock.now_utc().replace(tzinfo=None)


def receive_payment(db, s: OtaSettlement, *, user_id=None, received_on=None, received_amount=None,
                    note=None) -> OtaSettlement:
    """'Payment received': an admin saw this UTR's credit in the bank. Every booking in it turns
    received at once. A bank amount that differs from the transfer is kept and shown, not refused -
    the admin is reporting what the bank says."""
    if s.state == "received":
        raise PaymentError("This payment is already marked received.")
    amount = round(float(received_amount), 2) if received_amount is not None else float(s.amount or 0)
    if amount < 0:
        raise PaymentError("The amount seen in the bank cannot be negative.")
    s.state = "received"
    s.received_on = received_on or s.payment_date or clock.business_today()
    s.received_amount = amount
    s.received_note = (note or "").strip()[:500] or None
    s.received_at = _now_naive()
    s.received_by = user_id
    s.undone_at = s.undone_by = s.undo_reason = None
    db.flush()
    return s


def undo_receipt(db, s: OtaSettlement, *, user_id=None, reason=None) -> OtaSettlement:
    """Back to 'payment done'. A reason is required; the previous receipt stays in the audit log."""
    reason = (reason or "").strip()
    if s.state != "received":
        raise PaymentError("Only a payment marked received can be undone.")
    if len(reason) < 3:
        raise PaymentError("Give a reason for undoing the receipt.")
    s.state = "done"
    s.received_on = s.received_amount = s.received_note = s.received_at = s.received_by = None
    s.undone_at = _now_naive()
    s.undone_by = user_id
    s.undo_reason = reason[:500]
    db.flush()
    return s


def _line_status(db, line, booking, cfg) -> str:
    if booking.status == "cancelled":
        return "cancelled_booking"
    if booking.ota_net_payout is None:
        return "matched"
    owed = float(booking.ota_net_payout) - _paid_elsewhere(db, booking.booking_id, exclude_line_id=line.id)
    return "matched" if abs(float(line.amount_paid) - owed) <= cfg["tolerance"] else "amount_diff"


def link_line(db, line: OtaSettlementLine, booking, *, user_id=None) -> OtaSettlementLine:
    """An admin ties a payment line to a booking by hand (the OTA id was typed differently, or the
    booking was made without one). `linked_by` keeps automatic re-matching away from it."""
    if not is_tracked(booking):
        raise PaymentError("Only a Goibibo, MakeMyTrip or Yatra booking can be linked to an OTA payment.")
    cfg = get_ota_payment_config(db)
    line.booking_id = booking.booking_id
    line.expected_amount = booking.ota_net_payout
    line.linked_by = user_id
    db.flush()
    line.match_status = _line_status(db, line, booking, cfg)
    db.flush()
    return line


def ignore_line(db, line: OtaSettlementLine, *, user_id=None) -> OtaSettlementLine:
    """Take a line out of the exceptions (a stay from before the PMS, say). link_line undoes it."""
    line.booking_id = None
    line.match_status = "ignored"
    line.linked_by = user_id
    db.flush()
    return line


def _manual_rows(db, ids, amounts):
    bookings = {b.booking_id: b for b in db.query(Booking).filter(Booking.booking_id.in_(ids)).all()}
    missing = [i for i in ids if i not in bookings]
    if missing:
        raise PaymentError(f"Booking(s) not found: {', '.join(map(str, missing))}.")
    rows = []
    for i in ids:
        b = bookings[i]
        if not is_tracked(b):
            raise PaymentError(f"Booking #{i} is not a Goibibo / MakeMyTrip / Yatra booking.")
        a = amounts.get(i, b.ota_net_payout)
        if a is None:
            raise PaymentError(f"Booking #{i} has no voucher payable - enter its amount.")
        rows.append((b, round(float(a), 2)))
    return rows


def create_manual_payment(db, *, utr, payment_date, amount, booking_ids, amounts=None, note=None,
                          user_id=None) -> OtaSettlement:
    """'Add payment' for a transfer whose mail never arrived: the admin types the UTR, date and amount
    and ticks the bookings it covers. Each line is the voucher's payable unless `amounts` says
    otherwise ({booking_id: amount})."""
    utr = re.sub(r"\s+", "", str(utr or "")).upper()
    if not utr:
        raise PaymentError("Enter the UTR / bank reference.")
    if db.query(OtaSettlement).filter(OtaSettlement.utr == utr).first() is not None:
        raise PaymentError(f"A payment with UTR {utr} is already recorded.")
    if not payment_date:
        raise PaymentError("Enter the payment date.")
    try:
        amount = round(float(amount), 2)
    except (TypeError, ValueError):
        raise PaymentError("Enter the amount transferred.")
    if amount <= 0:
        raise PaymentError("The amount must be more than zero.")
    ids = list(dict.fromkeys(int(i) for i in (booking_ids or [])))
    if not ids:
        raise PaymentError("Tick at least one booking.")
    rows = _manual_rows(db, ids, {int(k): v for k, v in (amounts or {}).items()})
    cfg = get_ota_payment_config(db)
    lines_total = round(sum(a for _, a in rows), 2)
    sources = {b.booking_source for b, _ in rows}
    s = OtaSettlement(
        channel_code="yatra" if sources == {"yatra"} else "makemytrip",
        amount=amount, lines_total=lines_total, reference=utr, utr=utr,
        payment_date=payment_date, source="manual", state="done", created_by=user_id,
        flags="totals_mismatch" if abs(lines_total - amount) > 0.5 else None,
        period_start=min(b.check_in for b, _ in rows), period_end=max(b.check_in for b, _ in rows),
        notes=((note or "").strip()[:500] or f"Added by hand, {len(rows)} booking(s)"),
    )
    db.add(s)
    db.flush()
    for b, a in rows:
        line = OtaSettlementLine(
            settlement_id=s.id, channel_code=b.booking_source, ota_booking_id=b.ota_booking_id or f"#{b.booking_id}",
            guest_name=b.guest_name, check_in=b.check_in, check_out=b.check_out, amount_paid=a,
            booking_id=b.booking_id, expected_amount=b.ota_net_payout, linked_by=user_id,
            brand={"goibibo": "GOIBIBO", "yatra": "YATRA"}.get(b.booking_source, "MMTINDIA"))
        db.add(line)
        db.flush()
        line.match_status = _line_status(db, line, b, cfg)
    db.flush()
    return s


# ---------------------------------------------------------------------------
# Batch 2 - what the OTA Payments screen shows
# ---------------------------------------------------------------------------
def _tracked_bookings(db, cfg, until=None, chans=TRACKED_CHANNELS):
    """Tracked OTA bookings whose stay has begun (by `until`), from the tracking start. Yatra pays the
    day before check-in, so a Yatra stay starting tomorrow is not yet expected - same rule for all."""
    q = db.query(Booking).filter(Booking.booking_source.in_(chans),
                                 Booking.check_in <= (until or clock.business_today()))
    start = tracking_start(db, cfg)
    if start is not None:
        q = q.filter(Booking.check_in >= start)
    return q.all()


def _sum_money(xs, f) -> float:
    return round(sum(float(f(x) or 0) for x in xs), 2)


def summary(db, today=None, channel=None) -> dict:
    """The four tiles: pending, overdue, done-awaiting-bank-check, received this month (P2a: per channel)."""
    cfg = get_ota_payment_config(db)
    today = today or clock.business_today()
    chans = channels_of(channel)
    states = ota_payment_states(db, _tracked_bookings(db, cfg, today, chans), cfg=cfg, today=today)
    pend = [st for st in states.values() if st["state"] == "pending"]
    over = [st for st in pend if st["overdue"]]
    done = in_channels(db, db.query(OtaSettlement).filter(OtaSettlement.utr.isnot(None),
                                                          OtaSettlement.state == "done").all(), chans)
    rec = in_channels(db, (db.query(OtaSettlement)
                           .filter(OtaSettlement.state == "received", OtaSettlement.received_on >= today.replace(day=1),
                                   OtaSettlement.received_on <= today).all()), chans)
    return {
        "today": str(today),
        "pending": {"count": len(pend), "expected": _sum_money(pend, lambda s: s["expected"])},
        "overdue": {"count": len(over), "expected": _sum_money(over, lambda s: s["expected"])},
        "awaiting_bank_check": {"count": len(done), "amount": _sum_money(done, lambda s: s.amount)},
        "received_this_month": {"count": len(rec), "amount": _sum_money(rec, lambda s: s.received_amount)},
        "overdue_days": cfg["overdue_days"],
    }


_EXCEPTION_LINES = ("unknown_booking", "amount_diff", "cancelled_booking", "other_property")
_MONEY_FLAGS = {"short_paid", "over_paid", "paid_but_cancelled"}


def exceptions(db, today=None, channel=None) -> dict:
    """Everything a person must look at: problem lines, flagged payments, overdue / mis-paid bookings."""
    cfg = get_ota_payment_config(db)
    today = today or clock.business_today()
    chans = channels_of(channel)
    lines = (db.query(OtaSettlementLine).filter(OtaSettlementLine.match_status.in_(_EXCEPTION_LINES),
                                                OtaSettlementLine.channel_code.in_(chans))
             .order_by(OtaSettlementLine.settlement_id.desc(), OtaSettlementLine.id).all())
    sids = {x.settlement_id for x in lines}
    pays = {s.id: s for s in db.query(OtaSettlement).filter(OtaSettlement.id.in_(sids)).all()} if sids else {}
    out_lines = []
    for x in lines:
        r = serialize_line(x)
        s = pays.get(x.settlement_id)
        r["utr"] = s.utr if s else None
        r["payment_date"] = str(s.payment_date) if s and s.payment_date else None
        out_lines.append(r)
    flagged = [serialize_payment(s) for s in in_channels(db, db.query(OtaSettlement)
               .filter(OtaSettlement.flags.isnot(None), OtaSettlement.flags != "")
               .order_by(OtaSettlement.payment_date.desc()).all(), chans)]
    bs = _tracked_bookings(db, cfg, today, chans)
    states = ota_payment_states(db, bs, cfg=cfg, today=today)
    by_id = {b.booking_id: b for b in bs}
    bad = []
    for bid, st in states.items():
        if st["overdue"] or (set(st["flags"]) & _MONEY_FLAGS):
            b = by_id[bid]
            bad.append({**st, "guest_name": b.guest_name, "booking_source": b.booking_source,
                        "status": b.status, "check_in": str(b.check_in), "check_out": str(b.check_out)})
    bad.sort(key=lambda r: r["check_out"])
    return {"lines": out_lines, "payments": flagged, "bookings": bad}


# ---------------------------------------------------------------------------
# Batch 2 - alerts (called from utils/fraud_detection.run_reconciliation)
# ---------------------------------------------------------------------------
ALERT_OVERDUE = "ota_payment_overdue"          # med  - not paid N days after checkout (Yatra: check-in)
ALERT_UNCONFIRMED = "ota_payment_unconfirmed"  # low  - MMT says paid, nobody checked the bank in a week
ALERT_PROBLEM = "ota_payment_problem"          # high for a wrong bank account, else med


def _alert_overdue(db, make_alert, cfg, today) -> set:
    states = ota_payment_states(db, _tracked_bookings(db, cfg, today), cfg=cfg, today=today)
    overdue = set()
    for bid, st in states.items():
        if not st["overdue"]:
            continue
        overdue.add(bid)
        make_alert(type=ALERT_OVERDUE, severity="med", dedupe_key=f"{ALERT_OVERDUE}:booking:{bid}",
                   booking_id=bid,
                   detail={"ota_booking_id": st["ota_booking_id"], "expected": st["expected"],
                           "channel": st["channel"],
                           "note": (f"Yatra has not paid this booking {cfg['overdue_days']} days after check-in - "
                                    f"ask your Yatra account manager" if st["channel"] == "yatra" else
                                    f"Go-MMT has not paid this booking {cfg['overdue_days']} days after "
                                    f"checkout - ask payments@go-mmt.com")})
    return overdue


def _alert_unconfirmed(db, make_alert, today) -> set:
    cutoff = today - timedelta(days=UNCONFIRMED_AFTER_DAYS)
    waiting = set()
    for s in db.query(OtaSettlement).filter(OtaSettlement.utr.isnot(None), OtaSettlement.state == "done",
                                            OtaSettlement.payment_date <= cutoff).all():
        waiting.add(s.id)
        make_alert(type=ALERT_UNCONFIRMED, severity="low", dedupe_key=f"{ALERT_UNCONFIRMED}:payment:{s.id}",
                   detail={"payment_id": s.id, "utr": s.utr, "amount": float(s.amount or 0),
                           "payment_date": str(s.payment_date),
                           "note": "Go-MMT says this was paid over a week ago - check the bank and "
                                   "press Confirm received on OTA Payments"})
    return waiting


def _alert_problems(db, make_alert) -> set:
    bad_lines = {}
    for x in db.query(OtaSettlementLine).filter(
            OtaSettlementLine.match_status.in_(("unknown_booking", "amount_diff", "cancelled_booking"))).all():
        bad_lines[x.settlement_id] = bad_lines.get(x.settlement_id, 0) + 1
    flagged = {s.id: s for s in db.query(OtaSettlement).filter(OtaSettlement.flags.isnot(None),
                                                               OtaSettlement.flags != "").all()}
    problems = set(bad_lines) | set(flagged)
    for sid in sorted(problems):
        s = flagged.get(sid) or db.query(OtaSettlement).filter(OtaSettlement.id == sid).first()
        if s is None:
            continue
        flags = [f for f in (s.flags or "").split(",") if f]
        make_alert(type=ALERT_PROBLEM, severity="high" if "bank_account_mismatch" in flags else "med",
                   dedupe_key=f"{ALERT_PROBLEM}:payment:{sid}",
                   detail={"payment_id": sid, "utr": s.utr, "flags": flags,
                           "lines_to_check": bad_lines.get(sid, 0),
                           "note": "A Go-MMT payment needs checking on OTA Payments -> Exceptions"})
    return problems


def detect_alerts(db, make_alert, today=None) -> None:
    """Raise the payment alerts through fraud_detection's de-duplicating `make_alert`, and close the
    overdue / unconfirmed ones whose cause has gone (the booking got paid, the payment confirmed)."""
    import json
    from models import FraudAlert
    cfg = get_ota_payment_config(db)
    today = today or clock.business_today()
    overdue = _alert_overdue(db, make_alert, cfg, today)
    waiting = _alert_unconfirmed(db, make_alert, today)
    problems = _alert_problems(db, make_alert)
    from services import ota_statements
    missing = ota_statements.detect_alerts(db, make_alert, today=today)     # batch 3: statements not received
    live = {ALERT_UNCONFIRMED: waiting, ALERT_PROBLEM: problems}
    for a in db.query(FraudAlert).filter(FraudAlert.status == "open", FraudAlert.type.in_(
            (ALERT_OVERDUE, ALERT_UNCONFIRMED, ALERT_PROBLEM, ota_statements.ALERT_MISSING))).all():
        try:
            info = json.loads(a.detail or "{}")
        except (TypeError, ValueError):
            info = {}
        pid = info.get("payment_id")
        if (a.type == ALERT_OVERDUE and a.booking_id not in overdue) or \
                (a.type == ota_statements.ALERT_MISSING and info.get("dedupe_key") not in missing) or \
                (a.type in live and pid not in live[a.type]):
            a.status, a.reviewed_at = "dismissed", _now_naive()
            a.review_note = "Auto-closed: the payment has since arrived / been confirmed."
