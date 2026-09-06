"""Generic runtime-editable settings store (prompt 12).

The codebase's config idiom is env vars read at call time (see fraud_detection.get_config).
That works for values only the operator changes at deploy time, but some settings must be
changeable by an admin *without a redeploy* — starting with the cash-shift cycle and the
variance-alert threshold (prompt 12). Those live in the `app_settings` table (key -> text value)
and are read/written through the small typed helpers here.

Values are stored as text and typed by the accessor. Defaults are returned when a key is absent
(the migration seeds the cash keys, but the getters are defensive so a fresh DB still works).
This also unblocks prompt 11's deferred "live-editable thresholds" follow-up — future prompts can
migrate more env flags here.
"""
import json
import logging
import os
import re
from datetime import datetime

from models import AppSetting
from utils.audit import _resolve_user_id

logger = logging.getLogger(__name__)

# Cash-shift config keys + defaults (mirrored by the seed in migration 010).
CASH_CYCLE_KEY = "cash_cycle"
CASH_VARIANCE_THRESHOLD_KEY = "cash_variance_threshold"
CASH_VARIANCE_ALERT_KEY = "cash_variance_alert_enabled"

# Housekeeping config keys + defaults (prompt 13; seeded in migration 011).
HK_AUTO_INSPECT_KEY = "housekeeping_auto_inspect"

# Customer CRM config keys + defaults (prompt 14; seeded in migration 012).
LOYALTY_ENABLED_KEY = "loyalty_enabled"                     # master on/off for the loyalty programme
LOYALTY_POINTS_PER_RUPEE_KEY = "loyalty_points_per_rupee"   # points earned per ₹1 of stay spend
LOYALTY_RUPEE_PER_POINT_KEY = "loyalty_rupee_per_point"     # ₹ value of 1 point on redemption
BLACKLIST_ENFORCEMENT_KEY = "blacklist_enforcement"         # warn | block
CRM_LINK_TTL_HOURS_KEY = "crm_registration_link_ttl_hours"

# WhatsApp automation config keys + defaults (prompt 15; seeded in migration 013).
# Message-provider SECRETS live in ENV (WHATSAPP_*), not here — these are the admin-editable
# business toggles: which automations are on, their timing, the owner recipient, review link.
WA_CHECKOUT_REMINDER_KEY = "wa_checkout_reminder_enabled"
WA_CHECKOUT_LEAD_HOURS_KEY = "wa_checkout_reminder_lead_hours"
WA_OVERSTAY_KEY = "wa_overstay_enabled"
WA_CONFIRMATION_KEY = "wa_confirmation_enabled"
WA_RECEIPT_KEY = "wa_receipt_enabled"
WA_ROOM_READY_KEY = "wa_room_ready_enabled"
WA_PORTAL_LINK_KEY = "wa_portal_link_enabled"
WA_REVIEW_KEY = "wa_review_enabled"
WA_REVIEW_DELAY_HOURS_KEY = "wa_review_delay_hours"
WA_OWNER_ALERTS_KEY = "wa_owner_alerts_enabled"
WA_OWNER_NUMBER_KEY = "owner_whatsapp"
# Owner's email — the fallback when WhatsApp is unavailable (backlog v2 FE-11). There is
# no email column on User anywhere in the schema, so this setting is the only source.
OWNER_EMAIL_KEY = "owner_email"
WA_REVIEW_URL_KEY = "wa_google_review_url"
WA_JOB_INTERVAL_KEY = "wa_job_interval_minutes"
WA_DIGEST_HOUR_KEY = "wa_daily_digest_hour"
WA_WEEKLY_DIGEST_KEY = "wa_weekly_digest_enabled"
WA_WEEKLY_WEEKDAY_KEY = "wa_weekly_digest_weekday"   # 0=Mon … 6=Sun

# Reports / night-audit config keys + defaults (prompt 16; seeded in migration 014).
# `business_date` is the PMS's rolling business day (advanced by the night audit);
# empty until the first day-close runs, then set to that date's ISO string.
BUSINESS_DATE_KEY = "business_date"
NIGHT_AUDIT_HOUR_KEY = "night_audit_hour"        # IST hour (0-23) the day-close runs
NIGHT_AUDIT_ENABLED_KEY = "night_audit_enabled"  # master toggle for the scheduled routine

# India compliance / Tally / e-invoicing / 2FA config keys + defaults (prompt 18; seeded in migration 016).
# The e-invoicing SECRETS live in ENV (GST_EINVOICE_*), not here — these are admin-editable business values.
POLICE_STATION_NAME_KEY = "police_station_name"
POLICE_STATION_CODE_KEY = "police_station_code"
FRRO_OFFICE_KEY = "frro_office"
HOTEL_REGISTRATION_NO_KEY = "hotel_registration_no"
TALLY_COMPANY_KEY = "tally_company_name"
TALLY_SALES_LEDGER_KEY = "tally_sales_ledger"
TALLY_CGST_LEDGER_KEY = "tally_cgst_ledger"
TALLY_SGST_LEDGER_KEY = "tally_sgst_ledger"
TALLY_CASH_LEDGER_KEY = "tally_cash_ledger"
TALLY_BANK_LEDGER_KEY = "tally_bank_ledger"
TALLY_DEBTORS_LEDGER_KEY = "tally_debtors_ledger"
ADMIN_2FA_REQUIRED_KEY = "admin_2fa_required"          # if true, admins are nudged to enrol TOTP
ADMIN_IDLE_LOGOUT_MIN_KEY = "admin_idle_logout_minutes"  # client-side idle auto-logout window

# Integrated desk-payment config keys + defaults (prompt 19; seeded in migration 017).
# Which desk collection methods are offered + the per-method convenience-fee policy. The
# Razorpay SECRETS (RAZORPAY_KEY_ID/SECRET, RAZORPAY_WEBHOOK_SECRET) live in ENV, not here.
DESK_PAY_UPI_QR_ENABLED_KEY = "desk_pay_upi_qr_enabled"
DESK_PAY_LINK_ENABLED_KEY = "desk_pay_link_enabled"
DESK_PAY_CASH_ENABLED_KEY = "desk_pay_cash_enabled"
DESK_PAY_FEE_CARD_PERCENT_KEY = "desk_pay_fee_card_percent"   # % MDR passed to guest on card
DESK_PAY_FEE_GST_PERCENT_KEY = "desk_pay_fee_gst_percent"     # GST charged on the convenience fee
DESK_PAY_FEE_ON_CARD_KEY = "desk_pay_fee_on_card"
DESK_PAY_FEE_ON_UPI_KEY = "desk_pay_fee_on_upi"
DESK_PAY_FEE_ON_LINK_KEY = "desk_pay_fee_on_link"
DESK_PAY_QR_EXPIRY_MIN_KEY = "desk_pay_qr_expiry_minutes"     # dynamic-QR lifetime before it expires
DESK_PAY_VPA_ENABLED_KEY = "desk_pay_vpa_enabled"             # own-bank UPI VPA (0%) mode — DEFERRED, off

