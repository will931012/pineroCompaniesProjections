from dataclasses import dataclass
from functools import lru_cache

import httpx

from app.core.config import Settings, get_settings
from app.providers.market_data.base import MarketDataProvider, ProviderInfo
from app.providers.market_data.tiingo import INFO as TIINGO_INFO
from app.providers.market_data.tiingo import TiingoMarketDataProvider

SUPPORTED: dict[str, ProviderInfo] = {TIINGO_INFO.name: TIINGO_INFO}


@dataclass(frozen=True)
class ProviderStatus:
    name: str | None
    configured: bool
    code: str
    message: str


def market_data_status(settings: Settings) -> ProviderStatus:
    name = (settings.market_data_provider or "").strip().lower() or None
    if name is None:
        return ProviderStatus(
            None,
            False,
            "provider_not_configured",
            "No market-data provider is configured. Set MARKET_DATA_PROVIDER "
            "to one of: " + ", ".join(sorted(SUPPORTED)) + ".",
        )
    if name not in SUPPORTED:
        return ProviderStatus(
            name,
            False,
            "provider_unsupported",
            f"No adapter is implemented for market-data provider '{name}'.",
        )
    if name == "tiingo" and not settings.tiingo_api_key:
        return ProviderStatus(
            name,
            False,
            "provider_credentials_missing",
            "MARKET_DATA_PROVIDER=tiingo requires TIINGO_API_KEY.",
        )
    return ProviderStatus(name, True, "ok", f"{SUPPORTED[name].display_name} is configured.")


@lru_cache
def _http_client(timeout: float) -> httpx.Client:
    return httpx.Client(timeout=timeout, follow_redirects=False)


def get_market_data_provider(settings: Settings | None = None) -> MarketDataProvider | None:
    settings = settings or get_settings()
    status = market_data_status(settings)
    if not status.configured:
        return None
    assert settings.tiingo_api_key is not None
    return TiingoMarketDataProvider(
        settings.tiingo_api_key.get_secret_value(),
        _http_client(settings.market_data_timeout_seconds),
    )
