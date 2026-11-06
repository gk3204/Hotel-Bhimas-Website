"""OTA tracking endpoints (prompt 17, Phase A).

Makes OTA (MakeMyTrip / Goibibo / Booking.com / Agoda) business visible + reconcilable:
  * per-OTA config (commission %, active, mailbox toggle, cancellation rules)   [admin]
  * OTA bookings list (gross / commission / net payout / OTA id)                 [reception+admin]
  * payout reconciliation + actual-settlement entry                             [admin]
  * email-parsed draft inbox: confirm (one-click → real booking) / dismiss       [reception+admin]
  * manual IMAP poll trigger                                                     [admin]

Config/reconciliation/settlements are admin (owner cockpit — screens live in the web admin, which
the front-desk PC can reach). The OTA-bookings list + draft confirm/dismiss are reception-facing so
the desk can action them. Phase B (channel-manager two-way sync) is a later prompt.
"""
import logging
import re
from datetime import date, datetime, time, timedelta
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import or_
from sqlalchemy.orm import Session

from database import SessionLocal
from models import Booking, OtaSettlement, OtaSettlementLine, OtaDraftBooking, RoomType
from schemas import OtaChannelUpdate, OtaSettlementCreate, OtaDraftConfirm, DeskBookingCreate, BookingItemCreate
from utils.auth_utils import require_admin, require_admin_or_accountant, require_reception_or_admin
from utils.audit import write_audit, _resolve_user_id
from services import ota_service, ota_payments, ota_statements, ota_reports

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ota", tags=["OTA"])


# v5r: the placeholder rule lives in utils/phone so that services/ota_service can use the SAME one.
# It could not before — it called this private copy and raised NameError on every masked-phone voucher.
from utils.phone import ota_placeholder as _ota_placeholder_phone   # noqa: E402  (kept as a local name)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _parse_date(s, field):
    if not s:
        return None
    try:
        return date.fromisoformat(s)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid {field}. Use YYYY-MM-DD")


def _range(from_, to, default_days=30):
    """Inclusive (dfrom, dto). Missing both -> last `default_days`. dfrom > dto -> 400."""
    dfrom = _parse_date(from_, "from")
    dto = _parse_date(to, "to")
    if dto is None:
        dto = date.today()
    if dfrom is None:
        dfrom = dto - timedelta(days=default_days - 1)
    if dfrom > dto:
        raise HTTPException(status_code=400, detail="'from' must be on or before 'to'")
    return dfrom, dto


# ============================================================
# Per-OTA config
# ============================================================
@router.get("/channels")
def list_channels(db: Session = Depends(get_db), user=Depends(require_admin)):
    return {
        "channels": [ota_service.serialize_channel(c) for c in ota_service.get_channels(db)],
        "poller": ota_service.poller_status(db),
    }


@router.put("/channels/{code}")
def update_channel(code: str, data: OtaChannelUpdate,
                   db: Session = Depends(get_db), user=Depends(require_admin)):
    if code not in ota_service.ota_sources(db):
        raise HTTPException(status_code=404, detail=f"Unknown OTA channel: {code}")
    c = ota_service.upsert_channel(
        db, code,
        display_name=data.display_name,
        commission_percent=data.commission_percent,
        active=data.active,
        mailbox_parsing_enabled=data.mailbox_parsing_enabled,
        cancellation_policy=data.cancellation_policy,
    )
    write_audit(db, user, "ota.channel_update", "ota_channel", c.id,
                after=ota_service.serialize_channel(c), commit=True)
    return ota_service.serialize_channel(c)


@router.get("/status")
def poller_status(db: Session = Depends(get_db), user=Depends(require_admin)):
    return ota_service.poller_status(db)


@router.get("/rates")
def channel_rates(db: Session = Depends(get_db), user=Depends(require_reception_or_admin)):
    """Reception-readable channel commission defaults (no config/secrets) — the desktop uses these
    to pre-fill the commission % when the desk enters an OTA booking."""
    return {"channels": [
        {"code": c.code, "display_name": c.display_name,
         "commission_percent": float(c.commission_percent or 0), "active": bool(c.active)}
        for c in ota_service.get_channels(db)
    ]}


# ============================================================
# OTA bookings list
# ============================================================
@router.get("/bookings")
def list_ota_bookings(from_: str = Query(None, alias="from"), to: str = Query(None),
                      source: str = Query(None),
                      db: Session = Depends(get_db), user=Depends(require_reception_or_admin)):
    dfrom, dto = _range(from_, to)
    sources = ota_service.ota_sources(db)
    q = (db.query(Booking)
         .filter(Booking.booking_source.in_(sources),
                 Booking.check_in >= dfrom, Booking.check_in <= dto))
    if source:
        if source not in sources:
            raise HTTPException(status_code=400, detail=f"Unknown OTA source: {source}")
        q = q.filter(Booking.booking_source == source)
    bookings = q.order_by(Booking.check_in.desc(), Booking.booking_id.desc()).all()
    rows = [ota_service.serialize_ota_booking(db, b) for b in bookings]
    totals = {
        "bookings": len(rows),
        "gross": round(sum(r["gross"] for r in rows), 2),
        "commission_amount": round(sum(r["commission_amount"] or 0 for r in rows), 2),
        "net_payout": round(sum(r["net_payout"] or 0 for r in rows), 2),
    }
    return {"from": str(dfrom), "to": str(dto), "rows": rows, "totals": totals}


# ============================================================
# Payout reconciliation + settlements
# ============================================================
@router.get("/reconciliation")
def reconciliation(from_: str = Query(None, alias="from"), to: str = Query(None),
                   channel: str = Query(None),
                   db: Session = Depends(get_db), user=Depends(require_admin)):
    dfrom, dto = _range(from_, to)
    if channel and channel not in ota_service.ota_sources(db):
        raise HTTPException(status_code=400, detail=f"Unknown OTA channel: {channel}")
    return ota_service.reconcile_payouts(db, channel=channel, dfrom=dfrom, dto=dto)