# Google review auto-reply config keys + defaults (prompt 20; seeded in migration 018).
# GBP OAuth + Claude SECRETS live in ENV (GBP_*, ANTHROPIC_API_KEY), not here — these are the
# admin-editable business toggles: which ratings auto-post, the delay window, LLM on/off, cadence.
REVIEW_AUTO_REPLY_KEY = "review_auto_reply_enabled"          # master toggle for auto-posting thank-yous
REVIEW_THRESHOLD_KEY = "review_auto_reply_threshold"        # rating >= this auto-posts; below -> approval queue
REVIEW_LOW_BAND_SPLIT_KEY = "review_low_band_split"          # ratings <= this use the 1-2 apology band vs the 3 band
REVIEW_DELAY_MIN_HOURS_KEY = "review_post_delay_min_hours"   # randomized posting delay (lower bound)
REVIEW_DELAY_MAX_HOURS_KEY = "review_post_delay_max_hours"   # randomized posting delay (upper bound)
REVIEW_LOW_AUTO_SEND_KEY = "review_low_auto_send"            # auto-send low-rating drafts (default off = approval)
REVIEW_LLM_ENABLED_KEY = "review_llm_enabled"               # Claude generation on (still needs ANTHROPIC_API_KEY)
REVIEW_POLL_INTERVAL_KEY = "review_poll_interval_minutes"    # GBP poller cadence
REVIEW_OWNER_ALERTS_KEY = "review_owner_alerts_enabled"     # WhatsApp the owner on a <4 review

# Back-office config keys + defaults (prompt 18 slices 7/13/12/9; seeded in migration 019).
# Corporate credit control, vendor/AMC renewal alerts, roster + attendance behaviour.
COMPANY_CREDIT_BLOCK_KEY = "company_credit_block"            # refuse an over-credit-limit routing (409)
COMPANY_DEFAULT_CREDIT_DAYS_KEY = "company_default_credit_days"
COMPANY_INVOICE_PREFIX_KEY = "company_invoice_prefix"        # consolidated invoice number prefix
VENDOR_RENEWAL_ALERTS_KEY = "vendor_renewal_alerts_enabled"
VENDOR_RENEWAL_LEAD_DAYS_KEY = "vendor_renewal_lead_days"    # fallback when a contract sets no window
ROSTER_DEFAULT_SHIFT_TYPE_KEY = "roster_default_shift_type"
ATTENDANCE_PIN_ENABLED_KEY = "attendance_pin_enabled"        # allow PIN clock-in/out at the desk
ATTENDANCE_AUTO_CLOSE_HOURS_KEY = "attendance_auto_close_hours"  # forgotten clock-out safety cap

# Inventory / stock config keys + defaults (prompt 18c slice 8; seeded in migration 020).
LOW_STOCK_ALERTS_ENABLED_KEY = "low_stock_alerts_enabled"        # owner WhatsApp on a low-stock episode
LOW_STOCK_DEFAULT_THRESHOLD_KEY = "low_stock_default_threshold"  # fallback reorder point when an item sets none

# Guest-complaint config keys + defaults (prompt 18c slice 11; seeded in migration 020).
# SLA hours are per-priority (respond-by + resolve-by), used to stamp due-times at create and to
# drive the escalation sweep. Auto-compensation is OFF by default — compensation is always an
# admin, reason-required, audited action.
COMPLAINT_SLA_RESPONSE_PREFIX = "complaint_sla_response_hours_"   # + urgent|high|normal|low
COMPLAINT_SLA_RESOLVE_PREFIX = "complaint_sla_resolve_hours_"     # + urgent|high|normal|low
COMPLAINT_ESCALATION_ENABLED_KEY = "complaint_escalation_enabled"
COMPLAINT_AUTO_COMPENSATION_ENABLED_KEY = "complaint_auto_compensation_enabled"

# Guest portal / in-room QR config keys + defaults (prompt 18d slice 10; seeded in migration 021).
GUEST_PORTAL_ENABLED_KEY = "guest_portal_enabled"                 # master toggle
PORTAL_ROOM_SERVICE_ENABLED_KEY = "portal_room_service_enabled"
PORTAL_WIFI_ENABLED_KEY = "portal_wifi_enabled"
PORTAL_WAKEUP_ENABLED_KEY = "portal_wakeup_enabled"
PORTAL_CAB_ENABLED_KEY = "portal_cab_enabled"
PORTAL_CONTACTLESS_CHECKOUT_ENABLED_KEY = "portal_contactless_checkout_enabled"
WIFI_SSID_KEY = "wifi_ssid"
WIFI_VOUCHER_MODE_KEY = "wifi_voucher_mode"                       # auto (show a per-stay code) | manual (desk issues)

