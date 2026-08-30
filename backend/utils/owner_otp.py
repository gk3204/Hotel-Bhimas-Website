"""Owner-approval one-time codes (prompt 11).

Sensitive front-desk actions (refund, below-floor discount, ...) can require an owner
OTP before they proceed. The flow:
    1. desk calls POST /fraud/otp/request   -> create_otp(...) stores a row, returns otp_id
    2. the 6-digit code is delivered on-screen (admin Approvals inbox) + server log
       (THIS build's interim channel; prompt 15 swaps in WhatsApp behind create_otp)
    3. desk re-submits the action with owner_otp_id + owner_otp_code
    4. the gated endpoint calls consume_otp(...) which validates + single-uses the code

Enforcement is opt-in per action via env flags (default OFF) so existing behaviour and
the prompt-08 tests are unchanged until an owner turns it on.
"""
import json
import logging
import os
import secrets
from datetime import datetime, timedelta

from fastapi import HTTPException

from models import OwnerOtp
from utils.audit import _resolve_user_id

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# The action vocabulary (v4b0)
# ---------------------------------------------------------------------------
# This list used to live as a regex on schemas.OtpRequest.action, and it had gone stale:
# it allowed only refund / discount_below_floor / void / off_hours_issue, while the desk
# was already sending "card_issue" (ALT-1) and "ac_downgrade" (FE-10). Both 422'd before
# create_otp was ever reached, so those two gates were UNREACHABLE from the front desk —
# unnoticed only because both default off. Keep the list here, next to consume_otp, so it
# lives with its consumers and cannot drift from them again.
OTP_ACTIONS = frozenset({
    # shipped
    "refund",
    "discount_below_floor",
    "void",
    "off_hours_issue",
    "card_issue",              # ALT-1 lost/duplicate card
    # FE-10 room shift AC→non-AC. v4b7 folds this into "room_assignment" as a *reason* so one
    # button press needs one code; until that ships the desk still sends this string, and
    # dropping it here would break the gate a second time.
    "ac_downgrade",
    # v4
    "room_assignment",         # alt room type and/or AC downgrade, at check-in or shift (v4b7)
    "overstay_reverse",        # reverse an auto-charged overstay night (v4b3)
    "booking_complimentary",   # set/clear a complimentary stay (v4b6)
    "extend_waive",            # extend a stay for less than the quoted amount (v4b2)
    "room_service_cancel",     # cancel a room-service order (v4b5)
    "checkout_no_card",        # check out without the key card on the encoder (v4b4)
})


def _ttl_minutes(db) -> int:
    """Code lifetime. Admin-editable in Settings, defaulting to OWNER_OTP_TTL_MINUTES (FE-12)."""
    try:
        from utils import settings as app_settings
        return app_settings.get_fraud_config(db)["owner_otp_ttl_minutes"]
    except Exception:            # never let a config read break an approval
        try:
            return max(1, int(os.getenv("OWNER_OTP_TTL_MINUTES", "10")))
        except (TypeError, ValueError):
            return 10


def deliver_otp(db, otp, commit: bool = True) -> dict:
    """Push the code to the owner's WhatsApp and report honestly whether it left the
    building (backlog v2 F-C).

    The on-screen Approvals inbox + server log are the channel that ALWAYS works. WhatsApp
    is best-effort on top and must never break the OTP flow, so every failure is swallowed
    — but the caller gets a truthful `{channel, ok, detail}` back so the desk can tell the
    receptionist "sent to the owner's WhatsApp" instead of guessing.

    Note the stub driver reports status="sent" while delivering nothing, so a send only
    counts as real when a provider is actually configured.
    """
    logger.info(f"🔐 Owner OTP for action={otp.action} otp_id={otp.id}: code={otp.code} "
                f"(expires {otp.expires_at.isoformat()})")
    fallback = {"channel": "onscreen", "ok": False,
                "detail": "Could not message the owner — read the code from the admin "
                          "Approvals inbox."}
    try:
        from utils import whatsapp_service
        result = whatsapp_service.send_owner_otp(db, otp, commit=commit)
        if not isinstance(result, dict):
            return fallback
        if result.get("ok"):
            where = "WhatsApp" if result.get("channel") == "whatsapp" else "email"
            return {"channel": result["channel"], "ok": True,
                    "detail": f"Approval code sent to the owner by {where}."}
        return {"channel": "onscreen", "ok": False,
                "detail": (result.get("detail") or "").rstrip(".")
                          + " — read the code from the admin Approvals inbox."}
    except Exception as e:
        logger.warning(f"owner OTP delivery failed (on-screen still works): {e}")
        return fallback


