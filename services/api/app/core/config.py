from functools import lru_cache
from typing import Literal

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["development", "test", "staging", "production"]


class Settings(BaseSettings):
    """Runtime configuration. Secrets are only ever read from the environment."""

    app_name: str = "Pinero Research API"
    app_env: Environment = "development"
    api_prefix: str = "/api/v1"
    log_level: str = "INFO"
    log_format: Literal["json", "console"] = "json"

    database_url: str = "postgresql+psycopg://pinero:pinero@localhost:5432/pinero"
    redis_url: str = "redis://localhost:6379/0"

    # Public URL of the web app. The browser reaches the API through the web app's
    # same-origin /api rewrite, so this is also the origin allowed for unsafe requests.
    frontend_url: str = "http://localhost:3000"
    cors_origins: list[str] = []

    # Authentication
    session_cookie_name: str = "pinero_session"
    session_absolute_ttl_hours: int = 12
    session_idle_ttl_minutes: int = 60
    auth_password_login_enabled: bool = True
    auth_allow_registration: bool = False
    # Temporary convenience: skip sign-in and treat every visitor as a built-in local admin.
    # Anyone who can reach the app gets full access, so never enable it on a shared URL.
    auth_disabled: bool = False
    auth_secret: SecretStr | None = None
    oidc_issuer_url: str | None = None
    oidc_client_id: str | None = None
    oidc_client_secret: SecretStr | None = None
    oidc_default_role: Literal["viewer", "analyst"] = "viewer"

    # Rate limiting
    rate_limit_backend: Literal["redis", "memory"] = "redis"
    rate_limit_login_per_minute: int = 10
    rate_limit_api_per_minute: int = 600

    # Observability
    metrics_enabled: bool = True
    sentry_dsn: SecretStr | None = None

    # SEC EDGAR requires a descriptive User-Agent with contact details:
    # https://www.sec.gov/os/accessing-edgar-data
    sec_user_agent: str | None = None
    sec_profile_ttl_hours: int = 168
    # companyfacts responses are several megabytes for large filers.
    sec_timeout_seconds: float = 30.0
    fundamentals_ttl_hours: int = 24
    # New filings (8-Ks, Form 4s) appear daily, so the index refreshes more often than profiles.
    filings_ttl_hours: int = 6

    # Filing search embeddings: "fastembed" runs the open model locally (no key); "none"
    # leaves search to PostgreSQL full-text. The model must produce 384-dimension vectors.
    embedding_provider: Literal["fastembed", "none"] = "fastembed"
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_cache_dir: str | None = None

    # News: the GDELT index needs no key; the contact goes in the User-Agent. Tracked
    # companies (watchlists and alert rules) are polled by the worker.
    news_poll_minutes: int = 60
    filings_poll_minutes: int = 30

    # Alerts by email through Resend. Resend's test sender (onboarding@resend.dev) only
    # delivers to the Resend account's own address; verify a domain for anything else.
    resend_api_key: SecretStr | None = None
    alerts_email_from: str = "Pinero alerts <onboarding@resend.dev>"
    alerts_poll_minutes: int = 5

    # Market data
    market_data_provider: str | None = None
    tiingo_api_key: SecretStr | None = None
    market_data_cache_ttl_minutes: int = 360
    market_data_timeout_seconds: float = 10.0

    model_config = SettingsConfigDict(case_sensitive=False, extra="ignore")

    @property
    def is_production(self) -> bool:
        return self.app_env in {"staging", "production"}

    @property
    def oidc_configured(self) -> bool:
        return bool(self.oidc_issuer_url and self.oidc_client_id and self.oidc_client_secret)

    @property
    def allowed_origins(self) -> list[str]:
        return list(dict.fromkeys([self.frontend_url.rstrip("/"), *self.cors_origins]))

    @model_validator(mode="after")
    def _enforce_production_safety(self) -> "Settings":
        if not self.is_production:
            return self
        problems: list[str] = []
        if not self.frontend_url.startswith("https://"):
            problems.append("FRONTEND_URL must use https")
        if "local-development-only" in self.database_url:
            problems.append("DATABASE_URL uses the local development password")
        if self.oidc_configured and (
            not self.auth_secret or len(self.auth_secret.get_secret_value()) < 32
        ):
            problems.append("AUTH_SECRET must be at least 32 characters when OIDC is enabled")
        if not self.auth_password_login_enabled and not self.oidc_configured:
            problems.append("no login method is enabled")
        if problems:
            raise ValueError("Unsafe production configuration: " + "; ".join(problems))
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
