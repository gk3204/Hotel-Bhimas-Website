"""P1 Go-MMT payment tracking - parser, matching and the pending / done / received state.

The mail below copies the layout of a real "MMT Payment(Wire) Confirmation" (nested layout tables,
a header row naming the columns, one <tr> per booking, a "Total:" row) with made-up guests, ids and
account numbers - the real samples carry guest names and bank details and stay out of the repo
(HotelLock/qa-harness/verify_batch_p1.py runs against them).

    cd backend && python -m pytest tests/test_ota_payments.py -q
"""
from datetime import date, timedelta
from email.mime.text import MIMEText

import pytest

CELL = '<td width="10" align="center" valign="top" style="font-family:arial;">{}</td>'


def _row(*cells):
    return "<tr><td>&nbsp;</td>" + "".join(CELL.format(c) for c in cells) + "</tr>"


def payment_html(rows, total, utr="HDFCH09999999901", account="3124969778", when="12/10/2026"):
    head = _row("Booking ID", "Booking PNR", "Client Name", "Hotel Name", "Hotel City", "Check-in",
                "Check-Out", "Original Cost", "Payable", "Booking Type", "Brand")
    body = "".join(_row(*r) for r in rows)
    return f"""<html><body><table><tr><td><table>
      <tr><td><span>Payment Released (Wire)</span><br><span>Payment Processing Date - Mon, 12 October 26</span></td></tr>
      </table></td></tr>
      <tr><td><table>
        <tr><td>&nbsp;</td><td>Bank Name</td><td>Beneficiary Name</td><td>Account Number</td>
            <td>Total Amount Transferred (Rs.)</td><td>Transaction Date  </td><td>Transaction No</td><td>&nbsp;</td></tr>
        <tr><td>&nbsp;</td><td>CENTRAL BANK OF INDIA</td><td>K R G HOTELS PVT</td><td>{account}</td>
            <td>{total}</td><td>{when} </td><td>{utr}</td><td>&nbsp;</td></tr>
      </table></td></tr>
      <tr><td><table>{head}{body}<tr><td colspan="11">Total:{total}</td></tr></table></td></tr>
    </table></body></html>"""


def payment_mail(rows, total, code="HTLDOM014889", msg_id="<p1-test-1@mta.example>", **kw):
    m = MIMEText(payment_html(rows, total, **kw), "html", "utf-8")
    m["From"] = "hotelpayments1@makemytrip.com"
    m["Subject"] = f"MMT Payment(Wire) Confirmation For {code} And Payment Date : 2026-10-12"
    m["Message-ID"] = msg_id
    return m.as_bytes()


ROWS = [
    ("NH11111111111111", "0100000001", "Test Guest One", "Hotel Bhimas", "TIRUPATI", "11/10/2026",
     "13/10/2026", "9,050.00", "9,050.00", "Booked", "MMTINDIA"),
    ("GH22222222222222", "0100000002,0100000003", "", "Hotel Bhimas", "TIRUPATI", "09/10/2026",
     "10/10/2026", "2,666.00", "2,666.00", "Booked", "GOIBIBO"),
    ("NH33333333333333", "0100000004", "Not In Pms", "Hotel Bhimas", "TIRUPATI", "09/10/2026",
     "10/10/2026", "929.00", "929.00", "Booked", "MMTINDIA"),
]


def _booking(db, oid, net, source="makemytrip", status="checked_out", ci=date(2026, 10, 9), co=date(2026, 10, 11)):
    from models import Booking
    b = Booking(check_in=ci, check_out=co, booking_source=source, status=status, total_amount=net,
                grand_total=net, ota_booking_id=oid, ota_net_payout=net, guest_name="t")
    db.add(b)
    db.flush()
    return b


