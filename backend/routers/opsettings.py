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


@router.get("/desk-public-key", dependencies=[Depends(require_reception_or_admin)])
def desk_public_key():
    """The key the front desk seals ID scans with when it is OFFLINE.

    Public by construction — it only lets the desk ENCRYPT. Sealing with it produces an envelope
    only the server's ID_SCAN_DESK_PRIVATE_KEY can open (utils/scan_envelope.py). The desk fetches
    this at login and caches it beside the outbox, so a desk that has been online once can capture
    scans with the internet down. 503 means offline capture is simply not configured yet; the
    online path is unaffected."""
    from utils import scan_envelope
    pk = scan_envelope.public_key()
    if pk is None:
        raise HTTPException(status_code=503,
                            detail="Offline scan capture is not configured on the server.")
    return pk


# --- Arrival & departure rules (v5m) -------------------------------------------------------------
# One JSON document (utils/arrival_rules.py documents the shape). GET is reception-or-admin so the
# desk can show "free up to 30 min" hints; PUT is admin-only, validated, audited.

@router.get("/arrival-rules", dependencies=[Depends(require_reception_or_admin)])
def get_arrival_rules(db: Session = Depends(get_db)):
    from utils import arrival_rules
    return {"rules": arrival_rules.load_rules(db), "defaults": arrival_rules.DEFAULT_RULES}


@router.put("/arrival-rules")
def set_arrival_rules(rules: dict = Body(..., embed=True),
                      db: Session = Depends(get_db),
                      user=Depends(require_admin)):
    import json
    from utils import arrival_rules
    errs = arrival_rules.validate(rules)
    if errs:
        raise HTTPException(status_code=422, detail="; ".join(errs))
    clean = arrival_rules.normalise(rules)
    before = arrival_rules.load_rules(db)
    app_settings.set_setting(db, arrival_rules.RULES_KEY, json.dumps(clean), user=user, commit=True)
    write_audit(db, user, "settings.arrival_rules_update", "app_settings", None,
                before=before, after=clean, client="web", commit=True)
    return {"rules": clean}


# --- Front-desk policy (v5n) ----------------------------------------------------------------
# How much KYC the desk must capture, and the go-live cutoff for automatic no-shows. GET is
# reception-or-admin because the DESK enforces the same rule in its wizard (it also receives this
# block on every board poll); PUT is admin-only and audited.

@router.get("/frontdesk-config", dependencies=[Depends(require_reception_or_admin)])
def get_frontdesk_config(db: Session = Depends(get_db)):
    return app_settings.get_frontdesk_config(db)


@router.put("/frontdesk-config")
def set_frontdesk_config(id_scope: str | None = Body(None),
                         agent_lead_id_only: bool | None = Body(None),
                         lost_card_fee_amount: float | None = Body(None),
                         lost_card_fee_gst_percent: float | None = Body(None),
                         scans_required: bool | None = Body(None),
                         no_show_from_date: str | None = Body(None),
                         blocked_guest_phones: str | None = Body(None),
                         db: Session = Depends(get_db),
                         user=Depends(require_admin)):
    from datetime import datetime as _dt
    before = app_settings.get_frontdesk_config(db)
    if id_scope is not None:
        scope = (id_scope or "").strip().lower()
        if scope not in app_settings.ID_SCOPES:
            raise HTTPException(status_code=422,
                                detail="id_scope must be one of: " + ", ".join(app_settings.ID_SCOPES))
        app_settings.set_setting(db, app_settings.CHECKIN_ID_SCOPE_KEY, scope, user=user)
    if agent_lead_id_only is not None:
        app_settings.set_setting(db, app_settings.AGENT_LEAD_ID_ONLY_KEY,
                                 "true" if agent_lead_id_only else "false", user=user)
    if lost_card_fee_amount is not None:
        if lost_card_fee_amount < 0:
            raise HTTPException(status_code=422, detail="lost_card_fee_amount cannot be negative")
        app_settings.set_setting(db, app_settings.LOST_CARD_FEE_KEY,
                                 str(round(float(lost_card_fee_amount), 2)), user=user)
    if lost_card_fee_gst_percent is not None:
        if not 0 <= lost_card_fee_gst_percent <= 100:
            raise HTTPException(status_code=422,
                                detail="lost_card_fee_gst_percent must be between 0 and 100")
        app_settings.set_setting(db, app_settings.LOST_CARD_FEE_GST_KEY,
                                 str(round(float(lost_card_fee_gst_percent), 2)), user=user)
    if scans_required is not None:
        app_settings.set_setting(db, app_settings.CHECKIN_SCANS_REQUIRED_KEY,
                                 "true" if scans_required else "false", user=user)
    if no_show_from_date is not None:
        raw = (no_show_from_date or "").strip()
        if raw:
            try:
                raw = _dt.strptime(raw[:10], "%Y-%m-%d").date().isoformat()
            except ValueError:
                raise HTTPException(status_code=422, detail="no_show_from_date must be YYYY-MM-DD")
        app_settings.set_setting(db, app_settings.NO_SHOW_FROM_DATE_KEY, raw, user=user)
    if blocked_guest_phones is not None:
        # Stored as the admin typed it (free text, comma/semicolon separated); normalised on read by
        # utils.phone.blocked_numbers, so "+91 124 462 8747" and "1244628747" block the same line.
        from utils.phone import BLOCKED_PHONES_KEY
        app_settings.set_setting(db, BLOCKED_PHONES_KEY, (blocked_guest_phones or "").strip(), user=user)
    db.commit()
    after = app_settings.get_frontdesk_config(db)
    write_audit(db, user, "settings.frontdesk_config_update", "app_settings", None,
                before=before, after=after, client="web", commit=True)
    return after


# --- OTA intake policy (v5r) -----------------------------------------------------------------
# Whether a voucher becomes a booking on its own, and whether that includes multi-room vouchers.
# Reception can read it (the drafts screen explains why a draft is held); only an admin may change it.

@router.get("/ota-config", dependencies=[Depends(require_reception_or_admin)])
def get_ota_config(db: Session = Depends(get_db)):
    return app_settings.get_ota_config(db)


@router.put("/ota-config")
def set_ota_config(auto_confirm_mode: str | None = Body(None),
                   max_variance_percent: float | None = Body(None),
                   db: Session = Depends(get_db),
                   user=Depends(require_admin)):
    before = app_settings.get_ota_config(db)
    if auto_confirm_mode is not None:
        mode = (auto_confirm_mode or "").strip().lower()
        if mode not in app_settings.OTA_AUTO_CONFIRM_MODES:
            raise HTTPException(status_code=422,
                                detail="auto_confirm_mode must be one of: "
                                       + ", ".join(app_settings.OTA_AUTO_CONFIRM_MODES))
        app_settings.set_setting(db, app_settings.OTA_AUTO_CONFIRM_MODE_KEY, mode, user=user)
    if max_variance_percent is not None:
        pct = max(0.0, min(100.0, float(max_variance_percent)))
        app_settings.set_setting(db, app_settings.OTA_AUTO_CONFIRM_VARIANCE_KEY, f"{pct:g}", user=user)
    db.commit()
    after = app_settings.get_ota_config(db)
    write_audit(db, user, "settings.ota_config_update", "app_settings", None,
                before=before, after=after, client="web", commit=True)
    return after
