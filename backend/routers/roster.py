"""Staff duty roster + attendance endpoints (prompt 18, slices 12 & 9).

Two halves of one question — who was *supposed* to be on duty, and who actually was:

  * **Roster** (`staff_shifts`) — planned duties, built a week at a time. `POST /roster/bulk`
    copies last week forward, which is how a roster is actually maintained.
  * **Attendance** (`staff_attendance`) — worked sessions. Three sources:
      - `pin`   the staff member types their existing `User.pin` at the desk terminal,
      - `admin` an admin records or corrects a session,
      - `card`  a staff key-card tap resolves `users.staff_card_uid`. The endpoint is live, and the
        encoder library ALREADY exposes `EncoderService.ReadCard()`, so the desktop can read a card
        today. What is still unverified on hardware is whether a staff card's read-back payload is
        stable and unique per card (it may encode role/floor rather than a serial) — bind one UID
        and re-read it to confirm before relying on it. PIN punching covers the gap meanwhile.

Both sides link to money: `cash_shifts.roster_shift_id` ties the drawer that was actually opened
to the duty that was planned, which is what makes the staff-performance report's planned-vs-actual
column real. No new auth system — PIN punching reuses `User.pin` from prompt 01.
"""
import logging
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from database import SessionLocal
from models import CashShift, StaffAttendance, StaffShift, User
from schemas import (AttendanceCardPunch, AttendanceManual, AttendancePunch, RosterBulkCopy,
                     StaffCardAssign, StaffShiftCreate, StaffShiftUpdate)
from utils.audit import _resolve_user_id, write_audit
from utils.auth_utils import get_current_user, require_admin, require_reception_or_admin
from utils.settings import get_backoffice_config

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/roster", tags=["Roster & Attendance"])

# Roles that appear on the duty roster (website `user` accounts are guests, not staff).
# v4b8: `supervisor` merged into `housekeeper`, which is already here — so former
# supervisors now appear on the duty roster, which they never did before.
STAFF_ROLES = ("admin", "reception", "housekeeper", "maintenance", "roomservice")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("/health", dependencies=[Depends(require_reception_or_admin)])
def health():
    return {"status": "ok", "module": "roster"}


# ---------------------------------------------------------------- helpers

def _week_start(d: date) -> date:
    """Monday of the week containing `d` (Indian rosters run Mon-Sun)."""
    return d - timedelta(days=d.weekday())


