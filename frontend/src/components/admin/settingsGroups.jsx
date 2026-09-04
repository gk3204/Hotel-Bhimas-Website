// Every admin-editable config group, declared as data (backlog v2 FE-12).
//
// These settings used to live on ten different pages. Declaring them here means the
// Settings hub and the origin page render the SAME editor (ConfigPanel), so there is
// exactly one place a value can be changed and they cannot drift apart.
//
// A group is: { key, label, title, description, fields[], load(), save(patch) }.
// A field is: { key, label, hint, type, ... } where type is
//   toggle | text | number | select | textarea | csv.
// `load`/`save` may remap keys where the API's GET and PUT shapes differ.
import React from "react";
import { getCashConfig, updateCashConfig } from "../../api/cashShift";
import { getConfig as getHkConfig, updateConfig as updateHkConfig } from "../../api/housekeeping";
import { getPortalConfig, updatePortalConfig } from "../../api/portal";
import { getComplaintsConfig, updateComplaintsConfig } from "../../api/complaints";
import { getStockConfig, updateStockConfig } from "../../api/stock";
import { getBackofficeConfig, updateBackofficeConfig } from "../../api/backoffice";
import { getUsers } from "../../api/users";
import { getComplianceConfig, updateComplianceConfig } from "../../api/compliance";
import { getWhatsappConfig, updateWhatsappConfig } from "../../api/whatsapp";
import { getReviewConfig, updateReviewConfig } from "../../api/reviews";
import { getConfig as getFraudConfig, updateFraudConfig } from "../../api/fraud";
import { getOverstayConfig, updateOverstayConfig } from "../../api/reports";

const PRIORITIES = ["urgent", "high", "normal", "low"];
const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);