def create_otp(db, action: str, context, user, commit: bool = True, return_delivery: bool = False):
    """Create + persist an owner-approval code for `action`. The code is logged (interim
    delivery) but never returned to the requester — the owner reads it from the admin
    Approvals inbox. Caller is responsible for the surrounding transaction if commit=False.

    Returns the `OwnerOtp`, or `(otp, delivery_dict)` when `return_delivery=True`."""
    code = f"{secrets.randbelow(1_000_000):06d}"
    otp = OwnerOtp(
        action=action,
        context=json.dumps(context, default=str) if context is not None else None,
        code=code,
        expires_at=datetime.utcnow() + timedelta(minutes=_ttl_minutes(db)),
        used=False,
        created_by=_resolve_user_id(db, user),
    )
    db.add(otp)
    db.flush()
    if commit:
        db.commit()
    delivery = deliver_otp(db, otp, commit=commit)
    return (otp, delivery) if return_delivery else otp


# ---------------------------------------------------------------------------
# Context enrichment (v4b0)
# ---------------------------------------------------------------------------
# The owner approves these codes from a phone. Until now `context` was whatever free-form
# dict the DESK posted to /fraud/otp/request, and the web inbox rendered it as a raw
# key:value chip dump — so the owner was reading text the client wrote, and there was
# nothing tying it to the data the enforcement site would later act on. build_context()
# moves the enrichment SERVER-side: the desk sends ids, the server loads the real rows.

def _inr(v) -> str:
    """₹ with Indian digit grouping (12,34,567) — the format used across the PDFs."""
    try:
        n = float(v)
    except (TypeError, ValueError):
        return "—"
    neg, n = n < 0, abs(n)
    whole = f"{int(round(n)):d}"
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        whole = ",".join(parts) + "," + tail
    return f"{'-' if neg else ''}₹{whole}"


def _rooms_label(rooms) -> str:
    nums = [str(r.get("room_number")) for r in (rooms or []) if r.get("room_number")]
    if not nums:
        return "no room assigned"
    return ("Room " if len(nums) == 1 else "Rooms ") + ", ".join(nums)


def _guest_label(ctx) -> str:
    return (ctx.get("guest") or {}).get("name") or "guest"


def _booking_label(ctx) -> str:
    bid = (ctx.get("booking") or {}).get("booking_id")
    return f"booking {bid}" if bid else "no booking"


# One phrasing per action. A missing builder is a VISIBLE omission (the summary falls back
# to the action name), which is the point — adding a gate without telling the owner what
# they are approving should look wrong.
def _sum_refund(ctx):
    return (f"Refund {_inr(abs(ctx.get('amount') or 0))} to {_guest_label(ctx)} — "
            f"{_rooms_label(ctx.get('rooms'))} ({_booking_label(ctx)})")


def _sum_discount(ctx):
    return (f"Discount {_inr(abs(ctx.get('amount') or 0))} on {_guest_label(ctx)}'s bill — "
            f"{_rooms_label(ctx.get('rooms'))} ({_booking_label(ctx)})")


def _sum_void(ctx):
    return (f"Void a {_inr(abs(ctx.get('amount') or 0))} charge — "
            f"{_rooms_label(ctx.get('rooms'))} ({_booking_label(ctx)})")


def _sum_card_issue(ctx):
    kind = {"lost_reissue": "replace a LOST card",
            "extra": "cut an EXTRA card"}.get(ctx.get("issue_type"), "issue a card")
    return f"{kind[0].upper() + kind[1:]} for {_guest_label(ctx)} — {_rooms_label(ctx.get('rooms'))}"


def _sum_ac_downgrade(ctx):
    return (f"Move {_guest_label(ctx)} from an A/C room to a non-A/C room — "
            f"{_rooms_label(ctx.get('rooms'))} ({_booking_label(ctx)})")


def _sum_room_assignment(ctx):
    reasons = ctx.get("reasons") or []
    booked = (ctx.get("booked_type") or {}).get("name")
    physical = (ctx.get("physical_type") or {}).get("name")
    bits = []
    if "alt_room_type" in reasons and booked and physical:
        bits.append(f"sell {physical} {_rooms_label(ctx.get('rooms')).lower()} as {booked}")
    if "ac_downgrade" in reasons:
        bits.append("move the guest from an A/C room to a non-A/C room")
    what = " and ".join(bits) or f"assign {_rooms_label(ctx.get('rooms')).lower()}"
    delta = ctx.get("total_delta")
    money = f" — {_inr(delta)} below rack" if delta else ""
    return f"{what[0].upper() + what[1:]}{money} ({_guest_label(ctx)}, {_booking_label(ctx)})"


