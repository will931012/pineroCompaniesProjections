from functools import lru_cache
from typing import Any

from authlib.integrations.base_client.errors import OAuthError
from authlib.integrations.starlette_client import OAuth, StarletteOAuth2App

from app.core.config import get_settings


@lru_cache
def get_oidc_client() -> StarletteOAuth2App | None:
    settings = get_settings()
    if not settings.oidc_configured:
        return None
    assert settings.oidc_issuer_url and settings.oidc_client_secret
    oauth = OAuth()
    oauth.register(
        name="oidc",
        server_metadata_url=(
            f"{settings.oidc_issuer_url.rstrip('/')}/.well-known/openid-configuration"
        ),
        client_id=settings.oidc_client_id,
        client_secret=settings.oidc_client_secret.get_secret_value(),
        client_kwargs={"scope": "openid email profile", "code_challenge_method": "S256"},
    )
    client: Any = oauth.create_client("oidc")
    return client


def oidc_callback_url() -> str:
    # The browser reaches the API through the web app's /api rewrite, so the
    # callback must be the public web origin, never the internal API host.
    settings = get_settings()
    return f"{settings.frontend_url.rstrip('/')}{settings.api_prefix}/auth/oidc/callback"


__all__ = ["OAuthError", "get_oidc_client", "oidc_callback_url"]
