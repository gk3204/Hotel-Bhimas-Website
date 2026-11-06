"""P2b - OTA management and accounting reports across Goibibo, MakeMyTrip and Yatra.

Data is built with the P1 / P2a test helpers (synthetic Go-MMT payment mails and statements, Yatra YES
Bank advices and invoices). The owner's real samples are exercised by HotelLock/qa-harness/verify_batch_p2b.py.

    cd backend && python -m pytest tests/test_ota_reports.py -q
"""
from datetime import date, timedelta

import pytest

from tests.test_ota_payments import ROWS, payment_mail
from tests.test_ota_yatra import advice_mail, invoice_pdf, mail

TODAY = date(2026, 10, 20)


def _b(db, oid, net, source, ci, co, status="checked_out", commission=None, tcs=None, tds=None, gross=None,
       agent=False, rooms=1):
    from models import Booking, BookingItem, OtaDraftBooking
    b = Booking(check_in=ci, check_out=co, booking_source=source, status=status, total_amount=gross or net,
                grand_total=gross or net, ota_booking_id=oid, ota_net_payout=net if oid else None, guest_name="t",
                commission_amount=commission if agent else None, agent_id=None)
    db.add(b)
    db.flush()
    if rooms > 1:
        db.add(BookingItem(booking_id=b.booking_id, room_type_id=1, quantity=rooms, total_amount=gross or net))
    if oid:
        db.add(OtaDraftBooking(channel_code=source, ota_booking_id=oid, kind="confirmation", status="confirmed",
                               amount=gross, commission_amount=commission, net_payout=net, tcs_amount=tcs, tds_amount=tds))
    db.flush()
    return b


@pytest.fixture()
def data(db):
    """MakeMyTrip paid in full; Goibibo short paid; one MakeMyTrip never paid (overdue); a Yatra stay paid;
    a Yatra stay never paid (overdue from check-in); a direct booking; a Go-MMT line for a booking the PMS
    does not have."""
    from services import ota_payments as P
    d = {}
    d["mmt"] = _b(db, "NH11111111111111", 9050.0, "makemytrip", date(2026, 10, 11), date(2026, 10, 13),
                  commission=2643.2, tcs=56.0, tds=11.2, gross=11760.0)
    d["gi"] = _b(db, "GH22222222222222", 2700.0, "goibibo", date(2026, 10, 9), date(2026, 10, 10),
                 commission=600.0, gross=3300.0)                           # paid 2,666: short by 34
    d["late"] = _b(db, "NH44444444444444", 1500.0, "makemytrip", date(2026, 10, 1), date(2026, 10, 2))
    d["y"] = _b(db, "0012345678", 999.40, "yatra", date(2026, 10, 2), date(2026, 10, 3), commission=814.2,
                tcs=17.28, tds=3.45, gross=1834.33)
    d["ylate"] = _b(db, "P0000000009", 500.0, "yatra", date(2026, 10, 10), date(2026, 10, 15), status="no_show")
    d["direct"] = _b(db, None, 4000.0, "walk_in", date(2026, 10, 5), date(2026, 10, 7), rooms=2)
    P.ingest_mail(db, payment_mail(ROWS, "12,645.00"))     # NH111.. 9,050 + GH222.. 2,666 + NH333.. 929 (unknown)
    P.ingest_mail(db, advice_mail())                       # Yatra 1,000 for 0012345678
    db.flush()
    return d


# ---------------------------------------------------------------------------
def test_receivables_ageing_and_disputes(db, data):
    from services import ota_reports as R
    r = R.receivables(db, today=TODAY)
    owed = {x["ota_booking_id"]: x for x in r["rows"]}
    assert set(owed) == {"GH22222222222222", "NH44444444444444", "P0000000009"}
    assert owed["GH22222222222222"]["reason"] == "short_paid" and owed["GH22222222222222"]["owed"] == 34.0
    assert owed["NH44444444444444"]["age_days"] == 18 and owed["NH44444444444444"]["bucket"] == "16-30"
    assert owed["P0000000009"]["due_from"] == "2026-10-10" and owed["P0000000009"]["bucket"] == "8-15"   # Yatra: check-in
    assert r["total"] == 34.0 + 1500.0 + 500.0
    by = {c["channel"]: c for c in r["by_channel"]}
    assert by["yatra"]["total"] == 500.0 and by["makemytrip"]["16-30"] == 1500.0
    assert {x["ota_booking_id"] for x in r["disputes"]} == {"GH22222222222222", "NH44444444444444", "P0000000009"}
    only_y = R.receivables(db, channel="yatra", today=TODAY)
    assert [x["ota_booking_id"] for x in only_y["rows"]] == ["P0000000009"]


