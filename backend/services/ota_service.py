"""OTA tracking service (prompt 17, Phase A).

Makes OTA business (MakeMyTrip / Goibibo / Booking.com / Agoda) first-class inside the PMS:

  * Per-OTA config (commission %, active, mailbox toggle, cancellation rules) in `ota_channels`.
  * Commission / net-payout snapshot applied to a booking at desk entry (`apply_ota_fields`).
  * Payout reconciliation: expected payout (gross - commission) vs actual bank settlements.
  * OPTIONAL email auto-draft: poll a dedicated IMAP mailbox, parse OTA confirmation/cancellation
    emails, and upsert `ota_draft_bookings` for the desk to confirm in one click. This poller is
    OFF unless OTA_IMAP_* env is set AND the channel's mailbox_parsing_enabled flag is on — the
    same pluggable-off idiom as utils/whatsapp_service.py / utils/storage.py.

Nothing here raises into event hooks: the IMAP poller is best-effort and swallows its own errors.
Parsers are exposed as pure functions (`parse_email_bytes`) so they can be unit-tested against
`.eml` fixtures without a live mailbox.
"""
import os
import re
import email
import imaplib
import logging
from datetime import datetime, date, time
from email.header import decode_header, make_header

from sqlalchemy import text

from models import Booking, OtaChannel, OtaSettlement, OtaDraftBooking, RoomType

logger = logging.getLogger(__name__)

# Booking statuses that count as a real (non-cancelled) stay — matches routers/reports.py.
_LIVE_STATUSES = ("confirmed", "checked_in", "checked_out")

# Postgres advisory-lock key so only ONE mailbox poll runs at a time across the 4 uvicorn workers
# (each runs its own scheduler) and the desk's 10-min poll. Without it, two pollers racing on the
# same UNSEEN message could auto-confirm the same booking twice. Distinct from overstay's key.
_POLL_LOCK_KEY = 0x00074A11

# The OTA channels shipped as defaults (prompt 05 booking_source vocab + prompt 17 `other_ota`).
# Since v3 this is the SEED for the admin-editable `ota_source` family — call ota_sources(db)
# for the codes actually in force. This tuple stays the fallback when there is no session.
OTA_SOURCES = ("makemytrip", "goibibo", "booking_com", "agoda", "yatra", "other_ota")

# Default display names + email-sender domains used to auto-detect a channel from an email.
_CHANNEL_DEFAULTS = {
    "makemytrip":  {"display_name": "MakeMyTrip",  "domains": ("makemytrip.com", "go-mmt.com", "ingommt.com")},
    "goibibo":     {"display_name": "Goibibo",     "domains": ("goibibo.com", "go-mmt.com")},
    "booking_com": {"display_name": "Booking.com", "domains": ("booking.com",)},
    "agoda":       {"display_name": "Agoda",       "domains": ("agoda.com",)},
    # Yatra bookings come via its Travelguru hotelier desk (donotreply@yatra.com, replies to
    # rezrescue@desiya.com).
    "yatra":       {"display_name": "Yatra (Travelguru)", "domains": ("yatra.com", "desiya.com", "travelguru.com")},
    "other_ota":   {"display_name": "Other OTA",   "domains": ()},
}


def ota_sources(db=None) -> tuple:
    """The OTA channel codes in force. Admin-editable since v3 (`ota_source` family), which is
    constrained to be a subset of `booking_source` — so anything here is always a bookable
    source. Falls back to the shipped tuple with no session or a settings failure, because
    money paths (commission, payout reconciliation) must never lose their channel list."""
    if db is None:
        return OTA_SOURCES
    try:
        from utils.settings import get_category_list
        items = get_category_list(db, "ota_source")
        return tuple(items) if items else OTA_SOURCES
    except Exception:                      # settings table missing / unreadable
        return OTA_SOURCES


def is_ota_source(source, db=None) -> bool:
    return (source or "") in ota_sources(db)


def _channel_meta(code: str) -> dict:
    """Display name + sender domains for a channel. An admin-added code has no shipped entry,
    so it gets a prettified name and no domain auto-detection (its mail can still be parsed
    with the common parser once someone enables mailbox parsing for it)."""
    return _CHANNEL_DEFAULTS.get(
        code, {"display_name": code.replace("_", " ").title(), "domains": ()})


# ---------------------------------------------------------------------------
# Per-OTA config (ota_channels)
# ---------------------------------------------------------------------------
def ensure_channels(db, commit=False):
    """Make sure every recognised channel has a config row (defensive — migration 015 seeds them,
    but a fresh create_all DB may not have run the seed). Never overwrites an existing row."""
    existing = {c.code for c in db.query(OtaChannel).all()}
    created = False
    for code in ota_sources(db):
        if code not in existing:
            db.add(OtaChannel(code=code, display_name=_channel_meta(code)["display_name"],
                              commission_percent=0, active=True, mailbox_parsing_enabled=False))
            created = True
    if created:
        db.flush()
        if commit:
            db.commit()


def get_channels(db) -> list:
    ensure_channels(db)
    return db.query(OtaChannel).order_by(OtaChannel.id).all()


def get_channel(db, code):
    return db.query(OtaChannel).filter(OtaChannel.code == code).first()


def serialize_channel(c) -> dict:
    return {
        "code": c.code,
        "display_name": c.display_name,
        "commission_percent": float(c.commission_percent or 0),
        "active": bool(c.active),
        "mailbox_parsing_enabled": bool(c.mailbox_parsing_enabled),
        "cancellation_policy": c.cancellation_policy or "",
    }


def upsert_channel(db, code, *, display_name=None, commission_percent=None, active=None,
                   mailbox_parsing_enabled=None, cancellation_policy=None, commit=False):
    """Update a channel's config (creating the row if missing). Only provided fields change."""
    c = get_channel(db, code)
    if c is None:
        c = OtaChannel(code=code,
                       display_name=display_name or _CHANNEL_DEFAULTS.get(code, {}).get("display_name", code),
                       commission_percent=0, active=True, mailbox_parsing_enabled=False)
        db.add(c)
    if display_name is not None:
        c.display_name = display_name
    if commission_percent is not None:
        c.commission_percent = round(float(commission_percent), 2)
    if active is not None:
        c.active = bool(active)
    if mailbox_parsing_enabled is not None:
        c.mailbox_parsing_enabled = bool(mailbox_parsing_enabled)
    if cancellation_policy is not None:
        c.cancellation_policy = cancellation_policy
    db.flush()
    if commit:
        db.commit()
    return c


