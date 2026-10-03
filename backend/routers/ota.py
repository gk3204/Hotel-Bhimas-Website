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

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_
from sqlalchemy.orm import Session

from database import SessionLocal
from models import Booking, OtaSettlement, OtaDraftBooking, RoomType
from schemas import OtaChannelUpdate, OtaSettlementCreate, OtaDraftConfirm, DeskBookingCreate, BookingItemCreate
from utils.auth_utils import require_admin, require_reception_or_admin
from utils.audit import write_audit, _resolve_user_id
from services import ota_service

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
    )

    # Call the desk-booking handler directly (plain function — Depends bypassed by explicit args).
    # v6m: at the voucher's rates (or the admin's), not today's rate card.
    from routers.reception import create_desk_booking_at_rates
    result = create_desk_booking_at_rates(booking_req, db=db, user=user,
                                          rates=_confirm_rates(d, data, user))

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
