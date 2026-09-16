"""Follow-up to seed_restaurant_menu.py (owner, 2026-09-16): turn the printed menu's SECTIONS into
real room-service categories, and set each section's serving hours.

Why: the seed parked every dish under the generic `food` category with the printed section only in
`description`. The owner wants the sections as the actual categories (so the portal tabs, the staff
page groups and the desk search all read "Tiffins", "Tandoori Starter", ...) and per-section
availability windows so a guest can't order a dosa at 2pm or a biryani at 8am.

What it does (idempotent — safe to re-run, dry-run by default):
  1. registers the category slugs in the `categories_menu` setting (keeps the 4 defaults, since the
     admin dropdown / other code may still reference them);
  2. for every seeded item (matched by NAME, case-insensitive): category = its section's slug, with
     the owner's exceptions (Veg Samosa / Veg Cutlet -> snacks), `description` cleared (it only held the
     section, which is now the category), `available_windows` = the section's hours with the owner's
     per-item exceptions;
  3. items not on the printed menu are left untouched.

Serving hours (IST, as `[{"start":"HH:MM","end":"HH:MM"}]` — the format routers/portal._parse_windows reads):
  tiffins            07:00-12:00 + 16:00-22:00   (Upma, Pongal, Pesarattu, MLA Pesarattu: 07:00-11:00;
                                                  Channa Batura, Parota: 16:00-22:00)
  special_dosa       16:00-22:00
  snacks             11:00-17:00                 (Veg Samosa, Veg Cutlet)
  north_indian_rice, noodles, indian_starter, chinese_starter, tandoori_starter, main_course,
  momos, soups, chinese_rice, salad_raita        12:00-22:00
  bread              12:00-16:00 + 17:00-22:00
  lunch              11:30-22:00
  juice              08:00-22:00
  hot_beverages, dessert                        06:00-22:00

Usage (from backend/):
    python -m scripts.menu_categories_and_hours             # report only
    python -m scripts.menu_categories_and_hours --commit    # write
Targets whatever database.py resolves; set DATABASE_URL to the Railway DATABASE_PUBLIC_URL for prod.
"""
import argparse
import json
import logging
import re
import sys

from database import SessionLocal
from models import MenuItem
from utils.settings import get_category_list, set_category_list

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

# printed section -> category slug (<= 20 chars, [a-z0-9_]: models.MenuItem.category is String(20))
SECTION_SLUG = {
    "Tiffins": "tiffins",
    "Special Dosa Items": "special_dosa",
    "North Indian Rice Dishes": "north_indian_rice",
    "Noodles": "noodles",
    "Indian Starter": "indian_starter",
    "Chinese Starter": "chinese_starter",
    "Tandoori Starter": "tandoori_starter",
    "Bread": "bread",
    "Main Course": "main_course",
    "Momos": "momos",
    "Soups": "soups",
    "Chinese Rice Dishes": "chinese_rice",
    "Lunch": "lunch",
    "Salad and Raita": "salad_raita",
    "Juice": "juice",
    "Hot Beverages": "hot_beverages",
    "Dessert": "dessert",
}
SNACKS = "snacks"

def W(*pairs):
    return [{"start": s, "end": e} for s, e in pairs]

HOURS = {
    "tiffins": W(("07:00", "12:00"), ("16:00", "22:00")),
    "special_dosa": W(("16:00", "22:00")),
    SNACKS: W(("11:00", "17:00")),
    "north_indian_rice": W(("12:00", "22:00")),
    "noodles": W(("12:00", "22:00")),
    "indian_starter": W(("12:00", "22:00")),
    "chinese_starter": W(("12:00", "22:00")),
    "tandoori_starter": W(("12:00", "22:00")),
    "main_course": W(("12:00", "22:00")),
    "momos": W(("12:00", "22:00")),
    "soups": W(("12:00", "22:00")),
    "chinese_rice": W(("12:00", "22:00")),
    "salad_raita": W(("12:00", "22:00")),
    "bread": W(("12:00", "16:00"), ("17:00", "22:00")),
    "lunch": W(("11:30", "22:00")),
    "juice": W(("08:00", "22:00")),
    "hot_beverages": W(("06:00", "22:00")),
    "dessert": W(("06:00", "22:00")),
}

