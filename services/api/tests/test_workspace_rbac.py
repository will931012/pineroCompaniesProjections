from collections.abc import Callable

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import User
from tests.conftest import LoggedIn

pytestmark = pytest.mark.integration

ROWS = [[320193, "Apple Inc.", "AAPL", "Nasdaq"], [789019, "MICROSOFT CORP", "MSFT", "Nasdaq"]]


def test_analyst_manages_own_watchlist(analyst: LoggedIn, seed_directory) -> None:  # type: ignore[no-untyped-def]
    seed_directory(ROWS)
    created = analyst.client.post(
        "/api/v1/watchlists", json={"name": "Core"}, headers=analyst.headers()
    )
    assert created.status_code == 201
    watchlist_id = created.json()["id"]

    added = analyst.client.post(
        f"/api/v1/watchlists/{watchlist_id}/items",
        json={"ticker": "aapl"},
        headers=analyst.headers(),
    )
    again = analyst.client.post(
        f"/api/v1/watchlists/{watchlist_id}/items",
        json={"ticker": "AAPL"},
        headers=analyst.headers(),
    )
    assert added.status_code == 201
    assert [i["ticker"] for i in again.json()["items"]] == ["AAPL"]

    removed = analyst.client.delete(
        f"/api/v1/watchlists/{watchlist_id}/items/AAPL", headers=analyst.headers()
    )
    assert removed.json()["items"] == []
    duplicate = analyst.client.post(
        "/api/v1/watchlists", json={"name": "Core"}, headers=analyst.headers()
    )
    assert duplicate.status_code == 409


def test_unknown_ticker_cannot_be_added(analyst: LoggedIn, seed_directory) -> None:  # type: ignore[no-untyped-def]
    seed_directory(ROWS)
    watchlist_id = analyst.client.post(
        "/api/v1/watchlists", json={"name": "W"}, headers=analyst.headers()
    ).json()["id"]

    response = analyst.client.post(
        f"/api/v1/watchlists/{watchlist_id}/items",
        json={"ticker": "ZZZZ"},
        headers=analyst.headers(),
    )

    assert response.status_code == 404


def test_watchlists_are_owner_scoped(
    analyst: LoggedIn, make_user: Callable[..., str], login: Callable[[str], LoggedIn]
) -> None:
    other = login(make_user("other@example.com"))
    watchlist_id = analyst.client.post(
        "/api/v1/watchlists", json={"name": "Private"}, headers=analyst.headers()
    ).json()["id"]

    assert other.client.get("/api/v1/watchlists").json() == []
    delete = other.client.delete(f"/api/v1/watchlists/{watchlist_id}", headers=other.headers())
    assert delete.status_code == 404
    assert len(analyst.client.get("/api/v1/watchlists").json()) == 1


def test_viewer_is_read_only(
    make_user: Callable[..., str], login: Callable[[str], LoggedIn]
) -> None:
    viewer = login(make_user("viewer@example.com", "viewer"))

    assert viewer.client.get("/api/v1/watchlists").status_code == 200
    response = viewer.client.post(
        "/api/v1/watchlists", json={"name": "Nope"}, headers=viewer.headers()
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "insufficient_role"


def test_admin_endpoints_require_admin(analyst: LoggedIn, admin: LoggedIn) -> None:
    assert analyst.client.get("/api/v1/admin/users").status_code == 403
    assert admin.client.get("/api/v1/admin/users").status_code == 200


def test_role_change_revokes_sessions_and_is_audited(
    admin: LoggedIn, analyst: LoggedIn, db: Session
) -> None:
    user = db.scalar(select(User).where(User.email == analyst.email))
    assert user is not None

    response = admin.client.patch(
        f"/api/v1/admin/users/{user.id}", json={"role": "viewer"}, headers=admin.headers()
    )

    assert response.status_code == 200 and response.json()["role"] == "viewer"
    assert analyst.client.get("/api/v1/auth/session").status_code == 401
    events = admin.client.get("/api/v1/admin/audit-events").json()
    assert events[0]["action"] == "admin.user_update"
    assert events[0]["details"]["role"] == {"from": "analyst", "to": "viewer"}


def test_last_admin_cannot_be_demoted(admin: LoggedIn, db: Session) -> None:
    user = db.scalar(select(User).where(User.email == admin.email))
    assert user is not None

    response = admin.client.patch(
        f"/api/v1/admin/users/{user.id}", json={"is_active": False}, headers=admin.headers()
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "last_admin"
