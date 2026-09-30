"""Test configuration.

Unit tests need nothing external. Tests marked `integration` need PostgreSQL:
set TEST_DATABASE_URL (e.g. postgresql+psycopg://pinero@localhost:5432/pinero_test).
The schema is created with the real Alembic migrations, and every table is truncated
between tests. No test calls a live data provider.
"""

import os

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
os.environ.update(
    {
        "APP_ENV": "test",
        "DATABASE_URL": TEST_DATABASE_URL or "postgresql+psycopg://unused@localhost:1/unused",
        "RATE_LIMIT_BACKEND": "memory",
        "LOG_FORMAT": "console",
        "LOG_LEVEL": "WARNING",
        "AUTH_ALLOW_REGISTRATION": "true",
        "FRONTEND_URL": "http://testserver",
        "METRICS_ENABLED": "true",
        # Tests inject a deterministic fake instead of downloading a model.
        "EMBEDDING_PROVIDER": "none",
    }
)
for key in (
    "SEC_USER_AGENT",
    "MARKET_DATA_PROVIDER",
    "TIINGO_API_KEY",
    "OIDC_ISSUER_URL",
    "OIDC_CLIENT_ID",
    "OIDC_CLIENT_SECRET",
    "SENTRY_DSN",
    "CORS_ORIGINS",
):
    os.environ.pop(key, None)

from collections.abc import Callable, Generator, Iterator  # noqa: E402
from dataclasses import dataclass  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.core.rate_limit import MemoryRateLimiter, get_rate_limiter  # noqa: E402

API_ROOT = Path(__file__).resolve().parents[1]
PASSWORD = "correct-horse-battery-staple"


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if TEST_DATABASE_URL:
        return
    skip = pytest.mark.skip(reason="TEST_DATABASE_URL is not set")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def _reset_rate_limits() -> None:
    limiter = get_rate_limiter()
    if isinstance(limiter, MemoryRateLimiter):
        limiter.reset()


@pytest.fixture(scope="session")
def migrated_database() -> None:
    from alembic.config import Config

    from alembic import command

    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "alembic"))
    command.downgrade(config, "base")
    command.upgrade(config, "head")


@pytest.fixture
def db(migrated_database: None) -> Iterator[Session]:
    from app.db.base import Base
    from app.db.session import get_engine, get_sessionmaker

    tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
    with get_engine().begin() as connection:
        connection.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    with get_sessionmaker()() as session:
        yield session


@pytest.fixture
def app_client(db: Session) -> Iterator[TestClient]:
    from app.main import app

    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


@dataclass
class LoggedIn:
    client: TestClient
    csrf: str
    email: str

    def headers(self) -> dict[str, str]:
        return {"X-CSRF-Token": self.csrf}


@pytest.fixture
def make_user(db: Session) -> Callable[..., str]:
    from app.auth.service import create_user

    def factory(email: str, role: str = "analyst", password: str = PASSWORD) -> str:
        create_user(db, email=email, display_name=email.split("@")[0], password=password, role=role)
        db.commit()
        return email

    return factory


@pytest.fixture
def login(app_client: TestClient) -> Callable[[str], LoggedIn]:
    from app.main import app

    def do_login(email: str, password: str = PASSWORD) -> LoggedIn:
        # A fresh client per identity keeps cookie jars separate.
        client = TestClient(app)
        response = client.post("/api/v1/auth/login", json={"email": email, "password": password})
        assert response.status_code == 200, response.text
        return LoggedIn(client, response.json()["csrf_token"], email)

    return do_login


@pytest.fixture
def analyst(make_user: Callable[..., str], login: Callable[[str], LoggedIn]) -> LoggedIn:
    return login(make_user("analyst@example.com", "analyst"))


@pytest.fixture
def admin(make_user: Callable[..., str], login: Callable[[str], LoggedIn]) -> LoggedIn:
    return login(make_user("admin@example.com", "admin"))


@pytest.fixture
def seed_directory(db: Session) -> Callable[..., None]:
    """Insert companies through the real SEC sync path using a mock EDGAR transport."""

    from tests.fakes import sec_client_with

    def seed(rows: list[list[object]]) -> None:
        from app.ingestion.sec_directory import sync_sec_directory

        sync_sec_directory(db, sec_client_with(directory_rows=rows))

    return seed


@pytest.fixture
def override(app_client: TestClient) -> Generator[Callable[[Callable, Callable], None], None, None]:
    from app.main import app

    def set_override(dependency: Callable, replacement: Callable) -> None:
        app.dependency_overrides[dependency] = replacement

    yield set_override
    app.dependency_overrides.clear()