# ---------------------------------------------------------------------------
def test_parse_reads_transfer_and_every_booking():
    from services import ota_payments as P
    p = P.parse_payment_email(payment_mail(ROWS, "12,645.00"))
    assert p["account_code"] == "HTLDOM014889"
    assert p["utr"] == "HDFCH09999999901" and p["total"] == 12645.0 and p["mail_total"] == 12645.0
    assert p["bank_account"] == "3124969778" and p["txn_date"] == date(2026, 10, 12)
    assert [x["ota_booking_id"] for x in p["lines"]] == ["NH11111111111111", "GH22222222222222", "NH33333333333333"]
    first, second = p["lines"][0], p["lines"][1]
    assert first["amount_paid"] == 9050.0 and first["channel_code"] == "makemytrip"
    assert first["check_in"] == date(2026, 10, 11) and first["check_out"] == date(2026, 10, 13)
    # a blank guest name must not shift the payable into another column
    assert second["guest_name"] is None and second["amount_paid"] == 2666.0
    assert second["channel_code"] == "goibibo" and second["pnr"] == "0100000002,0100000003"


def test_not_a_payment_mail():
    from services import ota_payments as P
    m = MIMEText("<p>hello</p>", "html")
    m["From"] = "feedback@zen2-makemytrip.com"
    m["Subject"] = "MakeMyTrip - TCS statement for Bookings in AUGUST 2026"
    assert P.parse_payment_email(m.as_bytes()) is None
    # the right subject from the wrong sender is not money either
    m2 = MIMEText(payment_html(ROWS, "1"), "html")
    m2["From"] = "someone@example.com"
    m2["Subject"] = "MMT Payment(Wire) Confirmation For HTLDOM014889 And Payment Date : 2026-10-12"
    assert P.parse_payment_email(m2.as_bytes()) is None


def test_paradise_payment_is_never_stored(db):
    from models import OtaSettlement
    from services import ota_payments as P
    rows = [("GH44444444444444", "0100000009", "x", "Hotel Bhimas Paradise", "TIRUPATI", "06/10/2026",
             "07/10/2026", "2,079.00", "2,079.00", "Booked", "GOIBIBO")]
    res = P.ingest_payment_email(db, payment_mail(rows, "2,079.00", code="HTLDOM014793", account="15300200000660"))
    assert res["payment"] and not res["stored"] and res["reason"] == "other property"
    assert db.query(OtaSettlement).count() == 0


def test_ingest_matches_and_states(db):
    from models import OtaSettlementLine
    from services import ota_payments as P
    one = _booking(db, "NH11111111111111", 9049.60, ci=date(2026, 10, 11), co=date(2026, 10, 13))
    two = _booking(db, "GH22222222222222", 2500.00, source="goibibo", ci=date(2026, 10, 9), co=date(2026, 10, 10))
    unpaid = _booking(db, "NH55555555555555", 1000.00)
    res = P.ingest_payment_email(db, payment_mail(ROWS, "12,645.00"))
    assert res["stored"] and res["lines"] == 3 and res["flags"] == []
    st = {x.ota_booking_id: x.match_status for x in db.query(OtaSettlementLine).all()}
    # Go-MMT rounds 9,049.60 to 9,050 - inside the ₹1 tolerance
    assert st == {"NH11111111111111": "matched", "GH22222222222222": "amount_diff",
                  "NH33333333333333": "unknown_booking"}

    today = date(2026, 10, 20)
    states = P.ota_payment_states(db, [one, two, unpaid], today=today)
    assert states[one.booking_id]["state"] == "done"
    assert states[one.booking_id]["utrs"] == ["HDFCH09999999901"]
    assert states[two.booking_id]["state"] == "done" and "over_paid" in states[two.booking_id]["flags"]
    assert states[unpaid.booking_id]["state"] == "pending"
    assert states[unpaid.booking_id]["overdue"] is True          # checked out 11 Oct, 3-day rule
    assert P.ota_payment_states(db, [unpaid], today=date(2026, 10, 14))[unpaid.booking_id]["overdue"] is False


def test_same_mail_twice_is_one_payment(db):
    from models import OtaSettlement
    from services import ota_payments as P
    raw = payment_mail(ROWS, "12,645.00")
    assert P.ingest_payment_email(db, raw)["stored"]
    again = P.ingest_payment_email(db, raw)
    assert not again["stored"] and again["reason"] == "already recorded"
    # a re-sent mail (new Message-ID) for the same transfer is caught by the UTR
    assert P.ingest_payment_email(db, payment_mail(ROWS, "12,645.00", msg_id="<other@x>"))["reason"] == "already recorded"
    assert db.query(OtaSettlement).count() == 1