def commission_for(db, source) -> float:
    """The configured commission % for an OTA source (0 if unknown/unconfigured)."""
    c = get_channel(db, source)
    return float(c.commission_percent or 0) if c else 0.0


# ---------------------------------------------------------------------------
# Booking OTA snapshot
# ---------------------------------------------------------------------------
def apply_ota_fields(db, booking, source, ota_booking_id=None, commission_percent_override=None,
                     commission_amount=None, net_payout=None):
    """Stamp the OTA tracking snapshot onto a freshly-priced booking.

    commission % = the override (if the desk typed one) else the channel's configured default.
    net payout   = gross booking value - commission (the amount the hotel expects the OTA to remit).
    Gross = booking.grand_total (desk bookings carry no convenience fee, so grand_total == room total).
    No-ops for non-OTA sources. Joins the caller's transaction (no commit here).

    v4b1 (R9) also stamps the PREPAYMENT. An OTA guest has already paid the channel, but the
    hotel records no Payment row for it — rightly, that money never reached the hotel. Until
    now total_paid() therefore returned 0 for every OTA stay, so the folio showed the full room
    amount due and the desk collected it a SECOND time.

    ⚠️ prepaid_amount is the GROSS the guest paid, NOT ota_net_payout. The net payout is what
    the hotel eventually receives after the channel's commission; crediting that instead would
    under-credit the guest by exactly the commission and leave a phantom balance on the folio.

    This is the single place both OTA entry paths pass through — the desk's own OTA booking
    (reception.create_desk_booking) and the mailbox import (routers/ota.py, which delegates to
    the same handler) — so neither can drift.
    """
    if not is_ota_source(source, db):
        return booking
    gross = float(booking.grand_total or booking.total_amount or 0)
    # Prefer the ACTUAL commission the voucher states (read per booking) over a configured %.
    # The % is then a derived display figure; it falls back to the channel default only when the
    # email carried no commission (e.g. a desk-typed OTA booking).
    if commission_amount is not None:
        pct = round(float(commission_amount) / gross * 100, 2) if gross else 0.0
    else:
        pct = commission_percent_override
        if pct is None:
            pct = commission_for(db, source)
        pct = round(float(pct or 0), 2)
    booking.ota_booking_id = (str(ota_booking_id).strip() or None) if ota_booking_id else None
    booking.ota_commission_percent = pct
    booking.ota_commission_amount = round(float(commission_amount), 2) if commission_amount is not None else None
    # Net payout = the voucher's "Payable to Property" when known (the real bank amount, so payout
    # reconciliation matches the settlement), else gross - commission as before.
    if net_payout is not None:
        booking.ota_net_payout = round(float(net_payout), 2)
    else:
        comm = float(commission_amount) if commission_amount is not None else gross * pct / 100
        booking.ota_net_payout = round(gross - comm, 2)
    booking.prepaid_amount = round(gross, 2)
    booking.prepaid_source = "ota"
    return booking


def ota_gross_basis(b: Booking) -> float:
    """The value the CHANNEL actually sold — the basis commission is owed on.

    v4b2: `grand_total` stopped being that number the moment a stay could be extended at the
    desk. Extra nights sold face-to-face are a DIRECT hotel sale: the guest pays the hotel for
    them, and the OTA is owed nothing. Left on `grand_total`, every extension would have
    inflated the commission the hotel reports it owes the channel.

    `prepaid_amount` is exactly the OTA gross (stamped by apply_ota_fields at booking time and
    never touched by an extension), so it is the right basis. It falls back to `grand_total`
    for OTA bookings created before migration 028, whose prepaid_amount is 0 — so legacy rows
    report exactly as they always did.
    """
    return float(b.prepaid_amount or b.grand_total or b.total_amount or 0)


def serialize_ota_booking(db, b: Booking) -> dict:
    gross = float(b.grand_total or b.total_amount or 0)
    basis = ota_gross_basis(b)
    pct = float(b.ota_commission_percent) if b.ota_commission_percent is not None else None
    # Prefer the actual commission read from the voucher; fall back to %×gross for older/desk rows.
    if b.ota_commission_amount is not None:
        commission = float(b.ota_commission_amount)
    else:
        commission = round(basis * pct / 100, 2) if pct is not None else None
    net = float(b.ota_net_payout) if b.ota_net_payout is not None else (
        round(gross - (commission or 0), 2) if commission is not None else None)
    guest = b.guest
    return {
        "booking_id": b.booking_id,
        "guest_name": guest.name if guest else None,
        "phone": guest.phone if guest else None,
        "source": b.booking_source,
        "ota_booking_id": b.ota_booking_id,
        "status": b.status,
        "check_in": str(b.check_in),
        "check_out": str(b.check_out),
        "gross": round(gross, 2),
        "commission_percent": pct,
        "commission_amount": commission,
        "net_payout": net,
    }


# ---------------------------------------------------------------------------
# Payout reconciliation
# ---------------------------------------------------------------------------
def _expected_payout(db, code, dfrom, dto):
    """Expected payout for a channel over [dfrom, dto] (by check_in): sum of gross and of
    (gross - commission) across live OTA bookings. Uses the ota_net_payout snapshot when present,
    else falls back to gross * (1 - commission%/100) from the channel config."""
    rows = (db.query(Booking)
            .filter(Booking.booking_source == code, Booking.status.in_(_LIVE_STATUSES),
                    Booking.check_in >= dfrom, Booking.check_in <= dto)
            .all())
    gross = expected = 0.0
    default_pct = commission_for(db, code)
    for b in rows:
        g = float(b.grand_total or b.total_amount or 0)
        if b.ota_net_payout is not None:
            net = float(b.ota_net_payout)
        else:
            pct = float(b.ota_commission_percent) if b.ota_commission_percent is not None else default_pct
            net = g - g * pct / 100
        gross += g
        expected += net
    return round(gross, 2), round(expected, 2), len(rows)


