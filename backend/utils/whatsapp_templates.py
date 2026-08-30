"""WhatsApp template catalog (prompt 15).

Meta's WhatsApp Cloud API only sends **pre-approved** template messages. This catalog is the
single source of truth for:
  - the admin "Templates" list (name, what it's for, sample body),
  - building the Cloud API `template` payload (component params, in order),
  - which templates respect the opt-out list (guest marketing/review) vs. bypass it
    (owner alerts, OTP, overstay — operationally essential).

`meta_name` is the template's registered name in the WhatsApp Business account. `param_order`
documents the positional body variables ({{1}}, {{2}}, ...) the sender fills from the caller's
`params` dict. The owner submits these for approval in the Meta / BSP console (see how-to-test);
until approved (or until WHATSAPP_* env is set) the sender runs in stub/log mode and no real
template call is made, so the exact registered names can be reconciled at go-live.
"""

# category: utility | marketing | authentication  (Meta's message categories)
TEMPLATES = {
    "checkout_reminder": {
        "meta_name": "checkout_reminder",
        "category": "utility",
        "lang": "en",
        "param_order": ["guest_name", "checkout_time", "room_label"],
        "respect_optout": True,
        "body_preview": (
            "Hi {guest_name}, this is a reminder that your checkout at Hotel Bhimas is around "
            "{checkout_time} (room {room_label}). Reply EXTEND if you'd like to stay longer and "
            "we'll arrange it."
        ),
    },
    "overstay": {
        "meta_name": "overstay_alert",
        "category": "utility",
        "lang": "en",
        "param_order": ["guest_name", "room_label"],
        "respect_optout": False,  # operational — guest has passed checkout
        "body_preview": (
            "Hi {guest_name}, our records show your stay in room {room_label} has passed the "
            "checkout time. Please contact the front desk to extend or check out."
        ),
    },
    "booking_confirmation": {
        "meta_name": "booking_confirmation",
        "category": "utility",
        "lang": "en",
        "param_order": ["guest_name", "check_in", "check_out", "booking_ref"],
        "respect_optout": True,
        "body_preview": (
            "Thank you {guest_name}! Your booking at Hotel Bhimas is confirmed: {check_in} to "
            "{check_out} (ref {booking_ref}). We look forward to hosting you."
        ),
    },
    # Document-header sibling of the text template above. notify_guest_document() sends this
    # one with the PDF attached and silently falls back to the text template when the PDF or
    # the (Meta-approved) document template is unavailable. Body stays short -- the detail is
    # in the attachment.
    "booking_confirmation_doc": {
        "meta_name": "booking_confirmation_doc",
        "category": "utility",
        "lang": "en",
        "param_order": ["guest_name", "booking_ref"],
        "respect_optout": True,
        "body_preview": (
            "Hi {guest_name}, your Hotel Bhimas booking {booking_ref} is confirmed. The full "
            "confirmation is attached."
        ),
    },
    "payment_receipt": {
        "meta_name": "payment_receipt",
        "category": "utility",
        "lang": "en",
        "param_order": ["guest_name", "amount", "method", "booking_ref"],
        "respect_optout": True,
        "body_preview": (
            "Hi {guest_name}, we've received your payment of Rs. {amount} ({method}) for booking "
            "{booking_ref}. Thank you!"
        ),
    },
    # Document-header sibling of the text template above. notify_guest_document() sends this
    # one with the PDF attached and silently falls back to the text template when the PDF or
    # the (Meta-approved) document template is unavailable. Body stays short -- the detail is
    # in the attachment.
    "payment_receipt_doc": {
        "meta_name": "payment_receipt_doc",
        "category": "utility",
        "lang": "en",
        "param_order": ["guest_name", "amount"],
        "respect_optout": True,
        "body_preview": (
            "Hi {guest_name}, we've received your payment of Rs. {amount}. Your receipt is attached."
        ),
    },
    # The final GST invoice, sent once the guest is checked out (reception.py::check_out already
    # creates it via ensure_invoice). respect_optout is FALSE on purpose: a tax invoice is a
    # statutory document the guest is entitled to, not a marketing message.
    "invoice_doc": {
        "meta_name": "invoice_doc",
        "category": "utility",
        "lang": "en",
        "param_order": ["guest_name", "invoice_no"],
        "respect_optout": False,
        "body_preview": (
            "Hi {guest_name}, thank you for staying at Hotel Bhimas. Your tax invoice {invoice_no} "
            "is attached."
        ),
    },
    "room_ready": {
        "meta_name": "room_ready",
        "category": "utility",
        "lang": "en",
        "param_order": ["guest_name", "room_label"],
        "respect_optout": True,
        "body_preview": (
            "Welcome to Hotel Bhimas, {guest_name}! Your room {room_label} is ready. Enjoy your stay — "
            "reply here if you need anything."
        ),
    },
    "review_feedback": {
        "meta_name": "review_feedback",
        "category": "marketing",
        "lang": "en",
        "param_order": ["guest_name", "review_url"],
        "respect_optout": True,
        "body_preview": (
            "Hi {guest_name}, thank you for staying at Hotel Bhimas! If you enjoyed your stay we'd "
            "love a Google review: {review_url}. Had a problem? Reply PROBLEM and we'll make it right."
        ),
    },
    "complaint_ack": {
        "meta_name": "complaint_received",
        "category": "utility",
        "lang": "en",
        "param_order": ["guest_name"],
        "respect_optout": False,  # they're actively complaining — always acknowledge
        "body_preview": (
            "Hi {guest_name}, we're sorry to hear there was a problem. We've logged it and our team "
            "will follow up shortly. Thank you for telling us."
        ),
    },
    "prearrival_link": {
        "meta_name": "prearrival_link",
        "category": "utility",
        "lang": "en",
        "param_order": ["guest_name", "link"],
        "respect_optout": True,
        "body_preview": (
            "Hi {guest_name}, please complete your pre-arrival registration for Hotel Bhimas here: "
            "{link}. It saves you time at check-in."
        ),
    },
    "payment_link": {
        "meta_name": "payment_link",
        "category": "utility",
        "lang": "en",
        "param_order": ["guest_name", "amount", "link"],
        "respect_optout": False,  # transactional — the guest asked to pay
        "body_preview": (
            "Hi {guest_name}, please pay Rs. {amount} for your stay at Hotel Bhimas here: {link}. "
            "The folio updates automatically once paid. Thank you!"
        ),
    },
    # ---------------------------------------------------------------------------------
    # The owner approval pair. Meta REJECTED the combined utility template ("category was
    # wrongly selected ... looks like authentication"), and an authentication template has a
    # fixed one-variable body that cannot describe what is being approved. So the approval
    # travels as two messages: the context first (utility), then the code (authentication).
    # whatsapp_service.send_owner_otp() sends both, in that order.
    # ---------------------------------------------------------------------------------
    "owner_approval_request": {
        "meta_name": "owner_approval_request",
        "category": "utility",
        "lang": "en",
        "param_order": ["action"],       # the build_context() summary; see _otp_action_text
        "respect_optout": False,
        "body_preview": (
            "Hotel Bhimas — approval needed: {action}. The code that releases it follows in "
            "the next message and expires in a few minutes. If it does not arrive, open the admin "
            "Approvals inbox."
        ),
    },
    "owner_otp": {
        "meta_name": "owner_otp",
        # AUTHENTICATION. Meta owns the rendered body of an auth template -- it always reads
        # "<CODE> is your verification code." plus optional preset disclaimers, so body_preview
        # below is NOT what WhatsApp shows. It is what the EMAIL fallback sends
        # (services/notify.py renders it) and what the message log records, so it deliberately
        # says the same thing in our own words.
        # An auth template also needs the code echoed as a copy-code BUTTON parameter on every
        # send -- see the authentication branch in _send_meta().
        "category": "authentication",
        "lang": "en",
        "param_order": ["code"],
        "respect_optout": False,
        "body_preview": "{code} is your Hotel Bhimas approval code. It expires in a few minutes.",
    },
    "daily_digest": {
        "meta_name": "daily_digest",
        "category": "utility",
        "lang": "en",
        "param_order": ["day", "occupancy_pct", "revenue", "open_alerts"],
        "respect_optout": False,
        # Meta rejected the terser version: "too many variables for its length". A body needs
        # at least (3 x variables) + 1 words, counting the variables -- 13 here, and it had 11.
        "body_preview": (
            "Hotel Bhimas — here is your daily summary for {day}. Occupancy was "
            "{occupancy_pct}%, revenue collected was Rs. {revenue}, and {open_alerts} alerts "
            "are still open for review."
        ),
    },
    "weekly_digest": {
        "meta_name": "weekly_digest",
        "category": "utility",
        "lang": "en",
        "param_order": ["week", "summary"],
        "respect_optout": False,
        # trailing clause is deliberate: Meta rejects a body that ends on a variable
        "body_preview": (
            "Hotel Bhimas weekly summary ({week}): {summary} — full detail is in the admin "
            "dashboard."
        ),
    },
    "fraud_alert": {
        "meta_name": "fraud_alert",
        "category": "utility",
        "lang": "en",
        "param_order": ["alert_type", "detail"],
        "respect_optout": False,
        "body_preview": "Hotel Bhimas ALERT — {alert_type}: {detail}. Please review the admin dashboard.",
    },
    "cash_variance": {
        "meta_name": "cash_variance",
        "category": "utility",
        "lang": "en",
        "param_order": ["shift_id", "variance"],
        "respect_optout": False,
        "body_preview": (
            "Hotel Bhimas ALERT — cash variance on shift #{shift_id}: Rs. {variance}. Please review."
        ),
    },
    "review_alert": {
        "meta_name": "review_alert",
        "category": "utility",
        "lang": "en",
        "param_order": ["author_name", "rating", "snippet"],
        "respect_optout": False,
        # author before rating: the submitted template numbers {{1}}..{{3}} in param_order, and
        # Meta requires the placeholders to appear in ascending order in the text.
        "body_preview": (
            "Hotel Bhimas — new Google review from {author_name} ({rating} stars): "
            "\"{snippet}\". A draft reply is waiting in the admin approval queue and a follow-up "
            "ticket was opened."
        ),
    },
    "vendor_renewal": {
        "meta_name": "vendor_renewal",
        "category": "utility",
        "lang": "en",
        "param_order": ["vendor_name", "contract_title", "end_date", "days_left"],
        "respect_optout": False,  # operational owner alert, not marketing
        # vendor before contract, to match param_order (see review_alert)
        "body_preview": (
            "Hotel Bhimas — renewal due for {vendor_name}: contract \"{contract_title}\" ends "
            "on {end_date} ({days_left} day(s) left). Renew or cancel it in the admin Vendors screen."
        ),
    },
    "low_stock_alert": {
        "meta_name": "low_stock_alert",
        "category": "utility",
        "lang": "en",
        "param_order": ["item_name", "current_qty", "unit", "threshold"],
        "respect_optout": False,  # operational owner alert, not marketing
        # unit is parenthesised so no two placeholders end up adjacent
        "body_preview": (
            "Hotel Bhimas — low stock: {item_name} is down to {current_qty} ({unit}), "
            "reorder level {threshold}. Restock it in the admin Inventory screen."
        ),
    },
    "complaint_escalation": {
        "meta_name": "complaint_escalation",
        "category": "utility",
        "lang": "en",
        "param_order": ["complaint_id", "priority", "level", "issue", "reason"],
        "respect_optout": False,  # operational owner alert, not marketing
        # issue before reason, to match param_order (see review_alert)
        "body_preview": (
            "Hotel Bhimas — complaint #{complaint_id} ({priority}) escalated to level "
            "{level}: \"{issue}\". Reason: {reason}. Please review it in the admin Complaints screen."
        ),
    },
    # Document-header templates: the daily/weekly owner report PDF rides in the header; the body
    # carries a one-line headline. Owner must approve these in Meta with a DOCUMENT header.
    "daily_report_doc": {
        "meta_name": "daily_report_doc",
        "category": "utility",
        "lang": "en",
        "param_order": ["day"],
        "respect_optout": False,
        "body_preview": "Hotel Bhimas — your daily report for {day} is attached.",
    },
    "weekly_report_doc": {
        "meta_name": "weekly_report_doc",
        "category": "utility",
        "lang": "en",
        "param_order": ["week"],
        "respect_optout": False,
        "body_preview": "Hotel Bhimas — your weekly report ({week}) is attached.",
    },
    "portal_link": {
        "meta_name": "portal_link",
        "category": "utility",
        "lang": "en",
        "param_order": ["guest_name", "room_number", "link"],
        "respect_optout": False,  # in-stay service link to the current guest, not marketing
        # trailing sentence is deliberate: Meta rejects a body that ends on a variable
        "body_preview": (
            "Hi {guest_name}, welcome to Hotel Bhimas (room {room_number}). Order room service, get "
            "the WiFi code, request a wake-up call or cab, and check out here: {link}. Save this "
            "message for your stay."
        ),
    },
}


