"""India compliance, Tally & register exports (prompt 18, slices 1, 3, 4, 5).

Admin-only compliance surface (evacuation is reception+admin — a fire/safety desk tool):
  GET  /compliance/tally/export      — Tally XML / accounting CSV / JSON (slice 1)
  GET  /compliance/police-register   — local-police guest register (slice 3)
  GET  /compliance/form-c            — FRRO Form C for foreign guests (slice 3)
  GET  /compliance/evacuation        — live in-house list for fire/safety (slice 4)
  GET  /compliance/id-access-audit   — who viewed which guest's ID docs (slice 5)
  GET/PUT /compliance/config          — police/FRRO identity + Tally ledgers + 2FA toggles

Reuses the reports export dispatch (_export_or_json/_meta/_range) and query helpers so the
figures match the Reports screen. GST e-invoicing (slice 2) lives on routers/folio.py; admin
2FA (slice 6) lives on routers/twofa.py + routers/admin.py.
"""
import logging
from datetime import datetime, time as dtime

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import SessionLocal
from models import AuditLog, Booking, BookingItem, Guest, GuestProfile, Room, User
from utils.audit import write_audit
from utils.auth_utils import require_admin, require_reception_or_admin
from utils.settings import (COMPLIANCE_EDITABLE_KEYS, get_compliance_config, set_setting)
from utils.tally_export import build_accounting_csv, build_tally_xml
# Reuse the Reports export/dispatch + range helpers (single source of truth for totals).
from routers.reports import (_export_or_json, _meta, _range, in_house_data)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/compliance", tags=["Compliance"])

_LIVE_STATUSES = ("confirmed", "checked_in", "checked_out")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Slice 1 — Tally / accounting export
# ---------------------------------------------------------------------------