def test_cash_forecast(db, data):
    from services import ota_reports as R
    _b(db, "NH55555555555555", 3000.0, "makemytrip", TODAY + timedelta(days=1), TODAY + timedelta(days=2))
    _b(db, "P0000000010", 700.0, "yatra", TODAY + timedelta(days=3), TODAY + timedelta(days=4), status="confirmed")
    f = R.cash_forecast(db, today=TODAY, days=7)
    day = {x["date"]: x for x in f["by_day"]}
    # MakeMyTrip checking in tomorrow pays the day after (check-in + 1); Yatra checking in on day 3 pays
    # the day before (check-in - 1) - both land on day 2
    assert day[str(TODAY + timedelta(days=2))]["by_channel"] == {"makemytrip": 3000.0, "yatra": 700.0}
    assert day[str(TODAY + timedelta(days=2))]["total"] == 3700.0
    assert day[str(TODAY + timedelta(days=1))]["total"] == 0
    assert f["total"] == 3700.0 and f["late_total"] == 2000.0     # NH444 (1,500) + P..09 (500) already late
    assert f["lag"]["yatra"]["days"] == -1 and f["lag"]["makemytrip"]["learned"] is False


def test_channel_earnings(db, data):
    from services import ota_reports as R
    e = R.channel_earnings(db, date(2026, 10, 1), date(2026, 10, 1))
    row = {r["channel"]: r for r in e["rows"]}
    m = row["makemytrip"]
    assert m["bookings"] == 2 and m["voucher_missing"] == 1          # NH444 has no voucher commission
    assert m["commission_pct"] == round(100 * 2643.2 / 11760.0, 1)    # over the known booking only
    assert m["net"] == round(11760.0 - 2643.2 - 56.0 - 11.2, 2)
    d = row["direct"]
    assert d["room_nights"] == 4 and d["commission_pct"] == 0.0 and d["gross_per_room_night"] == 1000.0
    assert row["yatra"]["commission_pct"] == round(100 * 814.2 / 1834.33, 1)


def test_leakage(db, data):
    from services import ota_reports as R
    lk = R.leakage(db, today=TODAY)
    assert [x["ota_booking_id"] for x in lk["paid_not_in_pms"]["rows"]] == ["NH33333333333333"]
    assert lk["paid_not_in_pms"]["amount"] == 929.0
    assert {x["ota_booking_id"] for x in lk["never_paid"]["rows"]} == {"NH44444444444444", "P0000000009"}
    assert lk["mispaid"]["rows"][0]["amount"] == -34.0


