"""OTA management and accounting reports (P2b, owner 5-6 Nov 2026) - Goibibo, MakeMyTrip and Yatra.

Everything here is READ-ONLY and computed from what P1 / P2a already store - nothing new is kept:

  bookings + their vouchers (ota_draft_bookings)    what each stay is worth, its commission / TCS / TDS
  ota_settlements + ota_settlement_lines            what each OTA paid, when, under which UTR
  ota_tax_statements (+ lines)                      the OTA's own invoices, credit notes, TDS / TCS files
  services/ota_payments.ota_payment_states          each booking's pending / done / received state

Every report takes the channel filter (all | makemytrip | goibibo | yatra) and totals per channel.

  receivables      what the OTAs still owe, by age, with the dispute list          admin + accountant
  cash_forecast    OTA money expected in the next N days, from each OTA's own timing  admin
  channel_earnings gross, commission %, net per room-night: each OTA vs direct      admin
  leakage          paid-but-not-in-PMS, never paid, paid-but-cancelled, mis-paid    admin
  registers        GST input credit / TCS / TDS registers for the CA, one month    admin + accountant
  tally_summary    the month's OTA figures and suggested journal entries for Tally admin + accountant
  month_statement  opening owed + stays - received = closing owed, per OTA         admin + accountant
  tiles            the owner dashboard's OTA numbers                                admin
"""
import re
import statistics
from datetime import date, timedelta

from sqlalchemy import func

from models import Booking, BookingItem, OtaSettlement, OtaSettlementLine, OtaTaxStatement, OtaTaxStatementLine
from services import ota_payments as P
from services.ota_statements import _month_add, voucher_figures
from utils import clock
from utils.settings import get_ota_payment_config

# Booking statuses an OTA owes money on (a prepaid no-show is still paid) - as ota_payments.
PAYABLE = ("confirmed", "checked_in", "checked_out", "no_show")
LIVE = PAYABLE
LABEL = dict(P.CHANNEL_LABEL, direct="Direct", agent="Travel agents")
# Who deducts the tax, for the TDS (26AS) / TCS (GSTR-2B) registers - from the owner's documents:
# Go-MMT vouchers / TCS file "E-Commerce TCS_No_ 37AADCM5146R1C5", invoice GSTIN 06AADCM5146R1ZZ;
# Yatra invoices "PAN No.: AAACY2602D", "GST Number - 27AAACY2602D1ZS".
DEDUCTOR = {"makemytrip": ("MakeMyTrip (India) Pvt Ltd", "AADCM5146R"),
            "goibibo": ("MakeMyTrip (India) Pvt Ltd (Goibibo)", "AADCM5146R"),
            "yatra": ("Yatra Online Limited", "AAACY2602D")}
SUPPLIER_GSTIN_DEFAULT = {"go_mmt": "06AADCM5146R1ZZ", "yatra": "27AAACY2602D1ZS"}
# How many days after check-in each OTA usually pays, when there is no history to learn it from:
# Go-MMT 1-2 days after check-in (owner's payment mails), Yatra the day before (YES Bank advices).
DEFAULT_LAG = {"makemytrip": 1, "goibibo": 1, "yatra": -1}
_TOL = 1.0


def _r(x) -> float:
    return round(float(x or 0), 2)


def _due_base(b) -> date:
    """The day an OTA's payment for a stay falls due - ota_payments' overdue rule."""
    return b.check_in if (b.booking_source or "") in P.OVERDUE_FROM_CHECK_IN else b.check_out


def _tracked(db, chans, upto=None, since=None):
    q = db.query(Booking).filter(Booking.booking_source.in_(chans))
    start = P.tracking_start(db)
    if start is not None:
        q = q.filter(Booking.check_in >= start)
    if since is not None:
        q = q.filter(Booking.check_in >= since)
    if upto is not None:
        q = q.filter(Booking.check_in <= upto)
    return q.all()


def _per_channel(rows, key, value):
    out = {}
    for r in rows:
        out[r[key]] = _r(out.get(r[key], 0) + (value(r) or 0))
    return out


