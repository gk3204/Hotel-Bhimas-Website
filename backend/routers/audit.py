"""Append-only audit-log viewer (admin-only).

Read-only surface over models.AuditLog — who did what, when, and from where. This router
NEVER writes/updates rows (the trail is append-only; writes go through utils/audit.write_audit).

  GET /audit-logs   — filter by date range / action prefix / entity / user / client, paginated.
"""
from datetime import datetime, time as dtime, date, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from database import SessionLocal
from models import AuditLog, User
from utils.auth_utils import require_admin

router = APIRouter(prefix="/audit-logs", tags=["Audit"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _parse_date(s: str | None, default: date) -> date:
    if not s:
        return default
    try:
        return date.fromisoformat(s)
    except ValueError:
        return default


@router.get("")
def list_audit_logs(
    from_: str = Query(None, alias="from"),
    to: str = Query(None),
    action: str = Query(None, description="prefix match, e.g. 'booking.' or 'card.issue'"),
    entity_type: str = Query(None),
    entity_id: str = Query(None),
    user_id: int = Query(None),
    client: str = Query(None, description="desktop | web | housekeeper | system"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    user=Depends(require_admin),
):
    """List audit-trail entries, newest first. Returns {total, from, to, data:[...]}.
    `before`/`after` are the raw JSON snapshots (as stored) for the detail view."""
    dto = _parse_date(to, date.today())
    dfrom = _parse_date(from_, dto - timedelta(days=6))

    q = db.query(AuditLog).filter(
        AuditLog.created_at >= datetime.combine(dfrom, dtime.min),
        AuditLog.created_at <= datetime.combine(dto, dtime.max),
    )
    if action:
        q = q.filter(AuditLog.action.like(f"{action}%"))
    if entity_type:
        q = q.filter(AuditLog.entity_type == entity_type)
    if entity_id:
        q = q.filter(AuditLog.entity_id == str(entity_id))
    if user_id:
        q = q.filter(AuditLog.user_id == user_id)
    if client:
        q = q.filter(AuditLog.client == client)

    total = q.count()
    logs = q.order_by(AuditLog.created_at.desc()).offset(offset).limit(limit).all()

    # Resolve actor display names in one query.
    ids = {lg.user_id for lg in logs if lg.user_id}
    names: dict[int, str] = {}
    if ids:
        for u in db.query(User).filter(User.user_id.in_(ids)).all():
            names[u.user_id] = u.full_name or u.username

    data = [
        {
            "id": lg.log_id,
            "when": lg.created_at.isoformat() if lg.created_at else None,
            "actor": names.get(lg.user_id, "system") if lg.user_id else "system",
            "action": lg.action,
            "entity_type": lg.entity_type,
            "entity_id": lg.entity_id,
            "client": lg.client,
            "ip": lg.ip,
            "before": lg.before,
            "after": lg.after,
        }
        for lg in logs
    ]
    return {"total": total, "from": str(dfrom), "to": str(dto), "data": data}