def reconcile_payouts(db, channel=None, dfrom=None, dto=None) -> dict:
    """Per-OTA expected payout (gross - commission) vs actual bank settlements, mismatch flagged.

    `channel` limits to one code; otherwise every OTA source is reported. Bookings are counted by
    check_in in [dfrom, dto]; settlements by created_at date in the same window. A mismatch beyond
    ₹1 (rounding tolerance) sets `mismatch=true`."""
    codes = [channel] if channel else list(ota_sources(db))
    rows = []
    tot_gross = tot_expected = tot_actual = 0.0
    for code in codes:
        gross, expected, n = _expected_payout(db, code, dfrom, dto)
        q = db.query(OtaSettlement).filter(OtaSettlement.channel_code == code)
        if dfrom is not None:
            q = q.filter(func_date(OtaSettlement.created_at) >= dfrom)
        if dto is not None:
            q = q.filter(func_date(OtaSettlement.created_at) <= dto)
        actual = round(sum(float(s.amount or 0) for s in q.all()), 2)
        diff = round(actual - expected, 2)
        rows.append({
            "channel": code,
            "bookings": n,
            "gross": gross,
            "expected_payout": expected,
            "actual_settled": actual,
            "difference": diff,
            "mismatch": abs(diff) > 1.0,
        })
        tot_gross += gross
        tot_expected += expected
        tot_actual += actual
    totals = {
        "gross": round(tot_gross, 2),
        "expected_payout": round(tot_expected, 2),
        "actual_settled": round(tot_actual, 2),
        "difference": round(tot_actual - tot_expected, 2),
    }
    return {
        "from": str(dfrom) if dfrom else None,
        "to": str(dto) if dto else None,
        "rows": rows,
        "totals": totals,
    }


def func_date(col):
    """`DATE(col)` for cross-date comparisons (defined here to avoid importing func at module top)."""
    from sqlalchemy import func
    return func.date(col)


def record_settlement(db, channel_code, amount, *, period_start=None, period_end=None,
                      reference=None, notes=None, user_id=None, commit=False) -> OtaSettlement:
    s = OtaSettlement(channel_code=channel_code, amount=round(float(amount), 2),
                      period_start=period_start, period_end=period_end,
                      reference=reference, notes=notes, created_by=user_id)
    db.add(s)
    db.flush()
    if commit:
        db.commit()
    return s


def serialize_settlement(s: OtaSettlement) -> dict:
    return {
        "id": s.id,
        "channel_code": s.channel_code,
        "period_start": str(s.period_start) if s.period_start else None,
        "period_end": str(s.period_end) if s.period_end else None,
        "reference": s.reference,
        "amount": float(s.amount or 0),
        "notes": s.notes,
        "created_at": s.created_at.isoformat() if s.created_at else None,
    }


# ---------------------------------------------------------------------------
# Email auto-draft (IMAP) — optional, off unless OTA_IMAP_* env set
# ---------------------------------------------------------------------------
def imap_config():
    """IMAP connection config from env, or None when not configured (poller no-ops)."""
    host = os.getenv("OTA_IMAP_HOST")
    user = os.getenv("OTA_IMAP_USER")
    password = os.getenv("OTA_IMAP_PASSWORD")
    if not (host and user and password):
        return None
    return {
        "host": host,
        "port": int(os.getenv("OTA_IMAP_PORT", "993") or 993),
        "user": user,
        "password": password,
        "folder": os.getenv("OTA_IMAP_FOLDER", "INBOX"),
        "ssl": str(os.getenv("OTA_IMAP_SSL", "true")).strip().lower() not in ("false", "0", "no"),
    }


def is_imap_configured() -> bool:
    return imap_config() is not None


def poller_status(db=None) -> dict:
    """Read-only surface for the admin UI (no secrets)."""
    st = {"imap_configured": is_imap_configured()}
    if db is not None:
        st["channels_enabled"] = sorted(c.code for c in get_channels(db) if c.mailbox_parsing_enabled)
    return st


# --- header / body helpers ---
def _decode(value) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return str(value)


def _body_text(msg) -> str:
    """Best plain-text body from an email.message.Message (prefers text/plain; strips HTML tags
    from text/html as a fallback)."""
    def _payload(part):
        try:
            raw = part.get_payload(decode=True)
            if raw is None:
                return ""
            charset = part.get_content_charset() or "utf-8"
            return raw.decode(charset, errors="replace")
        except Exception:
            return ""

    plain, html = "", ""
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            disp = str(part.get("Content-Disposition") or "")
            if "attachment" in disp.lower():
                continue
            if ctype == "text/plain":
                plain += _payload(part)
            elif ctype == "text/html":
                html += _payload(part)
    else:
        if msg.get_content_type() == "text/html":
            html = _payload(msg)
        else:
            plain = _payload(msg)
    text = plain or _strip_html(html)
    return text


def _strip_html(html: str) -> str:
    if not html:
        return ""
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    return re.sub(r"[ \t]+", " ", text)


# --- channel + kind detection ---
def detect_channel(from_addr: str, subject: str, body: str = "") -> str | None:
    """Which OTA an email is from. Scans From + Subject AND a bounded slice of the BODY, because a
    FORWARDED OTA email carries your own address in the envelope From and often no brand in the
    subject — the real sender ("From: donotreply@yatra.com") lives in the forwarded body.

    Brand tokens are checked BEFORE the shared go-mmt.com domain, since MakeMyTrip and Goibibo both
    send from go-mmt.com and only the brand name can tell them apart."""
    hay = f"{from_addr} {subject}\n{body[:3000]}".lower()
    if "goibibo" in hay:
        return "goibibo"
    if "makemytrip" in hay or "make my trip" in hay:
        return "makemytrip"
    if "booking.com" in hay:
        return "booking_com"
    if "agoda" in hay:
        return "agoda"
    if "yatra" in hay or "travelguru" in hay or "desiya" in hay:
        return "yatra"
    # Fall back to sender domains for anything without an obvious brand token.
    for code, meta in _CHANNEL_DEFAULTS.items():
        for dom in meta["domains"]:
            if dom in hay:
                return code
    return None