def _sum_overstay_reverse(ctx):
    return (f"Reverse the {_inr(abs(ctx.get('amount') or 0))} overstay charge for "
            f"{ctx.get('night_date') or 'the last night'} — {_rooms_label(ctx.get('rooms'))}, "
            f"{_guest_label(ctx)} ({_booking_label(ctx)})")


def _sum_complimentary(ctx):
    mode = ctx.get("mode")
    what = {"all": "everything (room + all charges)",
            "room": "room rent only",
            "none": "nothing — CLEARING an existing complimentary stay"}.get(mode, mode)
    worth = ctx.get("amount")
    money = f", worth {_inr(abs(worth))}" if worth else ""
    return (f"Make {_guest_label(ctx)}'s stay complimentary — {what}{money} "
            f"({_rooms_label(ctx.get('rooms'))}, {_booking_label(ctx)})")


def _sum_extend_waive(ctx):
    return (f"Extend {_guest_label(ctx)}'s stay to {ctx.get('new_check_out') or '?'} charging "
            f"{_inr(ctx.get('applied_amount'))} instead of the quoted "
            f"{_inr(ctx.get('quoted_amount'))} — {_rooms_label(ctx.get('rooms'))}")


def _sum_rs_cancel(ctx):
    return (f"Cancel room-service order {ctx.get('kot_no') or ''} worth "
            f"{_inr(abs(ctx.get('amount') or 0))} — {_rooms_label(ctx.get('rooms'))}, "
            f"{_guest_label(ctx)}").replace("  ", " ")


def _sum_checkout_no_card(ctx):
    return (f"Check out {_guest_label(ctx)} WITHOUT the key card being returned — "
            f"{_rooms_label(ctx.get('rooms'))} ({_booking_label(ctx)})")


def _sum_off_hours(ctx):
    return (f"Issue a card outside the permitted hours — {_rooms_label(ctx.get('rooms'))}, "
            f"{_guest_label(ctx)}")


_SUMMARY_BUILDERS = {
    "refund": _sum_refund,
    "discount_below_floor": _sum_discount,
    "void": _sum_void,
    "off_hours_issue": _sum_off_hours,
    "card_issue": _sum_card_issue,
    "ac_downgrade": _sum_ac_downgrade,
    "room_assignment": _sum_room_assignment,
    "overstay_reverse": _sum_overstay_reverse,
    "booking_complimentary": _sum_complimentary,
    "extend_waive": _sum_extend_waive,
    "room_service_cancel": _sum_rs_cancel,
    "checkout_no_card": _sum_checkout_no_card,
}


# Keys the server owns. A client-supplied extra may not overwrite these.
_RESERVED_CONTEXT_KEYS = frozenset({
    "action", "reasons", "requested_by", "requested_at", "booking", "guest", "rooms", "summary",
    # build_context's own keyword params — a client context carrying any of these (e.g. the
    # overstay payload's "amount") would spread into **extra and raise
    # "got multiple values for keyword argument". Strip them; the explicit args carry them.
    "db", "folio", "amount", "user",
})


def safe_extras(context) -> dict:
    """Strip the server-owned keys out of a CLIENT-supplied context dict.

    Call this before spreading a client dict into build_context(**extras). Several reserved
    names ("booking", "rooms", ...) are also build_context's own keyword parameters, so an
    unfiltered spread raises TypeError: got multiple values for keyword argument — a 500 on
    a request the client fully controls. Filtering here turns that into a silent, correct
    drop, and build_context re-checks the same set as defence in depth."""
    return {k: v for k, v in (context or {}).items()
            if k not in _RESERVED_CONTEXT_KEYS}