@router.post("/settlements")
def create_settlement(data: OtaSettlementCreate,
                      db: Session = Depends(get_db), user=Depends(require_admin)):
    s = ota_service.record_settlement(
        db, data.channel_code, data.amount,
        period_start=data.period_start, period_end=data.period_end,
        reference=data.reference, notes=data.notes, user_id=_resolve_user_id(db, user))
    write_audit(db, user, "ota.settlement_record", "ota_settlement", s.id,
                after=ota_service.serialize_settlement(s), commit=True)
    return ota_service.serialize_settlement(s)


@router.get("/settlements")
def list_settlements(from_: str = Query(None, alias="from"), to: str = Query(None),
                     channel: str = Query(None),
                     db: Session = Depends(get_db), user=Depends(require_admin)):
    dfrom, dto = _range(from_, to, default_days=90)
    q = db.query(OtaSettlement).filter(
        ota_service.func_date(OtaSettlement.created_at) >= dfrom,
        ota_service.func_date(OtaSettlement.created_at) <= dto)
    if channel:
        q = q.filter(OtaSettlement.channel_code == channel)
    rows = [ota_service.serialize_settlement(s)
            for s in q.order_by(OtaSettlement.created_at.desc()).all()]
    return {"from": str(dfrom), "to": str(dto), "rows": rows}


# ============================================================
# P1 Go-MMT payment tracking (services/ota_payments.py). Batch 1 = intake + read; the screen and
# Confirm received / Undo arrive in batch 2. Admin only - this is money.
# ============================================================
def _chans(channel):
    """P2a channel filter: all | makemytrip | goibibo | yatra."""
    try:
        return ota_payments.channels_of(channel)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


_CHANNEL_Q = Query(None, description="all | makemytrip | goibibo | yatra")


@router.get("/payments")
def list_payments(from_: str = Query(None, alias="from"), to: str = Query(None), channel: str = _CHANNEL_Q,
                  db: Session = Depends(get_db), user=Depends(require_admin)):
    """OTA payments (Go-MMT transfers, Yatra advices) by payment date, newest first, with their counts."""
    dfrom, dto = _range(from_, to, default_days=90)
    chans = _chans(channel)
    pays = ota_payments.in_channels(db, (db.query(OtaSettlement)
            .filter(OtaSettlement.utr.isnot(None),
                    OtaSettlement.payment_date >= dfrom, OtaSettlement.payment_date <= dto)
            .order_by(OtaSettlement.payment_date.desc(), OtaSettlement.id.desc()).all()), chans)
    lines = {}
    if pays:
        for x in (db.query(OtaSettlementLine)
                  .filter(OtaSettlementLine.settlement_id.in_([s.id for s in pays])).all()):
            lines.setdefault(x.settlement_id, []).append(x)
    rows = []
    for s in pays:
        r = ota_payments.serialize_payment(s, lines.get(s.id, []))
        r.pop("lines", None)
        rows.append(r)
    return {"from": str(dfrom), "to": str(dto), "rows": rows,
            "total": round(sum(r["amount"] for r in rows), 2)}


@router.get("/payments/bookings")
def payment_states(from_: str = Query(None, alias="from"), to: str = Query(None),
                   state: str = Query(None, description="pending | done | received | not_applicable"),
                   overdue: bool = Query(None), channel: str = _CHANNEL_Q,
                   db: Session = Depends(get_db), user=Depends(require_admin)):
    """Every Goibibo / MakeMyTrip / Yatra booking checking out in [from, to] with its payment state."""
    dfrom, dto = _range(from_, to, default_days=30)
    bs = (db.query(Booking)
          .filter(Booking.booking_source.in_(_chans(channel)),
                  Booking.check_out >= dfrom, Booking.check_out <= dto)
          .order_by(Booking.check_out.desc(), Booking.booking_id.desc()).all())
    states = ota_payments.ota_payment_states(db, bs)
    rows = []
    for b in bs:
        st = states.get(b.booking_id)
        if st is None or (state and st["state"] != state) or (overdue is not None and st["overdue"] != overdue):
            continue
        rows.append({**st, "guest_name": b.guest_name, "booking_source": b.booking_source,
                     "status": b.status, "check_in": str(b.check_in), "check_out": str(b.check_out)})
    counts = {}
    for st in states.values():
        counts[st["state"]] = counts.get(st["state"], 0) + 1
    counts["overdue"] = sum(1 for st in states.values() if st["overdue"])
    return {"from": str(dfrom), "to": str(dto), "counts": counts, "rows": rows}


# --- P1 batch 2: the OTA Payments screen. Fixed paths come before /payments/{payment_id}. ---
class OtaPaymentReceive(BaseModel):
    received_on: Optional[date] = None        # the bank credit date; default = the payment date
    received_amount: Optional[float] = None   # what the bank shows; default = the transfer amount
    note: Optional[str] = Field(None, max_length=500)


class OtaPaymentUndo(BaseModel):
    reason: str = Field(..., min_length=3, max_length=500)


class OtaPaymentManual(BaseModel):
    utr: str = Field(..., min_length=4, max_length=40)
    payment_date: date
    amount: float = Field(..., gt=0)
    booking_ids: List[int] = Field(..., min_length=1)
    amounts: Optional[Dict[int, float]] = None   # booking_id -> amount, when not the voucher payable
    note: Optional[str] = Field(None, max_length=500)


class OtaPaymentLink(BaseModel):
    booking_id: int


def _payment_or_404(db, payment_id):
    s = db.query(OtaSettlement).filter(OtaSettlement.id == payment_id).first()
    if s is None:
        raise HTTPException(status_code=404, detail="Payment not found")
    return s


def _payment_out(db, s):
    lines = (db.query(OtaSettlementLine).filter(OtaSettlementLine.settlement_id == s.id)
             .order_by(OtaSettlementLine.id).all())
    return ota_payments.serialize_payment(s, lines)