_DEFAULTS = {
    CASH_CYCLE_KEY: "shift",
    CASH_VARIANCE_THRESHOLD_KEY: "100",
    CASH_VARIANCE_ALERT_KEY: "true",
    HK_AUTO_INSPECT_KEY: "false",
    LOYALTY_ENABLED_KEY: "true",
    LOYALTY_POINTS_PER_RUPEE_KEY: "0.01",
    LOYALTY_RUPEE_PER_POINT_KEY: "1",
    BLACKLIST_ENFORCEMENT_KEY: "warn",
    CRM_LINK_TTL_HOURS_KEY: "72",
    WA_CHECKOUT_REMINDER_KEY: "true",
    WA_CHECKOUT_LEAD_HOURS_KEY: "2",
    WA_OVERSTAY_KEY: "true",
    WA_CONFIRMATION_KEY: "true",
    WA_RECEIPT_KEY: "true",
    WA_ROOM_READY_KEY: "true",
    WA_PORTAL_LINK_KEY: "true",
    WA_REVIEW_KEY: "true",
    WA_REVIEW_DELAY_HOURS_KEY: "3",
    WA_OWNER_ALERTS_KEY: "true",
    WA_OWNER_NUMBER_KEY: "",
    WA_REVIEW_URL_KEY: "",
    WA_JOB_INTERVAL_KEY: "15",
    WA_DIGEST_HOUR_KEY: "9",
    WA_WEEKLY_DIGEST_KEY: "true",
    WA_WEEKLY_WEEKDAY_KEY: "0",
    BUSINESS_DATE_KEY: "",
    NIGHT_AUDIT_HOUR_KEY: "3",
    NIGHT_AUDIT_ENABLED_KEY: "true",
    POLICE_STATION_NAME_KEY: "",
    POLICE_STATION_CODE_KEY: "",
    FRRO_OFFICE_KEY: "",
    HOTEL_REGISTRATION_NO_KEY: "",
    TALLY_COMPANY_KEY: "Hotel Bhimas",
    TALLY_SALES_LEDGER_KEY: "Room Sales",
    TALLY_CGST_LEDGER_KEY: "CGST Output",
    TALLY_SGST_LEDGER_KEY: "SGST Output",
    TALLY_CASH_LEDGER_KEY: "Cash",
    TALLY_BANK_LEDGER_KEY: "Bank",
    TALLY_DEBTORS_LEDGER_KEY: "Sundry Debtors",
    ADMIN_2FA_REQUIRED_KEY: "false",
    ADMIN_IDLE_LOGOUT_MIN_KEY: "15",
    DESK_PAY_UPI_QR_ENABLED_KEY: "true",
    DESK_PAY_LINK_ENABLED_KEY: "true",
    DESK_PAY_CASH_ENABLED_KEY: "true",
    DESK_PAY_FEE_CARD_PERCENT_KEY: "2.0",
    DESK_PAY_FEE_GST_PERCENT_KEY: "18.0",
    DESK_PAY_FEE_ON_CARD_KEY: "true",
    DESK_PAY_FEE_ON_UPI_KEY: "false",
    DESK_PAY_FEE_ON_LINK_KEY: "false",
    DESK_PAY_QR_EXPIRY_MIN_KEY: "15",
    DESK_PAY_VPA_ENABLED_KEY: "false",
    REVIEW_AUTO_REPLY_KEY: "true",
    REVIEW_THRESHOLD_KEY: "4",
    REVIEW_LOW_BAND_SPLIT_KEY: "2",
    REVIEW_DELAY_MIN_HOURS_KEY: "2",
    REVIEW_DELAY_MAX_HOURS_KEY: "6",
    REVIEW_LOW_AUTO_SEND_KEY: "false",
    REVIEW_LLM_ENABLED_KEY: "false",
    REVIEW_POLL_INTERVAL_KEY: "15",
    REVIEW_OWNER_ALERTS_KEY: "true",
    COMPANY_CREDIT_BLOCK_KEY: "true",
    COMPANY_DEFAULT_CREDIT_DAYS_KEY: "30",
    COMPANY_INVOICE_PREFIX_KEY: "CINV",
    VENDOR_RENEWAL_ALERTS_KEY: "true",
    VENDOR_RENEWAL_LEAD_DAYS_KEY: "30",
    ROSTER_DEFAULT_SHIFT_TYPE_KEY: "general",
    ATTENDANCE_PIN_ENABLED_KEY: "true",
    ATTENDANCE_AUTO_CLOSE_HOURS_KEY: "16",
    LOW_STOCK_ALERTS_ENABLED_KEY: "true",
    LOW_STOCK_DEFAULT_THRESHOLD_KEY: "5",
    COMPLAINT_SLA_RESPONSE_PREFIX + "urgent": "1",
    COMPLAINT_SLA_RESPONSE_PREFIX + "high": "2",
    COMPLAINT_SLA_RESPONSE_PREFIX + "normal": "4",
    COMPLAINT_SLA_RESPONSE_PREFIX + "low": "8",
    COMPLAINT_SLA_RESOLVE_PREFIX + "urgent": "4",
    COMPLAINT_SLA_RESOLVE_PREFIX + "high": "8",
    COMPLAINT_SLA_RESOLVE_PREFIX + "normal": "24",
    COMPLAINT_SLA_RESOLVE_PREFIX + "low": "48",
    COMPLAINT_ESCALATION_ENABLED_KEY: "true",
    COMPLAINT_AUTO_COMPENSATION_ENABLED_KEY: "false",
    GUEST_PORTAL_ENABLED_KEY: "true",
    PORTAL_ROOM_SERVICE_ENABLED_KEY: "true",
    PORTAL_WIFI_ENABLED_KEY: "true",
    PORTAL_WAKEUP_ENABLED_KEY: "true",
    PORTAL_CAB_ENABLED_KEY: "true",
    PORTAL_CONTACTLESS_CHECKOUT_ENABLED_KEY: "true",
    WIFI_SSID_KEY: "HotelBhimas-Guest",
    WIFI_VOUCHER_MODE_KEY: "auto",
}


def get_setting(db, key, default=None):
    """Raw text value for `key`, or `default` (falls back to a known seed default)."""
    row = db.query(AppSetting).filter(AppSetting.key == key).first()
    if row is not None and row.value is not None:
        return row.value
    if default is not None:
        return default
    return _DEFAULTS.get(key)


def set_setting(db, key, value, user=None, commit=False):
    """Upsert a setting. Joins the caller's transaction unless commit=True."""
    row = db.query(AppSetting).filter(AppSetting.key == key).first()
    if row is None:
        row = AppSetting(key=key)
        db.add(row)
    row.value = None if value is None else str(value)
    row.updated_by = _resolve_user_id(db, user)
    row.updated_at = datetime.utcnow()
    if commit:
        db.commit()
    return row


def _as_bool(v) -> bool:
    return str(v).strip().lower() not in ("false", "0", "no", "")


def get_cash_config(db) -> dict:
    """Typed cash-shift config: cycle ('shift'|'day'), variance threshold (float, ₹),
    and whether an over-threshold variance raises an owner alert."""
    cycle = (get_setting(db, CASH_CYCLE_KEY) or "shift").strip().lower()
    if cycle not in ("shift", "day"):
        cycle = "shift"
    try:
        threshold = float(get_setting(db, CASH_VARIANCE_THRESHOLD_KEY) or 100)
    except (TypeError, ValueError):
        threshold = 100.0
    if threshold < 0:
        threshold = 0.0
    return {
        "cycle": cycle,
        "variance_threshold": round(threshold, 2),
        "variance_alert_enabled": _as_bool(get_setting(db, CASH_VARIANCE_ALERT_KEY, "true")),
    }


def get_housekeeping_config(db) -> dict:
    """Typed housekeeping config: whether marking a room clean also auto-inspects it
    (skipping the supervisor gate). Default off = a supervisor/admin must inspect before
    a room is re-sellable."""
    return {
        "auto_inspect": _as_bool(get_setting(db, HK_AUTO_INSPECT_KEY, "false")),
        # Surfaced read-only so the admin UI can show the fraud threshold. It is EDITED on
        # the fraud tab; read it from the same place so the two screens can't disagree (FE-12).
        "cleaning_max_hours": float(get_fraud_config(db)["cleaning_max_hours"]),
    }


def _as_float(v, default: float) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def get_crm_config(db) -> dict:
    """Typed Customer-CRM config (prompt 14): loyalty accrual/redemption rates,
    blacklist enforcement mode ('warn' surfaces a banner, 'block' 409s at booking/
    check-in), and the pre-arrival link lifetime."""
    ppr = _as_float(get_setting(db, LOYALTY_POINTS_PER_RUPEE_KEY), 0.01)
    rpp = _as_float(get_setting(db, LOYALTY_RUPEE_PER_POINT_KEY), 1.0)
    enforcement = (get_setting(db, BLACKLIST_ENFORCEMENT_KEY) or "warn").strip().lower()
    if enforcement not in ("warn", "block"):
        enforcement = "warn"
    ttl = _as_float(get_setting(db, CRM_LINK_TTL_HOURS_KEY), 72.0)
    return {
        "loyalty_enabled": _bool_or(get_setting(db, LOYALTY_ENABLED_KEY), True),
        "loyalty_points_per_rupee": max(0.0, ppr),
        "loyalty_rupee_per_point": max(0.0, rpp),
        "blacklist_enforcement": enforcement,
        "registration_link_ttl_hours": max(1.0, ttl),
    }


