from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.models import AuditEvent, User, UserSession
from tests.conftest import PASSWORD, LoggedIn

pytestmark = pytest.mark.integration


def test_liveness_and_readiness(app_client: TestClient) -> None:
    live = app_client.get("/health/live")
    ready = app_client.get("/health/ready")

    assert live.json() == {"status": "ok"}
    assert ready.status_code == 200
    assert ready.json()["checks"]["database"] == "ok"


def test_responses_carry_request_id_and_security_headers(app_client: TestClient) -> None:
    response = app_client.get("/health/live", headers={"X-Request-ID": "trace-12345678"})

    assert response.headers["X-Request-ID"] == "trace-12345678"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"


def test_unknown_route_uses_error_envelope(app_client: TestClient) -> None:
    body = app_client.get("/api/v1/does-not-exist").json()

    assert body["error"]["code"] == "not_found"
    assert body["error"]["request_id"]


def test_validation_errors_use_error_envelope(analyst: LoggedIn) -> None:
    response = analyst.client.get("/api/v1/market-data/AAPL/bars")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "request_validation_failed"
    assert response.json()["error"]["details"]


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/companies?query=a",
        "/api/v1/companies/AAPL",
        "/api/v1/watchlists",
        "/api/v1/system/status",
        "/api/v1/market-data/AAPL/bars?from=2026-01-01&to=2026-01-02",
    ],
)
def test_data_endpoints_require_authentication(app_client: TestClient, path: str) -> None:
    response = app_client.get(path)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"


def test_metrics_endpoint_exposes_request_histogram(app_client: TestClient) -> None:
    app_client.get("/health/live")

    assert "pinero_http_request_duration_seconds" in app_client.get("/metrics").text


def test_register_creates_session_with_httponly_cookie(app_client: TestClient, db: Session) -> None:
    response = app_client.post(
        "/api/v1/auth/register",
        json={"email": "New.User@Example.com", "password": PASSWORD, "display_name": "New"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["user"]["email"] == "new.user@example.com"
    assert body["user"]["role"] == "analyst"
    assert body["csrf_token"]
    cookie = response.headers["set-cookie"]
    assert "pinero_session=" in cookie and "HttpOnly" in cookie and "SameSite=lax" in cookie
    stored = db.scalar(select(UserSession))
    assert stored is not None
    assert stored.token_hash not in cookie  # only the hash is stored


def test_register_rejects_weak_password_and_duplicates(
    app_client: TestClient, make_user: Callable[..., str]
) -> None:
    weak = app_client.post(
        "/api/v1/auth/register",
        json={"email": "w@example.com", "password": "short", "display_name": "W"},
    )
    make_user("taken@example.com")
    duplicate = app_client.post(
        "/api/v1/auth/register",
        json={"email": "TAKEN@example.com", "password": PASSWORD, "display_name": "T"},
    )

    assert weak.status_code == 422 and weak.json()["error"]["code"] == "password_too_weak"
    assert duplicate.status_code == 409


def test_registration_can_be_disabled(app_client: TestClient, override) -> None:  # type: ignore[no-untyped-def]
    override(get_settings, lambda: Settings(auth_allow_registration=False))

    response = app_client.post(
        "/api/v1/auth/register",
        json={"email": "x@example.com", "password": PASSWORD, "display_name": "X"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "registration_disabled"


def test_login_session_and_logout(analyst: LoggedIn, db: Session) -> None:
    session = analyst.client.get("/api/v1/auth/session")
    assert session.status_code == 200
    assert session.json()["csrf_token"] == analyst.csrf

    missing_csrf = analyst.client.post("/api/v1/auth/logout")
    assert missing_csrf.status_code == 403
    assert missing_csrf.json()["error"]["code"] == "csrf_token_invalid"

    assert analyst.client.post("/api/v1/auth/logout", headers=analyst.headers()).status_code == 204
    assert analyst.client.get("/api/v1/auth/session").status_code == 401
    actions = db.scalars(select(AuditEvent.action).order_by(AuditEvent.id)).all()
    assert actions == ["auth.login", "auth.logout"]


def test_wrong_password_and_unknown_email_look_identical(
    app_client: TestClient, make_user: Callable[..., str]
) -> None:
    make_user("real@example.com")
    wrong = app_client.post(
        "/api/v1/auth/login", json={"email": "real@example.com", "password": "not-the-password"}
    )
    unknown = app_client.post(
        "/api/v1/auth/login", json={"email": "ghost@example.com", "password": "not-the-password"}
    )

    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["error"]["message"] == unknown.json()["error"]["message"]


def test_account_locks_after_repeated_failures(
    app_client: TestClient, make_user: Callable[..., str], db: Session
) -> None:
    from app.auth.service import MAX_FAILED_LOGINS
    from app.core.rate_limit import get_rate_limiter

    email = make_user("target@example.com")
    for _ in range(MAX_FAILED_LOGINS):
        get_rate_limiter().reset()  # type: ignore[attr-defined]
        app_client.post("/api/v1/auth/login", json={"email": email, "password": "bad-password!"})
    get_rate_limiter().reset()  # type: ignore[attr-defined]

    correct = app_client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})

    assert correct.status_code == 401
    user = db.scalar(select(User).where(User.email == email))
    db.refresh(user)
    assert user is not None and user.locked_until is not None


def test_login_is_rate_limited(app_client: TestClient) -> None:
    statuses = [
        app_client.post(
            "/api/v1/auth/login", json={"email": "someone@example.com", "password": "x"}
        ).status_code
        for _ in range(12)
    ]

    assert statuses[-1] == 429
    assert statuses.count(401) == 10


def test_cross_origin_unsafe_requests_are_blocked(app_client: TestClient) -> None:
    response = app_client.post(
        "/api/v1/auth/login",
        json={"email": "a@example.com", "password": PASSWORD},
        headers={"Origin": "https://evil.example"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "origin_not_allowed"


def test_oidc_login_fails_closed_when_unconfigured(app_client: TestClient) -> None:
    response = app_client.get("/api/v1/auth/oidc/login")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "oidc_not_configured"


def test_auth_config_reports_enabled_methods(app_client: TestClient) -> None:
    assert app_client.get("/api/v1/auth/config").json() == {
        "password_login": True,
        "registration": True,
        "oidc": False,
    }


def test_timestamps_are_returned_in_utc(analyst: LoggedIn) -> None:
    created = analyst.client.post(
        "/api/v1/watchlists", json={"name": "Clock"}, headers=analyst.headers()
    ).json()

    assert created["created_at"].endswith(("Z", "+00:00"))