@router.get("/payments/summary")
def payments_summary(channel: str = _CHANNEL_Q, db: Session = Depends(get_db), user=Depends(require_admin)):
    """The four tiles on OTA Payments."""
    _chans(channel)
    return ota_payments.summary(db, channel=channel)


@router.get("/payments/exceptions")
def payments_exceptions(channel: str = _CHANNEL_Q, db: Session = Depends(get_db), user=Depends(require_admin)):
    """Lines that found no booking or the wrong amount, flagged payments, overdue / mis-paid bookings."""
    _chans(channel)
    return ota_payments.exceptions(db, channel=channel)


@router.get("/payments/booking/{booking_id}/chain")
def payment_chain_of_booking(booking_id: int, db: Session = Depends(get_db), user=Depends(require_admin)):
    """P2a: the booking's money step by step - gross, commission, TCS, TDS, payable, paid, in bank - each
    as the voucher states it beside what the OTA's documents say. `chain` is null for an untracked booking."""
    b = db.query(Booking).filter(Booking.booking_id == booking_id).first()
    if b is None:
        raise HTTPException(status_code=404, detail="Booking not found")
    return {"booking_id": booking_id, "chain": ota_payments.booking_chain(db, b)}


class OtaPaymentSettings(BaseModel):
    forwarders: Optional[str] = Field(None, max_length=300)        # comma list of the hotel's own addresses
    yatra_vendor_id: Optional[str] = Field(None, max_length=12)
    bank_account: Optional[str] = Field(None, max_length=30)
    overdue_days: Optional[int] = Field(None, ge=0, le=60)
    tracking_start: Optional[str] = Field(None, max_length=10)     # YYYY-MM-DD, "" = automatic


@router.get("/payments/settings")
def get_payment_settings(db: Session = Depends(get_db), user=Depends(require_admin)):
    from utils.settings import get_ota_payment_config
    c = get_ota_payment_config(db)
    return {"forwarders": ", ".join(c["forwarders"]), "yatra_vendor_id": c["yatra_vendor_id"],
            "bank_account": c["bank_account"], "overdue_days": c["overdue_days"],
            "tracking_start": str(c["tracking_start"]) if c["tracking_start"] else "",
            "accounts": c["accounts"], "hotel_name": c["hotel_name"]}


@router.put("/payments/settings")
def put_payment_settings(data: OtaPaymentSettings, db: Session = Depends(get_db), user=Depends(require_admin)):
    """P2a: the payment-tracking settings that had no screen - the forwarding address(es) allowed to send
    payment mails in as attachments, the Yatra vendor ID, the bank account, overdue days, tracking start."""
    from utils import settings as S
    before = get_payment_settings(db, user)
    if data.forwarders is not None:
        bad = [a for a in (x.strip() for x in data.forwarders.split(",")) if a and "@" not in a]
        if bad:
            raise HTTPException(status_code=400, detail=f"Not an e-mail address: {bad[0]}")
        S.set_setting(db, S.OTA_PAYMENT_FORWARDERS_KEY,
                      ",".join(a.strip().lower() for a in data.forwarders.split(",") if a.strip()), user=user)
    if data.yatra_vendor_id is not None:
        if not re.fullmatch(r"\d{3,8}", data.yatra_vendor_id.strip()):
            raise HTTPException(status_code=400, detail="The Yatra vendor ID is a number, e.g. 58270")
        S.set_setting(db, S.OTA_PAYMENT_YATRA_VENDOR_KEY, data.yatra_vendor_id.strip(), user=user)
    if data.bank_account is not None:
        if not re.fullmatch(r"\d{6,20}", re.sub(r"\s", "", data.bank_account)):
            raise HTTPException(status_code=400, detail="Enter the full bank account number (digits only)")
        S.set_setting(db, S.OTA_PAYMENT_BANK_ACCOUNT_KEY, re.sub(r"\s", "", data.bank_account), user=user)
    if data.overdue_days is not None:
        S.set_setting(db, S.OTA_PAYMENT_OVERDUE_DAYS_KEY, str(data.overdue_days), user=user)
    if data.tracking_start is not None:
        if data.tracking_start.strip():
            _parse_date(data.tracking_start.strip(), "tracking_start")
        S.set_setting(db, S.OTA_PAYMENT_TRACKING_START_KEY, data.tracking_start.strip(), user=user)
    db.flush()
    after = get_payment_settings(db, user)
    write_audit(db, user, "ota.payment_settings", "app_settings", None, before=before, after=after, client="web")
    db.commit()
    return after


@router.get("/payments/booking/{booking_id}")
def payment_state_of_booking(booking_id: int, db: Session = Depends(get_db), user=Depends(require_admin)):
    """One booking's Go-MMT payment state (the badge on the booking detail). `state` is null for a
    booking that is not Goibibo / MakeMyTrip."""
    b = db.query(Booking).filter(Booking.booking_id == booking_id).first()
    if b is None:
        raise HTTPException(status_code=404, detail="Booking not found")
    return {"booking_id": booking_id, "state": ota_payments.ota_payment_state(db, b)}


@router.get("/payments/candidates")
def payment_candidates(q: str = Query(None, description="OTA id, guest name or booking number"),
                       unpaid_only: bool = Query(True), channel: str = _CHANNEL_Q,
                       db: Session = Depends(get_db), user=Depends(require_admin)):
    """Tracked OTA bookings to tick on "Add payment" or pick when linking a line - unpaid first."""
    query = db.query(Booking).filter(Booking.booking_source.in_(_chans(channel)))
    term = (q or "").strip()
    if term:
        like = f"%{term}%"
        conds = [Booking.ota_booking_id.ilike(like), Booking.guest_name.ilike(like)]
        if term.lstrip("#").isdigit():
            conds.append(Booking.booking_id == int(term.lstrip("#")))
        query = query.filter(or_(*conds))
    bs = query.order_by(Booking.check_in.desc()).limit(300).all()
    states = ota_payments.ota_payment_states(db, bs)
    rows = []
    for b in bs:
        st = states.get(b.booking_id)
        if st is None or (unpaid_only and st["state"] != "pending"):
            continue
        rows.append({**st, "guest_name": b.guest_name, "booking_source": b.booking_source, "status": b.status,
                     "check_in": str(b.check_in), "check_out": str(b.check_out)})
    return {"rows": rows[:100]}


