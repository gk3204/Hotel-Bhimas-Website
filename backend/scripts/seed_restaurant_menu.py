"""One-off seed: load the restaurant's printed menu (7 photographed pages) into the room-service
menu (`menu_items`) at the printed price PLUS the owner's room-service markup (default Rs 5).

Pricing rule. The printed menu says "GST 5% EXTRA", and the PMS stores menu prices EX-GST
(services/room_service.py — GST is added per line at billing). So each item is stored as
    price = printed price + MARKUP      (ex-GST)
    gst_percent = 5
which bills the guest exactly printed+markup, plus 5% GST on top, same as the restaurant.

Categories. `menu_items.category` must be one of the configured menu categories (default
food | beverage | snack | service). The printed menu's own section ("Tandoori Starter", "Bread",
"Juice"...) is kept in `description` so items stay grouped and findable in the admin, and
`sort_order` follows the printed order so the guest portal reads like the physical menu.

"Choice of X with (Gobi / Paneer / Mushroom)" lines are EXPANDED into one item per option
(e.g. "Gobi Manchurian", "Paneer Manchurian" ...) because a guest ordering from the portal has
no way to state a choice otherwise. Obvious print typos are corrected (Tamoto -> Tomato,
Rosted -> Roasted, Chinees -> Chinese ...); regional spellings (Dall, Kurma, Parota...) are kept.

It is deliberately SAFE to re-run:
  * an item whose name already exists (case-insensitive) is SKIPPED, never overwritten —
    pass --update-existing to overwrite price/gst/description/sort_order on those instead;
  * DRY RUN by default. Pass --commit to write.

Usage (from backend/):
    python -m scripts.seed_restaurant_menu                      # report only
    python -m scripts.seed_restaurant_menu --commit             # insert
    python -m scripts.seed_restaurant_menu --commit --update-existing
    python -m scripts.seed_restaurant_menu --markup 5 --gst 5   # (defaults)

Targets whatever database.py resolves (local .env). To seed the Railway database run it with
DATABASE_URL set to the Railway DATABASE_PUBLIC_URL for that one command.
"""
import argparse
import logging
import sys
from decimal import Decimal

from database import SessionLocal
from models import MenuItem
from utils.settings import get_category_list

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

DEFAULT_MARKUP = 5          # rupees added on top of every printed price (owner's instruction)
DEFAULT_GST = 5.0           # "GST 5% EXTRA" on the printed menu; PMS prices are ex-GST