def _as_int(v, default: int) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def get_whatsapp_config(db) -> dict:
    """Typed WhatsApp automation config (prompt 15): which automations fire, their timing,
    the owner recipient, the Google-review link, and the scheduler cadence. Message-provider
    credentials are ENV-owned (WHATSAPP_*) and surfaced read-only via whatsapp_service.provider_status()."""
    return {
        "checkout_reminder_enabled": _as_bool(get_setting(db, WA_CHECKOUT_REMINDER_KEY, "true")),
        "checkout_reminder_lead_hours": max(0.5, _as_float(get_setting(db, WA_CHECKOUT_LEAD_HOURS_KEY), 2.0)),
        "overstay_enabled": _as_bool(get_setting(db, WA_OVERSTAY_KEY, "true")),
        "confirmation_enabled": _as_bool(get_setting(db, WA_CONFIRMATION_KEY, "true")),
        "receipt_enabled": _as_bool(get_setting(db, WA_RECEIPT_KEY, "true")),
        "room_ready_enabled": _as_bool(get_setting(db, WA_ROOM_READY_KEY, "true")),
        "portal_link_enabled": _as_bool(get_setting(db, WA_PORTAL_LINK_KEY, "true")),
        "review_enabled": _as_bool(get_setting(db, WA_REVIEW_KEY, "true")),
        "review_delay_hours": max(0.0, _as_float(get_setting(db, WA_REVIEW_DELAY_HOURS_KEY), 3.0)),
        "owner_alerts_enabled": _as_bool(get_setting(db, WA_OWNER_ALERTS_KEY, "true")),
        "owner_whatsapp": (get_setting(db, WA_OWNER_NUMBER_KEY, "") or "").strip(),
        # Fallback address for owner alerts when WhatsApp is unavailable (FE-11).
        "owner_email": (get_setting(db, OWNER_EMAIL_KEY, "") or "").strip(),
        "google_review_url": (get_setting(db, WA_REVIEW_URL_KEY, "") or "").strip(),
        "job_interval_minutes": max(1, _as_int(get_setting(db, WA_JOB_INTERVAL_KEY), 15)),
        "daily_digest_hour": min(23, max(0, _as_int(get_setting(db, WA_DIGEST_HOUR_KEY), 9))),
        "weekly_digest_enabled": _as_bool(get_setting(db, WA_WEEKLY_DIGEST_KEY, "true")),
        "weekly_digest_weekday": min(6, max(0, _as_int(get_setting(db, WA_WEEKLY_WEEKDAY_KEY), 0))),
    }


def get_reports_config(db) -> dict:
    """Typed reports / night-audit config (prompt 16): the current business date (ISO string
    or None until the first day-close runs), the IST hour the scheduled day-close fires, and
    whether the scheduled routine is enabled. Manual trigger via POST /reports/day-close/run
    works regardless of the enabled flag."""
    bd = (get_setting(db, BUSINESS_DATE_KEY, "") or "").strip()
    return {
        "business_date": bd or None,
        "night_audit_hour": min(23, max(0, _as_int(get_setting(db, NIGHT_AUDIT_HOUR_KEY), 3))),
        "night_audit_enabled": _as_bool(get_setting(db, NIGHT_AUDIT_ENABLED_KEY, "true")),
    }


# Keys an admin may edit through PUT /compliance/config (secrets stay in ENV, never here).
COMPLIANCE_EDITABLE_KEYS = (
    POLICE_STATION_NAME_KEY, POLICE_STATION_CODE_KEY, FRRO_OFFICE_KEY, HOTEL_REGISTRATION_NO_KEY,
    TALLY_COMPANY_KEY, TALLY_SALES_LEDGER_KEY, TALLY_CGST_LEDGER_KEY, TALLY_SGST_LEDGER_KEY,
    TALLY_CASH_LEDGER_KEY, TALLY_BANK_LEDGER_KEY, TALLY_DEBTORS_LEDGER_KEY,
    ADMIN_2FA_REQUIRED_KEY, ADMIN_IDLE_LOGOUT_MIN_KEY,
)


def get_compliance_config(db) -> dict:
    """Typed India-compliance config (prompt 18): police/FRRO identity for the guest-register and
    Form C exports, the Tally company + ledger names for the accounting export, and the admin-2FA
    business toggles. e-Invoicing provider secrets are ENV-owned (GST_EINVOICE_*)."""
    return {
        "police_station_name": (get_setting(db, POLICE_STATION_NAME_KEY, "") or "").strip(),
        "police_station_code": (get_setting(db, POLICE_STATION_CODE_KEY, "") or "").strip(),
        "frro_office": (get_setting(db, FRRO_OFFICE_KEY, "") or "").strip(),
        "hotel_registration_no": (get_setting(db, HOTEL_REGISTRATION_NO_KEY, "") or "").strip(),
        "tally_company_name": (get_setting(db, TALLY_COMPANY_KEY) or "Hotel Bhimas").strip(),
        "tally_sales_ledger": (get_setting(db, TALLY_SALES_LEDGER_KEY) or "Room Sales").strip(),
        "tally_cgst_ledger": (get_setting(db, TALLY_CGST_LEDGER_KEY) or "CGST Output").strip(),
        "tally_sgst_ledger": (get_setting(db, TALLY_SGST_LEDGER_KEY) or "SGST Output").strip(),
        "tally_cash_ledger": (get_setting(db, TALLY_CASH_LEDGER_KEY) or "Cash").strip(),
        "tally_bank_ledger": (get_setting(db, TALLY_BANK_LEDGER_KEY) or "Bank").strip(),
        "tally_debtors_ledger": (get_setting(db, TALLY_DEBTORS_LEDGER_KEY) or "Sundry Debtors").strip(),
        "admin_2fa_required": _as_bool(get_setting(db, ADMIN_2FA_REQUIRED_KEY, "false")),
        "admin_idle_logout_minutes": max(1, _as_int(get_setting(db, ADMIN_IDLE_LOGOUT_MIN_KEY), 15)),
    }


def get_desk_pay_config(db) -> dict:
    """Typed integrated desk-payment config (prompt 19): which collection methods are offered,
    the per-method convenience-fee policy (percent + GST-on-fee + on/off per method), the dynamic
    UPI-QR lifetime, and the deferred own-bank VPA flag. Razorpay credentials/webhook secret are
    ENV-owned (RAZORPAY_*) and never live here."""
    return {
        "upi_qr_enabled": _as_bool(get_setting(db, DESK_PAY_UPI_QR_ENABLED_KEY, "true")),
        "link_enabled": _as_bool(get_setting(db, DESK_PAY_LINK_ENABLED_KEY, "true")),
        "cash_enabled": _as_bool(get_setting(db, DESK_PAY_CASH_ENABLED_KEY, "true")),
        "fee_card_percent": max(0.0, _as_float(get_setting(db, DESK_PAY_FEE_CARD_PERCENT_KEY), 2.0)),
        "fee_gst_percent": max(0.0, _as_float(get_setting(db, DESK_PAY_FEE_GST_PERCENT_KEY), 18.0)),
        "fee_on_card": _as_bool(get_setting(db, DESK_PAY_FEE_ON_CARD_KEY, "true")),
        "fee_on_upi": _as_bool(get_setting(db, DESK_PAY_FEE_ON_UPI_KEY, "false")),
        "fee_on_link": _as_bool(get_setting(db, DESK_PAY_FEE_ON_LINK_KEY, "false")),
        "qr_expiry_minutes": min(60, max(2, _as_int(get_setting(db, DESK_PAY_QR_EXPIRY_MIN_KEY), 15))),
        "vpa_enabled": _as_bool(get_setting(db, DESK_PAY_VPA_ENABLED_KEY, "false")),
    }