@router.get("/payments/{payment_id}")
def get_payment(payment_id: int, db: Session = Depends(get_db), user=Depends(require_admin)):
    return _payment_out(db, _payment_or_404(db, payment_id))


@router.post("/payments/import")
def import_payments(since: str = Query(..., description="YYYY-MM-DD - read OTA payment mails on/after this date"),
                    limit: int = Query(1000, ge=1, le=5000),
                    db: Session = Depends(get_db), user=Depends(require_admin)):
    """Backfill OTA payment mails (read or unread, left unread). Idempotent - a payment already
    recorded is skipped by Message-ID and by UTR. Also reads TCS/TDS into drafts saved before P1.
    Payments-only: no booking draft is ever created or changed by this import."""
    d = _parse_date(since, "since")
    res = ota_service.poll_mailbox(db, limit=limit, since=d, from_addr=ota_payments.PAYMENT_SENDER,
                                   payments_only=True)
    # plus batch 3's monthly Go-MMT statements, P2a's Yatra YES Bank advices and Yatra booking mails
    # (for their commission invoices / credit notes), and "Forward as attachment" mails from the
    # hotel's own inbox (ota_payment_forwarders)
    from utils.settings import get_ota_payment_config
    senders = [ota_statements.STATEMENT_SENDER, ota_payments.YES_BANK_SENDER, ota_statements.YATRA_DOC_SENDER]
    for fwd in senders + get_ota_payment_config(db)["forwarders"]:
        more = ota_service.poll_mailbox(db, limit=limit, since=d, from_addr=fwd, payments_only=True)
        res["processed"] = (res.get("processed") or 0) + (more.get("processed") or 0)
        for k, v in (more.get("payments") or {}).items():
            res.setdefault("payments", {}).setdefault(k, 0)
            res["payments"][k] += v
    taxes = ota_payments.fill_voucher_taxes(db)
    db.commit()
    res["voucher_taxes_filled"] = taxes
    write_audit(db, user, "ota.payments_import", "ota_settlement", None,
                after={"since": since, "limit": limit, "processed": res.get("processed"),
                       "payments": res.get("payments"), "voucher_taxes_filled": taxes},
                commit=True)
    return res


# --- P1 batch 3: Go-MMT monthly TDS / TCS / commission statements (services/ota_statements.py) ---
def _period(s):
    try:
        return datetime.strptime(s, "%Y-%m").date()
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Month must be YYYY-MM")


@router.get("/statements/months")
def statement_months(months: int = Query(12, ge=1, le=36),
                     channel: str = Query("go_mmt", pattern="^(go_mmt|yatra)$"),
                     db: Session = Depends(get_db), user=Depends(require_admin)):
    """One row per month: TDS / TCS statement vs vouchers, commission, and what is still missing.
    channel=yatra (P2a): Yatra commission invoices / credit notes and voucher TDS / TCS."""
    return ota_statements.month_report(db, months=months, channel=channel)


@router.get("/statements/month/{period}")
def statement_month(period: str, channel: str = Query("go_mmt", pattern="^(go_mmt|yatra)$"),
                    db: Session = Depends(get_db), user=Depends(require_admin)):
    """One month booking by booking (period = YYYY-MM)."""
    return ota_statements.month_detail(db, _period(period), channel=channel)


# ============================================================
# P2b OTA reports (services/ota_reports.py) - Goibibo, MakeMyTrip and Yatra. The accounting ones
# (receivables, registers, Tally summary, month-end statement) are open to the read-only accountant
# login too; the management ones (forecast, earnings, leakage, tiles) are admin only. All GET.
# ============================================================
def _month(s, field="month"):
    try:
        return datetime.strptime(s, "%Y-%m").date()
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail=f"{field} must be YYYY-MM")


def _report_chan(channel):
    _chans(channel)
    return None if channel in (None, "", "all") else channel


@router.get("/reports/receivables")
def report_receivables(channel: str = _CHANNEL_Q, db: Session = Depends(get_db),
                       user=Depends(require_admin_or_accountant)):
    """What the OTAs still owe, aged from when each payment fell due, with the dispute list."""
    return ota_reports.receivables(db, channel=_report_chan(channel))


@router.get("/reports/forecast")
def report_forecast(channel: str = _CHANNEL_Q, days: int = Query(7, ge=1, le=31),
                    db: Session = Depends(get_db), user=Depends(require_admin)):
    """OTA money expected in the next `days` days, from each OTA's own payment timing."""
    return ota_reports.cash_forecast(db, channel=_report_chan(channel), days=days)


@router.get("/reports/earnings")
def report_earnings(from_: str = Query(None, alias="from"), to: str = Query(None), channel: str = _CHANNEL_Q,
                    db: Session = Depends(get_db), user=Depends(require_admin)):
    """Per month and channel: gross, commission %, TCS, TDS, net per room-night - OTA vs direct."""
    m_to = _month(to, "to") if to else ota_payments.clock.business_today().replace(day=1)
    m_from = _month(from_, "from") if from_ else ota_statements._month_add(m_to, -5)
    if m_from > m_to:
        raise HTTPException(status_code=400, detail="'from' must be on or before 'to'")
    return ota_reports.channel_earnings(db, m_from, m_to, channel=_report_chan(channel))


@router.get("/reports/leakage")
def report_leakage(channel: str = _CHANNEL_Q, db: Session = Depends(get_db), user=Depends(require_admin)):
    """Paid-but-not-in-PMS, never paid, paid-but-cancelled, mis-paid."""
    return ota_reports.leakage(db, channel=_report_chan(channel))


