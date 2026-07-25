"""Google Business Profile (GBP) API client — pluggable, off by default (prompt 20).

Talks to the Google Business Profile API (`reviews.list` + `reviews.reply`) to fetch the hotel's
Google reviews and post our replies. Follows the same pluggable idiom as the WhatsApp (Meta),
e-invoicing, OTA-IMAP and R2/OCR integrations: real API calls happen ONLY when the `GBP_*` OAuth
env is configured; otherwise `list_reviews` returns `[]` and `post_reply` stub-logs and returns
success, so the whole review pipeline (generate -> queue -> approve -> post -> audit) is verifiable
offline via the admin "inject test review" path.

Uses the stdlib `requests` library already vendored in the backend (no httpx). Never raises out of
`list_reviews`/`post_reply` — the scheduler must keep running.

Required ENV for live mode (all must be set):
  GBP_CLIENT_ID, GBP_CLIENT_SECRET, GBP_REFRESH_TOKEN, GBP_ACCOUNT_ID, GBP_LOCATION_ID

Follow-ups at go-live: GBP API access approval (form-based, days of lead time), OAuth as the
profile owner/manager, and optionally Pub/Sub notifications to replace polling.
"""
import logging
import os
from datetime import datetime

import requests

logger = logging.getLogger(__name__)

_OAUTH_URL = "https://oauth2.googleapis.com/token"
_API_BASE = "https://mybusiness.googleapis.com/v4"
_TIMEOUT = 20

# Google returns the star rating as an enum string, not a number.
_STAR_MAP = {"ONE": 1, "TWO": 2, "THREE": 3, "FOUR": 4, "FIVE": 5}


def _env() -> dict:
    return {
        "client_id": os.getenv("GBP_CLIENT_ID"),
        "client_secret": os.getenv("GBP_CLIENT_SECRET"),
        "refresh_token": os.getenv("GBP_REFRESH_TOKEN"),
        "account_id": os.getenv("GBP_ACCOUNT_ID"),
        "location_id": os.getenv("GBP_LOCATION_ID"),
    }


def is_configured() -> bool:
    """True only when every GBP_* credential is present (live mode)."""
    return all(_env().values())


def provider_status() -> dict:
    """Read-only status surfaced to the admin config screen (never leaks secret values)."""
    e = _env()
    return {
        "configured": is_configured(),
        "account_set": bool(e["account_id"]),
        "location_set": bool(e["location_id"]),
    }


def _access_token(cfg: dict) -> str | None:
    """Exchange the long-lived refresh token for a short-lived access token."""
    try:
        resp = requests.post(_OAUTH_URL, data={
            "client_id": cfg["client_id"],
            "client_secret": cfg["client_secret"],
            "refresh_token": cfg["refresh_token"],
            "grant_type": "refresh_token",
        }, timeout=_TIMEOUT)
        if resp.status_code == 200:
            return resp.json().get("access_token")
        logger.error(f"❌ GBP token refresh failed {resp.status_code}: {resp.text[:250]}")
    except Exception as e:
        logger.error(f"❌ GBP token refresh error: {e}")
    return None


def _parse_created_at(s) -> datetime | None:
    if not s:
        return None
    try:
        # Google uses RFC3339, e.g. "2026-07-20T10:15:30.123Z"
        return datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=None)
    except (ValueError, AttributeError):
        return None


def _normalize(review: dict) -> dict | None:
    """Map a raw GBP review payload to our internal shape. Returns None if unusable."""
    rid = review.get("reviewId") or review.get("name")
    if not rid:
        return None
    rating = _STAR_MAP.get(str(review.get("starRating", "")).upper())
    if rating is None:
        return None
    reviewer = (review.get("reviewer") or {}).get("displayName")
    return {
        "gbp_review_id": str(rid),
        "rating": rating,
        "author_name": reviewer,
        "review_text": review.get("comment"),
        "review_created_at": _parse_created_at(review.get("createTime")),
    }


def list_reviews(db=None) -> list[dict]:
    """Fetch reviews from GBP (paginated). Returns a list of normalized dicts, or [] when the
    integration is not configured or the call fails. `db` is accepted for signature parity with the
    other pluggable services but is unused here."""
    if not is_configured():
        return []
    cfg = _env()
    token = _access_token(cfg)
    if not token:
        return []
    headers = {"Authorization": f"Bearer {token}"}
    base = f"{_API_BASE}/accounts/{cfg['account_id']}/locations/{cfg['location_id']}/reviews"
    out: list[dict] = []
    page_token = None
    try:
        for _ in range(20):  # hard page cap (safety)
            params = {"pageSize": 50}
            if page_token:
                params["pageToken"] = page_token
            resp = requests.get(base, headers=headers, params=params, timeout=_TIMEOUT)
            if resp.status_code != 200:
                logger.error(f"❌ GBP reviews.list failed {resp.status_code}: {resp.text[:250]}")
                break
            data = resp.json()
            for raw in data.get("reviews", []) or []:
                norm = _normalize(raw)
                if norm:
                    out.append(norm)
            page_token = data.get("nextPageToken")
            if not page_token:
                break
    except Exception as e:
        logger.error(f"❌ GBP reviews.list error: {e}")
    logger.info(f"GBP list_reviews fetched {len(out)} review(s)")
    return out


def post_reply(db, gbp_review_id: str, text: str) -> tuple[bool, str | None]:
    """Post our reply to a GBP review. Returns (ok, error). When the integration is not configured,
    stub-logs and returns success so the offline pipeline can be exercised end-to-end. Never raises."""
    if not is_configured():
        logger.info(f"📝 [GBP stub] would reply to review {gbp_review_id}: {text[:120]!r}")
        return True, None
    cfg = _env()
    token = _access_token(cfg)
    if not token:
        return False, "gbp auth failed (token refresh)"
    url = (f"{_API_BASE}/accounts/{cfg['account_id']}/locations/{cfg['location_id']}"
           f"/reviews/{gbp_review_id}/reply")
    try:
        resp = requests.put(url, json={"comment": text},
                            headers={"Authorization": f"Bearer {token}",
                                     "Content-Type": "application/json"}, timeout=_TIMEOUT)
        if resp.status_code == 200:
            return True, None
        err = f"http {resp.status_code}: {resp.text[:250]}"
        logger.error(f"❌ GBP reviews.reply failed {err}")
        return False, err
    except Exception as e:
        err = f"request error: {str(e)[:250]}"
        logger.error(f"❌ GBP reviews.reply error: {e}")
        return False, err