def detect_kind(subject: str, body: str) -> str:
    """Confirmation vs cancellation.

    The SUBJECT is the reliable signal — an OTA cancellation says so in the subject ("Booking
    Cancelled", "Cancellation of booking …"), whereas a confirmation *voucher* ALWAYS carries a
    "Cancellation Policy" / "Free Cancellation if you cancel before …" section and conditional
    "if the booking is cancelled later" notes in its body. The old code matched "cancel" anywhere
    in subject+body, so it mislabelled essentially every real booking as a cancellation.

    So: decide on the subject; fall back to the body only for an UNAMBIGUOUS, non-conditional
    "booking has been cancelled" statement (never the policy boilerplate)."""
    if re.search(r"\bcancel(?:l?ed|lation|ling)?\b", subject or "", re.IGNORECASE):
        return "cancellation"
    body = body or ""
    m = re.search(r"(?:booking|reservation)\s+(?:has\s+been|is\s+now|stands)\s+cancell?ed",
                  body, re.IGNORECASE)
    if m:
        # Guard against "if the booking has been cancelled later …" future-tense clauses that
        # appear inside confirmation vouchers.
        preceding = body[max(0, m.start() - 12):m.start()].lower()
        if "if " not in preceding:
            return "cancellation"
    return "confirmation"


# --- field extraction ---
_DATE_FORMATS = ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d %b %Y", "%d %B %Y",
                 "%b %d, %Y", "%B %d, %Y", "%d %b %y", "%d %B %y", "%a, %d %b %Y")


def _parse_date_loose(s):
    if not s:
        return None
    s = re.sub(r"[*|]", " ", s)                   # drop *bold* / |cell| markers from forwarded mail
    s = s.strip().strip(".,")
    s = s.replace("'", "")                        # Go-MMT "26 Aug '26" -> "26 Aug 26"
    s = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", s)  # 15th -> 15
    s = re.sub(r"\s+", " ", s).strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _first(patterns, text, flags=re.IGNORECASE):
    for pat in patterns:
        m = re.search(pat, text, flags)
        if m:
            return m.group(1).strip()
    return None


def _parse_common(text: str) -> dict:
    """Label-driven field extraction shared by all OTA parsers. OTAs vary in wording, so each
    field tries a few common labels. Missing fields stay None (draft is still created for manual
    completion)."""
    booking_id = _first([
        r"(?:booking\s*id|booking\s*(?:reference|ref|no\.?|number)|reservation\s*(?:id|number|no\.?)|confirmation\s*(?:number|no\.?|id)|itinerary\s*(?:id|no\.?))\s*[:\-#]?\s*([A-Za-z0-9\-\/]{4,40})",
    ], text)
    guest = _first([
        r"(?:guest\s*name|guest|lead\s*guest|customer\s*name|name\s*of\s*guest|primary\s*guest)\s*[:\-]?\s*([A-Za-z][A-Za-z .'\-]{1,60})",
    ], text)
    phone = _first([
        r"(?:phone|mobile|contact\s*(?:number|no\.?))\s*[:\-]?\s*(\+?\d[\d \-]{7,14}\d)",
    ], text)
    email_addr = _first([r"([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,})"], text)
    checkin = _first([
        r"(?:check[\-\s]?in|arrival|from\s*date|check in date)\s*[:\-]?\s*([0-9A-Za-z ,\/\-]{6,20})",
    ], text)
    checkout = _first([
        r"(?:check[\-\s]?out|departure|to\s*date|check out date)\s*[:\-]?\s*([0-9A-Za-z ,\/\-]{6,20})",
    ], text)
    room = _first([
        r"(?:room\s*type|room\s*category|room|accommodation)\s*[:\-]?\s*([A-Za-z0-9 ,'&\-]{2,60})",
    ], text)
    amount = _first([
        r"(?:total\s*(?:amount|payable|price)|grand\s*total|booking\s*amount|amount\s*paid|payable|total)\s*[:\-]?\s*(?:INR|Rs\.?|₹)?\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)",
    ], text)
    amount_val = None
    if amount:
        try:
            amount_val = round(float(amount.replace(",", "")), 2)
        except ValueError:
            amount_val = None
    return {
        "ota_booking_id": booking_id,
        "guest_name": (guest.strip() if guest else None),
        "phone": phone,
        "email": email_addr,
        "check_in": _parse_date_loose(checkin) if checkin else None,
        "check_out": _parse_date_loose(checkout) if checkout else None,
        "room_type_hint": (room.strip() if room else None),
        "amount": amount_val,
    }


# Per-OTA parsers. They currently all delegate to the common label extractor; kept as distinct
# seams so a channel whose emails need special handling can override without touching the others.
_GO_MMT_DATE = r"(\d{1,2}\s+[A-Za-z]{3,9}\s+'?\d{2,4})"


def _num(s):
    """'2,415.0' -> 2415.0, or None."""
    if not s:
        return None
    try:
        return round(float(s.replace(",", "").strip()), 2)
    except ValueError:
        return None