# ---------------------------------------------------------------------------
# 1. Receivables ageing + disputes
# ---------------------------------------------------------------------------
BUCKETS = (("0-7", 0, 7), ("8-15", 8, 15), ("16-30", 16, 30), ("30+", 31, 10 ** 6))


def _bucket(age: int) -> str:
    if age < 0:
        return "not_due"
    for name, lo, hi in BUCKETS:
        if lo <= age <= hi:
            return name
    return "30+"


def receivables(db, channel=None, today=None) -> dict:
    """What the OTAs still owe for stays that have begun: unpaid bookings (the voucher payable) and
    short payments (payable - paid), aged from the day the payment fell due. The dispute list is the
    part a person should chase: overdue, short paid, paid for a cancelled stay."""
    cfg = get_ota_payment_config(db)
    today = today or clock.business_today()
    chans = P.channels_of(channel)
    bs = _tracked(db, chans, upto=today)
    states = P.ota_payment_states(db, bs, cfg=cfg, today=today)
    rows = []
    for b in bs:
        st = states.get(b.booking_id)
        if st is None or st["expected"] is None or b.status not in PAYABLE:
            continue
        if st["state"] == "pending":
            owed, reason = st["expected"], "unpaid"
        elif "short_paid" in st["flags"]:
            owed, reason = _r(st["expected"] - st["paid"]), "short_paid"
        else:
            continue
        if owed <= _TOL:
            continue
        due = _due_base(b)
        age = (today - due).days
        rows.append({"booking_id": b.booking_id, "ota_booking_id": b.ota_booking_id, "channel": b.booking_source,
                     "guest_name": b.guest_name, "check_in": str(b.check_in), "check_out": str(b.check_out),
                     "status": b.status, "expected": st["expected"], "paid": st["paid"], "owed": owed,
                     "due_from": str(due), "age_days": age, "bucket": _bucket(age), "overdue": st["overdue"],
                     "reason": reason})
    rows.sort(key=lambda r: -r["age_days"])
    by = {}
    for r in rows:
        c = by.setdefault(r["channel"], {"channel": r["channel"], "label": LABEL.get(r["channel"]), "total": 0.0,
                                         "count": 0, **{b[0]: 0.0 for b in BUCKETS}, "not_due": 0.0})
        c[r["bucket"]] = _r(c[r["bucket"]] + r["owed"])
        c["total"] = _r(c["total"] + r["owed"])
        c["count"] += 1
    disputes = [r for r in rows if r["overdue"] or r["reason"] == "short_paid"]
    # money received for a stay that was cancelled: also a conversation with the OTA
    for b in bs:
        st = states.get(b.booking_id)
        if st and "paid_but_cancelled" in st["flags"]:
            disputes.append({"booking_id": b.booking_id, "ota_booking_id": b.ota_booking_id,
                             "channel": b.booking_source, "guest_name": b.guest_name, "check_in": str(b.check_in),
                             "check_out": str(b.check_out), "status": b.status, "expected": st["expected"],
                             "paid": st["paid"], "owed": _r(-(st["paid"] or 0)), "due_from": None,
                             "age_days": None, "bucket": None, "overdue": False, "reason": "paid_but_cancelled"})
    return {"today": str(today), "channel": channel or "all", "total": _r(sum(r["owed"] for r in rows)),
            "by_channel": sorted(by.values(), key=lambda c: c["channel"]), "buckets": [b[0] for b in BUCKETS],
            "rows": rows, "disputes": disputes}


# ---------------------------------------------------------------------------
# 2. Cash forecast
# ---------------------------------------------------------------------------
def payment_lag(db, chans, today) -> dict:
    """Median days from check-in to payment per channel, over the last 180 days of matched payments;
    the owner's observed defaults when there are fewer than 5."""
    since = today - timedelta(days=180)
    rows = (db.query(OtaSettlementLine.channel_code, OtaSettlement.payment_date, Booking.check_in)
            .join(OtaSettlement, OtaSettlement.id == OtaSettlementLine.settlement_id)
            .join(Booking, Booking.booking_id == OtaSettlementLine.booking_id)
            .filter(OtaSettlementLine.match_status == "matched", OtaSettlementLine.channel_code.in_(chans),
                    OtaSettlement.payment_date >= since).all())
    gaps = {}
    for ch, paid_on, ci in rows:
        if paid_on and ci:
            gaps.setdefault(ch, []).append((paid_on - ci).days)
    out = {}
    for ch in chans:
        g = gaps.get(ch, [])
        out[ch] = {"days": int(statistics.median(g)) if len(g) >= 5 else DEFAULT_LAG.get(ch, 1),
                   "samples": len(g), "learned": len(g) >= 5}
    return out


