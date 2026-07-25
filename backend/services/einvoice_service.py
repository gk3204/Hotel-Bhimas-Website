"""GST e-invoicing / IRN generation (prompt 18, slice 2) — pluggable, stub-by-default.

Mirrors the codebase's pluggable-service idiom (whatsapp_service / ocr_service / storage): the real
IRP/GSP HTTP call runs ONLY when the `GST_EINVOICE_*` env is configured; otherwise a deterministic
**stub IRN + signed-QR payload** is produced so the whole data flow (payload build -> persist EInvoice
-> QR on the invoice PDF) is exercisable now and go-live is just adding credentials.

Public API:
  provider_status()               -> {"configured": bool, "mode": "live"|"stub"}
  build_payload(invoice_data)     -> dict (the IRN request body; also stored for audit)
  generate_irn(invoice_data)      -> {"status","irn","ack_no","ack_date","signed_qr",
                                      "request_payload","response_payload","error"}

`invoice_data` is the same dict the invoice PDF is built from (see routers/folio.py), plus a
`seller_gstin` and optional `buyer` block. Nothing here raises on the stub path.
"""
import hashlib
import json
import logging
import os
from datetime import datetime

logger = logging.getLogger(__name__)

try:
    import requests  # already a dependency (see requirements.txt)
    _HAS_REQUESTS = True
except Exception:  # pragma: no cover
    requests = None
    _HAS_REQUESTS = False


def _enabled() -> bool:
    """Live mode requires the master flag + a base URL + the seller GSTIN (creds vary by GSP)."""
    return (
        os.getenv("GST_EINVOICE_ENABLED", "").strip().lower() in ("1", "true", "yes")
        and bool(os.getenv("GST_EINVOICE_BASE_URL"))
        and bool(os.getenv("GST_EINVOICE_GSTIN"))
        and _HAS_REQUESTS
    )


def provider_status() -> dict:
    return {"configured": _enabled(), "mode": "live" if _enabled() else "stub"}


def build_payload(invoice_data: dict) -> dict:
    """Assemble the IRP invoice request body from our invoice snapshot. Kept close to the GST
    e-invoice schema (a GSP adapts it to its own wrapper). Amounts are in ₹."""
    seller_gstin = (invoice_data.get("seller_gstin")
                    or os.getenv("GST_EINVOICE_GSTIN", "")).strip()
    buyer = invoice_data.get("buyer") or {}
    items = []
    for i, line in enumerate(invoice_data.get("gst_breakup") or [], start=1):
        items.append({
            "SlNo": str(i),
            "IsServc": "Y",
            "GstRt": float(line.get("gst_percent", 0) or 0),
            "TotAmt": float(line.get("taxable", 0) or 0),
            "CgstAmt": float(line.get("cgst", 0) or 0),
            "SgstAmt": float(line.get("sgst", 0) or 0),
            "TotItemVal": float(line.get("total", 0) or 0),
        })
    return {
        "Version": "1.1",
        "TranDtls": {"TaxSch": "GST", "SupTyp": "B2B"},
        "DocDtls": {
            "Typ": "INV",
            "No": invoice_data.get("invoice_no", ""),
            "Dt": invoice_data.get("invoice_date", ""),
        },
        "SellerDtls": {"Gstin": seller_gstin},
        "BuyerDtls": {
            "Gstin": (buyer.get("gstin") or "URP"),
            "LglNm": buyer.get("name") or invoice_data.get("guest_name") or "",
        },
        "ItemList": items,
        "ValDtls": {
            "AssVal": float(invoice_data.get("taxable_total", 0) or 0),
            "CgstVal": float(invoice_data.get("cgst_total", 0) or 0),
            "SgstVal": float(invoice_data.get("sgst_total", 0) or 0),
            "TotInvVal": float(invoice_data.get("grand_total", 0) or 0),
        },
    }


def _stub_irn(invoice_no: str) -> str:
    """Deterministic 64-char pseudo-IRN so the same invoice always maps to the same value."""
    digest = hashlib.sha256(f"{invoice_no}|hotelbhimas-stub".encode()).hexdigest()
    return (digest + digest)[:64]


def generate_irn(invoice_data: dict) -> dict:
    """Return an IRN result. Live IRP call when configured; deterministic stub otherwise.
    Never raises on the stub path; live-path exceptions are captured as status='failed'."""
    payload = build_payload(invoice_data)
    invoice_no = invoice_data.get("invoice_no", "")

    if not _enabled():
        irn = _stub_irn(invoice_no)
        now = datetime.utcnow()
        signed_qr = json.dumps({
            "SellerGstin": payload["SellerDtls"]["Gstin"],
            "BuyerGstin": payload["BuyerDtls"]["Gstin"],
            "DocNo": invoice_no,
            "DocDt": invoice_data.get("invoice_date", ""),
            "TotInvVal": payload["ValDtls"]["TotInvVal"],
            "Irn": irn,
            "Mode": "STUB",
        })
        logger.info("e-invoice stub IRN generated for %s (set GST_EINVOICE_* to go live)", invoice_no)
        return {
            "status": "stub",
            "irn": irn,
            "ack_no": f"STUB{now.strftime('%y%m%d%H%M%S')}",
            "ack_date": now.strftime("%Y-%m-%d %H:%M:%S"),
            "signed_qr": signed_qr,
            "request_payload": json.dumps(payload),
            "response_payload": None,
            "error": None,
        }

    # --- live IRP/GSP call ---
    base = os.getenv("GST_EINVOICE_BASE_URL", "").rstrip("/")
    headers = {"Content-Type": "application/json"}
    api_key = os.getenv("GST_EINVOICE_API_KEY")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    user = os.getenv("GST_EINVOICE_USERNAME")
    pwd = os.getenv("GST_EINVOICE_PASSWORD")
    if user:
        headers["username"] = user
    if pwd:
        headers["password"] = pwd
    try:
        resp = requests.post(f"{base}/invoice", json=payload, headers=headers, timeout=20)
        body = resp.json() if resp.content else {}
        if resp.status_code >= 400 or not (body.get("Irn") or body.get("irn")):
            err = body.get("error") or body.get("ErrorDetails") or f"HTTP {resp.status_code}"
            logger.error("e-invoice IRP call failed for %s: %s", invoice_no, err)
            return {"status": "failed", "irn": None, "ack_no": None, "ack_date": None,
                    "signed_qr": None, "request_payload": json.dumps(payload),
                    "response_payload": json.dumps(body), "error": str(err)[:300]}
        return {
            "status": "generated",
            "irn": body.get("Irn") or body.get("irn"),
            "ack_no": str(body.get("AckNo") or body.get("ack_no") or ""),
            "ack_date": str(body.get("AckDt") or body.get("ack_date") or ""),
            "signed_qr": body.get("SignedQRCode") or body.get("signed_qr") or "",
            "request_payload": json.dumps(payload),
            "response_payload": json.dumps(body),
            "error": None,
        }
    except Exception as exc:  # pragma: no cover - network path
        logger.exception("e-invoice IRP call raised for %s", invoice_no)
        return {"status": "failed", "irn": None, "ack_no": None, "ack_date": None,
                "signed_qr": None, "request_payload": json.dumps(payload),
                "response_payload": None, "error": str(exc)[:300]}
