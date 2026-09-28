from typing import Any

from sqlalchemy.orm import Session

from app.models import AuditLog


def log_action(
    db: Session,
    user_id: int | None,
    action: str,
    entity: str,
    entity_id: int | None = None,
    detail: dict[str, Any] | None = None,
) -> AuditLog:
    """Record an audit entry in the caller's transaction (caller commits)."""
    entry = AuditLog(
        user_id=user_id, action=action, entity=entity, entity_id=entity_id, detail=detail
    )
    db.add(entry)
    return entry