def cash_forecast(db, channel=None, today=None, days=7) -> dict:
    """OTA money expected in the next `days` days: every unpaid live booking, expected on check-in +
    that OTA's usual lag. What should already have come but has not is shown separately as 'late'."""
    cfg = get_ota_payment_config(db)
    today = today or clock.business_today()
    chans = P.channels_of(channel)
    lag = payment_lag(db, chans, today)
    horizon = today + timedelta(days=days)
    bs = (db.query(Booking).filter(Booking.booking_source.in_(chans), Booking.status.in_(PAYABLE),
                                   Booking.check_in >= today - timedelta(days=60),
                                   Booking.check_in <= horizon + timedelta(days=3)).all())
    start = P.tracking_start(db, cfg)
    if start is not None:
        bs = [b for b in bs if b.check_in >= start]
    states = P.ota_payment_states(db, bs, cfg=cfg, today=today)
    day_rows = {today + timedelta(days=i): {} for i in range(days + 1)}
    late, items = {}, []
    for b in bs:
        st = states.get(b.booking_id)
        if not st or st["state"] != "pending" or st["expected"] is None:
            continue
        when = b.check_in + timedelta(days=lag[b.booking_source]["days"])
        item = {"booking_id": b.booking_id, "ota_booking_id": b.ota_booking_id, "channel": b.booking_source,
                "guest_name": b.guest_name, "check_in": str(b.check_in), "expected_on": str(when),
                "amount": st["expected"], "late": when < today}
        if when < today:
            late[b.booking_source] = _r(late.get(b.booking_source, 0) + st["expected"])
        elif when <= horizon:
            d = day_rows[when]
            d[b.booking_source] = _r(d.get(b.booking_source, 0) + st["expected"])
        else:
            continue
        items.append(item)
    items.sort(key=lambda i: i["expected_on"])
    return {"today": str(today), "days": days, "lag": lag,
            "by_day": [{"date": str(d), "by_channel": v, "total": _r(sum(v.values()))} for d, v in sorted(day_rows.items())],
            "late": late, "late_total": _r(sum(late.values())),
            "total": _r(sum(sum(v.values()) for v in day_rows.values())), "items": items}


# ---------------------------------------------------------------------------
# 3. Channel earnings (OTA vs direct)
# ---------------------------------------------------------------------------
def _group_of(b, ota_codes) -> str:
    if (b.booking_source or "") in P.TRACKED_CHANNELS:
        return b.booking_source
    if (b.booking_source or "") in ota_codes:
        return "other_ota"
    if b.agent_id:
        return "agent"
    return "direct"