def _num(v):
    """Numeric(10,2) comes back as Decimal, which json.dumps cannot serialise."""
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def build_context(db, action: str, *, booking=None, rooms=None, folio=None, guest=None,
                  amount=None, user=None, reasons=None, **extra) -> dict:
    """Build the approval envelope the owner reads. Every call site produces the same shape,
    so the web inbox never has to guess what a given action put in the dict.

    `rooms` may be Room ORM objects or plain dicts. `booking`/`folio`/`guest` are ORM
    objects or None. Anything action-specific rides in **extra. Never raises — a context
    that cannot be built must not block an approval."""
    ctx = {
        "action": action,
        "reasons": list(reasons or []),
        "requested_by": (user or {}).get("username") if isinstance(user, dict) else getattr(user, "username", None),
        "requested_at": datetime.utcnow().isoformat(),
    }

    try:
        if booking is not None:
            nights = None
            try:
                nights = max(1, (booking.check_out - booking.check_in).days)
            except Exception:
                pass
            ctx["booking"] = {
                "booking_id": booking.booking_id,
                "status": booking.status,
                "booking_source": booking.booking_source,
                "check_in": str(booking.check_in) if booking.check_in else None,
                "check_out": str(booking.check_out) if booking.check_out else None,
                # v4b1/v4b6 add these columns; getattr keeps this batch migration-free.
                "original_check_out": (str(getattr(booking, "original_check_out", None))
                                       if getattr(booking, "original_check_out", None) else None),
                "comp_mode": getattr(booking, "comp_mode", None),
                "nights": nights,
                "grand_total": _num(booking.grand_total),
            }
            if guest is None:
                guest = getattr(booking, "guest", None)
    except Exception as e:                                  # pragma: no cover - defensive
        logger.warning(f"otp context: booking block failed: {e}")

    try:
        if guest is not None:
            ctx["guest"] = {"name": guest.name, "phone": guest.phone,
                            "id_number_masked": getattr(guest, "id_number_masked", None)}
    except Exception as e:                                  # pragma: no cover
        logger.warning(f"otp context: guest block failed: {e}")

    try:
        if folio is not None:
            ctx.setdefault("booking", {})
            ctx["booking"]["folio_id"] = folio.id
            ctx["booking"]["folio_balance"] = _num(folio.balance)
            ctx["booking"]["folio_status"] = folio.status
    except Exception as e:                                  # pragma: no cover
        logger.warning(f"otp context: folio block failed: {e}")

    try:
        out = []
        for r in (rooms or []):
            if isinstance(r, dict):
                out.append({k: r.get(k) for k in ("room_id", "room_number", "room_type_name")})
                continue
            out.append({"room_id": r.room_id, "room_number": r.room_number,
                        "room_type_name": None, "_rt_id": getattr(r, "room_type_id", None)})
        # `Room` has NO room_type relationship (only BookingItem does), so the type name has
        # to be looked up. One query for the whole list rather than N.
        missing = {o.pop("_rt_id", None) for o in out}
        missing = {m for m in missing if m}
        if missing and db is not None:
            from models import RoomType
            names = dict(db.query(RoomType.room_type_id, RoomType.name)
                           .filter(RoomType.room_type_id.in_(missing)).all())
            for o, r in zip(out, rooms or []):
                if o.get("room_type_name") is None and not isinstance(r, dict):
                    o["room_type_name"] = names.get(getattr(r, "room_type_id", None))
        for o in out:
            o.pop("_rt_id", None)
        if out:
            ctx["rooms"] = out
    except Exception as e:                                  # pragma: no cover
        logger.warning(f"otp context: rooms block failed: {e}")

    if amount is not None:
        ctx["amount"] = _num(amount)

    # Action-specific extras, dropped in before the summary so builders can read them.
    # The server-built blocks are RESERVED: extras arrive from the client, and letting them
    # overwrite booking/guest/rooms would put the whole enrichment back in the caller's hands,
    # which is the thing this function exists to stop.
    for k, v in (extra or {}).items():
        if v is not None and k not in _RESERVED_CONTEXT_KEYS:
            ctx[k] = v

    builder = _SUMMARY_BUILDERS.get(action)
    try:
        ctx["summary"] = builder(ctx) if builder else f"Approve: {action.replace('_', ' ')}"
    except Exception as e:                                  # pragma: no cover
        logger.warning(f"otp context: summary for {action} failed: {e}")
        ctx["summary"] = f"Approve: {action.replace('_', ' ')}"
    return ctx


def consume_otp(db, otp_id, code, action: str, user=None):
    """Validate + single-use an owner OTP for `action`. Raises HTTPException on any
    failure (missing, wrong action, expired, already used, wrong code). On success the
    row is marked used and the caller may proceed. Does NOT commit — it joins the caller's
    transaction so the OTP is only spent if the action itself commits."""
    if not otp_id or not code:
        raise HTTPException(status_code=403,
                            detail="owner_otp_required: this action needs an owner approval code")
    otp = db.query(OwnerOtp).filter(OwnerOtp.id == otp_id).first()
    if not otp or otp.action != action:
        raise HTTPException(status_code=403, detail="Invalid owner approval code")
    if otp.used:
        raise HTTPException(status_code=403, detail="Owner approval code already used")
    if otp.expires_at < datetime.utcnow():
        raise HTTPException(status_code=403, detail="Owner approval code expired")
    if not secrets.compare_digest(str(otp.code), str(code)):
        raise HTTPException(status_code=403, detail="Invalid owner approval code")
    otp.used = True
    otp.used_at = datetime.utcnow()
    otp.used_by = _resolve_user_id(db, user)
    db.flush()
    return otp