def test_registers_tally_statement(db, data):
    from services import ota_reports as R
    from services import ota_statements as S
    from tests.test_ota_statements import (COM_HEAD, COM_SUBJ, TDS_HEAD, TDS_SUBJ, xlsx, statement_mail, com_row,
                                           tds_row)
    S.ingest_yatra_documents(db, mail("donotreply@yatra.com", "x", invoice_pdf(), name="i.pdf", msg_id="<inv@x>"))
    # Go-MMT October commission invoice: MakeMyTrip 660 + GOIBIBO-branded row
    rows = [COM_HEAD, com_row("NH11111111111111", 660)]
    gi = com_row("GH22222222222222", 340)
    gi[8] = "GOIBIBO"
    rows.append(gi)
    S.ingest_statement_email(db, statement_mail(COM_SUBJ, xlsx(rows), pdf=b"%PDF-1.4 x", msg_id="<com@x>"))
    S.ingest_statement_email(db, statement_mail(TDS_SUBJ.replace("JULY", "OCTOBER"), xlsx([TDS_HEAD, tds_row("NH11111111111111", -11200)]),
                                                msg_id="<tds@x>"))
    gst = R.registers(db, date(2026, 10, 1), "gst")
    assert {r["doc_no"] for r in gst["rows"]} == {"H1101INV1"}       # the Yatra invoice is dated Sep (period Sep)
    gst_sep = R.registers(db, date(2026, 9, 1), "gst")
    assert gst_sep["rows"][0]["supplier_gstin"] == "27AAACY2602D1ZS" and gst_sep["rows"][0]["gst"] == 124.2
    tds = R.registers(db, date(2026, 10, 1), "tds")
    by = {(r["channel"], r["ota_booking_id"]): r for r in tds["rows"]}
    assert by[("makemytrip", "NH11111111111111")]["amount"] == 11.2 and by[("makemytrip", "NH11111111111111")]["source"] == "Go-MMT statement"
    assert by[("yatra", "0012345678")]["amount"] == 3.45 and by[("yatra", "0012345678")]["deductor_pan"] == "AAACY2602D"
    assert R.registers(db, date(2026, 10, 1), "tcs", channel="yatra")["totals"]["amount"] == 17.28

    t = R.tally_summary(db, date(2026, 10, 1))
    rows_ = {r["channel"]: r for r in t["rows"]}
    assert rows_["makemytrip"]["commission_taxable"] == 660.0 and rows_["goibibo"]["commission_taxable"] == 340.0
    assert rows_["makemytrip"]["received"] == 9050.0 + 929.0          # incl. the receipt not yet matched
    for j in t["journals"]:
        assert round(sum(x["debit"] for x in j["lines"]), 2) == round(sum(x["credit"] for x in j["lines"]), 2), j
        assert all(x["debit"] or x["credit"] for x in j["lines"])     # no zero lines

    st = R.month_statement(db, date(2026, 10, 1))
    r = {x["channel"]: x for x in st["rows"]}
    assert r["makemytrip"]["stays"] == 9050.0 + 1500.0 and r["makemytrip"]["received"] == 9050.0
    assert r["makemytrip"]["closing"] == 1500.0 and r["makemytrip"]["unallocated_receipts"] == 929.0
    assert r["yatra"]["stays"] == 999.4 + 500.0 and r["yatra"]["closing"] == round(999.4 + 500.0 - 1000.0, 2)


def test_tiles(db, data):
    from services import ota_reports as R
    t = R.tiles(db, today=TODAY)
    assert t["receivable"] == 2034.0 and t["overdue_count"] == 2 and t["commission_pct_this_month"] is not None


# --- HTTP: who may see what ---------------------------------------------------------------
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


ACCOUNTING = ["/ota/reports/receivables", "/ota/reports/registers?month=2026-10&kind=gst",
              "/ota/reports/registers?month=2026-10&kind=tds", "/ota/reports/tally?month=2026-10",
              "/ota/reports/statement?month=2026-10"]
MANAGEMENT = ["/ota/reports/forecast", "/ota/reports/earnings?from=2026-09&to=2026-10", "/ota/reports/leakage",
              "/ota/reports/tiles"]


def test_http_roles(db, http, data):
    client, role = http
    db.commit()
    for path in ACCOUNTING + MANAGEMENT:
        assert client.get(path).status_code == 200, path
    role["role"] = "accountant"
    for path in ACCOUNTING:
        assert client.get(path).status_code == 200, path
    for path in MANAGEMENT:
        assert client.get(path).status_code == 403, path
    role["role"] = "reception"
    for path in ACCOUNTING + MANAGEMENT:
        assert client.get(path).status_code == 403, path
    role["role"] = "admin"
    assert client.get("/ota/reports/tally?month=oct").status_code == 400
    assert client.get("/ota/reports/registers?month=2026-10&kind=vat").status_code == 422
    assert client.get("/ota/reports/receivables?channel=nope").status_code == 400
    assert client.get("/ota/reports/earnings?from=2026-11&to=2026-10").status_code == 400
