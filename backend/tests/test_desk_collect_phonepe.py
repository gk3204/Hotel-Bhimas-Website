"""POST /payments/desk/collect on PhonePe, the QR image, the status poll, and the gateway switch.

The point of the whole change is that a desk UPI collection costs nothing, so the fee assertion
here is the feature, not a detail.
"""
from decimal import Decimal

import pytest

from models import AppSetting, Payment
from services import phonepe_client


@pytest.fixture()
def phonepe_env(monkeypatch):
    monkeypatch.setenv("PHONEPE_CLIENT_ID", "id")
    monkeypatch.setenv("PHONEPE_CLIENT_SECRET", "secret")
    monkeypatch.setenv("PHONEPE_CLIENT_VERSION", "1")


@pytest.fixture()
def select_phonepe(db):
    db.add(AppSetting(key="desk_pay_gateway", value="phonepe"))
    db.commit()


@pytest.fixture()
def fake_qr(monkeypatch):
    """Stand in for PhonePe's /checkout/v2/pay, recording what we asked for."""
    calls = []

    def _create(order_ref, amount_paise, expiry_seconds, meta=None):
        calls.append({"order_ref": order_ref, "amount_paise": amount_paise,
                      "expiry_seconds": expiry_seconds, "meta": meta})
        return {"order_id": "OMO-1", "intent_url": f"upi://pay?pa=hotel@ybl&am={amount_paise / 100}",
                "expire_at": 1893456000}

    monkeypatch.setattr(phonepe_client, "create_upi_qr", _create)
    monkeypatch.setattr(phonepe_client, "is_configured", lambda: True)
    return calls


def _collect(client, booking, amount=1500.0):
    return client.post("/payments/desk/collect",
                       json={"booking_id": booking.booking_id, "amount": amount,
                             "method": "upi_qr", "client_ref": "desk-test-1"})


# ---- raising the charge ------------------------------------------------------------------

def test_collect_raises_a_phonepe_order_with_no_fee(client, db, booking_with_folio,
                                                    phonepe_env, select_phonepe, fake_qr):
    resp = _collect(client, booking_with_folio)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["collect_method"] == "upi_qr"
    assert body["status"] == "created"
    # The reason this change exists: UPI carries no MDR, desk_pay_fee_on_upi is false, so the
    # guest is charged exactly the amount owed.
    assert body["fee"] == 0.0
    assert body["amount"] == 1500.0

    payment = db.query(Payment).filter(Payment.payment_id == body["payment_id"]).first()
    assert payment.gateway == "phonepe"
    assert payment.order_id.startswith(f"BHS-{payment.payment_id}-")
    assert payment.upi_intent.startswith("upi://pay?")
    assert fake_qr[0]["amount_paise"] == 150000, "PhonePe takes paise, not rupees"


def test_the_order_ref_embeds_the_payment_id_so_the_webhook_can_find_it(
        client, db, booking_with_folio, phonepe_env, select_phonepe, fake_qr):
    """The webhook's only handle on our row is merchantOrderId, so this is load-bearing."""
    body = _collect(client, booking_with_folio).json()
    payment = db.query(Payment).filter(Payment.order_id == fake_qr[0]["order_ref"]).first()
    assert payment is not None
    assert payment.payment_id == body["payment_id"]


def test_qr_expiry_comes_from_the_setting(client, db, booking_with_folio,
                                          phonepe_env, select_phonepe, fake_qr):
    db.add(AppSetting(key="desk_pay_qr_expiry_minutes", value="5"))
    db.commit()
    _collect(client, booking_with_folio)
    assert fake_qr[0]["expiry_seconds"] == 300


# ---- the QR image ------------------------------------------------------------------------

def test_qr_png_is_rendered_locally_from_the_intent(client, db, booking_with_folio,
                                                    phonepe_env, select_phonepe, fake_qr):
    """No third-party fetch while a guest waits — segno renders the intent here."""
    payment_id = _collect(client, booking_with_folio).json()["payment_id"]
    resp = client.get(f"/payments/{payment_id}/qr.png")

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    assert resp.content[:8] == b"\x89PNG\r\n\x1a\n"


# ---- the status poll ---------------------------------------------------------------------

def test_status_poll_settles_a_payment_when_the_webhook_never_arrived(
        client, db, booking_with_folio, phonepe_env, select_phonepe, fake_qr, monkeypatch):
    """This is why the webhook credentials are optional rather than load-bearing."""
    payment_id = _collect(client, booking_with_folio).json()["payment_id"]
    monkeypatch.setattr(phonepe_client, "fetch_status",
                        lambda ref: {"state": "COMPLETED", "transaction_id": "T777",
                                     "payment_mode": "UPI_QR"})

    resp = client.get(f"/payments/{payment_id}/status")
    assert resp.status_code == 200
    assert resp.json()["status"] == "paid"

    db.expire_all()
    assert db.query(Payment).filter(Payment.payment_id == payment_id).first().payment_id_gateway == "T777"


def test_status_poll_survives_phonepe_being_unreachable(
        client, db, booking_with_folio, phonepe_env, select_phonepe, fake_qr, monkeypatch):
    """A guest is standing at the counter; a gateway outage must not 500 the desk."""
    payment_id = _collect(client, booking_with_folio).json()["payment_id"]
    monkeypatch.setattr(phonepe_client, "fetch_status", lambda ref: None)

    resp = client.get(f"/payments/{payment_id}/status")
    assert resp.status_code == 200
    assert resp.json()["status"] == "created"