def _commission_net(text: str) -> dict:
    """The ACTUAL commission (incl GST) and net payout the voucher states — so the PMS reads the
    real numbers per booking instead of applying a configured channel %. Works for Go-MMT ("(B) …
    Commission (including GST) (5+6) ₹271.4" / "Payable to Property (A-B-C) ₹929.2") and Yatra
    ("(B) … Commission (including GST) 271.40" / "… to pay Hotel (A - B - C - D) 929.69"). Skips the
    "(5+6)" / "(A-B-C)" formula refs and tolerates missing currency symbols and *bold* markers."""
    out = {}
    _ref = r"(?:\([A-Za-z0-9\s\-+]+\))?"          # a formula reference like (5+6) or (A - B - C - D)
    _cur = r"[\s:*|]*(?:₹|rs\.?|inr)?\s*"
    comm = _num(_first([r"commission\s*\(including\s*gst\)\s*" + _ref + _cur + r"([\d,]+\.?\d*)"], text))
    net = _num(_first([r"(?:payable\s+to\s+property|to\s+pay\s+hotel)\s*" + _ref + _cur + r"([\d,]+\.?\d*)"], text))
    if comm is not None:
        out["commission_amount"] = comm
    if net is not None:
        out["net_payout"] = net
    return out


def _occupancy(text: str) -> dict:
    """Adults / children from an OTA email. Handles both "2 Adults" (Go-MMT) and "Adults: 2"
    (Yatra), tolerating *bold* markers. Only sets a key when found (so the draft keeps its
    default otherwise)."""
    out = {}
    a = _first([r"(\d+)\s*adults?\b", r"\badults?\b[\s:*|]*(\d+)"], text)
    c = _first([r"(\d+)\s*child(?:ren)?\b", r"\bchild(?:ren)?\b[\s:*|]*(\d+)"], text)
    if a and a.isdigit():
        out["adults"] = int(a)
    if c and c.isdigit():
        out["children"] = int(c)
    return out


def _parse_go_mmt(text: str) -> dict:
    """MakeMyTrip & Goibibo share the Go-MMT "Hotelier Voucher" layout, where each label sits on its
    own line ABOVE its value and years use an apostrophe ("26 Aug '26"). The generic label=value
    parser mis-reads it — e.g. "PRIMARY GUEST DETAILS" yields guest "DETAILS", and "room" matches
    "2 Adults". So parse it explicitly here, falling back to the common parser for anything unfound.
    The guest phone/email are deliberately NOT in the voucher (Go-MMT masks them), so they stay
    None and the desk collects them at check-in."""
    out = _parse_common(text)

    # Guest: the value under "PRIMARY GUEST DETAILS", skipping loyalty badges / brand noise. In the
    # PDF the badge ("goTribe") sits on the SAME line as the name, so strip leading noise words too.
    m = re.search(r"primary\s+guest\s+details(.*?)(?:check[\-\s]?in|booking\s+id|total\s+no)",
                  text, re.IGNORECASE | re.DOTALL)
    if m:
        _noise = {"gotribe", "goibibo", "makemytrip", "go", "mmt", "go-mmt", "details",
                  "primary", "guest"}
        for line in re.split(r"[\n\r]+", m.group(1)):
            words = line.strip().split()
            while words and words[0].lower().strip(".-") in _noise:
                words.pop(0)
            cand = " ".join(words)
            if cand and re.fullmatch(r"[A-Za-z][A-Za-z .'\-]{1,60}", cand):
                out["guest_name"] = cand
                break

    bid = _first([r"booking\s*id[^A-Za-z0-9]*([A-Z0-9]{6,40})"], text)
    if bid:
        out["ota_booking_id"] = bid

    # Check-out carries "(N Night…)"; check-in is the date token immediately before it.
    all_dates = re.findall(_GO_MMT_DATE, text)
    co = _first([_GO_MMT_DATE + r"\s*\(\s*\d+\s*night"], text)
    if co:
        out["check_out"] = _parse_date_loose(co)
        if co in all_dates and all_dates.index(co) >= 1:
            out["check_in"] = _parse_date_loose(all_dates[all_dates.index(co) - 1])
    if not (out.get("check_in") and out.get("check_out")):
        seg = re.search(r"check[\-\s]?in.*", text, re.IGNORECASE | re.DOTALL)
        toks = re.findall(_GO_MMT_DATE, seg.group(0)) if seg else []
        if len(toks) >= 2:
            out["check_in"] = out.get("check_in") or _parse_date_loose(toks[0])
            out["check_out"] = out.get("check_out") or _parse_date_loose(toks[1])

    room = _first([r"\d+\s*[xX]\s*([A-Za-z0-9][A-Za-z0-9 /()&\-]*?room)\b"], text)
    if room:
        out["room_type_hint"] = re.sub(r"\s+", " ", room).strip()

    # Amount = "Property Gross Charges" (what the hotel invoices the guest), else the room grand
    # total. Currency may be ₹ / Rs / INR depending on how the HTML was flattened.
    _cur = r"(?:₹|rs\.?|inr)?\s*"
    gross = _num(_first([r"property\s+gross\s+charges\s*" + _cur + r"([\d,]+\.?\d*)",
                         r"grand\s*total\s*" + _cur + r"([\d,]+\.?\d*)"], text))
    if gross is not None:
        out["amount"] = gross
    out.update(_occupancy(text))
    out.update(_commission_net(text))
    return out


def parse_makemytrip(text: str) -> dict:
    return _parse_go_mmt(text)


def parse_goibibo(text: str) -> dict:
    return _parse_go_mmt(text)