def test_received_state_and_cancelled(db):
    from models import OtaSettlement
    from services import ota_payments as P
    b = _booking(db, "NH11111111111111", 9050.00)
    gone = _booking(db, "GH22222222222222", 2666.00, source="goibibo", status="cancelled")
    never = _booking(db, "NH66666666666666", 500.00, status="cancelled")
    P.ingest_payment_email(db, payment_mail(ROWS, "12,645.00"))
    s = db.query(OtaSettlement).one()
    s.state, s.received_on = "received", date(2026, 10, 13)
    db.flush()
    states = P.ota_payment_states(db, [b, gone, never])
    assert states[b.booking_id]["state"] == "received" and states[b.booking_id]["received_on"] == "2026-10-13"
    assert "paid_but_cancelled" in states[gone.booking_id]["flags"]
    assert states[never.booking_id]["state"] == "not_applicable"


def test_payment_before_booking_links_on_rematch(db):
    from models import OtaSettlementLine
    from services import ota_payments as P
    _booking(db, "NH99999999999999", 100.0, ci=date(2026, 1, 1), co=date(2026, 1, 2))  # tracking starts 1 Jan
    P.ingest_payment_email(db, payment_mail(ROWS, "12,645.00"))
    line = db.query(OtaSettlementLine).filter_by(ota_booking_id="NH33333333333333").one()
    assert line.match_status == "unknown_booking" and line.booking_id is None
    b = _booking(db, "NH33333333333333", 929.00)       # the desk confirms the voucher later
    assert P.rematch_open_lines(db) == 1
    assert line.booking_id == b.booking_id and line.match_status == "matched"


def test_stays_before_tracking_are_not_unknown(db):
    from models import OtaSettlementLine
    from services import ota_payments as P
    _booking(db, "NH77777777777777", 100.0, ci=date(2026, 10, 10), co=date(2026, 10, 11))
    P.ingest_payment_email(db, payment_mail(ROWS, "12,645.00"))
    st = {x.ota_booking_id: x.match_status for x in db.query(OtaSettlementLine).all()}
    assert st["NH33333333333333"] == "before_tracking"   # check-in 9 Oct, PMS's first stay 10 Oct


def test_wrong_bank_account_and_totals_are_flagged(db):
    from services import ota_payments as P
    res = P.ingest_payment_email(db, payment_mail(ROWS, "12,000.00", account="999999999"))
    assert set(res["flags"]) == {"totals_mismatch", "bank_account_mismatch"}


def test_reconciliation_splits_one_transfer_by_brand(db):
    from services import ota_payments as P
    from services.ota_service import _actual_settled
    P.ingest_payment_email(db, payment_mail(ROWS, "12,645.00"))
    d = date(2026, 10, 12)
    assert _actual_settled(db, "makemytrip", d, d) == 9979.0
    assert _actual_settled(db, "goibibo", d, d) == 2666.0
    assert _actual_settled(db, "goibibo", d + timedelta(days=1), d + timedelta(days=5)) == 0


def _forward_as_attachment(sender, *inner):
    from email.mime.message import MIMEMessage
    from email.mime.multipart import MIMEMultipart
    import email as _email
    outer = MIMEMultipart()
    outer["From"] = f"Owner <{sender}>"
    outer["Subject"] = "Fwd: payments"
    outer.attach(MIMEText("see attached", "plain"))
    for raw in inner:
        outer.attach(MIMEMessage(_email.message_from_bytes(raw)))
    return outer.as_bytes()


def test_forward_as_attachment_from_owner_is_read(db):
    from models import OtaSettlement
    from services import ota_payments as P
    from utils.settings import set_setting, OTA_PAYMENT_FORWARDERS_KEY
    set_setting(db, OTA_PAYMENT_FORWARDERS_KEY, "owner@hotel.example")
    db.flush()
    paradise = payment_mail([("GH4", "1", "x", "Hotel Bhimas Paradise", "T", "06/10/2026", "07/10/2026",
                              "1.00", "1.00", "Booked", "GOIBIBO")], "1.00", code="HTLDOM014793", msg_id="<pp@x>")
    second = payment_mail(ROWS[:1], "9,050.00", utr="HDFCH09999999902", msg_id="<p1-test-2@x>")
    raw = _forward_as_attachment("owner@hotel.example", payment_mail(ROWS, "12,645.00"), second, paradise)
    res = P.ingest_mail(db, raw)
    assert res["payment"] and res["forwarded"] == 3 and res["stored_count"] == 2 and res["skipped_count"] == 1
    assert db.query(OtaSettlement).count() == 2
    # forwarding the same batch again changes nothing
    assert P.ingest_mail(db, raw)["stored_count"] == 0 and db.query(OtaSettlement).count() == 2