def _get_staff(db: Session, user_id: int) -> User:
    row = db.query(User).filter(User.user_id == user_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Staff member not found")
    if row.role not in STAFF_ROLES:
        raise HTTPException(status_code=400, detail=f"'{row.role}' accounts are not rostered")
    return row


def _staff_name(db: Session, user_id) -> str | None:
    if not user_id:
        return None
    row = db.query(User).filter(User.user_id == user_id).first()
    return (row.full_name or row.username) if row else None


def _shift_dict(db: Session, s: StaffShift) -> dict:
    return {
        "id": s.id,
        "user_id": s.user_id,
        "staff_name": _staff_name(db, s.user_id),
        "shift_date": str(s.shift_date),
        "shift_type": s.shift_type,
        "start_time": str(s.start_time) if s.start_time else None,
        "end_time": str(s.end_time) if s.end_time else None,
        "role_label": s.role_label,
        "status": s.status,
        "notes": s.notes,
    }


def _attendance_dict(db: Session, a: StaffAttendance) -> dict:
    return {
        "id": a.id,
        "user_id": a.user_id,
        "staff_name": _staff_name(db, a.user_id),
        "clock_in": a.clock_in.isoformat() if a.clock_in else None,
        "clock_out": a.clock_out.isoformat() if a.clock_out else None,
        "minutes_worked": a.minutes_worked,
        "hours_worked": round((a.minutes_worked or 0) / 60, 2),
        "open": a.clock_out is None,
        "source": a.source,
        "station_id": a.station_id,
        "roster_shift_id": a.roster_shift_id,
        "note": a.note,
    }


def _open_session(db: Session, user_id: int) -> StaffAttendance | None:
    return db.query(StaffAttendance).filter(
        StaffAttendance.user_id == user_id,
        StaffAttendance.clock_out.is_(None),
    ).order_by(StaffAttendance.clock_in.desc()).first()


def match_roster_shift(db: Session, user_id: int, when: datetime | None = None) -> StaffShift | None:
    """The planned duty this staff member has on that date, if any.

    Also used by `routers/cash_shift.py` when a drawer is opened, so the shift report can compare
    planned against actual."""
    when = when or datetime.now()
    return db.query(StaffShift).filter(
        StaffShift.user_id == user_id,
        StaffShift.shift_date == when.date(),
        StaffShift.status != "absent",
    ).order_by(StaffShift.id).first()


def _auto_close_stale(db: Session, user_id: int) -> int:
    """Close sessions somebody forgot to clock out of, capped at the configured hours.

    Without this a single forgotten clock-out would make every later hours figure meaningless.
    The cap is recorded in the note so the admin can see it was a system close, not a real one."""
    cfg = get_backoffice_config(db)
    cap = timedelta(hours=cfg["attendance_auto_close_hours"])
    cutoff = datetime.utcnow() - cap
    stale = db.query(StaffAttendance).filter(
        StaffAttendance.user_id == user_id,
        StaffAttendance.clock_out.is_(None),
        StaffAttendance.clock_in < cutoff,
    ).all()
    for row in stale:
        row.clock_out = row.clock_in + cap
        row.minutes_worked = int(cap.total_seconds() // 60)
        row.note = ((row.note + " | ") if row.note else "") + \
            f"auto-closed after {cfg['attendance_auto_close_hours']}h (no clock-out recorded)"
    if stale:
        # SessionLocal has autoflush=False: without this flush the caller's _open_session()
        # re-read would still see clock_out IS NULL and clock the stale row out normally,
        # recording the full elapsed time instead of the capped value.
        db.flush()
    return len(stale)


# ---------------------------------------------------------------- roster

@router.get("/staff")
def list_staff(db: Session = Depends(get_db), user=Depends(require_reception_or_admin)):
    """Rosterable staff — the picker for the roster grid and attendance screens."""
    rows = db.query(User).filter(
        User.role.in_(STAFF_ROLES),
        User.is_active == True,          # noqa: E712
    ).order_by(User.role, User.username).all()
    return {"total": len(rows), "data": [{
        "user_id": u.user_id,
        "username": u.username,
        "full_name": u.full_name,
        "role": u.role,
        "phone": u.phone,
        "has_pin": bool(u.pin),
        "has_staff_card": bool(u.staff_card_uid),
    } for u in rows]}


@router.get("/")
def get_roster(week_start: str | None = Query(None, description="any date in the week (ISO)"),
               user_id: int | None = Query(None),
               db: Session = Depends(get_db), user=Depends(require_reception_or_admin)):
    """One week of planned duties as a grid: staff x the 7 dates."""
    try:
        anchor = date.fromisoformat(week_start) if week_start else date.today()
    except ValueError:
        raise HTTPException(status_code=400, detail="week_start must be ISO YYYY-MM-DD")
    start = _week_start(anchor)
    end = start + timedelta(days=6)

    q = db.query(StaffShift).filter(StaffShift.shift_date >= start,
                                    StaffShift.shift_date <= end)
    if user_id:
        q = q.filter(StaffShift.user_id == user_id)
    shifts = q.order_by(StaffShift.shift_date, StaffShift.user_id).all()

    return {
        "week_start": str(start),
        "week_end": str(end),
        "dates": [str(start + timedelta(days=i)) for i in range(7)],
        "total": len(shifts),
        "data": [_shift_dict(db, s) for s in shifts],
    }


@router.get("/me")
def my_roster(days: int = Query(14, ge=1, le=90), db: Session = Depends(get_db),
              user=Depends(get_current_user)):
    """A staff member's own upcoming duties + their open attendance session.

    Any logged-in staff role may call this (it only ever returns their own rows), which is what
    lets the prompt-13 staff PWA show a housekeeper their shifts later."""
    row = db.query(User).filter(User.username == user.get("sub")).first()
    if not row:
        raise HTTPException(status_code=404, detail="User not found")
    today = date.today()
    shifts = db.query(StaffShift).filter(
        StaffShift.user_id == row.user_id,
        StaffShift.shift_date >= today,
        StaffShift.shift_date <= today + timedelta(days=days),
    ).order_by(StaffShift.shift_date).all()
    open_session = _open_session(db, row.user_id)
    return {
        "user_id": row.user_id,
        "staff_name": row.full_name or row.username,
        "role": row.role,
        "total": len(shifts),
        "data": [_shift_dict(db, s) for s in shifts],
        "open_session": _attendance_dict(db, open_session) if open_session else None,
    }


@router.post("/shifts", dependencies=[Depends(require_admin)])
def create_shift(data: StaffShiftCreate, db: Session = Depends(get_db), user=Depends(require_admin)):
    try:
        staff = _get_staff(db, data.user_id)
        clash = db.query(StaffShift).filter(
            StaffShift.user_id == staff.user_id,
            StaffShift.shift_date == data.shift_date,
            StaffShift.shift_type == data.shift_type,
        ).first()
        if clash:
            raise HTTPException(
                status_code=409,
                detail=f"{staff.full_name or staff.username} already has a "
                       f"{data.shift_type} duty on {data.shift_date:%d-%m-%Y}")

        shift = StaffShift(**data.model_dump(), created_by=_resolve_user_id(db, user))
        db.add(shift)
        db.commit()
        db.refresh(shift)
        write_audit(db, user, "roster.shift_create", "staff_shift", shift.id,
                    after={"user_id": shift.user_id, "shift_date": str(shift.shift_date),
                           "shift_type": shift.shift_type},
                    client="web", commit=True)
        return _shift_dict(db, shift)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"create_shift failed: {e}", exc_info=True)
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to create the duty")


@router.put("/shifts/{shift_id}", dependencies=[Depends(require_admin)])
def update_shift(shift_id: int, data: StaffShiftUpdate, db: Session = Depends(get_db),
                 user=Depends(require_admin)):
    try:
        shift = db.query(StaffShift).filter(StaffShift.id == shift_id).first()
        if not shift:
            raise HTTPException(status_code=404, detail="Duty not found")
        before = {"shift_type": shift.shift_type, "status": shift.status}
        changes = data.model_dump(exclude_unset=True)

        if changes.get("shift_type") and changes["shift_type"] != shift.shift_type:
            clash = db.query(StaffShift).filter(
                StaffShift.user_id == shift.user_id,
                StaffShift.shift_date == shift.shift_date,
                StaffShift.shift_type == changes["shift_type"],
                StaffShift.id != shift.id,
            ).first()
            if clash:
                raise HTTPException(status_code=409,
                                    detail="That staff member already has that duty on this date")

        for key, value in changes.items():
            setattr(shift, key, value)
        shift.updated_by = _resolve_user_id(db, user)
        shift.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(shift)
        write_audit(db, user, "roster.shift_update", "staff_shift", shift.id,
                    before=before, after=changes, client="web", commit=True)
        return _shift_dict(db, shift)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"update_shift failed: {e}", exc_info=True)
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to update the duty")


