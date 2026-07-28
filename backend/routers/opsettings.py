"""Admin-editable operational settings (F-A settings backbone).

Surfaces the option-lists / taxonomies (expense / maintenance / complaint / menu
categories) and the printed registration-slip rules that used to be hard-coded, so an
admin can change them from the web Settings screen WITHOUT a redeploy.

  * GET is reception-or-admin — the front desk reads the lists to populate its dropdowns.
  * Writes are admin-only and audited (append-only trail, same idiom as the other config PUTs).

Membership is enforced at the point of use: the create endpoints call
`utils.settings.validate_category(db, family, value)` after their Pydantic schema was
loosened from a fixed enum-regex to a plain slug pattern.
"""
import logging

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy.orm import Session

from database import SessionLocal
from utils import settings as app_settings
from utils.audit import write_audit
from utils.auth_utils import require_admin, require_reception_or_admin

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/settings", tags=["Settings"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("/health", dependencies=[Depends(require_reception_or_admin)])
def health():
    return {"status": "ok", "module": "opsettings"}


# ---------------------------------------------------------------- category lists

@router.get("/categories", dependencies=[Depends(require_reception_or_admin)])
def get_categories(db: Session = Depends(get_db)):
    """Every editable category family with its current option-list, plus the seed defaults
    (so the UI can offer a 'reset to default' and show what shipped)."""
    return {
        "families": app_settings.get_all_category_lists(db),
        "defaults": app_settings.CATEGORY_FAMILIES,
    }


@router.put("/categories/{family}")
def set_categories(family: str,
                   items: list[str] = Body(..., embed=True),
                   db: Session = Depends(get_db),
                   user=Depends(require_admin)):
    """Replace a family's option-list. Slugs are normalised/deduped and cannot be empty."""
    if family not in app_settings.CATEGORY_FAMILIES:
        raise HTTPException(status_code=404, detail=f"Unknown category family: {family}")
    before = app_settings.get_category_list(db, family)
    try:
        after = app_settings.set_category_list(db, family, items, user=user, commit=True)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    write_audit(db, user, "settings.categories_update", "app_settings", None,
                before={"family": family, "items": before},
                after={"family": family, "items": after}, client="web", commit=True)
    return {"family": family, "items": after}


# ---------------------------------------------------------------- registration-slip rules

@router.get("/registration-rules", dependencies=[Depends(require_reception_or_admin)])
def get_registration_rules(db: Session = Depends(get_db)):
    return {"text": app_settings.get_registration_rules(db)}


@router.put("/registration-rules")
def set_registration_rules(text: str = Body(..., embed=True, max_length=4000),
                           db: Session = Depends(get_db),
                           user=Depends(require_admin)):
    app_settings.set_setting(db, app_settings.REGISTRATION_RULES_KEY,
                             (text or "").strip(), user=user, commit=True)
    write_audit(db, user, "settings.registration_rules_update", "app_settings", None,
                after={"length": len(text or "")}, client="web", commit=True)
    return {"text": app_settings.get_registration_rules(db)}
