"""Google review reply generation + low-rating loop-in (prompt 20).

Turns a `google_reviews` row into a reply:
  * rating >= threshold  -> a varied, name-personalized thank-you (auto-post after a delay),
    UNLESS the review text is heavy + negative despite the rating (then flag for approval).
  * rating <  threshold  -> a softened service-recovery draft (approval queue by default) +
    an owner WhatsApp alert + an auto-created complaint ticket (handled in utils/review_jobs.py).

Two generators, gated by config: a rotating TEMPLATE pool (default) and an optional Claude LLM
generator (`review_llm_enabled` + ANTHROPIC_API_KEY). Both obey the same tone rules:
  - warm + specific to the star band, never copy-paste-identical two replies in a row;
  - a low-rating reply apologizes, says the team is looking into it, and invites the guest to
    contact the hotel directly — NO excuses, NO arguing, NO admitting specific fault;
  - a public reply NEVER includes the matched guest's stay/room/booking details (privacy), even
    when `matched_guest_id` is set — replies are built from author name + generic phrasing only.

Uses the stdlib `requests` library already vendored in the backend (no anthropic SDK / no httpx).
"""
import logging
import os
import random

from models import GoogleReview
from utils import settings as app_settings

logger = logging.getLogger(__name__)

# --- Template pools -------------------------------------------------------------------
# {name} is replaced with the reviewer's first name, or dropped to a generic greeting when absent.

THANKYOU_TEMPLATES = [
    "Thank you so much for the kind words{name}! We're delighted you enjoyed your stay at "
    "Hotel Bhimas and we hope to welcome you back soon.",
    "We really appreciate you taking the time to share this{name}. It means a lot to our team — "
    "thank you, and we look forward to hosting you again at Hotel Bhimas.",
    "Thank you for the wonderful rating{name}! We're so glad you had a great experience with us. "
    "Do come back and see us at Hotel Bhimas.",
    "This made our day{name} — thank you! We work hard to make every stay special and we can't "
    "wait to have you back at Hotel Bhimas.",
    "Grateful for your lovely review{name}! We hope your next stay at Hotel Bhimas is even better. "
    "See you soon!",
    "Thank you{name}! Reviews like yours keep our team smiling. We'd love to welcome you back to "
    "Hotel Bhimas whenever your travels bring you our way.",
]

# Band A: 1-2 stars — sincere, humble apology.
RECOVERY_TEMPLATES_LOW = [
    "We're truly sorry your stay didn't meet expectations{name}. This isn't the experience we want "
    "for any guest, and our team is looking into it. Please reach out to us directly at the front "
    "desk or on WhatsApp so we can make things right.",
    "Thank you for letting us know{name} — we're sorry to hear this, and we take it seriously. Our "
    "management is reviewing what happened. We'd genuinely like the chance to put it right; please "
    "contact us directly so we can help.",
    "We sincerely apologize for the disappointment{name}. Feedback like this helps us do better, "
    "and we're already looking into it. Kindly reach out to us directly by phone or WhatsApp so we "
    "can address your concerns personally.",
]

# Band B: 3 stars — acknowledge, thank, invite to reconnect.
RECOVERY_TEMPLATES_MID = [
    "Thank you for your honest feedback{name}. We're glad parts of your stay worked well, and "
    "we'd like to understand where we can improve. Please feel free to reach out to us directly — "
    "we'd love the chance to make your next visit a great one.",
    "We appreciate you sharing this{name}. There's clearly room for us to do better, and we're "
    "taking your comments on board. If you'd be open to it, please contact us directly so we can "
    "learn more and improve.",
    "Thanks for taking the time to review us{name}. We're always working to improve, and your "
    "feedback helps. Do reach out to us directly — we'd welcome the opportunity to host you again "
    "and get it right.",
]

# Simple lexicon to spot a text-heavy negative review that carries a high star rating anyway
# (a mismatch worth a human's eyes rather than an automatic thank-you).
_NEGATIVE_WORDS = (
    "dirty", "rude", "broken", "worst", "terrible", "awful", "smell", "bug", "cockroach",
    "noisy", "noise", "cold", "leak", "overcharg", "refund", "complaint", "disappoint",
    "unhygienic", "poor", "bad", "horrible", "never again", "avoid", "scam", "cheat",
)


def _first_name(review: GoogleReview) -> str:
    name = (review.author_name or "").strip()
    if not name:
        return ""
    return name.split()[0]


def _name_suffix(review: GoogleReview) -> str:
    """', <FirstName>' for interpolation into a template's {name} slot, or '' when unknown."""
    fn = _first_name(review)
    return f", {fn}" if fn else ""


def _looks_negative_despite_rating(review: GoogleReview) -> bool:
    """A high-star review whose text is substantial and reads negative — flag for a human."""
    text = (review.review_text or "").strip().lower()
    if len(text) < 60:  # short praise doesn't need second-guessing
        return False
    return any(w in text for w in _NEGATIVE_WORDS)


def _last_auto_reply_text(db) -> str | None:
    """The most recent auto-posted reply, so we never post two identical thank-yous in a row."""
    row = (db.query(GoogleReview)
           .filter(GoogleReview.reply_status == "auto_sent", GoogleReview.reply_text.isnot(None))
           .order_by(GoogleReview.replied_at.desc().nullslast(), GoogleReview.id.desc())
           .first())
    return row.reply_text if row else None


