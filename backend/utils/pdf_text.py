"""Plain text out of a PDF attachment (P2a).

Yatra's YES Bank payment advice and its per-booking commission invoices / credit notes exist only as
PDFs. pypdf (pure Python, pinned in requirements.txt) extracts their text; the advice comes out with
labels and values in separate runs ("Booking ID : … YATMB0012288247 …"), so callers match VALUES by
pattern, never by the label that should precede them. Whitespace is collapsed to single spaces.
"""
import io
import logging
import re

logger = logging.getLogger(__name__)


def pdf_text(data: bytes, max_pages: int = 10) -> str:
    """All text of the first `max_pages` pages, single-spaced. "" when the PDF cannot be read."""
    if not data or not data[:5].startswith(b"%PDF"):
        return ""
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception:
                return ""
        parts = [(p.extract_text() or "") for p in reader.pages[:max_pages]]
    except Exception as e:      # a damaged attachment must never stop the mail poll
        logger.warning("pdf_text: could not read PDF (%s)", e)
        return ""
    return re.sub(r"\s+", " ", " ".join(parts)).strip()
