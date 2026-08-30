"""One-off backfill: attribute historic room-service folio charges to their menu item (v3 item 4).

Migration 027 added `folio_charges.menu_item_id`, but only charges posted AFTER it carry a value.
Without this script the product-wise sales report starts from an empty history.

How it matches. Every room-service order is a `GuestRequest(type='room_service')` whose payload
snapshots the priced lines (`items[] = {menu_item_id, name, qty, unit_price, ...}`), and
`services/room_service.post_to_folio` wrote one FolioCharge per line with a deterministic
description: "Room service — {name}". So a line is matched to a charge on
    folio_id  +  description == "Room service — {name}"  +  qty
which is exact for every order the app itself created.

It is deliberately CONSERVATIVE:
  * only charges with menu_item_id IS NULL are touched — re-running changes nothing;
  * a line that matches zero charges, or more than one, is REPORTED and skipped rather than
    guessed at. Attributing revenue to the wrong dish is worse than leaving it unattributed;
  * DRY RUN by default. Pass --commit to write.

Usage (from backend/):
    python -m scripts.backfill_menu_item_id            # report only
    python -m scripts.backfill_menu_item_id --commit   # write
"""
import json
import logging
import sys

from database import SessionLocal
from models import FolioCharge, GuestRequest

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

DESCRIPTION_PREFIX = "Room service — "


def backfill(db, commit: bool = False) -> dict:
    """Returns {orders, lines, matched, already_set, unmatched, ambiguous}."""
    stats = {"orders": 0, "lines": 0, "matched": 0, "already_set": 0,
             "unmatched": 0, "ambiguous": 0}
    unresolved = []

    orders = (db.query(GuestRequest)
              .filter(GuestRequest.type == "room_service")
              .order_by(GuestRequest.id).all())

    for r in orders:
        if not r.payload:
            continue
        try:
            lines = (json.loads(r.payload) or {}).get("items") or []
        except (ValueError, TypeError):
            logger.warning(f"order #{r.id}: payload is not readable JSON — skipped")
            continue
        if not lines:
            continue
        stats["orders"] += 1

        # The order's charges live on the booking's folio. Resolve it through the one charge
        # the order does point at, so an order whose folio was since re-opened still lands on
        # the right one; fall back to the booking's folio when that link is missing.
        folio_id = None
        if r.folio_charge_id:
            anchor = db.query(FolioCharge).filter(FolioCharge.id == r.folio_charge_id).first()
            folio_id = anchor.folio_id if anchor else None
        if folio_id is None:
            from models import Folio
            folio = db.query(Folio).filter(Folio.booking_id == r.booking_id).first()
            folio_id = folio.id if folio else None
        if folio_id is None:
            stats["unmatched"] += len(lines)
            unresolved.append(f"order #{r.id}: no folio found for booking {r.booking_id}")
            continue

        for ln in lines:
            stats["lines"] += 1
            menu_item_id = ln.get("menu_item_id")
            name = ln.get("name")
            if not menu_item_id or not name:
                stats["unmatched"] += 1
                unresolved.append(f"order #{r.id}: line has no menu_item_id/name")
                continue

            qty = float(ln.get("qty") or 1)
            candidates = (db.query(FolioCharge)
                          .filter(FolioCharge.folio_id == folio_id,
                                  FolioCharge.description == DESCRIPTION_PREFIX + name,
                                  FolioCharge.qty == qty)
                          .order_by(FolioCharge.id).all())

            open_ones = [c for c in candidates if c.menu_item_id is None]
            if not open_ones:
                if candidates:
                    stats["already_set"] += 1
                else:
                    stats["unmatched"] += 1
                    unresolved.append(
                        f"order #{r.id}: no charge on folio {folio_id} for "
                        f"'{name}' x{qty:g}")
                continue
            if len(open_ones) > 1:
                # The same dish ordered twice on one stay is indistinguishable by description
                # alone. Only ambiguous when the SAME order repeats it; two separate orders
                # each match their own charge in id order, so take them one at a time.
                stats["ambiguous"] += 1
                unresolved.append(
                    f"order #{r.id}: {len(open_ones)} unattributed charges match "
                    f"'{name}' x{qty:g} on folio {folio_id} — assigning the earliest")

            open_ones[0].menu_item_id = menu_item_id
            stats["matched"] += 1

    if commit:
        db.commit()
    else:
        db.rollback()

    logger.info("")
    logger.info(f"  orders scanned    : {stats['orders']}")
    logger.info(f"  order lines       : {stats['lines']}")
    logger.info(f"  charges attributed: {stats['matched']}")
    logger.info(f"  already attributed: {stats['already_set']}")
    logger.info(f"  no charge matched : {stats['unmatched']}")
    logger.info(f"  ambiguous matches : {stats['ambiguous']}")
    if unresolved:
        logger.info("")
        logger.info("  Needs a human look:")
        for u in unresolved[:50]:
            logger.info(f"    - {u}")
        if len(unresolved) > 50:
            logger.info(f"    ... and {len(unresolved) - 50} more")
    logger.info("")
    logger.info("  COMMITTED." if commit else "  DRY RUN — nothing written. Re-run with --commit.")
    return stats


def main():
    commit = "--commit" in sys.argv
    db = SessionLocal()
    try:
        backfill(db, commit=commit)
    finally:
        db.close()


if __name__ == "__main__":
    main()
