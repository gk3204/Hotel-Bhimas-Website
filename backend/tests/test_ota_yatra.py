"""P2a - Yatra payments (YES Bank advice PDFs), Yatra commission invoices / credit notes, per-channel overdue,
expected-vs-received, the commission check, the booking chain, channel filters and the settings screen.

PDFs are generated here with reportlab, laid out like the real ones (values in the same order and shapes,
made-up references). The owner's real Yatra samples are run by HotelLock/qa-harness/verify_batch_p2a.py.

    cd backend && python -m pytest tests/test_ota_yatra.py -q
"""
import io
from datetime import date
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import pytest


def pdf(*lines):
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    y = 800
    for ln in lines:
        c.drawString(30, y, ln)
        y -= 14
    c.save()
    return buf.getvalue()


def advice_pdf(amount="1,000.00", booking="YATMB0012345678", vendor="58270", payer="Yatra Online Limited",
               bank_ref="YESIG11112222", when="01/10/26", account="XXXXXX9778", stay=("01-Oct-26", "02-Oct-26")):
    return pdf("NEFT PAYMENT ADVICE", "Dear Sir/Madam, We have initiated your payment through NEFT with Beneficiary",
               f"Account No. {account} and IFSC Code CBIN0282088 for the value of Rs. {amount} for the services",
               f"{account} BT26100100000001 XXXXXXXXXXX1303 NA {when}", payer,
               f"Customer Ref. No. : Value Date : Bank Reference No. : {bank_ref} Payment Document No. :",
               f"HTLPYT200000001 {vendor} {booking} Mr Test Guest Double Bed Non AC Room {stay[0]} {stay[1]} 1/0",
               "paymentadvice@yatra.com,hotelbhimas@gmail.com")


def mail(sender, subject, attach=None, name="doc.pdf", msg_id="<y1@x>", body="see attached"):
    m = MIMEMultipart()
    m["From"], m["Subject"], m["Message-ID"] = sender, subject, msg_id
    m["Date"] = "Sun, 25 Oct 2026 17:07:01 +0530"
    m.attach(MIMEText(body, "html"))
    if attach:
        a = MIMEApplication(attach, "octet-stream")
        a.add_header("Content-Disposition", "attachment", filename=name)
        m.attach(a)
    return m.as_bytes()


YES = "Corporate.NetBankingAlerts@YES.BANK.IN"


def advice_mail(msg_id="<y1@x>", **kw):
    return mail(YES, "BENEFICIARY PAYMENT ADVICE", advice_pdf(**kw), name="BT26100100000001.pdf", msg_id=msg_id,
                body="Domestic Fund Transfer transaction processed favoring K R G HOTELS PVT LTD")


def invoice_pdf(doc="THI27/27/0000001", booking="0012345678", commission="690.00", igst="124.20", total="814.20",
                credit=False, ref="THI27/27/0000000"):
    head = (["Tax Credit Note Original for Recipient Yatra Online Limited",
             f"Tax Credit Note No : {doc} Invoice No: {ref} Cancellation Date: 16 Oct 2026"] if credit else
            ["Tax Invoice Original Invoice for Recipient Yatra Online Limited", f"Invoice No.: {doc} PAN No.: AAACY2602D"])
    return pdf(*head, "GST Number - 27AAACY2602D1ZS", "Hotel Information Name Hotel Bhimas GSTIN/UIN 37AAACK9397F1Z3",
               f"Booking ID: {booking} Check-in: 01 Oct 2026 ; Check-out: 02 Oct 2026 Invoice Date: 20 Sep 2026",
               "Booking Date: 20 Sep 2026 Booked by: Test Guest Description Amount (INR) Commission CGST (@0.00% of",
               "commission) SGST (@0.00% of commission) IGST(@18.00% of commission) UTGST(@0.00% of commission)",
               f"{commission} 0.00 0.00 {igst} 0.00 Total Commission Receivable in INR (Amount in words: Rupees x Only)",
               total)


def _booking(db, oid, net, ci=date(2026, 10, 1), co=date(2026, 10, 2), status="checked_out", source="yatra",
             commission=None, tcs=None, tds=None, gross=None):
    from models import Booking, OtaDraftBooking
    b = Booking(check_in=ci, check_out=co, booking_source=source, status=status, total_amount=gross or net,
                grand_total=gross or net, ota_booking_id=oid, ota_net_payout=net, guest_name="t")
    db.add(b)
    db.add(OtaDraftBooking(channel_code=source, ota_booking_id=oid, kind="confirmation", status="confirmed",
                           amount=gross, commission_amount=commission, net_payout=net, tcs_amount=tcs, tds_amount=tds))
    db.flush()
    return b


