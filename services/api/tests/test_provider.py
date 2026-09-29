from datetime import date
from decimal import Decimal

import httpx
import pytest
from pydantic import SecretStr

from app.core.config import Settings
from app.providers.base import ProviderError
from app.providers.market_data.base import DailyBar, bar_problems
from app.providers.market_data.registry import get_market_data_provider, market_data_status
from tests.fakes import TEST_TIINGO_KEY, json_response, tiingo_row, tiingo_with

START, END = date(2026, 1, 1), date(2026, 1, 31)


def test_unconfigured_provider_returns_no_adapter() -> None:
    settings = Settings(market_data_provider=None)

    assert get_market_data_provider(settings) is None
    assert market_data_status(settings).code == "provider_not_configured"


def test_unknown_provider_is_not_silently_accepted() -> None:
    status = market_data_status(Settings(market_data_provider="unknown"))

    assert not status.configured
    assert status.code == "provider_unsupported"


def test_tiingo_requires_its_api_key() -> None:
    status = market_data_status(Settings(market_data_provider="tiingo"))

    assert status.code == "provider_credentials_missing"
    assert "TIINGO_API_KEY" in status.message


def test_tiingo_configured() -> None:
    settings = Settings(market_data_provider="Tiingo", tiingo_api_key=SecretStr("k"))

    assert market_data_status(settings).configured
    assert get_market_data_provider(settings) is not None


def test_tiingo_parses_bars_and_keeps_token_out_of_provenance() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return json_response([tiingo_row("2026-01-02", 100.0), tiingo_row("2026-01-05", 101.5)])

    result = tiingo_with(handler).get_daily_bars("AAPL", START, END)

    assert [bar.close for bar in result.data] == [Decimal("100.0"), Decimal("101.5")]
    assert result.data[0].trade_date == date(2026, 1, 2)
    assert seen[0].headers["Authorization"] == f"Token {TEST_TIINGO_KEY}"
    assert TEST_TIINGO_KEY not in result.meta.source_url
    assert TEST_TIINGO_KEY not in str(result.meta.request_params)


def test_tiingo_rejects_invalid_rows_instead_of_repairing_them() -> None:
    rows = [
        tiingo_row("2026-01-02", 100.0),
        tiingo_row("2026-01-05", 100.0, high=90.0),  # high below close
        {**tiingo_row("2026-01-06", 100.0), "close": None},  # missing value
        tiingo_row("2026-01-07", -5.0),  # negative price
        tiingo_row("2025-12-31", 100.0),  # outside requested range
    ]

    result = tiingo_with(lambda _: json_response(rows)).get_daily_bars("AAPL", START, END)

    assert len(result.data) == 1
    assert result.rejected_count == 4


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (401, "provider_auth_failed"),
        (404, "provider_symbol_not_found"),
        (429, "provider_rate_limited"),
        (502, "provider_unavailable"),
    ],
)
def test_tiingo_error_mapping(status: int, code: str) -> None:
    provider = tiingo_with(lambda _: httpx.Response(status))

    with pytest.raises(ProviderError) as error:
        provider.get_daily_bars("AAPL", START, END)

    assert error.value.code == code


def test_bar_validation_rules() -> None:
    good = DailyBar(date(2026, 1, 2), Decimal(10), Decimal(12), Decimal(9), Decimal(11), 5)
    bad = DailyBar(date(2026, 1, 2), Decimal(10), Decimal(12), Decimal(11), Decimal(9), -1)

    assert bar_problems(good) == []
    assert set(bar_problems(bad)) == {"low_above_range", "negative_volume"}