@router.get("/reports/registers")
def report_registers(month: str = Query(...), kind: str = Query("gst", pattern="^(gst|tcs|tds)$"),
                     channel: str = _CHANNEL_Q, db: Session = Depends(get_db),
                     user=Depends(require_admin_or_accountant)):
    """GST input credit on commission / TCS / TDS register for one month, for the CA."""
    return ota_reports.registers(db, _month(month), kind, channel=_report_chan(channel))


@router.get("/reports/tally")
def report_tally(month: str = Query(...), channel: str = _CHANNEL_Q, db: Session = Depends(get_db),
                 user=Depends(require_admin_or_accountant)):
    """The month's OTA figures and the journal entries they suggest, for Tally."""
    return ota_reports.tally_summary(db, _month(month), channel=_report_chan(channel))


@router.get("/reports/statement")
def report_statement(month: str = Query(...), channel: str = _CHANNEL_Q, db: Session = Depends(get_db),
                     user=Depends(require_admin_or_accountant)):
    """Per OTA: opening owed + stays - received = closing owed, for one month."""
    return ota_reports.month_statement(db, _month(month), channel=_report_chan(channel))


@router.get("/reports/tiles")
def report_tiles(db: Session = Depends(get_db), user=Depends(require_admin)):
    """The owner dashboard's OTA numbers."""
    return ota_reports.tiles(db)


@router.get("/statements/{statement_id}/file")
def statement_file(statement_id: int, part: str = Query("xlsx", pattern="^(xlsx|pdf)$"),
                   db: Session = Depends(get_db), user=Depends(require_admin)):
    """The statement exactly as Go-MMT sent it - the spreadsheet, or the invoice / credit-memo PDF."""
    s = db.query(ota_statements.OtaTaxStatement).filter(ota_statements.OtaTaxStatement.id == statement_id).first()
    if s is None:
        raise HTTPException(status_code=404, detail="Statement not found")
    data, name = (s.pdf_data, s.pdf_name) if part == "pdf" else (s.file_data, s.file_name)
    if not data:
        raise HTTPException(status_code=404, detail="That file was not attached")
    media = "application/pdf" if part == "pdf" else \
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", name or f"statement-{s.id}.{part}")
    return Response(content=bytes(data), media_type=media,
                    headers={"Content-Disposition": f'attachment; filename="{safe}"'})


@router.post("/payments/upload-mail")
async def upload_mail(file: UploadFile = File(...), db: Session = Depends(get_db), user=Depends(require_admin)):
    """A Go-MMT payment or statement mail saved as .eml (Gmail: ⋮ -> Download message) - for mail that
    never reached the PMS mailbox. Goes through exactly the same reading as mail from the poller."""
    raw = await file.read()
    if not raw or len(raw) > 15 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Choose a .eml file under 15 MB")
    res = ota_payments.ingest_mail(db, raw, source="upload")
    if not (res.get("payment") or res.get("statement")):
        # P2a: a Yatra booking / cancellation mail - keep its commission invoice / credit note
        docs = ota_statements.ingest_yatra_documents(db, raw, source="upload")
        if not docs:
            db.rollback()
            raise HTTPException(status_code=400, detail="This is not an OTA payment, statement or Yatra invoice mail")
        first = docs[0]
        res = {"statement": True, "kind": first.get("kind"), "stored": any(x.get("stored") for x in docs),
               "reason": None if any(x.get("stored") for x in docs) else first.get("reason"),
               "rows": len(docs), "documents": docs, "channel": "yatra"}
    if res.get("payment"):
        ota_payments.rematch_open_lines(db)
    write_audit(db, user, "ota.mail_uploaded", "ota", None,
                after={k: res.get(k) for k in ("payment", "statement", "kind", "period", "utr", "stored", "reason",
                                               "stored_count", "statements_stored")}, client="web")
    db.commit()
    return res


@router.post("/payments")
def add_payment(data: OtaPaymentManual, db: Session = Depends(get_db), user=Depends(require_admin)):
    """'Add payment' - a Go-MMT transfer whose mail never arrived, typed in by an admin."""
    try:
        s = ota_payments.create_manual_payment(
            db, utr=data.utr, payment_date=data.payment_date, amount=data.amount,
            booking_ids=data.booking_ids, amounts=data.amounts, note=data.note,
            user_id=_resolve_user_id(db, user))
    except ota_payments.PaymentError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    out = _payment_out(db, s)
    write_audit(db, user, "ota.payment_add", "ota_settlement", s.id, after=out, client="web")
    db.commit()
    return out


@router.post("/payments/{payment_id}/receive")
def receive_payment(payment_id: int, data: OtaPaymentReceive,
                    db: Session = Depends(get_db), user=Depends(require_admin)):
    """'Confirm received' - an admin saw this UTR in the bank; all its bookings turn received."""
    s = _payment_or_404(db, payment_id)
    before = ota_payments.serialize_payment(s)
    try:
        ota_payments.receive_payment(db, s, user_id=_resolve_user_id(db, user), received_on=data.received_on,
                                     received_amount=data.received_amount, note=data.note)
    except ota_payments.PaymentError as e:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(e))
    out = _payment_out(db, s)
    write_audit(db, user, "ota.payment_received", "ota_settlement", s.id, before=before,
                after={k: out[k] for k in ("state", "utr", "amount", "received_on", "received_amount",
                                          "received_difference", "received_note")}, client="web")
    db.commit()
    return out


@router.post("/payments/{payment_id}/undo")
def undo_payment_receipt(payment_id: int, data: OtaPaymentUndo,
                         db: Session = Depends(get_db), user=Depends(require_admin)):
    """Undo 'received' (admin, with a reason). The bookings go back to 'payment done'."""
    s = _payment_or_404(db, payment_id)
    before = ota_payments.serialize_payment(s)
    try:
        ota_payments.undo_receipt(db, s, user_id=_resolve_user_id(db, user), reason=data.reason)
    except ota_payments.PaymentError as e:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(e))
    out = _payment_out(db, s)
    write_audit(db, user, "ota.payment_receipt_undone", "ota_settlement", s.id, before=before,
                after={"state": out["state"], "undo_reason": out["undo_reason"]}, client="web")
    db.commit()
    return out