def test_expired_state_is_recorded(client, db, booking_with_folio,
                                   phonepe_env, select_phonepe, fake_qr, monkeypatch):
    payment_id = _collect(client, booking_with_folio).json()["payment_id"]
    monkeypatch.setattr(phonepe_client, "fetch_status",
                        lambda ref: {"state": "EXPIRED", "transaction_id": None, "payment_mode": None})

    assert client.get(f"/payments/{payment_id}/status").json()["status"] == "expired"


# ---- failure and fallback ----------------------------------------------------------------

def _refusing_phonepe(monkeypatch):
    """PhonePe configured, but refusing the charge — e.g. UPI_QR not enabled on a new account."""
    def _boom(*a, **kw):
        raise phonepe_client.PhonePeError("UPI_QR not enabled for this merchant")

    monkeypatch.setattr(phonepe_client, "is_configured", lambda: True)
    monkeypatch.setattr(phonepe_client, "create_upi_qr", _boom)


def test_a_phonepe_refusal_falls_through_to_razorpay(
        client, db, booking_with_folio, phonepe_env, select_phonepe, monkeypatch):
    """PhonePe may not have the UPI_QR instrument enabled on a new merchant account.

    A guest is standing at the counter, so a 2% collection beats no collection: the desk hands
    back to the Razorpay path, exactly as it already does when Razorpay's own QR Codes are not
    enabled on the account.
    """
    _refusing_phonepe(monkeypatch)
    razorpay_calls = []
    monkeypatch.setattr("routers.payments.get_razorpay_client",
                        lambda: razorpay_calls.append(1) or object())

    _collect(client, booking_with_folio)
    assert razorpay_calls, "the collection must reach Razorpay rather than stopping at PhonePe"
    # And no half-built PhonePe row is left behind for the desk to poll until it expires.
    assert db.query(Payment).filter(Payment.gateway == "phonepe").count() == 0


def test_a_phonepe_refusal_with_no_razorpay_names_the_real_error(
        client, db, booking_with_folio, phonepe_env, select_phonepe, monkeypatch):
    """With nothing to fall back to, the receptionist must be told what actually failed.

    Reporting "Razorpay not configured" for a PhonePe outage would send them looking in the
    wrong dashboard.
    """
    _refusing_phonepe(monkeypatch)
    resp = _collect(client, booking_with_folio)

    assert resp.status_code == 502
    assert "UPI_QR not enabled" in resp.json()["detail"]
    assert db.query(Payment).count() == 0


def test_selecting_phonepe_without_credentials_is_a_clear_503(
        client, db, booking_with_folio, select_phonepe, monkeypatch):
    for key in ("PHONEPE_CLIENT_ID", "PHONEPE_CLIENT_SECRET", "PHONEPE_CLIENT_VERSION"):
        monkeypatch.delenv(key, raising=False)
    resp = _collect(client, booking_with_folio)
    assert resp.status_code == 503
    assert "PhonePe" in resp.json()["detail"]


# ---- the switch --------------------------------------------------------------------------

def test_razorpay_stays_the_default(client, db, booking_with_folio, phonepe_env, fake_qr):
    """Deploying this change must not move a single rupee of traffic on its own."""
    resp = _collect(client, booking_with_folio)
    assert resp.status_code == 503
    assert "Razorpay" in resp.json()["detail"]
    assert fake_qr == [], "PhonePe must not be called until it is explicitly selected"


def test_config_endpoint_refuses_a_gateway_with_no_credentials(client, db, monkeypatch):
    for key in ("PHONEPE_CLIENT_ID", "PHONEPE_CLIENT_SECRET", "PHONEPE_CLIENT_VERSION"):
        monkeypatch.delenv(key, raising=False)
    resp = client.put("/payments/desk/config", json={"desk_pay_gateway": "phonepe"})
    assert resp.status_code == 409
    assert "not configured" in resp.json()["detail"]


def test_config_endpoint_switches_the_gateway(client, db, phonepe_env, monkeypatch):
    monkeypatch.setattr(phonepe_client, "is_configured", lambda: True)
    resp = client.put("/payments/desk/config", json={"desk_pay_gateway": "phonepe"})
    assert resp.status_code == 200
    assert resp.json()["gateway"] == "phonepe"
    assert db.query(AppSetting).filter(AppSetting.key == "desk_pay_gateway").first().value == "phonepe"


def test_config_endpoint_rejects_an_unknown_gateway(client, db):
    resp = client.put("/payments/desk/config", json={"desk_pay_gateway": "stripe"})
    assert resp.status_code == 400


def test_env_selects_the_gateway_before_anyone_touches_the_settings_screen(db, monkeypatch):
    """A fresh deployment can choose PhonePe without an admin editing a row first."""
    from utils.settings import get_desk_pay_config
    monkeypatch.setenv("DESK_PAY_GATEWAY", "phonepe")
    assert get_desk_pay_config(db)["gateway"] == "phonepe"

    # ...and a row beats the env, so the owner can switch back mid-shift.
    db.add(AppSetting(key="desk_pay_gateway", value="razorpay"))
    db.commit()
    assert get_desk_pay_config(db)["gateway"] == "razorpay"


def test_an_unrecognised_gateway_value_falls_back_to_razorpay(db):
    """A typo in a setting must never take desk payments down."""
    from utils.settings import get_desk_pay_config
    db.add(AppSetting(key="desk_pay_gateway", value="phonepay"))
    db.commit()
    assert get_desk_pay_config(db)["gateway"] == "razorpay"