def channel_earnings(db, month_from: date, month_to: date, channel=None) -> dict:
    """Per month (by check-in) and booking channel: stays, room-nights, gross (incl. taxes), commission
    incl. GST and its %, TCS, TDS, what the hotel actually keeps, and that per room-night - each OTA
    beside direct and travel-agent bookings. OTA figures are the voucher's (what the OTA sold, so a
    stay extension paid at the desk does not inflate the commission %); direct = the booking's grand total."""
    from services.ota_service import ota_gross_basis, ota_sources
    ota_codes = set(ota_sources(db))
    end = _month_add(month_to, 1)
    bs = (db.query(Booking).filter(Booking.check_in >= month_from, Booking.check_in < end,
                                   Booking.status.in_(LIVE)).all())
    want = None if not channel or channel == "all" else {channel}
    rooms = dict(db.query(BookingItem.booking_id, func.sum(BookingItem.quantity))
                 .filter(BookingItem.booking_id.in_([b.booking_id for b in bs] or [0]))
                 .group_by(BookingItem.booking_id).all())
    v = voucher_figures(db, {b.ota_booking_id for b in bs if b.ota_booking_id})
    agg = {}
    for b in bs:
        g = _group_of(b, ota_codes)
        if want and g not in want and g not in ("direct", "agent"):
            continue
        nights = max((b.check_out - b.check_in).days, 1) * int(rooms.get(b.booking_id) or 1)
        vf = v.get(b.ota_booking_id, {}) if g in P.TRACKED_CHANNELS else {}
        if g in P.TRACKED_CHANNELS or g == "other_ota":
            gross = vf.get("gross") or float(ota_gross_basis(b) or 0)
            commission = vf.get("commission") if vf.get("commission") is not None else (
                float(b.ota_commission_amount) if b.ota_commission_amount is not None else None)
        else:
            gross = float(b.grand_total or b.total_amount or 0)
            commission = float(b.commission_amount or 0) if g == "agent" else 0.0
        tcs, tds = vf.get("tcs") or 0.0, vf.get("tds") or 0.0
        key = (b.check_in.replace(day=1), g)
        a = agg.setdefault(key, {"bookings": 0, "room_nights": 0, "gross": 0.0, "commission": 0.0, "tcs": 0.0,
                                 "tds": 0.0, "voucher_missing": 0, "gross_k": 0.0, "nights_k": 0})
        a["bookings"] += 1
        a["room_nights"] += nights
        a["gross"] += gross
        if commission is None:
            # no voucher figure: counted in the stays and gross, kept out of the % and the net, never as 0%
            a["voucher_missing"] += 1
            continue
        a["gross_k"] += gross
        a["nights_k"] += nights
        a["commission"] += commission
        a["tcs"] += tcs
        a["tds"] += tds
    rows = []
    for (m, g), a in sorted(agg.items(), key=lambda kv: (kv[0][0], kv[0][1]), reverse=True):
        net = a["gross_k"] - a["commission"] - a["tcs"] - a["tds"]
        rows.append({"month": str(m), "label": m.strftime("%b %Y"), "channel": g, "channel_label": LABEL.get(g, g),
                     "bookings": a["bookings"], "room_nights": a["room_nights"], "gross": _r(a["gross"]),
                     "commission": _r(a["commission"]), "tcs": _r(a["tcs"]), "tds": _r(a["tds"]),
                     # over the bookings whose commission is known (all but `voucher_missing`)
                     "commission_pct": round(100 * a["commission"] / a["gross_k"], 1) if a["gross_k"] else None,
                     "net": _r(net), "net_per_room_night": _r(net / a["nights_k"]) if a["nights_k"] else None,
                     "gross_per_room_night": _r(a["gross"] / a["room_nights"]) if a["room_nights"] else None,
                     "voucher_missing": a["voucher_missing"], "with_commission": a["bookings"] - a["voucher_missing"]})
    return {"from": str(month_from), "to": str(month_to), "rows": rows}