def _line_or_404(db, line_id):
    line = db.query(OtaSettlementLine).filter(OtaSettlementLine.id == line_id).first()
    if line is None:
        raise HTTPException(status_code=404, detail="Payment line not found")
    return line


@router.post("/payments/lines/{line_id}/link")
def link_payment_line(line_id: int, data: OtaPaymentLink,
                      db: Session = Depends(get_db), user=Depends(require_admin)):
    """Tie a payment line that found no booking to the right one."""
    line = _line_or_404(db, line_id)
    b = db.query(Booking).filter(Booking.booking_id == data.booking_id).first()
    if b is None:
        raise HTTPException(status_code=404, detail="Booking not found")
    before = ota_payments.serialize_line(line)
    try:
        ota_payments.link_line(db, line, b, user_id=_resolve_user_id(db, user))
    except ota_payments.PaymentError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    out = ota_payments.serialize_line(line)
    write_audit(db, user, "ota.payment_line_linked", "ota_settlement_line", line.id, before=before, after=out,
                client="web")
    db.commit()
    return out


@router.post("/payments/lines/{line_id}/ignore")
def ignore_payment_line(line_id: int, db: Session = Depends(get_db), user=Depends(require_admin)):
    """Take a line off the exceptions list (e.g. a stay from before the PMS). Linking it undoes this."""
    line = _line_or_404(db, line_id)
    before = ota_payments.serialize_line(line)
    ota_payments.ignore_line(db, line, user_id=_resolve_user_id(db, user))
    out = ota_payments.serialize_line(line)
    write_audit(db, user, "ota.payment_line_ignored", "ota_settlement_line", line.id, before=before, after=out,
                client="web")
    db.commit()
    return out


# ============================================================
# Email-parsed draft inbox
# ============================================================
@router.get("/drafts")
def list_drafts(status: str = Query(None),
                q: str = Query(None, max_length=80,
                               description="match on guest name, OTA booking id or phone"),
                kind: str = Query(None, description="confirmation | cancellation"),
                check_in_from: date = Query(None), check_in_to: date = Query(None),
                db: Session = Depends(get_db), user=Depends(require_reception_or_admin)):
    """The drafts list, newest first, capped at 200.

    ⚠️ `q` is filtered in SQL rather than by the caller, because of that cap: this property is
    already past 600 drafts, so a client-side filter would search only the newest 200 and report
    "not found" for a draft that is sitting right there. A desk looking up the guest at the
    counter has to search all of them.
    """
    query = db.query(OtaDraftBooking)
    if status:
        query = query.filter(OtaDraftBooking.status == status)
    if q and q.strip():
        like = "%" + q.strip() + "%"
        query = query.filter(or_(
            OtaDraftBooking.guest_name.ilike(like),
            OtaDraftBooking.ota_booking_id.ilike(like),
            OtaDraftBooking.phone.ilike(like),
        ))
    if kind:
        query = query.filter(OtaDraftBooking.kind == kind)
    if check_in_from:
        query = query.filter(OtaDraftBooking.check_in >= check_in_from)
    if check_in_to:
        query = query.filter(OtaDraftBooking.check_in <= check_in_to)
    drafts = query.order_by(OtaDraftBooking.created_at.desc()).limit(200).all()
    rows = [ota_service.serialize_draft(d, db) for d in drafts]
    _annotate_cancellations(db, drafts, rows)
    return {"rows": rows}


def _annotate_cancellations(db, drafts, rows):
    """b5: tie each confirmation draft to what the OTA later did with it, so the desk can show one
    row per booking instead of a confirmation row and a separate cancellation row.

    Fields added to every row:
      cancelled_by_ota        the OTA cancelled this stay (a `cancelled` draft, or a cancellation
                              draft exists for the same channel + OTA id)
      cancellation_draft_id   that cancellation draft, if one exists
      cancelled_on            when the cancellation arrived (the cancellation draft's created_at)
      booking_auto_cancelled  the booking made from this draft was cancelled automatically
      cancel_needs_desk       the cancellation is flagged: the guest had already checked in, so
                              the desk must settle it with the OTA
    Two queries whatever the page size.
    """
    pairs = {(d.channel_code, d.ota_booking_id) for d in drafts if d.ota_booking_id}
    cancels = {}
    if pairs:
        ids = {p[1] for p in pairs}
        for c in (db.query(OtaDraftBooking)
                  .filter(OtaDraftBooking.kind == "cancellation",
                          OtaDraftBooking.ota_booking_id.in_(ids)).all()):
            cancels[(c.channel_code, c.ota_booking_id)] = c
    booking_ids = {d.linked_booking_id for d in drafts if d.linked_booking_id}
    booking_ids |= {c.linked_booking_id for c in cancels.values() if c.linked_booking_id}
    bstatus = {}
    if booking_ids:
        bstatus = {bid: st for bid, st in
                   db.query(Booking.booking_id, Booking.status).filter(Booking.booking_id.in_(booking_ids)).all()}
    for d, row in zip(drafts, rows):
        c = cancels.get((d.channel_code, d.ota_booking_id)) if d.kind == "confirmation" else None
        by_ota = d.status == "cancelled" or c is not None or d.kind == "cancellation"
        linked = d.linked_booking_id or (c.linked_booking_id if c else None)
        row["cancelled_by_ota"] = bool(by_ota)
        row["cancellation_draft_id"] = c.id if c else (d.id if d.kind == "cancellation" else None)
        stamp = (c.created_at if c else (d.created_at if d.kind == "cancellation" else None))
        row["cancelled_on"] = stamp.isoformat() if stamp else None
        row["booking_auto_cancelled"] = bool(by_ota and linked and bstatus.get(linked) == "cancelled")
        src = c if c else (d if d.kind == "cancellation" else None)
        row["cancel_needs_desk"] = bool(src is not None and src.status == "flagged")


