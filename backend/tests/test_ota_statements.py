"""P1 batch 3 - Go-MMT monthly TDS / TCS / commission statements.

Spreadsheets are built here with the same headers and sign conventions as the real attachments (TDS
negative when deducted, TCS positive; commission + IGST; credit memo negative). The owner's real
samples are exercised by HotelLock/qa-harness/verify_batch_p1c.py.

    cd backend && python -m pytest tests/test_ota_statements.py -q
"""
import io
import zipfile
from datetime import date
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from xml.sax.saxutils import escape

import pytest

ME, PARADISE = "HTLDOM014889", "HTLDOM014793"


def xlsx(rows):
    """A minimal .xlsx (inline strings, numbers as numbers)."""
    def cell(ref, v):
        if isinstance(v, (int, float)):
            return f'<c r="{ref}"><v>{v}</v></c>'
        return f'<c r="{ref}" t="inlineStr"><is><t>{escape(str(v))}</t></is></c>'
    body = "".join(
        f'<row r="{i + 1}">' + "".join(cell(f"{chr(65 + j)}{i + 1}", v) for j, v in enumerate(r)) + "</row>"
        for i, r in enumerate(rows))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("xl/workbook.xml", '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"/>')
        z.writestr("xl/worksheets/sheet1.xml",
                   '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                   f"<sheetData>{body}</sheetData></worksheet>")
    return buf.getvalue()


TDS_HEAD = ["Booking Date", "Booking No_", "PNR No.", "Booking status", "Vendor Code", "Hotel Code", "Vendor Name",
            "Guest Name", "Chk-In Date", "Chk-Out Date", "Total booking amount", "TDS Base Amount", "Total TDS",
            "Advance_TDS_Flag"]
TCS_HEAD = ["Booking Date", "Booking No_", "MMT Vendor Code", "Guest Name", "PNR", "Chk-In Date", "Chk-Out Date",
            "E-Commerce TCS_No_", "Vendor_GSTN_", "Nature Of Supply", "Brand", "TCS Base Amount", "CTCS Amount",
            "STCS Amount", "Total TCS"]
COM_HEAD = ["Booking ID", "CheckIn - CheckOut Date", "Commission Invoice No", "Guest Name", "PNR", "Vendor No",
            "HotelName", "Hotel Code", "Company Code", "Commission", "PLB/VDI Income", "CGST/SGST", "IGST_",
            "Total Invoice Value", "ROE", "Conversion Amount"]


def tds_row(bid, base, code=ME):
    return ["", bid, "0100", "", code, "", "Hotel Bhimas", "G", "12/07/2026", "13/07/2026", "", base, round(base / 1000, 2), "0"]


def tcs_row(bid, base, code=ME):
    t = round(base * 0.005, 2)
    return ["01/Aug/2026", bid, code, "G", "0100", "04/Aug/2026", "05/Aug/2026", "x", "y", "B2C", "GOIBIBO", base,
            t / 2, t / 2, t]


def com_row(bid, commission, inv="H1101INV1", code=ME):
    igst = round(commission * 0.18, 2)
    return [bid, "25-Oct-2026 26-Oct-2026", inv, "G", "0100", code, "K R G", "1", "MMTINDIA", commission, 0, 0.0,
            igst, commission + igst, 1.0, commission + igst]


def statement_mail(subject, data, name="report.xlsx", pdf=None, msg_id="<s1@x>", sender="feedback@zen2-makemytrip.com"):
    m = MIMEMultipart()
    m["From"], m["Subject"], m["Message-ID"] = f"MMTINDIA <{sender}>", subject, msg_id
    m["Date"] = "Tue, 13 Oct 2026 14:35:32 +0530"
    m.attach(MIMEText("Please find attached the statement", "plain"))
    a = MIMEApplication(data, "zip")
    a.add_header("Content-Disposition", "attachment", filename=name)
    m.attach(a)
    if pdf:
        p = MIMEApplication(pdf, "pdf")
        p.add_header("Content-Disposition", "attachment", filename="inv_Invoice.pdf")
        m.attach(p)
    return m.as_bytes()


TDS_SUBJ = "MakeMyTrip (India) Limited-TDS statement for Bookings in, JULY,2026 at Hotel Bhimas Tirupati"
TCS_SUBJ = "MakeMyTrip - TCS statement for Bookings in AUGUST 2026 at Hotel Bhimas, Tirupati"
COM_SUBJ = "Commission Income for the month of Oct 2026"
CRM_SUBJ = "MMTINDIA-Monthly Commission Statement AUGUST,2026 at K R G HOTELS PRIVATE LTD null"


def _booking(db, oid, status="checked_out", source="goibibo"):
    from models import Booking
    b = Booking(check_in=date(2026, 7, 12), check_out=date(2026, 7, 13), booking_source=source, status=status,
                total_amount=1000, grand_total=1000, ota_booking_id=oid, guest_name="t")
    db.add(b)
    db.flush()
    return b


def _voucher(db, oid, tcs=None, tds=None):
    from models import OtaDraftBooking
    db.add(OtaDraftBooking(channel_code="goibibo", ota_booking_id=oid, kind="confirmation", status="confirmed",
                           tcs_amount=tcs, tds_amount=tds))
    db.flush()


# ---------------------------------------------------------------------------
@pytest.mark.parametrize("subject,want", [
    (TDS_SUBJ, ("tds", date(2026, 7, 1))),
    ("MakeMyTrip (India) Pvt. Ltd.-TDS statement for Bookings in, MARCH,2026 at Hotel Bhimas Tirupati",
     ("tds", date(2026, 3, 1))),
    (TCS_SUBJ, ("tcs", date(2026, 8, 1))),
    (COM_SUBJ, ("commission", date(2026, 10, 1))),
    (CRM_SUBJ, ("credit_memo", date(2026, 8, 1))),
    ("New Booking Received for Hotel Bhimas on MakeMyTrip - NH1", None),
])
def test_detect(subject, want):
    from services.ota_statements import detect
    assert detect("feedback@zen2-makemytrip.com", subject) == want
    assert detect("someone@example.com", subject) is None


def test_tds_signs_and_netting(db):
    from models import OtaTaxStatementLine
    from services import ota_payments as P
    rows = [TDS_HEAD, tds_row("GH1", -1900), tds_row("GH2", -1550), tds_row("GH2", 1550)]
    r = P.ingest_mail(db, statement_mail(TDS_SUBJ, xlsx(rows), name="TDS_REPORT.xlsx"))
    assert r["statement"] and r["stored"] and r["rows"] == 3 and r["total"] == 1.9
    net = {}
    for x in db.query(OtaTaxStatementLine).all():
        net[x.ota_booking_id] = round(net.get(x.ota_booking_id, 0) + float(x.amount), 2)
    assert net == {"GH1": 1.9, "GH2": 0.0}       # deducted shown positive; the cancelled one nets to 0


def test_tcs_commission_and_credit_memo(db):
    from models import OtaTaxStatement
    from services import ota_payments as P
    P.ingest_mail(db, statement_mail(TCS_SUBJ, xlsx([TCS_HEAD, tcs_row("GH1", 1900), tcs_row("GH9", -1900)]), msg_id="<a>"))
    P.ingest_mail(db, statement_mail(COM_SUBJ, xlsx([COM_HEAD, com_row("NH1", 660), com_row("NH2", 340)]),
                                     pdf=b"%PDF-1.4 fake", msg_id="<b>"))
    crm = [["Booking ID", "CheckIn - CheckOut Date", "Credit Memo No", "Commission Invoice No", "Guest Name", "PNR",
            "Vendor No", "HotelName", "Hotel Code", "Company Code", "Credit Memo", "PLB/VDI Income", "CGST/SGST",
            "IGST_", "Total Invoice Value", "ROE", "Conversion Amount"],
           ["GH3", "15-Jul-2026 16-Jul-2026", "H1101CM1", "", " ", "0100", ME, "K R G", "1", "GOIBIBO", -380.0, 0,
            0.0, -68.4, -448.4, 1.0, -448.4]]
    P.ingest_mail(db, statement_mail(CRM_SUBJ, xlsx(crm), msg_id="<c>"))
    s = {x.kind: x for x in db.query(OtaTaxStatement).all()}
    assert float(s["tcs"].total_amount) == 0.0 and float(s["tcs"].total_base) == 0.0
    assert float(s["commission"].total_base) == 1000.0 and float(s["commission"].total_gst) == 180.0
    assert float(s["commission"].total_amount) == 1180.0 and s["commission"].doc_no == "H1101INV1"
    assert s["commission"].pdf_data == b"%PDF-1.4 fake"
    assert float(s["credit_memo"].total_amount) == -448.4 and s["credit_memo"].doc_no == "H1101CM1"


def test_paradise_rows_are_not_stored(db):
    from models import OtaTaxStatement
    from services import ota_payments as P
    r = P.ingest_mail(db, statement_mail(TDS_SUBJ, xlsx([TDS_HEAD, tds_row("GH1", -1000), tds_row("GHP", -500, PARADISE)])))
    assert r["rows"] == 1 and r["other_rows"] == 1 and r["total"] == 1.0
    only = P.ingest_mail(db, statement_mail(TCS_SUBJ, xlsx([TCS_HEAD, tcs_row("GHP", 500, PARADISE)]), msg_id="<p>"))
    assert only["stored"] is False and only["reason"] == "other property"
    assert db.query(OtaTaxStatement).count() == 1


def test_resent_statements(db):
    from models import OtaTaxStatement
    from services import ota_payments as P
    P.ingest_mail(db, statement_mail(TDS_SUBJ, xlsx([TDS_HEAD, tds_row("GH1", -1000)]), msg_id="<t1>"))
    P.ingest_mail(db, statement_mail(TDS_SUBJ, xlsx([TDS_HEAD, tds_row("GH1", -2000)]), msg_id="<t2>"))
    live = db.query(OtaTaxStatement).filter_by(kind="tds", superseded=False).all()
    assert len(live) == 1 and float(live[0].total_amount) == 2.0       # the later one replaces the earlier
    c = statement_mail(COM_SUBJ, xlsx([COM_HEAD, com_row("NH1", 100)]), msg_id="<c1>")
    P.ingest_mail(db, c)
    again = P.ingest_mail(db, statement_mail(COM_SUBJ, xlsx([COM_HEAD, com_row("NH1", 100)]), msg_id="<c2>"))
    assert again["stored"] is False and again["reason"] == "already recorded"     # same invoice number
    assert P.ingest_mail(db, c)["reason"] == "already recorded"                    # same Message-ID


def test_month_detail_against_vouchers(db):
    from services import ota_payments as P
    from services import ota_statements as S
    _booking(db, "GH1")
    _voucher(db, "GH1", tds=1.9)                 # voucher agrees
    _booking(db, "GH4")
    _voucher(db, "GH4", tds=5.0)                 # voucher says 5, statement 1
    _booking(db, "GH2", status="cancelled")
    _voucher(db, "GH2", tds=1.55)                # cancelled: statement must net to 0
    rows = [TDS_HEAD, tds_row("GH1", -1900), tds_row("GH4", -1000), tds_row("GH2", -1550), tds_row("GH2", 1550),
            tds_row("GH7", -500)]
    P.ingest_mail(db, statement_mail(TDS_SUBJ, xlsx(rows)))
    d = {r["ota_booking_id"]: r for r in S.month_detail(db, date(2026, 7, 1))["rows"]}
    assert d["GH1"]["tds_check"] == "ok"
    assert d["GH4"]["tds_check"] == "differs" and "tds_differs" in d["GH4"]["flags"]
    assert d["GH2"]["tds_check"] == "ok" and d["GH2"]["tds_voucher"] == 0.0
    assert d["GH7"]["tds_check"] == "no_voucher" and "not_in_pms" in d["GH7"]["flags"]
    assert d["GH1"]["tcs_check"] == "not_in_statement"


def test_month_report_missing_and_alerts(db):
    from models import FraudAlert
    from services import ota_payments as P
    from services import ota_statements as S
    from utils.fraud_detection import _make_alert
    _booking(db, "GH1")                           # tracking starts July 2026
    P.ingest_mail(db, statement_mail(TDS_SUBJ, xlsx([TDS_HEAD, tds_row("GH1", -1900)])))
    rep = {m["period"]: m for m in S.month_report(db, months=6, today=date(2026, 8, 20))["months"]}
    assert set(rep) == {"2026-07-01", "2026-08-01"}
    assert rep["2026-07-01"]["missing"] == ["tcs", "commission"] and rep["2026-07-01"]["tds"]["statement"] == 1.9
    assert rep["2026-08-01"]["due"] is False and rep["2026-08-01"]["missing"] == []
    new = []
    P.detect_alerts(db, lambda **kw: _make_alert(new, set(), **kw), today=date(2026, 8, 20))
    for a in new:
        db.add(a)
    db.flush()
    assert {(a.type, a.severity) for a in new if a.type == S.ALERT_MISSING} == {(S.ALERT_MISSING, "low")}
    # the TCS statement arrives -> its alert closes on the next sweep
    got = P.ingest_mail(db, statement_mail(TCS_SUBJ.replace("AUGUST", "JULY"), xlsx([TCS_HEAD, tcs_row("GH1", 1900)]), msg_id="<x>"))
    assert got.get("stored"), got
    P.detect_alerts(db, lambda **kw: _make_alert([], {"x"}, **kw), today=date(2026, 8, 20))
    db.flush()          # the test session does not autoflush; the real sweep commits
    open_ = [a.detail for a in db.query(FraudAlert).filter_by(type=S.ALERT_MISSING, status="open").all()]
    assert all('"tcs"' not in d for d in open_) and any('"commission"' in d for d in open_)


def test_forwarded_statement_from_owner(db):
    from email.mime.message import MIMEMessage
    import email as _email
    from models import OtaTaxStatement
    from services import ota_payments as P
    from utils.settings import set_setting, OTA_PAYMENT_FORWARDERS_KEY
    set_setting(db, OTA_PAYMENT_FORWARDERS_KEY, "owner@hotel.example")
    db.flush()
    outer = MIMEMultipart()
    outer["From"], outer["Subject"] = "owner@hotel.example", "Fwd: statements"
    outer.attach(MIMEMessage(_email.message_from_bytes(statement_mail(TDS_SUBJ, xlsx([TDS_HEAD, tds_row("GH1", -1000)])))))
    r = P.ingest_mail(db, outer.as_bytes())
    assert r["forwarded"] == 1 and r["statements_stored"] == 1 and db.query(OtaTaxStatement).count() == 1


# --- HTTP -------------------------------------------------------------------------------
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


def test_http_statements(db, http):
    from models import OtaTaxStatement
    client, role = http
    _booking(db, "GH1")
    db.commit()
    raw = statement_mail(COM_SUBJ, xlsx([COM_HEAD, com_row("GH1", 660)]), pdf=b"%PDF-1.4 x")
    r = client.post("/ota/payments/upload-mail", files={"file": ("m.eml", raw, "message/rfc822")})
    assert r.status_code == 200 and r.json()["statement"] and r.json()["stored"]
    bad = client.post("/ota/payments/upload-mail", files={"file": ("m.eml", b"Subject: hi\n\nhello", "message/rfc822")})
    assert bad.status_code == 400
    sid = db.query(OtaTaxStatement).one().id
    f = client.get(f"/ota/statements/{sid}/file?part=pdf")
    assert f.status_code == 200 and f.content == b"%PDF-1.4 x" and "attachment" in f.headers["content-disposition"]
    assert client.get(f"/ota/statements/{sid}/file?part=xlsx").content[:2] == b"PK"
    assert client.get("/ota/statements/months").status_code == 200
    m = client.get("/ota/statements/month/2026-10").json()
    assert m["rows"][0]["commission"] == 778.8
    assert client.get("/ota/statements/month/oct").status_code == 400
    role["role"] = "reception"
    assert client.get("/ota/statements/months").status_code == 403
    assert client.get(f"/ota/statements/{sid}/file").status_code == 403
