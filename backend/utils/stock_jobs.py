"""Low-stock alerts (prompt 18c, slice 8).

Pure functions over a `db` session, the same shape as `utils/vendor_jobs.py`. The sweep is wired
into the EXISTING WhatsApp scheduler (`run_whatsapp_jobs`) rather than starting another APScheduler
— one cadence, one place to disable, one log line.

Idempotency: `stock_items.low_stock_alerted_at` is stamped when an alert goes out, so an item is
chased once per low-stock EPISODE, not once per sweep. `record_movement` clears it when a receive
lifts the item back above its reorder point, which re-arms the alert for the next episode.

Delivery rides `utils/whatsapp_service` — pluggable-off by default, so this is fully verifiable
before the owner's WhatsApp number is live.
"""
import logging
from datetime import datetime

from models import StockItem
from utils import settings as app_settings
from utils import whatsapp_service as wa

logger = logging.getLogger(__name__)


def send_low_stock_alerts(db) -> int:
    """WhatsApp the owner about active items at/below their reorder point that haven't been
    chased for the current episode. Returns the number of items alerted."""
    from routers.stock import is_low            # reuse the exact low-stock rule

    cfg = app_settings.get_stock_config(db)
    if not cfg["low_stock_alerts_enabled"]:
        return 0

    owner = wa.owner_number(db)
    if not owner:
        return 0

    items = db.query(StockItem).filter(StockItem.is_active == True).all()  # noqa: E712
    low = [it for it in items if is_low(db, it, cfg) and it.low_stock_alerted_at is None]
    if not low:
        return 0

    sent = 0
    for it in low:
        row = wa.send_template(
            db, owner, "low_stock_alert",
            {
                "item_name": it.name,
                "current_qty": str(float(it.current_qty or 0)),
                "unit": it.unit,
                "threshold": str(round(
                    it.reorder_threshold if float(it.reorder_threshold or 0) > 0
                    else cfg["low_stock_default_threshold"], 2)),
            },
            client_ref=f"low_stock:{it.id}:{it.low_stock_alerted_at}",
            respect_optout=False,          # operational alert to the owner, not marketing
        )
        if row is not None:
            it.low_stock_alerted_at = datetime.utcnow()
            sent += 1

    if sent:
        db.commit()
    return sent


def run_low_stock_sweep(db) -> dict:
    """Full inventory sweep. Isolated per step so one failing never blocks the rest (matches
    run_whatsapp_jobs / run_vendor_renewal_sweep)."""
    result = {}
    for name, fn in (("low_stock_alerts", send_low_stock_alerts),):
        try:
            result[name] = fn(db)
        except Exception as e:
            logger.error(f"stock_jobs: {name} failed: {e}")
            result[name] = f"error: {e}"
    logger.info(f"stock_jobs sweep: {result}")
    return result
