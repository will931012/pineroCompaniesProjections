from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit.service import record_audit_event
from app.auth.passwords import hash_password, needs_rehash, password_problems, verify_password
from app.core.errors import ApiError
from app.db.base import utcnow
from app.db.models import User, UserIdentity

MAX_FAILED_LOGINS = 10
LOCKOUT = timedelta(minutes=15)


def normalize_email(email: str) -> str:
    return email.strip().lower()


def create_user(
    db: Session,
    *,
    email: str,
    display_name: str,
    password: str | None,
    role: str,
) -> User:
    email = normalize_email(email)
    if password is not None:
        problems = password_problems(password, email)
        if problems:
            raise ApiError(
                422,
                "password_too_weak",
                problems[0],
                details=[{"location": ["body", "password"], "message": p} for p in problems],
            )
    user = User(
        email=email,
        display_name=display_name.strip(),
        password_hash=hash_password(password) if password is not None else None,
        role=role,
    )
    db.add(user)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise ApiError(
            409, "email_already_registered", "This email is already registered."
        ) from exc
    return user


def authenticate_password(db: Session, *, email: str, password: str, ip_address: str) -> User:
    """Verify credentials with lockout; always fails with the same generic message."""

    email = normalize_email(email)
    user = db.scalar(select(User).where(User.email == email))
    now = utcnow()
    invalid = ApiError(401, "invalid_credentials", "Email or password is incorrect.")

    if user is not None and user.locked_until is not None and user.locked_until > now:
        verify_password(None, password)
        record_audit_event(
            db,
            action="auth.login",
            outcome="denied",
            actor_user_id=user.id,
            ip_address=ip_address,
            details={"reason": "locked"},
        )
        db.commit()
        raise invalid

    if user is None or not user.is_active or not verify_password(user.password_hash, password):
        if user is not None:
            user.failed_login_count += 1
            if user.failed_login_count >= MAX_FAILED_LOGINS:
                user.locked_until = now + LOCKOUT
                user.failed_login_count = 0
        record_audit_event(
            db,
            action="auth.login",
            outcome="failure",
            actor_user_id=user.id if user else None,
            ip_address=ip_address,
            details={"reason": "invalid_credentials"},
        )
        db.commit()
        raise invalid

    assert user.password_hash is not None
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = now
    return user


def resolve_oidc_user(
    db: Session,
    *,
    issuer: str,
    subject: str,
    email: str | None,
    email_verified: bool,
    display_name: str | None,
    default_role: str,
) -> User:
    """Find the user linked to an OIDC subject, or create one.

    An existing password account is linked only when the provider asserts the email
    is verified, which prevents takeover through an unverified email claim.
    """

    identity = db.scalar(
        select(UserIdentity).where(UserIdentity.issuer == issuer, UserIdentity.subject == subject)
    )
    if identity is not None:
        user = identity.user
    else:
        if not email:
            raise ApiError(401, "oidc_claims_missing", "The identity provider returned no email.")
        normalized = normalize_email(email)
        existing = db.scalar(select(User).where(User.email == normalized))
        if existing is not None and not email_verified:
            raise ApiError(
                409,
                "oidc_email_unverified",
                "An account with this email exists; the identity provider must verify the email.",
            )
        user = existing or create_user(
            db,
            email=normalized,
            display_name=display_name or normalized.split("@")[0],
            password=None,
            role=default_role,
        )
        db.add(UserIdentity(user_id=user.id, issuer=issuer, subject=subject))
    if not user.is_active:
        raise ApiError(403, "account_disabled", "This account is disabled.")
    user.last_login_at = utcnow()
    return user


def count_admins(db: Session) -> int:
    return (
        db.scalar(
            select(func.count()).select_from(User).where(User.role == "admin", User.is_active)
        )
        or 0
    )