# Keys an admin may edit through PUT /reviews/config (GBP OAuth + Claude secrets stay in ENV).
REVIEW_EDITABLE_KEYS = (
    REVIEW_AUTO_REPLY_KEY, REVIEW_THRESHOLD_KEY, REVIEW_LOW_BAND_SPLIT_KEY,
    REVIEW_DELAY_MIN_HOURS_KEY, REVIEW_DELAY_MAX_HOURS_KEY, REVIEW_LOW_AUTO_SEND_KEY,
    REVIEW_LLM_ENABLED_KEY, REVIEW_POLL_INTERVAL_KEY, REVIEW_OWNER_ALERTS_KEY,
)


def get_review_config(db) -> dict:
    """Typed Google-review auto-reply config (prompt 20): the auto-post rating threshold, the
    low-rating apology band split, the randomized posting-delay window, whether low ratings
    auto-send (default off = approval queue), optional Claude LLM generation, the poller cadence,
    and owner alerts. GBP OAuth + ANTHROPIC_API_KEY are ENV-owned and never live here.

    `delay_min_hours`/`delay_max_hours` are clamped so max >= min (a 0-width window = post-now)."""
    threshold = min(5, max(1, _as_int(get_setting(db, REVIEW_THRESHOLD_KEY), 4)))
    split = min(5, max(1, _as_int(get_setting(db, REVIEW_LOW_BAND_SPLIT_KEY), 2)))
    dmin = max(0.0, _as_float(get_setting(db, REVIEW_DELAY_MIN_HOURS_KEY), 2.0))
    dmax = max(dmin, _as_float(get_setting(db, REVIEW_DELAY_MAX_HOURS_KEY), 6.0))
    return {
        "auto_reply_enabled": _as_bool(get_setting(db, REVIEW_AUTO_REPLY_KEY, "true")),
        "auto_reply_threshold": threshold,
        "low_band_split": split,
        "delay_min_hours": dmin,
        "delay_max_hours": dmax,
        "low_auto_send": _as_bool(get_setting(db, REVIEW_LOW_AUTO_SEND_KEY, "false")),
        "llm_enabled": _as_bool(get_setting(db, REVIEW_LLM_ENABLED_KEY, "false")),
        "poll_interval_minutes": max(1, _as_int(get_setting(db, REVIEW_POLL_INTERVAL_KEY), 15)),
        "owner_alerts_enabled": _as_bool(get_setting(db, REVIEW_OWNER_ALERTS_KEY, "true")),
    }


# Keys an admin may edit through PUT /companies/config (shared back-office settings screen).
# v4b8: every new maintenance ticket goes to this technician automatically. 0 / unset keeps
# the old behaviour (unassigned, and the technician self-claims).
MAINTENANCE_DEFAULT_ASSIGNEE_KEY = "maintenance_default_assignee_id"

BACKOFFICE_EDITABLE_KEYS = (
    COMPANY_CREDIT_BLOCK_KEY, COMPANY_DEFAULT_CREDIT_DAYS_KEY, COMPANY_INVOICE_PREFIX_KEY,
    VENDOR_RENEWAL_ALERTS_KEY, VENDOR_RENEWAL_LEAD_DAYS_KEY,
    ROSTER_DEFAULT_SHIFT_TYPE_KEY, ATTENDANCE_PIN_ENABLED_KEY, ATTENDANCE_AUTO_CLOSE_HOURS_KEY,
    MAINTENANCE_DEFAULT_ASSIGNEE_KEY,
)


def get_backoffice_config(db) -> dict:
    """Typed back-office config (prompt 18 slices 7/13/12/9): corporate credit enforcement +
    invoice numbering, vendor/AMC renewal alerting, and roster/attendance behaviour.

    `company_credit_block` is the hard gate — when true, routing a folio to a company that would
    breach its credit limit is refused (409). An admin can still override with a reason, which is
    audited, matching the void/discount idiom. When false the breach is reported but allowed."""
    prefix = (get_setting(db, COMPANY_INVOICE_PREFIX_KEY) or "CINV").strip().upper() or "CINV"
    shift_type = (get_setting(db, ROSTER_DEFAULT_SHIFT_TYPE_KEY) or "general").strip().lower()
    if shift_type not in ("morning", "evening", "night", "general"):
        shift_type = "general"
    return {
        "maintenance_default_assignee_id": _as_int(
            get_setting(db, MAINTENANCE_DEFAULT_ASSIGNEE_KEY), 0),
        "company_credit_block": _as_bool(get_setting(db, COMPANY_CREDIT_BLOCK_KEY, "true")),
        "company_default_credit_days": max(0, _as_int(get_setting(db, COMPANY_DEFAULT_CREDIT_DAYS_KEY), 30)),
        "company_invoice_prefix": prefix,
        "vendor_renewal_alerts_enabled": _as_bool(get_setting(db, VENDOR_RENEWAL_ALERTS_KEY, "true")),
        "vendor_renewal_lead_days": max(1, _as_int(get_setting(db, VENDOR_RENEWAL_LEAD_DAYS_KEY), 30)),
        "roster_default_shift_type": shift_type,
        "attendance_pin_enabled": _as_bool(get_setting(db, ATTENDANCE_PIN_ENABLED_KEY, "true")),
        "attendance_auto_close_hours": min(48, max(1, _as_int(get_setting(db, ATTENDANCE_AUTO_CLOSE_HOURS_KEY), 16))),
    }


# Keys an admin may edit through PUT /stock/config.
STOCK_EDITABLE_KEYS = (
    LOW_STOCK_ALERTS_ENABLED_KEY, LOW_STOCK_DEFAULT_THRESHOLD_KEY,
)


def get_stock_config(db) -> dict:
    """Typed inventory config (prompt 18c slice 8): whether low-stock episodes alert the owner,
    and the fallback reorder threshold used when an item leaves its own threshold at 0."""
    return {
        "low_stock_alerts_enabled": _as_bool(get_setting(db, LOW_STOCK_ALERTS_ENABLED_KEY, "true")),
        "low_stock_default_threshold": max(0.0, _as_float(get_setting(db, LOW_STOCK_DEFAULT_THRESHOLD_KEY), 5.0)),
    }


# =====================================================================
# ANTI-FRAUD THRESHOLDS + OTP GATES (backlog v2 FE-12)
# =====================================================================
# These were env-only and READ-ONLY in the admin UI, so turning a gate on meant a
# redeploy. They are now settings-backed with the ENV VALUE AS THE DEFAULT, which
# means an untouched install behaves exactly as before; the first admin save takes
# over. Every enforcement site reads get_fraud_config() so the screen can never
# claim a gate is on while the code checks something else.
FRAUD_CLEANING_MAX_HOURS_KEY = "fraud_cleaning_max_hours"
FRAUD_CLEANING_MIN_MINUTES_KEY = "fraud_cleaning_min_minutes"  # cleaned suspiciously fast
FRAUD_INSPECTION_MAX_HOURS_KEY = "fraud_inspection_max_hours"
FRAUD_ALLOWED_ISSUE_HOURS_KEY = "fraud_allowed_issue_hours"
FRAUD_ALLOWED_STATIONS_KEY = "fraud_allowed_stations"
FRAUD_REPEAT_REFUND_THRESHOLD_KEY = "fraud_repeat_refund_threshold"
FRAUD_REFUND_OTP_KEY = "fraud_refund_requires_owner_otp"
FRAUD_DISCOUNT_OTP_KEY = "fraud_discount_otp_required"
FRAUD_VOID_OTP_KEY = "fraud_void_otp_required"
FRAUD_CARD_ISSUE_OTP_KEY = "fraud_card_issue_otp_required"
FRAUD_OTP_TTL_MINUTES_KEY = "fraud_owner_otp_ttl_minutes"