# ---------------------------------------------------------------------------------------------
# The printed menu, page by page, in printed order: (section, category, [(name, printed_price)])
# "Choice of ..." lines are written out per option so the list is exactly what gets inserted.
# ---------------------------------------------------------------------------------------------
MENU = [
    # ---- page: TIFFINS ----
    ("Tiffins", "food", [
        ("Idly", 38), ("Sambar Idly", 55), ("Vada", 45), ("Sambar Vada", 57), ("Pongal", 50),
        ("Upma", 50), ("Poori", 59), ("Plain Dosa", 65), ("Masala Dosa", 70), ("Onion Dosa", 72),
        ("Onion Uthappam", 72), ("Rava Masala Dosa", 75), ("Onion Rava Dosa", 80),
        ("Onion Rava Masala Dosa", 92), ("Ghee Dosa", 85), ("Ghee Onion Dosa", 90),
        ("Ghee Rava Dosa", 85), ("Ghee Onion Masala Dosa", 95), ("Ghee Onion Rava Dosa", 90),
        ("Ghee Rava Masala Dosa", 95), ("Ghee Karam Dosa", 90), ("Ghee Karam Masala Dosa", 95),
        ("Ghee Onion Karam Masala Dosa", 100),
        ("Ghee Masala Dosa", 90), ("Butter Dosa", 85), ("Butter Rava Dosa", 85),
        ("Butter Onion Dosa", 90), ("Butter Masala Dosa", 90), ("Butter Onion Rava Dosa", 90),
        ("Rava Dosa", 72), ("Karam Dosa", 75), ("Karam Masala Dosa", 85), ("Garlic Dosa", 80),
        ("Garlic Rava Dosa", 80), ("Cashewnut Rava Dosa", 100), ("Pesarattu", 90),
        ("Tomato Uthappam", 90), ("Paper Roast", 100), ("Paper Masala", 115), ("Curd Vada", 60),
        ("MLA Pesarattu", 95), ("Set Dosa", 80), ("Open Masala Dosa", 75), ("Parota", 70),
        ("Channa Batura", 75),
    ]),
    # ---- page: SPL DOSA ITEMS / NORTH INDIAN RICE / KHICHDI / NOODLES ----
    ("Special Dosa Items", "food", [
        ("Kaju Dosa", 95), ("Mushroom Dosa", 95), ("Paneer Dosa", 90), ("Bread Dosa", 85),
        ("Green Peas Dosa", 85), ("Gobi Dosa", 90), ("Palak Dosa", 85), ("Cheese Dosa", 90),
        ("Veg Dosa", 85), ("Veg Samosa", 34), ("Veg Cutlet", 34), ("Chilli Idly", 75),
        ("Navarathna Dosa", 90),
    ]),
    ("North Indian Rice Dishes", "food", [
        ("Jeera Rice", 140), ("Ghee Rice", 145), ("Veg Pulav", 130), ("Baby Corn Pulav", 155),
        ("Baby Corn Tomato Pulav", 160), ("Kashmiri Pulav", 160), ("Sweet Corn Pulav", 160),
        ("Veg Biryani", 130), ("Mushroom Biryani", 155), ("Gobi Biryani", 155),
        ("Paneer Biryani", 165), ("Kaju Biryani", 165), ("Baby Corn Biryani", 165),
        ("Dall Khichdi", 165), ("Dall Tadaka Khichdi", 170), ("Steam Rice", 110), ("Curd Rice", 70),
    ]),
    ("Noodles", "food", [
        ("Veg Noodles", 135),
        # Choice of Noodles with (Gobi / Paneer / Mushroom) - 160
        ("Gobi Noodles", 160), ("Paneer Noodles", 160), ("Mushroom Noodles", 160),
        # Choice of Schezwan Noodles with (Paneer / Mushroom / Baby Corn) - 175
        ("Schezwan Paneer Noodles", 175), ("Schezwan Mushroom Noodles", 175),
        ("Schezwan Babycorn Noodles", 175),
        ("American Chopsuey", 165),
        # Choice of Gravy Noodles with (Paneer / Mushroom / Babycorn) - 170
        ("Paneer Gravy Noodles", 170), ("Mushroom Gravy Noodles", 170),
        ("Babycorn Gravy Noodles", 170),
        # Choice of Fried Noodles with (Gobi / Mushroom / Paneer / Babycorn) - 170
        ("Gobi Fried Noodles", 170), ("Mushroom Fried Noodles", 170),
        ("Paneer Fried Noodles", 170), ("Babycorn Fried Noodles", 170),
    ]),
    # ---- page: INDIAN STARTER / CHINESE STARTER ----
    ("Indian Starter", "food", [
        # Choice of Pakoda with (Gobi / Paneer / Mushroom) - 185
        ("Gobi Pakoda", 185), ("Paneer Pakoda", 185), ("Mushroom Pakoda", 185),
        # Choice of 65 (Gobi / Paneer / Mushroom / Babycorn) - 180
        ("Gobi 65", 180), ("Paneer 65", 180), ("Mushroom 65", 180), ("Babycorn 65", 180),
        ("Alu Mutter Fry", 145), ("Mushroom Kalimirchi", 185), ("Mushroom Malakad", 185),
        ("Paneer Majestic", 185), ("Babycorn Majestic", 185), ("Paneer Burji", 185),
        ("Pepper Mushroom Fry", 165),
    ]),
    ("Chinese Starter", "food", [
        # Choice of Manchurian with (Gobi / Paneer / Mushroom / Babycorn) - 165
        ("Gobi Manchurian", 165), ("Paneer Manchurian", 165), ("Mushroom Manchurian", 165),
        ("Babycorn Manchurian", 165),
        ("Chilli Aloo", 140), ("Chilli Gobi", 140), ("Chilli Babycorn", 150),
        ("Chilli Mushroom", 150), ("Chilli Paneer", 150), ("Chilli Parota", 90),
        ("Mongolian Paneer", 175), ("Garlic Paneer", 175), ("Gobi Lollypop", 175),
        ("Spring Roll", 145),
        # Choice of Dragon with (Babycorn / Mushroom / Paneer) - 195
        ("Dragon Babycorn", 195), ("Dragon Mushroom", 195), ("Dragon Paneer", 195),
        # Choice of Honey with (Paneer / Baby Corn / Mushroom) - 175
        ("Honey Paneer", 175), ("Honey Babycorn", 175), ("Honey Mushroom", 175),
        ("Chilli Potato", 160), ("Lemon Honey Paneer", 205), ("Hongkong Paneer", 175),
        ("Shanghai Paneer", 175), ("French Fries", 65),
    ]),
    # ---- page: TANDOORI STARTER / BREAD ----
    ("Tandoori Starter", "food", [
        ("Paneer Tikka", 205), ("Mushroom Tikka", 205), ("Stuffed Mushroom Tikka", 250),
        ("Aloo Tikka", 165), ("Tandoori Aloo", 155), ("Broccoli Tikka", 160),
        ("Hara Bhara Kabab", 165), ("Paneer Pudina Tikka", 185), ("Paneer Angari", 185),
        ("Rashmi Paneer Tikka", 205), ("Veg Tandoori Momos", 125), ("Veg Seek Kabab", 180),
        ("Tandoori Veg Roll", 90), ("Tandoori Gobi", 150), ("Tandoori Babycorn", 150),
        ("Roasted Papad", 27), ("Masala Papad", 45), ("Achari Paneer Tikka", 185),
        ("Tandoori Starters Platter", 320),
    ]),
    ("Bread", "food", [
        ("Roti", 27), ("Naan", 29), ("Butter Roti", 32), ("Butter Naan", 35),
        ("Tandoori Parota", 60), ("Lacha Parota", 60),
        # Stuffed (Naan, Kulcha, Parata) - 60
        ("Stuffed Naan", 60), ("Stuffed Kulcha", 60), ("Stuffed Parata", 60),
        ("Paneer Naan", 65), ("Garlic Naan", 65), ("Cheese Garlic Naan", 70),
        ("Masala Kulcha", 60), ("Kulcha", 40), ("Butter Kulcha", 60), ("Amritsari Kulcha", 65),
        ("Veg Kulcha", 55), ("Aloo Kulcha", 45), ("Aloo Parata", 60), ("Tomato Parota", 60),
        ("Paneer Parota", 65), ("Cheese Naan", 60), ("Kashmiri Naan", 70),
    ]),
    # ---- page: MAIN COURSE ----
    ("Main Course", "food", [
        ("Chenna Masala", 125), ("Green Peas Masala", 125), ("Dum Aloo", 125), ("Aloo Mutter", 125),
        ("Aloo Gobi", 130), ("Palak Aloo", 130), ("Gobi Masala", 130), ("Mixed Veg Curry", 130),
        ("Plain Palak", 125), ("Baingan Masala", 125), ("Stuffed Capsicum", 130), ("Dall Fry", 130),
        ("Dall Tadaka", 150), ("Paneer Butter Masala", 140), ("Mutter Paneer", 145),
        ("Navarathna Kurma", 170), ("Paneer Kurma", 150), ("Paneer Tikka Masala", 210),
        ("Mushroom Tikka Masala", 210), ("Aloo Tikka Masala", 210), ("Paneer Pasandhi", 180),
        ("Babycorn Paneer Masala", 180), ("Kaju Masala", 180), ("Veg Kolhapuri", 160),
        ("Kadai Paneer", 170), ("Kadai Mushroom", 175), ("Kadai Kaju", 185),
        ("Babycorn Capsicum Masala", 170), ("Paneer Koftha", 180), ("Malai Koftha", 170),
        ("Kadai Veg", 160), ("Kaju Paneer Masala", 180), ("Mushroom Masala", 160),
        ("Paneer Kandari", 170), ("Veg Jaipuri", 170), ("Paneer Shahi Kurma", 180),
        ("Palak Paneer", 140), ("Kaju Tomato", 180), ("Paneer Tawa Masala", 190),
        ("Mushroom Tawa Masala", 190), ("Paneer Jaipuri", 180), ("Mushroom Jaipuri", 185),
    ]),
    # ---- page: MOMOS / SOUPS / CHINESE RICE / LUNCH ----
    ("Momos", "snack", [
        ("Steam Momos", 110), ("Fried Momos", 116), ("Pan Fried Momos", 210),
    ]),
    ("Soups", "food", [
        ("Tomato Soup", 75), ("Potato Soup", 75), ("Sweetcorn Soup", 80), ("Green Peas Soup", 80),
        ("Mulligatawny Soup", 80), ("Manchow Soup", 85), ("Vegetable Soup", 75),
        ("Mushroom Soup", 85), ("French Onion Soup", 85), ("Babycorn Soup", 85),
        ("Lemon Coriander Soup", 85), ("Hot & Sour Soup", 90),
    ]),
    ("Chinese Rice Dishes", "food", [
        ("Veg Fried Rice", 130), ("Gobi Fried Rice", 140), ("Paneer Fried Rice", 140),
        ("Schezwan Fried Rice", 145),
        # Choice of Schezwan with (Paneer, Mushroom, Babycorn) - 150
        ("Schezwan Paneer Fried Rice", 150), ("Schezwan Mushroom Fried Rice", 150),
        ("Schezwan Babycorn Fried Rice", 150),
        ("Green Rice", 150), ("Cashew Fried Rice", 150),
    ]),
    ("Lunch", "food", [
        ("South Indian Meals", 190), ("North Indian Meals", 200), ("Chapathi", 70), ("Papad", 5),
    ]),
    # ---- page: SALAD AND RAITA / JUICE / HOT BEVERAGES / DESSERT ----
    ("Salad and Raita", "food", [
        ("Green Salad", 40), ("Onion Salad", 30), ("Onion Cucumber Salad", 40),
        ("Chinese Salad", 45), ("Mixed Veg Raita", 40),
    ]),
    ("Juice", "beverage", [
        ("Apple Juice", 60), ("Grape Juice", 60), ("Pomegranate Juice", 60), ("Sapota Juice", 60),
        ("Mixed Fruit Juice", 60), ("Pineapple Juice", 60), ("Mosambi Juice", 60),
        ("Orange Juice", 60), ("Mango Juice", 60), ("Lassi", 55), ("Water Melon Juice", 60),
        ("Butter Milk", 45),
    ]),
    ("Hot Beverages", "beverage", [
        ("Coffee", 27), ("Tea", 18), ("Milk", 19), ("Horlicks", 37), ("Bournvita", 37),
        ("Hot Badam Milk", 37),
    ]),
    ("Dessert", "food", [
        ("Basundi", 50), ("Bengali Sweet", 35), ("Bengali Sandwich", 50), ("Rasmalai", 50),
    ]),
]