def _confirm_lines(data) -> list:
    """The room lines this confirm is for (F-18).

    A mixed-type voucher supplies one line per room type; everything else is the single
    `room_type_id` x `quantity` pair the form has always sent. One helper so the price guard and the
    booking itself can never disagree about what is being confirmed.
    """
    lines = getattr(data, "room_lines", None)
    if lines:
        return [{"room_type_id": ln.room_type_id, "quantity": ln.quantity,
                 "price_per_night": getattr(ln, "price_per_night", None)} for ln in lines]
    return [{"room_type_id": data.room_type_id, "quantity": data.quantity,
             "price_per_night": getattr(data, "price_per_night", None)}]


def _confirm_rates(d, data, user) -> list:
    """v6m: the nightly rate (before GST, per room) each confirm line is booked at, or None = rate card.

    An OTA booking made weeks ago was SOLD at that day's rate; billing today's rate card instead is
    how a Rs 9,765 voucher came out 58% off. The voucher's room-wise table states the rate per room
    type (`room_lines[i].rate`), and that is the default. The desk may confirm at it without asking;
    any OTHER rate - or a rate on a voucher that stated none - is a price decision, so admin only.
    Lines are matched to the voucher's by position (both screens send them in voucher order)."""
    voucher = ota_service._load_room_lines(d) or []
    lines = _confirm_lines(data)
    out = []
    for i, ln in enumerate(lines):
        v_rate = voucher[i].get("rate") if i < len(voucher) and isinstance(voucher[i], dict) else None
        asked = ln.get("price_per_night")
        if asked is None:
            out.append(float(v_rate) if v_rate is not None else None)
            continue
        if (v_rate is None or abs(float(asked) - float(v_rate)) > 0.005) and (user or {}).get("role") != "admin":
            raise HTTPException(
                status_code=403,
                detail=("Only an admin can change the room rate on an OTA booking"
                        + (f" (the voucher says Rs {float(v_rate):,.0f} a night)." if v_rate is not None
                           else " (the voucher did not state one).")))
        out.append(round(float(asked), 2))
    return out


@router.post("/drafts/{draft_id}/confirm")
def confirm_draft(draft_id: int, data: OtaDraftConfirm,
                  db: Session = Depends(get_db), user=Depends(require_reception_or_admin)):
    """Turn a pending email draft into a real desk booking (one click). Reuses the desk-booking
    engine (availability + pricing + guest reuse) so the OTA booking consumes shared-DB availability
    exactly like any other booking, and stamps source + OTA id + commission."""
    d = db.query(OtaDraftBooking).filter(OtaDraftBooking.id == draft_id).first()
    if not d:
        raise HTTPException(status_code=404, detail="Draft not found")
    if d.status == "confirmed":
        raise HTTPException(status_code=409, detail="Draft already confirmed")
    if d.kind == "cancellation":
        raise HTTPException(status_code=400, detail="Cancellation drafts can't be confirmed as a booking; dismiss it.")
    if d.status == "cancelled":
        # b5: the OTA cancelled this stay; booking it would sell a room to a guest who is not coming.
        raise HTTPException(status_code=409, detail="The OTA cancelled this booking — there is nothing to confirm.")
    # v6m.2: ...and the same when the cancellation mail is on file but never retired this draft.
    # Production #325 was confirmed by hand three days after Yatra's cancellation had arrived.
    if d.ota_booking_id and db.query(OtaDraftBooking).filter(
            OtaDraftBooking.channel_code == d.channel_code,
            OtaDraftBooking.ota_booking_id == ota_service.canonical_ota_id(d.channel_code, d.ota_booking_id),
            OtaDraftBooking.kind == "cancellation").first():
        raise HTTPException(status_code=409, detail="The OTA has sent a CANCELLATION for this booking "
                                                    "— there is nothing to confirm.")

    guest_name = data.guest_name or d.guest_name
    check_in = data.check_in or d.check_in
    check_out = data.check_out or d.check_out
    if not guest_name:
        raise HTTPException(status_code=400, detail="Guest name is required (draft didn't parse it — supply in the request).")
    if not check_in or not check_out:
        raise HTTPException(status_code=400, detail="Check-in + check-out dates are required (draft didn't parse them — supply in the request).")
    # Phone is masked by the OTA, so fall back to a per-booking placeholder rather than blocking.
    phone = data.phone or d.phone or _ota_placeholder_phone(d.ota_booking_id)
    # Occupancy parsed from the voucher (v4 occupancy) — carry the real party size into the booking.
    adults = data.adults or d.adults or 1
    children = data.children if data.children is not None else (d.children or 0)

    commission_pct = data.ota_commission_percent
    if commission_pct is None and d.commission_percent is not None:
        commission_pct = float(d.commission_percent)

    # ---- the money must agree with the voucher (F-18) -------------------------------------------
    # Auto-confirm refuses a total more than `max_variance_percent` out; the manual path did not, and
    # the manual path is where a human is GUESSING the room count and type. A voucher listing 4 rooms
    # across 3 types cannot be expressed as one type x quantity, so confirming it "the only way the
    # form allows" quietly billed the guest Rs 7,350 against a voucher of Rs 6,300 — on a channel
    # whose own terms say the property must invoice the voucher's gross.
    # Same helper, same config and same tolerance as the auto path, so the two cannot drift.
    if d.amount and not data.accept_price_variance:
        from utils import settings as _st
        tol = float(_st.get_ota_config(db).get("max_variance_percent") or 0)
        if tol > 0:
            try:
                # F-18: price every line of a mixed-type voucher, not just the first.
                # v6m: at the rate each line is being BOOKED at (the voucher's, normally).
                _rates = _confirm_rates(d, data, user)
                _nights = max(1, (check_out - check_in).days)
                quoted = 0.0
                for ln, rate in zip(_confirm_lines(data), _rates):
                    if rate is not None:
                        _rt = db.query(RoomType).filter(RoomType.room_type_id == ln["room_type_id"]).first()
                        _g = float(_rt.gst_percent or 0) if _rt else 0.0
                        quoted += rate * _nights * ln["quantity"] * (1 + _g / 100)
                    else:
                        quoted += ota_service._quote_rooms_total(db, ln["room_type_id"], ln["quantity"],
                                                                 check_in, check_out, d.channel_code) or 0
            except HTTPException:
                raise
            except Exception as e:                       # pricing must never hard-fail a confirm
                logger.debug(f"OTA confirm variance check skipped for draft {d.id}: {e}")
                quoted = None
            voucher = float(d.amount)
            if quoted and voucher > 0:
                gap = abs(quoted - voucher) / voucher * 100
                if gap > tol:
                    raise HTTPException(
                        status_code=409,
                        detail=(f"The voucher says Rs {voucher:,.0f} but {data.quantity} x this room "
                                f"type prices at Rs {quoted:,.0f} ({gap:.0f}% out). Check the room "
                                f"count and type — a voucher with rooms of DIFFERENT types cannot be "
                                f"confirmed as one type, and the guest must be billed the voucher's "
                                f"total. Re-send with accept_price_variance=true to override."))

    booking_req = DeskBookingCreate(
        rooms=[BookingItemCreate(room_type_id=ln["room_type_id"], quantity=ln["quantity"])
               for ln in _confirm_lines(data)],
        guest_name=guest_name,
        phone=phone,
        email=(data.email or d.email or None),
        check_in=check_in,
        check_out=check_out,
        check_in_time=(data.check_in_time or time(12, 0)),
        adults=adults,
        children=children,
        booking_source=d.channel_code,
        ota_booking_id=d.ota_booking_id,
        ota_commission_percent=commission_pct,
        # Actual figures the voucher stated — preferred over %×gross (apply_ota_fields).
        ota_commission_amount=float(d.commission_amount) if d.commission_amount is not None else None,
        ota_net_payout=float(d.net_payout) if d.net_payout is not None else None,
        use_alternate=bool(getattr(data, 'use_alternate', False)),   # v7g
        alt_otp_id=getattr(data, 'alt_otp_id', None),
        alt_otp_code=getattr(data, 'alt_otp_code', None)
    )

    # Call the desk-booking handler directly (plain function — Depends bypassed by explicit args).
    # v6m: at the voucher's rates (or the admin's), not today's rate card.
    from routers.reception import create_desk_booking_at_rates
    # v7b (owner): reception may confirm an OTA booking into alternate-type rooms when its own
    # type is full - the channel has already sold it. Check-in then asks for the approval.
    result = create_desk_booking_at_rates(booking_req, db=db, user=user,
                                          rates=_confirm_rates(d, data, user), allow_alternate=True)

    d.status = "confirmed"
    d.linked_booking_id = result["booking_id"]
    write_audit(db, user, "ota.draft_confirm", "ota_draft_booking", d.id,
                after={"booking_id": result["booking_id"], "channel": d.channel_code,
                       "ota_booking_id": d.ota_booking_id}, commit=True)
    return {"draft_id": d.id, "booking": result}