def test_forward_as_attachment_from_stranger_is_ignored(db):
    from models import OtaSettlement
    from services import ota_payments as P
    from utils.settings import set_setting, OTA_PAYMENT_FORWARDERS_KEY
    set_setting(db, OTA_PAYMENT_FORWARDERS_KEY, "owner@hotel.example")
    db.flush()
    raw = _forward_as_attachment("someone@else.example", payment_mail(ROWS, "12,645.00"))
    assert P.ingest_mail(db, raw) == {"payment": False}
    assert db.query(OtaSettlement).count() == 0


@pytest.mark.parametrize("text,tcs,tds", [
    ("7.    TCS @ 0.5% ₹ 23.0      8.    TDS @ 0.1% ₹ 4.6            (C) Tax", 23.0, 4.6),
    (" TCS @ 0.5 % \n ₹ 19.0 \n\n 8. \n TDS @ 0.1 % \n ₹ 3.8 ", 19.0, 3.8),
    ("(C) TCS @0.5% (includes CGST/SGST)\t10.50\n(D) TDS @0.1% TDS under Section 194 O\t2.10\n", 10.5, 2.1),
])
def test_voucher_tcs_tds(text, tcs, tds):
    from services.ota_service import _commission_net
    got = _commission_net(text)
    assert got["tcs_amount"] == tcs and got["tds_amount"] == tds


# ===========================================================================
# Batch 2 - Confirm received / Undo / Add payment / link / alerts / screen data
# ===========================================================================
def _stored(db):
    from models import OtaSettlement
    from services import ota_payments as P
    P.ingest_payment_email(db, payment_mail(ROWS, "12,645.00"))
    return db.query(OtaSettlement).one()


def test_receive_then_undo(db):
    from services import ota_payments as P
    b = _booking(db, "NH11111111111111", 9050.00)
    s = _stored(db)
    P.receive_payment(db, s, user_id=None, received_on=date(2026, 10, 13), received_amount=12645.0, note="seen")
    assert P.ota_payment_state(db, b)["state"] == "received"
    assert P.serialize_payment(s)["received_difference"] == 0.0
    with pytest.raises(P.PaymentError):
        P.receive_payment(db, s)                       # twice
    with pytest.raises(P.PaymentError):
        P.undo_receipt(db, s, reason="")               # a reason is required
    P.undo_receipt(db, s, reason="wrong UTR checked")
    assert s.state == "done" and s.received_on is None and s.undo_reason == "wrong UTR checked"
    assert P.ota_payment_state(db, b)["state"] == "done"
    with pytest.raises(P.PaymentError):
        P.undo_receipt(db, s, reason="again")          # nothing to undo


def test_receive_records_a_bank_difference(db):
    from services import ota_payments as P
    s = _stored(db)
    P.receive_payment(db, s, received_amount=12600.0)
    assert P.serialize_payment(s)["received_difference"] == -45.0
    assert s.received_on == date(2026, 10, 12)         # defaults to the payment date


