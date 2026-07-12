"""Append-only audit logging.

Call write_audit(...) from any mutating endpoint to record who did what.
Rows in `audit_logs` are NEVER updated or deleted — corrections are new rows.

Example:
    from utils.audit import write_audit
    write_audit(db, user, "booking.cancel", "booking", booking_id,
                before={"status": "confirmed"}, after={"status": "cancelled"},
                ip=request.client.host, client="web")
"""
import json
import logging

from models import AuditLog

logger = logging.getLogger(__name__)


def _resolve_user_id(db, user):
    """`user` is the decoded JWT payload dict ({'sub': username, 'role': ...}).
    Resolve it to a users.user_id, or None for system events."""
    if not user:
        return None
    # Already an int id
    if isinstance(user, int):
        return user
    username = user.get("sub") if isinstance(user, dict) else None
    if not username:
        return None
    try:
        from models import User
        row = db.query(User).filter(User.username == username).first()
        return row.user_id if row else None
    except Exception:
        return None


def write_audit(db, user, action, entity_type=None, entity_id=None,
                before=None, after=None, ip=None, client=None, commit=False):
    """Insert an append-only audit row. Best-effort: never raises to the caller
    (a failed audit write must not break the business action — it's logged instead).

    - user: decoded JWT payload dict, a user_id int, or None (system).
    - before/after: dict/list (serialized to JSON text) or a string.
    - commit: if True, commit now; otherwise it commits with the caller's transaction.
    """
    try:
        def _ser(v):
            if v is None or isinstance(v, str):
                return v
            try:
                return json.dumps(v, default=str)
            except Exception:
                return str(v)

        entry = AuditLog(
            user_id=_resolve_user_id(db, user),
            action=action,
            entity_type=entity_type,
            entity_id=str(entity_id) if entity_id is not None else None,
            before=_ser(before),
            after=_ser(after),
            ip=ip,
            client=client,
        )
        db.add(entry)
        if commit:
            db.commit()
        return entry
    except Exception as e:
        logger.error(f"audit write failed for action={action}: {e}", exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass
        return None