@router.delete("/shifts/{shift_id}", dependencies=[Depends(require_admin)])
def delete_shift(shift_id: int, db: Session = Depends(get_db), user=Depends(require_admin)):
    """Remove a planned duty. Refused once attendance has been recorded against it — that would
    orphan a worked session's planned-vs-actual link."""
    try:
        shift = db.query(StaffShift).filter(StaffShift.id == shift_id).first()
        if not shift:
            raise HTTPException(status_code=404, detail="Duty not found")
        linked = db.query(StaffAttendance).filter(
            StaffAttendance.roster_shift_id == shift.id).count()
        if linked:
            raise HTTPException(status_code=409,
                                detail="Attendance is recorded against this duty — mark it "
                                       "'absent' or 'swapped' instead of deleting it")
        payload = {"user_id": shift.user_id, "shift_date": str(shift.shift_date),
                   "shift_type": shift.shift_type}
        db.delete(shift)
        db.commit()
        write_audit(db, user, "roster.shift_delete", "staff_shift", shift_id,
                    before=payload, client="web", commit=True)
        return {"status": "deleted", "shift_id": shift_id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"delete_shift failed: {e}", exc_info=True)
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to delete the duty")


@router.post("/bulk", dependencies=[Depends(require_admin)])
def copy_week(data: RosterBulkCopy, db: Session = Depends(get_db), user=Depends(require_admin)):
    """Copy a week of duties forward — how a roster is actually built week to week.

    Existing duties in the target week are left alone unless `overwrite` is set, so a partly
    filled target week is never silently clobbered."""
    try:
        src = _week_start(data.source_week_start)
        dst = _week_start(data.target_week_start)
        if src == dst:
            raise HTTPException(status_code=400, detail="Source and target weeks are the same")

        source = db.query(StaffShift).filter(
            StaffShift.shift_date >= src, StaffShift.shift_date <= src + timedelta(days=6)).all()
        if not source:
            raise HTTPException(status_code=400, detail="The source week has no duties to copy")

        offset = (dst - src).days
        created = skipped = replaced = 0
        for s in source:
            target_date = s.shift_date + timedelta(days=offset)
            existing = db.query(StaffShift).filter(
                StaffShift.user_id == s.user_id,
                StaffShift.shift_date == target_date,
                StaffShift.shift_type == s.shift_type,
            ).first()
            if existing:
                if not data.overwrite:
                    skipped += 1
                    continue
                existing.start_time = s.start_time
                existing.end_time = s.end_time
                existing.role_label = s.role_label
                existing.status = "planned"
                existing.updated_by = _resolve_user_id(db, user)
                existing.updated_at = datetime.utcnow()
                replaced += 1
                continue
            db.add(StaffShift(
                user_id=s.user_id, shift_date=target_date, shift_type=s.shift_type,
                start_time=s.start_time, end_time=s.end_time, role_label=s.role_label,
                status="planned", notes=s.notes, created_by=_resolve_user_id(db, user)))
            created += 1

        db.commit()
        result = {"source_week": str(src), "target_week": str(dst),
                  "created": created, "replaced": replaced, "skipped": skipped}
        write_audit(db, user, "roster.bulk_copy", "staff_shift", None,
                    after=result, client="web", commit=True)
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"copy_week failed: {e}", exc_info=True)
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to copy the roster week")