def parse_yatra(text: str) -> dict:
    """Yatra / Travelguru emails are a two-column label/value table. Each value is read as everything
    between its label and the NEXT known label — robust whether the HTML flattens onto one line or
    keeps each row separate. Handles BOTH layouts: the booking voucher ("Name of Guest", "Check in
    date", "Room Name") and the cancellation notice ("Name of Traveller", "Booking Id", "Room Type",
    "Cancelled from"). The guest phone is not shared."""
    out = _parse_common(text)

    def between(start, *ends):
        # Forwarded emails wrap labels/values in *bold* markers, so consume/strip *, |, : around
        # the value rather than treating them as part of it.
        end_alt = "|".join(ends) if ends else r"\Z"
        m = re.search(start + r"[\s:*|]*(.+?)\s*(?:" + end_alt + r")",
                      text, re.IGNORECASE | re.DOTALL)
        val = re.sub(r"\s+", " ", m.group(1)) if m else ""
        return val.strip(" *|:•·\t\r\n")[:60].strip() or None

    # Booking id: voucher uses "VOUCHER NUMBER P0002451913"; the cancellation uses "Booking Id:YAT
    # 0012061597" (letter prefix + a stray space). Capture prefix+digits and drop internal spaces.
    bid = _first([r"voucher\s*number[\s:*|]*([A-Za-z]{0,5}\s?\d{5,20})",
                  r"booking\s*id[\s:*|]*([A-Za-z]{0,5}\s?\d{5,20})"], text)
    if bid:
        out["ota_booking_id"] = re.sub(r"\s+", "", bid)

    guest = between(r"name\s+of\s+guest", r"booking\s+date", r"check\s*in") \
        or between(r"name\s+of\s+traveller", r"cancellation\s+id", r"booking\s+id", r"room\s+type", r"hotel\s+name")
    if guest and re.search(r"[A-Za-z]", guest):
        out["guest_name"] = guest

    ci = between(r"check\s*in\s*date", r"check\s*out\s*date")
    co = between(r"check\s*out\s*date", r"check\s*in\s*time", r"check\s*out\s*time", r"duration")
    if ci:
        out["check_in"] = _parse_date_loose(ci) or out.get("check_in")
    if co:
        out["check_out"] = _parse_date_loose(co) or out.get("check_out")
    # Cancellation notice labels both dates "Cancelled from" (the 2nd is really the check-out).
    if not (out.get("check_in") and out.get("check_out")):
        cf = re.findall(r"cancell?ed\s+from[\s:*|]*([0-9]{1,2}\s+[A-Za-z]{3,9}\s+'?\d{2,4})",
                        text, re.IGNORECASE)
        if cf:
            out["check_in"] = out.get("check_in") or _parse_date_loose(cf[0])
            if len(cf) >= 2:
                out["check_out"] = out.get("check_out") or _parse_date_loose(cf[1])

    room = between(r"room\s*name", r"type\s+of\s+room", r"number\s+of\s+rooms") \
        or between(r"type\s+of\s+room", r"number\s+of\s+rooms") \
        or between(r"room\s*type", r"cancelled\s+from", r"room\s*nights", r"number\s+of\s+rooms")
    if room:
        out["room_type_hint"] = room

    # Amount = "(A) Hotel Gross Charges" (what the hotel invoices the guest), else room charges.
    _cur = r"[\s:*|]*(?:₹|rs\.?|inr)?\s*"
    gross = _num(_first([r"hotel\s+gross\s+charges" + _cur + r"([\d,]+\.?\d*)",
                         r"total\s+room\s+charges" + _cur + r"([\d,]+\.?\d*)"], text))
    if gross is not None:
        out["amount"] = gross
    out.update(_occupancy(text))
    out.update(_commission_net(text))
    return out


def parse_booking_com(text: str) -> dict:
    return _parse_common(text)


def parse_agoda(text: str) -> dict:
    return _parse_common(text)


def parse_other(text: str) -> dict:
    return _parse_common(text)


_PARSERS = {
    "makemytrip": parse_makemytrip,
    "goibibo": parse_goibibo,
    "booking_com": parse_booking_com,
    "agoda": parse_agoda,
    "yatra": parse_yatra,
    "other_ota": parse_other,
}


def parse_email_bytes(raw: bytes, force_channel=None) -> dict | None:
    """Parse a raw RFC822 email into a normalized draft dict. Returns None when no OTA channel can
    be detected (and no `force_channel` given). PURE — no DB. Testable with `.eml` fixtures."""
    msg = email.message_from_bytes(raw)
    from_addr = _decode(msg.get("From"))
    subject = _decode(msg.get("Subject"))
    message_id = (msg.get("Message-ID") or msg.get("Message-Id") or "").strip() or None
    body = _body_text(msg)
    channel = force_channel or detect_channel(from_addr, subject, body)
    if channel is None:
        return None
    parser = _PARSERS.get(channel, parse_other)
    fields = parser(body)
    fields["channel_code"] = channel
    fields["kind"] = detect_kind(subject, body)
    fields["message_id"] = message_id
    fields["subject"] = subject
    # keep a bounded raw snippet for audit / manual fallback
    fields["raw_source"] = (subject + "\n\n" + body)[:4000]
    return fields


def _upsert_draft(db, fields: dict) -> tuple[OtaDraftBooking, bool]:
    """Insert or update a draft from a parsed dict. Dedupes on message_id first, then on
    (channel_code, ota_booking_id, kind). Returns (draft, created)."""
    channel = fields["channel_code"]
    kind = fields.get("kind", "confirmation")
    ota_id = fields.get("ota_booking_id")
    msg_id = fields.get("message_id")

    existing = None
    if msg_id:
        existing = db.query(OtaDraftBooking).filter(OtaDraftBooking.message_id == msg_id).first()
    if existing is None and ota_id:
        existing = (db.query(OtaDraftBooking)
                    .filter(OtaDraftBooking.channel_code == channel,
                            OtaDraftBooking.ota_booking_id == ota_id,
                            OtaDraftBooking.kind == kind)
                    .first())
    created = existing is None
    d = existing or OtaDraftBooking(channel_code=channel, kind=kind, status="pending")
    if created:
        # snapshot the channel commission at parse time
        d.commission_percent = commission_for(db, channel)
        db.add(d)
    d.ota_booking_id = ota_id
    d.guest_name = fields.get("guest_name")
    d.phone = fields.get("phone")
    d.email = fields.get("email")
    d.check_in = fields.get("check_in")
    d.check_out = fields.get("check_out")
    d.room_type_hint = fields.get("room_type_hint")
    d.adults = int(fields.get("adults") or 1)
    d.children = int(fields.get("children") or 0)
    d.amount = fields.get("amount")
    d.commission_amount = fields.get("commission_amount")
    d.net_payout = fields.get("net_payout")
    d.message_id = msg_id
    d.raw_source = fields.get("raw_source")
    db.flush()
    return d, created


# The bed-size words carry the strongest room-type signal; weighted ×3 when matching the OTA room
# wording to a PMS room type. Mirrors the desk's OtaDraftsViewModel.PickRoomType.
_BED_WORDS = frozenset({"single", "double", "triple", "twin", "quad", "family",
                        "deluxe", "deeluxe", "suite", "standard"})