def test_manual_payment(db):
    from services import ota_payments as P
    a = _booking(db, "NH80000000000001", 1000.00)
    b = _booking(db, "GH80000000000002", 2000.00, source="goibibo")
    walkin = _booking(db, None, 500.00, source="walk_in")
    s = P.create_manual_payment(db, utr="hdfc h0000001", payment_date=date(2026, 10, 20), amount=3000,
                                booking_ids=[a.booking_id, b.booking_id])
    assert s.utr == "HDFCH0000001" and s.source == "manual" and s.flags is None
    st = P.ota_payment_states(db, [a, b])
    assert st[a.booking_id]["state"] == "done" and st[b.booking_id]["state"] == "done"
    with pytest.raises(P.PaymentError, match="already recorded"):
        P.create_manual_payment(db, utr="HDFCH0000001", payment_date=date(2026, 10, 20), amount=1,
                                booking_ids=[a.booking_id])
    with pytest.raises(P.PaymentError, match="not a Goibibo"):
        P.create_manual_payment(db, utr="X1234", payment_date=date(2026, 10, 20), amount=1,
                                booking_ids=[walkin.booking_id])
    odd = P.create_manual_payment(db, utr="X5678", payment_date=date(2026, 10, 21), amount=999,
                                  booking_ids=[a.booking_id], amounts={a.booking_id: 100})
    assert odd.flags == "totals_mismatch"


def test_link_and_ignore_lines(db):
    from models import OtaSettlementLine
    from services import ota_payments as P
    _stored(db)
    line = db.query(OtaSettlementLine).filter_by(ota_booking_id="NH33333333333333").one()
    b = _booking(db, "NH-TYPED-DIFFERENTLY", 929.00)
    P.link_line(db, line, b)
    assert line.booking_id == b.booking_id and line.match_status == "matched" and line.linked_by is None
    # a hand link survives the automatic rematch
    line.linked_by = 1
    assert P.rematch_open_lines(db) == 0 and line.booking_id == b.booking_id
    other = db.query(OtaSettlementLine).filter_by(ota_booking_id="GH22222222222222").one()
    P.ignore_line(db, other, user_id=1)
    assert other.match_status == "ignored"
    assert all(x["id"] != other.id for x in P.exceptions(db)["lines"])


def test_summary_and_exceptions(db):
    from services import ota_payments as P
    paid = _booking(db, "NH11111111111111", 9050.00, ci=date(2026, 10, 11), co=date(2026, 10, 13))
    late = _booking(db, "NH90000000000009", 700.00, ci=date(2026, 10, 1), co=date(2026, 10, 2))
    _booking(db, "NH90000000000010", 800.00, ci=date(2026, 12, 1), co=date(2026, 12, 2))   # not started
    s = _stored(db)
    today = date(2026, 10, 20)
    sm = P.summary(db, today=today)
    assert sm["pending"]["count"] == 1 and sm["overdue"]["count"] == 1 and sm["overdue"]["expected"] == 700.0
    assert sm["awaiting_bank_check"] == {"count": 1, "amount": 12645.0}
    P.receive_payment(db, s, received_on=date(2026, 10, 14))
    assert P.summary(db, today=today)["received_this_month"] == {"count": 1, "amount": 12645.0}
    ex = P.exceptions(db, today=today)
    assert {x["ota_booking_id"] for x in ex["lines"]} >= {"NH33333333333333"}
    assert [r["booking_id"] for r in ex["bookings"]] == [late.booking_id]
    assert paid.booking_id not in [r["booking_id"] for r in ex["bookings"]]


def test_alerts_raise_and_close(db):
    from models import FraudAlert
    from services import ota_payments as P
    from utils.fraud_detection import _make_alert
    late = _booking(db, "NH90000000000009", 700.00, ci=date(2026, 10, 1), co=date(2026, 10, 2))
    s = _stored(db)

    def sweep(today):
        new, keys = [], {a.detail and __import__("json").loads(a.detail).get("dedupe_key")
                         for a in db.query(FraudAlert).filter_by(status="open").all()}
        P.detect_alerts(db, lambda **kw: _make_alert(new, keys, **kw), today=today)
        for a in new:
            db.add(a)
        db.flush()
        return {a.type for a in new}

    got = sweep(date(2026, 10, 20))
    assert got == {"ota_payment_overdue", "ota_payment_unconfirmed", "ota_payment_problem"}
    assert sweep(date(2026, 10, 20)) == set()          # de-duplicated
    # Go-MMT pays the late booking, the admin confirms the bank, the unknown line is ignored
    P.create_manual_payment(db, utr="LATE1", payment_date=date(2026, 10, 21), amount=700,
                            booking_ids=[late.booking_id])
    P.receive_payment(db, s)
    from models import OtaSettlementLine
    for x in db.query(OtaSettlementLine).filter(OtaSettlementLine.match_status.in_(
            ("unknown_booking", "amount_diff"))).all():
        P.ignore_line(db, x)
    sweep(date(2026, 10, 22))
    still = {a.type for a in db.query(FraudAlert).filter_by(status="open").all()}
    assert "ota_payment_overdue" not in still and "ota_payment_problem" not in still
    assert "ota_payment_unconfirmed" not in still or all(
        "LATE1" not in (a.detail or "") for a in db.query(FraudAlert).filter_by(status="open").all())