# ---------------------------------------------------------------- attendance

def _punch(db: Session, staff: User, *, source: str, station_id: str | None,
           note: str | None, card_uid: str | None, actor) -> dict:
    """Toggle: no open session -> clock in; open session -> clock out. One endpoint, one tap."""
    _auto_close_stale(db, staff.user_id)
    open_row = _open_session(db, staff.user_id)
    now = datetime.utcnow()

    if open_row is None:
        shift = match_roster_shift(db, staff.user_id, datetime.now())
        row = StaffAttendance(
            user_id=staff.user_id, clock_in=now, source=source, station_id=station_id,
            roster_shift_id=shift.id if shift else None, card_uid=card_uid, note=note,
            created_by=_resolve_user_id(db, actor))
        db.add(row)
        db.commit()
        db.refresh(row)
        write_audit(db, actor, "attendance.clock_in", "staff_attendance", row.id,
                    after={"user_id": staff.user_id, "source": source,
                           "station_id": station_id, "roster_shift_id": row.roster_shift_id},
                    client="desktop", commit=True)
        return {"action": "clock_in", **_attendance_dict(db, row)}

    open_row.clock_out = now
    open_row.minutes_worked = max(0, int((now - open_row.clock_in).total_seconds() // 60))
    if note:
        open_row.note = ((open_row.note + " | ") if open_row.note else "") + note
    db.commit()
    db.refresh(open_row)
    write_audit(db, actor, "attendance.clock_out", "staff_attendance", open_row.id,
                after={"user_id": staff.user_id, "source": source,
                       "minutes_worked": open_row.minutes_worked},
                client="desktop", commit=True)
    return {"action": "clock_out", **_attendance_dict(db, open_row)}


@router.post("/attendance/punch")
def punch_with_pin(data: AttendancePunch, db: Session = Depends(get_db),
                   user=Depends(require_reception_or_admin)):
    """Clock in/out at the desk terminal with the staff member's own username + PIN.

    The terminal is logged in as a reception/admin user; the PIN identifies WHO is punching, so
    a housekeeper can clock in on the front-desk machine without their own login."""
    cfg = get_backoffice_config(db)
    if not cfg["attendance_pin_enabled"]:
        raise HTTPException(status_code=403, detail="PIN attendance is switched off")

    staff = db.query(User).filter(User.username == data.username.strip()).first()
    # Deliberately one message for "no such user", "no PIN set" and "wrong PIN" — do not let the
    # desk terminal enumerate which staff usernames exist or which have PINs.
    if not staff or not staff.pin or staff.pin != data.pin or not staff.is_active:
        raise HTTPException(status_code=401, detail="Username or PIN is not recognised")
    if staff.role not in STAFF_ROLES:
        raise HTTPException(status_code=400, detail="That account is not rostered")

    return _punch(db, staff, source="pin", station_id=data.station_id,
                  note=data.note, card_uid=None, actor=user)


@router.post("/attendance/card")
def punch_with_card(data: AttendanceCardPunch, db: Session = Depends(get_db),
                    user=Depends(require_reception_or_admin)):
    """Clock in/out by staff key-card tap — resolves `users.staff_card_uid`.

    ⚠️ ENCODER SEAM: this endpoint is complete. `EncoderService.ReadCard()` already works, so the
    desktop can obtain a card payload today; what needs one hardware check is whether a staff
    card's payload is STABLE and UNIQUE per card (bind a UID here, re-read the same card, confirm
    it matches). Until that is confirmed, PIN punching is the supported path — nothing else is
    blocked, and no change is needed here when it is."""
    uid = data.card_uid.strip()
    staff = db.query(User).filter(User.staff_card_uid == uid).first()
    if not staff or not staff.is_active:
        raise HTTPException(status_code=404, detail="That card is not assigned to an active staff member")

    return _punch(db, staff, source="card", station_id=data.station_id,
                  note=None, card_uid=uid, actor=user)


@router.post("/attendance/manual", dependencies=[Depends(require_admin)])
def record_attendance(data: AttendanceManual, db: Session = Depends(get_db),
                      user=Depends(require_admin)):
    """Admin records a worked session that was missed (or corrects one). Audited."""
    try:
        staff = _get_staff(db, data.user_id)
        minutes = None
        if data.clock_out:
            minutes = max(0, int((data.clock_out - data.clock_in).total_seconds() // 60))
        shift = match_roster_shift(db, staff.user_id, data.clock_in)
        row = StaffAttendance(
            user_id=staff.user_id, clock_in=data.clock_in, clock_out=data.clock_out,
            minutes_worked=minutes, source="admin",
            roster_shift_id=shift.id if shift else None,
            note=data.note, created_by=_resolve_user_id(db, user))
        db.add(row)
        db.commit()
        db.refresh(row)
        write_audit(db, user, "attendance.manual", "staff_attendance", row.id,
                    after={"user_id": staff.user_id, "clock_in": data.clock_in.isoformat(),
                           "clock_out": data.clock_out.isoformat() if data.clock_out else None,
                           "minutes_worked": minutes, "note": data.note},
                    client="web", commit=True)
        return _attendance_dict(db, row)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"record_attendance failed: {e}", exc_info=True)
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to record attendance")


@router.get("/attendance")
def list_attendance(from_: str | None = Query(None, alias="from"),
                    to: str | None = Query(None),
                    user_id: int | None = Query(None),
                    open_only: bool = Query(False),
                    db: Session = Depends(get_db), user=Depends(require_reception_or_admin)):
    """Worked sessions in a period, with per-staff hour totals."""
    from routers.reports import _range
    dfrom, dto = _range(from_, to, default_days=30)   # _range parses the ISO strings itself

    q = db.query(StaffAttendance).filter(
        StaffAttendance.clock_in >= datetime.combine(dfrom, datetime.min.time()),
        StaffAttendance.clock_in < datetime.combine(dto, datetime.max.time()),
    )
    if user_id:
        q = q.filter(StaffAttendance.user_id == user_id)
    if open_only:
        q = q.filter(StaffAttendance.clock_out.is_(None))
    rows = q.order_by(StaffAttendance.clock_in.desc()).all()

    by_staff = {}
    for r in rows:
        entry = by_staff.setdefault(r.user_id, {
            "user_id": r.user_id, "staff_name": _staff_name(db, r.user_id),
            "sessions": 0, "minutes": 0})
        entry["sessions"] += 1
        entry["minutes"] += r.minutes_worked or 0
    for entry in by_staff.values():
        entry["hours"] = round(entry["minutes"] / 60, 2)

    return {
        "from": str(dfrom), "to": str(dto),
        "total": len(rows),
        "on_duty_now": db.query(StaffAttendance).filter(
            StaffAttendance.clock_out.is_(None)).count(),
        "by_staff": sorted(by_staff.values(), key=lambda x: -x["minutes"]),
        "data": [_attendance_dict(db, r) for r in rows],
    }


@router.get("/attendance/on-duty")
def on_duty(db: Session = Depends(get_db), user=Depends(require_reception_or_admin)):
    """Who is clocked in right now — the desk's "who's here" glance."""
    rows = db.query(StaffAttendance).filter(
        StaffAttendance.clock_out.is_(None)).order_by(StaffAttendance.clock_in).all()
    return {"total": len(rows), "data": [_attendance_dict(db, r) for r in rows]}


@router.put("/staff/{user_id}/card", dependencies=[Depends(require_admin)])
def assign_staff_card(user_id: int, data: StaffCardAssign, db: Session = Depends(get_db),
                      user=Depends(require_admin)):
    """Bind (or clear) a staff key-card UID. Pairs with POST /roster/attendance/card."""
    try:
        staff = _get_staff(db, user_id)
        uid = (data.card_uid or "").strip() or None
        if uid:
            clash = db.query(User).filter(User.staff_card_uid == uid,
                                          User.user_id != staff.user_id).first()
            if clash:
                raise HTTPException(status_code=409,
                                    detail="That card is already assigned to another staff member")
        before = staff.staff_card_uid
        staff.staff_card_uid = uid
        db.commit()
        write_audit(db, user, "attendance.card_assign", "user", staff.user_id,
                    before={"staff_card_uid": before}, after={"staff_card_uid": uid},
                    client="web", commit=True)
        return {"user_id": staff.user_id, "username": staff.username, "staff_card_uid": uid}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"assign_staff_card failed: {e}", exc_info=True)
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to assign the staff card")


@router.get("/coverage", dependencies=[Depends(require_admin)])
def coverage(week_start: str | None = Query(None), db: Session = Depends(get_db)):
    """Planned duties vs sessions actually worked, per staff member, for a week.

    This is the answer to "was the roster followed?" — a duty with no attendance is a no-show,
    attendance with no duty is unplanned overtime."""
    try:
        anchor = date.fromisoformat(week_start) if week_start else date.today()
    except ValueError:
        raise HTTPException(status_code=400, detail="week_start must be ISO YYYY-MM-DD")
    start = _week_start(anchor)
    end = start + timedelta(days=6)

    shifts = db.query(StaffShift).filter(
        StaffShift.shift_date >= start, StaffShift.shift_date <= end).all()
    sessions = db.query(StaffAttendance).filter(
        StaffAttendance.clock_in >= datetime.combine(start, datetime.min.time()),
        StaffAttendance.clock_in < datetime.combine(end, datetime.max.time()),
    ).all()
    drawers = db.query(CashShift).filter(
        CashShift.roster_shift_id.isnot(None)).all()
    drawer_by_shift = {d.roster_shift_id for d in drawers}

    rows = {}
    for s in shifts:
        e = rows.setdefault(s.user_id, {"user_id": s.user_id,
                                        "staff_name": _staff_name(db, s.user_id),
                                        "planned": 0, "absent": 0, "worked_sessions": 0,
                                        "minutes": 0, "drawers_opened": 0})
        e["planned"] += 1
        if s.status == "absent":
            e["absent"] += 1
        if s.id in drawer_by_shift:
            e["drawers_opened"] += 1
    for a in sessions:
        e = rows.setdefault(a.user_id, {"user_id": a.user_id,
                                        "staff_name": _staff_name(db, a.user_id),
                                        "planned": 0, "absent": 0, "worked_sessions": 0,
                                        "minutes": 0, "drawers_opened": 0})
        e["worked_sessions"] += 1
        e["minutes"] += a.minutes_worked or 0

    for e in rows.values():
        e["hours"] = round(e["minutes"] / 60, 2)
        e["unplanned"] = max(0, e["worked_sessions"] - (e["planned"] - e["absent"]))
        e["no_shows"] = max(0, (e["planned"] - e["absent"]) - e["worked_sessions"])

    return {"week_start": str(start), "week_end": str(end),
            "data": sorted(rows.values(), key=lambda x: (x["staff_name"] or ""))}