@router.get("/tally/export")
def tally_export(from_: str = Query(None, alias="from"), to: str = Query(None),
                 format: str = Query("xml"),
                 db: Session = Depends(get_db), user=Depends(require_admin)):
    """Export sales + collections for [from, to] as Tally XML, accounting CSV, or JSON journal.
    Totals reconcile with the Sales/GST reports (same helpers)."""
    dfrom, dto = _range(from_, to)
    cfg = get_compliance_config(db)
    fmt = (format or "xml").strip().lower()
    write_audit(db, user, "compliance.tally_export", "range", f"{dfrom}..{dto}",
                after={"format": fmt}, client="web", commit=True)
    if fmt == "xml":
        xml = build_tally_xml(db, dfrom, dto, cfg)
        return StreamingResponse(
            iter([xml]), media_type="application/xml",
            headers={"Content-Disposition": f'attachment; filename="tally_{dfrom}_{dto}.xml"'})
    if fmt == "csv":
        csv_text = build_accounting_csv(db, dfrom, dto, cfg)
        return StreamingResponse(
            iter([csv_text]), media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="accounting_{dfrom}_{dto}.csv"'})
    if fmt == "json":
        from utils.tally_export import build_journal
        vouchers, totals = build_journal(db, dfrom, dto, cfg)
        return {"from": str(dfrom), "to": str(dto), "company": cfg["tally_company_name"],
                "vouchers": vouchers, "totals": totals}
    raise HTTPException(status_code=400, detail="format must be xml, csv or json")


# ---------------------------------------------------------------------------
# Slice 3 — Police guest register + Form C (FRRO)
# ---------------------------------------------------------------------------

def _rooms_for_booking(db, booking_id):
    labels = []
    for it in db.query(BookingItem).filter(BookingItem.booking_id == booking_id).all():
        if it.room_id:
            r = db.query(Room).filter(Room.room_id == it.room_id).first()
            if r:
                labels.append(r.room_number)
    return ", ".join(labels) if labels else "—"


def _register_bookings(db, dfrom, dto):
    """Bookings that ARRIVED in [dfrom, dto] (live statuses), with guest + profile joined."""
    bookings = (db.query(Booking)
                .filter(Booking.check_in >= dfrom, Booking.check_in <= dto,
                        Booking.status.in_(_LIVE_STATUSES))
                .order_by(Booking.check_in).all())
    out = []
    for b in bookings:
        guest = db.query(Guest).filter(Guest.guest_id == b.guest_id).first()
        profile = (db.query(GuestProfile).filter(GuestProfile.guest_id == b.guest_id).first()
                   if guest else None)
        out.append((b, guest, profile))
    return out


@router.get("/police-register")
def police_register(from_: str = Query(None, alias="from"), to: str = Query(None),
                    format: str = Query("json"),
                    db: Session = Depends(get_db), user=Depends(require_admin)):
    dfrom, dto = _range(from_, to)
    cfg = get_compliance_config(db)
    rows_json, rows_tab = [], []
    for b, guest, profile in _register_bookings(db, dfrom, dto):
        rec = {
            "check_in": str(b.check_in), "check_out": str(b.check_out),
            "room": _rooms_for_booking(db, b.booking_id),
            "guest_name": guest.name if guest else "—",
            "phone": guest.phone if guest else "—",
            "address": (profile.address if profile else None) or "—",
            "id_type": (guest.id_type if guest else None) or "—",
            "id_number": (guest.id_number_masked if guest else None) or "—",
            "nationality": (profile.nationality if profile else None) or "Indian",
        }
        rows_json.append(rec)
        rows_tab.append([rec["check_in"], rec["check_out"], rec["room"], rec["guest_name"],
                         rec["phone"], rec["address"], rec["id_type"], rec["id_number"],
                         rec["nationality"]])
    write_audit(db, user, "compliance.police_register", "range", f"{dfrom}..{dto}",
                after={"count": len(rows_json), "format": format}, client="web", commit=True)
    meta = _meta(dfrom, dto)
    if cfg["police_station_name"]:
        meta["Police Station"] = cfg["police_station_name"]
    return _export_or_json(
        format,
        {"from": str(dfrom), "to": str(dto), "count": len(rows_json), "rows": rows_json,
         "police_station": cfg["police_station_name"]},
        title="Police Guest Register",
        columns=["Check-in", "Check-out", "Room", "Guest", "Phone", "Address", "ID Type",
                 "ID No (masked)", "Nationality"],
        rows=rows_tab, meta=meta, filename=f"police_register_{dfrom}_{dto}")


@router.get("/form-c")
def form_c(from_: str = Query(None, alias="from"), to: str = Query(None),
           format: str = Query("json"),
           db: Session = Depends(get_db), user=Depends(require_admin)):
    """FRRO Form C register — foreign nationals only (GuestProfile.is_foreign_national)."""
    dfrom, dto = _range(from_, to)
    cfg = get_compliance_config(db)
    rows_json, rows_tab = [], []
    for b, guest, profile in _register_bookings(db, dfrom, dto):
        if not (profile and profile.is_foreign_national):
            continue
        rec = {
            "guest_name": guest.name if guest else "—",
            "nationality": profile.nationality or "—",
            "passport_number": profile.passport_number_masked or "—",
            "passport_place_of_issue": profile.passport_place_of_issue or "—",
            "passport_expiry": str(profile.passport_expiry) if profile.passport_expiry else "—",
            "visa_number": profile.visa_number_masked or "—",
            "visa_type": profile.visa_type or "—",
            "visa_expiry": str(profile.visa_expiry) if profile.visa_expiry else "—",
            "arrived_from": profile.arrived_from or "—",
            "next_destination": profile.next_destination or "—",
            "room": _rooms_for_booking(db, b.booking_id),
            "check_in": str(b.check_in), "check_out": str(b.check_out),
        }
        rows_json.append(rec)
        rows_tab.append([rec["guest_name"], rec["nationality"], rec["passport_number"],
                         rec["passport_place_of_issue"], rec["passport_expiry"], rec["visa_number"],
                         rec["visa_type"], rec["visa_expiry"], rec["arrived_from"],
                         rec["next_destination"], rec["room"], rec["check_in"], rec["check_out"]])
    write_audit(db, user, "compliance.form_c", "range", f"{dfrom}..{dto}",
                after={"count": len(rows_json), "format": format}, client="web", commit=True)
    meta = _meta(dfrom, dto)
    if cfg["frro_office"]:
        meta["FRRO Office"] = cfg["frro_office"]
    return _export_or_json(
        format,
        {"from": str(dfrom), "to": str(dto), "count": len(rows_json), "rows": rows_json,
         "frro_office": cfg["frro_office"]},
        title="Form C — Foreign Guest Register (FRRO)",
        columns=["Guest", "Nationality", "Passport (masked)", "Place of Issue", "Passport Expiry",
                 "Visa (masked)", "Visa Type", "Visa Expiry", "Arrived From", "Next Destination",
                 "Room", "Check-in", "Check-out"],
        rows=rows_tab, meta=meta, filename=f"form_c_{dfrom}_{dto}")


# ---------------------------------------------------------------------------
# Slice 4 — Live evacuation list
# ---------------------------------------------------------------------------

@router.get("/evacuation")
def evacuation(format: str = Query("json"),
               db: Session = Depends(get_db), user=Depends(require_reception_or_admin)):
    """Everyone currently in-house — room, guest, phone — for a fire/safety roll-call. One tap."""
    data = in_house_data(db)
    rows_tab = [[r["rooms"], r["guest_name"] or "—", r["phone"] or "—",
                 r["check_in"], r["check_out"]] for r in data["rows"]]
    return _export_or_json(
        format,
        {"count": data["count"], "rows": data["rows"]},
        title="Evacuation List — Guests In-House",
        columns=["Room", "Guest", "Phone", "Check-in", "Check-out"],
        rows=rows_tab, meta=_meta(), filename="evacuation_list")


# ---------------------------------------------------------------------------
# Slice 5 — ID-access audit
# ---------------------------------------------------------------------------

@router.get("/id-access-audit")
def id_access_audit(from_: str = Query(None, alias="from"), to: str = Query(None),
                    guest_id: int = Query(None), format: str = Query("json"),
                    db: Session = Depends(get_db), user=Depends(require_admin)):
    """Who viewed a guest's ID document / masked ID number, and when (audit action 'id.*')."""
    dfrom, dto = _range(from_, to)
    q = (db.query(AuditLog).filter(AuditLog.action.like("id.%"),
                                   AuditLog.created_at >= datetime.combine(dfrom, dtime.min),
                                   AuditLog.created_at <= datetime.combine(dto, dtime.max)))
    if guest_id is not None:
        q = q.filter(AuditLog.entity_type == "guest", AuditLog.entity_id == str(guest_id))
    logs = q.order_by(AuditLog.created_at.desc()).limit(1000).all()
    rows_json, rows_tab = [], []
    for lg in logs:
        actor = None
        if lg.user_id:
            u = db.query(User).filter(User.user_id == lg.user_id).first()
            actor = (u.full_name or u.username) if u else None
        gname = None
        if lg.entity_type == "guest" and lg.entity_id and lg.entity_id.isdigit():
            g = db.query(Guest).filter(Guest.guest_id == int(lg.entity_id)).first()
            gname = g.name if g else None
        rec = {"when": lg.created_at.isoformat() if lg.created_at else None,
               "actor": actor or "system", "action": lg.action,
               "guest_id": lg.entity_id, "guest_name": gname or "—",
               "ip": lg.ip or "—", "client": lg.client or "—"}
        rows_json.append(rec)
        rows_tab.append([rec["when"], rec["actor"], rec["action"], rec["guest_id"],
                         rec["guest_name"], rec["ip"], rec["client"]])
    return _export_or_json(
        format,
        {"from": str(dfrom), "to": str(dto), "count": len(rows_json), "rows": rows_json},
        title="ID Document Access Audit",
        columns=["When (UTC)", "Viewed by", "Action", "Guest ID", "Guest", "IP", "Client"],
        rows=rows_tab, meta=_meta(dfrom, dto), filename=f"id_access_audit_{dfrom}_{dto}")


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

class ComplianceConfigUpdate(BaseModel):
    police_station_name: str | None = None
    police_station_code: str | None = None
    frro_office: str | None = None
    hotel_registration_no: str | None = None
    tally_company_name: str | None = None
    tally_sales_ledger: str | None = None
    tally_cgst_ledger: str | None = None
    tally_sgst_ledger: str | None = None
    tally_cash_ledger: str | None = None
    tally_bank_ledger: str | None = None
    tally_debtors_ledger: str | None = None
    admin_2fa_required: bool | None = None
    admin_idle_logout_minutes: int | None = None


@router.get("/config")
def get_config(db: Session = Depends(get_db), user=Depends(require_admin)):
    from services.einvoice_service import provider_status
    from utils import twofa
    cfg = get_compliance_config(db)
    cfg["einvoice_provider"] = provider_status()
    cfg["twofa_available"] = twofa.available()
    return cfg


@router.put("/config")
def put_config(data: ComplianceConfigUpdate, db: Session = Depends(get_db),
               user=Depends(require_admin)):
    payload = data.model_dump(exclude_unset=True)
    for key, value in payload.items():
        if key not in COMPLIANCE_EDITABLE_KEYS:
            continue
        if isinstance(value, bool):
            value = "true" if value else "false"
        set_setting(db, key, value, user=user)
    db.commit()
    write_audit(db, user, "compliance.config_update", "settings", "compliance",
                after=payload, client="web", commit=True)
    return get_compliance_config(db)
