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
            .filter(Booking.booking_source.in_(GO_MMT_CHANNELS), Booking.ota_booking_id.isnot(None))
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
def ingest_mail(db, raw: bytes, commit=False) -> dict:
    """The poller's entry point: a payment mail, or a mail carrying payment mails as attachments.
    Returns {"payment": False} for anything else so the voucher logic carries on."""
    res = ingest_payment_email(db, raw, commit=commit)
    if res.get("payment"):
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
           "results": []}
    for m in inner:
        r = ingest_payment_email(db, m.as_bytes(), commit=commit)
        if not r.get("payment"):
            continue
        out["forwarded"] += 1
        out["stored_count" if r.get("stored") else "skipped_count"] += 1
        out["results"].append({k: r.get(k) for k in ("utr", "amount", "stored", "reason", "lines")})
    out["stored"] = out["stored_count"] > 0
    if not out["forwarded"]:
        return None
    return out


def ingest_payment_email(db, raw: bytes, commit=False) -> dict:
    """Store one Go-MMT payment mail. Idempotent on Message-ID and on UTR. Never raises."""
    try:
        p = parse_payment_email(raw)
    except Exception as e:  # a malformed mail must not stop the poll
        logger.exception("OTA payment: could not parse mail")
        return {"payment": True, "stored": False, "reason": f"unreadable ({e})"}
    if p is None:
        return {"payment": False}
    cfg = get_ota_payment_config(db)
    code = p["account_code"]
    role = cfg["accounts"].get(code)
    base = {"payment": True, "account_code": code, "utr": p.get("utr"), "amount": p.get("total")}
    if role != "this":
        # Bhimas Paradise (or an account nobody has mapped) - its money is not this hotel's.
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
    flags = []
    if abs(lines_total - p["total"]) > 0.5 or (p.get("mail_total") is not None and abs(p["mail_total"] - p["total"]) > 0.5):
        flags.append("totals_mismatch")
    if cfg["bank_account"] and p.get("bank_account") and p["bank_account"] != cfg["bank_account"]:
        flags.append("bank_account_mismatch")
    ins = [x["check_in"] for x in p["lines"] if x.get("check_in")]
    s = OtaSettlement(
        channel_code="makemytrip",      # Go-MMT; each line carries its own brand/channel
        amount=p["total"], lines_total=lines_total, reference=p["utr"], utr=p["utr"],
        payment_date=p.get("txn_date") or p.get("subject_date"),
        period_start=min(ins) if ins else None, period_end=max(ins) if ins else None,
        ota_account_code=code, bank_name=p.get("bank_name"), bank_account=p.get("bank_account"),
        source="mail", message_id=p.get("message_id"), state="done", flags=",".join(flags) or None,
        raw_source=(p.get("subject") or "") + "\n" + _summary(p),
        notes=f"Go-MMT payment mail, {len(p['lines'])} booking(s)",
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
def is_go_mmt(b) -> bool:
    return (b.booking_source or "") in GO_MMT_CHANNELS


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
    overdue = (state == "pending" and b.check_out is not None
               and today > b.check_out + timedelta(days=cfg["overdue_days"]))
    last = max(sts, key=lambda s: (s.payment_date or date.min, s.id)) if sts else None
    return {
        "booking_id": b.booking_id,
        "ota_booking_id": b.ota_booking_id,
        "state": state,
        "overdue": overdue,
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
    bs = [b for b in bookings if is_go_mmt(b)]
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
        "created_at": s.created_at.isoformat() if s.created_at else None,
    }
    if lines is not None:
        out["lines"] = [serialize_line(x) for x in lines]
        out["bookings"] = len(lines)
        out["unmatched"] = sum(1 for x in lines if x.match_status == "unknown_booking")
        out["differences"] = sum(1 for x in lines if x.match_status in ("amount_diff", "cancelled_booking"))
    return out