def flatten(markup: int, gst: float):
    """Yield dicts ready for MenuItem(**d), in printed order, with the markup applied."""
    order = 0
    seen = set()
    for section, category, items in MENU:
        for name, printed in items:
            key = name.strip().lower()
            if key in seen:                 # same dish printed twice — keep the first
                continue
            seen.add(key)
            order += 10
            yield {
                "name": name.strip(),
                "description": section,
                "category": category,
                "price": Decimal(printed + markup),
                "gst_percent": gst,
                "is_available": True,
                "sort_order": order,
            }


def seed(db, markup: int, gst: float, commit: bool, update_existing: bool) -> dict:
    stats = {"total": 0, "inserted": 0, "updated": 0, "skipped": 0}

    # Categories are an admin-editable list; refuse up front rather than fail on row 200.
    allowed = set(get_category_list(db, "menu"))
    wanted = {c for _, c, _ in MENU}
    missing = sorted(wanted - allowed)
    if missing:
        raise SystemExit(f"menu category list does not contain {missing} "
                         f"(allowed: {sorted(allowed)}). Add them in Settings or edit MENU.")

    existing = {(m.name or "").strip().lower(): m for m in db.query(MenuItem).all()}

    for row in flatten(markup, gst):
        stats["total"] += 1
        cur = existing.get(row["name"].lower())
        if cur is None:
            db.add(MenuItem(**row))
            stats["inserted"] += 1
            logger.info(f"  + {row['name']:<32} {row['price']:>6}   [{row['description']}]")
        elif update_existing:
            before = cur.price
            for k in ("description", "category", "price", "gst_percent", "sort_order"):
                setattr(cur, k, row[k])
            stats["updated"] += 1
            logger.info(f"  ~ {row['name']:<32} {before} -> {row['price']}")
        else:
            stats["skipped"] += 1
            logger.info(f"  = {row['name']:<32} exists (id {cur.id}, price {cur.price}) — skipped")

    if commit:
        db.commit()
    else:
        db.rollback()
    return stats


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--commit", action="store_true", help="write changes (default: dry run)")
    p.add_argument("--update-existing", action="store_true",
                   help="overwrite price/gst/description/sort_order of items that already exist")
    p.add_argument("--markup", type=int, default=DEFAULT_MARKUP,
                   help=f"rupees added to every printed price (default {DEFAULT_MARKUP})")
    p.add_argument("--gst", type=float, default=DEFAULT_GST,
                   help=f"gst_percent stored on every item (default {DEFAULT_GST})")
    args = p.parse_args(argv)

    db = SessionLocal()
    try:
        stats = seed(db, args.markup, args.gst, args.commit, args.update_existing)
    finally:
        db.close()

    mode = "COMMITTED" if args.commit else "DRY RUN — nothing written (pass --commit)"
    logger.info(f"\n{mode}: {stats['total']} items on the printed menu · "
                f"{stats['inserted']} inserted · {stats['updated']} updated · "
                f"{stats['skipped']} skipped (already present) · markup Rs {args.markup} · GST {args.gst}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