def test_bank_account_mismatch_alert_is_high(db):
    from services import ota_payments as P
    from utils.fraud_detection import _make_alert
    P.ingest_payment_email(db, payment_mail(ROWS, "12,645.00", account="111"))
    new = []
    P.detect_alerts(db, lambda **kw: _make_alert(new, set(), **kw), today=date(2026, 10, 13))
    probs = [a for a in new if a.type == "ota_payment_problem"]
    assert len(probs) == 1 and probs[0].severity == "high"


# --- HTTP: admin only ---------------------------------------------------------------------
@pytest.fixture()
def http(db):
    import main
    from routers import ota
    from utils import auth_utils
    role = {"role": "admin"}

    def _get_db():
        yield db

    main.app.dependency_overrides[ota.get_db] = _get_db
    main.app.dependency_overrides[auth_utils.get_current_user] = lambda: {"user_id": None, "username": "t",
                                                                          "role": role["role"]}
    from tests.conftest import _SyncAsgiClient
    try:
        yield _SyncAsgiClient(main.app), role
    finally:
        main.app.dependency_overrides.clear()


def test_http_receive_undo_and_guard(db, http):
    client, role = http
    s = _stored(db)
    db.commit()
    role["role"] = "reception"
    assert client.post(f"/ota/payments/{s.id}/receive", json={}).status_code == 403
    assert client.get("/ota/payments/summary").status_code == 403
    role["role"] = "admin"
    r = client.post(f"/ota/payments/{s.id}/receive", json={"received_on": "2026-10-13", "note": "ok"})
    assert r.status_code == 200 and r.json()["state"] == "received" and len(r.json()["lines"]) == 3
    assert client.post(f"/ota/payments/{s.id}/receive", json={}).status_code == 409
    assert client.post(f"/ota/payments/{s.id}/undo", json={"reason": ""}).status_code == 422
    r = client.post(f"/ota/payments/{s.id}/undo", json={"reason": "checked the wrong statement"})
    assert r.status_code == 200 and r.json()["state"] == "done"
    assert client.get("/ota/payments/summary").status_code == 200
    assert client.get("/ota/payments/exceptions").status_code == 200
    from models import AuditLog
    acts = [a.action for a in db.query(AuditLog).all()]
    assert "ota.payment_received" in acts and "ota.payment_receipt_undone" in acts


def test_http_add_payment_and_link(db, http):
    from models import OtaSettlementLine
    client, _ = http
    b = _booking(db, "NH80000000000001", 1000.00)
    db.commit()
    r = client.post("/ota/payments", json={"utr": "MANUAL0001", "payment_date": "2026-10-20", "amount": 1000,
                                           "booking_ids": [b.booking_id]})
    assert r.status_code == 200 and r.json()["source"] == "manual"
    assert client.post("/ota/payments", json={"utr": "MANUAL0001", "payment_date": "2026-10-20", "amount": 1,
                                              "booking_ids": [b.booking_id]}).status_code == 400
    from services import ota_payments as P
    P.ingest_payment_email(db, payment_mail(ROWS, "12,645.00"))
    db.commit()
    line = db.query(OtaSettlementLine).filter_by(ota_booking_id="NH33333333333333").one()
    target = _booking(db, "NH-OTHER", 929.00)
    db.commit()
    r = client.post(f"/ota/payments/lines/{line.id}/link", json={"booking_id": target.booking_id})
    assert r.status_code == 200 and r.json()["match_status"] == "matched" and r.json()["linked_manually"] is False
    st = client.get(f"/ota/payments/booking/{target.booking_id}").json()["state"]
    assert st["state"] == "done"
    assert client.get("/ota/payments/candidates?q=NH8000").status_code == 200