# ---------------------------------------------------------------------------
def test_parse_advice():
    from services import ota_payments as P
    p = P.parse_yatra_advice_email(advice_mail())
    assert p["vendor_id"] == "58270" and p["total"] == 1000.0 and p["utr"] == "YESIG11112222"
    assert p["txn_date"] == date(2026, 10, 1) and p["bank_account"] == "XXXXXX9778"
    assert [(x["ota_booking_id"], x["amount_paid"], x["channel_code"]) for x in p["lines"]] == [("0012345678", 1000.0, "yatra")]
    p2 = P.parse_yatra_advice_email(advice_mail(booking="IXIJP0002555287"))
    assert p2["lines"][0]["ota_booking_id"] == "P0002555287"


def test_not_yatra():
    from services import ota_payments as P
    assert P.parse_yatra_advice_email(advice_mail(payer="Some Other Company Ltd")) is None   # YES Bank, not Yatra
    assert P.parse_yatra_advice_email(mail("someone@example.com", "BENEFICIARY PAYMENT ADVICE", advice_pdf())) is None


def test_ingest_match_and_states(db):
    from models import OtaSettlement, OtaSettlementLine
    from services import ota_payments as P
    b = _booking(db, "0012345678", 999.40, ci=date(2026, 10, 2), co=date(2026, 10, 3))
    r = P.ingest_mail(db, advice_mail())
    assert r["stored"] and r["channel"] == "yatra" and r["flags"] == []
    s = db.query(OtaSettlement).one()
    assert s.channel_code == "yatra" and s.utr == "YESIG11112222" and s.reference == "BT26100100000001"
    assert db.query(OtaSettlementLine).one().match_status == "matched"          # 999.40 vs 1,000: +-1
    out = P.serialize_payment(s, db.query(OtaSettlementLine).all())
    assert out["expected_total"] == 999.4 and out["expected_difference"] == 0.6 and out["channels"] == ["yatra"]
    assert P.ota_payment_state(db, b)["state"] == "done"
    assert P.ingest_mail(db, advice_mail())["reason"] == "already recorded"


def test_vendor_and_account_checks(db):
    from services import ota_payments as P
    r = P.ingest_payment_email(db, advice_mail(vendor="11111"))
    assert r["stored"] is False and r["reason"] == "unmapped account code"
    r = P.ingest_payment_email(db, advice_mail(account="XXXXXX1234", msg_id="<y2@x>", bank_ref="YESIG99998888"))
    assert r["stored"] and "bank_account_mismatch" in r["flags"]


def test_overdue_from_check_in_for_yatra(db):
    from services import ota_payments as P
    y = _booking(db, "P0000000001", 500.0, ci=date(2026, 10, 1), co=date(2026, 10, 10))
    g = _booking(db, "NH00000000000001", 500.0, ci=date(2026, 10, 1), co=date(2026, 10, 10), source="makemytrip")
    st = P.ota_payment_states(db, [y, g], today=date(2026, 10, 5))
    assert st[y.booking_id]["overdue"] is True and st[y.booking_id]["overdue_from"] == "check_in"
    assert st[g.booking_id]["overdue"] is False and st[g.booking_id]["overdue_from"] == "check_out"


def test_yatra_documents_and_commission_check(db):
    from models import OtaTaxStatement
    from services import ota_service as O
    from services import ota_statements as S
    _booking(db, "0012345678", 2789.07, commission=814.2)
    inv = mail("Yatra <donotreply@yatra.com>", "Amended | Yatra (Travelguru) booking for 0012345678",
               invoice_pdf(), name="THI27/27/0000001.pdf", msg_id="<v1@x>")
    res = O.ingest_email_bytes(db, inv, payments_only=True)
    assert res["yatra_documents"][0]["stored"] and res["yatra_documents"][0]["amount"] == 814.2
    s = db.query(OtaTaxStatement).one()
    assert (s.channel, s.kind, s.doc_no, s.supplier_gstin) == ("yatra", "commission", "THI27/27/0000001", "27AAACY2602D1ZS")
    assert float(s.total_base) == 690.0 and float(s.total_gst) == 124.2 and s.pdf_data[:4] == b"%PDF"
    assert S.ingest_yatra_documents(db, inv)[0]["reason"] == "already recorded"
    rows = {r["ota_booking_id"]: r for r in S.month_detail(db, date(2026, 9, 1), channel="yatra")["rows"]}
    assert rows["0012345678"]["commission_check"] == "ok"
    # a credit note dated the next month for the same booking nets the commission to 0
    cn = mail("donotreply@yatra.com", "Cancellation Request Booking ID(YATMB0012345678)",
              invoice_pdf(doc="THM27/27/0000002", credit=True, ref="THI27/27/0000001"), name="x_cr.pdf", msg_id="<c1@x>")
    S.ingest_yatra_documents(db, cn)
    crm = db.query(OtaTaxStatement).filter_by(kind="credit_memo").one()
    assert float(crm.total_amount) == -814.2 and crm.ref_doc_no == "THI27/27/0000001"
    # the Go-MMT report never shows Yatra documents
    assert all(m["commission"]["total"] == 0 for m in S.month_report(db, months=3, today=date(2026, 10, 20))["months"])


