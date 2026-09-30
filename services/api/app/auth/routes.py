import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import RedirectResponse
from starlette.concurrency import run_in_threadpool

from app.audit.service import record_audit_event
from app.auth.dependencies import (
    AppSettings,
    DbSession,
    client_ip,
    get_current_session,
    get_optional_session,
)
from app.auth.oidc import OAuthError, get_oidc_client, oidc_callback_url
from app.auth.schemas import AuthConfigOut, LoginIn, RegisterIn, SessionOut, UserOut
from app.auth.service import authenticate_password, create_user, local_user, resolve_oidc_user
from app.auth.sessions import (
    IssuedSession,
    issue_session,
    revoke_session,
    session_expires_at,
)
from app.core.config import Settings, get_settings
from app.core.errors import ApiError
from app.core.rate_limit import enforce_rate_limit
from app.db.models import User, UserSession
from app.db.session import get_sessionmaker

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])


def _set_session_cookie(response: Response, issued: IssuedSession, settings: Settings) -> None:
    response.set_cookie(
        settings.session_cookie_name,
        issued.token,
        max_age=settings.session_absolute_ttl_hours * 3600,
        httponly=True,
        secure=settings.is_production,
        samesite="lax",
        path="/",
    )


def _session_out(record: UserSession, user: User, settings: Settings) -> SessionOut:
    return SessionOut(
        user=UserOut.model_validate(user),
        csrf_token=record.csrf_token,
        expires_at=session_expires_at(record, settings),
        auth_method=record.auth_method,
    )


@router.get("/config", response_model=AuthConfigOut)
def auth_config(settings: AppSettings) -> AuthConfigOut:
    return AuthConfigOut(
        password_login=settings.auth_password_login_enabled,
        registration=settings.auth_allow_registration and settings.auth_password_login_enabled,
        oidc=settings.oidc_configured,
        auth_disabled=settings.auth_disabled,
    )


@router.get("/session", response_model=SessionOut)
def current_session(
    request: Request,
    response: Response,
    record: Annotated[UserSession | None, Depends(get_optional_session)],
    db: DbSession,
    settings: AppSettings,
) -> SessionOut:
    if record is None and settings.auth_disabled:
        # Sign-in is switched off: issue a real session for the local admin, so CSRF,
        # roles, and auditing keep working exactly as with a password login.
        user = local_user(db)
        ip = client_ip(request)
        issued = issue_session(
            db,
            user,
            settings,
            auth_method="auth_disabled",
            ip_address=ip,
            user_agent=request.headers.get("user-agent"),
        )
        record_audit_event(
            db, action="auth.auto_login", outcome="success", actor_user_id=user.id, ip_address=ip
        )
        db.commit()
        _set_session_cookie(response, issued, settings)
        return _session_out(issued.session, user, settings)
    if record is None:
        raise ApiError(401, "authentication_required", "No active session.")
    return _session_out(record, record.user, settings)


@router.post("/login", response_model=SessionOut)
def login(
    body: LoginIn, request: Request, response: Response, db: DbSession, settings: AppSettings
) -> SessionOut:
    if not settings.auth_password_login_enabled:
        raise ApiError(403, "password_login_disabled", "Password sign-in is disabled.")
    ip = client_ip(request)
    enforce_rate_limit("login-ip", ip, settings.rate_limit_login_per_minute)
    enforce_rate_limit("login-email", body.email.lower(), settings.rate_limit_login_per_minute)

    user = authenticate_password(db, email=body.email, password=body.password, ip_address=ip)
    issued = issue_session(
        db,
        user,
        settings,
        auth_method="password",
        ip_address=ip,
        user_agent=request.headers.get("user-agent"),
    )
    record_audit_event(
        db, action="auth.login", outcome="success", actor_user_id=user.id, ip_address=ip
    )
    db.commit()
    _set_session_cookie(response, issued, settings)
    return _session_out(issued.session, user, settings)