# v4b0: the gates FE-12 left behind. AC_DOWNGRADE_OTP_REQUIRED was read with os.getenv at
# reception.py, so the Settings screen could not arm it — an owner had a gate with no switch
# (one of the four reasons FE-10 "wasn't working"). The rest are the v4 gates; they are
# declared here so every one of them is armable from the same screen from day one.
FRAUD_AC_DOWNGRADE_OTP_KEY = "fraud_ac_downgrade_otp_required"
FRAUD_ALT_ROOM_TYPE_OTP_KEY = "fraud_alt_room_type_otp_required"
FRAUD_COMP_OTP_KEY = "fraud_comp_otp_required"
FRAUD_OVERSTAY_REVERSE_OTP_KEY = "fraud_overstay_reverse_otp_required"
FRAUD_RS_CANCEL_OTP_KEY = "fraud_rs_cancel_otp_required"
FRAUD_CHECKOUT_NO_CARD_OTP_KEY = "fraud_checkout_no_card_otp_required"

FRAUD_EDITABLE_KEYS = (
    FRAUD_CLEANING_MAX_HOURS_KEY, FRAUD_CLEANING_MIN_MINUTES_KEY,
    FRAUD_INSPECTION_MAX_HOURS_KEY,
    FRAUD_ALLOWED_ISSUE_HOURS_KEY, FRAUD_ALLOWED_STATIONS_KEY,
    FRAUD_REPEAT_REFUND_THRESHOLD_KEY, FRAUD_REFUND_OTP_KEY, FRAUD_DISCOUNT_OTP_KEY,
    FRAUD_CARD_ISSUE_OTP_KEY, FRAUD_OTP_TTL_MINUTES_KEY,
    FRAUD_AC_DOWNGRADE_OTP_KEY, FRAUD_ALT_ROOM_TYPE_OTP_KEY, FRAUD_COMP_OTP_KEY,
    FRAUD_OVERSTAY_REVERSE_OTP_KEY, FRAUD_RS_CANCEL_OTP_KEY, FRAUD_CHECKOUT_NO_CARD_OTP_KEY,
)

_ALLOWED_HOURS_RE = re.compile(r"^\d{1,2}-\d{1,2}$")


def _env_bool(name: str, default: str = "false") -> bool:
    return (os.getenv(name, default) or default).strip().lower() not in ("false", "0", "no")


def _bool_or(v, default: bool) -> bool:
    """Like _as_bool, but an ABSENT setting falls back to `default` instead of being
    coerced (plain _as_bool maps None -> True, which would silently arm a gate)."""
    if v is None or str(v).strip() == "":
        return default
    return str(v).strip().lower() not in ("false", "0", "no")


def get_fraud_config(db) -> dict:
    """Typed anti-fraud config. Each value falls back to its historical env var, so an
    install that has never opened the Settings screen keeps its current behaviour."""
    raw_stations = get_setting(db, FRAUD_ALLOWED_STATIONS_KEY, os.getenv("ALLOWED_STATIONS", ""))
    stations = [s.strip() for s in (raw_stations or "").split(",") if s.strip()]

    hours = (get_setting(db, FRAUD_ALLOWED_ISSUE_HOURS_KEY,
                         os.getenv("ALLOWED_ISSUE_HOURS", "06-23")) or "06-23").strip()
    if not _ALLOWED_HOURS_RE.match(hours):
        hours = "06-23"

    return {
        # 0 disables the cleaning-too-long detector's alerting window.
        "cleaning_max_hours": max(0, _as_int(get_setting(db, FRAUD_CLEANING_MAX_HOURS_KEY),
                                             _as_int(os.getenv("CLEANING_MAX_HOURS"), 6))),
        # A room "cleaned" in fewer than this many minutes is flagged (probably not really cleaned).
        # 0 disables the cleaning-too-fast detector.
        "cleaning_min_minutes": max(0, _as_int(get_setting(db, FRAUD_CLEANING_MIN_MINUTES_KEY),
                                               _as_int(os.getenv("CLEANING_MIN_MINUTES"), 10))),
        "inspection_max_hours": max(0, _as_int(get_setting(db, FRAUD_INSPECTION_MAX_HOURS_KEY),
                                               _as_int(os.getenv("INSPECTION_MAX_HOURS"), 6))),
        "allowed_issue_hours": hours,
        "allowed_stations": stations,
        "repeat_refund_threshold": max(1, _as_int(get_setting(db, FRAUD_REPEAT_REFUND_THRESHOLD_KEY),
                                                  _as_int(os.getenv("REPEAT_REFUND_THRESHOLD"), 3))),
        # Money-touching folio gates ship ARMED (owner asked every refund/discount/void to need a
        # code); the owner can still switch any off in Settings.
        "refund_requires_owner_otp": _bool_or(get_setting(db, FRAUD_REFUND_OTP_KEY),
                                              _env_bool("REFUND_REQUIRES_OWNER_OTP", "true")),
        "discount_otp_required": _bool_or(get_setting(db, FRAUD_DISCOUNT_OTP_KEY),
                                          _env_bool("DISCOUNT_OTP_REQUIRED", "true")),
        "void_otp_required": _bool_or(get_setting(db, FRAUD_VOID_OTP_KEY),
                                      _env_bool("VOID_OTP_REQUIRED", "true")),
        "card_issue_otp_required": _bool_or(get_setting(db, FRAUD_CARD_ISSUE_OTP_KEY),
                                            _env_bool("CARD_ISSUE_OTP_REQUIRED")),
        "owner_otp_ttl_minutes": max(1, _as_int(get_setting(db, FRAUD_OTP_TTL_MINUTES_KEY),
                                                _as_int(os.getenv("OWNER_OTP_TTL_MINUTES"), 10))),
        # v4b0 gates. Each keeps its historical env var as the default so an untouched
        # install is byte-for-byte unchanged.
        "ac_downgrade_otp_required": _bool_or(get_setting(db, FRAUD_AC_DOWNGRADE_OTP_KEY),
                                              _env_bool("AC_DOWNGRADE_OTP_REQUIRED")),
        "alt_room_type_otp_required": _bool_or(get_setting(db, FRAUD_ALT_ROOM_TYPE_OTP_KEY),
                                               _env_bool("ALT_ROOM_TYPE_OTP_REQUIRED")),
        # A comp is 100% of the stay and has no floor to fall back on, so unlike the other
        # gates this one ships ARMED.
        "comp_otp_required": _bool_or(get_setting(db, FRAUD_COMP_OTP_KEY),
                                      _env_bool("COMP_OTP_REQUIRED", "true")),
        # Likewise: an automatic charge must never be reversible without approval.
        "overstay_reverse_otp_required": _bool_or(get_setting(db, FRAUD_OVERSTAY_REVERSE_OTP_KEY),
                                                  _env_bool("OVERSTAY_REVERSE_OTP_REQUIRED", "true")),
        "rs_cancel_otp_required": _bool_or(get_setting(db, FRAUD_RS_CANCEL_OTP_KEY),
                                           _env_bool("RS_CANCEL_OTP_REQUIRED")),
        "checkout_no_card_otp_required": _bool_or(get_setting(db, FRAUD_CHECKOUT_NO_CARD_OTP_KEY),
                                                  _env_bool("CHECKOUT_NO_CARD_OTP_REQUIRED")),
    }


