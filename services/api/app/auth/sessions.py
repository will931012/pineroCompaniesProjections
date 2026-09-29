import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.base import utcnow
from app.db.models import User, UserSession

# Only refresh last_seen_at this often to avoid a write on every request.
_TOUCH_INTERVAL = timedelta(minutes=1)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class IssuedSession:
    session: UserSession
    token: str
    csrf_token: str


def issue_session(
    db: Session,
    user: User,
    settings: Settings,
    *,
    auth_method: str,
    ip_address: str | None,
    user_agent: str | None,
) -> IssuedSession:
    now = utcnow()
    token = secrets.token_urlsafe(32)
    csrf_token = secrets.token_urlsafe(32)
    record = UserSession(
        user_id=user.id,
        token_hash=hash_token(token),
        csrf_token=csrf_token,
        auth_method=auth_method,
        created_at=now,
        last_seen_at=now,
        expires_at=now + timedelta(hours=settings.session_absolute_ttl_hours),
        ip_address=ip_address,
        user_agent=(user_agent or "")[:300] or None,
    )
    db.add(record)
    return IssuedSession(record, token, csrf_token)


def csrf_matches(record: UserSession, presented: str | None) -> bool:
    if not presented:
        return False
    return hmac.compare_digest(record.csrf_token.encode(), presented.encode())


def resolve_session(db: Session, token: str | None, settings: Settings) -> UserSession | None:
    """Return the live session for a cookie token, enforcing absolute and idle expiry."""

    if not token:
        return None
    record = db.scalar(select(UserSession).where(UserSession.token_hash == hash_token(token)))
    if record is None or record.revoked_at is not None:
        return None
    now = utcnow()
    idle_limit = timedelta(minutes=settings.session_idle_ttl_minutes)
    if record.expires_at <= now or record.last_seen_at + idle_limit <= now:
        return None
    user = record.user
    if not user.is_active:
        return None
    if now - record.last_seen_at >= _TOUCH_INTERVAL:
        record.last_seen_at = now
        db.commit()
    return record


def session_expires_at(record: UserSession, settings: Settings) -> datetime:
    idle_expiry = record.last_seen_at + timedelta(minutes=settings.session_idle_ttl_minutes)
    return min(record.expires_at, idle_expiry)


def revoke_session(db: Session, record: UserSession) -> None:
    record.revoked_at = utcnow()


def revoke_all_sessions(db: Session, user: User) -> None:
    db.execute(
        update(UserSession)
        .where(UserSession.user_id == user.id, UserSession.revoked_at.is_(None))
        .values(revoked_at=utcnow())
    )