# ---------------------------------------------------------------------------
# 4. Leakage
# ---------------------------------------------------------------------------
def leakage(db, channel=None, today=None) -> dict:
    """Where OTA money can go missing:
      paid_not_in_pms   the OTA paid for a booking the PMS has no record of - a stay nobody entered
                        (unrecorded revenue) or a wrong ID; both need a person
      never_paid        a stay the OTA has not paid past its overdue date
      paid_cancelled    money received for a stay the PMS shows cancelled (retention, or a booking
                        cancelled in the PMS by mistake)
      short / over      paid, but not what the voucher says"""
    cfg = get_ota_payment_config(db)
    today = today or clock.business_today()
    chans = P.channels_of(channel)
    lines = (db.query(OtaSettlementLine, OtaSettlement)
             .join(OtaSettlement, OtaSettlement.id == OtaSettlementLine.settlement_id)
             .filter(OtaSettlementLine.match_status == "unknown_booking", OtaSettlementLine.channel_code.in_(chans))
             .order_by(OtaSettlement.payment_date.desc()).all())
    not_in_pms = [{"ota_booking_id": x.ota_booking_id, "channel": x.channel_code, "guest_name": x.guest_name,
                   "check_in": str(x.check_in) if x.check_in else None, "amount": _r(x.amount_paid),
                   "utr": s.utr, "payment_date": str(s.payment_date) if s.payment_date else None, "line_id": x.id}
                  for x, s in lines]
    bs = _tracked(db, chans, upto=today)
    states = P.ota_payment_states(db, bs, cfg=cfg, today=today)
    by_id = {b.booking_id: b for b in bs}
    never, cancelled, mispaid = [], [], []
    for bid, st in states.items():
        b = by_id[bid]
        row = {"booking_id": bid, "ota_booking_id": b.ota_booking_id, "channel": b.booking_source,
               "guest_name": b.guest_name, "check_in": str(b.check_in), "check_out": str(b.check_out),
               "status": b.status, "expected": st["expected"], "paid": st["paid"], "utrs": st["utrs"]}
        if st["overdue"]:
            never.append({**row, "amount": st["expected"]})
        if "paid_but_cancelled" in st["flags"]:
            cancelled.append({**row, "amount": st["paid"]})
        if set(st["flags"]) & {"short_paid", "over_paid"}:
            mispaid.append({**row, "amount": _r((st["paid"] or 0) - (st["expected"] or 0))})
    total = lambda xs: _r(sum(x["amount"] or 0 for x in xs))  # noqa: E731
    return {"today": str(today), "channel": channel or "all",
            "paid_not_in_pms": {"count": len(not_in_pms), "amount": total(not_in_pms), "rows": not_in_pms},
            "never_paid": {"count": len(never), "amount": total(never), "rows": never},
            "paid_cancelled": {"count": len(cancelled), "amount": total(cancelled), "rows": cancelled},
            "mispaid": {"count": len(mispaid), "amount": total(mispaid), "rows": mispaid}}


# ---------------------------------------------------------------------------
# 5. Registers for the CA
# ---------------------------------------------------------------------------
_GSTIN = re.compile(r"\b\d{2}[A-Z]{5}\d{4}[A-Z][A-Z0-9]Z[A-Z0-9]\b")


def _doc_ids(s: OtaTaxStatement, own_gstin=None):
    """(supplier GSTIN, IRN) of an invoice / credit note - stored, else read from its PDF."""
    gstin, irn = s.supplier_gstin, None
    if s.pdf_data:
        from utils.pdf_text import pdf_text
        t = pdf_text(bytes(s.pdf_data), max_pages=2)
        m = re.search(r"IRN\s*:\s*([0-9a-f]{64})", t)
        irn = m.group(1) if m else None
        if not gstin:
            found = [g for g in _GSTIN.findall(t) if g != own_gstin]
            gstin = found[-1] if found else None
    return gstin or SUPPLIER_GSTIN_DEFAULT.get(s.channel), irn


def go_mmt_channel(brand, ota_booking_id) -> str:
    """MakeMyTrip or Goibibo for a Go-MMT document row. The TCS file and the commission invoice carry a
    brand; the TDS file does not, so fall back to the booking ID (GH... = Goibibo, NH... = MakeMyTrip)."""
    b = (brand or "").upper()
    if b:
        return "goibibo" if b == "GOIBIBO" else "makemytrip"
    return "goibibo" if (ota_booking_id or "").upper().startswith("GH") else "makemytrip"


def _channel_of_ota(ch: str) -> str:
    return "yatra" if ch == "yatra" else "go_mmt"