# ---------------------------------------------------------------------------
# Overstay auto-billing (v4b3)
# ---------------------------------------------------------------------------
OVERSTAY_ENABLED_KEY = "overstay_auto_charge_enabled"
OVERSTAY_GRACE_KEY = "overstay_grace_minutes"
OVERSTAY_INTERVAL_KEY = "overstay_sweep_interval_minutes"
OVERSTAY_MAX_DAYS_KEY = "overstay_max_auto_days"

OVERSTAY_EDITABLE_KEYS = (
    OVERSTAY_ENABLED_KEY, OVERSTAY_GRACE_KEY, OVERSTAY_INTERVAL_KEY, OVERSTAY_MAX_DAYS_KEY,
)


def get_overstay_config(db) -> dict:
    """Automatic overstay billing (v4b3).

    ⚠️ `enabled` ships **false**. This is the only job in the system that spends a guest's
    money unattended, so the owner arms it from the Settings screen after watching
    `POST /reports/overstay/run?dry_run=true` for a while.

    `grace_minutes` is deliberately its own key rather than reusing CARD_GRACE_MINUTES: the
    card grace is a lock-clock drift buffer, the billing grace is a courtesy window, and a
    hotel may well want the card to die at +60 but not bill until +90.
    ⚠️ The grace only decides WHEN the sweep acts. The night it charges always runs from the
    booking's own checkout moment, never from grace expiry — see overstay_billing.
    """
    return {
        "enabled": _bool_or(get_setting(db, OVERSTAY_ENABLED_KEY),
                            _env_bool("OVERSTAY_AUTO_CHARGE_ENABLED")),
        "grace_minutes": max(0, _as_int(get_setting(db, OVERSTAY_GRACE_KEY),
                                        _as_int(os.getenv("OVERSTAY_GRACE_MINUTES"), 60))),
        "sweep_interval_minutes": max(1, _as_int(get_setting(db, OVERSTAY_INTERVAL_KEY),
                                                 _as_int(os.getenv("OVERSTAY_SWEEP_INTERVAL_MINUTES"), 15))),
        "max_auto_days": max(1, _as_int(get_setting(db, OVERSTAY_MAX_DAYS_KEY),
                                        _as_int(os.getenv("OVERSTAY_MAX_AUTO_DAYS"), 3))),
    }


def allowed_issue_hours_window(db):
    """Parse the configured 'HH-HH' window -> (start, end); allowed = start <= hour < end."""
    try:
        a, b = get_fraud_config(db)["allowed_issue_hours"].split("-")
        start, end = int(a), int(b)
        if 0 <= start <= 24 and 0 <= end <= 24 and start < end:
            return start, end
    except (ValueError, AttributeError):
        pass
    return 6, 23


# Keys an admin may edit through PUT /complaints/config.
COMPLAINT_EDITABLE_KEYS = tuple(
    [COMPLAINT_SLA_RESPONSE_PREFIX + p for p in ("urgent", "high", "normal", "low")]
    + [COMPLAINT_SLA_RESOLVE_PREFIX + p for p in ("urgent", "high", "normal", "low")]
    + [COMPLAINT_ESCALATION_ENABLED_KEY, COMPLAINT_AUTO_COMPENSATION_ENABLED_KEY]
)

# Fallback SLA hours if a key is somehow missing (mirror the migration seeds).
_COMPLAINT_SLA_RESPONSE_DEFAULTS = {"urgent": 1, "high": 2, "normal": 4, "low": 8}
_COMPLAINT_SLA_RESOLVE_DEFAULTS = {"urgent": 4, "high": 8, "normal": 24, "low": 48}


def get_complaints_config(db) -> dict:
    """Typed guest-complaint config (prompt 18c slice 11): per-priority respond-by and resolve-by
    SLA hours (used to stamp due-times at create and drive the escalation sweep), whether SLA
    escalation is active, and whether compensation may auto-post (default off — compensation is an
    admin, reason-required, audited action)."""
    resp, res = {}, {}
    for p in ("urgent", "high", "normal", "low"):
        resp[p] = max(1, _as_int(get_setting(db, COMPLAINT_SLA_RESPONSE_PREFIX + p),
                                 _COMPLAINT_SLA_RESPONSE_DEFAULTS[p]))
        res[p] = max(1, _as_int(get_setting(db, COMPLAINT_SLA_RESOLVE_PREFIX + p),
                                _COMPLAINT_SLA_RESOLVE_DEFAULTS[p]))
    return {
        "sla_response_hours": resp,
        "sla_resolve_hours": res,
        "escalation_enabled": _as_bool(get_setting(db, COMPLAINT_ESCALATION_ENABLED_KEY, "true")),
        "auto_compensation_enabled": _as_bool(get_setting(db, COMPLAINT_AUTO_COMPENSATION_ENABLED_KEY, "false")),
    }


# Keys an admin may edit through PUT /portal/config.
PORTAL_EDITABLE_KEYS = (
    GUEST_PORTAL_ENABLED_KEY, PORTAL_ROOM_SERVICE_ENABLED_KEY, PORTAL_WIFI_ENABLED_KEY,
    PORTAL_WAKEUP_ENABLED_KEY, PORTAL_CAB_ENABLED_KEY, PORTAL_CONTACTLESS_CHECKOUT_ENABLED_KEY,
    WIFI_SSID_KEY, WIFI_VOUCHER_MODE_KEY,
)


def get_portal_config(db) -> dict:
    """Typed guest-portal config (prompt 18d slice 10): the master on/off, the per-feature toggles
    the in-room QR page reads to decide what to show, and the WiFi SSID + voucher mode. `enabled`
    is the master gate — when false the whole portal is off regardless of the feature toggles."""
    mode = (get_setting(db, WIFI_VOUCHER_MODE_KEY) or "auto").strip().lower()
    if mode not in ("auto", "manual"):
        mode = "auto"
    return {
        "enabled": _as_bool(get_setting(db, GUEST_PORTAL_ENABLED_KEY, "true")),
        "room_service_enabled": _as_bool(get_setting(db, PORTAL_ROOM_SERVICE_ENABLED_KEY, "true")),
        "wifi_enabled": _as_bool(get_setting(db, PORTAL_WIFI_ENABLED_KEY, "true")),
        "wakeup_enabled": _as_bool(get_setting(db, PORTAL_WAKEUP_ENABLED_KEY, "true")),
        "cab_enabled": _as_bool(get_setting(db, PORTAL_CAB_ENABLED_KEY, "true")),
        "contactless_checkout_enabled": _as_bool(get_setting(db, PORTAL_CONTACTLESS_CHECKOUT_ENABLED_KEY, "true")),
        "wifi_ssid": (get_setting(db, WIFI_SSID_KEY, "") or "").strip(),
        "wifi_voucher_mode": mode,
    }


