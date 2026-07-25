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
    "owner_otp": {
        "meta_name": "owner_otp",
        "category": "authentication",
        "lang": "en",
        "param_order": ["code", "action"],
        "respect_optout": False,
        "body_preview": "Hotel Bhimas approval code: {code} (for {action}). Valid a few minutes. Do not share.",
    },
    "daily_digest": {
        "meta_name": "daily_digest",
        "category": "utility",
        "lang": "en",
        "param_order": ["day", "occupancy_pct", "revenue", "open_alerts"],
        "respect_optout": False,
        "body_preview": (
            "Hotel Bhimas — {day}: occupancy {occupancy_pct}%, revenue Rs. {revenue}, "
            "open alerts {open_alerts}."
        ),
    },
    "weekly_digest": {
        "meta_name": "weekly_digest",
        "category": "utility",
        "lang": "en",
        "param_order": ["week", "summary"],
        "respect_optout": False,
        "body_preview": "Hotel Bhimas weekly summary ({week}): {summary}",
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
        "body_preview": (
            "Hotel Bhimas — new {rating}-star Google review from {author_name}: \"{snippet}\". "
            "A draft reply is waiting in the admin approval queue and a follow-up ticket was opened."
        ),
    },
    "vendor_renewal": {
        "meta_name": "vendor_renewal",
        "category": "utility",
        "lang": "en",
        "param_order": ["vendor_name", "contract_title", "end_date", "days_left"],
        "respect_optout": False,  # operational owner alert, not marketing
        "body_preview": (
            "Hotel Bhimas — contract renewal due: {contract_title} with {vendor_name} ends on "
            "{end_date} ({days_left} day(s) left). Renew or cancel it in the admin Vendors screen."
        ),
    },
    "low_stock_alert": {
        "meta_name": "low_stock_alert",
        "category": "utility",
        "lang": "en",
        "param_order": ["item_name", "current_qty", "unit", "threshold"],
        "respect_optout": False,  # operational owner alert, not marketing
        "body_preview": (
            "Hotel Bhimas — low stock: {item_name} is down to {current_qty} {unit} "
            "(reorder at {threshold}). Restock it in the admin Inventory screen."
        ),
    },
    "complaint_escalation": {
        "meta_name": "complaint_escalation",
        "category": "utility",
        "lang": "en",
        "param_order": ["complaint_id", "priority", "level", "issue", "reason"],
        "respect_optout": False,  # operational owner alert, not marketing
        "body_preview": (
            "Hotel Bhimas — complaint #{complaint_id} ({priority}) escalated to level {level}: "
            "{reason}. \"{issue}\". Please review it in the admin Complaints screen."
        ),
    },
    "portal_link": {
        "meta_name": "portal_link",
        "category": "utility",
        "lang": "en",
        "param_order": ["guest_name", "room_number", "link"],
        "respect_optout": False,  # in-stay service link to the current guest, not marketing
        "body_preview": (
            "Hi {guest_name}, welcome to Hotel Bhimas (room {room_number}). Order room service, get "
            "the WiFi code, request a wake-up call or cab, and check out — all here: {link}"
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