# per-item overrides (name, lower-case) -> (category, hours). Applied after the section rule.
ITEM_OVERRIDES = {
    "upma": ("tiffins", W(("07:00", "11:00"))),
    "pongal": ("tiffins", W(("07:00", "11:00"))),
    "pesarattu": ("tiffins", W(("07:00", "11:00"))),
    "mla pesarattu": ("tiffins", W(("07:00", "11:00"))),
    "channa batura": ("tiffins", W(("16:00", "22:00"))),
    "parota": ("tiffins", W(("16:00", "22:00"))),      # the Tiffins "Parota" only; Bread parotas keep bread hours
    "veg samosa": (SNACKS, HOURS[SNACKS]),
    "veg cutlet": (SNACKS, HOURS[SNACKS]),
    "curd rice": ("lunch", HOURS["lunch"]),         # owner: lunch, not a rice-dish (2026-09-16)
}


def _printed_menu():
    """(section, [names]) from the seed script, so this file never duplicates the 268 names."""
    from scripts.seed_restaurant_menu import MENU
    return [(section, [name for name, _ in items]) for section, _cat, items in MENU]


def plan(db):
    """-> (category_slugs, [(item, new_category, new_windows_json, changed)])"""
    by_name = {}
    for m in db.query(MenuItem).all():
        by_name.setdefault(m.name.strip().lower(), m)
    rows, missing = [], []
    for section, names in _printed_menu():
        slug = SECTION_SLUG[section]
        for name in names:
            m = by_name.get(name.strip().lower())
            if m is None:
                missing.append(name)
                continue
            cat, hours = slug, HOURS[slug]
            if name.lower() in ITEM_OVERRIDES:
                cat, hours = ITEM_OVERRIDES[name.lower()]
            wjson = json.dumps(hours)
            changed = (m.category != cat) or ((m.available_windows or None) != wjson) or (m.description is not None)
            rows.append((m, cat, wjson, changed))
    return rows, missing


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--commit", action="store_true", help="write changes (default: dry run)")
    a = p.parse_args(argv)
    db = SessionLocal()
    try:
        rows, missing = plan(db)
        current = get_category_list(db, "menu")
        wanted = list(dict.fromkeys(current + list(SECTION_SLUG.values()) + [SNACKS]))
        cat_change = wanted != current

        per_cat = {}
        for m, cat, wjson, changed in rows:
            per_cat.setdefault(cat, [0, 0, wjson]); per_cat[cat][0] += 1; per_cat[cat][1] += int(changed)
        logger.info("%-20s %5s %8s  hours", "category", "items", "changing")
        for cat in list(SECTION_SLUG.values()) + [SNACKS]:
            n, c, wj = per_cat.get(cat, [0, 0, "[]"])
            hours = " + ".join(f"{w['start']}-{w['end']}" for w in json.loads(wj))
            logger.info("%-20s %5d %8d  %s", cat, n, c, hours)
        logger.info("item exceptions: %s", ", ".join(k.title() for k in ITEM_OVERRIDES))
        if missing:
            logger.warning("NOT FOUND in this DB (skipped): %s", ", ".join(missing))
        logger.info("categories_menu: %s -> %s", current, wanted if cat_change else "(unchanged)")

        n_changed = sum(1 for r in rows if r[3])
        if not a.commit:
            logger.info("\nDRY RUN — nothing written (pass --commit): %d items matched, %d would change, "
                        "%d missing", len(rows), n_changed, len(missing))
            return 0
        for m, cat, wjson, changed in rows:
            if not changed:
                continue
            m.category = cat
            m.available_windows = wjson
            m.description = None
        if cat_change:
            set_category_list(db, "menu", wanted, commit=False)
        db.commit()
        logger.info("\nCOMMITTED: %d items updated, %d already correct, %d missing; categories %s",
                    n_changed, len(rows) - n_changed, len(missing), "updated" if cat_change else "unchanged")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