def _rt_words(s: str) -> list:
    return [w for w in re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).split() if w]


def _pick_room_type_id(db, hint):
    """Map the OTA room wording (e.g. "Double Non AC Room") to an active PMS room type id, weighting
    the bed-size word most. Returns None when there are no active room types. Server-side twin of the
    desk's bed-size picker, so an auto-confirmed booking lands on the same room type the desk would pick."""
    types = db.query(RoomType).filter(RoomType.is_active == True).all()  # noqa: E712
    if not types:
        return None
    if not hint or not hint.strip():
        return types[0].room_type_id
    hint_words = _rt_words(hint)
    best, best_score = types[0], -1
    for t in types:
        tw = set(_rt_words(t.name))
        score = sum(3 if w in _BED_WORDS else 1 for w in hint_words if w in tw)
        if score > best_score:
            best, best_score = t, score
    return best.room_type_id


def _auto_confirm_enabled() -> bool:
    return os.getenv("OTA_AUTO_CONFIRM_ENABLED", "true").strip().lower() not in ("false", "0", "no")


def auto_confirm_draft(db, d, user=None):
    """Turn a *confident* pending confirmation draft into a real booking, WITHOUT human review.

    Confident = has dates + guest name + a resolvable room type. Anything less (or any failure such as
    no availability) leaves the draft PENDING for the desk to handle — never raises. Returns the new
    (or already-linked) booking id, or None. Idempotent: a draft whose ota_booking_id already has a
    live booking is linked, not duplicated — which is what makes the racing pollers safe.
    Gated by OTA_AUTO_CONFIRM_ENABLED (default on)."""
    if not _auto_confirm_enabled():
        return None
    if d.kind != "confirmation" or d.status != "pending" or d.linked_booking_id:
        return None
    if not (d.guest_name and d.check_in and d.check_out):
        return None

    # Already booked under this OTA id? Link + confirm, don't create a second one.
    if d.ota_booking_id:
        existing = (db.query(Booking)
                    .filter(Booking.ota_booking_id == d.ota_booking_id,
                            Booking.status.in_(_LIVE_STATUSES))
                    .first())
        if existing is not None:
            d.status = "confirmed"
            d.linked_booking_id = existing.booking_id
            db.flush()
            return existing.booking_id

    room_type_id = _pick_room_type_id(db, d.room_type_hint)
    if not room_type_id:
        return None

    # Persist the draft as PENDING before attempting the booking. create_desk_booking rolls the
    # session back on any failure, which would otherwise discard the just-upserted draft and rob the
    # desk of its manual fallback. Committing here pins it; a failed attempt rolls back only to this.
    db.commit()

    try:
        from schemas import DeskBookingCreate, BookingItemCreate
        from routers.reception import create_desk_booking
        commission_pct = float(d.commission_percent) if d.commission_percent is not None else None
        booking_req = DeskBookingCreate(
            rooms=[BookingItemCreate(room_type_id=room_type_id, quantity=1)],
            guest_name=d.guest_name,
            phone=(d.phone or _ota_placeholder_phone(d.ota_booking_id)),
            email=(d.email or None),
            check_in=d.check_in,
            check_out=d.check_out,
            check_in_time=time(12, 0),
            adults=int(d.adults or 1),
            children=int(d.children or 0),
            booking_source=d.channel_code,
            ota_booking_id=d.ota_booking_id,
            ota_commission_percent=commission_pct,
            ota_commission_amount=float(d.commission_amount) if d.commission_amount is not None else None,
            ota_net_payout=float(d.net_payout) if d.net_payout is not None else None,
        )
        # System actor (user=None): create_desk_booking's audit + write_audit already tolerate it,
        # exactly like the overstay auto-biller.
        result = create_desk_booking(booking_req, db=db, user=None)
    except Exception as e:
        # No availability, pricing gap, mapping miss — leave it PENDING for the desk. Never abort the poll.
        logger.info(f"OTA auto-confirm skipped draft {d.id} ({d.channel_code}/{d.ota_booking_id}): {e}")
        try:
            db.rollback()
        except Exception:
            pass
        return None

    d.status = "confirmed"
    d.linked_booking_id = result["booking_id"]
    try:
        from utils.audit import write_audit
        write_audit(db, None, "ota.auto_confirm", "ota_draft_booking", d.id,
                    after={"booking_id": result["booking_id"], "channel": d.channel_code,
                           "ota_booking_id": d.ota_booking_id, "room_type_id": room_type_id},
                    client="ota_poller")
    except Exception:
        logger.exception("OTA auto-confirm audit failed")
    db.flush()
    return result["booking_id"]