def registers(db, month: date, kind: str, channel=None) -> dict:
    """One month's register for the CA.
      gst  GST input credit on OTA commission: one row per invoice / credit note - supplier GSTIN, IRN,
           taxable value, GST, total (Go-MMT's monthly invoice; Yatra's per-booking invoices)
      tcs  TCS (GST) per booking: Go-MMT from its monthly statement, Yatra from the voucher (Yatra
           sends no statement; check-in month). Ties to the GST portal's TCS credit (GSTR-2B).
      tds  TDS (194-O) per booking, the same way. Ties to Form 26AS / AIS."""
    chans = P.channels_of(channel)
    groups = {_channel_of_ota(c) for c in chans}
    nxt = _month_add(month, 1)
    if kind == "gst":
        stmts = (db.query(OtaTaxStatement)
                 .filter(OtaTaxStatement.period == month, OtaTaxStatement.kind.in_(("commission", "credit_memo")),
                         OtaTaxStatement.superseded.is_(False), OtaTaxStatement.channel.in_(groups))
                 .order_by(OtaTaxStatement.channel, OtaTaxStatement.doc_no).all())
        # On the OTA's invoice the hotel (the "vendor") prints first and the OTA's own GSTIN last.
        own = None
        rows = []
        for s in stmts:
            gstin, irn = _doc_ids(s, own)
            rows.append({"channel": s.channel, "supplier": "Go-MMT (MakeMyTrip India)" if s.channel == "go_mmt"
                         else "Yatra Online Limited", "supplier_gstin": gstin, "doc_type":
                         "Credit note" if s.kind == "credit_memo" else "Invoice", "doc_no": s.doc_no,
                         "ref_doc_no": s.ref_doc_no, "irn": irn, "bookings": s.row_count,
                         "taxable": _r(s.total_base), "gst": _r(s.total_gst), "total": _r(s.total_amount),
                         "statement_id": s.id})
        totals = {"taxable": _r(sum(r["taxable"] for r in rows)), "gst": _r(sum(r["gst"] for r in rows)),
                  "total": _r(sum(r["total"] for r in rows))}
        return {"month": str(month), "kind": kind, "rows": rows, "totals": totals,
                "by_channel": {g: _r(sum(r["gst"] for r in rows if r["channel"] == g)) for g in groups}}
    if kind not in ("tcs", "tds"):
        raise ValueError("kind must be gst, tcs or tds")
    rows = []
    if "go_mmt" in groups:
        st = (db.query(OtaTaxStatement).filter(OtaTaxStatement.channel == "go_mmt", OtaTaxStatement.kind == kind,
                                               OtaTaxStatement.period == month,
                                               OtaTaxStatement.superseded.is_(False)).all())
        if st:
            net = {}
            for x in (db.query(OtaTaxStatementLine)
                      .filter(OtaTaxStatementLine.statement_id.in_([s.id for s in st])).all()):
                r = net.setdefault(x.ota_booking_id, {"ota_booking_id": x.ota_booking_id, "guest_name": x.guest_name,
                                                      "check_in": str(x.check_in) if x.check_in else None,
                                                      "brand": x.brand, "base": 0.0, "amount": 0.0})
                r["base"] = _r(r["base"] + float(x.base_amount or 0))
                r["amount"] = _r(r["amount"] + float(x.amount))
            for r in net.values():
                if abs(r["amount"]) < 0.005:
                    continue                  # booked and cancelled in the same statement
                ch = go_mmt_channel(r.pop("brand"), r["ota_booking_id"])
                if ch in chans:
                    name, pan = DEDUCTOR[ch]
                    rows.append({**r, "channel": ch, "deductor": name, "deductor_pan": pan, "source": "Go-MMT statement"})
    if "yatra" in groups:
        bs = (db.query(Booking).filter(Booking.booking_source == "yatra", Booking.status != "cancelled",
                                       Booking.check_in >= month, Booking.check_in < nxt,
                                       Booking.ota_booking_id.isnot(None)).all())
        vf = voucher_figures(db, {b.ota_booking_id for b in bs})
        name, pan = DEDUCTOR["yatra"]
        for b in bs:
            amt = vf.get(b.ota_booking_id, {}).get(kind)
            if amt:
                rows.append({"ota_booking_id": b.ota_booking_id, "guest_name": b.guest_name,
                             "check_in": str(b.check_in), "base": None, "amount": _r(amt), "channel": "yatra",
                             "deductor": name, "deductor_pan": pan, "source": "Yatra voucher"})
    rows.sort(key=lambda r: (r["channel"], r["check_in"] or "", r["ota_booking_id"]))
    by = _per_channel(rows, "channel", lambda r: r["amount"])
    return {"month": str(month), "kind": kind, "rows": rows, "totals": {"amount": _r(sum(r["amount"] for r in rows))},
            "by_channel": by}


