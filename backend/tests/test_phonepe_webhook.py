"""POST /payments/phonepe/webhook — the callback that posts the folio credit.

This is the highest-consequence code in the PhonePe change: it is what turns "a guest scanned a
QR" into "the folio says they paid". It cannot be tried by hand before the merchant account is
approved, so it is proven here.
"""
from decimal import Decimal

import pytest

from models import FolioCharge, Payment, WebhookEvent


def _phonepe_payment(db, booking, order_ref="BHS-1-abcd1234", amount="1500.00", status="created"):
    payment = Payment(booking_id=booking.booking_id, gateway="phonepe", method="upi",
                      collect_method="upi_qr", amount=Decimal(amount), currency="INR",
                      status=status, order_id=order_ref, upi_intent="upi://pay?pa=test@ybl")
    db.add(payment)
    db.commit()
    return payment


def _body(order_ref, state="COMPLETED", txn="T999"):
    return {
        "event": f"checkout.order.{state.lower()}",
        "payload": {
            "merchantOrderId": order_ref,
            "orderId": "OMO123",
            "state": state,
            "amount": 150000,
            "paymentDetails": [{"transactionId": txn, "paymentMode": "UPI_QR"}],
        },
    }


def test_completed_marks_paid_and_posts_the_folio_credit(client, db, booking_with_folio, webhook_auth):
    payment = _phonepe_payment(db, booking_with_folio)

    resp = client.post("/payments/phonepe/webhook", json=_body(payment.order_id), headers=webhook_auth)
    assert resp.status_code == 200, resp.text
    assert resp.json()["state"] == "COMPLETED"

    db.expire_all()
    payment = db.query(Payment).filter(Payment.payment_id == payment.payment_id).first()
    assert payment.status == "paid"
    assert payment.payment_id_gateway == "T999"

    credits = db.query(FolioCharge).filter(FolioCharge.type == "payment").all()
    assert len(credits) == 1
    assert float(credits[0].amount) == -1500.0, "the credit must be NEGATIVE or the balance grows"


def test_a_replayed_callback_is_not_applied_twice(client, db, booking_with_folio, webhook_auth):
    """PhonePe retries on any non-2xx, so duplicates are expected, not hypothetical.

    Applying one twice would post two credit lines and show the guest as having paid double.
    """
    payment = _phonepe_payment(db, booking_with_folio)
    body = _body(payment.order_id)

    first = client.post("/payments/phonepe/webhook", json=body, headers=webhook_auth)
    second = client.post("/payments/phonepe/webhook", json=body, headers=webhook_auth)

    assert first.json()["status"] == "received"
    assert second.json()["status"] == "already_processed"
    assert db.query(FolioCharge).filter(FolioCharge.type == "payment").count() == 1


def test_a_pending_notification_does_not_block_the_later_completion(client, db, booking_with_folio, webhook_auth):
    """The dedupe key is order+state, not order alone.

    One order legitimately notifies twice — PENDING, then COMPLETED. Keying on the order alone
    would swallow the second and the payment would never be recorded.
    """
    payment = _phonepe_payment(db, booking_with_folio)
    client.post("/payments/phonepe/webhook", json=_body(payment.order_id, state="PENDING"),
                headers=webhook_auth)
    resp = client.post("/payments/phonepe/webhook", json=_body(payment.order_id, state="COMPLETED"),
                       headers=webhook_auth)

    assert resp.json()["status"] == "received"
    db.expire_all()
    assert db.query(Payment).filter(Payment.payment_id == payment.payment_id).first().status == "paid"


def test_bad_credentials_are_rejected_with_401(client, db, booking_with_folio, webhook_auth):
    payment = _phonepe_payment(db, booking_with_folio)
    resp = client.post("/payments/phonepe/webhook", json=_body(payment.order_id),
                       headers={"Authorization": "not-the-digest"})
    assert resp.status_code == 401

    db.expire_all()
    assert db.query(Payment).filter(Payment.payment_id == payment.payment_id).first().status == "created"
    assert db.query(FolioCharge).filter(FolioCharge.type == "payment").count() == 0


def test_failed_state_marks_the_payment_failed(client, db, booking_with_folio, webhook_auth):
    payment = _phonepe_payment(db, booking_with_folio)
    resp = client.post("/payments/phonepe/webhook", json=_body(payment.order_id, state="FAILED"),
                       headers=webhook_auth)
    assert resp.status_code == 200

    db.expire_all()
    assert db.query(Payment).filter(Payment.payment_id == payment.payment_id).first().status == "failed"
    assert db.query(FolioCharge).filter(FolioCharge.type == "payment").count() == 0


def test_an_unknown_order_is_recorded_and_answered_200(client, db, webhook_auth):
    """200, not 404: a non-2xx makes PhonePe retry a callback we will never match."""
    resp = client.post("/payments/phonepe/webhook", json=_body("BHS-999-nothere"), headers=webhook_auth)
    assert resp.status_code == 200

    event = db.query(WebhookEvent).first()
    assert event is not None and event.status == "payment_not_found"


def test_a_callback_without_an_order_id_is_ignored(client, db, webhook_auth):
    resp = client.post("/payments/phonepe/webhook",
                       json={"event": "checkout.order.completed", "payload": {}}, headers=webhook_auth)
    assert resp.status_code == 200
    assert resp.json()["status"] == "ignored"


def test_a_razorpay_payment_is_never_matched_by_the_phonepe_webhook(client, db, booking_with_folio, webhook_auth):
    """Both gateways run at once, and order ids are not namespaced across them."""
    payment = Payment(booking_id=booking_with_folio.booking_id, gateway="razorpay", method="upi",
                      collect_method="upi_qr", amount=Decimal("1500.00"), currency="INR",
                      status="created", order_id="BHS-1-abcd1234")
    db.add(payment)
    db.commit()

    resp = client.post("/payments/phonepe/webhook", json=_body("BHS-1-abcd1234"), headers=webhook_auth)
    assert resp.json()["status"] == "received"

    db.expire_all()
    assert db.query(Payment).filter(Payment.payment_id == payment.payment_id).first().status == "created"
    assert db.query(WebhookEvent).first().status == "payment_not_found"


def test_an_already_paid_payment_is_not_credited_again(client, db, booking_with_folio, webhook_auth):
    """Belt to the dedupe braces: if a callback arrives after the poll already settled it."""
    payment = _phonepe_payment(db, booking_with_folio, status="paid")
    resp = client.post("/payments/phonepe/webhook", json=_body(payment.order_id), headers=webhook_auth)

    assert resp.status_code == 200
    assert db.query(FolioCharge).filter(FolioCharge.type == "payment").count() == 0


def test_a_malformed_body_is_a_400(client, webhook_auth):
    resp = client.post("/payments/phonepe/webhook", content=b"not json",
                       headers={**webhook_auth, "Content-Type": "application/json"})
    assert resp.status_code == 400