def test_yatra_mail_still_reaches_the_voucher_logic(db):
    from services import ota_service as O
    inv = mail("donotreply@yatra.com", "Yatra (Travelguru) booking for 0012345678", invoice_pdf(),
               name="THI27/27/0000001.pdf", msg_id="<v2@x>")
    res = O.ingest_email_bytes(db, inv)
    assert res.get("reason") != "payments-only import"        # went on past the document step


def test_booking_chain(db):
    from services import ota_payments as P
    from services import ota_statements as S
    b = _booking(db, "0012345678", 2789.07, commission=814.2, tcs=17.28, tds=3.45, gross=3624.0,
                 ci=date(2026, 10, 2), co=date(2026, 10, 3))
    S.ingest_yatra_documents(db, mail("donotreply@yatra.com", "x", invoice_pdf(), name="i.pdf", msg_id="<v3@x>"))
    P.ingest_mail(db, advice_mail(amount="2,789.00"))
    ch = P.booking_chain(db, b, today=date(2026, 10, 3))
    st = {x["key"]: x["status"] for x in ch["steps"]}
    assert st == {"gross": "info", "commission": "ok", "tcs": "voucher_only", "tds": "voucher_only", "payable": "ok",
                  "paid": "ok", "received": "pending"} and ch["breaks"] == []
    walkin = _booking(db, None, 100.0, source="walk_in")
    assert P.booking_chain(db, walkin) is None


# --- HTTP -----------------------------------------------------------------------------
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


def test_http_channels_chain_settings_upload(db, http):
    client, role = http
    b = _booking(db, "0012345678", 1000.0, ci=date(2026, 10, 2), co=date(2026, 10, 3))
    db.commit()
    r = client.post("/ota/payments/upload-mail", files={"file": ("a.eml", advice_mail(), "message/rfc822")})
    assert r.status_code == 200 and r.json()["payment"] and r.json()["stored"]
    r = client.post("/ota/payments/upload-mail",
                    files={"file": ("i.eml", mail("donotreply@yatra.com", "x", invoice_pdf(), name="i.pdf", msg_id="<u@x>"),
                                    "message/rfc822")})
    assert r.status_code == 200 and r.json()["channel"] == "yatra" and r.json()["stored"]
    assert client.get("/ota/payments?channel=yatra&from=2026-09-01&to=2026-10-31").json()["rows"][0]["channels"] == ["yatra"]
    assert client.get("/ota/payments?channel=goibibo&from=2026-09-01&to=2026-10-31").json()["rows"] == []
    assert client.get("/ota/payments?channel=nope").status_code == 400
    assert client.get("/ota/payments/summary?channel=yatra").status_code == 200
    ch = client.get(f"/ota/payments/booking/{b.booking_id}/chain").json()["chain"]
    assert ch["channel"] == "yatra" and [s["key"] for s in ch["steps"]][-1] == "received"
    assert client.get("/ota/statements/months?channel=yatra").status_code == 200
    assert client.get("/ota/statements/months?channel=bogus").status_code == 422
    s = client.get("/ota/payments/settings").json()
    assert s["yatra_vendor_id"] == "58270" and s["bank_account"] == "3124969778"
    assert client.put("/ota/payments/settings", json={"forwarders": "not-an-email"}).status_code == 400
    assert client.put("/ota/payments/settings", json={"yatra_vendor_id": "abc"}).status_code == 400
    r = client.put("/ota/payments/settings", json={"forwarders": "Owner@Hotel.example", "overdue_days": 5})
    assert r.status_code == 200 and r.json()["forwarders"] == "owner@hotel.example" and r.json()["overdue_days"] == 5
    from models import AuditLog
    assert db.query(AuditLog).filter_by(action="ota.payment_settings").count() == 1
    role["role"] = "reception"
    assert client.get("/ota/payments/settings").status_code == 403
    assert client.get(f"/ota/payments/booking/{b.booking_id}/chain").status_code == 403