# ---------------------------------------------------------------------------
# 6. Tally summary + 7. month-end statement
# ---------------------------------------------------------------------------
def _received(db, chans, d_from, d_to, linked_only=True):
    """Payment lines (by payment date) per channel - linked to a PMS booking, or every line."""
    q = (db.query(OtaSettlementLine.channel_code, func.sum(OtaSettlementLine.amount_paid))
         .join(OtaSettlement, OtaSettlement.id == OtaSettlementLine.settlement_id)
         .filter(OtaSettlementLine.channel_code.in_(chans), OtaSettlement.payment_date >= d_from,
                 OtaSettlement.payment_date < d_to))
    if linked_only:
        q = q.filter(OtaSettlementLine.booking_id.isnot(None),
                     OtaSettlementLine.match_status.in_(("matched", "amount_diff", "cancelled_booking")))
    else:
        q = q.filter(OtaSettlementLine.match_status.in_(("unknown_booking",)))
    return {ch: _r(v) for ch, v in q.group_by(OtaSettlementLine.channel_code).all()}


def _payable(db, chans, d_from, d_to):
    """Voucher payable of live stays starting in [d_from, d_to), per channel - what the OTA owes for them."""
    bs = _tracked(db, chans, since=d_from, upto=d_to - timedelta(days=1))
    out = {}
    for b in bs:
        if b.status in PAYABLE and b.ota_net_payout is not None:
            out[b.booking_source] = _r(out.get(b.booking_source, 0) + float(b.ota_net_payout))
    return out


def month_statement(db, month: date, channel=None) -> dict:
    """Per OTA for one month: opening owed + payable for stays starting this month - received this
    month = closing owed. Receipts for bookings the PMS does not have are shown separately (they cannot
    be set against a stay until someone links them on OTA Payments -> Exceptions)."""
    chans = P.channels_of(channel)
    start = P.tracking_start(db) or month
    nxt = _month_add(month, 1)
    before_pay = _payable(db, chans, start, month)
    before_rec = _received(db, chans, start, month)
    now_pay = _payable(db, chans, month, nxt)
    now_rec = _received(db, chans, month, nxt)
    unalloc = _received(db, chans, month, nxt, linked_only=False)
    rows = []
    for ch in chans:
        opening = _r(before_pay.get(ch, 0) - before_rec.get(ch, 0))
        closing = _r(opening + now_pay.get(ch, 0) - now_rec.get(ch, 0))
        rows.append({"channel": ch, "label": LABEL[ch], "opening": opening, "stays": now_pay.get(ch, 0.0),
                     "received": now_rec.get(ch, 0.0), "closing": closing,
                     "unallocated_receipts": unalloc.get(ch, 0.0)})
    keys = ("opening", "stays", "received", "closing", "unallocated_receipts")
    return {"month": str(month), "tracking_from": str(start), "rows": rows,
            "totals": {k: _r(sum(r[k] for r in rows)) for k in keys}}