def _pick(pool: list[str], review: GoogleReview, avoid: str | None = None) -> str:
    """Pick a template, filled with the reviewer's name, avoiding an exact repeat of `avoid`."""
    suffix = _name_suffix(review)
    rendered = [t.format(name=suffix) for t in pool]
    choices = [r for r in rendered if r != avoid] or rendered
    return random.choice(choices)


# --- Optional Claude LLM generation ---------------------------------------------------

_ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
_ANTHROPIC_MODEL = "claude-opus-4-8"

_TONE_SYSTEM = (
    "You write short, warm public replies to Google reviews on behalf of Hotel Bhimas, an "
    "independent hotel in India. Rules you must always follow:\n"
    "- Keep it to 2-4 sentences, friendly and human, never robotic or copy-pasted.\n"
    "- Address the reviewer by first name only if one is given; otherwise a warm generic greeting.\n"
    "- NEVER mention any personal, stay, room, or booking details — you only know the review text.\n"
    "- For low ratings: apologize sincerely, say the team is looking into it, and invite the guest "
    "to contact the hotel directly (front desk / phone / WhatsApp). No excuses, no arguing, and do "
    "NOT admit specific fault or blame.\n"
    "- For high ratings: thank them warmly and invite them back.\n"
    "- Output only the reply text, nothing else."
)


def _llm_configured() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY"))


def generate_llm_reply(review: GoogleReview, band: str) -> str | None:
    """Ask Claude for a review-specific reply within the tone rules. Returns None on any failure so
    the caller falls back to the template pool. `band` is 'thanks' | 'low' | 'mid' (context only)."""
    import requests
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        return None
    fn = _first_name(review) or "(no name given)"
    user = (
        f"Reviewer first name: {fn}\n"
        f"Star rating: {review.rating}/5\n"
        f"Review text: {review.review_text or '(no text)'}\n\n"
        f"Write the public reply now."
    )
    try:
        resp = requests.post(_ANTHROPIC_URL, json={
            "model": _ANTHROPIC_MODEL,
            "max_tokens": 400,
            "system": _TONE_SYSTEM,
            "messages": [{"role": "user", "content": user}],
        }, headers={
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }, timeout=30)
        if resp.status_code != 200:
            logger.error(f"❌ review LLM failed {resp.status_code}: {resp.text[:250]}")
            return None
        data = resp.json()
        # First text block of the response content.
        for block in data.get("content", []) or []:
            if block.get("type") == "text" and block.get("text", "").strip():
                return block["text"].strip()
        return None
    except Exception as e:
        logger.error(f"❌ review LLM error: {e}")
        return None


# --- Public API -----------------------------------------------------------------------

def generate_reply(db, review: GoogleReview) -> tuple[str, str, bool]:
    """Return (reply_text, reply_mode, needs_approval) for a review.

    reply_mode: 'auto' (thank-you, auto-post after delay) | 'approved' (draft -> approval queue).
    needs_approval: True routes the row to the pending_approval queue instead of auto-posting."""
    cfg = app_settings.get_review_config(db)
    threshold = cfg["auto_reply_threshold"]
    llm_on = cfg["llm_enabled"] and _llm_configured()

    if review.rating >= threshold and not _looks_negative_despite_rating(review):
        # High rating -> varied thank-you, auto-post.
        text = None
        if llm_on:
            text = generate_llm_reply(review, "thanks")
        if not text:
            text = _pick(THANKYOU_TEMPLATES, review, avoid=_last_auto_reply_text(db))
        return text, "auto", False

    if review.rating >= threshold:
        # High rating but the text reads negative — thank-you-ish draft, but let a human check it.
        text = None
        if llm_on:
            text = generate_llm_reply(review, "thanks")
        if not text:
            text = _pick(THANKYOU_TEMPLATES, review)
        return text, "approved", True

    # Low rating -> service-recovery draft. Band split (default: 1-2 vs 3).
    band = "low" if review.rating <= cfg["low_band_split"] else "mid"
    pool = RECOVERY_TEMPLATES_LOW if band == "low" else RECOVERY_TEMPLATES_MID
    text = None
    if llm_on:
        text = generate_llm_reply(review, band)
    if not text:
        text = _pick(pool, review)
    # Approval-gated unless the owner has explicitly opted into auto-sending low-rating drafts.
    needs_approval = not cfg["low_auto_send"]
    return text, "approved", needs_approval


def send_low_review_alert(db, review: GoogleReview):
    """Alert the owner about a <threshold review — WhatsApp, falling back to their email
    (FE-11). Best-effort + idempotent via client_ref; respects the wa_owner_alerts +
    review_owner_alerts toggles. Never raises."""
    try:
        from utils import whatsapp_service as wa
        from services import notify as notify_service
        cfg = app_settings.get_review_config(db)
        if not cfg["owner_alerts_enabled"]:
            return None
        if wa.already_sent(db, f"review_alert:{review.id}"):
            return None
        snippet = (review.review_text or "").strip().replace("\n", " ")
        if len(snippet) > 160:
            snippet = snippet[:157] + "..."
        return notify_service.notify_owner(
            db, template="review_alert",
            params={"author_name": review.author_name or "A guest",
                    "rating": str(review.rating),
                    "snippet": snippet or "(no text)"},
            client_ref=f"review_alert:{review.id}")
    except Exception as e:
        logger.error(f"❌ review owner alert failed: {e}")
        return None