@router.post("/drafts/{draft_id}/dismiss")
def dismiss_draft(draft_id: int,
                  db: Session = Depends(get_db), user=Depends(require_reception_or_admin)):
    d = db.query(OtaDraftBooking).filter(OtaDraftBooking.id == draft_id).first()
    if not d:
        raise HTTPException(status_code=404, detail="Draft not found")
    d.status = "dismissed"
    write_audit(db, user, "ota.draft_dismiss", "ota_draft_booking", d.id, commit=True)
    return {"draft_id": d.id, "status": d.status}


# ============================================================
# v6m.2 Yatra repair (also runs at the start of every poll)
# ============================================================
@router.post("/repair")
def repair_drafts(dry_run: bool = Query(True), db: Session = Depends(get_db), user=Depends(require_admin)):
    """Re-pair Yatra cancellations saved under a wrapped id ("YATMB0012309231") with their voucher -
    cancelling a booking whose guest has not arrived - clean guest names that swallowed "Guest Email
    Address", and read the voucher rate into pending drafts. dry_run (default) only reports."""
    res = ota_service.repair_yatra_drafts(db, dry_run=dry_run)
    if dry_run:
        db.rollback()
    else:
        write_audit(db, user, "ota.repair_yatra", "ota_draft", None, after=res, client="web")
        db.commit()
    return {"dry_run": dry_run, **res}


# ============================================================
# Manual IMAP poll
# ============================================================
@router.post("/poll")
def poll_now(db: Session = Depends(get_db), user=Depends(require_reception_or_admin)):
    """Trigger the IMAP mailbox poll inline (mirrors /whatsapp/jobs/run). No-ops with
    configured=false when OTA_IMAP_* env is unset. Reception can trigger it too — the desk's OTA
    drafts screen has a Poll button (the draft inbox is a front-desk workflow)."""
    return ota_service.poll_mailbox(db)


@router.post("/import")
def import_since(since: str = Query(..., description="YYYY-MM-DD — import OTA mail on/after this date"),
                 limit: int = Query(500, ge=1, le=2000),
                 future_only: bool = Query(True, description="skip stays whose check-in is already past"),
                 drafts_only: bool = Query(True, description="never auto-confirm; leave drafts for the desk"),
                 db: Session = Depends(get_db), user=Depends(require_admin)):
    """One-time backfill: import OTA emails on/after `since` (read or unread) WITHOUT marking them
    seen. By default only UPCOMING stays are kept and they stay as drafts so the desk picks the room
    type and confirms; pass future_only=false / drafts_only=false for the old go-live behaviour.
    Idempotent — safe to re-run. No-ops when the mailbox isn't configured."""
    d = _parse_date(since, "since")
    res = ota_service.poll_mailbox(db, limit=limit, since=d, future_only=future_only, drafts_only=drafts_only)
    write_audit(db, user, "ota.import_since", "ota", None,
                after={"since": since, "limit": limit, "future_only": future_only, "drafts_only": drafts_only,
                       "processed": res.get("processed"), "created": res.get("created"),
                       "skipped_past": res.get("skipped_past")},
                commit=True)
    return res