def ingest_email_bytes(db, raw: bytes, force_channel=None, commit=False) -> dict:
    """Parse + upsert one email into a draft. Auto-flags/cancels a matching booking when the email is
    a cancellation, and auto-confirms a *confident* new-booking draft. Best-effort; caller commits."""
    fields = parse_email_bytes(raw, force_channel=force_channel)
    if fields is None:
        return {"parsed": False, "reason": "no OTA channel detected"}
    channel = fields["channel_code"]
    # honour the per-channel mailbox toggle (a channel can be tracked but not auto-parsed)
    ch = get_channel(db, channel)
    if ch is not None and not ch.mailbox_parsing_enabled and force_channel is None:
        return {"parsed": True, "skipped": True, "reason": f"mailbox parsing disabled for {channel}"}
    d, created = _upsert_draft(db, fields)
    flagged_booking_id = None
    cancelled_booking_id = None
    if fields.get("kind") == "cancellation" and fields.get("ota_booking_id"):
        b = (db.query(Booking)
             .filter(Booking.ota_booking_id == fields["ota_booking_id"],
                     Booking.status.in_(_LIVE_STATUSES))
             .first())
        if b is not None:
            flagged_booking_id = b.booking_id
            d.linked_booking_id = b.booking_id
            d.status = "flagged"
            # The OTA is authoritative: a cancellation for a stay that has NOT arrived yet cancels
            # the booking outright — it then drops off the desk's Arrivals (status filter) and
            # check-in is refused (status guard). A guest already checked in can't be un-checked-in,
            # so leave that one flagged for the desk to handle. No Razorpay refund — the OTA refunds.
            if b.status == "confirmed":
                b.status = "cancelled"
                b.cancelled_at = datetime.now()
                b.cancel_reason = f"OTA cancellation ({channel}) {fields['ota_booking_id']}"
                cancelled_booking_id = b.booking_id
                try:
                    from utils.audit import write_audit
                    write_audit(db, None, "ota.auto_cancel", "booking", b.booking_id,
                                after={"channel": channel, "ota_booking_id": fields["ota_booking_id"]},
                                client="ota_poller")
                except Exception:
                    logger.exception("OTA auto-cancel audit failed")

    # A confident NEW-booking draft is auto-created into a real booking (owner: auto-confirm OTA
    # bookings). Unresolvable drafts stay pending for the desk. auto_confirm_draft never raises.
    auto_booking_id = None
    if fields.get("kind") == "confirmation":
        auto_booking_id = auto_confirm_draft(db, d, user=None)

    db.flush()
    if commit:
        db.commit()
    return {"parsed": True, "created": created, "draft_id": d.id, "channel": channel,
            "kind": fields.get("kind"), "flagged_booking_id": flagged_booking_id,
            "cancelled_booking_id": cancelled_booking_id,
            "auto_confirmed_booking_id": auto_booking_id}


def poll_mailbox(db, limit=50) -> dict:
    """Connect to the configured IMAP mailbox, ingest UNSEEN OTA emails into drafts, mark them seen.
    No-ops (returns configured=False) when OTA_IMAP_* env is unset. Best-effort: connection/parse
    errors are logged and summarised, never raised."""
    cfg = imap_config()
    if cfg is None:
        return {"configured": False, "processed": 0, "created": 0, "drafts": []}

    # Single-flight across workers + desk polls: auto-confirm makes a double-poll unsafe (two real
    # bookings from one email), so hold an advisory lock for the whole poll and skip if it's taken.
    locked = False
    try:
        locked = bool(db.execute(text("SELECT pg_try_advisory_lock(:k)"),
                                 {"k": _POLL_LOCK_KEY}).scalar())
    except Exception as e:
        logger.warning(f"OTA IMAP poll: advisory-lock check failed ({e}); proceeding without it")
    if not locked:
        logger.info("OTA IMAP poll: another instance holds the lock, skipping")
        return {"configured": True, "processed": 0, "created": 0, "skipped": "locked", "drafts": []}

    ensure_channels(db)
    processed = created = errors = 0
    drafts = []
    conn = None
    try:
        conn = imaplib.IMAP4_SSL(cfg["host"], cfg["port"]) if cfg["ssl"] else imaplib.IMAP4(cfg["host"], cfg["port"])
        conn.login(cfg["user"], cfg["password"])
        conn.select(cfg["folder"])
        typ, data = conn.search(None, "UNSEEN")
        if typ != "OK":
            return {"configured": True, "processed": 0, "created": 0, "error": "search failed"}
        ids = (data[0].split() if data and data[0] else [])[:limit]
        for num in ids:
            try:
                typ, msg_data = conn.fetch(num, "(RFC822)")
                if typ != "OK" or not msg_data or not msg_data[0]:
                    continue
                raw = msg_data[0][1]
                res = ingest_email_bytes(db, raw, commit=False)
                processed += 1
                if res.get("created"):
                    created += 1
                if res.get("draft_id"):
                    drafts.append(res["draft_id"])
                conn.store(num, "+FLAGS", "\\Seen")
            except Exception as e:  # per-message failure shouldn't abort the batch
                errors += 1
                logger.warning(f"OTA IMAP: failed to process message {num!r}: {e}")
        db.commit()
    except Exception as e:
        logger.error(f"OTA IMAP poll failed: {e}")
        try:
            db.rollback()
        except Exception:
            pass
        return {"configured": True, "processed": processed, "created": created, "error": str(e)}
    finally:
        if conn is not None:
            try:
                conn.logout()
            except Exception:
                pass
        # Release the advisory lock on THIS session — a pooled request session would otherwise carry
        # the lock back to the pool and starve every later poll.
        try:
            db.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": _POLL_LOCK_KEY})
            db.commit()
        except Exception as e:
            logger.warning(f"OTA IMAP poll: advisory-unlock failed: {e}")
    return {"configured": True, "processed": processed, "created": created,
            "errors": errors, "drafts": drafts}


def serialize_draft(d: OtaDraftBooking, db=None) -> dict:
    # A cancellation email carries no gross, so d.amount is None — show the ORIGINAL stay value from
    # the booking it flagged, so the row isn't a bare "—".
    amount = float(d.amount) if d.amount is not None else None
    if amount is None and d.linked_booking_id and db is not None:
        b = db.query(Booking).filter(Booking.booking_id == d.linked_booking_id).first()
        if b is not None and b.grand_total is not None:
            amount = float(b.grand_total)
    return {
        "id": d.id,
        "channel_code": d.channel_code,
        "ota_booking_id": d.ota_booking_id,
        "kind": d.kind,
        "status": d.status,
        "guest_name": d.guest_name,
        "phone": d.phone,
        "email": d.email,
        "check_in": str(d.check_in) if d.check_in else None,
        "check_out": str(d.check_out) if d.check_out else None,
        "room_type_hint": d.room_type_hint,
        "adults": int(d.adults or 1),
        "children": int(d.children or 0),
        "amount": amount,
        "commission_percent": float(d.commission_percent) if d.commission_percent is not None else None,
        "commission_amount": float(d.commission_amount) if d.commission_amount is not None else None,
        "net_payout": float(d.net_payout) if d.net_payout is not None else None,
        "linked_booking_id": d.linked_booking_id,
        "created_at": d.created_at.isoformat() if d.created_at else None,
    }