# =====================================================================
# Editable option-lists / taxonomies + printed text (F-A settings backbone).
# The owner-facing category families below were hard-coded as Pydantic regexes in
# schemas.py. They now live here as JSON lists so an admin can add/rename options
# WITHOUT a redeploy. The schemas were loosened to a slug pattern; list membership
# is enforced in the create endpoints via validate_category(). Defaults mirror the
# original regex values, so a fresh DB behaves exactly as before.
# =====================================================================
CATEGORY_FAMILIES = {
    "expense":     ["supplies", "staff", "vendor", "misc"],
    "maintenance": ["electrical", "plumbing", "carpentry", "appliance", "lock", "other"],
    "complaint":   ["cleanliness", "noise", "maintenance", "service", "billing", "amenities", "staff", "other"],
    "menu":        ["food", "beverage", "snack", "service"],
    # Backlog v2 FE-6 — the last two hard-coded option lists in the web admin
    # (Inventory.jsx and Vendors.jsx each shipped their own array).
    "stock":       ["minibar", "toiletries", "linen", "supplies", "fnb", "cleaning", "other"],
    "vendor":      ["laundry", "lock_amc", "linen", "electrical", "plumbing", "it",
                    "fnb", "security", "other"],
    # v3 item 2 — the remaining hard-coded desk dropdowns. Defaults mirror the C# arrays
    # in ReceptionApp exactly, so an untouched install behaves byte-for-byte as before.
    "id_type":     ["aadhaar", "passport", "driving_licence", "voter_id", "other"],
    "booking_source": ["walk_in", "frontdesk", "agent", "makemytrip", "goibibo",
                       "booking_com", "agoda", "yatra", "other_ota", "other"],
    # Subset of booking_source. Exists so the desk's "is this an OTA booking?" flag stays
    # in sync with the source list instead of being a second hard-coded array.
    "ota_source":  ["makemytrip", "goibibo", "booking_com", "agoda", "yatra", "other_ota"],
    "charge_type": ["food", "misc", "minibar", "laundry", "extra_bed"],
    "priority":    ["low", "normal", "high", "urgent"],
    # v4b8: who cleaned / who inspected, as editable NAME lists. Deliberately names rather
    # than user accounts, so a contract cleaner can be recorded without creating them a login.
    # ⚠️ Parallel to the login-derived name from HousekeepingTask.assigned_to — see
    # housekeeping.inspect_room. One is "which login did the work", this is "whose name goes
    # on the sheet". The store, endpoints and validator are already generic, so adding the
    # families here is all that is needed server-side.
    "cleaned_by":   ["housekeeping_team"],
    "inspected_by": ["housekeeping_team"],
    # NOT here, deliberately — these look like dropdowns but are contracts, and an admin
    # adding a value would break money handling or the encoder rather than add a label:
    #   payment / refund / collect method -> Payment.method, the cash-drawer gate
    #     (payments.py) and the four hard-coded buckets in reports.py.
    #   staff card scope -> the StaffCardScope enum the 32-bit encoder DLL accepts; a card
    #     type the lock firmware does not implement cannot be cut.
    #   bill-to scope -> a client-side behaviour switch (which lines to bill), not data.
}
_CATEGORY_KEY_PREFIX = "categories_"     # + family; stored as a JSON array of slugs
_SLUG_RE = re.compile(r"^[a-z0-9_]{2,40}$")

# Folio charge types the reports layer treats specially (reports.py `sales_by_date` splits
# room revenue out and skips payment/discount rows). They are POSTED BY THE SYSTEM, never
# chosen from the desk dropdown, so they must never enter the editable `charge_type` family
# — an admin adding "room" there would silently corrupt every revenue report.
RESERVED_CHARGE_TYPES = ("room", "payment", "discount")

# Families that must stay a subset of another family: {family: parent_family}.
SUBSET_FAMILIES = {"ota_source": "booking_source"}

# Registration-slip rules/terms printed on the guest reg-slip (FE-2). Admin-editable text.
REGISTRATION_RULES_KEY = "registration_rules_text"
_REGISTRATION_RULES_DEFAULT = (
    "1. Check-out time is 24 hours from the time of check-in unless otherwise agreed.\n"
    "2. The room key-card must be returned at check-out; a lost/unreturned card is chargeable.\n"
    "3. A valid government photo ID is mandatory for every guest staying in the room.\n"
    "4. The hotel is not responsible for cash/valuables not deposited at the reception.\n"
    "5. Guests are liable for any damage to hotel property during their stay."
)


def _clean_slug(v) -> str | None:
    """Normalise a user-supplied category to a safe slug, or None if unusable."""
    s = str(v or "").strip().lower().replace(" ", "_").replace("-", "_")
    return s if _SLUG_RE.match(s) else None


def get_category_list(db, family: str) -> list:
    """Configured option-list for a category family, else its seed default. Always non-empty."""
    default = CATEGORY_FAMILIES.get(family, [])
    raw = get_setting(db, _CATEGORY_KEY_PREFIX + family)
    if raw:
        try:
            items = [c for c in (json.loads(raw) or []) if isinstance(c, str) and _SLUG_RE.match(c)]
            if items:
                return items
        except (ValueError, TypeError):
            pass
    return list(default)


def get_all_category_lists(db) -> dict:
    """{family: [slugs]} for every editable family (current value or default)."""
    return {fam: get_category_list(db, fam) for fam in CATEGORY_FAMILIES}


def set_category_list(db, family: str, items, user=None, commit=False) -> list:
    """Replace a family's option-list. Slugs are cleaned/deduped and must stay non-empty."""
    if family not in CATEGORY_FAMILIES:
        raise ValueError(f"Unknown category family: {family}")
    cleaned, seen = [], set()
    for it in (items or []):
        s = _clean_slug(it)
        if s and s not in seen:
            seen.add(s)
            cleaned.append(s)
    if not cleaned:
        raise ValueError("A category list cannot be empty.")
    if family == "charge_type":
        clash = [c for c in cleaned if c in RESERVED_CHARGE_TYPES]
        if clash:
            raise ValueError(
                f"{', '.join(clash)} {'are' if len(clash) > 1 else 'is'} reserved for system-posted "
                f"lines and cannot be a manual charge type.")
    parent = SUBSET_FAMILIES.get(family)
    if parent:
        allowed = get_category_list(db, parent)
        extra = [c for c in cleaned if c not in allowed]
        if extra:
            raise ValueError(
                f"{', '.join(extra)} must first exist in the {parent} list "
                f"(it currently holds: {', '.join(allowed)}).")
    set_setting(db, _CATEGORY_KEY_PREFIX + family, json.dumps(cleaned), user=user, commit=commit)
    return cleaned


def validate_category(db, family: str, value: str) -> str:
    """Return the normalised value if it's in the family's configured list, else raise 422.
    Called by create endpoints after their schema loosened its regex to a plain slug."""
    from fastapi import HTTPException
    allowed = get_category_list(db, family)
    v = (value or "").strip().lower()
    if v in allowed:
        return v
    raise HTTPException(
        status_code=422,
        detail=f"'{value}' is not a valid {family} category. Allowed: {', '.join(allowed)}")


def get_registration_rules(db) -> str:
    """Admin-editable rules/terms text printed on the guest registration slip (FE-2)."""
    return (get_setting(db, REGISTRATION_RULES_KEY, _REGISTRATION_RULES_DEFAULT) or "").strip()
