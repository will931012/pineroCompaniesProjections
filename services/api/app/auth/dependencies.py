from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.auth.sessions import csrf_matches, resolve_session
from app.core.config import Settings, get_settings
from app.core.errors import ApiError
from app.core.rate_limit import enforce_rate_limit
from app.db.models import User, UserSession
from app.db.session import get_db

ROLE_RANK = {"viewer": 1, "analyst": 2, "admin": 3}
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
CSRF_HEADER = "X-CSRF-Token"

DbSession = Annotated[Session, Depends(get_db)]
AppSettings = Annotated[Settings, Depends(get_settings)]


def client_ip(request: Request) -> str:
    # Uvicorn's proxy-header support rewrites request.client from X-Forwarded-For
    # when the immediate peer is in --forwarded-allow-ips.
    return request.client.host if request.client else "unknown"


def get_optional_session(
    request: Request, db: DbSession, settings: AppSettings
) -> UserSession | None:
    return resolve_session(db, request.cookies.get(settings.session_cookie_name), settings)


def get_current_session(
    request: Request,
    record: Annotated[UserSession | None, Depends(get_optional_session)],
    settings: AppSettings,
) -> UserSession:
    if record is None:
        raise ApiError(401, "authentication_required", "Sign in to access this resource.")
    if request.method in UNSAFE_METHODS and not csrf_matches(
        record, request.headers.get(CSRF_HEADER)
    ):
        raise ApiError(403, "csrf_token_invalid", "The request is missing a valid CSRF token.")
    enforce_rate_limit("api", str(record.user_id), settings.rate_limit_api_per_minute)
    return record


def get_current_user(record: Annotated[UserSession, Depends(get_current_session)]) -> User:
    return record.user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_role(minimum: str) -> Callable[[User], User]:
    def dependency(user: CurrentUser) -> User:
        if ROLE_RANK[user.role] < ROLE_RANK[minimum]:
            raise ApiError(
                403, "insufficient_role", f"This action requires the {minimum} role or higher."
            )
        return user

    return dependency


AnalystUser = Annotated[User, Depends(require_role("analyst"))]
AdminUser = Annotated[User, Depends(require_role("admin"))]
