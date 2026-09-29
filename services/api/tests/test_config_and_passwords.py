import pytest
from pydantic import SecretStr, ValidationError

from app.auth.passwords import hash_password, password_problems, verify_password
from app.core.config import Settings


def test_production_rejects_insecure_frontend_url() -> None:
    with pytest.raises(ValidationError, match="FRONTEND_URL must use https"):
        Settings(
            app_env="production",
            frontend_url="http://app.example.com",
            database_url="postgresql+psycopg://u:p@db/pinero",
        )


def test_production_rejects_development_database_password() -> None:
    with pytest.raises(ValidationError, match="local development password"):
        Settings(
            app_env="production",
            frontend_url="https://app.example.com",
            database_url="postgresql+psycopg://pinero:local-development-only@db/pinero",
        )


def test_production_requires_strong_secret_with_oidc() -> None:
    with pytest.raises(ValidationError, match="AUTH_SECRET"):
        Settings(
            app_env="production",
            frontend_url="https://app.example.com",
            database_url="postgresql+psycopg://u:p@db/pinero",
            oidc_issuer_url="https://id.example.com",
            oidc_client_id="web",
            oidc_client_secret=SecretStr("s"),
            auth_secret=SecretStr("short"),
        )


def test_valid_production_configuration() -> None:
    settings = Settings(
        app_env="production",
        frontend_url="https://app.example.com",
        database_url="postgresql+psycopg://u:p@db/pinero",
    )

    assert settings.is_production
    assert settings.allowed_origins == ["https://app.example.com"]


def test_blank_oidc_secret_is_not_configured() -> None:
    settings = Settings(
        oidc_issuer_url="https://id.example", oidc_client_id="web", oidc_client_secret=SecretStr("")
    )

    assert not settings.oidc_configured


def test_password_hashing_round_trip() -> None:
    hashed = hash_password("correct-horse-battery-staple")

    assert hashed.startswith("$argon2id$")
    assert verify_password(hashed, "correct-horse-battery-staple")
    assert not verify_password(hashed, "wrong-password-entirely")
    assert not verify_password(None, "anything")


def test_password_policy() -> None:
    assert password_problems("short", "a@example.com")
    assert password_problems("a@example.com", "A@example.com")
    assert password_problems("long-enough-password", "a@example.com") == []