def get_template(name: str):
    return TEMPLATES.get(name)


def render_preview(name: str, params: dict) -> str:
    """Best-effort local render of the template body (used for the stub log + message `body`
    preview + the owner alerts which we send as plain text in stub mode). Missing params are
    left as their placeholder so nothing crashes."""
    tpl = TEMPLATES.get(name)
    if not tpl:
        return ""
    body = tpl["body_preview"]
    for k, v in (params or {}).items():
        body = body.replace("{" + k + "}", str(v))
    return body


# Readable stand-ins for the admin "send test" path. A test send exists to prove the template
# renders and lands on the handset, so blanks defeat the point -- and Meta rejects a body
# parameter whose text is empty outright with (#131008), which is why every test send used to
# fail. Keys are the param_order names across the whole catalog above.
_SAMPLE_PARAMS = {
    "action": "release a discounted rate (sample)",
    "alert_type": "sample_alert",
    "amount": "1500.00",
    "author_name": "Ravi Kumar",
    "booking_ref": "BK-1001",
    "check_in": "28-08-2026",
    "check_out": "29-08-2026",
    "checkout_time": "28-08-2026 11:00",
    "code": "123456",
    "complaint_id": "1",
    "contract_title": "Laundry contract (sample)",
    "current_qty": "4",
    "day": "28-08-2026",
    "days_left": "7",
    "detail": "sample alert detail",
    "end_date": "30-09-2026",
    "guest_name": "Ravi Kumar",
    "invoice_no": "INV-1001",
    "issue": "AC not cooling",
    "item_name": "Bath towels",
    "level": "1",
    "link": "https://example.com/sample",
    "method": "UPI",
    "occupancy_pct": "72",
    "open_alerts": "2",
    "priority": "high",
    "rating": "5",
    "reason": "unresolved past SLA",
    "revenue": "18500.00",
    "review_url": "https://example.com/review",
    "room_label": "101",
    "room_number": "101",
    "shift_id": "1",
    "snippet": "Lovely stay, very clean rooms.",
    "summary": "avg occupancy 72%, collected Rs. 1,29,500",
    "threshold": "10",
    "unit": "pcs",
    "variance": "250.00",
    "vendor_name": "Acme Supplies",
    "week": "W35 2026",
}


def fill_sample_params(name: str, given=None) -> dict:
    """Return `given` with every param the caller left blank filled by a sample value.

    Only the admin test-send uses this -- real sends must fail loudly rather than deliver a
    fake guest name. Covers missing keys, None, and empty/whitespace strings alike.
    """
    out = dict(given or {})
    tpl = TEMPLATES.get(name)
    if not tpl:
        return out
    for k in tpl["param_order"]:
        v = out.get(k)
        if v is None or not str(v).strip():
            out[k] = _SAMPLE_PARAMS.get(k, k.replace("_", " ").title())
    return out


def catalog() -> list:
    """The catalog as a list of plain dicts for the admin Templates tab."""
    return [
        {
            "name": name,
            "meta_name": t["meta_name"],
            "category": t["category"],
            "lang": t["lang"],
            "param_order": t["param_order"],
            "respect_optout": t["respect_optout"],
            "body_preview": t["body_preview"],
        }
        for name, t in TEMPLATES.items()
    ]
