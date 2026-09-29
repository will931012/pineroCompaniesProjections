import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.core.logging import get_request_id
from app.db.models import AuditEvent


def record_audit_event(
    session: Session,
    *,
    action: str,
    outcome: str,
    actor_user_id: uuid.UUID | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    ip_address: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    """Add an audit row to the caller's transaction; the caller commits."""

    session.add(
        AuditEvent(
            actor_user_id=actor_user_id,
            action=action,
            outcome=outcome,
            resource_type=resource_type,
            resource_id=resource_id,
            request_id=get_request_id(),
            ip_address=ip_address,
            details=details or {},
        )
    )