@router.post("/register", response_model=SessionOut, status_code=201)
def register(
    body: RegisterIn, request: Request, response: Response, db: DbSession, settings: AppSettings
) -> SessionOut:
    if not (settings.auth_allow_registration and settings.auth_password_login_enabled):
        raise ApiError(403, "registration_disabled", "Self-service registration is disabled.")
    ip = client_ip(request)
    enforce_rate_limit("register-ip", ip, 5, window_seconds=3600)

    # Self-registered accounts get a personal workspace but no administrative rights.
    user = create_user(
        db,
        email=body.email,
        display_name=body.display_name,
        password=body.password,
        role="analyst",
    )
    issued = issue_session(
        db,
        user,
        settings,
        auth_method="password",
        ip_address=ip,
        user_agent=request.headers.get("user-agent"),
    )
    record_audit_event(
        db, action="auth.register", outcome="success", actor_user_id=user.id, ip_address=ip
    )
    db.commit()
    _set_session_cookie(response, issued, settings)
    return _session_out(issued.session, user, settings)


@router.post("/logout", status_code=204)
def logout(
    request: Request,
    response: Response,
    record: Annotated[UserSession, Depends(get_current_session)],
    db: DbSession,
    settings: AppSettings,
) -> None:
    revoke_session(db, record)
    record_audit_event(
        db,
        action="auth.logout",
        outcome="success",
        actor_user_id=record.user_id,
        ip_address=client_ip(request),
    )
    db.commit()
    response.delete_cookie(
        settings.session_cookie_name,
        httponly=True,
        secure=settings.is_production,
        samesite="lax",
        path="/",
    )


@router.get("/oidc/login", include_in_schema=True)
async def oidc_login(request: Request) -> Response:
    client = get_oidc_client()
    if client is None:
        raise ApiError(503, "oidc_not_configured", "Single sign-on is not configured.")
    enforce_rate_limit("oidc-ip", client_ip(request), get_settings().rate_limit_login_per_minute)
    response: Response = await client.authorize_redirect(request, oidc_callback_url())
    return response


def _complete_oidc_login(claims: dict, issuer: str, ip: str, user_agent: str | None) -> str:
    settings = get_settings()
    with get_sessionmaker()() as db:
        user = resolve_oidc_user(
            db,
            issuer=issuer,
            subject=str(claims["sub"]),
            email=claims.get("email"),
            email_verified=bool(claims.get("email_verified")),
            display_name=claims.get("name"),
            default_role=settings.oidc_default_role,
        )
        issued = issue_session(
            db, user, settings, auth_method="oidc", ip_address=ip, user_agent=user_agent
        )
        record_audit_event(
            db,
            action="auth.login",
            outcome="success",
            actor_user_id=user.id,
            ip_address=ip,
            details={"method": "oidc"},
        )
        db.commit()
        return issued.token


@router.get("/oidc/callback", include_in_schema=False)
async def oidc_callback(request: Request) -> Response:
    client = get_oidc_client()
    settings = get_settings()
    if client is None:
        raise ApiError(503, "oidc_not_configured", "Single sign-on is not configured.")
    try:
        token = await client.authorize_access_token(request)
    except OAuthError as exc:
        logger.warning("oidc_login_failed", extra={"oidc_error": exc.error})
        return RedirectResponse(f"{settings.frontend_url}/login?error=sso_failed", 303)

    claims = token.get("userinfo") or {}
    if not claims.get("sub"):
        return RedirectResponse(f"{settings.frontend_url}/login?error=sso_claims", 303)
    metadata = await client.load_server_metadata()
    try:
        session_token = await run_in_threadpool(
            _complete_oidc_login,
            claims,
            str(metadata.get("issuer")),
            client_ip(request),
            request.headers.get("user-agent"),
        )
    except ApiError as exc:
        return RedirectResponse(f"{settings.frontend_url}/login?error={exc.code}", 303)

    response = RedirectResponse(settings.frontend_url, status_code=303)
    response.set_cookie(
        settings.session_cookie_name,
        session_token,
        max_age=settings.session_absolute_ttl_hours * 3600,
        httponly=True,
        secure=settings.is_production,
        samesite="lax",
        path="/",
    )
    return response
