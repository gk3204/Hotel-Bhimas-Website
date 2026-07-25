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
from datetime import datetime, date
from email.header import decode_header, make_header

from models import Booking, OtaChannel, OtaSettlement, OtaDraftBooking

logger = logging.getLogger(__name__)

# Booking statuses that count as a real (non-cancelled) stay — matches routers/reports.py.
_LIVE_STATUSES = ("confirmed", "checked_in", "checked_out")

# The OTA channels recognised today (prompt 05 booking_source vocab + prompt 17 `other_ota`).
OTA_SOURCES = ("makemytrip", "goibibo", "booking_com", "agoda", "other_ota")

# Default display names + email-sender domains used to auto-detect a channel from an email.
_CHANNEL_DEFAULTS = {
    "makemytrip":  {"display_name": "MakeMyTrip",  "domains": ("makemytrip.com", "go-mmt.com", "ingommt.com")},
    "goibibo":     {"display_name": "Goibibo",     "domains": ("goibibo.com", "go-mmt.com")},
    "booking_com": {"display_name": "Booking.com", "domains": ("booking.com",)},
    "agoda":       {"display_name": "Agoda",       "domains": ("agoda.com",)},
    "other_ota":   {"display_name": "Other OTA",   "domains": ()},
}


def is_ota_source(source) -> bool:
    return (source or "") in OTA_SOURCES


# ---------------------------------------------------------------------------
# Per-OTA config (ota_channels)
# ---------------------------------------------------------------------------
def ensure_channels(db, commit=False):
    """Make sure every recognised channel has a config row (defensive — migration 015 seeds them,
    but a fresh create_all DB may not have run the seed). Never overwrites an existing row."""
    existing = {c.code for c in db.query(OtaChannel).all()}
    created = False
    for code in OTA_SOURCES:
        if code not in existing:
            db.add(OtaChannel(code=code, display_name=_CHANNEL_DEFAULTS[code]["display_name"],
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
def apply_ota_fields(db, booking, source, ota_booking_id=None, commission_percent_override=None):
    """Stamp the OTA tracking snapshot onto a freshly-priced booking.

    commission % = the override (if the desk typed one) else the channel's configured default.
    net payout   = gross booking value - commission (the amount the hotel expects the OTA to remit).
    Gross = booking.grand_total (desk bookings carry no convenience fee, so grand_total == room total).
    No-ops for non-OTA sources. Joins the caller's transaction (no commit here)."""
    if not is_ota_source(source):
        return booking
    pct = commission_percent_override
    if pct is None:
        pct = commission_for(db, source)
    pct = round(float(pct or 0), 2)
    gross = float(booking.grand_total or booking.total_amount or 0)
    booking.ota_booking_id = (str(ota_booking_id).strip() or None) if ota_booking_id else None
    booking.ota_commission_percent = pct
    booking.ota_net_payout = round(gross - gross * pct / 100, 2)
    return booking


def serialize_ota_booking(db, b: Booking) -> dict:
    gross = float(b.grand_total or b.total_amount or 0)
    pct = float(b.ota_commission_percent) if b.ota_commission_percent is not None else None
    commission = round(gross * pct / 100, 2) if pct is not None else None
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
    codes = [channel] if channel else list(OTA_SOURCES)
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
def detect_channel(from_addr: str, subject: str) -> str | None:
    hay = f"{from_addr} {subject}".lower()
    for code, meta in _CHANNEL_DEFAULTS.items():
        for dom in meta["domains"]:
            if dom in hay:
                return code
    # subject/body brand-name fallback
    if "makemytrip" in hay or "make my trip" in hay:
        return "makemytrip"
    if "goibibo" in hay:
        return "goibibo"
    if "booking.com" in hay:
        return "booking_com"
    if "agoda" in hay:
        return "agoda"
    return None


def detect_kind(subject: str, body: str) -> str:
    hay = f"{subject}\n{body}".lower()
    if re.search(r"\bcancel(l?ed|lation)?\b", hay):
        return "cancellation"
    return "confirmation"


# --- field extraction ---
_DATE_FORMATS = ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d %b %Y", "%d %B %Y",
                 "%b %d, %Y", "%B %d, %Y", "%d %b %y", "%a, %d %b %Y")


def _parse_date_loose(s):
    if not s:
        return None
    s = s.strip().strip(".,")
    s = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", s)  # 15th -> 15
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
def parse_makemytrip(text: str) -> dict:
    return _parse_common(text)


def parse_goibibo(text: str) -> dict:
    return _parse_common(text)


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
    channel = force_channel or detect_channel(from_addr, subject)
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
    d.amount = fields.get("amount")
    d.message_id = msg_id
    d.raw_source = fields.get("raw_source")
    db.flush()
    return d, created


def ingest_email_bytes(db, raw: bytes, force_channel=None, commit=False) -> dict:
    """Parse + upsert one email into a draft. Also auto-flags a matching confirmed booking when the
    email is a cancellation. Returns a small result dict. Best-effort; caller decides commit."""
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
    if fields.get("kind") == "cancellation" and fields.get("ota_booking_id"):
        b = (db.query(Booking)
             .filter(Booking.ota_booking_id == fields["ota_booking_id"],
                     Booking.status.in_(_LIVE_STATUSES))
             .first())
        if b is not None:
            flagged_booking_id = b.booking_id
            d.linked_booking_id = b.booking_id
            d.status = "flagged"
    db.flush()
    if commit:
        db.commit()
    return {"parsed": True, "created": created, "draft_id": d.id, "channel": channel,
            "kind": fields.get("kind"), "flagged_booking_id": flagged_booking_id}


def poll_mailbox(db, limit=50) -> dict:
    """Connect to the configured IMAP mailbox, ingest UNSEEN OTA emails into drafts, mark them seen.
    No-ops (returns configured=False) when OTA_IMAP_* env is unset. Best-effort: connection/parse
    errors are logged and summarised, never raised."""
    cfg = imap_config()
    if cfg is None:
        return {"configured": False, "processed": 0, "created": 0, "drafts": []}
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
    return {"configured": True, "processed": processed, "created": created,
            "errors": errors, "drafts": drafts}


def serialize_draft(d: OtaDraftBooking) -> dict:
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
        "amount": float(d.amount) if d.amount is not None else None,
        "commission_percent": float(d.commission_percent) if d.commission_percent is not None else None,
        "linked_booking_id": d.linked_booking_id,
        "created_at": d.created_at.isoformat() if d.created_at else None,
    }
