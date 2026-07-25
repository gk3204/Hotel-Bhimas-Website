"""Pluggable ID OCR (prompt 14) — OFF by default.

On an ID upload (Aadhaar / passport / driving licence) the desk can OCR the document to
pre-populate name / number / DOB, then staff confirms. No OCR provider is wired in this
build (it needs an API key), so this is a clean seam:

  * When OCR_PROVIDER + OCR_API_KEY are set, `extract_id()` calls the provider.
  * Otherwise it returns {"configured": False} and the desk types the fields manually.

The returned ID number is masked (raw is never persisted). This mirrors the WhatsApp-stub
convention used elsewhere: one call site, swap the provider later without touching callers.
"""
import os
import logging

logger = logging.getLogger(__name__)


def is_configured() -> bool:
    return bool(os.getenv("OCR_PROVIDER") and os.getenv("OCR_API_KEY"))


def _mask(number: str | None) -> str | None:
    if not number:
        return None
    digits = "".join(ch for ch in number if ch.isalnum())
    return ("****" + digits[-4:]) if len(digits) >= 4 else "****"


def extract_id(image_bytes: bytes, id_type: str | None = None) -> dict:
    """Best-effort ID field extraction. Returns:
        {configured: bool, name: str|None, id_number_masked: str|None, dob: str|None, raw: dict}
    Never raises — a provider error degrades to configured=False so the desk can proceed."""
    if not is_configured():
        return {"configured": False, "name": None, "id_number_masked": None,
                "dob": None, "note": "OCR not configured — staff enters details manually."}

    provider = os.getenv("OCR_PROVIDER", "").strip().lower()
    try:
        # Provider integrations plug in here. Kept as an explicit dispatch so adding a
        # real provider (e.g. a cloud KYC/OCR API) is a localized change.
        result = _dispatch(provider, image_bytes, id_type)
        return {
            "configured": True,
            "name": result.get("name"),
            "id_number_masked": _mask(result.get("id_number")),
            "dob": result.get("dob"),
            "raw": {k: v for k, v in result.items() if k != "id_number"},  # never echo the raw number
        }
    except Exception as e:
        logger.error(f"[ocr] provider '{provider}' failed: {e}", exc_info=True)
        return {"configured": False, "name": None, "id_number_masked": None,
                "dob": None, "note": "OCR provider error — staff enters details manually."}


def _dispatch(provider: str, image_bytes: bytes, id_type: str | None) -> dict:
    """Route to a configured provider. No provider is bundled yet; wiring one is a
    single-function change here. Raises NotImplementedError for an unknown provider so
    extract_id() degrades gracefully to manual entry."""
    raise NotImplementedError(f"OCR provider '{provider}' is not integrated in this build")