def tally_summary(db, month: date, channel=None) -> dict:
    """The month's OTA figures for the books, per channel, and the journal entries they suggest:

      J1 OTA deductions  Dr Commission on OTA bookings (taxable) / Dr Input IGST on commission /
                         Dr TCS receivable (GST) / Dr TDS receivable (194-O)  -  Cr <OTA> party
      J2 OTA receipts    Dr Bank  -  Cr <OTA> party

    Assumes the stay's sale is already in the books with the OTA party as debtor for the gross (the
    hotel's own GST invoice). Commission and its GST are the OTA's invoices dated in the month; TCS / TDS
    are the registers of the month; receipts are payments by payment date."""
    chans = P.channels_of(channel)
    nxt = _month_add(month, 1)
    gst = registers(db, month, "gst", channel)
    tcs = registers(db, month, "tcs", channel)
    tds = registers(db, month, "tds", channel)
    rec = _received(db, chans, month, nxt)
    unalloc = _received(db, chans, month, nxt, linked_only=False)
    from services.ota_service import ota_gross_basis
    gross = {}
    stays = [b for b in _tracked(db, chans, since=month, upto=nxt - timedelta(days=1)) if b.status in PAYABLE]
    vfs = voucher_figures(db, {b.ota_booking_id for b in stays if b.ota_booking_id})
    for b in stays:
        vf = vfs.get(b.ota_booking_id, {})
        gross[b.booking_source] = _r(gross.get(b.booking_source, 0) + (vf.get("gross") or float(ota_gross_basis(b) or 0)))
    rows, journals = [], []
    for ch in chans:
        g = _channel_of_ota(ch)
        # the Go-MMT invoice is one document for MakeMyTrip + Goibibo: split it by its lines' brand
        com_taxable, com_gst = _commission_for(db, month, ch)
        r = {"channel": ch, "label": LABEL[ch], "gross_stays": gross.get(ch, 0.0),
             "commission_taxable": com_taxable, "commission_gst": com_gst,
             "tcs": tcs["by_channel"].get(ch, 0.0), "tds": tds["by_channel"].get(ch, 0.0),
             "received": rec.get(ch, 0.0) + unalloc.get(ch, 0.0), "group": g}
        rows.append(r)
        party = f"{LABEL[ch]} (OTA)"
        ded = _r(com_taxable + com_gst + r["tcs"] + r["tds"])
        if ded:
            lines = [{"ledger": "Commission on OTA bookings", "debit": com_taxable, "credit": 0},
                     {"ledger": "Input IGST on OTA commission", "debit": com_gst, "credit": 0},
                     {"ledger": "TCS receivable (GST)", "debit": r["tcs"], "credit": 0},
                     {"ledger": "TDS receivable (194-O)", "debit": r["tds"], "credit": 0}]
            lines = [x for x in lines if x["debit"]] + [{"ledger": party, "debit": 0, "credit": ded}]
            journals.append({"journal": f"OTA deductions {month:%b %Y} - {LABEL[ch]}", "lines": lines})
        if r["received"]:
            journals.append({"journal": f"OTA receipts {month:%b %Y} - {LABEL[ch]}", "lines": [
                {"ledger": "Bank", "debit": r["received"], "credit": 0},
                {"ledger": party, "debit": 0, "credit": r["received"]}]})
    keys = ("gross_stays", "commission_taxable", "commission_gst", "tcs", "tds", "received")
    return {"month": str(month), "rows": rows, "totals": {k: _r(sum(r[k] for r in rows)) for k in keys},
            "journals": journals, "gst_documents": len(gst["rows"])}


def _commission_for(db, month, ch):
    """(taxable, GST) of the OTA's commission documents dated in the month, for one channel. The Go-MMT
    monthly invoice covers both brands, so its lines are split by their brand."""
    g = _channel_of_ota(ch)
    q = (db.query(OtaTaxStatementLine.brand, OtaTaxStatementLine.ota_booking_id, OtaTaxStatementLine.base_amount,
                  OtaTaxStatementLine.gst_amount)
         .join(OtaTaxStatement, OtaTaxStatement.id == OtaTaxStatementLine.statement_id)
         .filter(OtaTaxStatement.channel == g, OtaTaxStatement.period == month,
                 OtaTaxStatement.kind.in_(("commission", "credit_memo")), OtaTaxStatement.superseded.is_(False)).all())
    taxable = gst = 0.0
    for brand, oid, base, tax in q:
        mine = (ch == "yatra") or go_mmt_channel(brand, oid) == ch
        if mine:
            taxable += float(base or 0)
            gst += float(tax or 0)
    return _r(taxable), _r(gst)


# ---------------------------------------------------------------------------
# 8. Owner dashboard tiles
# ---------------------------------------------------------------------------
def tiles(db, today=None) -> dict:
    today = today or clock.business_today()
    rec = receivables(db, today=today)
    month = today.replace(day=1)
    earn = channel_earnings(db, month, month)
    ota = [r for r in earn["rows"] if r["channel"] in P.TRACKED_CHANNELS]
    gross = sum(r["gross"] for r in ota)
    com = sum(r["commission"] for r in ota)
    overdue = [r for r in rec["rows"] if r["overdue"]]
    return {"today": str(today), "receivable": rec["total"], "receivable_count": len(rec["rows"]),
            "overdue": _r(sum(r["owed"] for r in overdue)), "overdue_count": len(overdue),
            "commission_pct_this_month": round(100 * com / gross, 1) if gross else None,
            "ota_gross_this_month": _r(gross)}