export const SETTINGS_GROUPS = [
  {
    key: "fraud",
    label: "Fraud & approvals",
    title: "Anti-fraud thresholds & approval gates",
    description:
      "Which sensitive actions need the owner's approval code, and when the detectors raise an alert. " +
      "These were previously fixed at deploy time — changing one meant a redeploy.",
    load: getFraudConfig,
    save: updateFraudConfig,
    fields: [
      { key: "refund_requires_owner_otp", type: "toggle", label: "Refunds need owner approval",
        hint: "Reception can refund, but only with a one-time code from the owner." },
      { key: "discount_otp_required", type: "toggle", label: "Deep discounts need owner approval",
        hint: "Applies to discounts past the floor percentage." },
      { key: "card_issue_otp_required", type: "toggle", label: "Lost / extra key cards need owner approval",
        hint: "The desk cannot cut a replacement or additional card until the owner approves." },
      { key: "ac_downgrade_otp_required", type: "toggle", label: "Moving a guest to a non-A/C room needs owner approval",
        hint: "Applies when a room shift takes the guest from an A/C room type to a non-A/C one." },
      { key: "alt_room_type_otp_required", type: "toggle", label: "Selling a room as its alternate type needs owner approval",
        hint: "Only possible when no room of the booked type is free — the desk is refused outright while one is." },
      { key: "comp_otp_required", type: "toggle", label: "Complimentary stays need owner approval",
        hint: "Both marking a stay free and taking that away. A comp is 100% of the stay, so this ships ON." },
      { key: "overstay_reverse_otp_required", type: "toggle", label: "Removing an automatic overstay charge needs owner approval",
        hint: "The charge a room past its checkout grace picks up automatically. Ships ON." },
      { key: "rs_cancel_otp_required", type: "toggle", label: "Cancelling a room-service order needs owner approval",
        hint: "The kitchen docket may already be out, so a cancel can hide food that was made." },
      { key: "checkout_no_card_otp_required", type: "toggle", label: "Checking out without the key card needs owner approval",
        hint: "Leave this OFF at first — the desk still records whether a card came back, so you can see how often it happens before making it blocking." },
      { key: "owner_otp_ttl_minutes", type: "number", min: 1, max: 1440, label: "Approval code valid for (minutes)",
        hint: "How long a code stays usable before it expires." },
      { key: "cleaning_max_hours", type: "number", min: 0, max: 168, label: "Alert if a room sits in cleaning for (hours)",
        hint: "A room held in cleaning this long with nobody checked in raises an alert." },
      { key: "inspection_max_hours", type: "number", min: 0, max: 168, label: "Alert if a cleaned room is not inspected for (hours)",
        hint: "A room cleaned but not signed-off (inspected) within this many hours raises an alert." },
      { key: "repeat_refund_threshold", type: "number", min: 1, max: 100, label: "Alert after this many refunds to one reference" },
      { key: "allowed_issue_hours", type: "text", placeholder: "06-23", label: "Cards may normally be cut between (HH-HH)",
        hint: "A card cut outside this window is flagged for review — it is not blocked." },
      { key: "allowed_stations", type: "csv", label: "Allowed front-desk stations",
        hint: "Comma-separated station IDs. Leave blank to allow any station.", wide: true },
    ],
  },
  {
    key: "overstay",
    label: "Overstay billing",
    title: "Automatic charge for guests who don't leave",
    description:
      "A guest still in the room past their check-out time picks up the next day's rent " +
      "automatically, and their key card stops working. Removing that charge needs your " +
      "approval code. Ships switched OFF — watch the dry run first (Reports → Overstay).",
    load: getOverstayConfig,
    save: updateOverstayConfig,
    fields: [
      { key: "enabled", type: "toggle", label: "Charge overstaying guests automatically",
        hint: "OFF by default. This is the only thing in the system that bills a guest without a person pressing anything, so turn it on once you have watched the dry run for a few days.",
        wide: true },
      { key: "grace_minutes", type: "number", min: 0, max: 1440,
        label: "Wait this long after check-out time before charging (minutes)",
        hint: "A courtesy window. It only delays the charge — the day charged still runs from the guest's actual check-out time, not from the end of this window." },
      { key: "max_auto_days", type: "number", min: 1, max: 60,
        label: "Stop after this many automatic days",
        hint: "A safety net for a forgotten check-out: after this many days the system stops charging and raises an alert for someone to look at, instead of quietly running up 40 nights." },
      { key: "sweep_interval_minutes", type: "number", min: 1, max: 1440,
        label: "Check for overstays every (minutes)",
        hint: "Takes effect after the next backend restart." },
    ],
  },
  {
    key: "cash",
    label: "Cash & shift",
    title: "Cash drawer & shift reconciliation",
    load: getCashConfig,
    save: updateCashConfig,
    fields: [
      { key: "cycle", type: "select", label: "Reconciliation cycle",
        options: [{ value: "shift", label: "Per shift" }, { value: "day", label: "Per day" }] },
      { key: "variance_threshold", type: "number", min: 0, step: "0.01", label: "Variance alert threshold (₹)",
        hint: "A counted-vs-expected gap bigger than this alerts the owner." },
      { key: "variance_alert_enabled", type: "toggle", label: "Alert the owner on an over-threshold variance" },
    ],
  },
  {
    key: "housekeeping",
    label: "Housekeeping",
    title: "Housekeeping",
    load: getHkConfig,
    // The GET also returns cleaning_max_hours, which is edited on the Fraud tab; only
    // send what this endpoint actually accepts.
    save: (patch) => updateHkConfig({ auto_inspect: !!patch.auto_inspect }),
    readOnlyNote:
      "The 'room stuck in cleaning' alert threshold lives on the Fraud & approvals tab.",
    fields: [
      { key: "auto_inspect", type: "toggle", label: "Marking a room clean also inspects it",
        hint: "Off (recommended): a supervisor must inspect before the room can be sold again.",
        wide: true },
    ],
  },
  {
    key: "portal",
    label: "Guest portal",
    title: "In-room QR guest portal",
    description: "What guests can do from the QR code in their room.",
    load: async () => {
      // GET returns short keys; PUT expects prefixed ones.
      const c = await getPortalConfig();
      return {
        guest_portal_enabled: c.enabled,
        portal_room_service_enabled: c.room_service_enabled,
        portal_wifi_enabled: c.wifi_enabled,
        portal_wakeup_enabled: c.wakeup_enabled,
        portal_cab_enabled: c.cab_enabled,
        portal_contactless_checkout_enabled: c.contactless_checkout_enabled,
        wifi_ssid: c.wifi_ssid,
        wifi_voucher_mode: c.wifi_voucher_mode,
      };
    },
    save: updatePortalConfig,
    fields: [
      { key: "guest_portal_enabled", type: "toggle", label: "Guest portal enabled",
        hint: "Master switch — off hides every feature below.", wide: true },
      { key: "portal_room_service_enabled", type: "toggle", label: "Room service ordering" },
      { key: "portal_wifi_enabled", type: "toggle", label: "WiFi voucher" },
      { key: "portal_wakeup_enabled", type: "toggle", label: "Wake-up call requests" },
      { key: "portal_cab_enabled", type: "toggle", label: "Cab requests" },
      { key: "portal_contactless_checkout_enabled", type: "toggle", label: "Contactless checkout requests",
        hint: "Raises a request for the desk — the guest never checks themselves out." },
      { key: "wifi_ssid", type: "text", label: "WiFi network name (SSID)" },
      { key: "wifi_voucher_mode", type: "select", label: "WiFi code mode",
        options: [{ value: "auto", label: "Generate per stay" }, { value: "manual", label: "Staff enter it" }] },
    ],
  },
  {
    key: "complaints",
    label: "Complaints SLA",
    title: "Guest-complaint response times",
    description: "How long staff have to respond to and resolve a complaint before it escalates.",
    load: async () => {
      const c = await getComplaintsConfig();
      const flat = {
        complaint_escalation_enabled: c.escalation_enabled,
        complaint_auto_compensation_enabled: c.auto_compensation_enabled,
      };
      for (const p of PRIORITIES) {
        flat[`complaint_sla_response_hours_${p}`] = c.sla_response_hours?.[p];
        flat[`complaint_sla_resolve_hours_${p}`] = c.sla_resolve_hours?.[p];
      }
      return flat;
    },
    save: updateComplaintsConfig,
    fields: [
      { key: "complaint_escalation_enabled", type: "toggle", label: "Escalate complaints that breach their SLA",
        wide: true },
      { key: "complaint_auto_compensation_enabled", type: "toggle",
        label: "Allow automatic goodwill compensation",
        hint: "Off (recommended): compensation stays a reason-required admin action.", wide: true },
      ...PRIORITIES.map((p) => ({
        key: `complaint_sla_response_hours_${p}`, type: "number", min: 1,
        label: `${cap(p)} — respond within (hours)`,
      })),
      ...PRIORITIES.map((p) => ({
        key: `complaint_sla_resolve_hours_${p}`, type: "number", min: 1,
        label: `${cap(p)} — resolve within (hours)`,
      })),
    ],
  },
  {
    key: "stock",
    label: "Inventory",
    title: "Inventory & low-stock alerts",
    load: getStockConfig,
    save: updateStockConfig,
    fields: [
      { key: "low_stock_alerts_enabled", type: "toggle", label: "Alert the owner when an item runs low" },
      { key: "low_stock_default_threshold", type: "number", min: 0, step: "0.01",
        label: "Default reorder level",
        hint: "Used when an item has no threshold of its own." },
    ],
  },
  {
    key: "backoffice",
    label: "Back office",
    title: "Corporate billing, vendors & staff",
    load: getBackofficeConfig,
    save: updateBackofficeConfig,
    fields: [
      { key: "company_credit_block", type: "toggle", label: "Block bookings past a company's credit limit",
        hint: "Off = warn only. An admin can always override with a reason.", wide: true },
      { key: "company_default_credit_days", type: "number", min: 0, max: 365, label: "Default credit days" },
      { key: "company_invoice_prefix", type: "text", label: "Consolidated invoice prefix" },
      { key: "vendor_renewal_alerts_enabled", type: "toggle", label: "Remind me before a contract renews" },
      { key: "vendor_renewal_lead_days", type: "number", min: 1, max: 365, label: "Renewal reminder lead (days)" },
      { key: "roster_default_shift_type", type: "text", label: "Default roster shift type" },
      { key: "attendance_pin_enabled", type: "toggle", label: "Staff may clock in with a PIN" },
      { key: "attendance_auto_close_hours", type: "number", min: 1, max: 48,
        label: "Auto-close a forgotten clock-out after (hours)" },
      // v4b8 (R20): tickets land on a person, not in a queue nobody owns. Options are the
      // active `maintenance` logins, loaded at render — a new technician appears here the
      // moment they are created, with no redeploy.
      { key: "maintenance_default_assignee_id", type: "select", wide: true,
        label: "New maintenance tickets go to",
        hint: "Every ticket — raised at the desk, by housekeeping or by a guest — is assigned to this technician automatically. Leave as 'nobody' to keep tickets open for a technician to claim.",
        options: [{ value: 0, label: "— nobody: leave tickets open to claim —" }],
        optionsLoad: async () => {
          const users = await getUsers();
          const list = Array.isArray(users) ? users : users?.data || [];
          return [
            { value: 0, label: "— nobody: leave tickets open to claim —" },
            ...list
              .filter((u) => u.role === "maintenance" && u.is_active !== false)
              .map((u) => ({ value: u.user_id, label: u.full_name || u.username })),
          ];
        } },
    ],
  },
  {
    key: "messaging",
    label: "Messaging",
    title: "WhatsApp & email automations",
    description:
      "Which messages go out automatically. If WhatsApp is unavailable the same message is sent by email where an address is on file.",
    load: getWhatsappConfig,
    save: updateWhatsappConfig,
    fields: [
      { key: "confirmation_enabled", type: "toggle", label: "Booking confirmation" },
      { key: "receipt_enabled", type: "toggle", label: "Payment receipt" },
      { key: "room_ready_enabled", type: "toggle", label: "Room ready" },
      { key: "portal_link_enabled", type: "toggle", label: "In-room portal link at check-in" },
      { key: "checkout_reminder_enabled", type: "toggle", label: "Check-out reminder" },
      { key: "checkout_reminder_lead_hours", type: "number", min: 0.5, max: 48, step: "0.5",
        label: "Send the check-out reminder this many hours before" },
      { key: "overstay_enabled", type: "toggle", label: "Overstay notice" },
      { key: "review_enabled", type: "toggle", label: "Ask for a review after check-out" },
      { key: "review_delay_hours", type: "number", min: 0, max: 168, step: "0.5",
        label: "Wait this many hours before asking for a review" },
      { key: "owner_alerts_enabled", type: "toggle", label: "Send owner alerts (approval codes, fraud, digest)" },
      { key: "owner_whatsapp", type: "text", label: "Owner WhatsApp number(s)", wide: true,
        hint: "Approval codes and alerts go here. Comma-separate for more than one owner — each is notified. Leave blank to use the on-screen inbox only." },
      { key: "owner_email", type: "text", label: "Owner email(s)", wide: true,
        hint: "Fallback for owner alerts when WhatsApp is unavailable. Comma-separate for more than one owner (paired with the numbers above, in order)." },
      { key: "google_review_url", type: "text", label: "Google review link", wide: true },
      { key: "job_interval_minutes", type: "number", min: 1, max: 1440, label: "Check for due messages every (minutes)" },
      { key: "daily_digest_hour", type: "number", min: 0, max: 23, label: "Send the daily digest at (hour, 24h)" },
      { key: "weekly_digest_enabled", type: "toggle", label: "Send the weekly owner report" },
      { key: "weekly_digest_weekday", type: "select", label: "Send the weekly report on",
        options: [{ value: 0, label: "Monday" }, { value: 1, label: "Tuesday" },
                  { value: 2, label: "Wednesday" }, { value: 3, label: "Thursday" },
                  { value: 4, label: "Friday" }, { value: 5, label: "Saturday" },
                  { value: 6, label: "Sunday" }] },
    ],
  },
  {
    key: "reviews",
    label: "Reviews",
    title: "Google review auto-reply",
    load: getReviewConfig,
    save: updateReviewConfig,
    fields: [
      { key: "auto_reply_enabled", type: "toggle", label: "Reply to reviews automatically" },
      { key: "auto_reply_threshold", type: "number", min: 1, max: 5,
        label: "Auto-thank reviews rated this or higher" },
      { key: "low_band_split", type: "number", min: 1, max: 5, label: "Treat reviews at or below this as poor" },
      { key: "low_auto_send", type: "toggle", label: "Send replies to poor reviews without approval",
        hint: "Off (recommended): a poor review's reply waits in the approval queue." },
      { key: "delay_min_hours", type: "number", min: 0, step: "0.5", label: "Wait at least (hours) before replying" },
      { key: "delay_max_hours", type: "number", min: 0, step: "0.5", label: "…and at most (hours)" },
      { key: "llm_enabled", type: "toggle", label: "Write replies with AI",
        hint: "Off = rotate through the built-in templates." },
      { key: "owner_alerts_enabled", type: "toggle", label: "Alert the owner about a poor review" },
      { key: "poll_interval_minutes", type: "number", min: 1, max: 1440, label: "Check for new reviews every (minutes)" },
    ],
  },
  {
    key: "compliance",
    label: "Compliance",
    title: "Compliance, Tally & admin security",
    load: getComplianceConfig,
    save: updateComplianceConfig,
    // Live provider status — not editable, but the admin needs to see it.
    renderExtra: (cfg) => (
      <div className="text-slate-400 text-sm space-y-1">
        <p>
          e-Invoicing provider: <b className="text-slate-200">{cfg?.einvoice_provider?.mode || "stub"}</b>{" "}
          {cfg?.einvoice_provider?.configured ? "(live)" : "(stub — set GST_EINVOICE_* to go live)"}
        </p>
        <p>2FA library available: <b className="text-slate-200">{cfg?.twofa_available ? "yes" : "no"}</b></p>
      </div>
    ),
    fields: [
      { key: "police_station_name", type: "text", label: "Police station name" },
      { key: "police_station_code", type: "text", label: "Police station code" },
      { key: "frro_office", type: "text", label: "FRRO office" },
      { key: "hotel_registration_no", type: "text", label: "Hotel registration number" },
      { key: "tally_company_name", type: "text", label: "Tally company name" },
      { key: "tally_sales_ledger", type: "text", label: "Tally — sales ledger" },
      { key: "tally_cgst_ledger", type: "text", label: "Tally — CGST ledger" },
      { key: "tally_sgst_ledger", type: "text", label: "Tally — SGST ledger" },
      { key: "tally_cash_ledger", type: "text", label: "Tally — cash ledger" },
      { key: "tally_bank_ledger", type: "text", label: "Tally — bank ledger" },
      { key: "tally_debtors_ledger", type: "text", label: "Tally — debtors ledger" },
      { key: "admin_2fa_required", type: "toggle", label: "Require two-factor login for admins" },
      { key: "admin_idle_logout_minutes", type: "number", min: 1, max: 240,
        label: "Log an idle admin out after (minutes)" },
    ],
  },
];

export const groupByKey = (key) => SETTINGS_GROUPS.find((g) => g.key === key);
